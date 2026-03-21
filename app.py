import json
import os
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.config import Config
from botocore.exceptions import ClientError
from flask import Flask, abort, jsonify, redirect, request, send_file, send_from_directory, url_for

from arminator_common import (
    BASE_DIR,
    JOB_RETENTION_HOURS,
    JOBS_DIR,
    PART_OPTIONS,
    PUBLIC_FIELDS,
    JobState,
    build_archive_name,
    build_render_command,
    build_request_hash,
    from_dynamodb_value,
    get_job_directory,
    make_output_filename,
    resolve_selected_parts,
    to_dynamodb_value,
    validate_parameters,
)


AWS_MODE = os.environ.get("USE_AWS_BACKEND", "").lower() in {"1", "true", "yes"}
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-2")
ARMINATOR_JOBS_TABLE = os.environ.get("ARMINATOR_JOBS_TABLE", "")
ARMINATOR_ARTIFACTS_BUCKET = os.environ.get("ARMINATOR_ARTIFACTS_BUCKET", "")
ARMINATOR_ECS_CLUSTER_ARN = os.environ.get("ARMINATOR_ECS_CLUSTER_ARN", "")
ARMINATOR_RENDERER_TASK_DEFINITION_ARN = os.environ.get("ARMINATOR_RENDERER_TASK_DEFINITION_ARN", "")
ARMINATOR_RENDERER_SUBNETS = [
    subnet.strip() for subnet in os.environ.get("ARMINATOR_RENDERER_SUBNETS", "").split(",") if subnet.strip()
]
ARMINATOR_RENDERER_SECURITY_GROUP = os.environ.get("ARMINATOR_RENDERER_SECURITY_GROUP", "")

ACTIVE_STATUSES = {"queued", "starting", "running"}
TERMINAL_STATUSES = {"completed", "failed", "canceled"}
DISPATCH_LOCK_JOB_ID = "__dispatch_lock__"
DISPATCH_LOCK_TTL_SECONDS = 60


app = Flask(__name__)
SITE_DIR = BASE_DIR / "site"
jobs_lock = threading.Lock()
queue_lock = threading.Lock()
queue_condition = threading.Condition(queue_lock)
background_thread_lock = threading.Lock()
background_thread_started = False

jobs: Dict[str, JobState] = {}
queued_job_ids: Deque[str] = deque()
running_job_id: Optional[str] = None
job_processes: Dict[str, subprocess.Popen] = {}
request_to_job_id: Dict[str, str] = {}
client_active_jobs: Dict[str, str] = {}


if AWS_MODE:
    _session = boto3.session.Session(region_name=AWS_REGION)
    dynamodb = _session.resource("dynamodb")
    ddb_table = dynamodb.Table(ARMINATOR_JOBS_TABLE)
    s3_client = _session.client(
        "s3",
        endpoint_url=f"https://s3.{AWS_REGION}.amazonaws.com",
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )
    ecs_client = _session.client("ecs")
else:
    ddb_table = None
    s3_client = None
    ecs_client = None


def active_status(status: str) -> bool:
    return status in ACTIVE_STATUSES


def local_job_to_payload(job: JobState) -> Dict[str, Any]:
    payload = {
        "job_id": job.job_id,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "selected_parts": job.selected_parts,
        "output_files": job.output_files,
        "current_part": job.current_part,
        "current_part_index": job.current_part_index,
        "total_parts": job.total_parts,
        "completed_parts": job.completed_parts,
        "queue_position": job.queue_position,
        "current_step": job.current_step,
        "status_line": job.status_line,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "updated_at": job.updated_at,
        "cached": job.cached,
        "duplicate_of": job.duplicate_of,
    }
    if job.download_name:
        payload["download_url"] = url_for("download_job_api", job_id=job.job_id, _external=False)
    return payload


def set_job_state(job_id: str, **changes: Any) -> None:
    with jobs_lock:
        job = jobs[job_id]
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = time.time()


def update_queue_positions() -> None:
    with jobs_lock, queue_lock:
        for job in jobs.values():
            if job.status == "running":
                job.queue_position = 0
            elif job.status == "queued" and job.job_id in queued_job_ids:
                job.queue_position = list(queued_job_ids).index(job.job_id) + 1
            else:
                job.queue_position = 0


