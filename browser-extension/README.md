# StreamKeep Companion (browser extension)

One-click "send current tab URL to StreamKeep desktop app."

## Install (Chrome / Edge — unpacked)

1. Visit `chrome://extensions`, enable **Developer mode**.
2. Click **Load unpacked**, pick this `browser-extension/` folder.
3. Pin the StreamKeep icon in the toolbar.

## Install (Firefox — temporary)

1. Visit `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on**, pick this `browser-extension/manifest.json`.

## Pair with StreamKeep

1. Open StreamKeep → **Settings** tab → **Browser companion**. Tick **Enable**.
2. Select **New code** and note the loopback port. The one-use code expires after five minutes.
3. Click the StreamKeep browser icon, enter the port and code, then select **Pair**.
4. Click **Test pairing** — should show `Paired with StreamKeep.`

## Send a URL

Open any supported page (Kick, Twitch, YouTube, Rumble, etc.), click the icon, then:

- **Send to Fetch** — StreamKeep jumps to the Download tab and fetches immediately.
- **Send to Queue** — Queued silently; StreamKeep shows a status toast.

## Security

- The application server binds **strictly to 127.0.0.1**. Optional LAN access requires an explicitly configured HTTPS reverse proxy on the StreamKeep PC.
- Pairing codes are short-lived and one-use. The issued client token is origin-bound, scoped, and held only in browser session storage.
- Every mutating request includes a fresh timestamp and cryptographic nonce; Host, Origin, and cross-site metadata are validated by the server.
- Re-pairing uses a new code; no extension re-install is needed.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Test failed: HTTP 401` | Access expired or was revoked — generate and enter a new pairing code. |
| `Test failed: Failed to fetch` | StreamKeep isn't running, or the companion server is off in Settings. |
| `Send failed: Current tab has no http(s) URL` | Extension pages (chrome://, about:…) can't be captured. |
