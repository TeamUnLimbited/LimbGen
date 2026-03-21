import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


BASE_DIR = Path(__file__).resolve().parent
SCAD_FILE = BASE_DIR / "UnLimbited Arm V3.00.scad"
JOBS_DIR = Path(os.environ.get("JOBS_DIR", BASE_DIR / "instance" / "jobs"))
JOB_RETENTION_HOURS = max(1, int(os.environ.get("JOB_RETENTION_HOURS", "24")))
VERIFICATION_TOKEN_TTL_SECONDS = max(300, int(os.environ.get("VERIFICATION_TOKEN_TTL_SECONDS", "900")))
VERIFIED_SESSION_TTL_SECONDS = max(3600, int(os.environ.get("VERIFIED_SESSION_TTL_SECONDS", str(7 * 24 * 3600))))
VALID_PURPOSES = {"recipient", "project", "other"}
VALID_RECIPIENT_SEXES = {"Male", "Female"}

ASSIGNMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.+?);\s*(?://\s*\[(?P<constraints>.*?)\])?\s*$"
)
SECTION_RE = re.compile(r"/\*\s*\[(?P<section>.+?)\]\s*\*/")
LABEL_OVERRIDES = {
    "BicepCircum": "Bicep Circumference",
    "ForearmLen": "Forarm Length",
}


@dataclass
class JobState:
    job_id: str
    request_hash: str
    created_at: float
    client_id: str
    status: str = "queued"
    progress: int = 0
    message: str = "Queued"
    error: Optional[str] = None
    download_name: Optional[str] = None
    selected_parts: List[str] = field(default_factory=list)
    output_files: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    current_part: Optional[str] = None
    current_part_index: int = 0
    total_parts: int = 0
    completed_parts: int = 0
    queue_position: int = 0
    current_step: str = "queued"
    status_line: str = "Waiting for an available generation slot."
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    updated_at: float = field(default_factory=lambda: 0.0)
    cached: bool = False
    duplicate_of: Optional[str] = None
    cancel_requested: bool = False
    archive_key: Optional[str] = None
    task_arn: Optional[str] = None
    expires_at: Optional[int] = None


def detect_openscad_binary() -> str:
    configured = os.environ.get("OPENSCAD_BIN")
    if configured:
        return configured

    discovered = shutil.which("openscad")
    if discovered:
        return discovered

    macos_bundle_binary = Path("/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD")
    if macos_bundle_binary.exists():
        return str(macos_bundle_binary)

    return "openscad"


def should_use_xvfb() -> bool:
    configured = os.environ.get("OPENSCAD_USE_XVFB")
    if configured is not None:
        return configured.lower() not in {"0", "false", "no"}
    return sys.platform.startswith("linux") and shutil.which("xvfb-run") is not None


OPENSCAD_BIN = detect_openscad_binary()
OPENSCAD_USE_XVFB = should_use_xvfb()


def parse_scad_value(raw_value: str) -> Tuple[Any, str]:
    value = raw_value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1], "string"
    if "." in value:
        return float(value), "float"
    return int(value), "int"


def parse_constraints(raw_constraints: Optional[str], value_type: str) -> Dict[str, Any]:
    if not raw_constraints:
        return {}

    constraints = raw_constraints.strip()
    if ":" in constraints and "," not in constraints:
        lower, upper = [part.strip() for part in constraints.split(":", 1)]
        return {
            "min": float(lower) if "." in lower else int(lower),
            "max": float(upper) if "." in upper else int(upper),
        }

    options = [option.strip() for option in constraints.split(",") if option.strip()]
    return {"options": options}


def humanize(name: str) -> str:
    if name in LABEL_OVERRIDES:
        return LABEL_OVERRIDES[name]
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name.replace("_", " ")).split()
    return " ".join(words)