def release_client_lock(job: JobState) -> None:
    with jobs_lock:
        current = client_active_jobs.get(job.client_id)
        if current == job.job_id:
            client_active_jobs.pop(job.client_id, None)


def cleanup_old_jobs() -> None:
    if AWS_MODE:
        return

    cutoff = time.time() - (JOB_RETENTION_HOURS * 3600)

    with jobs_lock:
        stale_jobs = [
            job_id
            for job_id, job in jobs.items()
            if job.updated_at < cutoff and job.status in TERMINAL_STATUSES
        ]
        for job_id in stale_jobs:
            job = jobs.pop(job_id, None)
            if not job:
                continue
            if request_to_job_id.get(job.request_hash) == job_id:
                request_to_job_id.pop(job.request_hash, None)
            if client_active_jobs.get(job.client_id) == job_id:
                client_active_jobs.pop(job.client_id, None)

    with queue_lock:
        active_queue = deque(job_id for job_id in queued_job_ids if job_id not in stale_jobs)
        queued_job_ids.clear()
        queued_job_ids.extend(active_queue)

    if not JOBS_DIR.exists():
        return

    for child in JOBS_DIR.iterdir():
        try:
            if child.stat().st_mtime < cutoff:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        except FileNotFoundError:
            continue


def ensure_background_worker_started() -> None:
    global background_thread_started
    with background_thread_lock:
        if background_thread_started:
            return
        if AWS_MODE:
            threading.Thread(target=aws_dispatch_loop, daemon=True).start()
        else:
            threading.Thread(target=worker_loop, daemon=True).start()
        background_thread_started = True


def run_openscad_with_heartbeat(job_id: str, command: List[str], part: str) -> Tuple[str, str]:
    process = subprocess.Popen(
        command,
        cwd=BASE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    with jobs_lock:
        job_processes[job_id] = process

    try:
        while True:
            return_code = process.poll()
            set_job_state(
                job_id,
                current_step="rendering",
                status_line=f"OpenSCAD is still rendering {part}. This model can take several minutes.",
            )
            if return_code is not None:
                stdout, stderr = process.communicate()
                with jobs_lock:
                    cancel_requested = jobs[job_id].cancel_requested
                if cancel_requested:
                    raise RuntimeError("canceled")
                if return_code != 0:
                    detail = stderr.strip() or stdout.strip() or "OpenSCAD exited with a non-zero status."
                    raise RuntimeError(f"Failed while rendering {part}: {detail}")
                return stdout, stderr
            time.sleep(1.0)
    finally:
        with jobs_lock:
            job_processes.pop(job_id, None)


def render_part(job_id: str, part: str, parameters: Dict[str, Any], output_path: Path) -> None:
    command = build_render_command(output_path, {**parameters, "Part": part})
    stdout, stderr = run_openscad_with_heartbeat(job_id, command, part)

    log_path = get_job_directory(job_id) / "render.log"
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"\n$ {' '.join(command)}\n")
        if stdout:
            log_file.write(stdout)
        if stderr:
            log_file.write(stderr)


def mark_job_canceled(job_id: str, message: str) -> None:
    set_job_state(
        job_id,
        status="canceled",
        progress=100,
        message=message,
        current_part=None,
        current_step="canceled",
        status_line=message,
        finished_at=time.time(),
    )
    with jobs_lock:
        job = jobs[job_id]
        if request_to_job_id.get(job.request_hash) == job_id:
            request_to_job_id.pop(job.request_hash, None)
    release_client_lock(jobs[job_id])


