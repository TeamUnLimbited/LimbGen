# UI Customization Guide

The current frontend is split into:

- layout in [`site/index.html`](/Users/droo/arminator/site/index.html)
- behavior in [`site/app.js`](/Users/droo/arminator/site/app.js)
- theme and layout styles in [`site/styles.css`](/Users/droo/arminator/site/styles.css)

The safest way to tweak the look is to work primarily in `styles.css`, then make only small structural changes in `index.html`.

## Current layout

The production UI is a three-column desktop layout:

- request details on the left
- measurements in the middle
- progress and download state on the right

Current primary action label:

- `Generate Arm`

The form fields themselves are still generated dynamically from `/api/config`.

## Safe to change

These are safe to restyle freely:

- colors in `:root`
- fonts
- spacing
- widths
- borders
- backgrounds
- shadows
- button styling
- panel sizing
- progress image framing

These classes are good candidates for layout/theming changes:

- `.page-shell`
- `.topbar`
- `.brand`
- `.workspace`
- `.workspace-panel`
- `.details-panel`
- `.parameters-panel`
- `.status-panel`
- `.status-card`
- `.section-card`
- `.field-group`
- `.radio-list`
- `.radio-option`
- `.range-field`
- `.range-slider`
- `.verification-strip`
- `.modal-card`
- `.progress-visual`

## Do not rename

These IDs are read directly by JavaScript and must stay intact:

- `generator-form`
- `submit-button`
- `submission-note`
- `request-sections`
- `parameter-sections`
- `progress-track`
- `progress-fill`
- `progress-value`
- `progress-visual`
- `progress-visual-image`
- `progress-visual-caption`
- `job-status`
- `job-message`
- `job-detail`
- `job-meta`
- `part-list`
- `error-box`
- `cancel-button`
- `download-link`
- `page-title`
- `page-subtitle`
- `verification-strip`
- `verification-title`
- `verification-detail`
- `verification-modal-shell`
- `verification-modal-backdrop`
- `verification-form`
- `verification-email`
- `verification-notify`
- `verification-cancel`
- `verification-submit`
- `verification-error`

If any of those IDs disappear, the page may still render but the behavior will break.

## How the form is built

The request and measurement controls are not hardcoded in [`site/index.html`](/Users/droo/arminator/site/index.html).

They are rendered by `renderForm()` in [`site/app.js`](/Users/droo/arminator/site/app.js) using:

- `GET /api/config`

That payload comes from `frontend_config()` in [`arminator_aws_backend.py`](/Users/droo/arminator/arminator_aws_backend.py).

Form sections come from:

1. `REQUEST_FIELDS` in [`arminator_aws_backend.py`](/Users/droo/arminator/arminator_aws_backend.py)
2. parsed public OpenSCAD parameters in [`arminator_common.py`](/Users/droo/arminator/arminator_common.py)

## Current request fields

The request-details section currently includes:

- `name`
- `country`
- `purpose`
- `recipient_sex`
- `recipient_name`
- `recipient_age`
- `summary`

If you want to add, remove, or reorder request metadata, do it in `REQUEST_FIELDS`, not only in the DOM.

## Control types

The frontend currently supports these field kinds:

- `text`
- `email`
- `number_input`
- `textarea`
- `radio`
- `select`
- `number`

`number` is the measurement control:

- read-only numeric value box
- slider underneath
- min/max/step from the backend config

If a measurement should stay slider-driven, keep it as `kind: "number"`.

## Conditional field behavior

Conditional display is driven by:

- `show_when` in the config
- `data-show-field`
- `data-show-values`
- `updateConditionalFields()` in [`site/app.js`](/Users/droo/arminator/site/app.js)

Current behavior:

- if `purpose = recipient`
  - show `recipient_sex`
  - show `recipient_name`
  - show `recipient_age`
- if `purpose = project`
  - show `summary` labeled `Project Summary`
- if `purpose = other`
  - show `summary` labeled `Other Summary`

## Country field

The country field is currently a dropdown list, not a free-text box.

It is:

- populated client-side from `COUNTRY_CODES`
- defaulted from the CloudFront viewer country header when available
- still fully editable by the user through the dropdown

If you replace it, keep the field name `country` unless you also update backend validation.

## Status area

The status panel is updated from:

- `GET /api/jobs/:job_id`

The panel currently expects these elements:

- `progress-track`
- `progress-fill`
- `progress-value`
- `progress-visual`
- `progress-visual-image`
- `progress-visual-caption`
- `job-status`
- `job-message`
- `job-detail`
- `job-meta`
- `part-list`
- `download-link`

The generated filename bullet list was intentionally removed, so there is no `output-list` requirement anymore.

## Progress visuals

The progress image box uses files from [`progressimages/`](/Users/droo/arminator/progressimages).

Current mapping in [`site/app.js`](/Users/droo/arminator/site/app.js):

- default: `start.jpg`
- `Pins`: `pins.jpg`
- `Cuff Jig`: `cuffjig.jpg`
- `Cuff`: `cuff.jpg`
- `Forearm`: `forarm.jpg`
- `Hand`: `hand.jpg`

If you change those filenames, update the mapping in `PROGRESS_VISUALS`.

## Verification flow

The verification UX depends on:

- the top verification strip
- the modal
- preserved draft data

Required modal and strip elements:

- `verification-strip`
- `verification-title`
- `verification-detail`
- `verification-modal-shell`
- `verification-form`
- `verification-email`
- `verification-notify`

Current behavior:

- unverified user clicks `Generate Arm`
- modal opens
- verification email is requested
- user returns via magic link
- verified session is loaded
- draft values are reapplied
- `Generate Arm` becomes ready

There are now three visual states in the verification strip:

- verification required
- verification link sent
- email verified

Verified-session identity is now cookie-based via `arminator_client_id`, not a JavaScript-managed browser ID.

## Draft preservation

Form values are preserved in two ways:

- browser local storage under `arminator-form-draft`
- server-side session/verification payloads

That behavior lives in [`site/app.js`](/Users/droo/arminator/site/app.js) and [`arminator_aws_backend.py`](/Users/droo/arminator/arminator_aws_backend.py).

Important implementation detail:

- slider restoration must set the slider position itself, not only the read-only numeric field
- otherwise the slider sync pass will overwrite restored values back to defaults

If someone rewrites the form markup manually, they need to preserve:

- input `name` attributes
- `collectPayload()`
- `applyDraft()`
- conditional field logic

## Safe theming strategy

Best sequence:

1. change `styles.css`
2. adjust high-level structure in `index.html`
3. avoid touching `app.js` unless behavior must change
4. if fields change, update backend config and frontend together

## Quick regression checklist

After any UI work, test:

1. page loads without JS errors
2. country defaults sensibly
3. `Recipient` shows sex, name, and age
4. `Project` and `Other` show summary with the correct label
5. sliders still update the read-only number boxes
6. Generate opens the verification modal when unverified
7. verified return preserves form values
8. active-job reconnect preserves form values
9. progress animation starts immediately when generation starts
10. ZIP download link appears when the job completes

## Common breakages

- renaming required IDs
- removing `name` attributes from fields
- replacing generated form markup without updating `collectPayload()`
- changing field names like `purpose`, `recipient_name`, `recipient_age`, `Knuckle_Width`, or `ForearmLen`
- breaking the verification modal IDs
- changing progress image filenames without updating `PROGRESS_VISUALS`
