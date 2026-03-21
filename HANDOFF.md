# Handoff Notes

This file is the current operational snapshot for the live Team UnLimbited limb generator.

For the broader design history, implementation decisions, and consolidated lessons learned, read [`PROJECT_STATUS.md`](/Users/droo/arminator/PROJECT_STATUS.md) alongside this file.

For architecture diagrams and the AWS component map, read [`docs/architecture/ARCHITECTURE_OVERVIEW.md`](/Users/droo/arminator/docs/architecture/ARCHITECTURE_OVERVIEW.md).

## Live service

- Public URL: [https://limbgen.teamunlimbited.org](https://limbgen.teamunlimbited.org)
- CloudFront domain: `dgoyd3w2re4bs.cloudfront.net`
- GitHub repo remote: `git@github.com:TeamUnLimbited/LimbGen.git`
- AWS account: `236209347845`
- Region: `eu-west-2`
- Current renderer deployment version env: `20260321-174441-phoenix-device`
- Current renderer image: `236209347845.dkr.ecr.eu-west-2.amazonaws.com/arminator-renderer:20260321-174441-phoenix-device`
- Current renderer task definition revision: `arminator-renderer:19`
- Current renderer rollback target: [`RENDERER_TRIXIE_ROLLOUT.md`](/Users/droo/arminator/RENDERER_TRIXIE_ROLLOUT.md)

## Current AWS resources

- CloudFront distribution for public delivery
- S3 site bucket for static assets
- S3 artifacts bucket for generated STL/ZIP files
- Lambda API function: `arminator-api`
- DynamoDB jobs table: `arminator-jobs`
- ECS cluster: `arminator-cluster`
- Renderer task definition: `arminator-renderer`
- There is no always-on ECS service; Lambda starts one-off Fargate tasks with `RunTask`

Terraform outputs are defined in [`infra/aws/outputs.tf`](/Users/droo/arminator/infra/aws/outputs.tf).

## GitHub repo state

- Repository remote is configured and pushes are working over SSH.
- The repository has been scrubbed so no `.scad` files exist in reachable git history.
- All `.scad` files are ignored going forward.
- Repo hygiene is now enforced by:
  - [`scripts/check_repo_hygiene.sh`](/Users/droo/arminator/scripts/check_repo_hygiene.sh)
  - [`.github/workflows/repo-hygiene.yml`](/Users/droo/arminator/.github/workflows/repo-hygiene.yml)
- The private OpenSCAD source remains local-only and is not present in the GitHub repository contents or reachable branch history.

### Public visibility

- As of `2026-03-21`, the repository is not publicly readable from anonymous GitHub API access.
- SSH push access alone is not enough to change repository visibility.
- To switch the GitHub repo to public, a GitHub web session, `gh` auth, or a PAT with repository-admin rights is still required.

## Current production behavior

- The UI is a white three-column layout aligned to the Team UnLimbited brand
- The left column collects request details
- The middle column collects handedness and measurements
- The right column shows progress state, progress image, part list, and download button
- The panel headings currently shown in production are:
  - `1 - Request Details`
  - `2 - Select Device and Set Parameters`
  - `3 - Generate`
- The middle column now starts with a required device selector split into:
  - `Arm`
    - `Version 2`
    - `Version 3 Beta`
  - `Hand`
    - `UnLimbited Phoenix`
- No device is preselected on first load; the radio choice is required before generation
- `Version 2`, `Version 3 Beta`, and `UnLimbited Phoenix` use different SCAD-derived schemas
- The UI no longer shows a generated filename bullet list
- The request flow is now explicitly gated left-to-right:
  - `Verify Session` is the verification/session button
  - `Generate` in panel 3 is the only action that starts a render job
  - `End Session` clears the cookie-backed verified session and requires a new magic link
- User-facing copy now consistently prefers `generate/generating`
- All jobs generate the full kit; no part picker is exposed
- Every submission creates a fresh render
- The app still reconnects to an already-running job for the same active browser/session
- Verified-session identity is now based on the `arminator_client_id` browser cookie, not source IP
- Form values are preserved:
  - in local storage
  - through the email-verification flow
  - when reconnecting to an active job
- Generation payload capture is fresh at click time:
  - verification drafts are only restore convenience
  - request details and arm parameters are re-read from the live form when `Generate` is clicked
- The renderer is still single-worker:
  - one generation runs at a time
  - queued jobs now show queue position and ETA in the UI
  - new submissions are rejected once the queued backlog reaches the configured cap

## Queue settings

- Default max queued jobs: `8`
- Default queue ETA slot size: `35` seconds
- Optional env overrides:
  - `ARMINATOR_MAX_QUEUE_LENGTH`
  - `ARMINATOR_QUEUE_SLOT_ESTIMATE_SECONDS`

## Current part generation order

1. `Pins`
2. `Cuff Jig`
3. `Cuff`
4. `Forearm`
5. `Hand`

This is controlled in [`arminator_common.py`](/Users/droo/arminator/arminator_common.py) by `PART_RENDER_PRIORITY`.

## Current frontend files

- [`site/index.html`](/Users/droo/arminator/site/index.html)
- [`site/app.js`](/Users/droo/arminator/site/app.js)
- [`site/styles.css`](/Users/droo/arminator/site/styles.css)
- [`progressimages/`](/Users/droo/arminator/progressimages)

Current progress image mapping in [`site/app.js`](/Users/droo/arminator/site/app.js):

- queued/starting/default: `start.jpg`
- `Pins`: `pins.jpg`
- `Cuff Jig`: `cuffjig.jpg`
- `Cuff`: `cuff.jpg`
- `Forearm`: `forarm.jpg`
- `Hand`: `hand.jpg`

## Current backend files

- [`arminator_common.py`](/Users/droo/arminator/arminator_common.py)
- [`arminator_aws_backend.py`](/Users/droo/arminator/arminator_aws_backend.py)
- [`lambda_api.py`](/Users/droo/arminator/lambda_api.py)
- [`renderer_job.py`](/Users/droo/arminator/renderer_job.py)
- [`Dockerfile.renderer-trixie`](/Users/droo/arminator/Dockerfile.renderer-trixie)

## Session identity

The public site now uses an `HttpOnly` cookie:

- cookie name: `arminator_client_id`
- scope: `/`
- `Secure`
- `HttpOnly`
- `SameSite=Lax`

The frontend no longer sends `client_id` explicitly in normal API calls. Lambda reads or mints the browser identity from the cookie.

Current session controls:

- `Verify Session`: active only when not already verified; once verified it greys out
- `End Session`: clears the `arminator_client_id` cookie and deletes the verified session record server-side; it stays disabled until a verified session exists

Clearing browser cookies for `limbgen.teamunlimbited.org` also resets the verified-session state. Draft form values still live separately in local storage unless `End Session` or job start clears them.

## Open issues / known blockers

### SES

SES identity setup is complete, but public sending is still blocked:

- domain verification: `SUCCESS`
- DKIM: `SUCCESS`
- MAIL FROM: `SUCCESS`
- production access: denied / still sandboxed

Last known SES review details:

- `ProductionAccessEnabled: false`
- `ReviewDetails.Status: DENIED`
- `CaseId: 177401940100896`

Implication:

- verification and completion email code is deployed
- sandbox testing to verified domain recipients can work
- public magic links are not ready until SES production access is approved
- deliverability/auth configuration is technically correct, but Gmail may still place the message in spam because sender reputation is still new

### Mail auth / deliverability findings

Checked and currently correct:

- SES domain identity: verified
- DKIM: enabled and passing
- custom MAIL FROM: `sesmail.teamunlimbited.org`
- root SPF for Google Workspace
- MAIL FROM SPF for SES
- DMARC record present on `_dmarc.teamunlimbited.org`

Important learning:

- correct SPF/DKIM/DMARC does not guarantee inbox placement
- sandbox or newly established SES sending can still hit Gmail spam folders
- magic-link emails have phishing-like characteristics and may be treated conservatively by Gmail
- ask internal testers to use `Not spam` to help train Gmail on early messages

### Intentional wording quirks

The measurement label currently shown in the live UI is:

- `Forarm Length`

That spelling is intentional because it was explicitly requested for the public UI.

## Recent functional changes already live

- Team UnLimbited visual restyle
- three-column layout
- logo fix in header
- country field changed to dropdown and auto-defaulted from CloudFront country header
- recipient flow expanded with:
  - recipient name
  - recipient sex
  - recipient age
- dynamic summary label:
  - `Project Summary`
  - `Other Summary`
- hand measurements switched to a 2x2 desktop grid
- progress bar indeterminate animation starts immediately
- status panel includes progress image box
- STL filename bullet list removed from UI
- completed render cache disabled
- old cached completed generations purged from AWS
- verification restore bug fixed so slider values are restored correctly after magic-link verification
- verification strip now turns green when the link has been sent
- completion emails now include requester data, recipient/project metadata, parameters used, and donation link
- worker/status copy now says `generate/generating` after renderer image rebuild
- completion now also sends a structured internal report email to `drew@teamunlimbited.org`
- the internal structured report now uses the subject `ARM GENERATION`
- the renderer now attempts the internal report even if the user completion email throws an exception
- generated ZIP/job retention now defaults to 7 days
- completion emails now state that the generator download link is valid for 7 days
- completed/failed/canceled job records are scrubbed of requester details and verified email after terminal completion
- verified-session drafts are cleared when a job starts, and used verification-token records are stripped of email/draft data
- `Verify Session` and `End Session` are now the explicit session controls in panel 1
- `Instructions` links now sit beside the version names instead of making the version labels themselves hyperlinks
- `End Session` now clears both the browser cookie and the server-side session through `/api/session/end`
- `UnLimbited Phoenix` is now a third selectable device under the `Hand` group and is normalized to the same five public render phases

## Recent email delivery finding

- A live renderer log issue was identified on `2026-03-21`: internal generation reports were failing with `NameError: name 'json' is not defined` in [`arminator_aws_backend.py`](/Users/droo/arminator/arminator_aws_backend.py).
- That failure affected the internal `drew@teamunlimbited.org` generation report path.
- The fix is to import `json` in the AWS backend module and redeploy a new renderer image, because the renderer container vendors the repo code at build time.

## Current live UI specifics

- panel headings use `Poppins`
- main UI/body text uses `Open Sans`
- section legends such as `Arm Version`, `Arm Selection`, `Hand Measurements (mm)` are bold
- field values inside controls are regular weight
- panel 2 and panel 3 start greyed out until the session is verified and a device is selected
- `Verify Session` is green until the session is verified, then disabled
- `End Session` is red and disabled until a verified session exists
- version help links and the `Read here if your not sure.` link are italic
- the top-right nav now only shows `Contact` and `Donate`
- `V2` top cards are currently stacked full-width:
  - `Arm Selection`
  - `Hand Measurements (mm)`
- `V3` arm selection is centered within its card, with the dropdown text left-aligned
- `V3` hand measurements now use a 1x4 desktop row
- `Phoenix` currently shows:
  - `Hand Selection`
  - `Hand Measurements (%)`

## Deploy procedure

### API / Lambda changes

```bash
/opt/homebrew/bin/terraform -chdir=infra/aws apply -target=aws_lambda_function.api -auto-approve
```

### Static frontend changes

```bash
/opt/homebrew/bin/terraform -chdir=infra/aws apply -target=aws_s3_object.site_files -auto-approve
```

If progress images change too:

```bash
/opt/homebrew/bin/terraform -chdir=infra/aws apply \
  -target=aws_s3_object.site_files \
  -target=aws_s3_object.progress_files \
  -auto-approve
```

### CloudFront cache clear

```bash
/usr/local/bin/aws cloudfront create-invalidation \
  --distribution-id E10FKGA9LCY1CH \
  --paths '/index.html' '/app.js' '/styles.css'
```

If images changed, include `/progressimages/*`.

### Renderer image rebuild

If the renderer container code changes, a new image must be built and pushed before ECS can use it.

This machine may not have `docker`, so the current documented fallback is:

1. sync the repo to a Docker-capable build host
2. `docker login` that host to ECR
3. build and push the renderer image there
4. update [`infra/aws/terraform.tfvars`](/Users/droo/arminator/infra/aws/terraform.tfvars) with the new `renderer_image` tag and `deployment_version`
5. run targeted Terraform apply for:
   - `aws_ecs_task_definition.renderer`
   - `aws_lambda_function.api`

That last Lambda apply matters because the API environment contains the renderer task definition ARN.

The currently deployed renderer image is the dedicated `trixie` OpenSCAD build. For rebuild steps, rollout, and explicit rollback procedure, see [`RENDERER_TRIXIE_ROLLOUT.md`](/Users/droo/arminator/RENDERER_TRIXIE_ROLLOUT.md).

## Retention

- App-level job retention and completion-email wording currently say download links are valid for `7 days`
- The live S3 artifacts bucket lifecycle in AWS currently expires artifacts after `3 days` as of `2026-03-21`
- Terraform default in [`infra/aws/variables.tf`](/Users/droo/arminator/infra/aws/variables.tf) is still `3`
- This mismatch is currently unresolved and should be reconciled before depending on the public 7-day wording

## Handoff warning

Some repo files are legacy from the earlier always-on Flask deployment:

- [`app.py`](/Users/droo/arminator/app.py)
- [`Dockerfile`](/Users/droo/arminator/Dockerfile)
- [`docker-compose.yml`](/Users/droo/arminator/docker-compose.yml)

They are not the current production path. The active deployment path is the AWS low-idle stack under [`infra/aws/`](/Users/droo/arminator/infra/aws).

## Practical testing notes

- Any `@teamunlimbited.org` address can be used for SES sandbox testing because the whole domain identity is verified.
- External addresses will not work until SES production access is approved or the specific external address is separately verified.
- A verification token can be requested in one session and clicked in the same browser later; the saved draft should now restore correctly.
