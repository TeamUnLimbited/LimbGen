# Arm Version Selector Rollback Plan

This document captures the pre-rollout snapshot and the exact rollback path before introducing the `V2` / `V3` arm-version selector and version-specific SCAD handling.

## Snapshot

- Snapshot captured at: `2026-03-21T09:38:55Z`
- Snapshot branch: `codex/arm-version-selector`
- Snapshot tag: `snapshot/pre-arm-version-selector-20260321-093855`
- Pre-feature production hostname: `https://limbgen.teamunlimbited.org`
- Pre-feature renderer image:
  - `236209347845.dkr.ecr.eu-west-2.amazonaws.com/arminator-renderer:20260320-2259-renderer-trixie`
- Pre-feature deployment version:
  - `20260320-2259-renderer-trixie`

This baseline includes the widened measurements layout already deployed to production before the arm-version-selector rollout starts.

## Scope Of The Rollout

The arm-version-selector rollout will change:

- static frontend files in [`site/`](/Users/droo/arminator/site)
- Lambda API code in [`lambda_api.py`](/Users/droo/arminator/lambda_api.py) and [`arminator_aws_backend.py`](/Users/droo/arminator/arminator_aws_backend.py)
- shared rendering helpers in [`arminator_common.py`](/Users/droo/arminator/arminator_common.py)
- renderer job logic in [`renderer_job.py`](/Users/droo/arminator/renderer_job.py)
- renderer image contents because the ECS image copies the full repo, including SCAD sources

## Fast Rollback Criteria

Roll back immediately if any of the following happen after deployment:

- the Measurements form fails to load
- `V2` or `V3` selection does not reveal the correct measurement fields
- valid jobs are rejected by the API
- jobs queue but renderer tasks fail to start
- renderer tasks start but OpenSCAD fails for either version
- archive naming or output generation breaks for existing `V3` requests

## Rollback Procedure

### 1. Restore repo state to the baseline snapshot

Use the snapshot tag recorded above:

```bash
git checkout snapshot/pre-arm-version-selector-20260321-093855 -- \
  site/styles.css \
  site/app.js \
  arminator_common.py \
  arminator_aws_backend.py \
  lambda_api.py \
  renderer_job.py \
  infra/aws/terraform.tfvars \
  Dockerfile.renderer-trixie
```

If the rollout added new files, delete only the new rollout-specific files rather than resetting the whole tree.

### 2. Restore the previous renderer image and deployment version

Edit [`infra/aws/terraform.tfvars`](/Users/droo/arminator/infra/aws/terraform.tfvars) back to:

```hcl
renderer_image     = "236209347845.dkr.ecr.eu-west-2.amazonaws.com/arminator-renderer:20260320-2259-renderer-trixie"
deployment_version = "20260320-2259-renderer-trixie"
```

### 3. Redeploy Lambda and ECS task definition

```bash
/opt/homebrew/bin/terraform -chdir=infra/aws apply \
  -target=aws_ecs_task_definition.renderer \
  -target=aws_lambda_function.api \
  -auto-approve
```

### 4. Redeploy static site assets

```bash
/opt/homebrew/bin/terraform -chdir=infra/aws apply \
  -target=aws_s3_object.site_files \
  -auto-approve
```

### 5. Invalidate CloudFront

```bash
/usr/local/bin/aws cloudfront create-invalidation \
  --distribution-id E10FKGA9LCY1CH \
  --paths '/index.html' '/app.js' '/styles.css'
```

### 6. Verify rollback

After rollback:

1. Load the public site and confirm the pre-selector Measurements UI is back.
2. Submit one `V3` generation request.
3. Confirm Lambda accepts the request and the renderer task starts.
4. Confirm the ZIP download completes successfully.

## Notes

- Do not use `git reset --hard` for rollback in this repo.
- Prefer targeted Terraform applies so rollback affects only the API, renderer task definition, and static site assets.
- If only the frontend is broken and backend rendering remains healthy, steps 4 and 5 are sufficient.
