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

## Research-Driven Additions

### P0 — Now

- [ ] P0 — Unify credential storage and produce secret-free exports/backups
  Why: Tokens, webhooks, media-server credentials, `config.json`, and `cookies.txt` can bypass the DPAPI/keyring boundary and leave the machine in plaintext exports or backups.
  Evidence: `streamkeep/accounts.py`, `streamkeep/secrets.py`, `streamkeep/backup.py:20-33`, `streamkeep/ui/tabs/settings.py:378-430,753`, `streamkeep/ui/main_window.py:709`; DPAPI and RFC 9106.
  Touches: `streamkeep/config.py`, `streamkeep/secrets.py`, `streamkeep/accounts.py`, `streamkeep/backup.py`, `streamkeep/cookies.py`, settings/main-window credential fields, migration tests.
  Acceptance: No credential value appears in `config.json`, logs, diagnostics, normal JSON exports, or default `.skbackup` files; legacy plaintext migrates atomically; ordinary restore excludes auth state; an explicit portable-secret backup uses Argon2id plus authenticated encryption and wrong-password/tamper tests.
  Complexity: L

- [ ] P0 — Bind parallel HTTP resume parts to one representation
  Why: Size-only part reuse can join byte ranges from different origin representations into a corrupt file that appears complete.
  Evidence: `streamkeep/http.py:345-439`; RFC 9110 range/validator semantics and RFC 9530 content digests.
  Touches: `streamkeep/http.py`, persisted part metadata, `tests/test_http.py`.
  Acceptance: Resume captures a strong ETag or Last-Modified value, sends `If-Range`, requires exact `206` and `Content-Range`, invalidates all old parts on validator/length change, refuses old-part resume without a safe validator, and verifies `Content-Digest`/`Repr-Digest` when present.
  Complexity: M

- [ ] P0 — Quarantine executable behavior during configuration import
  Why: An imported JSON dictionary is applied immediately and can activate `shell=True` hooks and outbound automation without a trust review.
  Evidence: `streamkeep/ui/tabs/settings.py:378-430`; `streamkeep/hooks.py:27-58`.
  Touches: `streamkeep/config.py`, `streamkeep/hooks.py`, `streamkeep/ui/tabs/settings.py`, config-import tests.
  Acceptance: Imports enforce a versioned schema plus byte/depth/count limits, show a diff, quarantine hooks/webhooks/proxies/auto-imports as disabled, require explicit per-capability activation, never interpolate remote metadata into commands, and leave the pre-import config untouched on validation failure.
  Complexity: M

- [ ] P0 — Establish a frozen-executable startup and process-isolation contract
  Why: Source/offscreen tests pass while one-file builds observed on 2026-07-15 crashed or spawned multiple visible windows; release correctness must be tested at the artifact boundary.
  Evidence: 2026-07-15 packaged failures; `StreamKeep.py`, `StreamKeep.spec`, `streamkeep/ui/main_window.py`, `streamkeep/ui/tabs/history.py`; recurring frozen re-entry commits in the last 200 commits.
  Touches: launcher/boot lifecycle, `StreamKeep.spec`, release scripts, `tests/test_gui_smoke.py`, new artifact-level tests.
  Acceptance: An isolated one-file build starts non-interactively with empty, migrated, and populated databases; initializes history/thumbnails and embedded yt-dlp; exposes exactly one application instance with no child re-entry/window fanout; supports windowed CLI streams safely; emits a machine-readable ready marker; and exits cleanly under a timeout.
  Complexity: M

- [ ] P0 — Enforce toolchain security floors through one runtime capability registry
  Why: The bundled lower bounds and PATH tools can silently select releases affected by media-parser, downloader, and transport advisories published through 2026-07-15 or incomplete YouTube runtime support.
  Evidence: `requirements.txt`, `streamkeep/bootstrap.py:24-84`, `streamkeep/diagnostics.py`, `streamkeep/extractors/ytdlp.py`; yt-dlp 2026.06.09, Pillow 12.3.0, FFmpeg and curl security advisories, and yt-dlp EJS requirements.
  Touches: dependency manifests, launcher/onboarding/Settings diagnostics, `streamkeep/bootstrap.py`, extractor/worker command resolution, release tooling.
  Acceptance: The exact executable/module used is recorded with path, semantic version, provenance, and capabilities; release floors are at least yt-dlp 2026.06.09, Pillow 12.3.0, curl 8.21.0, and FFmpeg 8.1.2; matching Deno/EJS is deterministic; vulnerable tools block risky paths with repair guidance; startup never runs implicit `pip install`.
  Complexity: L