def run_job(job_id: str) -> None:
    with jobs_lock:
        job = jobs[job_id]
        parameters = dict(job.parameters)
        selected_parts = list(job.selected_parts)

    job_dir = get_job_directory(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    handedness = str(parameters["LeftRight"])
    total_parts = len(selected_parts)
    rendered_files: List[Path] = []

    try:
        with jobs_lock:
            if job.cancel_requested:
                raise RuntimeError("canceled-before-start")

        set_job_state(
            job_id,
            status="running",
            started_at=time.time(),
            progress=0,
            message="Preparing render workspace",
            total_parts=total_parts,
            completed_parts=0,
            current_step="preparing",
            status_line="Preparing OpenSCAD render workspace.",
        )

        for index, part in enumerate(selected_parts, start=1):
            with jobs_lock:
                if jobs[job_id].cancel_requested:
                    raise RuntimeError("canceled")

            set_job_state(
                job_id,
                progress=int(((index - 1) / total_parts) * 100),
                message=f"Rendering {part} ({index}/{total_parts})",
                current_part=part,
                current_part_index=index,
                current_step="rendering",
                status_line=f"Queued render step started for {part}.",
            )

            output_name = make_output_filename(index, part, str(parameters["LeftRight"]))
            output_path = job_dir / output_name
            render_part(job_id, part, parameters, output_path)
            rendered_files.append(output_path)

            set_job_state(
                job_id,
                progress=int((index / total_parts) * 100),
                message=f"Rendered {part} ({index}/{total_parts})",
                output_files=[path.name for path in rendered_files],
                completed_parts=index,
                status_line=f"Finished {part}.",
            )

        set_job_state(
            job_id,
            progress=100,
            message="Building ZIP archive",
            current_step="packaging",
            status_line="Compressing generated STL files into a ZIP archive.",
        )

        archive_path = job_dir / build_archive_name(parameters)
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for rendered_file in rendered_files:
                archive.write(rendered_file, arcname=rendered_file.name)

        set_job_state(
            job_id,
            status="completed",
            progress=100,
            message="Render complete",
            download_name=archive_path.name,
            output_files=[path.name for path in rendered_files],
            current_part=None,
            current_part_index=total_parts,
            completed_parts=total_parts,
            current_step="completed",
            status_line="ZIP archive is ready for download.",
            finished_at=time.time(),
        )
    except RuntimeError as exc:
        if str(exc) in {"canceled", "canceled-before-start"}:
            mark_job_canceled(job_id, "Render canceled.")
        else:
            set_job_state(
                job_id,
                status="failed",
                progress=100,
                message="Render failed",
                error=str(exc),
                current_step="failed",
                status_line="OpenSCAD exited with an error.",
                finished_at=time.time(),
            )
            with jobs_lock:
                failed_job = jobs[job_id]
                if request_to_job_id.get(failed_job.request_hash) == job_id:
                    request_to_job_id.pop(failed_job.request_hash, None)
            release_client_lock(jobs[job_id])
    except Exception as exc:  # pragma: no cover
        set_job_state(
            job_id,
            status="failed",
            progress=100,
            message="Render failed",
            error=str(exc),
            current_step="failed",
            status_line="Unexpected render failure.",
            finished_at=time.time(),
        )
        with jobs_lock:
            failed_job = jobs[job_id]
            if request_to_job_id.get(failed_job.request_hash) == job_id:
                request_to_job_id.pop(failed_job.request_hash, None)
        release_client_lock(jobs[job_id])
    finally:
        release_client_lock(jobs[job_id])


def worker_loop() -> None:
    global running_job_id
    while True:
        with queue_condition:
            while not queued_job_ids:
                queue_condition.wait()
            job_id = queued_job_ids.popleft()
            running_job_id = job_id
        update_queue_positions()
        run_job(job_id)
        with queue_condition:
            running_job_id = None
        update_queue_positions()


def require_client_id(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[Tuple[Dict[str, Any], int]]]:
    client_id = str(payload.get("client_id") or "").strip()
    if not client_id:
        return None, ({"error": "Missing client identifier."}, 400)
    return client_id, None


def enqueue_job(job: JobState) -> None:
    with jobs_lock:
        jobs[job.job_id] = job
        request_to_job_id[job.request_hash] = job.job_id
        client_active_jobs[job.client_id] = job.job_id

    with queue_condition:
        queued_job_ids.append(job.job_id)
        queue_condition.notify()

    update_queue_positions()


def aws_scan_all(filter_expression=None) -> List[Dict[str, Any]]:
    assert ddb_table is not None
    items: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {"ConsistentRead": True}
    if filter_expression is not None:
        kwargs["FilterExpression"] = filter_expression

    while True:
        response = ddb_table.scan(**kwargs)
        items.extend(from_dynamodb_value(item) for item in response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    return items


def aws_get_job_record(job_id: str) -> Optional[Dict[str, Any]]:
    assert ddb_table is not None
    response = ddb_table.get_item(Key={"job_id": job_id}, ConsistentRead=True)
    item = response.get("Item")
    return from_dynamodb_value(item) if item else None


def aws_set_fields(job_id: str, changes: Dict[str, Any], condition_expression=None) -> Dict[str, Any]:
    assert ddb_table is not None
    payload = dict(changes)
    payload.setdefault("updated_at", time.time())
    expression_names: Dict[str, str] = {}
    expression_values: Dict[str, Any] = {}
    assignments: List[str] = []

    for index, (key, value) in enumerate(payload.items()):
        name_ref = f"#upd{index}"
        value_ref = f":upd{index}"
        expression_names[name_ref] = key
        expression_values[value_ref] = to_dynamodb_value(value)
        assignments.append(f"{name_ref} = {value_ref}")

    response = ddb_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET " + ", ".join(assignments),
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values,
        ConditionExpression=condition_expression,
        ReturnValues="ALL_NEW",
    )
    return from_dynamodb_value(response["Attributes"])


def aws_put_job_record(record: Dict[str, Any]) -> None:
    assert ddb_table is not None
    ddb_table.put_item(Item=to_dynamodb_value(record))


def aws_try_acquire_dispatch_lock(holder: str) -> bool:
    assert ddb_table is not None
    now = int(time.time())
    expires_at = now + DISPATCH_LOCK_TTL_SECONDS
    try:
        ddb_table.put_item(
            Item=to_dynamodb_value(
                {
                    "job_id": DISPATCH_LOCK_JOB_ID,
                    "holder": holder,
                    "expires_at": expires_at,
                    "updated_at": time.time(),
                }
            ),
            ConditionExpression="attribute_not_exists(job_id) OR expires_at < :now",
            ExpressionAttributeValues={":now": now},
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def aws_release_dispatch_lock(holder: str) -> None:
    assert ddb_table is not None
    try:
        ddb_table.delete_item(
            Key={"job_id": DISPATCH_LOCK_JOB_ID},
            ConditionExpression="holder = :holder",
            ExpressionAttributeValues={":holder": holder},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def aws_sort_jobs(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(items, key=lambda item: (float(item.get("created_at", 0)), item["job_id"]))


def aws_pick_existing_by_hash(request_hash: str) -> Optional[Dict[str, Any]]:
    matches = aws_scan_all(
        Attr("request_hash").eq(request_hash) & Attr("status").is_in(list(ACTIVE_STATUSES | {"completed"}))
    )
    if not matches:
        return None

    active = [item for item in matches if item.get("status") in ACTIVE_STATUSES]
    if active:
        return aws_sort_jobs(active)[0]

    completed = [item for item in matches if item.get("status") == "completed"]
    if completed:
        return sorted(completed, key=lambda item: float(item.get("updated_at", 0)), reverse=True)[0]
    return None


def aws_pick_active_for_client(client_id: str) -> Optional[Dict[str, Any]]:
    matches = aws_scan_all(
        Attr("client_id").eq(client_id) & Attr("status").is_in(list(ACTIVE_STATUSES))
    )
    if not matches:
        return None
    return aws_sort_jobs(matches)[0]


def aws_queue_position(job: Dict[str, Any]) -> int:
    status = job.get("status")
    if status not in {"queued", "starting"}:
        return 0
    queued = aws_sort_jobs(aws_scan_all(Attr("status").eq("queued")))
    if status == "starting":
        return 0
    job_ids = [item["job_id"] for item in queued]
    return job_ids.index(job["job_id"]) + 1 if job["job_id"] in job_ids else 0


def aws_job_to_payload(job: Dict[str, Any], cached: Optional[bool] = None) -> Dict[str, Any]:
    payload = {
        "job_id": job["job_id"],
        "status": job.get("status", "queued"),
        "progress": int(job.get("progress", 0)),
        "message": job.get("message", "Queued"),
        "error": job.get("error"),
        "selected_parts": job.get("selected_parts", []),
        "output_files": job.get("output_files", []),
        "current_part": job.get("current_part"),
        "current_part_index": int(job.get("current_part_index", 0)),
        "total_parts": int(job.get("total_parts", 0)),
        "completed_parts": int(job.get("completed_parts", 0)),
        "queue_position": aws_queue_position(job),
        "current_step": job.get("current_step", "queued"),
        "status_line": job.get("status_line", ""),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "updated_at": job.get("updated_at"),
        "cached": cached if cached is not None else bool(job.get("cached", False)),
        "duplicate_of": job.get("duplicate_of"),
    }
    if job.get("archive_key") and job.get("status") == "completed":
        payload["download_url"] = url_for("download_job_api", job_id=job["job_id"], _external=False)
    return payload


def aws_run_renderer_task(job_id: str) -> str:
    assert ecs_client is not None
    response = ecs_client.run_task(
        cluster=ARMINATOR_ECS_CLUSTER_ARN,
        taskDefinition=ARMINATOR_RENDERER_TASK_DEFINITION_ARN,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": ARMINATOR_RENDERER_SUBNETS,
                "securityGroups": [ARMINATOR_RENDERER_SECURITY_GROUP],
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": "renderer",
                    "environment": [{"name": "ARMINATOR_JOB_ID", "value": job_id}],
                }
            ]
        },
    )
    failures = response.get("failures", [])
    if failures:
        detail = failures[0].get("reason") or failures[0].get("detail") or "Unknown ECS RunTask failure."
        raise RuntimeError(detail)
    tasks = response.get("tasks", [])
    if not tasks:
        raise RuntimeError("ECS RunTask returned no task ARN.")
    return tasks[0]["taskArn"]


def aws_dispatch_once() -> None:
    if not all(
        [
            ARMINATOR_JOBS_TABLE,
            ARMINATOR_ARTIFACTS_BUCKET,
            ARMINATOR_ECS_CLUSTER_ARN,
            ARMINATOR_RENDERER_TASK_DEFINITION_ARN,
            ARMINATOR_RENDERER_SUBNETS,
            ARMINATOR_RENDERER_SECURITY_GROUP,
        ]
    ):
        return

    lock_holder = uuid.uuid4().hex
    if not aws_try_acquire_dispatch_lock(lock_holder):
        return

    try:
        active_jobs = aws_scan_all(Attr("status").is_in(["starting", "running"]))
        if active_jobs:
            return

        queued_jobs = aws_sort_jobs(aws_scan_all(Attr("status").eq("queued")))
        for job in queued_jobs:
            try:
                aws_set_fields(
                    job["job_id"],
                    {
                        "status": "starting",
                        "message": "Starting render worker",
                        "current_step": "starting",
                        "status_line": "Requesting a renderer task from ECS.",
                    },
                    condition_expression=Attr("status").eq("queued"),
                )
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    continue
                raise

            try:
                task_arn = aws_run_renderer_task(job["job_id"])
                aws_set_fields(
                    job["job_id"],
                    {
                        "task_arn": task_arn,
                        "status_line": "Renderer task launched. Waiting for the worker to start.",
                    },
                )
            except Exception as exc:
                aws_set_fields(
                    job["job_id"],
                    {
                        "status": "failed",
                        "progress": 100,
                        "message": "Failed to launch render worker",
                        "error": str(exc),
                        "current_step": "failed",
                        "status_line": "ECS could not start the renderer task.",
                        "finished_at": time.time(),
                    },
                )
            return
    finally:
        aws_release_dispatch_lock(lock_holder)


def aws_dispatch_loop() -> None:
    while True:
        try:
            aws_dispatch_once()
        except Exception:
            pass
        time.sleep(5)


def aws_create_job(payload: Dict[str, Any], client_id: str):
    parameters, errors = validate_parameters(payload)
    selected_parts = resolve_selected_parts(payload.get("parts"))

    if errors:
        return jsonify({"errors": errors}), 400

    request_hash = build_request_hash(parameters, selected_parts)
    existing_job = aws_pick_existing_by_hash(request_hash)
    if existing_job:
        existing_payload = aws_job_to_payload(existing_job, cached=existing_job.get("status") == "completed")
        existing_payload["reused_existing_job"] = True
        return (
            jsonify(existing_payload),
            200 if existing_job.get("status") == "completed" else 202,
        )

    active_job = aws_pick_active_for_client(client_id)
    if active_job:
        active_payload = aws_job_to_payload(active_job)
        active_payload["active_job_exists"] = True
        active_payload["error"] = "A render job is already active for this browser."
        return jsonify(active_payload), 409

    now = time.time()
    job_id = uuid.uuid4().hex
    record = {
        "job_id": job_id,
        "request_hash": request_hash,
        "created_at": now,
        "updated_at": now,
        "client_id": client_id,
        "status": "queued",
        "progress": 0,
        "message": "Queued",
        "selected_parts": selected_parts,
        "output_files": [],
        "parameters": parameters,
        "current_part": None,
        "current_part_index": 0,
        "total_parts": len(selected_parts),
        "completed_parts": 0,
        "current_step": "queued",
        "status_line": "Waiting in the render queue.",
        "started_at": None,
        "finished_at": None,
        "cached": False,
        "duplicate_of": None,
        "cancel_requested": False,
        "download_name": None,
        "archive_key": None,
        "task_arn": None,
        "expires_at": int(now + (JOB_RETENTION_HOURS * 3600)),
    }
    aws_put_job_record(record)
    return jsonify(aws_job_to_payload(record)), 202


def aws_get_job(job_id: str):
    job = aws_get_job_record(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(aws_job_to_payload(job))


def aws_cancel_job(job_id: str, client_id: str):
    job = aws_get_job_record(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if job.get("client_id") != client_id:
        return jsonify({"error": "This job belongs to a different browser session."}), 403
    if job.get("status") in TERMINAL_STATUSES:
        return jsonify(aws_job_to_payload(job)), 200

    if job.get("status") == "queued":
        updated = aws_set_fields(
            job_id,
            {
                "status": "canceled",
                "progress": 100,
                "message": "Queued render canceled.",
                "current_step": "canceled",
                "status_line": "Queued render canceled.",
                "finished_at": time.time(),
                "cancel_requested": True,
            },
        )
        return jsonify(aws_job_to_payload(updated)), 200

    updated = aws_set_fields(
        job_id,
        {
            "cancel_requested": True,
            "status_line": "Cancel requested. Waiting for the renderer to stop.",
        },
    )
    task_arn = job.get("task_arn")
    if task_arn:
        try:
            assert ecs_client is not None
            ecs_client.stop_task(cluster=ARMINATOR_ECS_CLUSTER_ARN, task=task_arn, reason="Canceled by user")
        except Exception:
            pass
    return jsonify(aws_job_to_payload(updated)), 202


@app.get("/")
def index() -> Any:
    return send_from_directory(SITE_DIR, "index.html")


@app.get("/<path:asset_path>")
def site_assets(asset_path: str) -> Any:
    if asset_path.startswith(("api/", "downloads/", "healthz")):
        abort(404)
    target = SITE_DIR / asset_path
    if not target.exists() or target.is_dir():
        abort(404)
    return send_from_directory(SITE_DIR, asset_path)


@app.get("/healthz")
def healthcheck():
    ensure_background_worker_started()
    cleanup_old_jobs()
    return jsonify({"status": "ok", "backend": "aws" if AWS_MODE else "local"})


@app.get("/api/config")
def config():
    sections: Dict[str, List[Dict[str, Any]]] = {}
    for field in PUBLIC_FIELDS:
        sections.setdefault(field["section"], []).append(field)

    return jsonify(
        {
            "title": "Build a printable UnLimbited Arm kit",
            "subtitle": "Measurements are captured here, then the STL files are rendered on demand and zipped for download.",
            "part_options": PART_OPTIONS,
            "sections": [{"name": name, "fields": fields} for name, fields in sections.items()],
        }
    )


@app.post("/api/jobs")
def create_job():
    ensure_background_worker_started()
    cleanup_old_jobs()
    payload = request.get_json(silent=True) or {}
    client_id, error_response = require_client_id(payload)
    if error_response:
        body, status = error_response
        return jsonify(body), status

    if AWS_MODE:
        return aws_create_job(payload, client_id)

    parameters, errors = validate_parameters(payload)
    selected_parts = resolve_selected_parts(payload.get("parts"))

    if errors:
        return jsonify({"errors": errors}), 400

    request_hash = build_request_hash(parameters, selected_parts)

    with jobs_lock:
        existing_by_hash_id = request_to_job_id.get(request_hash)
        if existing_by_hash_id:
            existing_job = jobs.get(existing_by_hash_id)
            if existing_job and existing_job.status in {"queued", "running", "completed"}:
                existing_payload = local_job_to_payload(existing_job)
                existing_payload["reused_existing_job"] = True
                return jsonify(existing_payload), 200 if existing_job.status == "completed" else 202
            request_to_job_id.pop(request_hash, None)

        active_job_id = client_active_jobs.get(client_id)
        if active_job_id:
            active_job = jobs.get(active_job_id)
            if active_job and active_status(active_job.status):
                active_payload = local_job_to_payload(active_job)
                active_payload["active_job_exists"] = True
                active_payload["error"] = "A render job is already active for this browser."
                return jsonify(active_payload), 409
            client_active_jobs.pop(client_id, None)

    job_id = uuid.uuid4().hex
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    job = JobState(
        job_id=job_id,
        request_hash=request_hash,
        created_at=time.time(),
        updated_at=time.time(),
        client_id=client_id,
        selected_parts=selected_parts,
        parameters=parameters,
        total_parts=len(selected_parts),
        current_step="queued",
        status_line="Waiting in the render queue.",
    )
    enqueue_job(job)
    return jsonify(local_job_to_payload(job)), 202


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    ensure_background_worker_started()
    cleanup_old_jobs()
    if AWS_MODE:
        return aws_get_job(job_id)

    update_queue_positions()
    with jobs_lock:
        job = jobs.get(job_id)

    if not job:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(local_job_to_payload(job))


@app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id: str):
    ensure_background_worker_started()
    payload = request.get_json(silent=True) or {}
    client_id, error_response = require_client_id(payload)
    if error_response:
        body, status = error_response
        return jsonify(body), status

    if AWS_MODE:
        return aws_cancel_job(job_id, client_id)

    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found."}), 404
        if job.client_id != client_id:
            return jsonify({"error": "This job belongs to a different browser session."}), 403
        if job.status in TERMINAL_STATUSES:
            return jsonify(local_job_to_payload(job)), 200
        job.cancel_requested = True
        job.updated_at = time.time()

    removed_from_queue = False
    with queue_condition:
        if job_id in queued_job_ids:
            queued_job_ids.remove(job_id)
            removed_from_queue = True

    if removed_from_queue:
        mark_job_canceled(job_id, "Queued render canceled.")
        update_queue_positions()
        return jsonify(local_job_to_payload(jobs[job_id])), 200

    with jobs_lock:
        process = job_processes.get(job_id)
        jobs[job_id].status_line = "Cancel requested. Waiting for OpenSCAD to stop."
        jobs[job_id].updated_at = time.time()
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    return jsonify(local_job_to_payload(jobs[job_id])), 202


@app.get("/downloads/<job_id>")
@app.get("/api/jobs/<job_id>/download", endpoint="download_job_api")
def download_job(job_id: str):
    if AWS_MODE:
        job = aws_get_job_record(job_id)
        if not job or job.get("status") != "completed" or not job.get("archive_key"):
            abort(404)
        assert s3_client is not None
        presigned = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": ARMINATOR_ARTIFACTS_BUCKET, "Key": job["archive_key"]},
            ExpiresIn=3600,
        )
        return redirect(presigned, code=302)

    with jobs_lock:
        job = jobs.get(job_id)

    if not job or job.status != "completed" or not job.download_name:
        abort(404)

    archive_path = get_job_directory(job_id) / job.download_name
    if not archive_path.exists():
        abort(404)

    return send_file(archive_path, as_attachment=True, download_name=archive_path.name)

if __name__ == "__main__":
    ensure_background_worker_started()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=False)
