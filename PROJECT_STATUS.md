# Project Status

This document consolidates the current state of the Team UnLimbited limb generator, the major decisions made so far, and the operational lessons learned during implementation.

## Purpose

The project turns private local UnLimbited arm OpenSCAD sources into a public workflow where a user:

- enters request details and measurements
- verifies their email with a magic link
- chooses `Version2 Alfie Edition` or `Version 3 BETA`
- starts a fresh generation job
- tracks progress while parts are generated
- downloads a ZIP of STL files

Live URL:

- [https://limbgen.teamunlimbited.org](https://limbgen.teamunlimbited.org)

## Architecture chosen

The original always-on Flask container was replaced with a low-idle AWS design because idle cost mattered more than simplicity.

Current production stack:

- `CloudFront` for the public HTTPS endpoint
- `S3` for the static frontend in [`site/`](/Users/droo/arminator/site)
- `Lambda` for the API in [`lambda_api.py`](/Users/droo/arminator/lambda_api.py) and [`arminator_aws_backend.py`](/Users/droo/arminator/arminator_aws_backend.py)
- `DynamoDB` for jobs, verification tokens, and verified sessions
- `ECS/Fargate` one-off renderer tasks using [`renderer_job.py`](/Users/droo/arminator/renderer_job.py)
- `S3` for STL and ZIP artifacts

Terraform for the live stack is under [`infra/aws/`](/Users/droo/arminator/infra/aws).

## Live product behavior

- The site opens directly to a plain form.
- The UI is a three-column layout:
  - left: request details
  - middle: arm selection and measurements
  - right: generation status
- The request flow is now verification-first and left-to-right:
  - `Lets Go !` establishes or reuses the verified session
  - panel 2 unlocks after verification
  - panel 3 unlocks after a device is selected
  - `Generate` in panel 3 is the only action that actually starts a render
- The middle column now requires an arm-version choice before generation:
  - `Version2 Alfie Edition`
  - `Version 3 BETA`
- No arm version is preselected on first load
- Full-kit generation only. Users no longer pick individual parts.
- Panel 1 now also includes:
  - `Reset`, which clears request/device fields but keeps the session
  - `End Session`, which clears the cookie-backed verified session and requires a new magic link
- User-facing copy prefers `generate/generating`, not `render/rendering`.
- Every submission creates a fresh generation. Completed-job cache reuse is disabled.
- Duplicate in-flight work is still avoided by reconnecting to an already-active job for the same browser/session.
- Verified state is browser-cookie based via `arminator_client_id`, not source-IP based.
- Form values are preserved locally and restored through the verification flow.
- Generation payload capture intentionally uses the current live form values at click time, not a stale saved draft from the earlier verification step.

## Current generation order

The parts are generated in this order:

1. `Pins`
2. `Cuff Jig`
3. `Cuff`
4. `Forearm`
5. `Hand`

`Hand` is intentionally last because it is the slowest part.

This is controlled by `PART_RENDER_PRIORITY` in [`arminator_common.py`](/Users/droo/arminator/arminator_common.py).

## UI decisions now in production

- White background and Team UnLimbited-aligned palette
- Team UnLimbited header/logo treatment
- Panel headings now use:
  - `1 - Request Details`
  - `2 - Select Device and Set Parameters`
  - `3 - Generate`
- Panel headings use `Poppins`; main UI/body text uses `Open Sans`
- Section legends are bold while field/control values are regular weight
- `Lets Go !` is always green, `Reset` is grey, and `End Session` is red
- Country is a dropdown, auto-defaulted from the CloudFront country header when available
- Recipient flow includes:
  - recipient sex
  - recipient name
  - recipient age
- Project and Other flows share the summary field, with a dynamic label:
  - `Project Summary`
  - `Other Summary`
- `V2` and `V3` use different SCAD-derived parameter schemas and labels
- Version help links sit beside the version names as italic `Instructions` links
- The `Read here if your not sure.` helper link is italic
- `V2` currently presents:
  - `Arm Selection`
  - `Hand Measurements (mm)`
  - `Arm Measurements (mm)`
  - `Other Parameters`
- `V3` currently presents:
  - `Arm Selection`
  - `Hand Measurements (mm)`
  - `Arm Measurements (mm)`
- Hand measurements use a 2x2 desktop layout to reduce vertical space
- Progress image box switches per generated part from [`progressimages/`](/Users/droo/arminator/progressimages)
- The generated STL filename bullet list was intentionally removed from the status UI so the layout fits without scrolling

## Important implementation learnings

### Progress behavior

- Stock OpenSCAD does not provide a trustworthy internal percentage for long-running CLI exports.
- Honest progress works better than fake percentages.
- The current UX shows:
  - queue/starting/running state
  - current part
  - elapsed time
  - part progression
  - progress image
- The indeterminate progress animation must start immediately when generation begins; otherwise users assume the job is hung.

### State restoration

- Saving a draft is not enough; the UI must restore slider positions themselves, not only the numeric display values.
- A previous bug restored the read-only value fields but then overwrote them from default slider values.
- The current fix is in [`site/app.js`](/Users/droo/arminator/site/app.js): draft reapplication sets the slider positions and then syncs the display from the sliders.
- Drafts are now convenience only. The authoritative generation payload comes from the current live form when `Generate` is clicked.
- `Reset` now clears both the local draft and the saved server-side session draft.

### Session identity

- Source-IP-based identity is not suitable for this product.
- Browser-cookie identity is more predictable for verification and testing.
- Current cookie:
  - name: `arminator_client_id`
  - `HttpOnly`
  - `Secure`
  - `SameSite=Lax`
- There are now explicit session controls:
  - `Reset` keeps the session but clears form/device state
  - `End Session` clears the cookie and deletes the verified session record server-side

### AWS deployment

- Static frontend + Lambda + one-off Fargate tasks is materially cheaper than an always-on ECS web container.
- The renderer container still has to be rebuilt separately when worker code changes.
- If the local machine does not have usable `docker`, the renderer image can be rebuilt on a separate Docker-capable machine.
- The live renderer now uses a dedicated `openscad/openscad:trixie`-based image with Manifold forced through [`Dockerfile.renderer-trixie`](/Users/droo/arminator/Dockerfile.renderer-trixie).
- Rollback instructions for that renderer image are kept in [`RENDERER_TRIXIE_ROLLOUT.md`](/Users/droo/arminator/RENDERER_TRIXIE_ROLLOUT.md).
- Internal structured generation reports are sent to `drew@teamunlimbited.org` with the subject `ARM GENERATION`.
- Generated ZIP/job retention now defaults to 7 days.
- Completion emails now explicitly state that the generator download link is valid for 7 days.
- A renderer-side bug on `2026-03-21` caused internal generation reports to fail with `NameError: name 'json' is not defined`; that requires a renderer image rebuild to fix in production.
- As of `2026-03-21`, the live S3 artifact bucket lifecycle is still `3 days`, so the public 7-day wording and the actual bucket expiration are not yet aligned.

### Rendering performance

- The original Debian/OpenSCAD `2021.01` renderer path was materially slower.
- The current live renderer uses newer OpenSCAD `2026.01.19` from the official `trixie` image base.
- The largest observed speed gain came from newer OpenSCAD plus Manifold support, not from `xvfb` removal alone.
- Output geometry is extremely close to the older renderer but not byte-identical, so the rollback plan is intentionally retained.

### S3 download links

- Presigned URLs must use the regional S3 endpoint.
- A previous global-endpoint redirect caused `SignatureDoesNotMatch`.
- That was fixed by generating URLs against the regional endpoint directly.

### Deliverability

- SPF, DKIM, DMARC, and custom MAIL FROM can all be correct and Gmail can still place messages in spam.
- Early transactional/magic-link traffic from a new SES sender should be assumed to have weak reputation.
- The current issue is not broken DNS auth; it is sender reputation plus SES sandbox status.

## Email status

SES is configured correctly for `teamunlimbited.org`:

- domain verification: `SUCCESS`
- DKIM: `SUCCESS`
- custom MAIL FROM: `SUCCESS`

But the account is still blocked for public sending because SES production access in `eu-west-2` was denied and the account remains in sandbox.

Current implications:

- magic-link and completion-email code is already deployed
- internal testing to `@teamunlimbited.org` addresses can work in sandbox
- external recipients will not work until AWS approves production access or the exact external address is verified
- Gmail may still spam-folder internal tests

## Completion email behavior

The completion email now includes:

- requester details
- recipient details when relevant
- project/other summary when relevant
- all generation parameters used
- download link
- donation link:
  - [PayPal donation page](https://www.paypal.com/donate/?cmd=_s-xclick&hosted_button_id=A64GWM82ZV3EE&source=url&ssrt=1774037086418)

There is also now an internal structured report email sent to:

- `drew@teamunlimbited.org`

That report includes:

- requester details entered on the form
- recipient/project metadata
- all arm parameters used
- job start and finish time
- duration
- a JSON block delimited with:
  - `BEGIN_ARMINATOR_JSON`
  - `END_ARMINATOR_JSON`

The report is intended to be parseable later into a local database or spreadsheet.

## Personal-data retention adjustments

To reduce how much personal information stays online:

- used verification-token records are scrubbed of `email` and `draft`
- verified-session `draft` data is cleared when a job starts
- terminal job records are scrubbed of:
  - `requester`
  - `verified_email`
  - `notify_completed`

This keeps the operational job metadata and artifacts, but removes the submitted requester details from completed job records.

## Files that matter most

Core backend:

- [`arminator_common.py`](/Users/droo/arminator/arminator_common.py)
- [`arminator_aws_backend.py`](/Users/droo/arminator/arminator_aws_backend.py)
- [`lambda_api.py`](/Users/droo/arminator/lambda_api.py)
- [`renderer_job.py`](/Users/droo/arminator/renderer_job.py)

Frontend:

- [`site/index.html`](/Users/droo/arminator/site/index.html)
- [`site/app.js`](/Users/droo/arminator/site/app.js)
- [`site/styles.css`](/Users/droo/arminator/site/styles.css)
- [`progressimages/`](/Users/droo/arminator/progressimages)

Infrastructure:

- [`infra/aws/main.tf`](/Users/droo/arminator/infra/aws/main.tf)
- [`infra/aws/variables.tf`](/Users/droo/arminator/infra/aws/variables.tf)
- [`infra/aws/terraform.tfvars`](/Users/droo/arminator/infra/aws/terraform.tfvars)
- [`infra/aws/outputs.tf`](/Users/droo/arminator/infra/aws/outputs.tf)

Legacy files still in the repo but not in the production path:

- [`app.py`](/Users/droo/arminator/app.py)
- [`Dockerfile`](/Users/droo/arminator/Dockerfile)
- [`docker-compose.yml`](/Users/droo/arminator/docker-compose.yml)

## Recommended handoff reading order

1. [`README.md`](/Users/droo/arminator/README.md)
2. [`docs/architecture/ARCHITECTURE_OVERVIEW.md`](/Users/droo/arminator/docs/architecture/ARCHITECTURE_OVERVIEW.md)
3. [`PROJECT_STATUS.md`](/Users/droo/arminator/PROJECT_STATUS.md)
4. [`HANDOFF.md`](/Users/droo/arminator/HANDOFF.md)
5. [`UI_CUSTOMIZATION.md`](/Users/droo/arminator/UI_CUSTOMIZATION.md)

## Current risk areas

- SES production access is still not approved
- Gmail deliverability is still immature
- OpenSCAD generation remains the dominant runtime bottleneck
- Renderer image changes require a build/push step, not only Terraform

## Short operational summary

The system is live, low-idle, and functional for generation/download flows. The main unresolved item is public email sending via SES. The biggest technical runtime constraint remains OpenSCAD generation speed, not the web layer.
