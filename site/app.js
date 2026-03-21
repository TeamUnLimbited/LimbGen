const form = document.getElementById("generator-form");
const submitButton = document.getElementById("submit-button");
const submissionNote = document.getElementById("submission-note");
const requestSections = document.getElementById("request-sections");
const parameterSections = document.getElementById("parameter-sections");
const armVersionPanel = document.getElementById("arm-version-panel");
const parametersPanel = document.getElementById("parameters-panel");
const statusPanel = document.getElementById("status-panel");
const progressTrack = document.getElementById("progress-track");
const progressFill = document.getElementById("progress-fill");
const progressValue = document.getElementById("progress-value");
const progressVisual = document.getElementById("progress-visual");
const progressVisualImage = document.getElementById("progress-visual-image");
const progressVisualCaption = document.getElementById("progress-visual-caption");
const jobStatus = document.getElementById("job-status");
const jobMessage = document.getElementById("job-message");
const jobDetail = document.getElementById("job-detail");
const jobMeta = document.getElementById("job-meta");
const partList = document.getElementById("part-list");
const errorBox = document.getElementById("error-box");
const generateButton = document.getElementById("generate-button");
const cancelButton = document.getElementById("cancel-button");
const downloadLink = document.getElementById("download-link");
const resetButton = document.getElementById("reset-button");
const endSessionButton = document.getElementById("end-session-button");
const pageTitle = document.getElementById("page-title");
const pageSubtitle = document.getElementById("page-subtitle");
const verificationStrip = document.getElementById("verification-strip");
const verificationTitle = document.getElementById("verification-title");
const verificationDetail = document.getElementById("verification-detail");

const verificationModalShell = document.getElementById("verification-modal-shell");
const verificationModalBackdrop = document.getElementById("verification-modal-backdrop");
const verificationForm = document.getElementById("verification-form");
const verificationEmail = document.getElementById("verification-email");
const verificationNotify = document.getElementById("verification-notify");
const verificationCancel = document.getElementById("verification-cancel");
const verificationSubmit = document.getElementById("verification-submit");
const verificationError = document.getElementById("verification-error");
const armVersionInputs = Array.from(document.querySelectorAll('input[name="arm_version"]'));

const ACTIVE_JOB_KEY = "arminator-active-job-id";
const DRAFT_KEY = "arminator-form-draft";
const REQUESTER_FIELDS = new Set(["name", "country", "purpose", "recipient_name", "recipient_sex", "recipient_age", "summary"]);
const DEFAULT_PROGRESS_VISUAL = {
  src: "/progressimages/start.jpg",
  label: "Preparing part generation",
};
const PROGRESS_VISUALS = {
  "Pins": {
    src: "/progressimages/pins.jpg",
    label: "Generating pins",
  },
  "Cuff Jig": {
    src: "/progressimages/cuffjig.jpg",
    label: "Generating cuff jig",
  },
  "Cuff": {
    src: "/progressimages/cuff.jpg",
    label: "Generating cuff",
  },
  "Forearm": {
    src: "/progressimages/forarm.jpg",
    label: "Generating forearm",
  },
  "Hand": {
    src: "/progressimages/hand.jpg",
    label: "Generating hand",
  },
};
const COUNTRY_CODES = [
  "AF", "AL", "DZ", "AD", "AO", "AG", "AR", "AM", "AU", "AT", "AZ",
  "BS", "BH", "BD", "BB", "BY", "BE", "BZ", "BJ", "BT", "BO", "BA", "BW", "BR", "BN", "BG", "BF", "BI",
  "CV", "KH", "CM", "CA", "CF", "TD", "CL", "CN", "CO", "KM", "CG", "CD", "CR", "CI", "HR", "CU", "CY", "CZ",
  "DK", "DJ", "DM", "DO",
  "EC", "EG", "SV", "GQ", "ER", "EE", "SZ", "ET",
  "FJ", "FI", "FR",
  "GA", "GM", "GE", "DE", "GH", "GR", "GD", "GT", "GN", "GW", "GY",
  "HT", "HN", "HU",
  "IS", "IN", "ID", "IR", "IQ", "IE", "IL", "IT",
  "JM", "JP", "JO",
  "KZ", "KE", "KI", "KP", "KR", "KW", "KG",
  "LA", "LV", "LB", "LS", "LR", "LY", "LI", "LT", "LU",
  "MG", "MW", "MY", "MV", "ML", "MT", "MH", "MR", "MU", "MX", "FM", "MD", "MC", "MN", "ME", "MA", "MZ", "MM",
  "NA", "NR", "NP", "NL", "NZ", "NI", "NE", "NG", "MK", "NO",
  "OM",
  "PK", "PW", "PA", "PG", "PY", "PE", "PH", "PL", "PT",
  "QA",
  "RO", "RU", "RW",
  "KN", "LC", "VC", "WS", "SM", "ST", "SA", "SN", "RS", "SC", "SL", "SG", "SK", "SI", "SB", "SO", "ZA", "SS", "ES", "LK", "SD", "SR", "SE", "CH", "SY",
  "TW", "TJ", "TZ", "TH", "TL", "TG", "TO", "TT", "TN", "TR", "TM", "TV",
  "UG", "UA", "AE", "GB", "US", "UY", "UZ",
  "VU", "VA", "VE", "VN",
  "YE",
  "ZM", "ZW",
];

