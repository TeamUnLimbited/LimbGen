import json
import uuid
from typing import Any, Dict, Optional, Tuple

from arminator_aws_backend import (
    cancel_job,
    confirm_verification_token,
    create_job,
    end_session,
    frontend_config,
    generate_download_url,
    get_session_payload,
    get_job_payload,
    request_verification_link,
)

COOKIE_NAME = "arminator_client_id"
COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365


def build_client_cookie(client_id: str) -> str:
    return (
        f"{COOKIE_NAME}={client_id}; Path=/; Max-Age={COOKIE_MAX_AGE_SECONDS}; "
        "Secure; HttpOnly; SameSite=Lax"
    )


def clear_client_cookie() -> str:
    return f"{COOKIE_NAME}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax"


def response_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {
        "Cache-Control": "no-store",
    }
    if extra:
        headers.update(extra)
    return headers


def json_response(status_code: int, payload: Dict[str, Any], set_cookie: str = "") -> Dict[str, Any]:
    response = {
        "statusCode": status_code,
        "headers": response_headers({"Content-Type": "application/json"}),
        "body": json.dumps(payload),
    }
    if set_cookie:
        response["cookies"] = [set_cookie]
    return response


def redirect_response(location: str, set_cookie: str = "") -> Dict[str, Any]:
    response = {
        "statusCode": 302,
        "headers": response_headers({"Location": location}),
        "body": "",
    }
    if set_cookie:
        response["cookies"] = [set_cookie]
    return response


def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body")
    if not body:
        return {}
    if event.get("isBase64Encoded"):
        raise ValueError("Base64-encoded request bodies are not supported.")
    return json.loads(body)


def request_cookie_map(event: Dict[str, Any]) -> Dict[str, str]:
    cookie_map: Dict[str, str] = {}

    for item in event.get("cookies") or []:
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        cookie_map[name.strip()] = value.strip()

    raw_cookie = str((event.get("headers") or {}).get("cookie") or "")
    for chunk in raw_cookie.split(";"):
        if "=" not in chunk:
            continue
        name, value = chunk.split("=", 1)
        cookie_map[name.strip()] = value.strip()

    return cookie_map


def resolve_client_id(event: Dict[str, Any], payload: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    cookies = request_cookie_map(event)
    cookie_client_id = str(cookies.get(COOKIE_NAME) or "").strip()
    if cookie_client_id:
        return cookie_client_id, ""

    payload_client_id = str((payload or {}).get("client_id") or "").strip()
    if payload_client_id:
        return payload_client_id, build_client_cookie(payload_client_id)

    generated = uuid.uuid4().hex
    return generated, build_client_cookie(generated)


def normalize_path(event: Dict[str, Any]) -> str:
    path = event.get("rawPath") or event.get("requestContext", {}).get("http", {}).get("path") or "/"
    return path.rstrip("/") or "/"


def query_param(event: Dict[str, Any], key: str) -> str:
    params = event.get("queryStringParameters") or {}
    return str(params.get(key) or "").strip()


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    method = (event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = normalize_path(event)
    headers = event.get("headers") or {}

    if method == "GET" and path == "/api/healthz":
        return json_response(200, {"status": "ok", "backend": "lambda"})

    if method == "GET" and path == "/api/config":
        arm_version = query_param(event, "arm_version").lower() or None
        viewer_country_code = str((headers.get("cloudfront-viewer-country") or headers.get("CloudFront-Viewer-Country") or "")).strip().upper()
        return json_response(200, frontend_config(arm_version=arm_version, viewer_country_code=viewer_country_code))

    if method == "GET" and path == "/api/session":
        client_id, set_cookie = resolve_client_id(event)
        return json_response(200, get_session_payload(client_id, headers=headers), set_cookie=set_cookie)

    if method == "POST" and path == "/api/session/end":
        client_id = str(request_cookie_map(event).get(COOKIE_NAME) or "").strip()
        status_code, response_payload = end_session(client_id, headers=headers)
        return json_response(status_code, response_payload, set_cookie=clear_client_cookie())

    if method == "POST" and path == "/api/verification-links":
        try:
            payload = parse_body(event)
        except (ValueError, json.JSONDecodeError) as exc:
            return json_response(400, {"error": str(exc)})
        client_id, set_cookie = resolve_client_id(event, payload)
        status_code, response_payload = request_verification_link(payload, client_id, headers=headers)
        return json_response(status_code, response_payload, set_cookie=set_cookie)

    if method == "POST" and path == "/api/verify":
        try:
            payload = parse_body(event)
        except (ValueError, json.JSONDecodeError) as exc:
            return json_response(400, {"error": str(exc)})
        client_id, set_cookie = resolve_client_id(event, payload)
        token = str(payload.get("token") or "").strip()
        if not token:
            return json_response(400, {"error": "Missing verification token."})
        status_code, response_payload = confirm_verification_token(token, client_id, headers=headers)
        return json_response(status_code, response_payload, set_cookie=set_cookie)

    if method == "POST" and path == "/api/jobs":
        try:
            payload = parse_body(event)
        except (ValueError, json.JSONDecodeError) as exc:
            return json_response(400, {"error": str(exc)})
        client_id, set_cookie = resolve_client_id(event, payload)
        status_code, response_payload = create_job(payload, client_id, api_prefix="/api")
        return json_response(status_code, response_payload, set_cookie=set_cookie)

    if method == "GET" and path.startswith("/api/jobs/"):
        suffix = path[len("/api/jobs/") :]
        if suffix.endswith("/download"):
            job_id = suffix[: -len("/download")]
            if not job_id:
                return json_response(404, {"error": "Job not found."})
            presigned = generate_download_url(job_id)
            if not presigned:
                return json_response(404, {"error": "Job not found."})
            return redirect_response(presigned)

        job_id = suffix
        if "/" in job_id or not job_id:
            return json_response(404, {"error": "Job not found."})
        status_code, response_payload = get_job_payload(job_id, api_prefix="/api")
        return json_response(status_code, response_payload)

    if method == "POST" and path.startswith("/api/jobs/") and path.endswith("/cancel"):
        job_id = path[len("/api/jobs/") : -len("/cancel")].rstrip("/")
        if "/" in job_id or not job_id:
            return json_response(404, {"error": "Job not found."})
        try:
            payload = parse_body(event)
        except (ValueError, json.JSONDecodeError) as exc:
            return json_response(400, {"error": str(exc)})
        client_id, set_cookie = resolve_client_id(event, payload)
        status_code, response_payload = cancel_job(job_id, client_id, api_prefix="/api")
        return json_response(status_code, response_payload, set_cookie=set_cookie)

    return json_response(404, {"error": "Not found."})
