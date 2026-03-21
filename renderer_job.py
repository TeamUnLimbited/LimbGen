import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from arminator_aws_backend import (
    clear_session_draft,
    dispatch_once,
    get_job_record,
    scrub_job_personal_data,
    send_completion_email,
    send_internal_generation_report,
    set_job_fields,
)
from arminator_aws_backend import _s3_client as s3_client
from arminator_common import (
    build_archive_name,
    build_render_command,
    build_render_parameters,
    get_render_steps,
    make_output_filename,
)


AWS_REGION = os.environ.get("AWS_REGION", "eu-west-2")
ARTIFACTS_BUCKET = os.environ["ARMINATOR_ARTIFACTS_BUCKET"]
JOB_ID = os.environ["ARMINATOR_JOB_ID"]

terminate_requested = False
current_process: Optional[subprocess.Popen] = None


def handle_termination(_signum, _frame) -> None:
    global terminate_requested
    terminate_requested = True
    if current_process and current_process.poll() is None:
        try:
            current_process.terminate()
        except Exception:
            pass


signal.signal(signal.SIGTERM, handle_termination)
signal.signal(signal.SIGINT, handle_termination)


def get_job() -> Dict[str, Any]:
    item = get_job_record(JOB_ID)
    if not item:
        raise RuntimeError(f"Job {JOB_ID} not found in DynamoDB.")
    return item


def update_job(changes: Dict[str, Any]) -> Dict[str, Any]:
    return set_job_fields(JOB_ID, changes)


def discover_task_arn() -> Optional[str]:
    metadata_base = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if not metadata_base:
        return None
    try:
        with urllib.request.urlopen(f"{metadata_base}/task", timeout=5) as response:
            payload = json.loads(response.read().decode())
        return payload.get("TaskARN")
    except Exception:
        return None


def upload_file(local_path: Path, key: str) -> None:
    s3_client.upload_file(str(local_path), ARTIFACTS_BUCKET, key)


def job_prefix(job_id: str) -> str:
    return f"jobs/{job_id}"


def check_canceled() -> None:
    job = get_job()
    if terminate_requested or job.get("cancel_requested"):
        raise RuntimeError("canceled")


def run_openscad_with_heartbeat(command: List[str], part: str) -> None:
    global current_process
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    current_process = process

    try:
        while True:
            if terminate_requested:
                if process.poll() is None:
                    process.terminate()
                raise RuntimeError("canceled")

            try:
                check_canceled()
            except RuntimeError:
                if process.poll() is None:
                    process.terminate()
                raise
            return_code = process.poll()
            update_job(
                {
                    "current_step": "rendering",
                    "status_line": f"OpenSCAD is still generating {part}. This model can take several minutes.",
                }
            )
            if return_code is not None:
                stdout, stderr = process.communicate()
                if terminate_requested:
                    raise RuntimeError("canceled")
                if return_code != 0:
                    detail = stderr.strip() or stdout.strip() or "OpenSCAD exited with a non-zero status."
                    raise RuntimeError(f"Failed while generating {part}: {detail}")
                return
            time.sleep(1.0)
    finally:
        current_process = None


