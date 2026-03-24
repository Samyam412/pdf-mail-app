const state = {
  root: "",
  jobs: new Map(),
  jobPollers: new Map(),
  previewTarget: { type: "permit" },
  rectangleCounter: 0,
  previewDrag: null,
};

const elements = {
  rootPath: document.getElementById("rootPath"),
  modeSelect: document.getElementById("modeSelect"),
  sourceLabel: document.getElementById("sourceLabel"),
  sourcePath: document.getElementById("sourcePath"),
  sourceSummary: document.getElementById("sourceSummary"),
  pickSourceButton: document.getElementById("pickSourceButton"),
  permitText: document.getElementById("permitText"),
  boxX: document.getElementById("boxX"),
  boxTopY: document.getElementById("boxTopY"),
  usePermitClickButton: document.getElementById("usePermitClickButton"),
  insertBlanks: document.getElementById("insertBlanks"),
  blankIntervalGroup: document.getElementById("blankIntervalGroup"),
  blankInterval: document.getElementById("blankInterval"),
  singlePdfStepGroup: document.getElementById("singlePdfStepGroup"),
  singlePdfStep: document.getElementById("singlePdfStep"),
  outputPath: document.getElementById("outputPath"),
  outputSummary: document.getElementById("outputSummary"),
  pickOutputButton: document.getElementById("pickOutputButton"),
  suggestOutputButton: document.getElementById("suggestOutputButton"),
  addRectangleButton: document.getElementById("addRectangleButton"),
  rectanglesList: document.getElementById("rectanglesList"),
  rectangleTemplate: document.getElementById("rectangleTemplate"),
  previewPanel: document.getElementById("previewPanel"),
  previewFrame: document.querySelector(".preview-frame"),
  previewImage: document.getElementById("previewImage"),
  previewOverlay: document.getElementById("previewOverlay"),
  previewStatus: document.getElementById("previewStatus"),
  previewTargetLabel: document.getElementById("previewTargetLabel"),
  jobForm: document.getElementById("jobForm"),
  runButton: document.getElementById("runButton"),
  runStatus: document.getElementById("runStatus"),
  jobs: document.getElementById("jobs"),
  jobTemplate: document.getElementById("jobTemplate"),
  stepSource: document.getElementById("stepSource"),
  stepOutput: document.getElementById("stepOutput"),
  stepOptions: document.getElementById("stepOptions"),
  stepRun: document.getElementById("stepRun"),
};

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Request failed");
  }
  return payload;
}

function currentMode() {
  return elements.modeSelect.value;
}

function hasSource() {
  return Boolean(elements.sourcePath.value.trim());
}

function hasOutput() {
  return Boolean(elements.outputPath.value.trim());
}

function setRunStatus(text, isError = false) {
  elements.runStatus.textContent = text;
  elements.runStatus.dataset.state = isError ? "error" : "default";
}

function pathPlaceholder() {
  return currentMode() === "folder" ? "/path/to/folder" : "/path/to/file.pdf";
}

function summarizePath(path, emptyText) {
  return path ? path : emptyText;
}

function setSectionLocked(section, locked) {
  section.classList.toggle("is-locked", locked);
  const fields = section.querySelectorAll("input, select, textarea, button");
  fields.forEach((field) => {
    if (field.dataset.alwaysEnabled === "true") {
      return;
    }
    field.disabled = locked;
  });
}

function refreshPathSummaries() {
  elements.sourceSummary.textContent = summarizePath(
    elements.sourcePath.value.trim(),
    "Nothing selected yet."
  );
  elements.outputSummary.textContent = summarizePath(
    elements.outputPath.value.trim(),
    "No output selected yet."
  );
}

function previewTargetText() {
  if (state.previewTarget.type === "permit") {
    return "Target: Permit box";
  }
  const row = document.querySelector(`.rectangle-row[data-id="${state.previewTarget.id}"]`);
  if (!row) {
    return "Target: Permit box";
  }
  const index = Array.from(elements.rectanglesList.children).indexOf(row) + 1;
  return `Target: Rectangle ${index}`;
}

function setPreviewTarget(target) {
  state.previewTarget = target;
  elements.previewTargetLabel.textContent = previewTargetText();
  elements.previewStatus.textContent = target.type === "permit"
    ? "Click the preview to set the permit box top-left coordinate."
    : "Drag on the preview to size and place the whiteout rectangle.";
  document.querySelectorAll(".rectangle-row").forEach((row) => {
    row.classList.toggle(
      "is-targeted",
      target.type === "rectangle" && row.dataset.id === target.id
    );
  });
  elements.usePermitClickButton.classList.toggle(
    "is-active",
    target.type === "permit"
  );
  renderPreviewOverlays();
}

