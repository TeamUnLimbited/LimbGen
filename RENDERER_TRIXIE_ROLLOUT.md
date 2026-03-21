# Renderer Trixie Rollout

This document describes the live ECS renderer image based on `openscad/openscad:trixie` with Manifold enabled by default, and how to revert quickly if production behavior becomes unacceptable.

## Goal

Replace only the on-demand ECS renderer container with a newer OpenSCAD image.

Keep unchanged:

- Lambda API code path
- Terraform resource topology
- job orchestration
- S3/DynamoDB/ECS task wiring

## Change shape

The new renderer image uses:

- [`Dockerfile.renderer-trixie`](/Users/droo/arminator/Dockerfile.renderer-trixie)
- OpenSCAD base image: `openscad/openscad:trixie`
- `OPENSCAD_USE_XVFB=0`
- `OPENSCAD_BIN=/usr/local/bin/openscad-manifold`

The wrapper script inside the image runs:

```bash
openscad --backend Manifold "$@"
```

That means the application code stays unchanged while the renderer task gets newer OpenSCAD plus explicit Manifold.

## Why this is low-risk

- ECS already takes the renderer image from [`renderer_image`](/Users/droo/arminator/infra/aws/variables.tf#L25).
- The renderer task definition already runs `python renderer_job.py` regardless of image internals.
- A rollback only needs the previous image tag and a task-definition/Lambda refresh.

## Pre-deploy checks

1. Build the image on the remote Docker host `192.168.1.103`.
2. Run a full-kit render locally on that host and confirm the image can generate all five parts.
3. Compare at least the `Hand` and `Forearm` STL outputs against the current production image.
4. Confirm the image has:
   - `python`
   - `openscad`
   - successful `openscad --version`
   - successful `openscad --help` showing `--backend`

## Build and push

Assumptions:

- AWS account: `236209347845`
- region: `eu-west-2`
- ECR repo: `arminator-renderer`
- remote Docker host already has the repo synced

Pick a new rollout version string:

```bash
export RENDERER_VERSION="$(date -u +%Y%m%d-%H%M%S)-renderer-trixie"
export AWS_REGION="eu-west-2"
export AWS_ACCOUNT_ID="236209347845"
export ECR_REPO="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/arminator-renderer"
```

Build:

```bash
docker build -f Dockerfile.renderer-trixie -t "${ECR_REPO}:${RENDERER_VERSION}" .
```

Smoke test the image before push:

```bash
docker run --rm "${ECR_REPO}:${RENDERER_VERSION}" openscad --version
docker run --rm "${ECR_REPO}:${RENDERER_VERSION}" /usr/local/bin/openscad-manifold --help | grep -- --backend
docker run --rm "${ECR_REPO}:${RENDERER_VERSION}" python -c "import boto3; print('python ok')"
```

Push:

```bash
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

docker push "${ECR_REPO}:${RENDERER_VERSION}"
```

## Terraform rollout

Update [`infra/aws/terraform.tfvars`](/Users/droo/arminator/infra/aws/terraform.tfvars):

- set `renderer_image = "${ECR_REPO}:${RENDERER_VERSION}"`
- set `deployment_version = "${RENDERER_VERSION}"`

Apply:

```bash
/opt/homebrew/bin/terraform -chdir=infra/aws apply \
  -target=aws_ecs_task_definition.renderer \
  -target=aws_lambda_function.api \
  -auto-approve
```

Why Lambda is included:

- the API environment carries the renderer task definition ARN
- Terraform will issue a new task definition revision
- the Lambda environment must see that revision

## Production verification

After rollout:

1. Start one real generation job.
2. Confirm the ECS renderer task starts successfully.
3. Confirm CloudWatch logs show all five parts completing.
4. Download the ZIP and open the STL files.
5. Compare generation time against the previous baseline.

Useful checks:

- ECS task exit code is `0`
- job status reaches `completed`
- ZIP is uploaded to the artifacts bucket
- no new OpenSCAD CLI errors appear in logs

## Revert procedure

Revert is image-tag based and should take only a few minutes.

Current pre-change renderer image recorded in this repo:

- `236209347845.dkr.ecr.eu-west-2.amazonaws.com/arminator-renderer:20260320-211316-admin-report`

If the trixie renderer is bad, do this immediately:

1. Edit [`infra/aws/terraform.tfvars`](/Users/droo/arminator/infra/aws/terraform.tfvars).
2. Restore:

```hcl
renderer_image     = "236209347845.dkr.ecr.eu-west-2.amazonaws.com/arminator-renderer:20260320-211316-admin-report"
deployment_version = "20260320-211316-admin-report"
```

3. Re-apply the same two Terraform targets:

```bash
/opt/homebrew/bin/terraform -chdir=infra/aws apply \
  -target=aws_ecs_task_definition.renderer \
  -target=aws_lambda_function.api \
  -auto-approve
```

4. Start a fresh test generation job and confirm the older renderer path is back.

## Revert triggers

Roll back if any of these occur:

- renderer tasks fail to start
- STL generation fails for any part
- output geometry is materially wrong
- production timing regresses unexpectedly
- CloudWatch logs show new fatal OpenSCAD errors

## Notes

- Newer OpenSCAD output is not byte-identical to older output, so a visual and printability check is required before considering the rollout complete.
- This image is intended for the ECS renderer task only. It should not replace the legacy all-in-one local Docker image unless that is a separate decision.
- This rollout is now live. Keep this document because it is also the current rollback runbook.
