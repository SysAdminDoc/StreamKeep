// StreamKeep Companion popup. Exchanges a one-time code for an origin-bound
// token, then sends replay-protected requests to the loopback listener.

const $ = (id) => document.getElementById(id);
const statusEl = $("status");

async function storedPairing() {
  const [{ port }, { token }] = await Promise.all([
    chrome.storage.local.get(["port"]),
    chrome.storage.session.get(["token"]),
  ]);
  return { port, token };
}

function setStatus(msg, cls) {
  statusEl.className = "status " + (cls || "");
  statusEl.textContent = msg || "";
}

async function load() {
  const cfg = await storedPairing();
  if (cfg.port) $("port").value = cfg.port;
  if (cfg.token) setStatus("Paired. Test the connection or send a URL.", "ok");
}

async function pair() {
  const port = parseInt($("port").value, 10) || 0;
  const code = $("pairing-code").value.trim();
  if (!port || !code) throw new Error("Enter the loopback port and one-time pairing code.");
  const result = await companionRequest(
    "/pair",
    "POST",
    { code, scopes: ["status", "queue"] },
    port,
  );
  if (!result.ok) throw new Error(result.message || "Pairing failed.");
  $("pairing-code").value = "";
  setStatus("Paired with StreamKeep.", "ok");
}

async function currentTabUrl() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab && tab.url ? tab.url : "";
}

function companionRequest(path, method, body, port = 0) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(
      { type: "companionRequest", path, method, body, port },
      (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
        } else if (!response) {
          reject(new Error("The StreamKeep service worker did not respond."));
        } else {
          resolve(response);
        }
      },
    );
  });
}

async function companionCall(path, method, body) {
  const result = await companionRequest(path, method, body);
  if (!result.ok) {
    if (result.err === "token_invalid") {
      throw new Error("Access expired or was rotated. Generate a new pairing code in StreamKeep Settings.");
    }
    if (result.err === "scope_denied") {
      throw new Error(result.message || "Token lacks the required scope.");
    }
    throw new Error(result.message || `HTTP ${result.status || 0}`);
  }
  return result.data || {};
}

async function testPairing() {
  setStatus("Checking…");
  try {
    const r = await companionCall("/ping", "GET");
    setStatus(`Paired with ${r.app || "StreamKeep"}.`, "ok");
  } catch (e) {
    setStatus(`Test failed: ${e.message}`, "err");
  }
}

async function sendUrl(action) {
  setStatus("Sending…");
  try {
    const url = await currentTabUrl();
    if (!url || !/^https?:/.test(url)) {
      setStatus("Current tab has no http(s) URL.", "err");
      return;
    }
    const body = { url, action };
    const clipStart = ($("clip-start") || {}).value || "";
    const clipEnd = ($("clip-end") || {}).value || "";
    if (clipStart) body.clip_start = clipStart.trim();
    if (clipEnd) body.clip_end = clipEnd.trim();
    await companionCall("/send_url", "POST", body);
    const clipNote = (clipStart || clipEnd) ? " with clip range" : "";
    setStatus(`Sent to ${action}${clipNote}.`, "ok");
  } catch (e) {
    setStatus(`Send failed: ${e.message}`, "err");
  }
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !Number.isInteger(tab.id) || !/^https?:/.test(tab.url || "")) {
    throw new Error("The active tab has no http(s) URL.");
  }
  return tab;
}

function captureMessage(message) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else if (!response || !response.ok) {
        reject(new Error(response?.message || "Capture request failed."));
      } else {
        resolve(response);
      }
    });
  });
}

function renderCandidates(state) {
  const list = $("media-list");
  list.textContent = "";
  const candidates = state?.candidates || [];
  if (!candidates.length) {
    list.textContent = state?.armed
      ? "Waiting for media requests…"
      : "Start capture, then play the media in the tab.";
    return;
  }
  for (const candidate of [...candidates].reverse()) {
    const row = document.createElement("div");
    row.className = "media-item";
    const url = document.createElement("div");
    url.className = "media-url";
    url.textContent = candidate.url;
    const meta = document.createElement("div");
    meta.className = "media-meta";
    meta.textContent = `${candidate.kind}${candidate.content_type ? ` · ${candidate.content_type}` : ""}`;
    const actions = document.createElement("div");
    actions.className = "row";
    for (const action of ["fetch", "queue"]) {
      const button = document.createElement("button");
      button.textContent = action === "fetch" ? "Send to Fetch" : "Queue";
      if (action === "queue") button.className = "secondary";
      button.addEventListener("click", () => sendCaptured(candidate, action));
      actions.appendChild(button);
    }
    row.append(url, meta, actions);
    list.appendChild(row);
  }
}

async function refreshCapture() {
  try {
    const tab = await activeTab();
    const state = await captureMessage({ type: "getCaptureState", tabId: tab.id });
    renderCandidates(state);
    $("capture-start").disabled = Boolean(state.armed);
    $("capture-stop").disabled = !state.armed;
  } catch (e) {
    renderCandidates({ armed: false, candidates: [] });
    $("capture-start").disabled = false;
    $("capture-stop").disabled = true;
  }
}

async function startCapture() {
  try {
    const tab = await activeTab();
    await captureMessage({
      type: "armCapture",
      tabId: tab.id,
      tabUrl: tab.url,
      tabTitle: tab.title,
    });
    setStatus("Capturing media requests for this tab…", "ok");
    await refreshCapture();
  } catch (e) {
    setStatus(`Capture failed: ${e.message}`, "err");
  }
}

async function stopCapture() {
  try {
    const tab = await activeTab();
    await captureMessage({ type: "stopCapture", tabId: tab.id });
    setStatus("Media capture stopped.", "ok");
    await refreshCapture();
  } catch (e) {
    setStatus(`Capture failed: ${e.message}`, "err");
  }
}

async function clearCapture() {
  try {
    const tab = await activeTab();
    await captureMessage({ type: "clearCapture", tabId: tab.id });
    await refreshCapture();
  } catch (e) {
    setStatus(`Capture failed: ${e.message}`, "err");
  }
}

async function sendCaptured(candidate, action) {
  setStatus("Sending captured media…");
  try {
    await companionCall("/send_url", "POST", {
      url: candidate.url,
      action,
      request_headers: candidate.request_headers || {},
      source_context: {
        tab_url: candidate.tab_url || "",
        tab_title: candidate.tab_title || "",
        kind: candidate.kind || "",
        content_type: candidate.content_type || "",
      },
    });
    setStatus(`Captured ${candidate.kind} sent to ${action}.`, "ok");
  } catch (e) {
    setStatus(`Send failed: ${e.message}`, "err");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  load();
  $("pair").addEventListener("click", async () => {
    setStatus("Pairing…");
    try { await pair(); } catch (e) { setStatus(`Pairing failed: ${e.message}`, "err"); }
  });
  $("test").addEventListener("click", testPairing);
  $("send-fetch").addEventListener("click", () => sendUrl("fetch"));
  $("send-queue").addEventListener("click", () => sendUrl("queue"));
  $("capture-start").addEventListener("click", startCapture);
  $("capture-stop").addEventListener("click", stopCapture);
  $("capture-clear").addEventListener("click", clearCapture);
  refreshCapture();
  window.setInterval(refreshCapture, 1000);
});