function refreshModeFields() {
  const folderMode = currentMode() === "folder";
  elements.sourceLabel.textContent = folderMode ? "Source folder" : "Source PDF";
  elements.sourcePath.placeholder = pathPlaceholder();
  elements.singlePdfStepGroup.hidden = folderMode;
  elements.pickSourceButton.textContent = folderMode ? "Choose Folder" : "Choose PDF";
}

function refreshBlankFields() {
  elements.blankIntervalGroup.hidden = !elements.insertBlanks.checked;
}

function refreshWorkflowState() {
  const sourceReady = hasSource();
  const outputReady = hasOutput();
  setSectionLocked(elements.stepOutput, !sourceReady);
  setSectionLocked(elements.stepOptions, !sourceReady);
  setSectionLocked(elements.stepRun, !(sourceReady && outputReady));
  elements.modeSelect.disabled = false;
  elements.pickSourceButton.disabled = false;
  elements.sourcePath.disabled = false;
  elements.runButton.disabled = !(sourceReady && outputReady);
  refreshPathSummaries();
}

async function suggestOutputPath() {
  const source = elements.sourcePath.value.trim();
  if (!source) {
    elements.outputPath.value = "";
    refreshWorkflowState();
    return;
  }
  try {
    const payload = await fetchJson(
      `/api/suggest-output?mode=${encodeURIComponent(currentMode())}&source=${encodeURIComponent(source)}`
    );
    elements.outputPath.value = payload.output_path;
    refreshWorkflowState();
  } catch (error) {
    setRunStatus(error.message, true);
  }
}

async function loadPreview() {
  const source = elements.sourcePath.value.trim();
  if (!source) {
    elements.previewPanel.hidden = true;
    elements.previewImage.removeAttribute("src");
    return;
  }

  elements.previewPanel.hidden = false;
  elements.previewStatus.textContent = "Loading first-page preview...";
  const previewUrl =
    `/api/preview-first-page?mode=${encodeURIComponent(currentMode())}` +
    `&source=${encodeURIComponent(source)}` +
    `&v=${Date.now()}`;
  elements.previewImage.src = previewUrl;
}

async function pickSourcePath() {
  try {
    const endpoint = currentMode() === "folder"
      ? "/api/pick-source-folder"
      : "/api/pick-source-pdf";
    const initialPath = elements.sourcePath.value.trim();
    const payload = await fetchJson(
      `${endpoint}?initial_path=${encodeURIComponent(initialPath)}`
    );
    elements.sourcePath.value = payload.path;
    await suggestOutputPath();
    await loadPreview();
    refreshWorkflowState();
  } catch (error) {
    if (error.message !== "Picker cancelled") {
      setRunStatus(error.message, true);
    }
  }
}

async function pickOutputPath() {
  try {
    const suggestedPath = elements.outputPath.value.trim();
    const payload = await fetchJson(
      `/api/pick-output-pdf?suggested_path=${encodeURIComponent(suggestedPath)}`
    );
    elements.outputPath.value = payload.path;
    refreshWorkflowState();
  } catch (error) {
    if (error.message !== "Picker cancelled") {
      setRunStatus(error.message, true);
    }
  }
}

function createRectangleRow(rectangle = {}) {
  const fragment = elements.rectangleTemplate.content.cloneNode(true);
  const row = fragment.querySelector(".rectangle-row");
  const rowId = `rect-${state.rectangleCounter += 1}`;
  row.dataset.id = rowId;

  row.querySelector(".rectangle-x").value = rectangle.x ?? "";
  row.querySelector(".rectangle-top-y").value = rectangle.top_y ?? "";
  row.querySelector(".rectangle-width").value = rectangle.width ?? "";
  row.querySelector(".rectangle-height").value = rectangle.height ?? "";

  row.querySelector(".rectangle-target").addEventListener("click", () => {
    setPreviewTarget({ type: "rectangle", id: rowId });
  });

  row.querySelector(".rectangle-remove").addEventListener("click", () => {
    const wasTargeted =
      state.previewTarget.type === "rectangle" && state.previewTarget.id === rowId;
    row.remove();
    if (wasTargeted) {
      setPreviewTarget({ type: "permit" });
    }
    renderPreviewOverlays();
  });

  row.querySelectorAll("input").forEach((input) => {
    input.addEventListener("input", renderPreviewOverlays);
  });

  elements.rectanglesList.append(row);
  renderPreviewOverlays();
  return row;
}

