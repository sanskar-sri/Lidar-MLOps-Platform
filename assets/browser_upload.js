(function () {
  const supportedExtensions = new Set([
    ".ply",
    ".las",
    ".laz",
    ".pts",
    ".xyz",
    ".txt",
    ".csv",
    ".xml",
    ".json",
    ".yaml",
    ".yml",
  ]);

  const tileExtensions = new Set([".ply", ".las", ".laz", ".pts", ".xyz", ".txt", ".csv"]);

  const state = {
    files: [],
    session: null,
    active: false,
    paused: false,
    abortRequested: false,
    committedBytes: 0,
    inFlightBytes: new Map(),
  };

  const maxRetries = 5;

  function el(id) {
    return document.getElementById(id);
  }

  function extensionOf(name) {
    const index = String(name || "").lastIndexOf(".");
    return index >= 0 ? String(name).slice(index).toLowerCase() : "";
  }

  function fmtBytes(value) {
    const n = Number(value || 0);
    if (n >= 1024 ** 4) return `${(n / 1024 ** 4).toFixed(2)} TB`;
    if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`;
    if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(2)} MB`;
    if (n >= 1024) return `${(n / 1024).toFixed(2)} KB`;
    return `${n} B`;
  }

  function totalBytes() {
    return state.files.reduce((sum, file) => sum + file.size, 0);
  }

  function liveUploadedBytes() {
    let inFlight = 0;
    state.inFlightBytes.forEach((value) => {
      inFlight += value;
    });
    return state.committedBytes + inFlight;
  }

  function setStatus(message, tone) {
    const target = el("browser-upload-client-status");
    if (!target) return;
    target.className = `browser-upload-client-status ${tone || ""}`.trim();
    target.textContent = message || "";
  }

  function updateControls() {
    const pause = el("browser-upload-pause-button");
    const abort = el("browser-upload-abort-button");
    const start = el("browser-upload-start-button");

    if (pause) {
      pause.disabled = !state.active;
      pause.textContent = state.paused ? "Resume" : "Pause";
    }
    if (abort) abort.disabled = !state.active || !state.session;
    if (start) start.disabled = state.active;
  }

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function renderSelectedFiles() {
    const target = el("browser-upload-file-list");
    if (!target) return;

    if (!state.files.length) {
      target.innerHTML = "<div class=\"browser-upload-empty\">No browser files selected.</div>";
      return;
    }

    const rows = state.files
      .slice(0, 12)
      .map((file) => {
        const rel = file.webkitRelativePath || file.name;
        return `<div class="browser-upload-file-row"><span>${escapeHtml(rel)}</span><strong>${fmtBytes(file.size)}</strong></div>`;
      })
      .join("");
    const extra = state.files.length > 12
      ? `<div class="browser-upload-more">+ ${state.files.length - 12} more file(s)</div>`
      : "";

    target.innerHTML = `
      <div class="browser-upload-summary">
        ${state.files.length} file(s), ${fmtBytes(totalBytes())} selected
      </div>
      ${rows}
      ${extra}
    `;
  }

  function filterSupportedFiles(files) {
    return Array.from(files || []).filter((file) => supportedExtensions.has(extensionOf(file.name)));
  }

  function validateSelection(files) {
    if (!files.length) {
      throw new Error("No supported raw tiles or label-map files were selected.");
    }
    const hasTile = files.some((file) => tileExtensions.has(extensionOf(file.name)));
    if (!hasTile) {
      throw new Error("Select at least one point-cloud tile. XML/JSON/YAML files are optional companions.");
    }
  }

  function chooseFiles(asFolder) {
    const input = ensurePickerInput(asFolder);
    input.value = "";
    input.click();
  }

  function ensurePickerInput(asFolder) {
    const id = asFolder
      ? "browser-upload-hidden-folder-input"
      : "browser-upload-hidden-file-input";
    let input = document.getElementById(id);

    if (!input) {
      input = document.createElement("input");
      input.id = id;
      input.type = "file";
      input.multiple = true;
      input.style.position = "fixed";
      input.style.left = "-10000px";
      input.style.top = "0";
      input.style.width = "1px";
      input.style.height = "1px";
      input.style.opacity = "0";
      input.setAttribute("aria-hidden", "true");

      if (asFolder) {
        input.setAttribute("webkitdirectory", "");
        input.setAttribute("directory", "");
      }

      input.addEventListener("change", () => {
        try {
          state.files = filterSupportedFiles(input.files);
          validateSelection(state.files);
          state.session = null;
          state.committedBytes = 0;
          state.inFlightBytes.clear();
          renderSelectedFiles();
          setStatus(
            `${state.files.length} file(s), ${fmtBytes(totalBytes())} ready for chunked upload.`,
            "info"
          );
        } catch (error) {
          state.files = [];
          renderSelectedFiles();
          setStatus(error.message || String(error), "error");
        }
      });

      document.body.appendChild(input);
    }

    return input;
  }

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `Request failed: ${response.status}`);
    }
    return data;
  }

  async function getJson(url) {
    const response = await fetch(url);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `Request failed: ${response.status}`);
    }
    return data;
  }

  async function createSession() {
    const datasetId = (el("dataset-id-input") || {}).value || "";
    const datasetName = (el("dataset-name-input") || {}).value || datasetId;
    const uploadMode = (el("upload-mode-dropdown") || {}).value || "browser_chunked";
    const description = (el("dataset-description-input") || {}).value || "";

    if (!datasetId.trim()) throw new Error("Enter a Dataset ID before uploading.");
    validateSelection(state.files);

    const files = state.files.map((file) => ({
      name: file.name,
      relative_path: file.webkitRelativePath || file.name,
      size: file.size,
      type: file.type || "application/octet-stream",
      last_modified: file.lastModified || null,
    }));

    const result = await postJson("/api/browser-upload/sessions", {
      dataset_id: datasetId.trim(),
      dataset_name: datasetName.trim() || datasetId.trim(),
      upload_mode: uploadMode,
      description,
      files,
    });
    state.session = result.session;
    return state.session;
  }

  function waitWhilePaused() {
    return new Promise((resolve) => {
      const tick = () => {
        if (state.abortRequested) resolve();
        else if (!state.paused) resolve();
        else window.setTimeout(tick, 250);
      };
      tick();
    });
  }

  function renderClientProgress(label) {
    const total = totalBytes();
    const uploaded = Math.min(liveUploadedBytes(), total);
    const percent = total ? ((uploaded / total) * 100).toFixed(2) : "0.00";
    const suffix = label ? ` | ${label}` : "";
    setStatus(
      `Browser upload: ${percent}% | ${fmtBytes(uploaded)} / ${fmtBytes(total)}${suffix}`,
      "active"
    );
  }

  function sendChunk(file, serverFile, chunkIndex, blob, progressKey) {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append("session_id", state.session.session_id);
      form.append("file_id", serverFile.file_id);
      form.append("chunk_index", String(chunkIndex));
      form.append("chunk", blob, `${serverFile.filename}.part-${chunkIndex}`);

      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/browser-upload/chunk");
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          state.inFlightBytes.set(progressKey, event.loaded);
          renderClientProgress(`${serverFile.filename} chunk ${chunkIndex + 1}/${serverFile.total_chunks}`);
        }
      };
      xhr.onload = () => {
        let data = {};
        try {
          data = JSON.parse(xhr.responseText || "{}");
        } catch (error) {
          reject(new Error(`Dash chunk upload returned an invalid response: HTTP ${xhr.status}`));
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300 && data.ok !== false) {
          resolve(data);
        } else {
          reject(new Error(data.error || `Dash chunk upload failed with HTTP ${xhr.status}`));
        }
      };
      xhr.onerror = () => reject(new Error("Connection to the Dash upload server failed while sending a chunk."));
      xhr.onabort = () => reject(new Error("Chunk upload aborted."));
      xhr.send(form);
    });
  }

  async function uploadChunk(file, serverFile, chunkIndex, chunkSize) {
    const start = chunkIndex * chunkSize;
    const end = Math.min(start + chunkSize, file.size);
    const blob = file.slice(start, end);
    const progressKey = `${serverFile.file_id}:${chunkIndex}`;

    for (let attempt = 1; attempt <= maxRetries; attempt += 1) {
      await waitWhilePaused();
      if (state.abortRequested) throw new Error("Upload aborted.");

      try {
        await sendChunk(file, serverFile, chunkIndex, blob, progressKey);
        state.inFlightBytes.delete(progressKey);
        state.committedBytes += blob.size;
        renderClientProgress(`${serverFile.filename} chunk ${chunkIndex + 1}/${serverFile.total_chunks}`);
        return;
      } catch (error) {
        state.inFlightBytes.delete(progressKey);
        if (attempt === maxRetries) throw error;
        setStatus(
          `${error.message || error}. Retrying chunk ${chunkIndex + 1}/${serverFile.total_chunks}...`,
          "error"
        );
        await new Promise((resolve) => window.setTimeout(resolve, attempt * 1500));
      }
    }
  }

  async function uploadFile(file, serverFile) {
    const chunkSize = Number(serverFile.chunk_size_bytes || state.session.chunk_size_bytes);
    const totalChunks = Number(serverFile.total_chunks || Math.ceil(file.size / chunkSize));

    for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex += 1) {
      await uploadChunk(file, serverFile, chunkIndex, chunkSize);
    }

    await postJson("/api/browser-upload/complete-file", {
      session_id: state.session.session_id,
      file_id: serverFile.file_id,
    });
  }

  function matchServerFile(file, serverFiles) {
    const rel = file.webkitRelativePath || file.name;
    return serverFiles.find(
      (item) =>
        item.relative_path === rel &&
        item.filename === file.name &&
        Number(item.file_size_bytes) === Number(file.size)
    );
  }

  async function pollSession(sessionId) {
    try {
      const result = await getJson(`/api/browser-upload/sessions/${sessionId}`);
      const session = result.session || {};
      const message = session.message || "B2 upload is running in the background.";

      if (session.status === "completed") {
        setStatus(message, "success");
        return;
      }
      if (session.status === "failed" || session.status === "aborted") {
        setStatus(message, "error");
        return;
      }
      setStatus(message, "info");
      window.setTimeout(() => pollSession(sessionId), 2000);
    } catch (error) {
      setStatus(error.message || String(error), "error");
    }
  }

  async function startUpload() {
    if (state.active) return;
    state.active = true;
    state.paused = false;
    state.abortRequested = false;
    state.committedBytes = 0;
    state.inFlightBytes.clear();
    updateControls();

    try {
      const session = await createSession();
      renderClientProgress("session created");

      for (const file of state.files) {
        if (state.abortRequested) throw new Error("Upload aborted.");
        const serverFile = matchServerFile(file, session.files || []);
        if (!serverFile) throw new Error(`Could not match server upload file for ${file.name}`);
        await uploadFile(file, serverFile);
      }

      await postJson("/api/browser-upload/complete-session", {
        session_id: session.session_id,
      });
      setStatus("Files staged. B2 upload and metadata are running in the background.", "success");
      pollSession(session.session_id);
    } catch (error) {
      setStatus(error.message || String(error), "error");
    } finally {
      state.active = false;
      state.paused = false;
      updateControls();
    }
  }

  async function abortUpload() {
    state.abortRequested = true;
    if (state.session) {
      try {
        await postJson("/api/browser-upload/abort", {
          session_id: state.session.session_id,
        });
      } catch (error) {
        setStatus(error.message || String(error), "error");
      }
    }
    state.active = false;
    state.paused = false;
    updateControls();
    setStatus("Browser upload aborted.", "error");
  }

  function wire() {
    const chooseFilesButton = el("browser-upload-choose-files-button");
    const chooseFolderButton = el("browser-upload-choose-folder-button");
    const startButton = el("browser-upload-start-button");
    const pauseButton = el("browser-upload-pause-button");
    const abortButton = el("browser-upload-abort-button");

    if (chooseFilesButton && !chooseFilesButton.dataset.browserUploadWired) {
      chooseFilesButton.dataset.browserUploadWired = "1";
      chooseFilesButton.addEventListener("click", () => chooseFiles(false));
    }
    if (chooseFolderButton && !chooseFolderButton.dataset.browserUploadWired) {
      chooseFolderButton.dataset.browserUploadWired = "1";
      chooseFolderButton.addEventListener("click", () => chooseFiles(true));
    }
    if (startButton && !startButton.dataset.browserUploadWired) {
      startButton.dataset.browserUploadWired = "1";
      startButton.addEventListener("click", startUpload);
    }
    if (pauseButton && !pauseButton.dataset.browserUploadWired) {
      pauseButton.dataset.browserUploadWired = "1";
      pauseButton.addEventListener("click", () => {
        state.paused = !state.paused;
        updateControls();
        setStatus(state.paused ? "Browser upload paused." : "Browser upload resumed.", "info");
      });
    }
    if (abortButton && !abortButton.dataset.browserUploadWired) {
      abortButton.dataset.browserUploadWired = "1";
      abortButton.addEventListener("click", abortUpload);
    }

    renderSelectedFiles();
    updateControls();
  }

  document.addEventListener("DOMContentLoaded", wire);
  window.setInterval(wire, 1000);
})();
