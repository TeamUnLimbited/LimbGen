#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "docs" / "architecture" / "diagrams"
KROKI_URL = "https://kroki.io/mermaid/svg"
TARGET_WIDTH = 2200

DIAGRAMS = [
    (
        "01-full-system",
        """flowchart LR
    User["User Browser"] --> CF["CloudFront"]
    CF --> Site["S3 Site Bucket\\nstatic app + progress images"]
    CF --> Api["Lambda API"]

    Api --> DDB["DynamoDB\\njobs + sessions + verification tokens + drafts"]
    Api --> ECS["ECS RunTask\\nFargate renderer"]
    Api --> SES["SES\\nverification + completion + internal report"]
    Api --> Art["S3 Artifacts Bucket\\nZIPs + STLs"]

    ECS --> ECR["ECR\\nrenderer image"]
    ECS --> DDB
    ECS --> Art
    ECS --> SES
    ECS --> LogsR["CloudWatch Logs\\nrenderer"]

    Api --> LogsL["CloudWatch Logs\\nlambda"]
    ACM["ACM cert"] --> CF
""",
    ),
    (
        "02-user-request-flow",
        """sequenceDiagram
    participant U as User
    participant CF as CloudFront
    participant L as Lambda API
    participant D as DynamoDB
    participant S as SES
    participant E as ECS Renderer
    participant A as S3 Artifacts

    U->>CF: Load app
    CF->>U: index.html / app.js / styles.css

    U->>CF: GET /api/config
    CF->>L: Forward request
    L->>U: Version-aware form schema

    U->>CF: Request verification
    CF->>L: POST /api/verify/request
    L->>D: Store token + draft/session
    L->>S: Send magic link
    L->>U: Verification pending

    U->>CF: Submit generation
    CF->>L: POST /api/jobs
    L->>D: Create queued job
    L->>E: RunTask

    loop Poll status
      U->>CF: GET /api/jobs/:id
      CF->>L: Forward request
      L->>D: Read job
      L->>U: Progress payload
    end

    E->>A: Upload ZIP
    E->>D: Mark completed
    E->>S: Send completion/internal emails

    U->>CF: GET /api/jobs/:id/download
    CF->>L: Forward request
    L->>A: Create presigned URL
    L->>U: Redirect to ZIP
""",
    ),
    (
        "03-generation-worker-flow",
        """sequenceDiagram
    participant F as Fargate Task
    participant D as DynamoDB
    participant A as S3 Artifacts
    participant S as SES

    F->>D: Load job and parameters
    F->>F: Build render params from arm version
    F->>F: Generate Pins
    F->>F: Generate Cuff Jig
    F->>F: Generate Cuff
    F->>F: Generate Forearm
    F->>F: Generate Hand
    F->>F: Build ZIP archive
    F->>A: Upload STL and ZIP artifacts
    F->>D: Update terminal job state
    F->>S: Send completion and ARM GENERATION emails
    F->>D: Scrub requester personal data
""",
    ),
    (
        "04-aws-infrastructure-map",
        """flowchart TB
    subgraph Edge["Edge / Public"]
        CF["CloudFront"]
        ACM["ACM certificate"]
    end

    subgraph Frontend["Static frontend"]
        SiteBucket["S3 site bucket"]
    end

    subgraph ApiLayer["API"]
        Lambda["Lambda function"]
        LambdaURL["Lambda Function URL"]
        LambdaLogs["CloudWatch log group\\nlambda"]
    end

    subgraph Data["State + artifacts"]
        DDB["DynamoDB jobs table\\nTTL on expires_at"]
        ArtBucket["S3 artifacts bucket\\nlifecycle expiration"]
    end

    subgraph Render["Rendering"]
        ECS["ECS cluster"]
        TaskDef["Fargate task definition"]
        ECR["ECR renderer repo"]
        SG["Renderer security group"]
        RenderLogs["CloudWatch log group\\nrenderer"]
    end

    subgraph Mail["Email"]
        SES["SES"]
    end

    ACM --> CF
    CF --> SiteBucket
    CF --> LambdaURL
    LambdaURL --> Lambda
    Lambda --> LambdaLogs
    Lambda --> DDB
    Lambda --> ECS
    Lambda --> SES
    Lambda --> ArtBucket

    ECS --> TaskDef
    TaskDef --> ECR
    TaskDef --> SG
    TaskDef --> RenderLogs
    TaskDef --> DDB
    TaskDef --> ArtBucket
    TaskDef --> SES
""",
    ),
    (
        "05-versioned-form-logic",
        """flowchart LR
    Version["Arm Version selection"] --> V2["V2\\nVersion2 Alfie Edition"]
    Version --> V3["V3\\nVersion 3 BETA"]

    V2 --> V2Schema["Parse UnLimbited_Arm_V2.2.scad"]
    V3 --> V3Schema["Parse UnLimbited Arm V3.00.scad"]

    V2Schema --> Fields["Frontend form sections"]
    V3Schema --> Fields

    Fields --> Validate["Lambda validation"]
    Validate --> Render["Renderer command build"]
    Render --> Progress["Canonical progress names\\nPins, Cuff Jig, Cuff, Forearm, Hand"]
""",
    ),
    (
        "06-deployment-path",
        """flowchart LR
    Code["Repo code"] --> Static["Static files\\nindex.html / app.js / styles.css"]
    Code --> LambdaPkg["Lambda package"]
    Code --> Renderer["Renderer container image"]

    Static --> SiteBucket["S3 site bucket"]
    LambdaPkg --> Lambda["Lambda API"]
    Renderer --> ECR["ECR"]
    ECR --> ECS["ECS task definition"]

    SiteBucket --> CF["CloudFront invalidation"]
    Lambda --> Live["Live system"]
    ECS --> Live
    CF --> Live
""",
    ),
]


def fetch_svg(diagram_text: str) -> bytes:
    result = subprocess.run(
        [
            "/usr/bin/curl",
            "-fsS",
            "-H",
            "Content-Type: text/plain; charset=utf-8",
            "--data-binary",
            "@-",
            KROKI_URL,
        ],
        input=diagram_text,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.encode("utf-8")


def convert_svg_to_jpg(svg_path: Path, jpg_path: Path) -> None:
    subprocess.run(
        [
            "/usr/bin/sips",
            "-s",
            "format",
            "jpeg",
            "--resampleWidth",
            str(TARGET_WIDTH),
            str(svg_path),
            "--out",
            str(jpg_path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        for slug, diagram_text in DIAGRAMS:
            svg_path = temp_dir / f"{slug}.svg"
            jpg_path = OUTPUT_DIR / f"{slug}.jpg"
            svg_path.write_bytes(fetch_svg(diagram_text))
            convert_svg_to_jpg(svg_path, jpg_path)
            print(f"wrote {jpg_path.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
