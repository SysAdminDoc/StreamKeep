# StreamKeep Roadmap

StreamKeep is a Python/PyQt6 desktop downloader and archive manager for live streams, VODs, podcasts, and direct media URLs. This roadmap tracks pending work only; historical feature lists are archived under `docs/archive/roadmap/`.

## Planning Docs

- Current completed state: `COMPLETED.md`
- Research synthesis: `RESEARCH.md`
- Legacy research: `RESEARCH_REPORT.md`
- Legacy roadmap archive: `docs/archive/roadmap/ROADMAP-legacy.md`
- Legacy feature-candidate archive: `docs/archive/roadmap/features.md`

## Current Baseline

- Current package version: v4.44.0.
- The legacy F1-F80 roadmap has been implemented and is summarized in `COMPLETED.md`.
- Current architecture is modular: extractors, workers, post-processing, player, local server, SQLite library, plugin manager, upload adapters, intelligence helpers, and UI modules.
- History, monitor channels, and queue state live in SQLite; user preferences remain in JSON config.

## Active Roadmap

### 0. Versatility Program (2026-07-16 research — active drain queue)

Mission: any video or audio, from any website, in any format, at any quality the source offers, with full user control. See `RESEARCH.md` (2026-07-29) for the current evidence synthesis; dated notes below preserve their own scoped evidence. DRM circumvention is out of bounds throughout.

#### VP-P0 — Depth of control (yt-dlp passthrough + UI)

#### VP-P1 — Breadth (new source classes)

#### VP-P2 — Automation, lifecycle, and reach


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


#### P2 — Later

### 2026-07-18 Research-Driven Additions

#### P0 — Now

#### P1 — Next

#### P2 — Later

#### P3 — Under Consideration

- [ ] P3 — Add responsive web remote UI for mobile access
  Why: Community signal shows mobile access is the #1 feature request across Parabolic (#1694), TubeArchivist, and Pinchflat. A native mobile app is rejected (second codebase), but a responsive authenticated web remote served from StreamKeep's existing local server would provide queue management, status monitoring, and basic library browsing from any device on the LAN.
  Evidence: Parabolic #1694 (Android app request — most-upvoted); TubeArchivist mobile web; yt-dlp-web-ui responsive design; cobalt mobile-first web UI.
  Touches: `streamkeep/local_server.py`, new static HTML/CSS/JS assets, existing authentication and pairing infrastructure.
  Acceptance: The web remote is responsive (mobile-first); authenticated via the existing pairing/token system; provides URL submission, queue status, active download monitoring, and basic library browsing; does not require any native mobile app installation; works over HTTPS reverse proxy for LAN access.
  Complexity: L
  > 2026-07-29: Core status, queue, library, monitor, resumable, and failure views already ship as an embedded viewport-aware SPA. Remaining scope is V45 first, then real mobile-browser accessibility, focus/error/loading states, narrow-width overflow, and HTTPS reverse-proxy smoke—not a second web UI. (RESEARCH.md 2026-07-29)

### 2026-07-20 Research-Driven Additions

Note: The 2026-07-18 pass's security/dependency items are verified CLOSED in v4.41.0 (SFTP host-key verify, gallery Range caps, `_shared` lock, lazy BandwidthTracker, channel_stats try/finally, aria2c HLS/DASH gating, Python 3.11 floor, cryptography 49.0.0, pillow 12.3.0, `pyqt6-qt6==6.11.1` past CVE-2026-6210). Do not re-open. New IDs continue the V-scheme (highest prior = V25).

#### P1 — Next

#### P2 — Later

#### P3 — Under Consideration

- [ ] P3 — Auto-translate embedded metadata & chapters to the app language
  Why: Parabolic 2026.5.0 ships this as a visible polish differentiator for non-English archives; StreamKeep writes metadata/NFO/chapters but never localizes titles/descriptions/chapter names.
  Evidence: Parabolic 2026.5.0 release notes; `streamkeep/metadata.py` NFO/chapter writer; existing i18n (en/es) + `whisper_model`/`hf_token` config already present.
  Touches: metadata/NFO/chapter writer, optional translation backend (local-first, opt-in, consent-gated like summaries), Settings, tests.
  Acceptance: Users can opt in to translate embedded title/description/chapter text to the configured app language; originals are preserved alongside translations; local-first default; no cloud call without per-run disclosure and consent; off by default.
  Complexity: M


### 2026-07-27 Research-Driven Additions

Note: v4.42.0 shipped the prior pass's top items (disk-health alerts + native notifications wired, SSRF policy on REST-submitted URLs, SponsorBlock-delay heuristic); v4.43.0 added gallery-dl/lux engines; v4.43.1/v4.43.2 fixed live-capture keep-on-Stop and the `adv_override_badge` download crash. All dependencies meet 2026 CVE floors (no new security item). Qt CVE-2026-15037 is N/A (no QtXml/QDom usage). SponsorBlock mark-mode, configurable subtitles, and emote-aware chat render already exist — see RESEARCH.md "Rejected Ideas". New IDs continue the V-scheme (highest prior = V32).

#### P1 — Next

#### P2 — Later

#### P3 — Under Consideration

- [ ] P3 — V42 — yt-dlp stable/nightly update channel toggle
  Why: YouTube fixes land in yt-dlp nightly days before stable; power users want to opt into nightly for the fast-moving platforms without waiting for a StreamKeep release. StreamKeep bundles a frozen yt-dlp.
  Evidence: https://github.com/alexta69/metube/releases (nightly toggle); `streamkeep/capabilities.py` (`resolve_command_prefix`), `streamkeep/updater.py`.
  Touches: capability resolution to allow a user-supplied/updatable yt-dlp, a stable/nightly channel setting, health surfacing of the active yt-dlp version/channel, Settings, tests.
  Acceptance: a setting lets the user point at an external/nightly yt-dlp or self-update the bundled one; the health panel shows the active version and channel; the bundled frozen yt-dlp remains the default; switching channels is reversible.
  Complexity: M
  > 2026-08-02: yt-dlp 2026.07.04 is still the newest published release, so the pinned lock is current and the `Roadmap_Blocked.md` lock-bump entry remains the prerequisite. Any external/nightly binary must resolve through `capabilities.resolve_command_prefix` so a below-floor yt-dlp cannot enter download paths, and must be version-probed rather than trusted. (RESEARCH.md 2026-08-02)

