# StreamKeep Roadmap

StreamKeep is a Python/PyQt6 desktop downloader and archive manager for live streams, VODs, podcasts, and direct media URLs. This roadmap tracks pending work only; historical feature lists are archived under `docs/archive/roadmap/`.

## Planning Docs

- Current completed state: `COMPLETED.md`
- Research synthesis: `RESEARCH.md`
- Legacy research: `RESEARCH_REPORT.md`
- Legacy roadmap archive: `docs/archive/roadmap/ROADMAP-legacy.md`
- Legacy feature-candidate archive: `docs/archive/roadmap/features.md`

## Current Baseline

- Current package version: v4.33.0.
- The legacy F1-F80 roadmap has been implemented and is summarized in `COMPLETED.md`.
- Current architecture is modular: extractors, workers, post-processing, player, local server, SQLite library, plugin manager, upload adapters, intelligence helpers, and UI modules.
- History, monitor channels, and queue state live in SQLite; user preferences remain in JSON config.

## Active Roadmap

### 0. Versatility Program (2026-07-16 research — active drain queue)

Mission: any video or audio, from any website, in any format, at any quality the source offers, with full user control. See `RESEARCH.md` 2026-07-16 pass for the capability matrix and evidence. DRM circumvention is out of bounds throughout.

#### VP-P0 — Depth of control (yt-dlp passthrough + UI)

#### VP-P1 — Breadth (new source classes)

- [ ] V9 — Raw-protocol capture jobs (leapfrog)
  What: job types for RTSP (cameras, transport tcp/udp), RTMP-listen (receive OBS pushes), SRT caller/listener (+passphrase), UDP/RTP multicast (IPTV), ICY internet radio with now-playing capture and per-track splitting; ffmpeg reconnect family; duration caps; version-gate ffmpeg 8 options.
  Verify: real capture of a public RTSP/SRT test feed and an ICY radio stream with track split.
  Effort: L

- [ ] V10 — gallery-dl second engine
  What: route image/gallery/social-post URLs (Twitter media, Instagram posts, Pixiv, boorus) to optional gallery-dl with shared folder/archive config; graceful absent-dep messaging.
  Effort: M

- [ ] V11 — User-guided extraction (leapfrog; Downie-class)
  What: visible Playwright window; user navigates/logs in/plays; response sniffer surfaces manifests/media with variant picker; queue with captured request headers/cookies; refuse when EME/DRM session detected.
  Effort: L

- [ ] V12 — Extension network sniffer + header handoff
  What: MV3 webRequest capture of m3u8/mpd/media URLs + request headers on the active tab; one-click send-to-StreamKeep with full request context.
  Effort: M

- [ ] V13 — streamlink live engine (optional)
  What: in-process streamlink for Twitch/Kick live: mandatory ad-filtering, low-latency mode, DVR rewind (--hls-start-offset/--hls-live-restart), stream-up polling for monitors.
  Effort: L

- [ ] V14 — MSE buffer recorder (DRM-free only)
  What: Playwright init-script hook on SourceBuffer.appendBuffer teeing segments to disk; ffmpeg concat/remux; hard-refuse on any EME session; tab-open/playback-speed limitations documented.
  Effort: L

#### VP-P2 — Automation, lifecycle, and reach

- [ ] V15 — Rules engine (Packagizer-class): ordered match(site/uploader/title-regex/duration/type) → set folder/template/preset/priority/proxy/auto-start. Effort: L
- [ ] V16 — URL-pattern → profile auto-selection + zero-dialog Smart Mode toggle. Effort: M
- [ ] V17 — Quality-upgrade redownload pass + retention policies (delete after N days / after watched / keep-last). Effort: M
- [ ] V18 — Media-server output layouts per monitor (Jellyfin/Plex/Kodi S/E naming + NFO). Effort: M
- [ ] V19 — YouTube health doctor: Deno/EJS runtime detect, PO-token provider status, player_client strategy presets, degraded-capability warnings. Effort: M
- [ ] V20 — Pre-queue validation probe + multi-media picker responses (cobalt-style) in GUI and REST. Effort: M
- [ ] V21 — aria2c external-downloader routing with mandatory URL sanitization (CVE-2026-50574). Effort: S
- [ ] V22 — HAR import (media/manifest URLs + headers → link table). Effort: S-M
- [ ] V23 — streamkeep:// protocol handler + bookmarklet + documented iOS Shortcut. Effort: S
- [ ] V24 — Queue-complete power actions (notify/sleep/shutdown/run-hook). Effort: S
- [ ] V25 — lux fallback routing for CN platforms (bilibili/douyin/youku). Effort: M

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