let pollHandle = null;
let sessionState = {
  verified: false,
  email: "",
  notify_completed: true,
  viewer_country_code: "",
  verification_pending: false,
};
let latestConfig = null;
let currentJobState = null;

function sectionClassName(sectionName) {
  return `section-${String(sectionName || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")}`;
}

function setActiveJobId(jobId) {
  if (jobId) {
    window.localStorage.setItem(ACTIVE_JOB_KEY, jobId);
  } else {
    window.localStorage.removeItem(ACTIVE_JOB_KEY);
  }
}

function getActiveJobId() {
  return window.localStorage.getItem(ACTIVE_JOB_KEY);
}

function getSelectedArmVersion() {
  const selected = armVersionInputs.find((input) => input.checked);
  return selected ? selected.value : "";
}

function setArmVersionSelection(armVersion) {
  for (const input of armVersionInputs) {
    input.checked = input.value === armVersion;
  }
}

function optionValue(option) {
  return typeof option === "object" && option !== null ? option.value : option;
}

function optionLabel(option) {
  return typeof option === "object" && option !== null ? option.label : option;
}

function saveDraft(payload) {
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(payload));
  } catch (_error) {
    // Ignore storage failures; the server-side draft still covers verification callbacks.
  }
}

function loadDraft() {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch (_error) {
    return null;
  }
}

function clearDraft() {
  window.localStorage.removeItem(DRAFT_KEY);
}

function isTerminalState(state) {
  return ["completed", "failed", "canceled"].includes(state.status);
}

function isActiveState(state) {
  return ["queued", "starting", "running"].includes(state.status);
}

function controlIsVisible(control) {
  return !control.closest(".hidden");
}

function sectionValidity(section, report = false) {
  if (!section) {
    return true;
  }

  for (const control of section.querySelectorAll("input, select, textarea")) {
    if (control.disabled || !controlIsVisible(control)) {
      continue;
    }
    if (!control.checkValidity()) {
      if (report) {
        control.reportValidity();
      }
      return false;
    }
  }

  return true;
}

function hasActiveGeneration() {
  return Boolean(currentJobState && isActiveState(currentJobState));
}

function setPanelDisabled(panel, disabled) {
  panel.classList.toggle("is-disabled", disabled);
  panel.setAttribute("aria-disabled", disabled ? "true" : "false");
}

function setPanelControlsDisabled(panel, disabled) {
  for (const control of panel.querySelectorAll("input, select, textarea")) {
    control.disabled = disabled;
  }
}

function setIdleStatusCopy() {
  if (hasActiveGeneration() || (currentJobState && isTerminalState(currentJobState))) {
    return;
  }

  const selectedArmVersion = getSelectedArmVersion();
  if (!sessionState.verified) {
    jobStatus.textContent = sessionState.verification_pending ? "Check your email to unlock the flow" : "Waiting for verification";
    jobMessage.textContent = "Complete request details, click Lets Go !, and verify your email to unlock the generator.";
  } else if (!selectedArmVersion) {
    jobStatus.textContent = "Select a device to continue";
    jobMessage.textContent = "Choose Version2 Alfie Edition or Version 3 BETA to unlock generation.";
  } else {
    jobStatus.textContent = "Ready to generate";
    jobMessage.textContent = "Review the selected device settings, then click Generate.";
  }
}

function syncUiState() {
  const verified = Boolean(sessionState.verified);
  const selectedArmVersion = getSelectedArmVersion();
  const generationActive = hasActiveGeneration();
  const parametersEnabled = verified && !generationActive;
  const statusEnabled = generationActive || (verified && Boolean(selectedArmVersion));
  const canGenerate = statusEnabled
    && !generationActive
    && sectionValidity(requestSections)
    && sectionValidity(parameterSections);

  setPanelDisabled(parametersPanel, !parametersEnabled);
  setPanelControlsDisabled(parametersPanel, !parametersEnabled);
  setPanelDisabled(statusPanel, !statusEnabled);

  generateButton.classList.toggle("hidden", !statusEnabled || generationActive);
  generateButton.disabled = !canGenerate;
  cancelButton.classList.toggle("hidden", !generationActive);
  resetButton.disabled = generationActive;
  endSessionButton.disabled = generationActive;

  setIdleStatusCopy();
}

function showSubmissionNote(message, neutral = true) {
  if (!message) {
    submissionNote.classList.add("hidden");
    submissionNote.classList.remove("neutral");
    submissionNote.textContent = "";
    return;
  }
  submissionNote.classList.remove("hidden");
  submissionNote.classList.toggle("neutral", neutral);
  submissionNote.textContent = message;
}

