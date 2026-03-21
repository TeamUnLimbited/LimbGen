import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import quote
from typing import Any, Dict, List, Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.config import Config
from botocore.exceptions import ClientError

from arminator_common import (
    ARM_VERSION_OPTIONS,
    JOB_RETENTION_HOURS,
    VERIFIED_SESSION_TTL_SECONDS,
    VERIFICATION_TOKEN_TTL_SECONDS,
    build_request_hash,
    from_dynamodb_value,
    get_part_labels,
    get_public_fields,
    humanize,
    resolve_selected_parts,
    to_dynamodb_value,
    validate_arm_version,
    validate_parameters,
    validate_requester_details,
)


AWS_REGION = os.environ.get("AWS_REGION", "eu-west-2")
ARMINATOR_JOBS_TABLE = os.environ.get("ARMINATOR_JOBS_TABLE", "")
ARMINATOR_ARTIFACTS_BUCKET = os.environ.get("ARMINATOR_ARTIFACTS_BUCKET", "")
ARMINATOR_ECS_CLUSTER_ARN = os.environ.get("ARMINATOR_ECS_CLUSTER_ARN", "")
ARMINATOR_RENDERER_TASK_DEFINITION_ARN = os.environ.get("ARMINATOR_RENDERER_TASK_DEFINITION_ARN", "")
ARMINATOR_RENDERER_SUBNETS = [
    subnet.strip() for subnet in os.environ.get("ARMINATOR_RENDERER_SUBNETS", "").split(",") if subnet.strip()
]
ARMINATOR_RENDERER_SECURITY_GROUP = os.environ.get("ARMINATOR_RENDERER_SECURITY_GROUP", "")
ARMINATOR_PUBLIC_BASE_URL = os.environ.get("ARMINATOR_PUBLIC_BASE_URL", "").rstrip("/")
ARMINATOR_EMAIL_FROM = os.environ.get("ARMINATOR_EMAIL_FROM", "")
ARMINATOR_EMAIL_REPLY_TO = os.environ.get("ARMINATOR_EMAIL_REPLY_TO", "")
ARMINATOR_REPORT_EMAIL_TO = os.environ.get("ARMINATOR_REPORT_EMAIL_TO", "drew@teamunlimbited.org").strip()
DONATION_URL = "https://www.paypal.com/donate/?cmd=_s-xclick&hosted_button_id=A64GWM82ZV3EE&source=url&ssrt=1774037086418"

ACTIVE_STATUSES = {"queued", "starting", "running"}
TERMINAL_STATUSES = {"completed", "failed", "canceled"}
DISPATCH_LOCK_JOB_ID = "__dispatch_lock__"
DISPATCH_LOCK_TTL_SECONDS = 60
STARTING_RECOVERY_SECONDS = 90
MAX_QUEUE_LENGTH = max(1, int(os.environ.get("ARMINATOR_MAX_QUEUE_LENGTH", "8")))
QUEUE_SLOT_ESTIMATE_SECONDS = max(15, int(os.environ.get("ARMINATOR_QUEUE_SLOT_ESTIMATE_SECONDS", "35")))
QUEUE_FULL_MESSAGE = "I'm really busy come back in a bit !"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SESSION_PREFIX = "session#"
VERIFY_PREFIX = "verify#"

_session = boto3.session.Session(region_name=AWS_REGION)
_ddb_table = _session.resource("dynamodb").Table(ARMINATOR_JOBS_TABLE)
_s3_client = _session.client(
    "s3",
    endpoint_url=f"https://s3.{AWS_REGION}.amazonaws.com",
    config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
)
_ecs_client = _session.client("ecs")
_ses_client = _session.client("sesv2")