function collectRectangles() {
  return Array.from(elements.rectanglesList.querySelectorAll(".rectangle-row"))
    .map((row) => ({
      x: row.querySelector(".rectangle-x").value.trim(),
      top_y: row.querySelector(".rectangle-top-y").value.trim(),
      width: row.querySelector(".rectangle-width").value.trim(),
      height: row.querySelector(".rectangle-height").value.trim(),
    }))
    .filter((rectangle) =>
      rectangle.x || rectangle.top_y || rectangle.width || rectangle.height
    );
}

function collectPayload() {
  return {
    mode: currentMode(),
    source_path: elements.sourcePath.value.trim(),
    permit_text: elements.permitText.value,
    box_x: elements.boxX.value,
    box_top_y: elements.boxTopY.value,
    insert_blanks: elements.insertBlanks.checked,
    blank_interval: elements.blankInterval.value,
    single_pdf_step: elements.singlePdfStep.value,
    output_path: elements.outputPath.value.trim(),
    whiteout_rectangles: collectRectangles(),
  };
}

function parseRectangleRows() {
  return Array.from(elements.rectanglesList.querySelectorAll(".rectangle-row"))
    .map((row) => {
      const x = Number(row.querySelector(".rectangle-x").value);
      const topY = Number(row.querySelector(".rectangle-top-y").value);
      const width = Number(row.querySelector(".rectangle-width").value);
      const height = Number(row.querySelector(".rectangle-height").value);
      if (!Number.isFinite(x) || !Number.isFinite(topY) || !Number.isFinite(width) || !Number.isFinite(height)) {
        return null;
      }
      if (width <= 0 || height <= 0) {
        return null;
      }
      return {
        id: row.dataset.id,
        x,
        topY,
        width,
        height,
      };
    })
    .filter(Boolean);
}

function previewScale() {
  const imageWidth = elements.previewImage.clientWidth;
  const imageHeight = elements.previewImage.clientHeight;
  const naturalWidth = elements.previewImage.naturalWidth;
  const naturalHeight = elements.previewImage.naturalHeight;
  if (!imageWidth || !imageHeight || !naturalWidth || !naturalHeight) {
    return null;
  }
  return {
    imageWidth,
    imageHeight,
    naturalWidth,
    naturalHeight,
  };
}

function rectangleToPixels(rectangle, scale) {
  return {
    left: (rectangle.x / scale.naturalWidth) * scale.imageWidth,
    top: ((scale.naturalHeight - rectangle.topY) / scale.naturalHeight) * scale.imageHeight,
    width: (rectangle.width / scale.naturalWidth) * scale.imageWidth,
    height: (rectangle.height / scale.naturalHeight) * scale.imageHeight,
  };
}

function rectangleElement(rectangle, scale, live = false) {
  const node = document.createElement("div");
  const pixelRect = rectangleToPixels(rectangle, scale);
  node.className = `preview-rectangle${live ? " is-live" : ""}`;
  if (
    state.previewTarget.type === "rectangle" &&
    state.previewTarget.id &&
    rectangle.id === state.previewTarget.id
  ) {
    node.classList.add("is-targeted");
  }
  node.style.left = `${pixelRect.left}px`;
  node.style.top = `${pixelRect.top}px`;
  node.style.width = `${pixelRect.width}px`;
  node.style.height = `${pixelRect.height}px`;
  return node;
}

function renderPreviewOverlays() {
  elements.previewOverlay.replaceChildren();
  const scale = previewScale();
  if (!scale) {
    return;
  }

  parseRectangleRows().forEach((rectangle) => {
    elements.previewOverlay.append(rectangleElement(rectangle, scale));
  });

  if (state.previewDrag?.rectangle) {
    elements.previewOverlay.append(rectangleElement(state.previewDrag.rectangle, scale, true));
  }
}

function latestLogLine(job) {
  if (!job.logs?.length) {
    return "";
  }
  return job.logs[job.logs.length - 1];
}

function jobSummary(job) {
  if (job.status === "completed") {
    return "Finished successfully.";
  }
  if (job.status === "failed") {
    return job.error || latestLogLine(job) || "Job failed.";
  }
  return latestLogLine(job) || "Working...";
}

function jobTitle(job) {
  return job.mode === "folder" ? "Folder run" : "Single PDF run";
}

function createJobCard(job) {
  const fragment = elements.jobTemplate.content.cloneNode(true);
  const container = fragment.querySelector(".job-card");
  container.id = `job-${job.id}`;

  const toggle = container.querySelector(".job-toggle");
  toggle.addEventListener("click", () => {
    const details = container.querySelector(".job-details");
    const expanded = !details.hidden;
    details.hidden = expanded;
    toggle.textContent = expanded ? "View details" : "Hide details";
  });

  elements.jobs.prepend(container);
  return container;
}