function formatElapsed(startedAt, finishedAt) {
  if (!startedAt) {
    return "";
  }
  const end = finishedAt || Date.now() / 1000;
  const elapsedSeconds = Math.max(0, Math.round(end - startedAt));
  const minutes = Math.floor(elapsedSeconds / 60);
  const seconds = elapsedSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s elapsed` : `${seconds}s elapsed`;
}

function formatHeartbeat(updatedAt) {
  if (!updatedAt) {
    return "";
  }
  const ageSeconds = Math.max(0, Math.round((Date.now() / 1000) - updatedAt));
  return ageSeconds <= 1 ? "Active just now" : `Last activity ${ageSeconds}s ago`;
}

function formatWaitDuration(seconds) {
  const roundedSeconds = Math.max(0, Math.round(seconds || 0));
  if (roundedSeconds < 60) {
    return `${roundedSeconds} second${roundedSeconds === 1 ? "" : "s"}`;
  }

  const minutes = Math.round(roundedSeconds / 60);
  if (minutes < 60) {
    return `${minutes} minute${minutes === 1 ? "" : "s"}`;
  }

  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  if (!remainingMinutes) {
    return `${hours} hour${hours === 1 ? "" : "s"}`;
  }
  return `${hours} hour${hours === 1 ? "" : "s"} ${remainingMinutes} minute${remainingMinutes === 1 ? "" : "s"}`;
}

function resolveProgressVisual(state) {
  if (state.current_part && PROGRESS_VISUALS[state.current_part]) {
    return PROGRESS_VISUALS[state.current_part];
  }

  if (state.status === "completed") {
    const finalPart = state.selected_parts?.[state.selected_parts.length - 1];
    if (finalPart && PROGRESS_VISUALS[finalPart]) {
      return {
        src: PROGRESS_VISUALS[finalPart].src,
        label: "All parts generated",
      };
    }
  }

  if ((state.status === "failed" || state.status === "canceled") && state.completed_parts) {
    const completedPart = state.selected_parts?.[Math.max(0, state.completed_parts - 1)];
    if (completedPart && PROGRESS_VISUALS[completedPart]) {
      return {
        src: PROGRESS_VISUALS[completedPart].src,
        label: `${completedPart} completed before generation stopped`,
      };
    }
  }

  return DEFAULT_PROGRESS_VISUAL;
}

function setProgressVisual(state) {
  const visual = resolveProgressVisual(state);
  progressVisualImage.src = visual.src;
  progressVisualImage.alt = visual.label;
  progressVisualCaption.textContent = visual.label;
  progressVisual.classList.remove("hidden");
}

function renderPartList(state) {
  const parts = state.selected_parts || [];
  if (!parts.length) {
    partList.classList.add("hidden");
    partList.innerHTML = "";
    return;
  }

  const completedCount = state.completed_parts || 0;
  const activeIndex = state.status === "running" ? Math.max(0, (state.current_part_index || 1) - 1) : -1;
  partList.innerHTML = parts.map((part, index) => {
    let statusClass = "pending";
    let marker = "Queued";

    if (index < completedCount) {
      statusClass = "done";
      marker = "Done";
    } else if (index === activeIndex) {
      statusClass = "active";
      marker = "Generating";
    } else if (state.status === "starting" && index === completedCount) {
      statusClass = "active";
      marker = "Starting";
    } else if (state.status === "completed") {
      statusClass = "done";
      marker = "Done";
    } else if (state.status === "canceled" || state.status === "failed") {
      marker = index < completedCount ? "Done" : "Skipped";
    }

    return `<li class="part-list-item ${statusClass}"><span>${part}</span><strong>${marker}</strong></li>`;
  }).join("");
  partList.classList.remove("hidden");
}

function resetJobUi() {
  currentJobState = null;
  progressFill.style.width = "0%";
  progressTrack.classList.remove("indeterminate");
  progressValue.textContent = "0%";
  setProgressVisual({ status: "idle", progress: 0 });
  jobDetail.classList.add("hidden");
  jobDetail.textContent = "";
  jobMeta.classList.add("hidden");
  jobMeta.textContent = "";
  errorBox.classList.add("hidden");
  errorBox.textContent = "";
  partList.classList.add("hidden");
  partList.innerHTML = "";
  downloadLink.classList.add("hidden");
  downloadLink.removeAttribute("href");
  jobMessage.textContent = "Complete request details, verify your email, choose the device, and generate the parts.";
}

function clearFieldInputs(root) {
  for (const control of root.querySelectorAll("input, select, textarea")) {
    if (control.name === "arm_version") {
      continue;
    }
    if (control.type === "radio" || control.type === "checkbox") {
      control.checked = false;
    } else {
      control.value = "";
    }
  }
}

function emptyDraftPayload() {
  return {
    arm_version: "",
    requester: {},
    parameters: {},
  };
}

async function persistClearedDraft() {
  const response = await fetch("/api/session/draft", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ draft: null }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Unable to clear the saved draft.");
  }
  return data;
}

async function resetFormState(message = "Form reset. Session is still active.") {
  if (hasActiveGeneration()) {
    return;
  }

  clearDraft();
  try {
    await persistClearedDraft();
  } catch (error) {
    showSubmissionNote(error.message, false);
    return;
  }

  currentJobState = null;
  setActiveJobId(null);
  setArmVersionSelection("");
  await loadConfig("");
  clearFieldInputs(requestSections);
  clearFieldInputs(parameterSections);
  updateConditionalFields();
  resetJobUi();
  closeVerificationModal();
  saveDraft(emptyDraftPayload());
  setVerificationUi();
  showSubmissionNote(message);
}

function syncSliderValue(slider) {
  const target = document.getElementById(slider.dataset.target);
  if (!target) {
    return;
  }
  target.value = slider.value;
}

function wireSliders() {
  for (const slider of document.querySelectorAll(".range-slider")) {
    syncSliderValue(slider);
    slider.addEventListener("input", () => syncSliderValue(slider));
    slider.addEventListener("change", () => syncSliderValue(slider));
  }
}

function countryNameFromCode(code) {
  if (!code) {
    return "";
  }
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(code) || code;
  } catch (_error) {
    return code;
  }
}

function countryOptions() {
  return COUNTRY_CODES
    .map((code) => {
      const name = countryNameFromCode(code);
      return name ? { code, name } : null;
    })
    .filter(Boolean)
    .sort((left, right) => left.name.localeCompare(right.name));
}

function applyCountryDefault() {
  const countryInput = document.getElementById("country");
  if (!countryInput || countryInput.value.trim()) {
    return;
  }
  const suggested = countryNameFromCode(sessionState.viewer_country_code);
  if (suggested) {
    countryInput.value = suggested;
  }
}

function setVerificationUi() {
  verificationStrip.classList.toggle("verified", sessionState.verified);
  verificationStrip.classList.toggle("pending", !sessionState.verified && Boolean(sessionState.verification_pending));
  submitButton.classList.toggle("button-alert", !sessionState.verified);
  submitButton.classList.toggle("button-ready", sessionState.verified);
  submitButton.textContent = "Lets Go !";
  if (sessionState.verified) {
    verificationTitle.textContent = "Email verified";
    verificationDetail.textContent = sessionState.notify_completed
      ? `Verified as ${sessionState.email}. You can now select a device and completed files will also be emailed to you.`
      : `Verified as ${sessionState.email}. You can now select a device and generate parts.`;
  } else if (sessionState.verification_pending) {
    verificationTitle.textContent = "Verification link sent";
    verificationDetail.textContent = `A sign-in link was sent to ${sessionState.email}. Open it to unlock device selection and generation.`;
  } else {
    verificationTitle.textContent = "Email verification required before part generation.";
    verificationDetail.textContent = "Complete request details, click Lets Go !, then verify your email to continue.";
  }
  syncUiState();
}

function fieldIsVisible(fieldName) {
  const fieldGroup = form.querySelector(`[data-field="${fieldName}"]`);
  return !fieldGroup || !fieldGroup.classList.contains("hidden");
}

function updateConditionalFields() {
  const formData = new FormData(form);
  const values = Object.fromEntries(formData.entries());
  for (const group of form.querySelectorAll("[data-show-field]")) {
    const controllingField = group.dataset.showField;
    const allowed = (group.dataset.showValues || "").split(",").filter(Boolean);
    const visible = allowed.includes(values[controllingField] || "");
    group.classList.toggle("hidden", !visible);
    for (const input of group.querySelectorAll("input, textarea, select")) {
      input.required = visible && input.dataset.required === "true";
      if (!visible) {
        if (input.type === "radio" || input.type === "checkbox") {
          input.checked = false;
        } else {
          input.value = "";
        }
      }
    }
  }

  const summaryGroup = form.querySelector('[data-field="summary"]');
  if (summaryGroup) {
    const summaryLabel = summaryGroup.querySelector("label");
    if (summaryLabel) {
      summaryLabel.textContent = values.purpose === "project" ? "Project Summary" : "Other Summary";
    }
  }
}

function applyDraft(payload) {
  if (!payload || typeof payload !== "object") {
    return;
  }

  const requester = payload.requester && typeof payload.requester === "object" ? payload.requester : {};
  const parameters = payload.parameters && typeof payload.parameters === "object" ? payload.parameters : {};
  const fieldValues = { ...requester, ...parameters };
  if (payload.arm_version) {
    fieldValues.arm_version = payload.arm_version;
  }

  for (const [name, value] of Object.entries(fieldValues)) {
    const inputs = form.querySelectorAll(`[name="${name}"]`);
    if (!inputs.length) {
      continue;
    }

    if (inputs[0].type === "radio") {
      const hasMatchingOption = Array.from(inputs).some((input) => input.value === String(value));
      if (!hasMatchingOption) {
        continue;
      }
      for (const input of inputs) {
        input.checked = input.value === String(value);
      }
      continue;
    }

    if (inputs[0].tagName === "SELECT") {
      const hasMatchingOption = Array.from(inputs[0].options).some((option) => option.value === String(value));
      if (!hasMatchingOption) {
        continue;
      }
    }

    for (const input of inputs) {
      input.value = value ?? "";
    }

    const slider = form.querySelector(`.range-slider[data-target="${name}"]`);
    if (slider) {
      slider.value = value ?? "";
      syncSliderValue(slider);
    }
  }

  updateConditionalFields();
}

async function restoreDraft(payload) {
  if (!payload || typeof payload !== "object") {
    return;
  }

  const armVersion = String(payload.arm_version || "").trim().toLowerCase();
  if (armVersion && armVersion !== getSelectedArmVersion()) {
    setArmVersionSelection(armVersion);
    await loadConfig(armVersion);
  } else if (!armVersion && getSelectedArmVersion()) {
    setArmVersionSelection("");
    await loadConfig("");
  }

  applyDraft(payload);
}

function renderField(field) {
  const wrapper = document.createElement("div");
  wrapper.className = "field-group";
  wrapper.dataset.field = field.name;

  if (field.show_when) {
    wrapper.dataset.showField = field.show_when.field;
    wrapper.dataset.showValues = field.show_when.in.join(",");
  }

  const label = document.createElement("label");
  label.textContent = field.label;
  if (field.kind !== "radio") {
    label.setAttribute("for", field.name);
  }
  wrapper.appendChild(label);

  if (field.name === "country") {
    const select = document.createElement("select");
    select.id = field.name;
    select.name = field.name;
    select.autocomplete = "country-name";
    if (field.required) {
      select.required = true;
      select.dataset.required = "true";
    }

    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Select country";
    select.appendChild(placeholder);

    for (const option of countryOptions()) {
      const optionEl = document.createElement("option");
      optionEl.value = option.name;
      optionEl.textContent = option.name;
      select.appendChild(optionEl);
    }

    wrapper.appendChild(select);
  } else if (field.kind === "select") {
    const select = document.createElement("select");
    select.id = field.name;
    select.name = field.name;
    if (field.required) {
      select.required = true;
      select.dataset.required = "true";
    }
    for (const option of field.options) {
      const optionEl = document.createElement("option");
      optionEl.value = String(optionValue(option));
      optionEl.textContent = optionLabel(option);
      optionEl.selected = String(optionValue(option)) === String(field.default);
      select.appendChild(optionEl);
    }
    wrapper.appendChild(select);
  } else if (field.kind === "radio") {
    const optionList = document.createElement("div");
    optionList.className = "radio-list";
    for (const option of field.options) {
      const optionLabel = document.createElement("label");
      optionLabel.className = "radio-option";

      const input = document.createElement("input");
      input.type = "radio";
      input.name = field.name;
      input.value = option.value;
      input.checked = option.value === field.default;
      if (field.required) {
        input.required = true;
        input.dataset.required = "true";
      }

      const text = document.createElement("span");
      text.textContent = option.label;

      optionLabel.appendChild(input);
      optionLabel.appendChild(text);
      optionList.appendChild(optionLabel);
    }
    wrapper.appendChild(optionList);
  } else if (field.kind === "textarea") {
    const textarea = document.createElement("textarea");
    textarea.id = field.name;
    textarea.name = field.name;
    textarea.rows = 4;
    if (field.max_length) {
      textarea.maxLength = field.max_length;
    }
    wrapper.appendChild(textarea);
  } else if (field.kind === "text" || field.kind === "email" || field.kind === "number_input") {
    const input = document.createElement("input");
    input.id = field.name;
    input.name = field.name;
    input.type = field.kind === "number_input" ? "number" : field.kind;
    if (field.required) {
      input.required = true;
      input.dataset.required = "true";
    }
    if (field.autocomplete) {
      input.autocomplete = field.autocomplete;
    }
    if (field.step) {
      input.step = field.step;
    }
    if (field.min !== undefined) {
      input.min = field.min;
    }
    if (field.max !== undefined) {
      input.max = field.max;
    }
    wrapper.appendChild(input);
  } else {
    const rangeField = document.createElement("div");
    rangeField.className = "range-field";

    const output = document.createElement("input");
    output.id = field.name;
    output.name = field.name;
    output.className = "range-value";
    output.type = "number";
    output.value = field.default;
    output.step = field.step;
    output.readOnly = true;
    output.required = true;
    if (field.min !== undefined) {
      output.min = field.min;
    }
    if (field.max !== undefined) {
      output.max = field.max;
    }
    rangeField.appendChild(output);

    if (field.min !== undefined && field.max !== undefined) {
      const slider = document.createElement("input");
      slider.type = "range";
      slider.className = "range-slider";
      slider.value = field.default;
      slider.min = field.min;
      slider.max = field.max;
      slider.step = field.step;
      slider.dataset.target = field.name;
      slider.setAttribute("aria-label", field.label);
      rangeField.appendChild(slider);
    }

    wrapper.appendChild(rangeField);
  }

  if (field.note) {
    const note = document.createElement("p");
    note.className = "note";
    note.textContent = field.note;
    wrapper.appendChild(note);
  }

  return wrapper;
}

function renderForm(config) {
  latestConfig = config;

  requestSections.innerHTML = "";
  parameterSections.innerHTML = "";
  if (config.selected_arm_version) {
    parameterSections.dataset.armVersion = config.selected_arm_version;
  } else {
    delete parameterSections.dataset.armVersion;
  }
  for (const section of config.sections) {
    const fieldset = document.createElement("fieldset");
    fieldset.className = `section-card ${sectionClassName(section.name)}`;

    const legend = document.createElement("legend");
    legend.textContent = section.name;
    fieldset.appendChild(legend);

    for (const field of section.fields) {
      fieldset.appendChild(renderField(field));
    }
    if (section.name === "Request Details") {
      requestSections.appendChild(fieldset);
    } else {
      parameterSections.appendChild(fieldset);
    }
  }

  if (!config.selected_arm_version) {
    const emptyState = document.createElement("p");
    emptyState.className = "panel-copy measurement-empty-state";
    emptyState.textContent = "Choose Version2 Alfie Edition or Version 3 BETA above to load the correct measurements.";
    parameterSections.appendChild(emptyState);
  }

  wireSliders();
  updateConditionalFields();
  applyCountryDefault();
  syncUiState();
}

function setJobUi(state) {
  currentJobState = state;
  setProgressVisual(state);
  const hasActivePart = state.status === "running" && state.current_part;
  const showIndeterminate = state.status === "starting" || state.status === "queued" || Boolean(hasActivePart);
  progressFill.style.width = showIndeterminate ? "" : `${state.progress || 0}%`;
  progressValue.textContent = state.status === "running"
    ? `${state.completed_parts || 0}/${state.total_parts || 0} complete`
    : `${state.progress || 0}%`;
  jobStatus.textContent = state.status.charAt(0).toUpperCase() + state.status.slice(1);
  if (state.status === "queued") {
    const queueParts = [];
    if (state.queue_position) {
      queueParts.push(`You are ${state.queue_position} in the queue`);
    }
    if (state.estimated_wait_seconds) {
      queueParts.push(`Estimated wait time is ${formatWaitDuration(state.estimated_wait_seconds)}`);
    }
    jobMessage.textContent = queueParts.length
      ? `${queueParts.join(". ")}.`
      : "Now Spinning Up Part Generation Engine, Hold on to your Filament";
  } else if (isActiveState(state)) {
    jobMessage.textContent = "Now Spinning Up Part Generation Engine, Hold on to your Filament";
  } else {
    jobMessage.textContent = state.message || "";
  }
  progressTrack.classList.toggle("indeterminate", showIndeterminate);

  if (isActiveState(state)) {
    jobDetail.classList.add("hidden");
    jobDetail.textContent = "";
  } else if (state.total_parts) {
    jobDetail.textContent = `${state.completed_parts || 0} of ${state.total_parts} parts completed.`;
    jobDetail.classList.remove("hidden");
  } else {
    jobDetail.classList.add("hidden");
    jobDetail.textContent = "";
  }

  const metaParts = [];
  const elapsed = formatElapsed(state.started_at, state.finished_at);
  if (elapsed) {
    metaParts.push(elapsed);
  }
  if (state.status === "queued") {
    if (state.status_line) {
      metaParts.push(state.status_line);
    }
  } else if (isActiveState(state)) {
    const heartbeat = formatHeartbeat(state.updated_at);
    if (heartbeat) {
      metaParts.push(heartbeat);
    }
  } else {
    if (state.status_line) {
      metaParts.push(state.status_line);
    }
  }
  if (metaParts.length) {
    jobMeta.textContent = metaParts.join(" • ");
    jobMeta.classList.remove("hidden");
  } else {
    jobMeta.classList.add("hidden");
    jobMeta.textContent = "";
  }

  if (state.error) {
    errorBox.textContent = state.error;
    errorBox.classList.remove("hidden");
  } else {
    errorBox.classList.add("hidden");
    errorBox.textContent = "";
  }

  if (state.download_url) {
    downloadLink.href = state.download_url;
    downloadLink.classList.remove("hidden");
  } else {
    downloadLink.classList.add("hidden");
    downloadLink.removeAttribute("href");
  }

  renderPartList(state);

  if (isActiveState(state)) {
    setActiveJobId(state.job_id);
    showSubmissionNote("Part generation is already active for this browser. Reloading the page will reconnect to it.");
  } else {
    if (isTerminalState(state)) {
      setActiveJobId(null);
      if (state.cached) {
        showSubmissionNote("This ZIP was served from a previously completed matching part-generation request.");
      } else if (state.status === "completed") {
        showSubmissionNote("Part generation finished. You can submit another configuration.");
      } else {
        showSubmissionNote("");
      }
    }
  }

  syncUiState();
}

function collectPayload() {
  const formData = new FormData(form);
  const payload = {
    arm_version: "",
    requester: {},
    parameters: {},
  };

  for (const [name, value] of formData.entries()) {
    if (name === "arm_version") {
      payload.arm_version = value;
      continue;
    }
    if (!fieldIsVisible(name)) {
      continue;
    }
    if (REQUESTER_FIELDS.has(name)) {
      payload.requester[name] = value;
    } else {
      payload.parameters[name] = value;
    }
  }

  return payload;
}

function openVerificationModal() {
  verificationError.classList.add("hidden");
  verificationError.textContent = "";
  verificationNotify.checked = sessionState.notify_completed ?? true;
  if (sessionState.email) {
    verificationEmail.value = sessionState.email;
  }
  verificationModalShell.classList.remove("hidden");
  verificationEmail.focus();
}

function closeVerificationModal() {
  verificationModalShell.classList.add("hidden");
}

async function pollJob(jobId) {
  try {
    const response = await fetch(`/api/jobs/${jobId}`);
    const state = await response.json();
    setJobUi(state);

    if (isTerminalState(state) && pollHandle) {
      window.clearInterval(pollHandle);
      pollHandle = null;
    }
  } catch (error) {
    if (pollHandle) {
      window.clearInterval(pollHandle);
      pollHandle = null;
    }
    setJobUi({
      status: "failed",
      progress: 100,
      message: "Unable to refresh job status.",
      error: error.message,
      completed_parts: 0,
      total_parts: 0,
      output_files: [],
    });
  }
}

function startPolling(jobId) {
  setActiveJobId(jobId);
  if (pollHandle) {
    window.clearInterval(pollHandle);
  }
  pollHandle = window.setInterval(() => pollJob(jobId), 1000);
  pollJob(jobId);
}

async function loadConfig(armVersion = getSelectedArmVersion()) {
  const suffix = armVersion ? `?arm_version=${encodeURIComponent(armVersion)}` : "";
  const response = await fetch(`/api/config${suffix}`);
  if (!response.ok) {
    throw new Error("Unable to load form configuration.");
  }
  const config = await response.json();
  renderForm(config);
}

async function loadSessionState() {
  const response = await fetch("/api/session");
  if (!response.ok) {
    throw new Error("Unable to load verification status.");
  }
  sessionState = await response.json();
  setVerificationUi();
  applyCountryDefault();
  if (sessionState.draft) {
    await restoreDraft(sessionState.draft);
    saveDraft(sessionState.draft);
  }
}

async function submitJob(payload) {
  downloadLink.classList.add("hidden");
  errorBox.classList.add("hidden");
  errorBox.textContent = "";
  saveDraft(payload);

  setJobUi({
    job_id: getActiveJobId() || "",
    status: "queued",
    progress: 0,
    message: "Submitting part generation job.",
    completed_parts: 0,
    total_parts: 0,
    output_files: [],
    status_line: "Sending the request to the server.",
  });

  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const data = await response.json();
    if (!response.ok) {
      if (response.status === 403) {
        sessionState.verified = false;
        sessionState.verification_pending = false;
        setVerificationUi();
        openVerificationModal();
        showSubmissionNote("Verify your email before part generation can start.");
        return;
      }

      if (response.status === 409 && data.job_id) {
        showSubmissionNote("Part generation is already active for this browser. Reconnected to the active job.");
        startPolling(data.job_id);
        return;
      }

      if (response.status === 429) {
        setActiveJobId(null);
        showSubmissionNote(data.error || "I'm really busy come back in a bit !", false);
        setJobUi({
          status: "failed",
          progress: 100,
          message: data.error || "I'm really busy come back in a bit !",
          completed_parts: 0,
          total_parts: 0,
          output_files: [],
        });
        return;
      }

      setJobUi({
        status: "failed",
        progress: 100,
        message: "The part generation request was rejected.",
        error: (data.errors || [data.error || "Unknown error"]).join(" "),
        completed_parts: 0,
        total_parts: 0,
        output_files: [],
      });
      return;
    }

    setJobUi(data);
    if (data.requester || data.parameters || data.arm_version) {
      const draftPayload = {
        arm_version: data.arm_version || getSelectedArmVersion(),
        requester: data.requester || {},
        parameters: data.parameters || {},
      };
      await restoreDraft(draftPayload);
      saveDraft(draftPayload);
    }
    if (isActiveState(data)) {
      startPolling(data.job_id);
    } else {
      setActiveJobId(null);
    }
  } catch (error) {
    setJobUi({
      status: "failed",
      progress: 100,
      message: "The part generation request could not be submitted.",
      error: error.message,
      completed_parts: 0,
      total_parts: 0,
      output_files: [],
    });
  }
}

async function handleVerificationCallback() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("verify");
  if (!token) {
    return;
  }

  try {
    const response = await fetch("/api/verify", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ token }),
    });
    const data = await response.json();
    if (!response.ok) {
      showSubmissionNote(data.error || "The verification link could not be confirmed.", false);
    } else {
      sessionState = data;
      setVerificationUi();
      if (data.draft) {
        await restoreDraft(data.draft);
        saveDraft(data.draft);
      }
      showSubmissionNote(data.message || "Email verified. You can now select a device and generate the parts.");
    }
  } catch (error) {
    showSubmissionNote(error.message, false);
  } finally {
    params.delete("verify");
    const nextUrl = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ""}`;
    window.history.replaceState({}, "", nextUrl);
  }
}