REQUEST_FIELDS = [
    {"name": "name", "label": "Your Name", "kind": "text", "required": True, "autocomplete": "name"},
    {"name": "country", "label": "Country", "kind": "text", "required": True, "autocomplete": "country-name"},
    {
        "name": "purpose",
        "label": "This Device Is For",
        "kind": "radio",
        "required": True,
        "default": "recipient",
        "options": [
            {"value": "recipient", "label": "Recipient"},
            {"value": "project", "label": "Project"},
            {"value": "other", "label": "Other"},
        ],
    },
    {
        "name": "recipient_sex",
        "label": "Recipient Sex",
        "kind": "radio",
        "required": True,
        "default": "Male",
        "show_when": {"field": "purpose", "in": ["recipient"]},
        "options": [
            {"value": "Male", "label": "Male"},
            {"value": "Female", "label": "Female"},
        ],
    },
    {
        "name": "recipient_name",
        "label": "Recipient Name",
        "kind": "text",
        "required": True,
        "autocomplete": "name",
        "show_when": {"field": "purpose", "in": ["recipient"]},
    },
    {
        "name": "recipient_age",
        "label": "Recipient Age",
        "kind": "number_input",
        "required": True,
        "step": "1",
        "min": 0,
        "max": 120,
        "show_when": {"field": "purpose", "in": ["recipient"]},
    },
    {
        "name": "summary",
        "label": "Project Or Other Summary",
        "kind": "textarea",
        "required": False,
        "max_length": 280,
        "note": "Optional, up to 280 characters.",
        "show_when": {"field": "purpose", "in": ["project", "other"]},
    },
]


def session_key(client_id: str) -> str:
    return f"{SESSION_PREFIX}{client_id}"


def verification_key(token: str) -> str:
    return f"{VERIFY_PREFIX}{token}"


def infer_viewer_country_code(headers: Optional[Dict[str, str]]) -> str:
    headers = headers or {}
    for key, value in headers.items():
        if key.lower() == "cloudfront-viewer-country":
            return str(value or "").strip().upper()
    return ""


def get_record(record_key: str) -> Optional[Dict[str, Any]]:
    response = _ddb_table.get_item(Key={"job_id": record_key}, ConsistentRead=True)
    item = response.get("Item")
    return from_dynamodb_value(item) if item else None


def get_verified_session(client_id: str) -> Optional[Dict[str, Any]]:
    record = get_record(session_key(client_id))
    if not record:
        return None
    if int(record.get("expires_at", 0) or 0) < int(time.time()):
        return None
    if not record.get("verified"):
        return None
    return record


def validate_email_address(email: str) -> bool:
    return bool(EMAIL_RE.match(email))


def send_email(recipient: str, subject: str, text_body: str, html_body: str) -> None:
    if not ARMINATOR_EMAIL_FROM:
        raise RuntimeError("Email sending is not configured.")

    kwargs: Dict[str, Any] = {
        "FromEmailAddress": ARMINATOR_EMAIL_FROM,
        "Destination": {"ToAddresses": [recipient]},
        "Content": {
            "Simple": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                },
            }
        },
    }
    if ARMINATOR_EMAIL_REPLY_TO:
        kwargs["ReplyToAddresses"] = [ARMINATOR_EMAIL_REPLY_TO]
    _ses_client.send_email(**kwargs)


def format_utc_timestamp(timestamp: Optional[float]) -> str:
    if not timestamp:
        return ""
    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()


def form_sections(arm_version: Optional[str]) -> List[Dict[str, Any]]:
    sections: Dict[str, List[Dict[str, Any]]] = {}
    if arm_version:
        for field in get_public_fields(arm_version):
            sections.setdefault(field["section"], []).append(field)
    ordered_sections = [{"name": "Request Details", "fields": REQUEST_FIELDS}]
    ordered_sections.extend({"name": section_name, "fields": fields} for section_name, fields in sections.items())
    return ordered_sections


