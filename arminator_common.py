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
JOBS_DIR = Path(os.environ.get("JOBS_DIR", BASE_DIR / "instance" / "jobs"))
JOB_RETENTION_HOURS = max(1, int(os.environ.get("JOB_RETENTION_HOURS", str(7 * 24))))
VERIFICATION_TOKEN_TTL_SECONDS = max(300, int(os.environ.get("VERIFICATION_TOKEN_TTL_SECONDS", "900")))
VERIFIED_SESSION_TTL_SECONDS = max(3600, int(os.environ.get("VERIFIED_SESSION_TTL_SECONDS", str(7 * 24 * 3600))))
VALID_PURPOSES = {"recipient", "project", "other"}
VALID_RECIPIENT_SEXES = {"Male", "Female"}
ARM_VERSION_OPTIONS = [
    {"value": "v2", "label": "Version 2"},
    {"value": "v3", "label": "Version 3 Beta"},
    {"value": "phoenix", "label": "UnLimbited Phoenix"},
]
DEFAULT_ARM_VERSION = "v3"
RENDER_STEP_TEMPLATES = {
    "v2": [
        {"part_label": "Pins", "status_part": "Pins"},
        {"part_label": "Cuff & Elbow Jig", "status_part": "Cuff Jig"},
        {"part_label": "Cuff", "status_part": "Cuff"},
        {"part_label": "Forearm", "status_part": "Forearm"},
        {"part_label": "WristJig & Palm Grip", "status_part": "Hand"},
        {"part_label": "Palm", "status_part": "Hand"},
        {"part_label": "Fingers", "status_part": "Hand"},
        {"part_label": "Phalanx", "status_part": "Hand"},
    ],
    "v3": [
        {"part_label": "Pins", "status_part": "Pins"},
        {"part_label": "Cuff Jig", "status_part": "Cuff Jig"},
        {"part_label": "Cuff", "status_part": "Cuff"},
        {"part_label": "Forearm", "status_part": "Forearm"},
        {"part_label": "Hand", "status_part": "Hand"},
    ],
    "phoenix": [
        {"part_label": "Pins", "status_part": "Pins"},
        {"part_label": "Tension Pins", "status_part": "Pins"},
        {"part_label": "Jig", "status_part": "Cuff Jig"},
        {"part_label": "Gauntlet", "status_part": "Cuff"},
        {"part_label": "Tension Box", "status_part": "Forearm"},
        {"part_label": "Palm", "status_part": "Hand"},
        {"part_label": "Fingers", "status_part": "Hand"},
        {"part_label": "Phalanx", "status_part": "Hand"},
    ],
}

ASSIGNMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.+?);\s*(?://\s*\[(?P<constraints>.*?)\])?\s*$"
)
SECTION_RE = re.compile(r"/\*\s*\[(?P<section>.+?)\]\s*\*/")
LABEL_OVERRIDES = {
    "BicepCircum": "Bicep Circumference",
    "ForearmLen": "Forarm Length",
    "HandLen": "Hand Length",
    "HandPerc": "Hand Scale (%)",
    "LeftRight": "Left or Right",
    "PinHoleDia": "Pin Hole Diameter",
}
FIELD_OVERRIDES_BY_VERSION = {
    "v2": {
        "LeftRight": {"section": "Arm Selection"},
        "HandLen": {"section": "Hand Measurements (mm)"},
        "ForearmLen": {"section": "Arm Measurements (mm)"},
        "BicepCircum": {"section": "Arm Measurements (mm)"},
        "PinHoleDia": {"section": "Other Parameters"},
        "CenterSlots": {"section": "Other Parameters"},
    },
    "v3": {
        "LeftRight": {"section": "Arm Selection"},
    },
    "phoenix": {
        "LeftRight": {"section": "Hand Selection"},
        "HandPerc": {"section": "Hand Measurements (%)"},
    },
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
    arm_version: Optional[str] = None
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


@dataclass(frozen=True)
class ArmVersionSpec:
    key: str
    label: str
    scad_file: Path
    part_parameter_name: str
    public_fields: List[Dict[str, Any]]
    part_options: List[Dict[str, Any]]


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


def coerce_value(raw_value: str, value_type: str) -> Any:
    value = raw_value.strip()
    if value_type == "string":
        return value
    if value_type == "float":
        return float(value)
    return int(value)


def parse_option_definition(raw_option: str, value_type: str) -> Dict[str, Any]:
    option = raw_option.strip()
    if ":" in option:
        raw_value, raw_label = option.split(":", 1)
        return {
            "value": coerce_value(raw_value, value_type),
            "label": raw_label.strip(),
        }
    return {
        "value": coerce_value(option, value_type),
        "label": option,
    }


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

    options = [parse_option_definition(option, value_type) for option in constraints.split(",") if option.strip()]
    return {"options": options}


def humanize(name: str) -> str:
    if name in LABEL_OVERRIDES:
        return LABEL_OVERRIDES[name]
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name.replace("_", " ")).split()
    return " ".join(words)


