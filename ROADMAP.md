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

## Research-Driven Additions


### P2 — Medium (maintainability, platform, quality)









### P3 — Lower (features, polish, future)











### P1 - Packaging, Secrets, and Test Coverage

### P2 - Platform Depth, I18n, and Extensibility

- [ ] P2 - Support dynamic DASH and low-latency live manifest patterns
  Why: `dash.py` rejects dynamic MPD manifests and HLS parsing does not model low-latency partial segments, while comparable downloaders handle broader live/DVR manifest shapes.
  Evidence: `streamkeep/dash.py`, `streamkeep/hls.py`, N_m3u8DL-RE, DASH-IF docs, RFC 8216.
  Touches: `streamkeep/dash.py`, `streamkeep/hls.py`, `streamkeep/workers/download.py`, `tests/test_scrape.py`, new manifest parser tests.
  Acceptance: parser tests cover dynamic MPD fail/handle decisions, multi-period static MPD, LL-HLS tags, and DRM skip behavior; supported dynamic/live cases download through ffmpeg safely with existing protocol restrictions.
  Complexity: L

- [ ] P2 - Publish a plugin SDK contract and sample plugin
  Why: plugin trust gating exists, but developers lack schema validation, examples, compatibility checks, and extension-point documentation.
  Evidence: `streamkeep/plugins.py`, `README.md`, yt-dlp plugin docs, Streamlink plugin docs.
  Touches: `streamkeep/plugins.py`, `README.md`, `tests/test_plugins.py`, sample plugin fixture.
  Acceptance: manifest schema is validated with clear errors; sample extractor plugin loads only when trusted; README documents extension points, version compatibility, and failure modes; tests cover incompatible manifest versions.
  Complexity: M

- [ ] P2 - Bridge structured logging into the GUI log panel
  Why: recent modules use `logging`, but many GUI and helper paths still rely on ad-hoc strings or silent fallbacks, making unattended monitor failures harder to diagnose.
  Evidence: `streamkeep/workers/download.py`, `streamkeep/config.py`, `streamkeep/ui/main_window.py`, Python logging documentation.
  Touches: `streamkeep/config.py`, `streamkeep/ui/main_window.py`, `streamkeep/workers/`, `streamkeep/extractors/`, tests.
  Acceptance: logging records from core modules appear in the existing log panel with level/module labels; crash/log files rotate; tests verify warning/error propagation without duplicating messages.
  Complexity: M

### P3 - Product Polish and Longer Bets

- [ ] P3 - Add HandBrake-style conversion presets
  Why: StreamKeep exposes codec/container controls, but user-facing device/social/archive presets would reduce errors for common export targets.
  Evidence: `streamkeep/postprocess/processor.py`, `streamkeep/ui/tabs/settings.py`, HandBrake preset UX.
  Touches: `streamkeep/postprocess/processor.py`, `streamkeep/ui/tabs/settings.py`, config migration, tests.
  Acceptance: users can choose Archive, Discord, YouTube, Audio-only, and device-safe presets; selecting a preset updates codec/container/bitrate fields predictably; tests cover config persistence.
  Complexity: M

- [ ] P3 - Add remote web UI recovery views
  Why: Ganymede/LiveStreamDVR-style unattended use depends on seeing failures and retrying from another device, while StreamKeep's web remote is still queue/status focused.
  Evidence: `streamkeep/local_server.py`, `streamkeep/gallery.py`, Ganymede, LiveStreamDVR.
  Touches: `streamkeep/local_server.py`, `streamkeep/ui/tabs/settings.py`, `streamkeep/db.py`, tests.
  Acceptance: authenticated web remote lists recent failures, resume sidecars, active workers, and retry/discard actions without exposing filesystem paths unnecessarily.
  Complexity: L

## Definition of Done

- Active planning remains in this file.
- Shipped state is recorded in `COMPLETED.md` and `CHANGELOG.md`.
- Research and rationale are summarized in `RESEARCH.md`.
- Legacy planning artifacts stay archived and out of the repo root.

## Research-Driven Additions

### P2 - Distribution, Portability, and Operations

- [ ] P2 - Add SBOM and dependency-advisory release check
  Why: packaged builds bundle Python runtime dependencies, but release validation does not yet produce a dependency inventory or fail on known vulnerable packages.
  Evidence: `requirements.txt`, `StreamKeep.spec`, `packaging/`, yt-dlp 2026.06.09 release notes, Pillow releases, CycloneDX, pip-audit.
  Touches: `requirements.txt`, `packaging/`, release validation scripts, `tests/test_packaging.py`, `README.md`.
  Acceptance: local release validation generates a CycloneDX SBOM from the frozen environment, runs a pip-audit or OSV-compatible advisory scan, fails on known vulnerable runtime dependencies unless explicitly documented, and emits the SBOM beside built artifacts without adding CI.
  Complexity: M

