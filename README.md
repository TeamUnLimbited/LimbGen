# UnLimbited Assistive Device Generator

This repository turns private local UnLimbited arm OpenSCAD sources into a public web workflow where a user:

- enters request details and arm measurements
- verifies their email with a magic link
- selects a device:
  - `Version 2`
  - `Version 3 Beta`
  - `UnLimbited Phoenix`
- starts a fresh part generation job
- watches per-part progress
- downloads a ZIP of generated STL files

The production deployment is live at [https://limbgen.teamunlimbited.org](https://limbgen.teamunlimbited.org).

## OpenSCAD source files

No real `.scad` files are committed to this repository. The private device source files used for `v2`, `v3`, and `phoenix` rendering must remain local and must not be synced to GitHub.

- The repository includes a placeholder at [`UnLimbited Arm V3.00.scad.example`](/Users/droo/arminator/UnLimbited%20Arm%20V3.00.scad.example)
- Without the real local source files, render and deployment paths that package OpenSCAD sources will not work.

For a fresh clone checklist, see [`SETUP_ON_NEW_MACHINE.md`](/Users/droo/arminator/SETUP_ON_NEW_MACHINE.md).

## Repo hygiene

The repository history has been scrubbed to remove all `.scad` files, and `.scad` files are now ignored going forward.

Future sync safety is enforced by:

- [`scripts/check_repo_hygiene.sh`](/Users/droo/arminator/scripts/check_repo_hygiene.sh)
- [repo-hygiene.yml](/Users/droo/arminator/.github/workflows/repo-hygiene.yml)

That check blocks:

- tracked `.scad` files
- `.scad` files in reachable git history
- local-only Terraform state and override files
- obvious secret and private-environment markers

## Renderer image

The built renderer container image is not stored in Git. This repository tracks the image definition and rollout inputs instead:

- [`Dockerfile.renderer-trixie`](/Users/droo/arminator/Dockerfile.renderer-trixie)
- [`scripts/build_renderer_trixie_image.sh`](/Users/droo/arminator/scripts/build_renderer_trixie_image.sh)
- [`infra/aws/terraform.tfvars.example`](/Users/droo/arminator/infra/aws/terraform.tfvars.example)

The current live renderer image tag is documented below and can be rebuilt from this repository.

## Current production architecture

Production no longer uses the original always-on Flask container.

It now runs as a low-idle AWS stack:

- `CloudFront` for the public site and HTTPS
- `S3` for the static frontend in [`site/`](/Users/droo/arminator/site)
- `Lambda` for the API in [`lambda_api.py`](/Users/droo/arminator/lambda_api.py) and [`arminator_aws_backend.py`](/Users/droo/arminator/arminator_aws_backend.py)
- `DynamoDB` for job state, verification tokens, and verified sessions
- `ECS/Fargate` one-off renderer tasks using [`renderer_job.py`](/Users/droo/arminator/renderer_job.py)
- `S3` for generated STL and ZIP artifacts

The Terraform for this stack is in [`infra/aws/`](/Users/droo/arminator/infra/aws).

For the architecture diagrams and component map, see [`docs/architecture/ARCHITECTURE_OVERVIEW.md`](/Users/droo/arminator/docs/architecture/ARCHITECTURE_OVERVIEW.md).

## Website copy drafts

The repository now includes paste-ready website copy drafts for the public device-information pages:

- [`docs/version2-page-draft.md`](/Users/droo/arminator/docs/version2-page-draft.md)
- [`docs/version2-page-paste-ready-with-image-notes.txt`](/Users/droo/arminator/docs/version2-page-paste-ready-with-image-notes.txt)
- [`docs/phoenix-page-paste-ready-with-image-notes.txt`](/Users/droo/arminator/docs/phoenix-page-paste-ready-with-image-notes.txt)

Supporting extracted/collected image assets live in:

- [`docs/version2-assets/`](/Users/droo/arminator/docs/version2-assets)
- [`docs/phoenix-hero.jpg`](/Users/droo/arminator/docs/phoenix-hero.jpg)

## What the app does now

- Always generates the full kit; users do not choose individual parts
- Uses the public OpenSCAD customizer parameters only
- Requires a device selection before generation:
  - `Arm`
    - `Version 2`
    - `Version 3 Beta`
  - `Hand`
    - `UnLimbited Phoenix`
- Loads device-specific measurement fields and validation rules from the matching SCAD source
- Uses a verification-first left-to-right flow:
  - `Verify Session` establishes or reuses the verified session
  - `Generate` in panel 3 starts the actual job
  - `End Session` clears the cookie-backed verified session and requires a fresh magic link
- Generates parts in this order:
  1. `Pins`
  2. `Cuff Jig`
  3. `Cuff`
  4. `Forearm`
  5. `Hand`
- Renders `Hand` last because it is the slowest part
- Shows current part, part list state, elapsed time, and heartbeat-style activity
- Swaps progress images from [`progressimages/`](/Users/droo/arminator/progressimages) to match the part being generated
- Preserves entered form values locally and through verification/reconnect flows
- Uses an `HttpOnly` browser cookie for the verified session/browser identity
- Starts the indeterminate progress animation immediately when generation begins
- Forces fresh generation each time; completed-job cache reuse is disabled
- Completion emails include request details, recipient/project metadata, generation parameters, and a donation link
- Completion email download links now state that the generator link remains valid for 7 days
- Internal structured generation reports are emailed to `drew@teamunlimbited.org`
- Internal structured generation reports now use the subject `ARM GENERATION`
- Internal report delivery is attempted even if the user-facing completion email fails
- Generated ZIP/job retention now defaults to 7 days
- After terminal completion, the job record is scrubbed of requester details and verified email
- The verified-session draft is cleared once a job is started so form details are not retained server-side longer than necessary

## Current request flow

1. User lands on the form immediately; there is no start screen.
2. Panel `1 - Request Details` is active; panels `2 - Select Device and Set Parameters` and `3 - Generate` start greyed out.
3. User enters:
   - requester details
   - country
   - purpose
   - recipient or project metadata
4. Clicking `Verify Session` opens the email-verification modal if the browser session is not yet verified.
5. The user receives a magic link, verifies, and returns to the site.
6. After verification, panel 2 unlocks. Panel 3 unlocks when a device is selected.
7. The user selects `Version 2`, `Version 3 Beta`, or `UnLimbited Phoenix`, fills the device-specific parameter set, and clicks `Generate` in panel 3.
8. While generation is active, panel 2 locks again so in-flight parameters cannot drift.
9. The finished ZIP is available from the browser, and optionally by email once SES production access is enabled.

## Current UI layout

The public UI is a three-column desktop layout:

- left: request details
- middle: measurements
- right: progress and download state

Current live panel headings:

- `1 - Request Details`
- `2 - Select Device and Set Parameters`
- `3 - Generate`

Current left-column actions:

- `Verify Session` is green until the session is verified, then it is greyed out
- `End Session` is red and disabled until a verified session exists

Current live measurements behavior:

- No device is preselected on first load; the user must choose one to continue
- The selector is split into:
  - `Arm`
  - `Hand`
- `Version 2`, `Version 3 Beta`, and `UnLimbited Phoenix` load different parameter schemas
- `V2` currently presents:
  - `Arm Selection`
  - `Hand Measurements (mm)`
  - `Arm Measurements (mm)`
  - `Other Parameters`
- `V3` currently presents:
  - `Arm Selection`
  - `Hand Measurements (mm)`
  - `Arm Measurements (mm)`
- `Phoenix` currently presents:
  - `Hand Selection`
  - `Hand Measurements (%)`
- `V3` hand measurements use a 1x4 desktop layout

Current live typography:

- panel headings use `Poppins`
- main UI/body text uses `Open Sans`
- box titles/legends are bold
- values inside inputs/selects/radios are regular weight
- version help links such as `Read here if your not sure.` and `Instructions` are italic

Key frontend files:

- [`site/index.html`](/Users/droo/arminator/site/index.html)
- [`site/app.js`](/Users/droo/arminator/site/app.js)
- [`site/styles.css`](/Users/droo/arminator/site/styles.css)

For detailed theming and DOM constraints, see [`UI_CUSTOMIZATION.md`](/Users/droo/arminator/UI_CUSTOMIZATION.md).

## Current backend files

- [`arminator_common.py`](/Users/droo/arminator/arminator_common.py)
  - OpenSCAD parameter parsing
  - label overrides
  - render command construction
  - part ordering
  - ZIP naming
- [`arminator_aws_backend.py`](/Users/droo/arminator/arminator_aws_backend.py)
  - API business logic
  - job creation
  - verification tokens and sessions
  - DynamoDB/S3/ECS/SES integration
- [`lambda_api.py`](/Users/droo/arminator/lambda_api.py)
  - Lambda entrypoint
- [`renderer_job.py`](/Users/droo/arminator/renderer_job.py)
  - one-off Fargate renderer worker

## Important current behavior

- Full-kit only generation
- Fresh render per submission
- Active-job reconnect still exists to prevent duplicate in-flight work
- Generation always uses the live current form values at the instant `Generate` is clicked, not a stale saved verification draft
- ZIP names encode key measurements, for example:
  - `RK64HL141WW45WH35FL150BC198.zip`
- The UI no longer lists generated STL filenames in the status card
- The current arm label is intentionally `Forarm Length` because that is what was requested in the live UI
- User-facing copy now prefers `generate/generating` instead of `render/rendering`
- Session/draft helpers now include:
  - `End Session`, which clears the `arminator_client_id` cookie and the verified server-side session

## Retention note

- App-level job retention and completion-email copy currently say artifact links are valid for `7 days`
- The live S3 artifacts bucket lifecycle in AWS is currently set to `3 days` as of `2026-03-21`
- This mismatch should be reconciled before relying on the public retention wording

## Email status

Domain-level SES setup is complete for `teamunlimbited.org`:

- domain verification: complete
- DKIM: complete
- custom MAIL FROM: complete

But public email sending is still blocked because the SES account in `eu-west-2` is still in sandbox after a production-access denial.

That means:

- magic-link and completion-email code is deployed
- delivery works only within sandbox rules
- public recipients will not work until AWS approves SES production access
- even with correct SPF/DKIM/DMARC, Gmail may still place sandbox/test mail into spam because sender reputation is still new

See [`HANDOFF.md`](/Users/droo/arminator/HANDOFF.md) for the latest operational status.

## Deployment

Typical production changes are deployed with targeted Terraform applies:

```bash
/opt/homebrew/bin/terraform -chdir=infra/aws apply -target=aws_lambda_function.api -auto-approve
/opt/homebrew/bin/terraform -chdir=infra/aws apply -target=aws_s3_object.site_files -auto-approve
```

Then invalidate CloudFront when static assets change:

```bash
/usr/local/bin/aws cloudfront create-invalidation \
  --distribution-id E10FKGA9LCY1CH \
  --paths '/index.html' '/app.js' '/styles.css'
```

Terraform variables currently live in [`infra/aws/terraform.tfvars`](/Users/droo/arminator/infra/aws/terraform.tfvars).

The current live renderer image tag is:

- `236209347845.dkr.ecr.eu-west-2.amazonaws.com/arminator-renderer:20260321-174441-phoenix-device`

The current renderer deployment version env is:

- `20260321-174441-phoenix-device`

When local `docker` is unavailable, the renderer image can be rebuilt on another Docker-capable machine and then rolled out by updating [`infra/aws/terraform.tfvars`](/Users/droo/arminator/infra/aws/terraform.tfvars) and applying the ECS task definition/Lambda changes.

The current live renderer path is the dedicated `trixie` OpenSCAD image described in [`Dockerfile.renderer-trixie`](/Users/droo/arminator/Dockerfile.renderer-trixie). Rollout and rollback instructions are documented in [`RENDERER_TRIXIE_ROLLOUT.md`](/Users/droo/arminator/RENDERER_TRIXIE_ROLLOUT.md).

## Legacy local/container app

Older local Docker/Flask files still exist in the repo:

- [`app.py`](/Users/droo/arminator/app.py)
- [`Dockerfile`](/Users/droo/arminator/Dockerfile)
- [`docker-compose.yml`](/Users/droo/arminator/docker-compose.yml)

They are no longer the production path. Treat them as legacy unless you explicitly want to revive the original single-container deployment.

The dedicated production renderer image definition is separate:

- [`Dockerfile.renderer-trixie`](/Users/droo/arminator/Dockerfile.renderer-trixie)

## Additional docs

- [`PROJECT_STATUS.md`](/Users/droo/arminator/PROJECT_STATUS.md): consolidated project state, major decisions, and operational learnings
- [`UI_CUSTOMIZATION.md`](/Users/droo/arminator/UI_CUSTOMIZATION.md): frontend theming and DOM constraints
- [`HANDOFF.md`](/Users/droo/arminator/HANDOFF.md): current live state, AWS resources, recent changes, and unresolved items

Recommended handoff reading order:

1. [`README.md`](/Users/droo/arminator/README.md)
2. [`docs/architecture/ARCHITECTURE_OVERVIEW.md`](/Users/droo/arminator/docs/architecture/ARCHITECTURE_OVERVIEW.md)
3. [`PROJECT_STATUS.md`](/Users/droo/arminator/PROJECT_STATUS.md)
4. [`HANDOFF.md`](/Users/droo/arminator/HANDOFF.md)
5. [`UI_CUSTOMIZATION.md`](/Users/droo/arminator/UI_CUSTOMIZATION.md)

## License

This repository is licensed under the Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License:

- [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)

See [`LICENSE`](/Users/droo/arminator/LICENSE) for the repository license notice.