def parse_public_parameters(scad_path: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    fields: List[Dict[str, Any]] = []
    part_options: List[Dict[str, Any]] = []
    part_parameter_name = "Part"
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

        if name.lower() == "part":
            part_options = constraints.get("options", [])
            part_parameter_name = name
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

    return fields, part_options, part_parameter_name


def apply_field_overrides(arm_version: str, fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    overrides = FIELD_OVERRIDES_BY_VERSION.get(arm_version, {})
    customized_fields: List[Dict[str, Any]] = []
    for field in fields:
        customized = dict(field)
        customized.update(overrides.get(field["name"], {}))
        customized_fields.append(customized)
    return customized_fields


def build_arm_version_specs() -> Dict[str, ArmVersionSpec]:
    spec_paths = {
        "v2": BASE_DIR / "UnLimbited_Arm_V2.2.scad",
        "v3": BASE_DIR / "correctv3" / "UnLimbited Arm V3.00.scad",
        "phoenix": BASE_DIR / "UnLimbitedPhoenix.scad",
    }
    specs: Dict[str, ArmVersionSpec] = {}
    for option in ARM_VERSION_OPTIONS:
        fields, part_options, part_parameter_name = parse_public_parameters(spec_paths[option["value"]])
        fields = apply_field_overrides(option["value"], fields)
        specs[option["value"]] = ArmVersionSpec(
            key=option["value"],
            label=option["label"],
            scad_file=spec_paths[option["value"]],
            part_parameter_name=part_parameter_name,
            public_fields=fields,
            part_options=part_options,
        )
    return specs


ARM_VERSION_SPECS = build_arm_version_specs()
SCAD_FILE = ARM_VERSION_SPECS[DEFAULT_ARM_VERSION].scad_file
PUBLIC_FIELDS = ARM_VERSION_SPECS[DEFAULT_ARM_VERSION].public_fields
PART_OPTIONS = [option["label"] for option in ARM_VERSION_SPECS[DEFAULT_ARM_VERSION].part_options]


def resolve_arm_version(raw_value: Any) -> Optional[str]:
    value = str(raw_value or "").strip().lower()
    return value if value in ARM_VERSION_SPECS else None


def require_arm_version(arm_version: Optional[str]) -> str:
    resolved = resolve_arm_version(arm_version)
    if not resolved:
        raise ValueError("Unknown arm version.")
    return resolved


def get_arm_version_spec(arm_version: str) -> ArmVersionSpec:
    return ARM_VERSION_SPECS[require_arm_version(arm_version)]


def get_public_fields(arm_version: str) -> List[Dict[str, Any]]:
    return get_arm_version_spec(arm_version).public_fields


def get_part_options(arm_version: str) -> List[Dict[str, Any]]:
    return get_arm_version_spec(arm_version).part_options


def get_part_value(arm_version: str, part_label: str) -> Any:
    for option in get_part_options(arm_version):
        if option["label"] == part_label:
            return option["value"]
    raise ValueError(f"Unknown part for {arm_version}: {part_label}")


def get_render_steps(arm_version: str, selected_parts: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    spec = get_arm_version_spec(arm_version)
    allowed_status_parts = set(selected_parts or [])
    templates = RENDER_STEP_TEMPLATES[arm_version]

    steps: List[Dict[str, Any]] = []
    for template in templates:
        if allowed_status_parts and template["status_part"] not in allowed_status_parts:
            continue
        steps.append(
            {
                "part_label": template["part_label"],
                "part_value": get_part_value(arm_version, template["part_label"]),
                "status_part": template["status_part"],
                "part_parameter_name": spec.part_parameter_name,
            }
        )

    status_parts: List[str] = []
    for step in steps:
        if step["status_part"] not in status_parts:
            status_parts.append(step["status_part"])

    phase_totals = {
        status_part: sum(1 for step in steps if step["status_part"] == status_part)
        for status_part in status_parts
    }
    phase_indices = {status_part: index + 1 for index, status_part in enumerate(status_parts)}
    phase_counts: Dict[str, int] = {}
    for step in steps:
        phase_counts[step["status_part"]] = phase_counts.get(step["status_part"], 0) + 1
        step["phase_index"] = phase_indices[step["status_part"]]
        step["phase_total"] = len(status_parts)
        step["phase_complete"] = phase_counts[step["status_part"]] == phase_totals[step["status_part"]]

    return steps


def get_part_labels(arm_version: str) -> List[str]:
    labels: List[str] = []
    for step in get_render_steps(arm_version):
        if step["status_part"] not in labels:
            labels.append(step["status_part"])
    return labels


def validate_arm_version(payload: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
    arm_version = resolve_arm_version(payload.get("arm_version"))
    if arm_version:
        return arm_version, []
    return None, ["Arm Version is required."]


def format_scad_definition(name: str, value: Any) -> str:
    if isinstance(value, str):
        return f"{name}={json.dumps(value)}"
    if isinstance(value, bool):
        return f"{name}={'true' if value else 'false'}"
    return f"{name}={value}"


def build_render_command(output_path: Path, parameters: Dict[str, Any], arm_version: str) -> List[str]:
    spec = get_arm_version_spec(arm_version)
    command: List[str] = []
    if OPENSCAD_USE_XVFB:
        command.extend(["xvfb-run", "-a"])

    command.extend([OPENSCAD_BIN, "-o", str(output_path)])
    for name, value in parameters.items():
        command.extend(["-D", format_scad_definition(name, value)])
    command.append(str(spec.scad_file))
    return command


def get_job_directory(job_id: str) -> Path:
    return JOBS_DIR / job_id


def build_request_hash(arm_version: str, parameters: Dict[str, Any], selected_parts: List[str]) -> str:
    spec = get_arm_version_spec(arm_version)
    payload = {
        "arm_version": arm_version,
        "parameters": parameters,
        "parts": sorted(selected_parts),
        # Include the relative SCAD path so geometry-source swaps invalidate cached jobs.
        "source_file": str(spec.scad_file.relative_to(BASE_DIR)),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_handedness(handedness: Any) -> str:
    value = str(handedness or "").strip().lower()
    if value in {"0", "left", "l"}:
        return "left"
    return "right"


def make_output_filename(index: int, part: str, handedness: str) -> str:
    slug = part.lower().replace(" ", "-")
    if part in {"Cuff Jig", "Pins"}:
        return f"{index:02d}-{slug}.stl"
    return f"{index:02d}-{slug}-{normalize_handedness(handedness)}.stl"


def order_selected_parts(parts: List[str], arm_version: str) -> List[str]:
    order = {
        option["label"]: index
        for index, option in enumerate(get_part_options(arm_version))
    }
    return sorted(parts, key=lambda part: (order.get(part, 999), part))


def resolve_selected_parts(parts: Any, arm_version: str) -> List[str]:
    if not isinstance(parts, list) or not parts:
        return order_selected_parts(get_part_labels(arm_version), arm_version)
    return order_selected_parts([str(part) for part in parts], arm_version)


def build_archive_name(parameters: Dict[str, Any], arm_version: str) -> str:
    side = "L" if normalize_handedness(parameters.get("LeftRight")) == "left" else "R"
    if arm_version == "v2":
        return (
            f"V2-{side}"
            f"HL{int(parameters['HandLen'])}"
            f"FL{int(parameters['ForearmLen'])}"
            f"BC{int(parameters['BicepCircum'])}"
            f"PH{int(parameters['PinHoleDia'])}.zip"
        )
    if arm_version == "phoenix":
        return f"Phoenix-{side}-HP{int(parameters['HandPerc'])}.zip"
    return (
        f"{side}"
        f"K{int(parameters['Knuckle_Width'])}"
        f"HL{int(parameters['Hand_Length'])}"
        f"WW{int(parameters['Wrist_Width'])}"
        f"WH{int(parameters['Wrist_Height'])}"
        f"FL{int(parameters['ForearmLen'])}"
        f"BC{int(parameters['BicepCircum'])}.zip"
    )


def build_render_parameters(arm_version: str, parameters: Dict[str, Any], part_label: str) -> Dict[str, Any]:
    spec = get_arm_version_spec(arm_version)
    render_parameters = dict(parameters)
    render_parameters[spec.part_parameter_name] = get_part_value(arm_version, part_label)
    return render_parameters


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


def validate_parameters(payload: Dict[str, Any], arm_version: str) -> Tuple[Dict[str, Any], List[str]]:
    fields = get_public_fields(arm_version)
    submitted_parameters = payload.get("parameters", {})
    selected_parts = resolve_selected_parts(payload.get("parts"), arm_version)
    errors: List[str] = []
    validated: Dict[str, Any] = {}

    for field in fields:
        raw_value = submitted_parameters.get(field["name"])
        if raw_value in (None, ""):
            errors.append(f"{field['label']} is required.")
            continue

        if field["kind"] == "select":
            option_values = {str(option["value"]): option for option in field["options"]}
            value = str(raw_value).strip()
            if value not in option_values:
                labels = ", ".join(option["label"] for option in field["options"])
                errors.append(f"{field['label']} must be one of: {labels}.")
                continue
            validated[field["name"]] = option_values[value]["value"]
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

    valid_parts = set(get_part_labels(arm_version))
    invalid_parts = [part for part in selected_parts if part not in valid_parts]
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