def frontend_config(arm_version: Optional[str] = None, viewer_country_code: str = "") -> Dict[str, Any]:
    resolved_arm_version = arm_version if arm_version in {option["value"] for option in ARM_VERSION_OPTIONS} else None
    return {
        "title": "Build a printable UnLimbited assistive device kit",
        "subtitle": "Measurements are captured here, then the full assistive device kit is generated on demand and zipped for download.",
        "arm_versions": ARM_VERSION_OPTIONS,
        "selected_arm_version": resolved_arm_version,
        "part_options": get_part_labels(resolved_arm_version) if resolved_arm_version else [],
        "sections": form_sections(resolved_arm_version),
        "viewer_country_code": viewer_country_code,
    }


def scan_all(filter_expression=None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {"ConsistentRead": True}
    if filter_expression is not None:
        kwargs["FilterExpression"] = filter_expression

    while True:
        response = _ddb_table.scan(**kwargs)
        items.extend(from_dynamodb_value(item) for item in response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    return items


def get_job_record(job_id: str) -> Optional[Dict[str, Any]]:
    response = _ddb_table.get_item(Key={"job_id": job_id}, ConsistentRead=True)
    item = response.get("Item")
    return from_dynamodb_value(item) if item else None


def set_job_fields(job_id: str, changes: Dict[str, Any], condition_expression=None) -> Dict[str, Any]:
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

    update_kwargs: Dict[str, Any] = {
        "Key": {"job_id": job_id},
        "UpdateExpression": "SET " + ", ".join(assignments),
        "ExpressionAttributeNames": expression_names,
        "ExpressionAttributeValues": expression_values,
        "ReturnValues": "ALL_NEW",
    }
    if condition_expression is not None:
        update_kwargs["ConditionExpression"] = condition_expression

    response = _ddb_table.update_item(**update_kwargs)
    return from_dynamodb_value(response["Attributes"])


def put_job_record(record: Dict[str, Any]) -> None:
    _ddb_table.put_item(Item=to_dynamodb_value(record))


def sort_jobs(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(items, key=lambda item: (float(item.get("created_at", 0)), item["job_id"]))


def queue_metrics(job: Dict[str, Any]) -> Tuple[int, int, Optional[int]]:
    status = job.get("status")
    if status not in {"queued", "starting"}:
        return 0, 0, None
    if status == "starting":
        return 0, 0, 0

    queued = sort_jobs(scan_all(Attr("status").eq("queued")))
    job_ids = [item["job_id"] for item in queued]
    if job["job_id"] not in job_ids:
        return 0, 0, None

    position = job_ids.index(job["job_id"]) + 1
    active_count = len(scan_all(Attr("status").is_in(["starting", "running"])))
    slots_ahead = active_count + max(0, position - 1)
    return position, slots_ahead, slots_ahead * QUEUE_SLOT_ESTIMATE_SECONDS


def pick_active_by_hash(request_hash: str) -> Optional[Dict[str, Any]]:
    matches = scan_all(
        Attr("request_hash").eq(request_hash) & Attr("status").is_in(list(ACTIVE_STATUSES))
    )
    if not matches:
        return None

    active = [item for item in matches if item.get("status") in ACTIVE_STATUSES]
    if active:
        return sort_jobs(active)[0]
    return None


def pick_active_for_client(client_id: str) -> Optional[Dict[str, Any]]:
    matches = scan_all(Attr("client_id").eq(client_id) & Attr("status").is_in(list(ACTIVE_STATUSES)))
    if not matches:
        return None
    return sort_jobs(matches)[0]


def pick_active_for_email(email: str) -> Optional[Dict[str, Any]]:
    matches = scan_all(Attr("verified_email").eq(email) & Attr("status").is_in(list(ACTIVE_STATUSES)))
    if not matches:
        return None
    return sort_jobs(matches)[0]


def queue_position(job: Dict[str, Any]) -> int:
    position, _, _ = queue_metrics(job)
    return position


def parameter_fields_for_job(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    arm_version = str(job.get("arm_version") or "").strip().lower()
    if not arm_version:
        return []
    return get_public_fields(arm_version)


def job_to_payload(job: Dict[str, Any], cached: Optional[bool] = None, api_prefix: str = "/api") -> Dict[str, Any]:
    position, slots_ahead, estimated_wait_seconds = queue_metrics(job)
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
        "queue_position": position,
        "queue_slots_ahead": slots_ahead,
        "estimated_wait_seconds": estimated_wait_seconds,
        "current_step": job.get("current_step", "queued"),
        "status_line": job.get("status_line", ""),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "updated_at": job.get("updated_at"),
        "cached": cached if cached is not None else bool(job.get("cached", False)),
        "duplicate_of": job.get("duplicate_of"),
        "arm_version": job.get("arm_version"),
        "requester": job.get("requester", {}),
        "parameters": job.get("parameters", {}),
    }
    if job.get("archive_key") and job.get("status") == "completed":
        payload["download_url"] = f"{api_prefix}/jobs/{job['job_id']}/download"
    return payload


def require_render_configuration() -> None:
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
        raise RuntimeError("AWS render configuration is incomplete.")


def get_session_payload(client_id: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    session = get_verified_session(client_id)
    return {
        "verified": bool(session),
        "email": session.get("email") if session else None,
        "notify_completed": bool(session.get("notify_completed", True)) if session else True,
        "draft": session.get("draft") if session else None,
        "viewer_country_code": infer_viewer_country_code(headers),
    }


def request_verification_link(
    payload: Dict[str, Any],
    client_id: str,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Dict[str, Any]]:
    email = str(payload.get("email") or "").strip()
    notify_completed = bool(payload.get("notify_completed", True))
    draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else None
    if not validate_email_address(email):
        return 400, {"error": "Enter a valid email address."}
    if not ARMINATOR_PUBLIC_BASE_URL:
        return 500, {"error": "Verification links are not configured yet."}

    now = time.time()
    token = uuid.uuid4().hex
    put_job_record(
        {
            "job_id": verification_key(token),
            "type": "verification_token",
            "token": token,
            "email": email,
            "client_id": client_id,
            "notify_completed": notify_completed,
            "draft": draft,
            "created_at": now,
            "updated_at": now,
            "expires_at": int(now + VERIFICATION_TOKEN_TTL_SECONDS),
            "used": False,
        }
    )

    verification_url = f"{ARMINATOR_PUBLIC_BASE_URL}/?verify={quote(token)}"
    text_body = (
        "Verify your email to start generating your UnLimbited assistive device kit.\n\n"
        f"Open this link in your browser:\n{verification_url}\n\n"
        "After verification, return to the form, select the device, and generate the parts."
    )
    html_body = (
        "<p>Verify your email to start generating your UnLimbited assistive device kit.</p>"
        f"<p><a href=\"{verification_url}\">Verify email and return to the generator</a></p>"
        "<p>After verification, return to the form, select the device, and generate the parts.</p>"
    )

    try:
        send_email(email, "Verify your Team UnLimbited generator email", text_body, html_body)
    except Exception as exc:
        return 502, {"error": f"Could not send the verification email: {exc}"}

    payload = get_session_payload(client_id, headers=headers)
    payload["message"] = f"Verification link sent to {email}."
    return 200, payload


def confirm_verification_token(token: str, client_id: str, headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, Any]]:
    record = get_record(verification_key(token))
    now = time.time()
    if not record:
        return 404, {"error": "Verification link not found."}
    if record.get("used"):
        return 400, {"error": "This verification link has already been used."}
    if int(record.get("expires_at", 0) or 0) < int(now):
        return 400, {"error": "This verification link has expired."}

    put_job_record(
        {
            "job_id": session_key(client_id),
            "type": "session",
            "client_id": client_id,
            "verified": True,
            "email": record["email"],
            "notify_completed": bool(record.get("notify_completed", True)),
            "draft": record.get("draft"),
            "verified_at": now,
            "updated_at": now,
            "expires_at": int(now + VERIFIED_SESSION_TTL_SECONDS),
        }
    )
    set_job_fields(
        verification_key(token),
        {
            "used": True,
            "used_at": now,
            "verified_client_id": client_id,
            "email": None,
            "draft": None,
        },
    )
    payload = get_session_payload(client_id, headers=headers)
    payload["message"] = f"Verified as {record['email']}."
    return 200, payload


def send_completion_email(job: Dict[str, Any]) -> None:
    recipient = str(job.get("verified_email") or "").strip()
    if not recipient or not job.get("notify_completed"):
        return
    if not ARMINATOR_PUBLIC_BASE_URL:
        return

    download_url = f"{ARMINATOR_PUBLIC_BASE_URL}/api/jobs/{job['job_id']}/download"
    archive_name = job.get("download_name") or "generated-kit.zip"
    requester = dict(job.get("requester") or {})
    parameters = dict(job.get("parameters") or {})
    arm_version = str(job.get("arm_version") or "").upper() or "-"

    requester_lines: List[str] = [
        f"Arm Version: {arm_version}",
        f"Requester Name: {requester.get('name') or '-'}",
        f"Country: {requester.get('country') or '-'}",
        f"This Device Is For: {str(requester.get('purpose') or '-').title()}",
    ]
    purpose = str(requester.get("purpose") or "").lower()
    if purpose == "recipient":
        requester_lines.extend(
            [
                f"Recipient Name: {requester.get('recipient_name') or '-'}",
                f"Recipient Sex: {requester.get('recipient_sex') or '-'}",
                f"Recipient Age: {requester.get('recipient_age') or '-'}",
            ]
        )
    elif purpose in {"project", "other"}:
        requester_lines.append(f"{purpose.title()} Summary: {requester.get('summary') or '-'}")

    parameter_lines = [
        f"{field['label']}: {parameters.get(field['name'])}"
        for field in parameter_fields_for_job(job)
        if field["name"] in parameters
    ]

    requester_text = "\n".join(requester_lines)
    parameters_text = "\n".join(parameter_lines)
    requester_html = "".join(f"<li>{line}</li>" for line in requester_lines)
    parameters_html = "".join(f"<li>{line}</li>" for line in parameter_lines)

    text_body = (
        "Your UnLimbited assistive device files are ready.\n\n"
        f"Download ZIP: {download_url}\n"
        f"This link is valid for {JOB_RETENTION_HOURS // 24} days.\n"
        f"Archive name: {archive_name}\n"
        f"Donate / say thank you: {DONATION_URL}\n\n"
        "Request details:\n"
        f"{requester_text}\n\n"
        "Generation parameters:\n"
        f"{parameters_text}\n"
    )
    html_body = (
        "<p>Your UnLimbited assistive device files are ready.</p>"
        f"<p><a href=\"{download_url}\">Download the ZIP archive</a></p>"
        f"<p>This link is valid for <strong>{JOB_RETENTION_HOURS // 24} days</strong>.</p>"
        f"<p>Archive name: <strong>{archive_name}</strong></p>"
        "<p><strong>Request details</strong></p>"
        f"<ul>{requester_html}</ul>"
        "<p><strong>Generation parameters</strong></p>"
        f"<ul>{parameters_html}</ul>"
        f"<p><a href=\"{DONATION_URL}\">Say thank you and donate today</a></p>"
    )
    send_email(recipient, "Your Team UnLimbited STL files are ready", text_body, html_body)


def send_internal_generation_report(job: Dict[str, Any]) -> None:
    recipient = ARMINATOR_REPORT_EMAIL_TO
    if not recipient:
        return

    requester = dict(job.get("requester") or {})
    parameters = dict(job.get("parameters") or {})
    arm_version = str(job.get("arm_version") or "").lower()
    started_at = job.get("started_at")
    finished_at = job.get("finished_at")
    duration_seconds = ""
    if started_at and finished_at:
        duration_seconds = f"{max(0, round(float(finished_at) - float(started_at), 2))}"

    report_payload = {
        "report_version": 1,
        "report_type": "generation_completed",
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "arm_version": arm_version,
        "generated_at_utc": format_utc_timestamp(time.time()),
        "started_at_utc": format_utc_timestamp(started_at),
        "finished_at_utc": format_utc_timestamp(finished_at),
        "duration_seconds": duration_seconds,
        "download_name": job.get("download_name") or "",
        "selected_parts": list(job.get("selected_parts") or []),
        "requester": {
            "name": requester.get("name") or "",
            "country": requester.get("country") or "",
            "purpose": requester.get("purpose") or "",
            "recipient_name": requester.get("recipient_name") or "",
            "recipient_sex": requester.get("recipient_sex") or "",
            "recipient_age": requester.get("recipient_age") or "",
            "summary": requester.get("summary") or "",
        },
        "parameters": {
            field["name"]: parameters.get(field["name"])
            for field in parameter_fields_for_job(job)
            if field["name"] in parameters
        },
    }

    summary_lines = [
        "ARMINATOR_REPORT_VERSION: 1",
        "REPORT_TYPE: generation_completed",
        f"JOB_ID: {report_payload['job_id'] or ''}",
        f"STATUS: {report_payload['status'] or ''}",
        f"ARM_VERSION: {report_payload['arm_version'] or ''}",
        f"GENERATED_AT_UTC: {report_payload['generated_at_utc']}",
        f"STARTED_AT_UTC: {report_payload['started_at_utc']}",
        f"FINISHED_AT_UTC: {report_payload['finished_at_utc']}",
        f"DURATION_SECONDS: {report_payload['duration_seconds']}",
        f"DOWNLOAD_NAME: {report_payload['download_name']}",
        "",
        f"REQUESTER_NAME: {report_payload['requester']['name']}",
        f"COUNTRY: {report_payload['requester']['country']}",
        f"PURPOSE: {report_payload['requester']['purpose']}",
        f"RECIPIENT_NAME: {report_payload['requester']['recipient_name']}",
        f"RECIPIENT_SEX: {report_payload['requester']['recipient_sex']}",
        f"RECIPIENT_AGE: {report_payload['requester']['recipient_age']}",
        f"SUMMARY: {report_payload['requester']['summary']}",
        "",
    ]
    for field in parameter_fields_for_job(job):
        if field["name"] in report_payload["parameters"]:
            summary_lines.append(f"PARAM_{field['name'].upper()}: {report_payload['parameters'][field['name']]}")
    summary_lines.extend(
        [
            "",
            "BEGIN_ARMINATOR_JSON",
            json.dumps(report_payload, indent=2, sort_keys=True),
            "END_ARMINATOR_JSON",
        ]
    )
    text_body = "\n".join(summary_lines)
    html_body = (
        "<p>Structured generation report attached inline for later processing.</p>"
        "<pre>"
        + text_body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        + "</pre>"
    )
    send_email(recipient, "ARM GENERATION", text_body, html_body)


def scrub_job_personal_data(job_id: str) -> None:
    set_job_fields(
        job_id,
        {
            "requester": {},
            "verified_email": None,
            "notify_completed": False,
        },
    )


def clear_session_draft(client_id: str) -> None:
    if not client_id:
        return
    record = get_record(session_key(client_id))
    if not record:
        return
    set_job_fields(session_key(client_id), {"draft": None})


def end_session(client_id: str, headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, Any]]:
    if client_id:
        try:
            _ddb_table.delete_item(Key={"job_id": session_key(client_id)})
        except ClientError as exc:
            return 500, {"error": f"Could not end the current session: {exc}"}

    payload = {
        "verified": False,
        "email": None,
        "notify_completed": True,
        "draft": None,
        "viewer_country_code": infer_viewer_country_code(headers),
        "verification_pending": False,
        "message": "Session ended. Verify by magic link again to continue.",
    }
    return 200, payload


def update_session_draft(client_id: str, draft: Optional[Dict[str, Any]], headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, Any]]:
    if client_id:
        record = get_record(session_key(client_id))
        if record:
            set_job_fields(session_key(client_id), {"draft": draft if isinstance(draft, dict) else None})

    payload = get_session_payload(client_id, headers=headers)
    payload["message"] = "Draft updated."
    return 200, payload


def try_acquire_dispatch_lock(holder: str) -> bool:
    now = int(time.time())
    expires_at = now + DISPATCH_LOCK_TTL_SECONDS
    try:
        _ddb_table.put_item(
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


def release_dispatch_lock(holder: str) -> None:
    try:
        _ddb_table.delete_item(
            Key={"job_id": DISPATCH_LOCK_JOB_ID},
            ConditionExpression="holder = :holder",
            ExpressionAttributeValues={":holder": holder},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def run_renderer_task(job_id: str) -> str:
    require_render_configuration()
    print(f"Launching renderer task for job {job_id}")
    response = _ecs_client.run_task(
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
    print(f"Renderer task launched for job {job_id}: {tasks[0]['taskArn']}")
    return tasks[0]["taskArn"]


def dispatch_once() -> None:
    require_render_configuration()
    lock_holder = uuid.uuid4().hex
    if not try_acquire_dispatch_lock(lock_holder):
        return

    try:
        now = time.time()
        starting_jobs = scan_all(Attr("status").eq("starting"))
        for job in starting_jobs:
            if job.get("task_arn"):
                continue
            updated_at = float(job.get("updated_at", 0) or 0)
            if now - updated_at < STARTING_RECOVERY_SECONDS:
                continue
            set_job_fields(
                job["job_id"],
                {
                    "status": "queued",
                    "current_step": "queued",
                    "status_line": "Recovered a stalled worker start. Returning the job to the queue.",
                },
            )

        active_jobs = scan_all(Attr("status").is_in(["starting", "running"]))
        if active_jobs:
            return

        queued_jobs = sort_jobs(scan_all(Attr("status").eq("queued")))
        for job in queued_jobs:
            print(f"Dispatching queued job {job['job_id']}")
            try:
                set_job_fields(
                    job["job_id"],
                    {
                        "status": "starting",
                        "message": "Starting generation worker",
                        "current_step": "starting",
                        "status_line": "Requesting a generation worker task from ECS.",
                    },
                    condition_expression=Attr("status").eq("queued"),
                )
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    continue
                raise

            try:
                task_arn = run_renderer_task(job["job_id"])
                set_job_fields(
                    job["job_id"],
                    {
                        "task_arn": task_arn,
                        "status_line": "Generation worker launched. Waiting for it to start.",
                    },
                )
            except Exception as exc:
                print(f"Failed to launch renderer for job {job['job_id']}: {exc}")
                set_job_fields(
                    job["job_id"],
                    {
                        "status": "failed",
                        "progress": 100,
                        "message": "Failed to launch generation worker",
                        "error": str(exc),
                        "current_step": "failed",
                        "status_line": "ECS could not start the renderer task.",
                        "finished_at": time.time(),
                    },
                )
            return
    finally:
        release_dispatch_lock(lock_holder)


def create_job(payload: Dict[str, Any], client_id: str, api_prefix: str = "/api") -> Tuple[int, Dict[str, Any]]:
    arm_version, errors = validate_arm_version(payload)
    parameters: Dict[str, Any] = {}
    selected_parts: List[str] = []
    if arm_version:
        parameters, parameter_errors = validate_parameters(payload, arm_version)
        selected_parts = resolve_selected_parts(payload.get("parts"), arm_version)
        errors.extend(parameter_errors)
    requester, requester_errors = validate_requester_details(payload)
    errors.extend(requester_errors)

    if errors:
        return 400, {"errors": errors}

    session = get_verified_session(client_id)
    if not session:
        return 403, {"error": "Verify your email before generating files."}

    assert arm_version is not None
    request_hash = build_request_hash(arm_version, parameters, selected_parts)
    existing_job = pick_active_by_hash(request_hash)
    if existing_job:
        existing_payload = job_to_payload(existing_job, cached=False, api_prefix=api_prefix)
        existing_payload["reused_existing_job"] = True
        return 202, existing_payload

    active_job = pick_active_for_client(client_id)
    if active_job:
        active_payload = job_to_payload(active_job, api_prefix=api_prefix)
        active_payload["active_job_exists"] = True
        active_payload["error"] = "A generation job is already active for this browser."
        return 409, active_payload

    active_email_job = pick_active_for_email(str(session.get("email") or ""))
    if active_email_job:
        active_payload = job_to_payload(active_email_job, api_prefix=api_prefix)
        active_payload["active_job_exists"] = True
        active_payload["error"] = "A generation job is already active for this verified email."
        return 409, active_payload

    queued_job_count = len(scan_all(Attr("status").eq("queued")))
    if queued_job_count >= MAX_QUEUE_LENGTH:
        return 429, {"error": QUEUE_FULL_MESSAGE}

    now = time.time()
    job_id = uuid.uuid4().hex
    record = {
        "job_id": job_id,
        "request_hash": request_hash,
        "created_at": now,
        "updated_at": now,
        "client_id": client_id,
        "arm_version": arm_version,
        "status": "queued",
        "progress": 0,
        "message": "Queued",
        "selected_parts": selected_parts,
        "output_files": [],
        "parameters": parameters,
        "requester": requester,
        "verified_email": session.get("email"),
        "notify_completed": bool(session.get("notify_completed", True)),
        "current_part": None,
        "current_part_index": 0,
        "total_parts": len(selected_parts),
        "completed_parts": 0,
        "current_step": "queued",
        "status_line": "Waiting in the generation queue.",
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
    put_job_record(record)
    clear_session_draft(client_id)
    dispatch_once()
    refreshed = get_job_record(job_id) or record
    return 202, job_to_payload(refreshed, api_prefix=api_prefix)


def get_job_payload(job_id: str, api_prefix: str = "/api") -> Tuple[int, Dict[str, Any]]:
    try:
        dispatch_once()
    except Exception as exc:
        print(f"dispatch_once failed for {job_id}: {exc}")
    job = get_job_record(job_id)
    if not job:
        return 404, {"error": "Job not found."}
    return 200, job_to_payload(job, api_prefix=api_prefix)


def cancel_job(job_id: str, client_id: str, api_prefix: str = "/api") -> Tuple[int, Dict[str, Any]]:
    job = get_job_record(job_id)
    if not job:
        return 404, {"error": "Job not found."}
    if job.get("client_id") != client_id:
        return 403, {"error": "This job belongs to a different browser session."}
    if job.get("status") in TERMINAL_STATUSES:
        return 200, job_to_payload(job, api_prefix=api_prefix)

    if job.get("status") == "queued":
        updated = set_job_fields(
            job_id,
            {
                "status": "canceled",
                "progress": 100,
                "message": "Queued generation canceled.",
                "current_step": "canceled",
                "status_line": "Queued generation canceled.",
                "finished_at": time.time(),
                "cancel_requested": True,
            },
        )
        dispatch_once()
        return 200, job_to_payload(updated, api_prefix=api_prefix)

    updated = set_job_fields(
        job_id,
        {
            "cancel_requested": True,
            "status_line": "Cancel requested. Waiting for the generation worker to stop.",
        },
    )
    task_arn = job.get("task_arn")
    if task_arn:
        try:
            _ecs_client.stop_task(cluster=ARMINATOR_ECS_CLUSTER_ARN, task=task_arn, reason="Canceled by user")
        except Exception:
            pass
    return 202, job_to_payload(updated, api_prefix=api_prefix)


def generate_download_url(job_id: str, expires_in: int = 3600) -> Optional[str]:
    job = get_job_record(job_id)
    if not job or job.get("status") != "completed" or not job.get("archive_key"):
        return None
    return _s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": ARMINATOR_ARTIFACTS_BUCKET, "Key": job["archive_key"]},
        ExpiresIn=expires_in,
    )
