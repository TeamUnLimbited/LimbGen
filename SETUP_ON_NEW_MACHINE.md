# Setup On New Machine

This repository is intentionally incomplete for rendering by itself.

For the full disaster-recovery and production rebuild procedure, read [`REDEPLOY_FROM_SCRATCH.md`](/Users/droo/arminator/REDEPLOY_FROM_SCRATCH.md).

What is in Git:

- application code
- frontend code
- Terraform infrastructure code
- renderer Dockerfile and build script
- documentation and rollout notes

What is intentionally not in Git:

- any `.scad` source files
- the private OpenSCAD source bundle:
  - `UnLimbited_Arm_V2.2.scad`
  - `UnLimbitedPhoenix.scad`
  - `correctv3/UnLimbited Arm V3.00.scad`
  - `correctv3/Splines.scad`
- local Terraform state and plans
- local `terraform.tfvars`
- local AWS credentials

## Prerequisites

Install locally:

- `git`
- `python3`
- `docker`
- `aws` CLI
- `terraform`

Optional but useful:

- GitHub SSH key access for pushing changes
- Docker-capable remote build host if the local machine cannot run Docker

## Required local setup

1. Clone the repository.
2. Supply the private OpenSCAD sources locally:
   - place `UnLimbited_Arm_V2.2.scad` at the repo root
   - place `UnLimbitedPhoenix.scad` at the repo root
   - place `correctv3/UnLimbited Arm V3.00.scad`
   - place `correctv3/Splines.scad`
3. Create local Terraform deployment settings:
   - copy [`infra/aws/terraform.tfvars.example`](/Users/droo/arminator/infra/aws/terraform.tfvars.example) to `infra/aws/terraform.tfvars`
   - fill in the real values needed for deployment
4. Configure AWS credentials locally so Terraform, ECR, Lambda, ECS, and S3 access work.
5. If pushing to GitHub, configure git identity and GitHub SSH access.
6. Before pushing changes, run:
   - `bash scripts/check_repo_hygiene.sh`

## Renderer image

The renderer image is rebuilt from:

- [`Dockerfile.renderer-trixie`](/Users/droo/arminator/Dockerfile.renderer-trixie)
- [`scripts/build_renderer_trixie_image.sh`](/Users/droo/arminator/scripts/build_renderer_trixie_image.sh)

The built image itself is not stored in Git.

For the full bootstrap order, HTTPS/DNS steps, SES requirements, and verification checklist, continue in [`REDEPLOY_FROM_SCRATCH.md`](/Users/droo/arminator/REDEPLOY_FROM_SCRATCH.md).

## OpenSCAD source rule

No `.scad` files are committed to this repository.

If you add or create any local `.scad` files, they remain ignored and must not be pushed.