def parse_public_parameters(scad_path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    fields: List[Dict[str, Any]] = []
    part_options: List[str] = []
    current_section = "General"
    pending_note_lines: List[str] = []
    in_hidden_block = False

    for line in scad_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        section_match = SECTION_RE.search(stripped)
        if section_match:
            current_section = section_match.group("section")
            pending_note_lines = []
            if current_section == "Hidden":
                in_hidden_block = True
            continue

        if in_hidden_block:
            continue

        if not stripped:
            pending_note_lines = []
            continue

        if stripped.startswith("//"):
            note = stripped[2:].strip()
            if note and not note.startswith("*") and note not in {"Parameters", "Part Selection"}:
                pending_note_lines.append(note)
            continue

        assignment_match = ASSIGNMENT_RE.match(stripped)
        if not assignment_match:
            pending_note_lines = []
            continue

        name = assignment_match.group("name")
        raw_value = assignment_match.group("value")
        raw_constraints = assignment_match.group("constraints")
        default_value, value_type = parse_scad_value(raw_value)
        constraints = parse_constraints(raw_constraints, value_type)

        if name == "Part":
            part_options = constraints.get("options", [])
            pending_note_lines = []
            continue

        field_definition = {
            "name": name,
            "label": humanize(name),
            "section": current_section,
            "default": default_value,
            "value_type": value_type,
            "note": " ".join(pending_note_lines),
            **constraints,
        }

        if "options" in constraints:
            field_definition["kind"] = "select"
        else:
            field_definition["kind"] = "number"
            field_definition["step"] = "0.1" if value_type == "float" else "1"

        fields.append(field_definition)
        pending_note_lines = []

    return fields, part_options


PUBLIC_FIELDS, PART_OPTIONS = parse_public_parameters(SCAD_FILE)

PART_RENDER_PRIORITY = {
    "Pins": 10,
    "Cuff Jig": 20,
    "Cuff": 30,
    "Forearm": 40,
    "Hand": 50,
}


def format_scad_definition(name: str, value: Any) -> str:
    if isinstance(value, str):
        return f"{name}={json.dumps(value)}"
    if isinstance(value, bool):
        return f"{name}={'true' if value else 'false'}"
    return f"{name}={value}"


def build_render_command(output_path: Path, parameters: Dict[str, Any]) -> List[str]:
    command: List[str] = []
    if OPENSCAD_USE_XVFB:
        command.extend(["xvfb-run", "-a"])

    command.extend([OPENSCAD_BIN, "-o", str(output_path)])
    for name, value in parameters.items():
        command.extend(["-D", format_scad_definition(name, value)])
    command.append(str(SCAD_FILE))
    return command


def get_job_directory(job_id: str) -> Path:
    return JOBS_DIR / job_id


def build_request_hash(parameters: Dict[str, Any], selected_parts: List[str]) -> str:
    payload = {
        "parameters": parameters,
        "parts": sorted(selected_parts),
        "source_file": SCAD_FILE.name,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def make_output_filename(index: int, part: str, handedness: str) -> str:
    slug = part.lower().replace(" ", "-")
    if part in {"Cuff Jig", "Pins"}:
        return f"{index:02d}-{slug}.stl"
    return f"{index:02d}-{slug}-{handedness.lower()}.stl"


def order_selected_parts(parts: List[str]) -> List[str]:
    return sorted(parts, key=lambda part: (PART_RENDER_PRIORITY.get(part, 999), part))


def resolve_selected_parts(parts: Any) -> List[str]:
    if not isinstance(parts, list) or not parts:
        return order_selected_parts(list(PART_OPTIONS))
    return order_selected_parts(list(parts))


def build_archive_name(parameters: Dict[str, Any]) -> str:
    side = "L" if str(parameters.get("LeftRight", "")).lower().startswith("l") else "R"
    return (
        f"{side}"
        f"K{int(parameters['Knuckle_Width'])}"
        f"HL{int(parameters['Hand_Length'])}"
        f"WW{int(parameters['Wrist_Width'])}"
        f"WH{int(parameters['Wrist_Height'])}"
        f"FL{int(parameters['ForearmLen'])}"
        f"BC{int(parameters['BicepCircum'])}.zip"
    )


def validate_requester_details(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    submitted = payload.get("requester", {})
    errors: List[str] = []

    name = str(submitted.get("name") or "").strip()
    country = str(submitted.get("country") or "").strip()
    purpose = str(submitted.get("purpose") or "").strip().lower()
    summary = str(submitted.get("summary") or "").strip()
    recipient_sex = str(submitted.get("recipient_sex") or "").strip()
    recipient_name = str(submitted.get("recipient_name") or "").strip()
    recipient_age_raw = submitted.get("recipient_age")

    if not name:
        errors.append("Your Name is required.")
    elif len(name) > 120:
        errors.append("Your Name must be 120 characters or fewer.")

    if not country:
        errors.append("Country is required.")
    elif len(country) > 120:
        errors.append("Country must be 120 characters or fewer.")

    if purpose not in VALID_PURPOSES:
        errors.append("This Device Is For must be Recipient, Project, or Other.")

    validated: Dict[str, Any] = {
        "name": name,
        "country": country,
        "purpose": purpose,
        "summary": "",
        "recipient_name": None,
        "recipient_sex": None,
        "recipient_age": None,
    }

    if purpose == "recipient":
        if not recipient_name:
            errors.append("Recipient Name is required.")
        elif len(recipient_name) > 120:
            errors.append("Recipient Name must be 120 characters or fewer.")
        else:
            validated["recipient_name"] = recipient_name

        if recipient_sex not in VALID_RECIPIENT_SEXES:
            errors.append("Recipient Sex must be Male or Female.")
        else:
            validated["recipient_sex"] = recipient_sex

        if recipient_age_raw in (None, ""):
            errors.append("Recipient Age is required.")
        else:
            try:
                recipient_age = int(float(recipient_age_raw))
            except (TypeError, ValueError):
                errors.append("Recipient Age must be a whole number.")
            else:
                if recipient_age < 0 or recipient_age > 120:
                    errors.append("Recipient Age must be between 0 and 120.")
                else:
                    validated["recipient_age"] = recipient_age
    else:
        if len(summary) > 280:
            errors.append("Project Or Other Summary must be 280 characters or fewer.")
        validated["summary"] = summary

    return validated, errors


def validate_parameters(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    submitted_parameters = payload.get("parameters", {})
    selected_parts = resolve_selected_parts(payload.get("parts"))
    errors: List[str] = []
    validated: Dict[str, Any] = {}

    for field in PUBLIC_FIELDS:
        raw_value = submitted_parameters.get(field["name"])
        if raw_value in (None, ""):
            errors.append(f"{field['label']} is required.")
            continue

        if field["kind"] == "select":
            value = str(raw_value)
            if value not in field["options"]:
                errors.append(f"{field['label']} must be one of: {', '.join(field['options'])}.")
                continue
            validated[field["name"]] = value
            continue

        try:
            numeric_value = float(raw_value)
        except (TypeError, ValueError):
            errors.append(f"{field['label']} must be a number.")
            continue

        minimum = field.get("min")
        maximum = field.get("max")
        if minimum is not None and numeric_value < minimum:
            errors.append(f"{field['label']} must be at least {minimum}.")
            continue
        if maximum is not None and numeric_value > maximum:
            errors.append(f"{field['label']} must be at most {maximum}.")
            continue

        if field["value_type"] == "int":
            numeric_value = int(round(numeric_value))

        validated[field["name"]] = numeric_value

    invalid_parts = [part for part in selected_parts if part not in PART_OPTIONS]
    if invalid_parts:
        errors.append(f"Unknown parts requested: {', '.join(invalid_parts)}.")

    return validated, errors


def to_dynamodb_value(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: to_dynamodb_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_dynamodb_value(item) for item in value]
    return value


def from_dynamodb_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {key: from_dynamodb_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [from_dynamodb_value(item) for item in value]
    return value
