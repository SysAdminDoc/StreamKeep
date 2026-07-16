// StreamKeep Companion popup. Exchanges a one-time code for an origin-bound
// token, then sends replay-protected requests to the loopback listener.

const $ = (id) => document.getElementById(id);
const statusEl = $("status");

function freshHeaders() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return {
    "X-StreamKeep-Timestamp": String(Math.floor(Date.now() / 1000)),
    "X-StreamKeep-Nonce": Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(""),
  };
}

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
  const resp = await fetch(`http://127.0.0.1:${port}/pair`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...freshHeaders() },
    body: JSON.stringify({ code, scopes: ["status", "queue"] }),
  });
  const result = await resp.json();
  if (!resp.ok || !result.token) throw new Error(result.message || "Pairing failed.");
  await chrome.storage.local.set({ port });
  await chrome.storage.session.set({ token: result.token });
  $("pairing-code").value = "";
  setStatus("Paired with StreamKeep.", "ok");
}

async function currentTabUrl() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab && tab.url ? tab.url : "";
}

async function companionCall(path, method, body) {
  const { port, token } = await storedPairing();
  if (!port || !token) {
    throw new Error("Pair this extension with StreamKeep first.");
  }
  const url = `http://127.0.0.1:${port}${path}`;
  const resp = await fetch(url, {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...freshHeaders(),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const err = await resp.json();
      if (err.err === "token_invalid") {
        throw new Error("Access expired or was rotated. Generate a new pairing code in StreamKeep Settings.");
      }
      if (err.err === "scope_denied") {
        throw new Error(err.message || "Token lacks the required scope.");
      }
      if (err.message) detail = err.message;
    } catch (e) {
      if (e.message.startsWith("Access expired") || e.message.startsWith("Token lacks"))
        throw e;
    }
    throw new Error(detail);
  }
  return resp.json();
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

async function sendAllTabs() {
  setStatus("Sending tabs…");
  try {
    const tabs = await chrome.tabs.query({});
    const urls = tabs
      .map((t) => t.url)
      .filter((u) => u && /^https?:/.test(u));
    if (urls.length === 0) {
      setStatus("No http(s) tabs found.", "err");
      return;
    }
    let ok = 0;
    for (const url of urls) {
      try {
        await companionCall("/send_url", "POST", { url, action: "queue" });
        ok++;
      } catch (_) {
        /* skip individual failures */
      }
    }
    setStatus(`Sent ${ok} of ${urls.length} tabs.`, ok > 0 ? "ok" : "err");
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
  $("send-all-tabs").addEventListener("click", sendAllTabs);
});
