# StreamKeep Roadmap

StreamKeep is a Python/PyQt6 desktop downloader and archive manager for live streams, VODs, podcasts, and direct media URLs. This roadmap tracks pending work only; historical feature lists are archived under `docs/archive/roadmap/`.

## Planning Docs

- Current completed state: `COMPLETED.md`
- Research synthesis: `RESEARCH_REPORT.md`
- Legacy roadmap archive: `docs/archive/roadmap/ROADMAP-legacy.md`
- Legacy feature-candidate archive: `docs/archive/roadmap/features.md`

## Current Baseline

- Current package version: v4.31.2.
- The legacy F1-F80 roadmap has been implemented and is summarized in `COMPLETED.md`.
- Current architecture is modular: extractors, workers, post-processing, player, local server, SQLite library, plugin manager, upload adapters, intelligence helpers, and UI modules.
- History, monitor channels, and queue state live in SQLite; user preferences remain in JSON config.

## Active Roadmap

### 1. Release Hygiene

- Normalize `CHANGELOG.md` around current v4.31.x releases.
- Add reproducible release notes that pull from the full implementation history without duplicating legacy roadmap text.
- Ensure packaged builds include the current launcher, icon assets, plugin docs, browser companion, and optional dependency notes.
- Document the supported validation bundle for each release: `py_compile`, lint/pyflakes where available, unit tests, and a headless smoke path.

### 2. Test Coverage and CI

- Expand tests around the current high-risk areas: extractor command construction, SQLite migrations, upload adapters, local server auth/CORS, plugin manifest validation, backup/restore, and updater hash verification.
- Add regression tests for the v4.31.1/v4.31.2 audit fixes where coverage is still missing.
- Add a CI workflow that runs the lightweight validation suite on pushes and pull requests.

### 3. Security and Reliability Hardening

- Continue audit passes across subprocess boundaries, path normalization, URL handling, local API auth, upload destinations, updater downloads, and credential storage.
- Keep subprocess URL/argument handling using explicit separators and platform-safe quoting.
- Keep local server endpoints localhost-only and token-gated.
- Add visible error propagation for background workers that still fail silently.

### 4. Product Documentation

- Rewrite README into a current user guide covering the full app: downloader, monitor, queue, player, intelligence features, uploads, web gallery, RSS/feed outputs, CLI, server mode, browser companion, backup/restore, plugins, and accessibility options.
- Add screenshots or a short feature tour for the main tabs.
- Document configuration/storage locations and migration behavior.
- Document plugin SDK and browser companion pairing.

### 5. Future Feature Queue

- Remote/headless management polish for the REST server and local web gallery.
- More extractor-specific resilience for API churn on Kick, Twitch, Rumble, SoundCloud, Reddit, Audius, and yt-dlp fallback paths.
- Deeper library views for very large archives: filters, smart collections, transcript search, notes, bandwidth/storage trends, and channel statistics.
- Optional integrations where they stay local-first and user-controlled.

## Definition of Done

- Active planning remains in this file.
- Shipped state is recorded in `COMPLETED.md` and `CHANGELOG.md`.
- Research and rationale are summarized in `RESEARCH_REPORT.md`.
- Legacy planning artifacts stay archived and out of the repo root.
