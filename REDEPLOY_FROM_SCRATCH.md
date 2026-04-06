# Redeploy From Scratch

This runbook documents the full recovery path for rebuilding the live AWS deployment from a fresh machine with no local context.

It is intentionally explicit about the parts that are not in Git, because the repository alone is not sufficient to generate devices without those private inputs.

## What Git contains

- application code
- frontend assets
- Terraform infrastructure code
- renderer image definition
- operational docs and rollout notes

## What Git does not contain

- private OpenSCAD source files
- local Terraform state
- local `terraform.tfvars`
- AWS credentials
- DNS provider credentials
- SES production-access approval state

## External dependencies you must restore first

### Private OpenSCAD files

The AWS Lambda deployment package and the renderer both depend on these real files existing at these exact repo-relative paths under the checkout root:

| Required file | Required path |
| --- | --- |
| `UnLimbited_Arm_V2.2.scad` | `UnLimbited_Arm_V2.2.scad` |
| `UnLimbitedPhoenix.scad` | `UnLimbitedPhoenix.scad` |
| `UnLimbited Arm V3.00.scad` | `correctv3/UnLimbited Arm V3.00.scad` |
| `Splines.scad` | `correctv3/Splines.scad` |

Notes:

- [`UnLimbited Arm V3.00.scad.example`](/Users/droo/arminator/UnLimbited%20Arm%20V3.00.scad.example) is only a placeholder.
- If any of the four real files above are missing, Terraform packaging and runtime generation will fail.
- Do not commit these files. Repo hygiene intentionally blocks `.scad` files.

### Access and accounts

You also need:

- AWS access to the target account and region
- control of the public DNS zone for the chosen hostname
- access to the SES domain identity for the sender domain
- a Docker-capable machine for renderer image builds

Current live reference values are recorded in [`HANDOFF.md`](/Users/droo/arminator/HANDOFF.md).

## Tooling prerequisites

Install:

- `git`
- `python3`
- `docker`
- `aws` CLI
- `terraform` `>= 1.5.0`

Optional but useful:

- GitHub SSH access
- a separate remote build host if the operator machine cannot run Docker

## Important state-management note

This Terraform stack does not currently use a remote backend. The active state is local-only unless you deliberately move it elsewhere.

That means:

- after a successful deploy, `infra/aws/terraform.tfstate` and backups must be stored in a secure operator-controlled location
- losing that state does not make redeploy impossible, but it does make safe incremental updates much harder
- if this system is expected to survive operator turnover, moving Terraform state to a remote backend is strongly recommended

## Deployment inputs

Start from [`infra/aws/terraform.tfvars.example`](/Users/droo/arminator/infra/aws/terraform.tfvars.example) and create a real local `infra/aws/terraform.tfvars`.

At minimum, verify these values before the first apply:

- `region`
- `project_name`
- `domain_name`
- `enable_https`
- `renderer_image`
- `deployment_version`
- `public_base_url`
- `email_from_address`
- `email_reply_to`
- `report_email_to`
- `artifact_retention_days`

Important relationships:

- `public_base_url` must match the public hostname users will open in the browser
- `renderer_image` and `deployment_version` should move together for each renderer rollout
- `artifact_retention_days` should remain aligned with the public promise of `7 days`

## Step-by-step rebuild

### 1. Clone the repo

```bash
git clone git@github.com:TeamUnLimbited/LimbGen.git arminator
cd arminator
```

### 2. Restore the private OpenSCAD bundle

Place the four private files at the exact paths listed above.

Quick check:

```bash
ls -l \
  UnLimbited_Arm_V2.2.scad \
  UnLimbitedPhoenix.scad \
  correctv3/'UnLimbited Arm V3.00.scad' \
  correctv3/Splines.scad
```

### 3. Configure AWS credentials

Use an IAM identity that can manage:

- ECR
- Lambda
- ECS/Fargate
- DynamoDB
- S3
- CloudWatch Logs
- IAM roles and policies
- CloudFront
- ACM
- SES

Sanity check:

```bash
aws sts get-caller-identity
aws configure get region
```

The current live stack uses account `236209347845` in region `eu-west-2`.

### 4. Create local Terraform settings

```bash
cp infra/aws/terraform.tfvars.example infra/aws/terraform.tfvars
```

Edit `infra/aws/terraform.tfvars` and set:

- the real public hostname
- the intended initial renderer image URI
- the intended deployment version
- the real email sender/reply-to/report addresses

For a brand-new rollout, pick a version string first:

```bash
export RENDERER_VERSION="$(date -u +%Y%m%d-%H%M%S)-initial-redeploy"
```

Then set both:

- `renderer_image = "ACCOUNT.dkr.ecr.REGION.amazonaws.com/arminator-renderer:${RENDERER_VERSION}"`
- `deployment_version = "${RENDERER_VERSION}"`

### 5. Initialize Terraform

```bash
terraform -chdir=infra/aws init
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws plan
```

### 6. Bootstrap the AWS stack

Run the first apply with `enable_https = false` unless ACM validation is already complete.

```bash
terraform -chdir=infra/aws apply
```

This creates or updates:

- ECR repository
- S3 buckets
- DynamoDB table
- ECS cluster and task definition
- Lambda function and function URL
- CloudFront distribution
- ACM certificate request when `domain_name` is set

Important:

- the ECS task definition can reference a renderer image tag before that tag is pushed
- do not run real generation jobs until the renderer image has actually been pushed to ECR

### 7. Complete manual DNS and HTTPS steps

This repo does not manage Route 53 or other DNS records. DNS must be updated manually at the domain provider.

After the first apply, retrieve:

```bash
terraform -chdir=infra/aws output cloudfront_domain_name
terraform -chdir=infra/aws output certificate_dns_validation_name
terraform -chdir=infra/aws output certificate_dns_validation_type
terraform -chdir=infra/aws output certificate_dns_validation_value
```

Create:

- the ACM validation DNS record using the certificate outputs above
- the public hostname record pointing to the CloudFront distribution domain

Then wait for ACM validation to complete, set `enable_https = true`, and apply again:

```bash
terraform -chdir=infra/aws apply
```

### 8. Build and push the renderer image

Use [`scripts/build_renderer_trixie_image.sh`](/Users/droo/arminator/scripts/build_renderer_trixie_image.sh) on a Docker-capable machine.

Example:

```bash
export AWS_REGION="eu-west-2"
export AWS_ACCOUNT_ID="236209347845"
export IMAGE_VERSION="${RENDERER_VERSION}"
export PUSH_IMAGE="1"

bash scripts/build_renderer_trixie_image.sh
```

That script:

- builds [`Dockerfile.renderer-trixie`](/Users/droo/arminator/Dockerfile.renderer-trixie)
- smoke-tests OpenSCAD and Python inside the image
- optionally logs into ECR and pushes the tag

If Docker is unavailable locally, do this on a separate build host with the repo and private `.scad` files present.

### 9. Configure SES for email flows

The code assumes SES is the mail transport for:

- magic-link verification
- completion emails
- internal structured generation reports

Before public email can work, the target AWS account must have:

- a verified SES domain identity
- DKIM enabled
- a working custom MAIL FROM domain if you want the current mail-auth shape
- correct SPF and DMARC records at the DNS provider
- `email_from_address` verified or covered by the verified SES identity

If public recipients must work, request SES production access in the deployment region. Without that, the system remains subject to SES sandbox limits.

Current live email status and lessons learned are documented in:

- [`README.md`](/Users/droo/arminator/README.md)
- [`PROJECT_STATUS.md`](/Users/droo/arminator/PROJECT_STATUS.md)
- [`HANDOFF.md`](/Users/droo/arminator/HANDOFF.md)

### 10. Verify the rebuilt stack

Infrastructure checks:

```bash
terraform -chdir=infra/aws output
curl -i https://YOUR_PUBLIC_HOSTNAME/api/healthz
```

Expected health response includes:

- `"status": "ok"`
- `"backend": "lambda"`

Functional checks:

1. Load the public site.
2. Verify static assets load through CloudFront.
3. Trigger the email-verification flow.
4. Confirm a job can be queued.
5. Confirm the ECS renderer task starts and exits `0`.
6. Confirm all five parts complete.
7. Download the ZIP and inspect the generated STL files.

Useful runtime checks:

- Lambda logs: `/aws/lambda/arminator-api`
- renderer logs: `/ecs/arminator-renderer`
- DynamoDB table: `arminator-jobs`

### 11. Cache invalidation for frontend-only changes

If only static assets changed after the stack is live:

```bash
aws cloudfront create-invalidation \
  --distribution-id YOUR_DISTRIBUTION_ID \
  --paths '/index.html' '/app.js' '/styles.css'
```

Retrieve the current distribution domain from Terraform outputs. The current live distribution id is recorded in [`README.md`](/Users/droo/arminator/README.md) and [`HANDOFF.md`](/Users/droo/arminator/HANDOFF.md) for reference, but a clean rebuild may create a different distribution.

## Files to read before making production changes

Read in this order:

1. [`README.md`](/Users/droo/arminator/README.md)
2. [`REDEPLOY_FROM_SCRATCH.md`](/Users/droo/arminator/REDEPLOY_FROM_SCRATCH.md)
3. [`docs/architecture/ARCHITECTURE_OVERVIEW.md`](/Users/droo/arminator/docs/architecture/ARCHITECTURE_OVERVIEW.md)
4. [`PROJECT_STATUS.md`](/Users/droo/arminator/PROJECT_STATUS.md)
5. [`HANDOFF.md`](/Users/droo/arminator/HANDOFF.md)
6. [`RENDERER_TRIXIE_ROLLOUT.md`](/Users/droo/arminator/RENDERER_TRIXIE_ROLLOUT.md)

## Failure modes to remember

- Missing private `.scad` files break both Lambda packaging and runtime generation.
- Losing local Terraform state removes the safest path for incremental updates.
- DNS and SES are not fully codified in Terraform here; they require manual provider-side work.
- Public email delivery can still fail even with correct DNS if SES production access is not approved.
- The renderer image is built separately from Terraform and must exist in ECR before real jobs run.
