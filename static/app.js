const form = document.getElementById("generator-form");
const submitButton = document.getElementById("submit-button");
const submissionNote = document.getElementById("submission-note");
const jobPanel = document.getElementById("job-panel");
const progressTrack = document.getElementById("progress-track");
const progressFill = document.getElementById("progress-fill");
const progressValue = document.getElementById("progress-value");
const jobStatus = document.getElementById("job-status");
const jobMessage = document.getElementById("job-message");
const jobDetail = document.getElementById("job-detail");
const jobMeta = document.getElementById("job-meta");
const outputList = document.getElementById("output-list");
const errorBox = document.getElementById("error-box");
const cancelButton = document.getElementById("cancel-button");
const downloadLink = document.getElementById("download-link");
const sliders = Array.from(document.querySelectorAll(".range-slider"));

const CLIENT_ID_KEY = "arminator-client-id";
const ACTIVE_JOB_KEY = "arminator-active-job-id";

let pollHandle = null;

function getClientId() {
  let clientId = window.localStorage.getItem(CLIENT_ID_KEY);
  if (!clientId) {
    clientId = window.crypto?.randomUUID?.() || `client-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    window.localStorage.setItem(CLIENT_ID_KEY, clientId);
  }
  return clientId;
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

function isTerminalState(state) {
  return ["completed", "failed", "canceled"].includes(state.status);
}

function isActiveState(state) {
  return ["queued", "starting", "running"].includes(state.status);
}

function setFormLocked(locked) {
  for (const element of form.elements) {
    element.disabled = locked;
  }
  cancelButton.classList.toggle("hidden", !locked);
  cancelButton.disabled = false;
}

function syncSliderValue(slider) {
  const target = document.getElementById(slider.dataset.target);
  if (!target) {
    return;
  }
  target.value = slider.value;
}

function initializeSliders() {
  for (const slider of sliders) {
    syncSliderValue(slider);
    slider.addEventListener("input", () => syncSliderValue(slider));
    slider.addEventListener("change", () => syncSliderValue(slider));
  }
}

function showSubmissionNote(message) {
  if (!message) {
    submissionNote.classList.add("hidden");
    submissionNote.textContent = "";
    return;
  }
  submissionNote.classList.remove("hidden");
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

function renderOutputList(files) {
  if (files?.length) {
    outputList.innerHTML = files.map((file) => `<li>${file}</li>`).join("");
    outputList.classList.remove("hidden");
  } else {
    outputList.classList.add("hidden");
    outputList.innerHTML = "";
  }
}

function setJobUi(state) {
  jobPanel.classList.remove("hidden");
  progressFill.style.width = `${state.progress || 0}%`;
  progressValue.textContent = state.status === "running"
    ? `${state.completed_parts || 0}/${state.total_parts || 0} complete`
    : `${state.progress || 0}%`;
  jobStatus.textContent = state.status.charAt(0).toUpperCase() + state.status.slice(1);
  jobMessage.textContent = state.message || "";

  const hasActivePart = state.status === "running" && state.current_part;
  progressTrack.classList.toggle("indeterminate", Boolean(hasActivePart));

  if (state.total_parts) {
    const currentIndex = state.current_part_index || state.completed_parts || 0;
    const detail = hasActivePart
      ? `Currently rendering ${state.current_part} (${currentIndex}/${state.total_parts}). OpenSCAD does not expose in-part percentage progress.`
      : `${state.completed_parts || 0} of ${state.total_parts} parts completed.`;
    jobDetail.textContent = detail;
    jobDetail.classList.remove("hidden");
  } else {
    jobDetail.classList.add("hidden");
    jobDetail.textContent = "";
  }

  const metaParts = [];
  if (state.status === "queued" && state.queue_position) {
    metaParts.push(`Queue position ${state.queue_position}`);
  }
  if (state.status_line) {
    metaParts.push(state.status_line);
  }
  const elapsed = formatElapsed(state.started_at, state.finished_at);
  if (elapsed) {
    metaParts.push(elapsed);
  }
  if (state.status === "running") {
    const heartbeat = formatHeartbeat(state.updated_at);
    if (heartbeat) {
      metaParts.push(heartbeat);
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

  renderOutputList(state.output_files);

  if (isActiveState(state)) {
    setActiveJobId(state.job_id);
    setFormLocked(true);
    showSubmissionNote("A render is already active for this browser. Reloading the page will reconnect to it.");
  } else {
    setFormLocked(false);
    if (isTerminalState(state)) {
      setActiveJobId(null);
      if (state.cached) {
        showSubmissionNote("This ZIP was served from a previously completed matching render.");
      } else if (state.status === "completed") {
        showSubmissionNote("Render finished. You can submit another configuration.");
      } else {
        showSubmissionNote("");
      }
    }
  }
}

function collectPayload() {
  const formData = new FormData(form);
  const payload = {
    client_id: getClientId(),
    parameters: {},
    parts: [],
  };

  for (const [name, value] of formData.entries()) {
    if (name === "parts") {
      payload.parts.push(value);
      continue;
    }
    payload.parameters[name] = value;
  }

  return payload;
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
    setFormLocked(false);
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

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = collectPayload();
  downloadLink.classList.add("hidden");
  errorBox.classList.add("hidden");
  errorBox.textContent = "";

  setJobUi({
    job_id: getActiveJobId() || "",
    status: "queued",
    progress: 0,
    message: "Submitting render job.",
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
      if (response.status === 409 && data.job_id) {
        showSubmissionNote("A render is already active for this browser. Reconnected to the active job.");
        startPolling(data.job_id);
        return;
      }

      setFormLocked(false);
      setJobUi({
        status: "failed",
        progress: 100,
        message: "The render request was rejected.",
        error: (data.errors || [data.error || "Unknown error"]).join(" "),
        completed_parts: 0,
        total_parts: 0,
        output_files: [],
      });
      return;
    }

    setJobUi(data);
    if (isActiveState(data)) {
      startPolling(data.job_id);
    } else {
      setActiveJobId(null);
    }
  } catch (error) {
    setFormLocked(false);
    setJobUi({
      status: "failed",
      progress: 100,
      message: "The render request could not be submitted.",
      error: error.message,
      completed_parts: 0,
      total_parts: 0,
      output_files: [],
    });
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
      body: JSON.stringify({ client_id: getClientId() }),
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
  initializeSliders();
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
    if (isActiveState(state)) {
      showSubmissionNote("Reconnected to your active render job.");
      startPolling(jobId);
    } else {
      setActiveJobId(null);
    }
  } catch (_error) {
    setActiveJobId(null);
  }
});
