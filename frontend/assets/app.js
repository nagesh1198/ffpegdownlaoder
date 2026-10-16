const form = document.querySelector("#download-form");
const urlInput = document.querySelector("#media-url");
const formatSelect = document.querySelector("#output-format");
const submitButton = document.querySelector("#submit-button");
const submitLabel = document.querySelector("#submit-label");
const jobPanel = document.querySelector("#job-panel");
const jobTitle = document.querySelector("#job-title");
const jobMessage = document.querySelector("#job-message");
const jobPercent = document.querySelector("#job-percent");
const jobSpinner = document.querySelector("#job-spinner");
const progressTrack = document.querySelector("#progress-track");
const progressFill = document.querySelector("#progress-fill");
const downloadLink = document.querySelector("#download-link");
const retryButton = document.querySelector("#retry-button");
const apiIndicator = document.querySelector("#api-indicator");
const apiStatus = document.querySelector("#api-status");
const formatHint = document.querySelector("#format-hint");
const formatIcon = document.querySelector("#format-icon");
const pasteButton = document.querySelector("#paste-button");

const formatDetails = {
  mp3: {
    hint: "192 kbps · compact",
    icon: '<svg viewBox="0 0 20 20" fill="none"><path d="M8 4v8.4a2.5 2.5 0 1 1-1.5-2.3V6l8-2v8.4a2.5 2.5 0 1 1-1.5-2.3V2L8 4Z" /></svg>',
  },
  mp4: {
    hint: "H.264 video · AAC audio",
    icon: '<svg viewBox="0 0 20 20" fill="none"><rect x="2.5" y="4" width="15" height="12" rx="2" /><path d="m8 7 5 3-5 3V7Z" /></svg>',
  },
};

function setApiStatus(state, text) {
  apiIndicator.classList.remove("is-online", "is-offline");
  if (state === "online") apiIndicator.classList.add("is-online");
  if (state === "offline") apiIndicator.classList.add("is-offline");
  apiStatus.textContent = text;
}

async function checkApi() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error("The server returned an error.");
    const health = await response.json();
    if (!health.ffmpeg_available) {
      setApiStatus("offline", "FFmpeg not installed");
      return;
    }
    setApiStatus("online", "Server ready");
  } catch {
    setApiStatus("offline", "Server unavailable");
  }
}

function setProgress(percent, indeterminate = false) {
  progressTrack.classList.toggle("is-indeterminate", indeterminate);
  if (percent === null) {
    jobPercent.textContent = "";
    progressFill.style.width = "35%";
  } else {
    jobPercent.textContent = `${percent}%`;
    progressFill.style.width = `${percent}%`;
  }
}

function showError(message) {
  jobTitle.textContent = "That download didn't work";
  jobMessage.textContent = message;
  jobMessage.classList.add("is-error");
  jobSpinner.classList.add("is-error");
  jobSpinner.classList.remove("is-done");
  progressTrack.hidden = true;
  jobPercent.textContent = "";
  retryButton.hidden = false;
}

function readableDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return minutes ? `${minutes} min ${remainder} sec` : `${remainder} sec`;
}

function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function readError(response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // The response may not contain JSON; use the generic message below.
  }
  return `The server returned an error (${response.status}).`;
}

async function monitorJob(jobId, format) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < 60 * 60 * 1000) {
    await delay(900);
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (!response.ok) throw new Error(await readError(response));

    const job = await response.json();
    if (job.status === "failed") throw new Error(job.error || "FFmpeg could not process this link.");
    if (job.status === "complete") {
      setProgress(100);
      jobSpinner.classList.add("is-done");
      jobTitle.textContent = "Your file is ready";
      const duration = readableDuration(job.duration);
      jobMessage.classList.remove("is-error");
      jobMessage.textContent = duration
        ? `${format.toUpperCase()} · ${duration}`
        : `${format.toUpperCase()} · Ready to save`;
      downloadLink.href = `/api/jobs/${encodeURIComponent(jobId)}/download`;
      downloadLink.download = `download.${format}`;
      downloadLink.hidden = false;
      return;
    }

    const percent = Number.isInteger(job.percent) ? job.percent : null;
    if (percent === null || percent === 0) {
      setProgress(null, true);
      jobTitle.textContent = "Connecting to your media";
      jobMessage.textContent = "This can take a moment for larger links.";
    } else {
      setProgress(percent);
      jobTitle.textContent = "Making your download";
      jobMessage.textContent = "You can leave this tab open while it finishes.";
    }
  }
  throw new Error("This is taking longer than expected. Please try again in a moment.");
}

formatSelect.addEventListener("change", () => {
  const selected = formatDetails[formatSelect.value];
  formatHint.textContent = selected.hint;
  formatIcon.innerHTML = selected.icon;
});

pasteButton.addEventListener("click", async () => {
  try {
    urlInput.value = await navigator.clipboard.readText();
    urlInput.focus();
  } catch (error) {
    pasteButton.textContent = "Use Ctrl+V";
    pasteButton.title = error instanceof Error ? error.message : "Clipboard access is unavailable.";
    window.setTimeout(() => {
      pasteButton.textContent = "Paste";
      pasteButton.removeAttribute("title");
    }, 2500);
  }
});

retryButton.addEventListener("click", () => {
  jobPanel.hidden = true;
  urlInput.focus();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;

  const format = formatSelect.value;
  submitButton.disabled = true;
  submitLabel.textContent = "Starting your download…";
  jobPanel.hidden = false;
  jobMessage.classList.remove("is-error");
  jobSpinner.classList.remove("is-done", "is-error");
  jobTitle.textContent = "Starting your download";
  jobMessage.textContent = "Connecting to the server.";
  jobPercent.textContent = "";
  progressFill.style.width = "0%";
  progressTrack.hidden = false;
  progressTrack.classList.remove("is-indeterminate");
  downloadLink.hidden = true;
  retryButton.hidden = true;

  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: urlInput.value.trim(), format }),
    });
    if (!response.ok) throw new Error(await readError(response));
    const { job_id: jobId } = await response.json();
    await monitorJob(jobId, format);
  } catch (error) {
    showError(error instanceof Error ? error.message : "An unexpected error occurred.");
  } finally {
    submitButton.disabled = false;
    submitLabel.textContent = "Make it a download";
  }
});

checkApi();
