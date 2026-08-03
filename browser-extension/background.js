// Service worker — registers right-click context menu and handles sends.

function freshHeaders() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return {
    "X-StreamKeep-Timestamp": String(Math.floor(Date.now() / 1000)),
    "X-StreamKeep-Nonce": Array.from(
      bytes,
      (b) => b.toString(16).padStart(2, "0"),
    ).join(""),
  };
}

const COMPANION_ROUTES = new Set(["/pair", "/ping", "/send_url"]);

async function companionRequest(path, method, body, portOverride = 0) {
  if (!COMPANION_ROUTES.has(path)) {
    return { ok: false, status: 400, message: "Unsupported companion route." };
  }
  const [{ port }, { token }] = await Promise.all([
    chrome.storage.local.get(["port"]),
    chrome.storage.session.get(["token"]),
  ]);
  const portNumber = Number(portOverride || port || 0);
  const pairing = path === "/pair";
  if (!portNumber || (!pairing && !token)) {
    return { ok: false, status: 401, message: "Pair this extension with StreamKeep first." };
  }
  const headers = { "Content-Type": "application/json", ...freshHeaders() };
  if (!pairing) headers.Authorization = `Bearer ${token}`;
  const url = `http://127.0.0.1:${portNumber}${path}`;
  const resp = await fetch(url, {
    method: method || "GET",
    headers,
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await resp.json(); } catch (_) { /* empty response */ }
  if (resp.status === 401) {
    console.warn("[StreamKeep] Access expired or rotated. Generate a new pairing code in Settings.");
  }
  if (!resp.ok) {
    return {
      ok: false,
      status: resp.status,
      err: data.err || "",
      message: data.message || `HTTP ${resp.status}`,
    };
  }
  if (pairing && data.token) {
    await chrome.storage.local.set({ port: portNumber });
    await chrome.storage.session.set({ token: data.token });
    delete data.token;
  }
  return { ok: true, status: resp.status, data };
}

async function companionPost(path, body) {
  return companionRequest(path, "POST", body);
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "companionRequest") return false;
  companionRequest(
    String(message.path || ""),
    String(message.method || "GET"),
    message.body,
    Number(message.port || 0),
  )
    .then(sendResponse)
    .catch((error) => sendResponse({ ok: false, status: 0, message: error.message }));
  return true;
});

const MAX_CAPTURED_MEDIA = 48;
const MAX_PENDING_REQUESTS = 256;
const REPLAY_HEADERS = {
  authorization: "Authorization",
  cookie: "Cookie",
  origin: "Origin",
  referer: "Referer",
  "user-agent": "User-Agent",
};
const MANIFEST_EXTENSIONS = [".m3u8", ".mpd"];
const MEDIA_EXTENSIONS = [
  ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".flv", ".avi",
  ".m4a", ".mp3", ".aac", ".flac", ".wav", ".opus", ".ogg", ".oga",
  ".ts", ".m4s", ".m4f", ".cmfv", ".cmfa",
];
const CAPTURE_REQUEST_TYPES = new Set(["media", "xmlhttprequest", "fetch", "other"]);

let captureStateLoaded = false;
const captureTabs = new Set();
const captureMeta = {};
const capturedMedia = {};
const pendingRequests = new Map();
let requestListenersRegistered = false;

async function loadCaptureState() {
  if (captureStateLoaded) return;
  const stored = await chrome.storage.session.get([
    "streamkeepCaptureTabs", "streamkeepCaptureMeta", "streamkeepCapturedMedia",
  ]);
  for (const tabId of stored.streamkeepCaptureTabs || []) captureTabs.add(Number(tabId));
  Object.assign(captureMeta, stored.streamkeepCaptureMeta || {});
  Object.assign(capturedMedia, stored.streamkeepCapturedMedia || {});
  captureStateLoaded = true;
  refreshRequestListeners();
}

let persistTimer = null;
function persistCaptureState() {
  if (persistTimer !== null) clearTimeout(persistTimer);
  persistTimer = setTimeout(() => {
    persistTimer = null;
    chrome.storage.session.set({
      streamkeepCaptureTabs: [...captureTabs],
      streamkeepCaptureMeta: captureMeta,
      streamkeepCapturedMedia: capturedMedia,
    });
  }, 50);
}