- [ ] P3 — V43 — gallery-dl image-set ingest into the local gallery (CBZ + sidecar metadata)
  Why: gallery-dl is already an integrated engine but only downloads to disk; its image sets and `info.json` sidecars never enter StreamKeep's library/local gallery, and comic/manga sites benefit from CBZ packaging.
  Evidence: `streamkeep/integrations/gallery_dl.py`, `streamkeep/gallery.py`, `streamkeep/db.py`; https://codeberg.org/mikf/gallery-dl/releases (metadata/CBZ postprocessors).
  Touches: gallery-dl postprocessor config (CBZ/ZIP + `info.json`), library ingest of image sets, local gallery rendering of non-video media, tests.
  Acceptance: a gallery-dl job optionally packages an image set as CBZ/ZIP with sidecar metadata and registers it in the library so the local gallery lists it; video-only assumptions in the gallery are removed; a unit test covers ingest of a multi-image set.
  Complexity: M


### 2026-07-29 Research-Driven Additions

#### P1 — Next

#### P2 — Later

- [ ] P2 — V53 — Produce and smoke-test unsigned macOS and portable Linux artifacts
  Why: source-level platform claims are not release proof; Windows has real artifact smoke, while macOS has no package and Flatpak/MSIX validation is mostly static.
  Evidence: current packaging tree; Open Video Downloader, Parabolic, and Media Downloader release matrices; V35 remains the separate Windows lane.
  Touches: target-specific PyInstaller/packaging definitions, macOS x64/arm64 bundles, portable Linux artifact plus Flatpak install smoke, release manifests/checksums/SBOM, target-host smoke scripts and docs.
  Acceptance: native target hosts build unsigned macOS x64 and arm64 app bundles and at least one portable Linux artifact; each launches against empty, migrated, and populated profiles, resolves bundled runtime paths, opens the desktop, and exits cleanly; hashes/SBOMs are published and support claims name only proven targets; no code signing, notarization, or signing gate is added.
  Complexity: L

- [ ] P2 — V54 — Follow the OS contrast preference when System theme is selected
  Why: StreamKeep ships a tested High Contrast theme, and pinned Qt 6.11 exposes live accessibility contrast hints, but System mode never consumes them.
  Evidence: `streamkeep/theme.py`, appearance settings, `tests/test_accessibility.py`; Qt `QAccessibilityHints::contrastPreference` available since Qt 6.10; WCAG 2.2.
  Touches: theme/system-preference observer, main-window theme refresh, Appearance microcopy, signal-driven tests.
  Acceptance: System theme enters/exits StreamKeep High Contrast when the OS contrast preference changes without restart; explicit Dark, Light, or High Contrast selections remain sticky and ignore later system changes; focus, disabled, error, selection and chart colors remain distinguishable at 100% and 200% scale; tests simulate both signal directions.
  Complexity: S
  > 2026-08-02: Confirmed feasible on the pinned runtime — `QStyleHints::accessibility` and the `QAccessibilityHints` class landed in Qt 6.10, and the lock pins `pyqt6-qt6==6.11.1`. The same hints object also exposes a reduce-motion preference, which should gate the shell's shimmer/hover-lift animations in the same change. Screen-reader role fixes are separate work (V86). (RESEARCH.md 2026-08-02)

## Audit Findings — 2026-08-02

Deep audit pass on v4.44.0. Baseline captured first: `1293 passed, 113 subtests` (`py -3.12 -m pytest tests/`), pyflakes clean, ruff reports 50 style-only items (42 `E402` launcher-import ordering, plus test-file dead imports — see V64). New IDs continue the V-scheme (highest prior = V54). Every item below was traced to a reachable path and confirmed against current source; confidence is stated per item. No code was changed in this pass.

### Unaudited — needs a dedicated pass

The 2026-08-02 audit prioritized security/subprocess boundaries, the download/worker/data layer, and UI theme/UX/a11y, tracing across module boundaries and running the suite/linters to confirm findings (the GUI was traced from source, not driven live — no display was launched). The following larger modules received only a smell-level sweep and warrant their own pass: `streamkeep/cli.py` (~67 KB), `streamkeep/headless_service.py` (~42 KB) beyond its SSRF entry point, `streamkeep/download_options.py`, `streamkeep/job_spec.py`, `streamkeep/maintenance.py`, `streamkeep/lifecycle.py`, `streamkeep/tags.py`, `streamkeep/metadata.py` internals, and the `streamkeep/player/` package interactions. Performance was spot-checked and found clean (prior passes removed the known O(n²) hotspots); a dedicated profiling pass over large-archive History/Storage/Analytics rendering with 100k+ rows has not been done.

> 2026-08-02 research pass: the `download_options.py` sweep above did surface a confirmed finding — see V67. `Roadmap_Blocked.md`'s V29 entry (ffmpeg-8 TLS-verify toggle) has a **stale blocker**: it was deferred pending V9 raw-protocol capture, V9 has shipped, and `README.md:164` already documents opt-in self-signed TLS for RTSPS/RTMPS. Verify and close that entry rather than carrying it.

### 2026-08-02 Research-Driven Additions

