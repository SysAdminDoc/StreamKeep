# StreamKeep Roadmap

StreamKeep is a Python/PyQt6 desktop downloader and archive manager for live streams, VODs, podcasts, and direct media URLs. This roadmap tracks pending work only; historical feature lists are archived under `docs/archive/roadmap/`.

## Planning Docs

- Current completed state: `COMPLETED.md`
- Research synthesis: `RESEARCH.md`
- Legacy research: `RESEARCH_REPORT.md`
- Legacy roadmap archive: `docs/archive/roadmap/ROADMAP-legacy.md`
- Legacy feature-candidate archive: `docs/archive/roadmap/features.md`

## Current Baseline

- Current package version: v4.31.2.
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











### P0 - Trust and Remote-Control Correctness

- [ ] P0 - Restore secure LAN companion access
  Why: LAN mode binds to `0.0.0.0`, but Host validation only allows localhost, so legitimate remote devices are likely rejected while the UI says LAN access is enabled.
  Evidence: `streamkeep/local_server.py`, `streamkeep/ui/tabs/settings.py`, GitHub DNS rebinding guidance.
  Touches: `streamkeep/local_server.py`, `streamkeep/ui/tabs/settings.py`, `tests/test_local_server.py`
  Acceptance: local-only mode still rejects non-local Host headers; LAN mode accepts explicit configured LAN host/IP values; tests cover localhost, LAN IP, hostile Host, and Origin behavior.
  Complexity: M

- [ ] P0 - Fail closed on updater integrity metadata
  Why: Self-update accepts a downloaded executable when no `.sha256` asset exists, relying only on size sanity; executable replacement needs mandatory integrity verification.
  Evidence: `streamkeep/updater.py`, Microsoft MSIX signing docs, PyInstaller release-artifact practice.
  Touches: `streamkeep/updater.py`, `streamkeep/ui/tabs/download.py`, `tests/test_updater.py`, release packaging notes.
  Acceptance: update install is blocked when SHA-256 metadata is missing or malformed; UI explains the block; tests cover missing hash, bad hash, good hash, and cancelled download cleanup.
  Complexity: M

### P1 - Packaging, Secrets, and Test Coverage

- [ ] P1 - Make release packaging reproducible and complete
  Why: Packaging scaffolds exist, but `StreamKeep.spec` has empty data/runtime-hook lists and Flatpak ffmpeg uses a placeholder hash, so release artifacts can miss assets or be unreproducible.
  Evidence: `StreamKeep.spec`, `packaging/flatpak/com.github.SysAdminDoc.StreamKeep.yml`, `README.md`, PyInstaller hook docs, Microsoft MSIX signing docs.
  Touches: `StreamKeep.spec`, `assets/`, `browser-extension/`, `streamkeep/i18n/`, `packaging/msix/`, `packaging/flatpak/`, packaging README notes.
  Acceptance: clean packaging run includes icons, browser extension, translations, assets, and manifests; Flatpak source hashes are real; Windows artifact is signed when a cert is available; smoke launch proves one process and working icon/assets.
  Complexity: L

- [ ] P1 - Remove the silent base64 secrets fallback
  Why: `secrets.py` can store `b64:` values when keyring is unavailable, and `requirements.txt` does not install `keyring`, weakening non-Windows credential storage.
  Evidence: `streamkeep/secrets.py`, `requirements.txt`, Python keyring documentation.
  Touches: `requirements.txt`, `streamkeep/secrets.py`, `streamkeep/ui/tabs/settings.py`, `tests/test_accounts.py`
  Acceptance: source installs include `keyring`; secure-store failures surface a visible warning; new sensitive values are not stored as `b64:` unless the user explicitly chooses an insecure portable fallback.
  Complexity: M

- [ ] P1 - Add GUI and worker lifecycle smoke tests
  Why: Unit coverage improved, but startup, tab construction, dialogs, language switching, and long-running QThread signal lifetimes still have limited automated coverage.
  Evidence: `tests/`, `streamkeep/ui/main_window.py`, `streamkeep/ui/tabs/*.py`, pytest-qt documentation.
  Touches: `tests/`, `pytest.ini`, `streamkeep/ui/`, `streamkeep/workers/`
  Acceptance: pytest-qt smoke tests instantiate the app headlessly, visit each tab, open key dialogs, run representative worker success/failure signals, and pass without network or real ffmpeg downloads.
  Complexity: L

- [ ] P1 - Harden yt-dlp runtime readiness checks
  Why: yt-dlp handles long-tail platforms and now has platform-specific runtime needs such as JavaScript interpreters; StreamKeep should detect missing support before a user starts a long download.
  Evidence: `streamkeep/extractors/ytdlp.py`, `streamkeep/ui/onboarding.py`, yt-dlp documentation and release notes.
  Touches: `streamkeep/extractors/ytdlp.py`, `streamkeep/bootstrap.py`, `streamkeep/ui/onboarding.py`, `streamkeep/ui/tabs/settings.py`, tests.
  Acceptance: onboarding/settings show yt-dlp version and optional runtime status; unsupported runtime conditions produce actionable errors; tests cover version parsing and missing-runtime messaging.
  Complexity: M

### P2 - Platform Depth, I18n, and Extensibility

- [ ] P2 - Compile and ship real translation artifacts
  Why: `.ts` sources exist, but `available_languages()` only exposes `.qm` files and the repo ships no compiled translations.
  Evidence: `streamkeep/i18n/__init__.py`, `streamkeep/i18n/compile_translations.py`, Qt Linguist documentation.
  Touches: `streamkeep/i18n/`, packaging specs, settings language selector, tests.
  Acceptance: translation compile command produces `.qm` files; packaged builds include them; language selector lists Spanish when available; smoke test verifies switching language does not crash.
  Complexity: M

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