form.addEventListener("input", (event) => {
  if (event.target instanceof HTMLElement && event.target.closest(".section-card")) {
    updateConditionalFields();
    saveDraft(collectPayload());
    syncUiState();
  }
});

form.addEventListener("change", (event) => {
  if (event.target instanceof HTMLElement && event.target.closest(".section-card")) {
    updateConditionalFields();
    saveDraft(collectPayload());
    syncUiState();
  }
});

for (const input of armVersionInputs) {
  input.addEventListener("change", async () => {
    const preservedDraft = collectPayload();
    try {
      await loadConfig(getSelectedArmVersion());
      applyDraft(preservedDraft);
      saveDraft(collectPayload());
      showSubmissionNote("");
      syncUiState();
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.classList.remove("hidden");
    }
  });
}

submitButton.addEventListener("click", () => {
  if (!sectionValidity(requestSections, true)) {
    return;
  }

  saveDraft(collectPayload());
  if (sessionState.verified) {
    showSubmissionNote("Email already verified. Select a device and generate when you are ready.");
    syncUiState();
    return;
  }

  openVerificationModal();
});

generateButton.addEventListener("click", async () => {
  if (!sessionState.verified) {
    openVerificationModal();
    return;
  }
  if (!sectionValidity(requestSections, true) || !sectionValidity(parameterSections, true)) {
    syncUiState();
    return;
  }

  await submitJob(collectPayload());
});

verificationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  verificationError.classList.add("hidden");
  verificationError.textContent = "";
  verificationSubmit.disabled = true;

  try {
    const response = await fetch("/api/verification-links", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        email: verificationEmail.value.trim(),
        notify_completed: verificationNotify.checked,
        draft: collectPayload(),
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      verificationError.textContent = data.error || "Could not send the verification email.";
      verificationError.classList.remove("hidden");
      return;
    }

    sessionState = {
      ...sessionState,
      email: verificationEmail.value.trim(),
      notify_completed: verificationNotify.checked,
      verification_pending: true,
    };
    setVerificationUi();
    closeVerificationModal();
    showSubmissionNote(data.message || "Verification link sent. Open the email, click the link, then come back here to select the device and generate.");
  } catch (error) {
    verificationError.textContent = error.message;
    verificationError.classList.remove("hidden");
  } finally {
    verificationSubmit.disabled = false;
  }
});

verificationCancel.addEventListener("click", closeVerificationModal);
verificationModalBackdrop.addEventListener("click", closeVerificationModal);

endSessionButton.addEventListener("click", async () => {
  if (hasActiveGeneration()) {
    return;
  }

  endSessionButton.disabled = true;
  try {
    const response = await fetch("/api/session/end", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({}),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Unable to end the current session.");
    }

    sessionState = {
      ...data,
      verification_pending: false,
      email: "",
    };
    currentJobState = null;
    setActiveJobId(null);
    setArmVersionSelection("");
    await loadConfig("");
    resetJobUi();
    closeVerificationModal();
    setVerificationUi();
    showSubmissionNote(data.message || "Session ended. Verify by magic link again to continue.");
  } catch (error) {
    showSubmissionNote(error.message, false);
  } finally {
    syncUiState();
  }
});

