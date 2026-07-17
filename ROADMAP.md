# StreamKeep Roadmap

StreamKeep is a Python/PyQt6 desktop downloader and archive manager for live streams, VODs, podcasts, and direct media URLs. This roadmap tracks pending work only; historical feature lists are archived under `docs/archive/roadmap/`.

## Planning Docs

- Current completed state: `COMPLETED.md`
- Research synthesis: `RESEARCH.md`
- Legacy research: `RESEARCH_REPORT.md`
- Legacy roadmap archive: `docs/archive/roadmap/ROADMAP-legacy.md`
- Legacy feature-candidate archive: `docs/archive/roadmap/features.md`

## Current Baseline

- Current package version: v4.37.0.
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

### P2 — Later

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

#### P2 — Later

- [ ] P2 — Add an operations view over queue, monitor, and failure state
  Why: Durable jobs and retries exist, but operators lack one filterable view of failed-only work, retry reasons, source health, totals, and next actions.
  Evidence: `streamkeep/db.py` queue/failed-job tables, `streamkeep/ui/tabs/download.py`, `ui/tabs/monitor.py`; Parabolic failed filtering and TubeSync task visibility.
  Touches: typed job/event queries, queue/monitor UI model, local server reads, thumbnails, batch actions and tests.
  Acceptance: Users can filter by state/source/stage, see batch count/duration/size estimates plus last success/next run/retry reason, retry or discard selected failures, and export a redacted report; 100,000 seeded jobs remain paged and responsive; state matches CLI/server reads after restart.
  Complexity: L

- [ ] P3 — Normalize button/label capitalization to one house style
  Why: Primary buttons and section labels mix Title Case (e.g. "Clear History", "Add Channel", "Load More VODs", "Download Selected") with Sentence case (e.g. "Recycle selected", "Rename selected", "Save profile", "Export Clip"). The Monitor and Download-VOD surfaces are almost entirely Title Case while dialogs are Sentence case, so the product reads as several design systems.
  Evidence: `streamkeep/ui/tabs/monitor.py`, `download.py`, `history.py`, `storage.py`, `settings*.py`, and the dialog modules; every literal is i18n-extracted, so a sweep must regenerate `streamkeep_en.ts`/`_es.ts` and recompile the `.qm`.
  Touches: UI string literals across all tabs/dialogs, `SPANISH_CORE` entries, translation catalog regeneration, GUI/i18n tests that assert specific labels.
  Acceptance: One documented convention (Sentence case, matching the newer dialogs) is applied consistently across every button/label; catalogs regenerated; i18n and GUI-smoke assertions updated.
  Complexity: M