- [ ] P0 — Make updates publisher-authenticated, downgrade-resistant, and self-rolling-back
  Why: A hash from the same release channel authenticates bytes but not the publisher, and the updater verified on 2026-07-15 has no automatic escape path from a startup-regressing build.
  Evidence: `streamkeep/updater.py`; USENIX Security 2026 desktop-updater findings; Microsoft MSIX signing guidance (https://learn.microsoft.com/en-us/windows/msix/package/sign-msix-package-guide).
  Touches: `streamkeep/updater.py`, boot health markers, release signing/stamping, database pre-update snapshots, updater tests.
  Acceptance: Updates require an offline-signed manifest and valid portable-EXE/MSIX signature, reject replay/downgrade and path substitution, stage replacement atomically, snapshot migration-sensitive state, mark healthy only after full initialization, and restore the last-known-good binary/state with a visible recovery log after failed startup.
  Complexity: L

- [ ] P0 — Repair launcher CLI dispatch and configuration-root binding
  Why: Implemented `db` and `snapshot` commands enter the GUI path, while `server --config-dir` changes the root after module constants are already bound.
  Evidence: `StreamKeep.py:40-45`, `streamkeep/cli.py:302-309,413-468,510-518`, `streamkeep/config.py:12`, `streamkeep/db.py:25`.
  Touches: `StreamKeep.py`, `streamkeep/cli.py`, paths/config/database initialization, `tests/test_cli.py`.
  Acceptance: Every parser command dispatches identically through source and frozen launchers; alternate config roots bind before importing stateful modules; commands never initialize/show the GUI; subprocess tests prove filesystem isolation and exit/output contracts.
  Complexity: S

- [ ] P0 — Turn headless service acknowledgements into durable executed jobs
  Why: As verified on 2026-07-15, the server reports accepted URLs while only printing them, so remote clients can receive false success and no usable state/library response.
  Evidence: `streamkeep/cli.py:335-337`; `streamkeep/local_server.py`; refines the existing “Remote/headless management polish” item; MeTube and MyJDownloader API behavior.
  Touches: `streamkeep/cli.py`, queue repository, download/finalize orchestration, `streamkeep/local_server.py`, service integration tests.
  Acceptance: `/api/queue` persists then executes jobs through the same state machine as the GUI; restart resumes eligible work; status/library/failure endpoints reflect SQLite truth; cancellation and recovery are durable; every acknowledgement carries a job ID with observable terminal outcome.
  Complexity: L

- [ ] P0 — Define a safe opt-in LAN control boundary
  Why: Plain HTTP bearer control and command-line token exposure are incompatible with operations that queue, recover, or publish local media.
  Evidence: `streamkeep/local_server.py`, `streamkeep/cli.py --token`, browser-extension pairing; existing local-server security roadmap direction.
  Touches: local server auth/origin code, CLI secret input, Settings pairing UI, browser extension, threat-model tests.
  Acceptance: Loopback remains default; tokens are generated with at least 128 bits, stored in the secure store, never required in argv/URLs/logs, scoped and rotatable; LAN mode requires explicit pairing plus an HTTPS/reverse-proxy trust boundary; CSRF/origin/Host/replay tests cover every mutating endpoint.
  Complexity: L

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

- [ ] P1 — Complete browser clip-range and pairing handoff end to end
  Why: The extension sends clip bounds and the server emits them, but the GUI never connects the signal; advertised non-loopback host entry also exceeds manifest permissions.
  Evidence: `browser-extension/popup.js`, `browser-extension/manifest.json`, `streamkeep/local_server.py:72,475`, `streamkeep/ui/tabs/settings.py:1176-1181`.
  Touches: extension permissions/pairing, local server signals, main-window/download/clip flow, extension and GUI integration tests.
  Acceptance: A paired browser action can send URL-only or URL plus validated start/end; the desktop opens the correct prefilled workflow once; unsupported/expired pairing is actionable; host permissions exactly match supported origins and never request broad browsing access.
  Complexity: S

- [ ] P1 — Validate backup databases before atomic activation
  Why: Restoring malformed or incompatible SQLite/FTS state can replace a working library before integrity is known.
  Evidence: `streamkeep/backup.py::restore_backup`, `streamkeep/db.py::quick_check`, `streamkeep/search.py`; SQLite security guidance (https://sqlite.org/security.html) and PRAGMA reference (https://www.sqlite.org/pragma.html).
  Touches: backup staging, database connection policy, FTS rebuild, restore UI/tests.
  Acceptance: Restore extracts to a private staging directory, validates metadata/schema versions, enables `trusted_schema=OFF`, runs `quick_check` and `foreign_key_check`, opens/rebuilds FTS, and swaps all files atomically only on success; failure leaves pre-restore state byte-for-byte intact with a redacted report.
  Complexity: M

- [ ] P1 — Make HLS variant and resume semantics standards-complete
  Why: The parser ignores rendition groups, codecs, frame rate, HDR, and average bandwidth, while resume identity is not tied to manifest validators and discontinuity/media sequence.
  Evidence: `streamkeep/hls.py`, `streamkeep/resume.py`; RFC 8216 (https://www.rfc-editor.org/info/rfc8216/); refines existing extractor/protocol resilience work.
  Touches: typed HLS models/parser, quality UI, resume state, protocol fixtures.
  Acceptance: `EXT-X-STREAM-INF` and `EXT-X-MEDIA` associate audio/subtitle/language variants; codec/FPS/HDR/average bandwidth reach format selection; resumed segments key on manifest validator plus media/discontinuity sequence; fixture tests cover live rollover, gaps, discontinuities, alternate renditions, and malformed playlists.
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
