# StreamKeep Roadmap

StreamKeep is a Python/PyQt6 desktop downloader and archive manager for live streams, VODs, podcasts, and direct media URLs. This roadmap tracks pending work only; historical feature lists are archived under `docs/archive/roadmap/`.

## Planning Docs

- Current completed state: `COMPLETED.md`
- Research synthesis: `RESEARCH.md`
- Legacy research: `RESEARCH_REPORT.md`
- Legacy roadmap archive: `docs/archive/roadmap/ROADMAP-legacy.md`
- Legacy feature-candidate archive: `docs/archive/roadmap/features.md`

## Current Baseline

- Current package version: v4.31.4.
- The legacy F1-F80 roadmap has been implemented and is summarized in `COMPLETED.md`.
- Current architecture is modular: extractors, workers, post-processing, player, local server, SQLite library, plugin manager, upload adapters, intelligence helpers, and UI modules.
- History, monitor channels, and queue state live in SQLite; user preferences remain in JSON config.

## Active Roadmap

### 1. Security and Reliability Hardening

- Continue audit passes across subprocess boundaries, path normalization, URL handling, local API auth, upload destinations, updater downloads, and credential storage.
- Keep subprocess URL/argument handling using explicit separators and platform-safe quoting.
- Keep local server endpoints localhost-only and token-gated.
- Add visible error propagation for background workers that still fail silently.

### 2. Future Feature Queue

- Remote/headless management polish for the REST server and local web gallery.
- More extractor-specific resilience for API churn on Kick, Twitch, Rumble, SoundCloud, Reddit, Audius, and yt-dlp fallback paths.
- Deeper library views for very large archives: filters, smart collections, transcript search, notes, bandwidth/storage trends, and channel statistics.
- Optional integrations where they stay local-first and user-controlled.

### 3. Audit-Deferred Items

- [ ] P2 — Remove b64 fallback in secrets.py or gate it behind explicit opt-in
  Why: `allow_insecure_fallback=True` stores secrets as trivially reversible base64 in config.json.
  Where: `streamkeep/secrets.py`

- [ ] P2 — Isolate plugin imports from the app namespace
  Why: `load_plugin` appends plugin parent to `sys.path`, allowing a malicious plugin to shadow stdlib modules.
  Where: `streamkeep/plugins.py`

- [ ] P3 — Increase gallery share ID entropy from 48 to 128 bits
  Why: `uuid4().hex[:12]` (48 bits) is brute-forceable when bound to LAN (`bind_lan=True`).
  Where: `streamkeep/gallery.py`

- [ ] P3 — Strip trailing punctuation from clipboard URL captures
  Why: regex captures trailing `)`, `,`, `.` from natural text around URLs.
  Where: `streamkeep/clipboard.py`

- [ ] P3 — Validate FTP STOR filenames for special characters
  Why: `os.path.basename` strips paths but filenames with spaces or FTP-meaningful chars could fail.
  Where: `streamkeep/upload/ftp.py`

## Definition of Done

- Active planning remains in this file.
- Shipped state is recorded in `COMPLETED.md` and `CHANGELOG.md`.
- Research and rationale are summarized in `RESEARCH.md`.
- Legacy planning artifacts stay archived and out of the repo root.
