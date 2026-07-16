// Service worker — registers right-click context menu and handles sends.

async function companionPost(path, body) {
  const [{ port }, { token }] = await Promise.all([
    chrome.storage.local.get(["port"]),
    chrome.storage.session.get(["token"]),
  ]);
  if (!port || !token) return;
  const url = `http://127.0.0.1:${port}${path}`;
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      "X-StreamKeep-Timestamp": String(Math.floor(Date.now() / 1000)),
      "X-StreamKeep-Nonce": Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(""),
    },
    body: JSON.stringify(body),
  });
  if (resp.status === 401) {
    console.warn("[StreamKeep] Access expired or rotated. Generate a new pairing code in Settings.");
  }
}

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