def main() -> int:
    job = get_job()
    arm_version = str(job.get("arm_version") or "").strip().lower()
    if not arm_version:
        raise RuntimeError(f"Job {JOB_ID} is missing arm_version.")
    parameters = dict(job.get("parameters", {}))
    selected_parts = list(job.get("selected_parts", []))
    render_steps = get_render_steps(arm_version, selected_parts)
    total_parts = len(selected_parts)
    rendered_files: List[Path] = []

    with tempfile.TemporaryDirectory(prefix=f"arminator-{JOB_ID}-") as temp_dir_name:
        job_dir = Path(temp_dir_name)
        try:
            update_job(
                {
                    "status": "running",
                    "started_at": time.time(),
                    "progress": 0,
                    "message": "Preparing generation workspace",
                    "total_parts": total_parts,
                    "completed_parts": 0,
                    "current_step": "preparing",
                    "status_line": "Preparing OpenSCAD generation workspace.",
                    "task_arn": discover_task_arn(),
                }
            )

            completed_phases = 0
            for index, step in enumerate(render_steps, start=1):
                check_canceled()
                update_job(
                    {
                        "progress": int((completed_phases / total_parts) * 100) if total_parts else 0,
                        "message": f"Generating {step['status_part']} ({step['phase_index']}/{total_parts})",
                        "current_part": step["status_part"],
                        "current_part_index": step["phase_index"],
                        "current_step": "rendering",
                        "status_line": f"Queued generation step started for {step['part_label']}.",
                    }
                )

                output_name = make_output_filename(index, step["part_label"], str(parameters["LeftRight"]))
                output_path = job_dir / output_name
                render_parameters = build_render_parameters(arm_version, parameters, step["part_label"])
                command = build_render_command(output_path, render_parameters, arm_version)
                run_openscad_with_heartbeat(command, step["part_label"])
                rendered_files.append(output_path)
                if step["phase_complete"]:
                    completed_phases += 1

                update_job(
                    {
                        "progress": int((completed_phases / total_parts) * 100) if total_parts else 100,
                        "message": f"Generated {step['status_part']} ({completed_phases}/{total_parts})",
                        "output_files": [path.name for path in rendered_files],
                        "completed_parts": completed_phases,
                        "status_line": f"Finished {step['part_label']}.",
                    }
                )

            check_canceled()
            update_job(
                {
                    "progress": 100,
                    "message": "Building ZIP archive",
                    "current_step": "packaging",
                    "status_line": "Compressing generated STL files into a ZIP archive.",
                }
            )

            prefix = job_prefix(JOB_ID)
            for rendered_file in rendered_files:
                upload_file(rendered_file, f"{prefix}/{rendered_file.name}")

            archive_name = build_archive_name(parameters, arm_version)
            archive_path = job_dir / archive_name
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for rendered_file in rendered_files:
                    archive.write(rendered_file, arcname=rendered_file.name)
            archive_key = f"{prefix}/{archive_name}"
            upload_file(archive_path, archive_key)

            update_job(
                {
                    "status": "completed",
                    "progress": 100,
                    "message": "Generation complete",
                    "download_name": archive_name,
                    "archive_key": archive_key,
                    "output_files": [path.name for path in rendered_files],
                    "current_part": None,
                    "current_part_index": total_parts,
                    "completed_parts": total_parts,
                    "current_step": "completed",
                    "status_line": "ZIP archive is ready for download.",
                    "finished_at": time.time(),
                }
            )
            completed_job = get_job()
            try:
                send_completion_email(completed_job)
            except Exception:
                print("Failed to send completion email.", file=sys.stderr)
                traceback.print_exc()
            try:
                send_internal_generation_report(completed_job)
            except Exception:
                print("Failed to send internal generation report.", file=sys.stderr)
                traceback.print_exc()
            try:
                scrub_job_personal_data(JOB_ID)
                clear_session_draft(str(job.get("client_id") or ""))
            except Exception:
                pass
            return 0
        except RuntimeError as exc:
            if str(exc) == "canceled":
                update_job(
                    {
                        "status": "canceled",
                        "progress": 100,
                        "message": "Generation canceled.",
                        "current_part": None,
                        "current_step": "canceled",
                        "status_line": "Generation canceled.",
                        "finished_at": time.time(),
                    }
                )
                try:
                    scrub_job_personal_data(JOB_ID)
                    clear_session_draft(str(job.get("client_id") or ""))
                except Exception:
                    pass
                return 0
            update_job(
                {
                    "status": "failed",
                    "progress": 100,
                    "message": "Generation failed",
                    "error": str(exc),
                    "current_step": "failed",
                    "status_line": "OpenSCAD exited with an error.",
                    "finished_at": time.time(),
                }
            )
            try:
                scrub_job_personal_data(JOB_ID)
                clear_session_draft(str(job.get("client_id") or ""))
            except Exception:
                pass
            return 1
        except Exception as exc:  # pragma: no cover
            update_job(
                {
                    "status": "failed",
                    "progress": 100,
                    "message": "Generation failed",
                    "error": str(exc),
                    "current_step": "failed",
                    "status_line": "Unexpected render failure.",
                    "finished_at": time.time(),
                }
            )
            try:
                scrub_job_personal_data(JOB_ID)
                clear_session_draft(str(job.get("client_id") or ""))
            except Exception:
                pass
            return 1
        finally:
            try:
                dispatch_once()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