resetButton.addEventListener("click", async () => {
  resetButton.disabled = true;
  try {
    await resetFormState(sessionState.verified ? "Form reset. Session is still active." : "Form reset.");
  } finally {
    syncUiState();
  }
});

cancelButton.addEventListener("click", async () => {
  const jobId = getActiveJobId();
  if (!jobId) {
    return;
  }

  cancelButton.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${jobId}/cancel`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({}),
    });
    const state = await response.json();
    setJobUi(state);
    if (isActiveState(state)) {
      startPolling(jobId);
    }
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  } finally {
    cancelButton.disabled = false;
  }
});

window.addEventListener("load", async () => {
  try {
    await loadConfig();
    const localDraft = loadDraft();
    if (localDraft) {
      await restoreDraft(localDraft);
    }
    await loadSessionState();
    await handleVerificationCallback();
  } catch (error) {
    setJobUi({
      status: "failed",
      progress: 100,
      message: "Unable to load the generator.",
      error: error.message,
      completed_parts: 0,
      total_parts: 0,
      output_files: [],
    });
    return;
  }

  syncUiState();

  const jobId = getActiveJobId();
  if (!jobId) {
    return;
  }

  try {
    const response = await fetch(`/api/jobs/${jobId}`);
    if (!response.ok) {
      setActiveJobId(null);
      return;
    }
    const state = await response.json();
    setJobUi(state);
    if (state.requester || state.parameters || state.arm_version) {
      const draftPayload = {
        arm_version: state.arm_version || getSelectedArmVersion(),
        requester: state.requester || {},
        parameters: state.parameters || {},
      };
      await restoreDraft(draftPayload);
      saveDraft(draftPayload);
    }
    if (isActiveState(state)) {
      showSubmissionNote("Reconnected to your active part generation job.");
      startPolling(jobId);
    } else {
      setActiveJobId(null);
    }
  } catch (_error) {
    setActiveJobId(null);
  }
});