function captureKey(tabId, url) {
  return `${tabId}|${url}`;
}

function contentType(headers) {
  for (const header of headers || []) {
    if (String(header.name || "").toLowerCase() === "content-type") {
      return String(header.value || "").split(";", 1)[0].trim().toLowerCase();
    }
  }
  return "";
}

function classifyMedia(url, mime = "") {
  let path = "";
  try { path = new URL(url).pathname.toLowerCase(); } catch (_) { return ""; }
  if (MANIFEST_EXTENSIONS.some((ext) => path.endsWith(ext))) return "manifest";
  if (mime === "application/vnd.apple.mpegurl" ||
      mime === "application/x-mpegurl" || mime === "application/dash+xml") {
    return "manifest";
  }
  if (MEDIA_EXTENSIONS.some((ext) => path.endsWith(ext))) return "media";
  if (mime.startsWith("video/") || mime.startsWith("audio/")) return "media";
  return "";
}

function replayHeaders(rawHeaders) {
  const output = {};
  const entries = Array.isArray(rawHeaders)
    ? rawHeaders
    : Object.entries(rawHeaders || {}).map(([name, value]) => ({ name, value }));
  for (const header of entries) {
    const name = REPLAY_HEADERS[String(header.name || "").toLowerCase()];
    const value = String(header.value || "").trim();
    if (!name || !value || value.length > 8192 || /[\u0000-\u001f\u007f]/.test(value)) continue;
    if (!(name in output)) output[name] = value;
  }
  return output;
}

function tabDetails(tabId, details) {
  const meta = captureMeta[tabId] || {};
  return {
    tab_url: String(meta.tab_url || details.documentUrl || "").slice(0, 4096),
    tab_title: String(meta.tab_title || "").slice(0, 256),
  };
}

function recordMedia(tabId, details, kind, headers, mime) {
  if (!captureTabs.has(Number(tabId)) || !kind) return;
  const key = captureKey(tabId, details.url);
  const previous = capturedMedia[key] || {};
  const context = tabDetails(tabId, details);
  capturedMedia[key] = {
    id: key,
    url: details.url.slice(0, 4096),
    kind,
    content_type: mime || previous.content_type || "",
    request_headers: {
      ...(previous.request_headers || {}),
      ...replayHeaders(headers),
    },
    ...context,
    observed_at: Date.now(),
  };
  const keys = Object.keys(capturedMedia)
    .filter((entry) => Number(entry.split("|", 1)[0]) === Number(tabId))
    .sort((a, b) => Number(capturedMedia[b].observed_at || 0) - Number(capturedMedia[a].observed_at || 0));
  for (const oldKey of keys.slice(MAX_CAPTURED_MEDIA)) delete capturedMedia[oldKey];
  persistCaptureState();
}

function activeOriginPattern(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
    const host = parsed.hostname.includes(":")
      ? `[${parsed.hostname}]`
      : parsed.hostname;
    return `${parsed.protocol}//${host}/*`;
  } catch (_) {
    return "";
  }
}

function refreshRequestListeners() {
  if (requestListenersRegistered) {
    chrome.webRequest.onBeforeSendHeaders.removeListener(onBeforeSendHeaders);
    chrome.webRequest.onHeadersReceived.removeListener(onHeadersReceived);
    requestListenersRegistered = false;
  }
  const urls = [...captureTabs]
    .map((tabId) => activeOriginPattern(captureMeta[tabId]?.tab_url || ""))
    .filter(Boolean);
  if (!urls.length) return;
  const filter = { urls: [...new Set(urls)], types: [...CAPTURE_REQUEST_TYPES] };
  chrome.webRequest.onBeforeSendHeaders.addListener(
    onBeforeSendHeaders,
    filter,
    ["requestHeaders", "extraHeaders"],
  );
  chrome.webRequest.onHeadersReceived.addListener(
    onHeadersReceived,
    filter,
    ["responseHeaders", "extraHeaders"],
  );
  requestListenersRegistered = true;
}