## Definition of Done

- Active planning remains in this file.
- Shipped state is recorded in `COMPLETED.md` and `CHANGELOG.md`.
- Research and rationale are summarized in `RESEARCH.md`.
- Legacy planning artifacts stay archived and out of the repo root.

## Research-Driven Additions

### P0 — Now

### P1 — Next

- [ ] P1 — Build the intentionally large bundle reproducibly from an isolated manifest
  Why: Ambient-environment collection makes the 406 MiB artifact, licenses, and SBOM nondeterministic; size is acceptable, untraceable contents are not.
  Evidence: `StreamKeep.spec`, `requirements.txt`, `packaging/sbom.py`, Flatpak manifest, MSIX/Flatpak 4.31.4 versus app 4.31.5; PyInstaller reproducible-build guidance (https://pyinstaller.org/en/v6.5.0/advanced-topics.html) and pip repeatable installs (https://pip.pypa.io/en/latest/topics/repeatable-installs/).
  Touches: package metadata, locked/hash-checked full-bundle inputs, isolated build script, `StreamKeep.spec`, SBOM/license generation, MSIX/Flatpak metadata.
  Acceptance: A clean local environment builds the same declared capability set twice; dependency/browser/tool revisions are locked and hash-verified; SBOM and license inventory describe the frozen artifact; one version source stamps app/README/MSIX/Flatpak; Flatpak installs from valid package metadata; artifact smoke runs before release.
  Complexity: L

- [ ] P1 — Gate shipped capability claims on reachable integration paths
  Why: Numerous modules added on 2026-07-01 have unit tests but no GUI/CLI/server caller, so code existence is being mistaken for product completion.
  Evidence: `streamkeep/backup.py`, `disk_monitor.py`, `bandwidth.py`, `channel_stats.py`, `feed.py`, `gallery.py`, `notes.py`, `intelligence/`, `native_notify.py`, `plugins.py`, `integrations/sidecar_profiles.py`, `http.py`, `storage.py`; README and `COMPLETED.md` claims.
  Touches: capability registry, README/completed-state generation or validation, integration smokes, each affected entry point.
  Acceptance: Every claimed shipped capability has a user-reachable path and an end-to-end test that asserts its result; otherwise it is labeled experimental/unavailable and omitted from release claims; the release check fails on orphaned claims or unreachable actions.
  Complexity: M

- [ ] P1 — Restore full keyboard and assistive-technology operation
  Why: All tables explicitly reject focus and no controls expose explicit accessible names/descriptions, buddy labels, tab order, or async status announcements.
  Evidence: `streamkeep/ui/widgets.py:530-535`; zero accessibility API calls across UI/player; WCAG 2.2 and Qt accessibility guidance.
  Touches: shared widgets, all major tabs/dialogs, custom-painted calendar/storage/analytics/waveform controls, status messaging, accessibility tests.
  Acceptance: Core URL-to-download, monitor, history, storage, settings, clip, and recovery flows complete keyboard-only; tables retain focus/navigation; names/roles/states and progress/error announcements are exposed; focus is visible and unobscured; 200% scaling and system high-contrast smoke tests have no clipped controls or color-only state.
  Complexity: L

- [ ] P1 — Page large archives through Qt model/view and database queries
  Why: Loading all history rows, cell widgets, and thumbnail work cannot scale to the large archives the product targets.
  Evidence: `streamkeep/db.py::load_history`, `streamkeep/ui/tabs/history.py`; Qt model/view `canFetchMore`/`fetchMore`; refines the existing “Deeper library views for very large archives” item.
  Touches: history/queue/monitor/storage repositories, `QAbstractTableModel` implementations, proxy filters/sorts, thumbnail loader, database indexes and query tests.
  Acceptance: A seeded 100,000-row library opens and filters without material UI stalls or unbounded widget/thumbnail creation; queries use indexed sort/filter plus stable pagination; only visible/near-visible thumbnails are scheduled and stale work is cancelled; selection/actions survive page changes.
  Complexity: L

- [ ] P1 — Complete Qt internationalization instead of loading inert catalogs
  Why: The translator is installed, but hundreds of UI strings bypass translation and the 23-message catalogs cannot produce a translated workflow.
  Evidence: `streamkeep/i18n.py`, translation catalogs, UI/player scan with zero `tr()`/`translate()` calls; Qt localization guidance.
  Touches: all UI/player strings, `lupdate`/`lrelease` build flow, plural/context handling, runtime retranslation, translation tests.
  Acceptance: User-facing strings are extractable; switching language retranslates the live shell and dialogs without restart; Spanish covers the core URL/download/history/settings/error flows; plural and dynamic-status strings use contexts; a pseudo-locale catches clipping; frozen builds contain matching `.qm` files.
  Complexity: L

- [ ] P1 — Consolidate and activate the visual-system controls
  Why: High-contrast, density, and accent APIs are dead code while duplicate legacy/active styles and per-tab dimensions keep visual behavior inconsistent.
  Evidence: `streamkeep/theme.py:45-79,1049`, `streamkeep/ui/widgets.py`, fixed-size/fixed-width usage across UI modules; offscreen dark/light/high-contrast render audit on 2026-07-15.
  Touches: `streamkeep/theme.py`, shared widgets/layout metrics, Settings appearance controls, major tabs/dialogs, screenshot tests.
  Acceptance: One token/component stylesheet owns type, spacing, borders, density, focus, and states; System/Dark/Light/High Contrast plus density/accent settings are reachable and persistent; no fixed dimensions clip at the supported minimum or 200% scale; screenshot matrices detect theme/layout regressions.
  Complexity: M

### P2 — Later

- [ ] P2 — Expose archive maintenance as one dry-run-first workflow
  Why: Reconcile/import, backup, disk alerts, notes, bandwidth, and channel statistics exist separately but were not reachable as an archive-maintenance workflow on 2026-07-15.
  Evidence: dormant modules `streamkeep/storage.py`, `backup.py`, `disk_monitor.py`, `notes.py`, `bandwidth.py`, and `channel_stats.py`; Tube Archivist/Pinchflat archive operations.
  Touches: Storage/History/Analytics/Settings surfaces, maintenance coordinator, background workers, SQLite, integration tests.
  Acceptance: A maintenance screen previews disk-to-library imports, missing/orphaned/moved files, backup/integrity status, disk thresholds, and index/stat rebuild effects; user approval applies an auditable batch; cancellation/restart is safe; no sidecar or history data is overwritten silently.
  Complexity: XL

- [ ] P2 — Complete authenticated gallery and RSS publishing
  Why: Generators and registry code exist without persisted sharing lifecycle or server routes, so advertised local publishing is unreachable.
  Evidence: `streamkeep/gallery.py`, `streamkeep/feed.py`, `streamkeep/local_server.py`; Pinchflat RSS and self-hosted archive patterns; depends on the existing gallery-ID entropy item and safe LAN boundary.
  Touches: database share state, gallery/feed routes, History actions, local server auth, URL rendering, tests.
  Acceptance: Users explicitly share/unshare selected recordings or feeds; state survives restart; authenticated routes stream only canonical allowed paths; feed URLs and enclosure metadata validate; revocation is immediate; traversal, enumeration, stale-file, and LAN-origin tests pass.
  Complexity: L

- [ ] P2 — Complete secure upload and media-server export delivery
  Why: Upload workers and sidecar profiles have no finalization call path, plain FTP remains the only FTP-family adapter, and README claims SFTP that is not implemented.
  Evidence: `streamkeep/upload/`, `streamkeep/integrations/sidecar_profiles.py`, download finalization; ytdl-sub and Pinchflat media-server conventions; existing FTP filename-validation item is prerequisite.
  Touches: finalize pipeline, destination configuration/secure store, SFTP or FTPS adapter, retry ledger, sidecar validation, media-server refresh hooks, tests.
  Acceptance: A completed job can generate a previewed Jellyfin/Plex/Kodi/Emby layout and then upload with persisted progress/retry; SFTP verifies host keys (or FTPS verifies certificates); credentials never reach logs; interrupted transfers resume or fail without false completion; plain FTP is explicitly insecure and disabled by default.
  Complexity: XL

- [ ] P2 — Wire summaries and smart thumbnails with explicit data-boundary consent
  Why: Intelligence workers are unreachable, and enabling cloud summaries without per-run disclosure would violate the local-first trust model.
  Evidence: `streamkeep/intelligence/summarize.py`, `streamkeep/intelligence/thumbnail.py`; local video-search/community demand and commercial AI paywall signals.
  Touches: History/player actions, provider configuration/secure store, local model capability checks, worker cancellation/progress, metadata persistence, privacy tests.
  Acceptance: Local processing is the default; before any cloud request the UI names the provider and exact transcript payload, requires explicit consent, and offers redaction/cancel; results record provider/model/version and remain editable/rebuildable; thumbnails preserve originals and enforce Pillow resource limits.
  Complexity: L

- [ ] P2 — Define tested plugin adapters beyond extractor discovery
  Why: Documentation claims extractor, post-process, and upload extension points, but only extractor subclass loading has a demonstrated contract.
  Evidence: `streamkeep/plugins.py`, plugin example/tests, Streamlink plugin contracts; depends on the existing plugin namespace-isolation item.
  Touches: plugin manifest/schema, extractor/post-process/upload adapter interfaces, lifecycle/capability broker, diagnostics, sample plugins and contract tests.
  Acceptance: Each adapter type has a versioned interface, declared permissions/dependencies, timeouts/cancellation, typed outcomes, compatibility diagnostics, and a minimal sample test; unsupported manifest versions fail closed; no plugin directory is appended globally to `sys.path`.
  Complexity: L

- [ ] P2 — Parse WebVTT and Podcast Namespace transcript/chapter metadata correctly
  Why: Regex-only WebVTT indexing rejects valid minute-only timestamps and loses cue structure, while standardized podcast transcripts/chapters are not imported into the existing search/player model.
  Evidence: `streamkeep/search.py:111-137`, podcast extractor/feed code; W3C WebVTT (https://www.w3.org/TR/webvtt1/) and Podcast Namespace 1.0 (https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md).
  Touches: transcript parser/indexer, podcast extraction, sidecar downloader, search, player chapters, fixtures.
  Acceptance: Valid `MM:SS.mmm` and `HH:MM:SS.mmm` cues, identifiers/settings/markup, malformed-cue isolation, transcript/chapter URLs, hashes, language, and refresh behavior are covered by fixtures; indexed hits jump to the correct timestamp offline.
  Complexity: M

### P3 — Under Consideration

- [ ] P3 — Add optional local semantic moment search after pagination lands
  Why: Exact transcript FTS is valuable but cannot find visually or semantically related moments; local hybrid retrieval could differentiate StreamKeep without a cloud index.
  Evidence: existing `streamkeep/search.py`; WISE multimodal retrieval research (https://arxiv.org/abs/2602.12819); DataHoarder demand for searchable local video archives.
  Touches: rebuildable local index schema, scene/OCR/audio embedding workers, search ranking/UI, resource controls, privacy/export tests.
  Acceptance: Users explicitly opt in per library; FTS remains available without models; local-only indexing returns timestamped transcript/scene/OCR results with provenance and confidence; indexes are cancellable, size-bounded, versioned, rebuildable, and excluded from portable backups by default.
  Complexity: XL

### 2026-07-16 Scope Corrections to Existing Items

- Audit-deferred base64 fallback: new secrets already fail closed because `allow_insecure_fallback` defaults to false; scope the item to deleting or explicitly labeling legacy/test-only fallback paths.
- Plugin isolation: the plugin parent is appended, not prepended, so the remaining work is eliminating global `sys.path` mutation and containing trusted in-process execution, not a proven current stdlib-prepend shadow.
- Browser clip handoff: manifest permissions are now exact loopback origins; retain the item for the unconnected `clip_received` desktop signal and end-to-end pairing/clip validation only.
- Secure upload delivery: SFTP exists through Paramiko; replace the stale “not implemented” premise with mandatory known-host verification, certificate-verified FTPS/WebDAV HTTPS, and explicit disabling of insecure FTP/HTTP defaults.

#### P0 — Now

#### P1 — Next

- [ ] P1 — Make one versioned DownloadJobSpec authoritative across every surface
  Why: Seven construction sites manually copy a mutable worker property bag, causing GUI/CLI/headless/monitor/queue/resume option drift and making V5/V6 riskier.
  Evidence: `streamkeep/workers/download.py:39-83`; constructors in `cli.py`, `headless_service.py`, `ui/main_window.py`, `ui/tabs/download.py`, and `ui/tabs/monitor.py`.
  Touches: shared models/options, command planning, queue DB payloads, resume migration, all job builders, fixtures.
  Acceptance: An immutable schema-versioned job spec is built and validated once, serializes without secrets, rejects unsupported future versions, migrates existing queue/resume payloads, and produces equivalent sanitized command plans from GUI, CLI, REST/headless, and monitor fixtures; workers receive the spec instead of field-by-field mutation.
  Complexity: L

- [ ] P1 — Split portable and MSIX build/update contracts
  Why: The tracked spec produces one-file output, the MSIX builder requires a directory, and the updater self-replaces `sys.executable` even though MSIX content is package-managed.
  Evidence: `StreamKeep.spec`, `packaging/msix/build_msix.py`, `streamkeep/updater.py`; https://learn.microsoft.com/en-us/windows/msix/app-installer/auto-update-and-repair--overview.
  Touches: PyInstaller targets, MSIX/App Installer manifests, updater/update runtime, signing/SBOM/version stamping, artifact smokes.
  Acceptance: Portable EXE retains signed last-known-good self-replacement; MSIX is built from an explicit signed onedir target and updates only through signed App Installer/Store semantics; each artifact reports its channel, refuses the other's updater, and passes clean install, upgrade, interrupted-update, repair, rollback, and uninstall smoke tests.
  Complexity: L

- [ ] P1 — Add live credential and cookie-profile validation
  Why: Settings currently reports stored-value presence as “authenticated,” so private/age-restricted failures are discovered only after queueing and are hard to distinguish from extractor/network failure.
  Evidence: `streamkeep/accounts.py::credential_status`, `streamkeep/ui/tabs/settings.py::_update_cookies_status`; https://github.com/JunkFood02/Seal/issues/2026; https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp; extends V19/V20 without replacing them.
  Touches: account/cookie services, platform-specific safe probes, Settings and pre-queue status, diagnostics, secret-redaction tests.
  Acceptance: A cancellable non-downloading probe reports valid, expired/revoked, insufficient scope, rate-limited, unsupported, or network failure for configured Twitch/YouTube/Kick/cookie profiles; it records only redacted time/status metadata, never secrets or signed URLs, and V20 can surface the result before queueing while offline operation remains available.
  Complexity: M

#### P2 — Later

- [ ] P2 — Add an operations view over queue, monitor, and failure state
  Why: Durable jobs and retries exist, but operators lack one filterable view of failed-only work, retry reasons, source health, totals, and next actions.
  Evidence: `streamkeep/db.py` queue/failed-job tables, `streamkeep/ui/tabs/download.py`, `ui/tabs/monitor.py`; Parabolic failed filtering and TubeSync task visibility.
  Touches: typed job/event queries, queue/monitor UI model, local server reads, thumbnails, batch actions and tests.
  Acceptance: Users can filter by state/source/stage, see batch count/duration/size estimates plus last success/next run/retry reason, retry or discard selected failures, and export a redacted report; 100,000 seeded jobs remain paged and responsive; state matches CLI/server reads after restart.
  Complexity: L

- [ ] P2 — Normalize YouTube live-chat replay into the existing chat pipeline
  Why: StreamKeep captures Twitch/Kick chat and Twitch VOD replay, but YouTube replay cannot feed its chat highlights, overlay renderer, or archive exports.
  Evidence: `streamkeep/chat/`, `streamkeep/extractors/twitch.py`, `workers/finalize.py`; imsyy/yt-dlp-gui YouTube live-chat replay export.
  Touches: yt-dlp subtitle/chat extraction, normalized chat model, finalize/resume, JSONL/CSV export, filters and fixtures.
  Acceptance: Eligible YouTube VODs can opt into bounded, cancellable replay capture; timestamps, author/channel-owner/member flags, message text, and supported emotes normalize to `chat.jsonl`; regex/user filters and CSV export work; partial/unavailable replay is non-fatal; existing spike/highlight/render tools consume the result unchanged.
  Complexity: M

- [ ] P2 — Add bilingual subtitle merge and LRC export
  Why: The current SRT/VTT/ASS workflow lacks two concrete archive/listening outputs already exposed by a comparable yt-dlp GUI.
  Evidence: `streamkeep/download_options.py`, `workers/download.py`, subtitle UI; imsyy/yt-dlp-gui bilingual subtitle and LRC features; current WebVTT recommendation.
  Touches: subtitle parser/model, post-processing, per-job UI/CLI/job spec, sidecar naming, fixtures.
  Acceptance: Users select primary/secondary tracks and a deterministic overlap/alignment policy; merged SRT/ASS preserves language labels and cue order; LRC emits validated monotonic timestamps for audio; malformed cues isolate rather than abort the download; originals are retained by default and outputs round-trip Unicode in frozen builds.
  Complexity: M