Evidence synthesis in `RESEARCH.md` (2026-08-02). Baseline measured after `a3c7317`: v4.44.0, `1390 passed, 115 subtests` at 59.63% coverage against a 47.5% floor, pyflakes clean, 43 ruff findings (all `E402` launcher-import ordering). The 2026-07-29 P0 set is verified closed in source — remote-manifest scheme confinement, the execution lease, provenance separation and browser pairing all ship; do not re-open them. New IDs continue the V-scheme (highest prior = V66).

#### P0 — Now

#### P1 — Next

- [ ] P1 — V71 — Set `Secure` on the session cookie and pin the companion extension origin
  Why: the bearer token is written to a cookie with `HttpOnly; SameSite=Strict` but no `Secure`, on every response including errors, while LAN mode is explicitly HTTPS-only; and `_origin_ok` accepts *any* `chrome-extension://`/`moz-extension://` origin, so every installed extension satisfies the origin gate.
  Evidence: `streamkeep/local_server.py:914-917` (`_set_auth_cookie`, called from `_json_response`/`_html_response`/`_bytes_response`), `:421-426` (HTTPS-only external origin validation), `:640-641` and `:409` (extension-origin shape check only).
  Touches: `streamkeep/local_server.py`, `streamkeep/ui/tabs/settings_companion.py` (store the paired extension id), `tests/test_local_server.py`.
  Acceptance: the session cookie carries `Secure` whenever the request arrived through the configured HTTPS origin, and the attribute is asserted in tests; pairing records the companion extension's own id and later requests from a different extension origin are rejected even with a valid token; the shipped extension still pairs and operates end to end.
  Complexity: S

- [ ] P1 — V72 — Create secret-store temp files at 0600 and make portable-secret restore transactional
  Why: both secret writers create the temp file under the default umask, write the payload, `os.replace`, then `chmod 0o600` — so the DPAPI blob and the encrypted portable envelope are mode 0644 for their whole lifetime on POSIX; and a restore failure at the cookie step leaves the credential store half-overwritten with no rollback.
  Evidence: `streamkeep/secrets.py:146-159` (`_write_local_store`), `streamkeep/portable_secrets.py:193-202` (`_write_atomic`), `streamkeep/portable_secrets.py:111-121` (restore order: config → per-account → cookies, returning `False` after partial application).
  Touches: `streamkeep/secrets.py`, `streamkeep/portable_secrets.py`, `tests/test_secrets.py`, `tests/test_portable_secrets.py`.
  Acceptance: temp files are created with `os.open(..., O_CREAT|O_EXCL|O_WRONLY, 0o600)` so the payload is never world-readable at any point; restore stages every credential and cookie change and commits atomically, leaving the prior store completely intact on any failure; a test injects a cookie-write failure and asserts the pre-restore store is byte-identical afterwards.
  Complexity: S