- [ ] P2 - Package and smoke-test the browser companion as a release artifact
  Why: browser capture is table-stakes in comparable download managers, but StreamKeep's MV3 companion is not yet verified as a deterministic packaged artifact with minimal permissions and working pairing.
  Evidence: `browser-extension/manifest.json`, `StreamKeep.spec`, `README.md`, Chrome MV3 docs, MeTube, Downie, 4K Video Downloader, Internet Download Manager.
  Touches: `browser-extension/`, `packaging/`, `tests/test_packaging.py`, `streamkeep/local_server.py`, `README.md`.
  Acceptance: local packaging emits a deterministic extension ZIP with manifest, icons, popup, and background files; tests validate MV3 version, minimal permissions, host permissions, and missing-asset failures; smoke test exercises `/ping` against the local test server.
  Complexity: M

- [ ] P2 - Add OPML import/export for podcast and monitor subscriptions
  Why: StreamKeep already parses podcast feeds and monitors channels, but subscription portability is app-specific while OPML is the common feed-exchange format.
  Evidence: `streamkeep/extractors/podcast.py`, `streamkeep/monitor.py`, `streamkeep/db.py`, OPML 2.0 specification.
  Touches: `streamkeep/extractors/podcast.py`, `streamkeep/monitor.py`, `streamkeep/db.py`, `streamkeep/ui/tabs/monitor.py`, `tests/test_db.py`, new OPML tests.
  Acceptance: users can import nested OPML outlines into podcast/feed monitor entries with duplicate and invalid-feed reporting, export selected or all RSS-capable subscriptions, and roundtrip valid OPML in tests.
  Complexity: M

- [ ] P2 - Add a headless/service deployment profile
  Why: CLI/server mode exists, and self-hosted competitors treat service operation as normal, but StreamKeep lacks a documented and smoke-tested service profile with explicit bind, token, config, and output paths.
  Evidence: `StreamKeep.py`, `streamkeep/local_server.py`, `streamkeep/cli.py`, MeTube, Tube Archivist, Pinchflat, TubeSync.
  Touches: `StreamKeep.py`, `streamkeep/cli.py`, `streamkeep/local_server.py`, `packaging/`, `tests/test_local_server.py`, `README.md`.
  Acceptance: local service profile runs server mode with explicit config directory, bind address, token, and output directory; Windows Task Scheduler/service and systemd user examples live in existing packaging/docs locations; smoke test starts the server and verifies authenticated `/ping` without using GUI defaults implicitly.
  Complexity: M

### P3 - Archive Interoperability

- [ ] P3 - Add metadata sidecar export profiles for media servers
  Why: long-term archives should remain useful outside StreamKeep, and comparable media-library workflows rely on portable NFO/JSON/thumb sidecars for Jellyfin/Plex-style folders.
  Evidence: `streamkeep/metadata.py`, `streamkeep/integrations/media_server.py`, Jellyfin, Ganymede, Pinchflat.
  Touches: `streamkeep/metadata.py`, `streamkeep/integrations/media_server.py`, `streamkeep/ui/tabs/settings.py`, `tests/test_metadata.py`, media-server integration tests.
  Acceptance: per-library profiles generate or refresh compatible NFO/JSON/thumb sidecars without overwriting user edits; disabled profiles leave existing files untouched; tests cover output names, metadata fields, and idempotent reruns.
  Complexity: M

## Research-Driven Additions

### P2 - Data Store Reliability

- [ ] P2 - Add SQLite maintenance and recovery diagnostics
  Why: StreamKeep's archive state depends on SQLite, but `db.py` has no integrity check, optimize/vacuum, WAL checkpoint, or user-visible repair guidance, while comparable archive apps report database growth and reindex pain.
  Evidence: `streamkeep/db.py`, `tests/test_db.py`, Pinchflat issue #887, Tube Archivist issue #915, SQLite PRAGMA docs, restic check/rebuild patterns.
  Touches: `streamkeep/db.py`, `streamkeep/backup.py`, `streamkeep/ui/tabs/settings.py`, `streamkeep/local_server.py`, `tests/test_db.py`, `tests/test_backup.py`.
  Acceptance: Settings or CLI can run a read-only integrity check, `PRAGMA optimize`, WAL checkpoint, and optional vacuum after backup; failures produce a clear diagnostic/export bundle and never mutate the DB before a backup snapshot; tests cover healthy DB, corrupt-copy detection, backup-before-vacuum, and no-op behavior when the DB is missing.
  Complexity: M