async function onBeforeSendHeaders(details) {
  await loadCaptureState();
  if (!captureTabs.has(Number(details.tabId))) return;
  const tabId = Number(details.tabId);
  const headers = replayHeaders(details.requestHeaders);
  const kind = classifyMedia(details.url);
  if (kind) recordMedia(tabId, details, kind, headers, "");
  if (CAPTURE_REQUEST_TYPES.has(details.type)) {
    const key = `${tabId}|${details.requestId}`;
    pendingRequests.set(key, {
      url: details.url,
      headers,
      documentUrl: details.documentUrl || "",
    });
    const keys = [...pendingRequests.keys()].filter((entry) => entry.startsWith(`${tabId}|`));
    for (const oldKey of keys.slice(0, Math.max(0, keys.length - MAX_PENDING_REQUESTS))) {
      pendingRequests.delete(oldKey);
    }
  }
}

async function onHeadersReceived(details) {
  await loadCaptureState();
  const tabId = Number(details.tabId);
  if (!captureTabs.has(tabId)) return;
  const pending = pendingRequests.get(`${tabId}|${details.requestId}`);
  pendingRequests.delete(`${tabId}|${details.requestId}`);
  const mime = contentType(details.responseHeaders);
  const kind = classifyMedia(details.url, mime);
  if (!kind) return;
  recordMedia(
    tabId,
    { ...details, documentUrl: pending?.documentUrl || details.documentUrl || "" },
    kind,
    pending?.headers || {},
    mime,
  );
}

async function captureMessage(message) {
  await loadCaptureState();
  const tabId = Number(message.tabId);
  if (!Number.isInteger(tabId) || tabId < 0) throw new Error("Invalid tab id");
  if (message.type === "armCapture") {
    if (!activeOriginPattern(message.tabUrl || "")) {
      throw new Error("Capture requires an http(s) active tab.");
    }
    captureTabs.add(tabId);
    captureMeta[tabId] = {
      tab_url: String(message.tabUrl || "").slice(0, 4096),
      tab_title: String(message.tabTitle || "").slice(0, 256),
    };
    refreshRequestListeners();
    for (const key of Object.keys(capturedMedia)) {
      if (Number(key.split("|", 1)[0]) === tabId) delete capturedMedia[key];
    }
    persistCaptureState();
  } else if (message.type === "stopCapture") {
    captureTabs.delete(tabId);
    refreshRequestListeners();
    persistCaptureState();
  } else if (message.type === "clearCapture") {
    for (const key of Object.keys(capturedMedia)) {
      if (Number(key.split("|", 1)[0]) === tabId) delete capturedMedia[key];
    }
    persistCaptureState();
  } else if (message.type === "getCaptureState") {
    return {
      armed: captureTabs.has(tabId),
      candidates: Object.values(capturedMedia)
        .filter((candidate) => Number(candidate.id.split("|", 1)[0]) === tabId)
        .sort((a, b) => Number(a.observed_at || 0) - Number(b.observed_at || 0)),
    };
  }
  return {
    armed: captureTabs.has(tabId),
    candidates: Object.values(capturedMedia)
      .filter((candidate) => Number(candidate.id.split("|", 1)[0]) === tabId),
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || !String(message.type || "").endsWith("Capture")) return false;
  captureMessage(message)
    .then((result) => sendResponse({ ok: true, ...result }))
    .catch((error) => sendResponse({ ok: false, message: error.message }));
  return true;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  captureTabs.delete(Number(tabId));
  delete captureMeta[tabId];
  for (const key of Object.keys(capturedMedia)) {
    if (Number(key.split("|", 1)[0]) === Number(tabId)) delete capturedMedia[key];
  }
  refreshRequestListeners();
  persistCaptureState();
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (!changeInfo.url || !captureTabs.has(Number(tabId))) return;
  captureTabs.delete(Number(tabId));
  delete captureMeta[tabId];
  for (const key of Object.keys(capturedMedia)) {
    if (Number(key.split("|", 1)[0]) === Number(tabId)) delete capturedMedia[key];
  }
  refreshRequestListeners();
  persistCaptureState();
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "send-link",
    title: "Send link to StreamKeep",
    contexts: ["link"],
  });
  chrome.contextMenus.create({
    id: "send-page",
    title: "Send page to StreamKeep",
    contexts: ["page"],
  });
});

chrome.contextMenus.onClicked.addListener((info) => {
  const url = info.linkUrl || info.pageUrl;
  if (!url || !/^https?:/.test(url)) return;
  companionPost("/send_url", { url, action: "queue" });
});