function renderJob(job) {
  let container = document.getElementById(`job-${job.id}`);
  if (!container) {
    container = createJobCard(job);
  }

  container.querySelector(".job-title").textContent = jobTitle(job);
  container.querySelector(".job-status").textContent = job.status;
  container.querySelector(".job-status").className = `job-status ${job.status}`;
  container.querySelector(".job-summary").textContent = jobSummary(job);
  container.querySelector(".job-meta").textContent = job.source_path;
  container.querySelector(".job-paths").textContent = `Output: ${job.output_path}`;
  container.querySelector(".job-log").textContent = job.logs?.length
    ? job.logs.join("\n")
    : "Waiting for output...";
}

async function pollJob(jobId) {
  try {
    const payload = await fetchJson(`/api/jobs/${jobId}`);
    state.jobs.set(jobId, payload);
    renderJob(payload);
    if (payload.status === "completed" || payload.status === "failed") {
      clearInterval(state.jobPollers.get(jobId));
      state.jobPollers.delete(jobId);
      setRunStatus(jobSummary(payload), payload.status === "failed");
    }
  } catch (_error) {
    clearInterval(state.jobPollers.get(jobId));
    state.jobPollers.delete(jobId);
  }
}

function applyPreviewClick(event) {
  if (!elements.previewImage.naturalWidth || !elements.previewImage.naturalHeight) {
    return;
  }
  if (state.previewTarget.type !== "permit") {
    return;
  }
  const bounds = elements.previewImage.getBoundingClientRect();
  const x = ((event.clientX - bounds.left) / bounds.width) * elements.previewImage.naturalWidth;
  const yFromTop = ((event.clientY - bounds.top) / bounds.height) * elements.previewImage.naturalHeight;
  const topY = elements.previewImage.naturalHeight - yFromTop;
  const xValue = x.toFixed(1);
  const yValue = topY.toFixed(1);

  elements.boxX.value = xValue;
  elements.boxTopY.value = yValue;
  elements.previewStatus.textContent = `Permit box position set to x ${xValue}, top y ${yValue}.`;
}

function previewPointFromEvent(event) {
  const scale = previewScale();
  if (!scale) {
    return null;
  }
  const bounds = elements.previewImage.getBoundingClientRect();
  const x = ((event.clientX - bounds.left) / bounds.width) * scale.naturalWidth;
  const yFromTop = ((event.clientY - bounds.top) / bounds.height) * scale.naturalHeight;
  const topY = scale.naturalHeight - yFromTop;
  return { x, topY };
}

function rectangleFromPoints(startPoint, endPoint) {
  const left = Math.min(startPoint.x, endPoint.x);
  const right = Math.max(startPoint.x, endPoint.x);
  const topY = Math.max(startPoint.topY, endPoint.topY);
  const bottomY = Math.min(startPoint.topY, endPoint.topY);
  return {
    id: state.previewTarget.id,
    x: left,
    topY,
    width: right - left,
    height: topY - bottomY,
  };
}

function startRectangleDrag(event) {
  if (state.previewTarget.type !== "rectangle") {
    return;
  }
  if ("button" in event && event.button !== 0) {
    return;
  }
  event.preventDefault();
  const row = document.querySelector(`.rectangle-row[data-id="${state.previewTarget.id}"]`);
  if (!row) {
    setPreviewTarget({ type: "permit" });
    return;
  }
  const point = previewPointFromEvent(event);
  if (!point) {
    return;
  }
  state.previewDrag = {
    start: point,
    rectangle: {
      id: row.dataset.id,
      x: point.x,
      topY: point.topY,
      width: 0,
      height: 0,
    },
  };
  elements.previewStatus.textContent = "Drag to draw the whiteout rectangle.";
  renderPreviewOverlays();
}

function updateRectangleDrag(event) {
  if (!state.previewDrag) {
    return;
  }
  const point = previewPointFromEvent(event);
  if (!point) {
    return;
  }
  state.previewDrag.rectangle = rectangleFromPoints(state.previewDrag.start, point);
  renderPreviewOverlays();
}