- [ ] P1 — V77 — Persist a canonical media identity and canonicalize URLs before keying
  Why: nothing carries a stable `(platform, source_id)` through VOD → job → history, and archive keys derive from raw URLs, so the same video under three URL forms is three items. This value is the shared prerequisite for V78, V79, V80, V82 and V83 — build it first.
  Evidence: `streamkeep/models.py`, `streamkeep/job_spec.py`, `streamkeep/db.py` history schema, `streamkeep/extractors/ytdlp.py:853` (`--download-archive`); browsertrix normalizes scheme, `www` and query order before dedupe (https://crawler.docs.browsertrix.com/user-guide/crawl-scope/); the 2026-07-29 pass identified the same missing value in the monitor upgrade path.
  Touches: `streamkeep/models.py`, `streamkeep/job_spec.py`, `streamkeep/db.py` (schema migration), `streamkeep/extractors/*`, `streamkeep/utils.py`, `streamkeep/metadata.py` sidecar schema, `tests/test_db.py`, `tests/test_extractors.py`, `tests/test_job_spec.py`.
  Acceptance: every extractor returns a canonical `(platform, source_id)` and a canonical page URL; both are persisted on the job, the history row and the `metadata.json` sidecar; a migration back-fills existing rows where a source id is recoverable and marks the rest unknown rather than guessing; URL canonicalization (scheme, host case, `www`, tracking and ordering of query parameters) runs before any archive or dedupe key is computed; a test asserts three URL forms of one video resolve to one identity.
  Complexity: M

- [ ] P1 — V78 — Record deletion tombstones so deliberately removed media is never re-fetched
  Why: nothing in the schema distinguishes "not downloaded yet" from "downloaded and deliberately deleted", so recycling a recording makes a monitored channel or playlist re-acquire it on the next pass. Two competitor trackers carry this as an unresolved request.
  Evidence: no `deleted` state in `streamkeep/db.py`; https://github.com/kieraneglin/pinchflat/issues/805, https://github.com/meeb/tubesync/issues/431; depends on V77.
  Touches: `streamkeep/db.py` (schema migration), `streamkeep/ui/tabs/history.py` and `storage.py` delete paths, `streamkeep/lifecycle.py`, `streamkeep/monitor.py`, `streamkeep/workers/playlist.py`, `streamkeep/ui/tabs/settings.py`, `tests/test_db.py`, `tests/test_monitor.py`.
  Acceptance: deleting a recording records a tombstone keyed on the canonical identity with a timestamp and reason (user, retention, lifecycle); monitor, playlist expansion and queue dispatch skip tombstoned identities and say so in the log; tombstones are listable, individually clearable, survive backup/restore, and lifecycle-driven deletions are distinguishable from user deletions so retention pruning does not permanently blacklist a channel's back catalogue.
  Complexity: M

- [ ] P1 — V79 — Adopt an existing library: folder tree, download archives, and other tools' state
  Why: StreamKeep can acquire almost anything but cannot adopt anything — `storage.import_folders` only claims orphans under the configured root. No surveyed tool ingests a `yt-dlp --download-archive`, another tool's database, or an arbitrary tree, and Pinchflat's maintainer pause has stranded a large userbase that is actively shopping.
  Evidence: `streamkeep/storage.py:167` (`import_folders`), `streamkeep/maintenance.py:130` (`plan_maintenance` import candidates); https://github.com/DialmasterOrg/Youtarr/issues/531; https://github.com/kieraneglin/pinchflat/issues/800 (254 reactions); https://github.com/jmbannon/ytdl-sub/issues/1483; depends on V77.
  Touches: new import module, `streamkeep/db.py`, `streamkeep/storage.py`, `streamkeep/maintenance.py`, `streamkeep/cli.py`, `streamkeep/ui/tabs/storage.py`, `streamkeep/metadata.py` sidecar readers, tests.
  Acceptance: a preview-then-apply importer accepts an arbitrary directory tree, a `yt-dlp --download-archive` file, and `.info.json`/NFO sidecars, mapping each to a canonical identity; a dry-run report lists adopt / skip / conflict per item with a reason before anything is written; adoption never moves, renames, re-encodes or re-downloads a file; imported archive ids seed the per-source download archive so monitored sources do not re-acquire adopted media; conflicts (two files claiming one identity) go to an explicit review list rather than being resolved silently; interrupted imports change nothing and can be previewed again.
  Complexity: L

- [ ] P1 — V80 — Rebuild the SQLite index from on-disk sidecars
  Why: the database is authoritative and there is no path from disk back to it, which is exactly the lock-in users have been burned by. Making the filesystem the source of truth and the DB a rebuildable cache is also the property that makes V79 tractable.
  Evidence: `streamkeep/verify.py:181` (`create_archive_manifest`) and `:232` (sidecar loader) already write per-recording manifests; `streamkeep/metadata.py` writes `metadata.json`/NFO; ArchiveBox's "all data is readable without needing to run ArchiveBox" contract (https://github.com/ArchiveBox/ArchiveBox); TubeArchivist v0.5.9 rebuilds its index from embedded file metadata; depends on V77.
  Touches: `streamkeep/db.py`, `streamkeep/verify.py`, `streamkeep/metadata.py` (versioned sidecar schema), `streamkeep/maintenance.py`, `streamkeep/cli.py` (`db rebuild`), `tests/test_db.py`, `tests/test_verify.py`, `tests/test_maintenance.py`.
  Acceptance: `python StreamKeep.py db rebuild --from <root>` reconstructs history rows, tags, integrity manifests and archive keys from sidecars alone; sidecars carry a schema version so older ones migrate rather than fail; the rebuild is a preview-then-apply operation that writes a backup first and never deletes media; a test writes a library, drops the database, rebuilds, and asserts history, tags and manifest state match; anything not reconstructible from disk is enumerated explicitly rather than silently lost.
  Complexity: L

- [ ] P1 — V81 — Refresh an expired manifest or token mid-capture instead of failing the job
  Why: there is no 403/410 handling anywhere in the download worker, so an expired Usher/playlist token — which Twitch and Kick rotate on the order of hours — ends a long live capture as a failure rather than a continuation. Streamlink treats playlist reload and segment retry as first-class, defaulted behavior.
  Evidence: no `403`/`410`/re-resolve handling in `streamkeep/workers/download.py`; `streamkeep/hls.py:415-433` already invalidates a resume across a discontinuity change, so the media-sequence bookkeeping to resume correctly exists; https://streamlink.github.io/cli.html (`--hls-playlist-reload-attempts`, `--hls-segment-attempts`, `--stream-timeout`); https://github.com/yt-dlp/yt-dlp/issues/13650.
  Touches: `streamkeep/workers/download.py`, `streamkeep/hls.py`, `streamkeep/dash.py`, `streamkeep/extractors/twitch.py` and `kick.py` (re-resolve entry points), `streamkeep/resume.py`, `tests/test_download_worker.py`, `tests/test_live_capture.py`.
  Acceptance: a 403/410 on a segment or playlist reload triggers a bounded re-resolve of the source and a rebuild of the media playlist URL, continuing the same recording and the same resume sidecar rather than starting a new job; the retry budget is capped and jittered and gives up with a named reason; a discontinuity or codec change across the refresh is recorded so the finish step can seam correctly; a test serves 403 mid-capture and asserts one continuous output plus a logged refresh.
  Complexity: M

- [ ] P1 — V82 — Quality-upgrade decision engine with recorded reasons and versioned replacement
  Why: the monitor compares an upgrade candidate against the channel's latest history row rather than the same media item, records no decision, and has no undo. This is the one feature that can destroy an archive, so the decision log and the versioning must land with it, not after.
  Evidence: `streamkeep/ui/tabs/monitor.py:1415-1442` (compares against `db.find_latest_history(channel=...)`); the 2026-07-29 pass flagged the same identity error; Sonarr's ordered quality ladder, `Upgrade Until` cutoff and ~29 named accept/reject specifications (https://trash-guides.info/Radarr/radarr-setup-quality-profiles/, https://github.com/Sonarr/Sonarr/tree/develop/src/NzbDrone.Core/DecisionEngine/Specifications); Syncthing staggered versioning (https://docs.syncthing.net/users/versioning.html); depends on V77.
  Touches: new decision module, `streamkeep/ui/tabs/monitor.py`, `streamkeep/rules.py`, `streamkeep/db.py`, `streamkeep/ui/tabs/settings.py`, `streamkeep/workers/finalize.py`, tests.
  Acceptance: an upgrade profile is an ordered format ladder with an explicit cutoff and optional scored matchers where a negative score is a hard veto; a candidate is only ever compared against the same canonical identity; every evaluation records accept or reject with a named reason, visible per item; replacement downloads to staging, verifies, then commits atomically, and the previous file is retained under a bounded staggered-versioning policy rather than deleted; any failure leaves the known-good file in place; upgrades are off by default.
  Complexity: L

- [ ] P1 — V83 — Retroactively re-template an existing archive when the naming scheme changes
  Why: changing an output or folder template today applies only to new downloads, leaving the archive split across two schemes; users then reorganize by hand, which breaks the library's own path references. Asked for in three separate trackers, shipped by none.
  Evidence: `streamkeep/utils.py` template rendering, `streamkeep/ui/rename_dialog.py:247` (single-item rename only), `streamkeep/db.py` `update_history_entry` path column; https://github.com/kieraneglin/pinchflat/issues/408 (17👍), https://github.com/jmbannon/ytdl-sub/issues/536; depends on V77 and shares the preview/apply/audit machinery with V79.
  Touches: `streamkeep/maintenance.py`, `streamkeep/utils.py`, `streamkeep/storage.py`, `streamkeep/db.py`, `streamkeep/ui/tabs/storage.py`, `streamkeep/cli.py`, `tests/test_maintenance.py`, `tests/test_output_templates.py`.
  Acceptance: a preview shows every current path, its proposed path, and any collision or unresolvable-field problem before anything moves; apply moves media plus all sidecars together, updates history, manifests, notes, tags and publishing rows in one transaction per item, and records each move in the maintenance audit; collisions and long-path or reserved-name results are refused rather than truncated; an interrupted run leaves every item either fully moved or fully untouched and can be previewed again.
  Complexity: M

#### P2 — Later

- [ ] P2 — V84 — Rolling integrity scrub over archive manifests
  Why: SHA-256 manifests exist but are only verified when a user asks, so bit rot and silent truncation are found by accident. A rolling fractional scrub verifies the whole library over a month at negligible cost.
  Evidence: `streamkeep/verify.py:260` (`verify_archive_manifest`), `streamkeep/maintenance.py:130` (integrity in the preview only); restic `--read-data-subset n/t` separates structure checks from data checks (https://restic.readthedocs.io/en/stable/045_working_with_repos.html).
  Touches: `streamkeep/verify.py`, `streamkeep/schedule.py` or `streamkeep/scheduler.py`, `streamkeep/db.py` (last-scrubbed state), `streamkeep/notifications.py`, `streamkeep/ui/tabs/storage.py`, `tests/test_verify.py`.
  Acceptance: a cheap check (presence, size, mtime) runs on every storage scan and a configurable fraction of the library is fully re-hashed per run, tracked so every recording is covered within the configured period; scrubs are cancellable, rate- and CPU-bounded, and skip offline volumes without marking them failed; mismatches raise a notification and are listed with the affected files; nothing is ever auto-repaired or auto-deleted.
  Complexity: M

- [ ] P2 — V85 — Bring the HLS parser current with RFC 8216bis-22: delta playlists and DATERANGE
  Why: the parser handles media/discontinuity sequence, GAP, BYTERANGE, PDT, `EXT-X-PART` and `EXT-X-PRELOAD-HINT` but not `EXT-X-SKIP` (so a delta playlist response is read as if segments were removed) and not `EXT-X-DATERANGE`, which is now the carrier for SSAI markers, SCTE-35, client-side interstitials and Apple's 2026 out-of-band `daterange-schedule` sidecar. This is the generic substrate V37 should build on.
  Evidence: `streamkeep/hls.py:63` (known-tag list), `:338-399` (playlist parser); https://datatracker.ietf.org/doc/draft-pantos-hls-rfc8216bis/ (draft-22); https://developer.apple.com/streaming/Whats-new-HLS.pdf; https://developer.apple.com/streaming/GettingStartedWithHLSInterstitials.pdf.
  Touches: `streamkeep/hls.py`, `streamkeep/workers/download.py` segment selection, `streamkeep/metadata.py` (marker sidecar), `tests/fixtures/manifests/`, `tests/test_manifest_fixtures.py`.
  Acceptance: `EXT-X-SKIP` delta updates are merged against the retained segment list instead of being treated as removals, and a fixture proves no segment is dropped; `EXT-X-DATERANGE` attributes (including `CLASS`, `SCTE35-OUT`/`SCTE35-IN`, and interstitial `X-ASSET-URI`/`X-ASSET-LIST`) are parsed and written to a marker sidecar; a `com.apple.hls.daterange-schedule` `X-URI` is fetched through `net_guard` and archived alongside; interstitial assets are recorded as markers and never captured as primary content; unknown DATERANGE classes are preserved verbatim, not dropped.
  Complexity: M

- [ ] P2 — V86 — Close the WCAG 2.2 AA gaps and adopt Qt 6.11 accessibility roles
  Why: the offscreen theme/density/contrast matrices are strong but assert none of WCAG 2.2's new criteria, and the pinned Qt 6.11.1 exposes roles the app does not use — toggles currently report to screen readers as checkboxes.
  Evidence: `tests/test_accessibility.py` (5 tests, none covering target size, dragging alternatives or focus obscuring), `streamkeep/ui/clip_dialog.py:1535` filmstrip drag handles, `streamkeep/ui/calendar_widget.py`; https://www.w3.org/TR/WCAG22/ (2.4.11, 2.5.7, 2.5.8); https://doc.qt.io/qt-6/whatsnew611.html (`QAccessible::Switch`, `Orientation` attribute, AT-SPI Collection).
  Touches: `streamkeep/ui/widgets.py`, `streamkeep/ui/clip_dialog.py`, `streamkeep/ui/calendar_widget.py`, `streamkeep/player/player_controls.py`, `streamkeep/theme.py`, `tests/test_accessibility.py`.
  Acceptance: every drag-operated control (clip handles, schedule blocks, timeline scrubber) has a keyboard and single-pointer alternative that reaches the same values; interactive targets meet 24×24 CSS pixels at 100% scale or have adequate spacing, asserted by an offscreen geometry test; a focused control is never fully obscured by sticky headers or docked panels at the supported minimum size; toggles expose `QAccessible::Switch` and sliders expose `Orientation`; the checks run in the existing offscreen matrix.
  Complexity: M

- [ ] P2 — V87 — Podcast archiving at Podcasting 2.0 fidelity
  Why: only `podcast:transcript` and `podcast:chapters` are read today. The leading OSS CLI moved off GitHub and never supported chapters or transcripts, so the fidelity gap is unserved — while `podcast:guid` is the only stable episode identity across feed-URL changes and `alternateEnclosure/integrity` provides a publisher-supplied hash to verify a download against.
  Evidence: `streamkeep/podcast_sidecars.py` (two tags), `streamkeep/extractors/podcast.py`, `streamkeep/opml.py`; https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md (phases 1–7 ratified); https://codeberg.org/janw/podcast-archiver; https://github.com/advplyr/audiobookshelf/issues/1573 (41👍 per-episode artwork), https://github.com/AntennaPod/AntennaPod/issues/1946 (44👍 chapters).
  Touches: `streamkeep/extractors/podcast.py`, `streamkeep/podcast_sidecars.py`, `streamkeep/metadata.py`, `streamkeep/feed.py`, `streamkeep/monitor.py`, `tests/test_podcast_sidecars.py`, `tests/test_opml.py`.
  Acceptance: `guid`, `season`, `episode`, `medium`, `person`, `soundbite`, `funding`, `license`, `location`, `txt` and per-episode artwork are captured into the sidecar and the library; `alternateEnclosure`/`source`/`integrity` hashes are recorded and verified against the downloaded file when present, with a mismatch surfaced rather than silently accepted; `podcast:value` is stored verbatim and never acted on; RFC 5005 paged feeds are followed so older episodes are reachable; episode identity uses `guid` and falls back to enclosure URL, so dynamic ad insertion changing the bytes does not create a duplicate; chapters convert to ffmetadata and a WebVTT sidecar.
  Complexity: L

- [ ] P2 — V88 — Explicit, verified acquisition of the JavaScript runtime yt-dlp now requires
  Why: full YouTube support requires an external JS runtime, and StreamKeep only detects one — a user with no Deno or Node gets a correct diagnosis and no path forward. Competitors bundle and self-update it. The v4.12.1 fork bomb makes implicit install a permanent non-option, so this must be explicit, hash-verified and user-initiated.
  Evidence: `streamkeep/capabilities.py:689-701` (detect-only, with correct Deno 2.3 / Node 22 floors), `streamkeep/bootstrap.py` (frozen early-return), `README.md:235`; https://github.com/yt-dlp/yt-dlp/issues/15012; Parabolic bundles and self-updates Deno (https://github.com/NickvisionApps/Parabolic/releases/tag/2026.5.0).
  Touches: `streamkeep/capabilities.py`, new runtime-acquisition module, `streamkeep/ui/tabs/settings_tools.py`, `streamkeep/cli.py` (`youtube-health`), `streamkeep/diagnostics.py`, `tests/test_capabilities.py`, `tests/test_youtube_health.py`.
  Acceptance: a Settings and CLI action downloads a pinned Deno release into the StreamKeep data directory, verifies a pinned SHA-256 before extraction, and registers it in the capability registry; nothing is ever downloaded or executed without an explicit user action, and never at startup or import time; the frozen-build guard from v4.12.1 remains in force; the managed runtime is listed with its path, version and provenance in diagnostics, is removable, and a PATH-provided runtime still takes precedence when the user prefers it.
  Complexity: M

- [ ] P2 — V89 — Delete the unreachable gallery share registry and unused feed constant
  Why: `gallery.py`'s in-memory registry was superseded by the database publishing registry and is now called only by tests, while keeping two misleading fallback branches alive that make the module look like it can serve an empty or missing share when it never runs that way.
  Evidence: `streamkeep/gallery.py:23,24,51,62,67,71,77` (registry never imported by `streamkeep/`), `:87` and `:132` (dead fallbacks), `streamkeep/local_server.py:1386-1448` (imports only the renderers); `streamkeep/feed.py:20` (`_PUBLISHING_ID_RE` unused).
  Touches: `streamkeep/gallery.py`, `streamkeep/feed.py`, `tests/test_gallery.py`, `tests/test_publishing.py`.
  Acceptance: the registry functions and their tests are removed, the renderers keep their existing behavior for real database-backed shares, publishing and revocation still pass end to end, and pyflakes plus the full suite stay clean.
  Complexity: S

- [ ] P2 — V90 — Strip C0 control characters before XML escaping in feed and gallery output
  Why: `xml.sax.saxutils.escape` handles `& < >` but not control characters, and scraped platform titles reach the feed unfiltered — `safe_filename` strips them only for paths — so a recording title containing `\x01`–`\x08` produces a document podcast clients reject.
  Evidence: `streamkeep/feed.py:82-83,128,147-149`; `streamkeep/utils.py:79` (control stripping exists for filenames only).
  Touches: `streamkeep/feed.py`, `streamkeep/gallery.py`, `streamkeep/utils.py` (shared text sanitizer), `tests/test_publishing.py`.
  Acceptance: all XML and HTML text nodes and attributes pass through one sanitizer that removes characters XML 1.0 forbids before escaping; a test feeds a title containing C0 bytes and asserts the generated feed parses with a strict XML parser; existing escaping behavior for `& < > " '` is unchanged.
  Complexity: S

- [ ] P2 — V91 — Retire the MSIX lane and state the shipped distribution matrix
  Why: MSIX cannot install without a signed package, which the no-code-signing policy forbids permanently — so the scaffold, its packaging code and its blocked roadmap entry are carrying cost for an artifact that can never ship. Inno Setup, the portable onedir, Flatpak and WinGet already cover every target.
  Evidence: `packaging/msix/build_msix.py`, `packaging/msix/AppxManifest.xml`, `Roadmap_Blocked.md` ("Split portable and MSIX build/update contracts", blocked on signing), `README.md:269,273`; https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes.
  Touches: `packaging/msix/`, `packaging/build.py`, `packaging/versioning.py` (MSIX stamping), `packaging/release_gate.py`, `README.md`, `Roadmap_Blocked.md`, `tests/test_packaging.py`, `tests/test_windows_distribution.py`.
  Acceptance: the MSIX builder and manifest are removed along with their version-stamping and gate stages; README documents the shipped matrix as unsigned Inno installer, portable onedir zip, Flatpak manifest and WinGet manifest; the blocked entry is deleted with a one-line rationale in the changelog; the release gate and reproducible build still pass and produce the same Windows artifacts.
  Complexity: S

- [ ] P2 — V92 — Reuse SQLite connections instead of opening one per operation
  Why: `_connect` is called from 57 sites and each call runs `busy_timeout`, `foreign_keys` and a `journal_mode` round-trip plus a runtime safety check before a single row is read — a cost paid by every History page, every queue poll and every monitor tick.
  Evidence: `streamkeep/db.py:43-52` (`_connect`), `streamkeep/sqlite_runtime.py:90-130` (`connect` PRAGMA sequence and `require_safe_runtime`).
  Touches: `streamkeep/db.py`, `streamkeep/sqlite_runtime.py`, `tests/test_db.py`, `tests/test_history_paging.py`.
  Acceptance: connections are cached per thread and reused, with PRAGMAs applied once at creation and the runtime safety check hoisted out of the hot path; the existing write lock and WAL/rollback selection are unchanged; connections are closed on thread exit and on shutdown so no handle survives a profile switch; a benchmark test shows a measurable reduction in per-query overhead and the concurrency tests still pass.
  Complexity: M

- [ ] P2 — V93 — Test the untested subprocess and file-writing workers, and the player package
  Why: 26 modules have no test reference, and the highest-risk set both spawns subprocesses and writes files. The entire `player/` package (1489 LOC) is untested despite a recorded use-after-free in `sync_viewer._relayout_grid`, and `ui/main_window_jobs.py` — untested — is the code path that selects the yt-dlp argument template implicated in V67.
  Evidence: no test file references `postprocess/clip_worker.py`, `transcribe_worker.py`, `chat_render_worker.py`, `thumb_worker.py`, `scene_worker.py`, `normalization.py`, `codecs.py`, `convert_worker.py`, `integrations/auto_editor.py`, `intelligence/summarize.py`, `ui/main_window_jobs.py`, `ui/tabs/settings_companion.py`, or any `streamkeep/player/` module; `.coveragerc` floor is 47.5%.
  Touches: new `tests/test_postprocess_workers.py`, `tests/test_player.py`, `tests/test_main_window_jobs.py`, `tests/test_settings_companion.py`, `.coveragerc`.
  Acceptance: each named post-process worker has command-construction tests asserting argument shape, `--`/`-nostdin` handling and output-path safety without invoking a real encoder, plus a failure-path test asserting no partial file survives; the player package is instantiated offscreen with a stub mpv, covering the sync-viewer relayout that previously caused a use-after-free; the coverage floor is raised to match the new measured value.
  Complexity: L

- [ ] P2 — V94 — Decompose `db.py` and `local_server.py` behind stable facades
  Why: the two largest modules are 4409 and 2429 lines and concentrate unrelated concerns, which is why security review of the server's route table and schema review of the database both require reading the whole file. `ui/main_window.py:5` still carries a deferred-decomposition comment at 2760 lines.
  Evidence: measured line counts; `streamkeep/local_server.py` `do_GET`/`do_POST` carry roughly 40 branches each and embed ~200 lines of `_WEB_UI_HTML`; `streamkeep/db.py` owns 15 schema migrations plus every table family.
  Touches: new `streamkeep/db/` package, new `streamkeep/server/` package, `streamkeep/ui/main_window.py`, all importers, existing tests.
  Acceptance: `db.py` becomes a package split per table family with one module owning the migration sequence, and `local_server.py` becomes a route table plus separate auth-policy and static-asset modules with the web UI moved to a data file; every existing public import path keeps working through a facade so no caller changes in the same commit; the full suite and the OpenAPI consistency test pass unchanged; no behavior change ships in the same commit as the move.
  Complexity: L

- [ ] P2 — V95 — Correct the stale architecture and bootstrap claims in the agent-facing notes
  Why: `CLAUDE.md` is the documented source of truth for every session and still describes a 4-tab UI and states "PyQt6 and yt-dlp auto-installed" — the exact behavior removed in v4.12.1 to stop the frozen-exe fork bomb. A future change made from that description reintroduces a known machine-wedging defect. `ROADMAP.md` also links to three files `AGENTS.md` forbids.
  Evidence: `CLAUDE.md` "Build/Run" and "Architecture (v2.0.0)" sections vs the shipped six-page shell and `streamkeep/bootstrap.py`'s frozen early-return; `ROADMAP.md:7-11` links `COMPLETED.md`, `RESEARCH_REPORT.md` and `ROADMAP-COMPLETED.md`, all listed under "Never create" in `AGENTS.md`.
  Touches: `CLAUDE.md`, `ROADMAP.md` planning-docs list, `packaging/release_gate.py` release-claims stage.
  Acceptance: the package layout, page count, dependency-installation behavior and database description in `CLAUDE.md` match the shipped code, and the no-implicit-install rule is stated where the old auto-install claim was; the roadmap's planning-docs list references only files the documentation policy allows; the release-claims gate stage additionally fails on a machine-checkable claim drift between `CLAUDE.md`'s stated page count and the shipped tab registry.
  Complexity: S

#### P3 — Under Consideration

- [ ] P3 — V96 — Persistent health panel with scheduled probes and a notification event vocabulary
  Why: capability probes, disk thresholds, credential checks and YouTube health already exist but are spread across Settings, onboarding and diagnostics, and surface as transient toasts rather than standing conditions. Sonarr's model — background checks producing a severity-ranked, persistent list plus pluggable notification targets on a fixed event vocabulary — is the proven shape.
  Evidence: `streamkeep/capabilities.py`, `streamkeep/disk_monitor.py`, `streamkeep/credential_check.py`, `streamkeep/startup_check.py`, `streamkeep/hooks.py:37` (`HOOK_EVENTS`); https://wiki.servarr.com/sonarr/system.
  Touches: new health module, `streamkeep/ui/main_window.py`, `streamkeep/hooks.py`, `streamkeep/local_server.py`, `streamkeep/cli.py`, tests.
  Acceptance: a scheduled health run aggregates below-floor or missing tools, expired credentials, offline archive roots, repeated extractor failures and disk pressure into a persistent severity-ranked list with a repair action per row; the same conditions fire on the existing structured-hook and webhook surface under stable event names; the panel is reachable from desktop, CLI and the authenticated API; resolved conditions clear themselves without a restart.
  Complexity: M

- [ ] P3 — V97 — Model watch state and library events as an append-only action log
  Why: watched flags, playback positions, favorites and deletions are mutable columns, so there is no history, no undo, and no basis for reconciling state after a restore or a rebuild. gPodder's episode-action model shows an append-only log gives all three for free even in a single-user app.
  Evidence: `streamkeep/db.py` history columns updated in place by `update_history_entry`; `streamkeep/maintenance.py:253` already demonstrates the append-only audit pattern in this codebase; https://gpoddernet.readthedocs.io/en/latest/api/reference/events.html.
  Touches: `streamkeep/db.py` (schema migration), `streamkeep/player/player_panel.py`, `streamkeep/ui/tabs/history.py`, `streamkeep/backup.py`, tests.
  Acceptance: state changes append a timestamped action row and the current value is a derived projection; the projection is rebuildable from the log alone; the log is bounded and compactable so it cannot grow without limit; a restore or a V80 rebuild reconciles by replaying actions; existing history reads keep their current shape and performance.
  Complexity: L

- [ ] P3 — V98 — Declarative source adapters so new sites need no Python
  Why: extractors and plugin adapters both require Python, so adding a PeerTube instance, a self-hosted feed or a simple HTML page means shipping code. Prowlarr supports 500+ sources through declarative YAML definitions with selector rules and no recompile.
  Evidence: `streamkeep/extractors/base.py:50` (`NotImplementedError` contract), `streamkeep/plugins.py` (manifest v2, Python entry points); https://github.com/Prowlarr/Prowlarr (Cardigann YAML definitions).
  Touches: new declarative-adapter loader, `streamkeep/extractors/base.py`, `streamkeep/plugins.py`, `streamkeep/scrape.py`, `streamkeep/ui/tabs/settings_tools.py`, tests.
  Acceptance: a user-editable YAML definition describes request shape, response parsing and field mapping for a source and produces the same `StreamInfo`/`VODInfo` values a Python extractor does; definitions execute no code and cannot reach the filesystem or spawn processes; every request routes through `net_guard` with the same SSRF policy as the rest of the app; definitions are versioned, validated with clear errors, hot-reloadable, and quarantined on config import like other capabilities.
  Complexity: L

- [ ] P3 — V99 — Offer FFmpeg 8's `whisper` filter as an alternate transcription backend
  Why: transcription currently needs faster-whisper or a whisper.cpp binary as a separate optional runtime; FFmpeg 8.0 ships a `whisper` filter that transcribes inside the same graph as the remux, removing one optional dependency for users whose FFmpeg build includes it.
  Evidence: `streamkeep/postprocess/transcribe_worker.py` (faster-whisper preferred, whisper-cli fallback), `streamkeep/capabilities.py` FFmpeg 8.1.2 floor; https://linuxiac.com/ffmpeg-8-0-arrives-with-whisper-filter-vulkan-encoders/.
  Touches: `streamkeep/postprocess/transcribe_worker.py`, `streamkeep/capabilities.py`, `streamkeep/ui/tabs/settings_tools.py`, `tests/test_capabilities.py`.
  Acceptance: the capability registry probes whether the resolved FFmpeg exposes the `whisper` filter and a model path is configured, and only then offers it as a backend; the existing faster-whisper and whisper-cli paths remain and stay the default; all backends produce the same `.srt`/`.vtt`/`.transcript.json`/`.chapters.auto.txt` outputs; an absent filter is reported as unavailable rather than failing a job.
  Complexity: M

- [ ] P3 — V100 — Archive platform comments alongside chat for VOD sources
  Why: StreamKeep captures live chat but not comments, so the discussion attached to a VOD or podcast episode is lost when the source is removed — the failure mode archiving exists to prevent. TubeArchivist treats comment archiving as core.
  Evidence: `streamkeep/chat/` (live chat only), `streamkeep/chat/youtube_replay.py` (replay chat, not comments); https://github.com/tubearchivist/tubearchivist (comment archiving and search).
  Touches: `streamkeep/extractors/ytdlp.py` (comment extraction args), `streamkeep/workers/finalize.py`, `streamkeep/metadata.py`, `streamkeep/search.py` FTS, `streamkeep/db.py`, tests.
  Acceptance: comment capture is opt-in per job and per monitor, bounded by a configurable maximum count and payload size; comments are written as a versioned sidecar next to the recording and indexed for search; author names are stored as published with no additional profile lookups; a source that refuses or rate-limits comments logs the reason and does not fail the download.
  Complexity: M