function finishRectangleDrag(event) {
  if (!state.previewDrag) {
    return;
  }
  event.preventDefault();
  const row = document.querySelector(`.rectangle-row[data-id="${state.previewTarget.id}"]`);
  if (!row) {
    state.previewDrag = null;
    renderPreviewOverlays();
    return;
  }
  const point = previewPointFromEvent(event) || state.previewDrag.start;
  const rectangle = rectangleFromPoints(state.previewDrag.start, point);
  state.previewDrag = null;
  row.querySelector(".rectangle-x").value = rectangle.x.toFixed(1);
  row.querySelector(".rectangle-top-y").value = rectangle.topY.toFixed(1);
  row.querySelector(".rectangle-width").value = rectangle.width.toFixed(1);
  row.querySelector(".rectangle-height").value = rectangle.height.toFixed(1);
  elements.previewStatus.textContent =
    `Rectangle set to x ${rectangle.x.toFixed(1)}, top y ${rectangle.topY.toFixed(1)}, width ${rectangle.width.toFixed(1)}, height ${rectangle.height.toFixed(1)}.`;
  renderPreviewOverlays();
}

async function syncSourceSideEffects() {
  await suggestOutputPath();
  await loadPreview();
  refreshWorkflowState();
}

async function initialize() {
  const payload = await fetchJson("/api/config");
  state.root = payload.root;
  elements.rootPath.textContent = payload.root;
  elements.modeSelect.value = payload.defaults.mode || "folder";
  elements.boxX.value = payload.defaults.box_x;
  elements.boxTopY.value = payload.defaults.box_top_y;
  elements.permitText.value = payload.defaults.permit_text;
  elements.blankInterval.value = payload.defaults.blank_interval;
  elements.singlePdfStep.value = payload.defaults.single_pdf_step;
  elements.insertBlanks.checked = payload.defaults.insert_blanks;
  elements.pickSourceButton.dataset.alwaysEnabled = "true";
  elements.modeSelect.dataset.alwaysEnabled = "true";
  elements.sourcePath.dataset.alwaysEnabled = "true";
  refreshModeFields();
  refreshBlankFields();
  refreshWorkflowState();
  setPreviewTarget({ type: "permit" });
}

elements.modeSelect.addEventListener("change", async () => {
  refreshModeFields();
  elements.sourcePath.value = "";
  elements.outputPath.value = "";
  elements.previewPanel.hidden = true;
  elements.previewImage.removeAttribute("src");
  refreshPathSummaries();
  setPreviewTarget({ type: "permit" });
  setRunStatus("");
  refreshWorkflowState();
});

elements.insertBlanks.addEventListener("change", () => {
  refreshBlankFields();
  refreshWorkflowState();
});

elements.sourcePath.addEventListener("change", syncSourceSideEffects);
elements.sourcePath.addEventListener("blur", syncSourceSideEffects);
elements.outputPath.addEventListener("input", refreshWorkflowState);
elements.outputPath.addEventListener("blur", refreshWorkflowState);

elements.pickSourceButton.addEventListener("click", pickSourcePath);
elements.pickOutputButton.addEventListener("click", pickOutputPath);
elements.suggestOutputButton.addEventListener("click", suggestOutputPath);
elements.usePermitClickButton.addEventListener("click", () => {
  setPreviewTarget({ type: "permit" });
});
elements.addRectangleButton.addEventListener("click", () => {
  const row = createRectangleRow();
  setPreviewTarget({ type: "rectangle", id: row.dataset.id });
});

elements.previewImage.addEventListener("load", () => {
  elements.previewStatus.textContent = state.previewTarget.type === "permit"
    ? "Click the preview to set the permit box top-left coordinate."
    : "Drag on the preview to size and place the whiteout rectangle.";
  renderPreviewOverlays();
});

elements.previewImage.addEventListener("error", () => {
  elements.previewStatus.textContent = "Preview could not be loaded for this source.";
});

elements.previewImage.addEventListener("dragstart", (event) => {
  event.preventDefault();
});
elements.previewFrame.addEventListener("click", applyPreviewClick);
elements.previewFrame.addEventListener("pointerdown", startRectangleDrag);
window.addEventListener("pointermove", updateRectangleDrag);
window.addEventListener("pointerup", finishRectangleDrag);
elements.previewFrame.addEventListener("mousedown", startRectangleDrag);
window.addEventListener("mousemove", updateRectangleDrag);
window.addEventListener("mouseup", finishRectangleDrag);
window.addEventListener("resize", renderPreviewOverlays);

elements.jobForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setRunStatus("Starting job...");
  try {
    const response = await fetchJson("/api/run", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(collectPayload()),
    });
    setRunStatus(`Job ${response.job_id} is running.`);
    const poller = setInterval(() => pollJob(response.job_id), 1200);
    state.jobPollers.set(response.job_id, poller);
    await pollJob(response.job_id);
  } catch (error) {
    setRunStatus(error.message, true);
  }
});

initialize().catch((error) => {
  setRunStatus(error.message, true);
});
