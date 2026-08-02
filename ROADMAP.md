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

- [ ] P2 — V32 — Pluggable remote-cipher / PO-token backend for JS-challenge churn
  Why: YouTube's rotating nsig/signature challenges break pure in-process solving during multi-day windows; the market answer is offloading challenge-solving to a helper service. StreamKeep's plugin system can host this without a hard dependency.
  Evidence: TubeSync `yt-dlp-remote-cipher` plugin (0.17.0); yt-dlp #15751 live regressions; existing `streamkeep/plugins.py`; overlaps V26 (share the provider-config surface).
  Touches: plugin contract for a cipher/token backend, extractor-args wiring, config for backend URL/mode, health surfacing in `youtube_health_report()`, sample backend + contract test.
  Acceptance: With a remote-cipher/token backend configured, YouTube resolves that fail in-process succeed via the backend; the backend is optional and declared through the plugin manifest; health report shows backend reachability; a sample backend passes a contract test; absence degrades to current behavior.
  Complexity: L
  > 2026-07-27: Complementary to V33 (local bgutil PO-token sidecar + JS-runtime version gate) and V34 (yt-dlp-ytse SABR fallback). V32 is the *remote* offload path; V33/V34 are the *local* paths. Share one provider/health-config surface across all three. (RESEARCH.md 2026-07-27)

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

- [ ] P2 — V34 — Optional yt-dlp-ytse SABR fallback engine for YouTube
  Why: YouTube increasingly leaves only SABR formats for many clients, where a normal resolve returns storyboard-only/"requested format not available"; the native SABR downloader `yt-dlp-ytse` recovers real media.
  Evidence: https://pypi.org/project/yt-dlp-ytse/; https://github.com/yt-dlp/yt-dlp/issues/12482, /14810; `streamkeep/extractors/ytdlp.py` (`looks_like_sabr_or_pot_failure` already detects the condition). Complements V32/V33 (share the provider/health surface).
  Touches: capability detection for `yt-dlp-ytse`, extractor-args wiring (`youtube:formats=sabr`), a SABR-only fallback branch in the resolve/download path, health-report surfacing, tests.
  Acceptance: when a YouTube resolve is detected as SABR-only/storyboard-only and `yt-dlp-ytse` is present, StreamKeep retries via `--extractor-args youtube:formats=sabr` and downloads real media; the engine is optional and detected like gallery-dl/lux; known ytse limits (no `--download-sections`/`-N`/resume) are surfaced, not silently hit; a unit test covers the SABR-detected fallback path.
  Complexity: M

- [ ] P2 — V37 — Twitch SSAI ad-segment detection/stripping for VOD and live
  Why: Twitch server-side-inserts ads into the m3u8 (SSAI) and signs manifests; VOD downloads splice ad segments into the recording. No all-in-one tool strips them.
  Evidence: https://getblockify.com/blog/how-to-block-twitch-ads/; https://streamlink.github.io/cli/plugins/twitch.html (ad-filter default since 7.5.0); `streamkeep/extractors/twitch.py`, `twitch_recover.py`. Belongs with V13.
  Touches: HLS playlist parsing (`hls.py`), ad-segment signature/discontinuity detection, `workers/download.py` segment filter, Twitch extractor, tests with a stitched-ad fixture.
  Acceptance: ad segments (stitched-ad discontinuity markers) are detected and excluded from Twitch VOD/live output without corrupting the timeline; a fixture playlist with injected ad segments produces an ad-free recording; non-Twitch sources are unaffected.
  Complexity: L

- [ ] P2 — V38 — Twitch VOD auto-unmute of copyright-muted segments
  Why: Twitch mutes copyright-flagged audio in VODs; downloads inherit the muted gaps. twitch-dlp restores original audio where a valid CDN URL still exists.
  Evidence: https://github.com/DmitryScaletta/twitch-dlp; `streamkeep/extractors/twitch.py`, `twitch_recover.py` (already probes CDN hashes/trackers).
  Touches: Twitch extractor muted-segment detection, unmuted-URL probing (reuse `twitch_recover` CDN logic), `workers/download.py` segment substitution, tests.
  Acceptance: muted VOD segments are detected and, where an unmuted CDN URL resolves, substituted so the recording has original audio; when no unmuted source exists it logs and keeps the muted segment; opt-in toggle; unit test covers detect + substitute + no-source fallback.
  Complexity: M

- [ ] P2 — V40 — Dubbed-audio-language selection + clean `mute` (audio-strip) output mode
  Why: cobalt exposes `youtubeDubLang` and a `mute` mode; StreamKeep's per-download overrides cannot pick a dubbed audio track or produce a video-only output cleanly.
  Evidence: https://github.com/imputnet/cobalt/blob/main/docs/api.md; `streamkeep/ui/tabs/download_controls.py`, `streamkeep/download_options.py`. Extends V20.
  Touches: `download_options.py` (dub-lang + mute validation), `download.py` yt-dlp/ffmpeg args (`--audio-multistreams`/language selection; `-an` for mute), `download_controls.py` UI, tests.
  Acceptance: a per-download control selects a dubbed audio language when the source offers one, and a `mute` toggle produces a video-only file without a stray empty audio track; both round-trip through the override payload with unit tests.
  Complexity: M

#### P3 — Under Consideration

- [ ] P3 — V42 — yt-dlp stable/nightly update channel toggle
  Why: YouTube fixes land in yt-dlp nightly days before stable; power users want to opt into nightly for the fast-moving platforms without waiting for a StreamKeep release. StreamKeep bundles a frozen yt-dlp.
  Evidence: https://github.com/alexta69/metube/releases (nightly toggle); `streamkeep/capabilities.py` (`resolve_command_prefix`), `streamkeep/updater.py`.
  Touches: capability resolution to allow a user-supplied/updatable yt-dlp, a stable/nightly channel setting, health surfacing of the active yt-dlp version/channel, Settings, tests.
  Acceptance: a setting lets the user point at an external/nightly yt-dlp or self-update the bundled one; the health panel shows the active version and channel; the bundled frozen yt-dlp remains the default; switching channels is reversible.
  Complexity: M

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

## Audit Findings — 2026-08-02

Deep audit pass on v4.44.0. Baseline captured first: `1293 passed, 113 subtests` (`py -3.12 -m pytest tests/`), pyflakes clean, ruff reports 50 style-only items (42 `E402` launcher-import ordering, plus test-file dead imports — see V64). New IDs continue the V-scheme (highest prior = V54). Every item below was traced to a reachable path and confirmed against current source; confidence is stated per item. No code was changed in this pass.

### Unaudited — needs a dedicated pass

The 2026-08-02 audit prioritized security/subprocess boundaries, the download/worker/data layer, and UI theme/UX/a11y, tracing across module boundaries and running the suite/linters to confirm findings (the GUI was traced from source, not driven live — no display was launched). The following larger modules received only a smell-level sweep and warrant their own pass: `streamkeep/cli.py` (~67 KB), `streamkeep/headless_service.py` (~42 KB) beyond its SSRF entry point, `streamkeep/download_options.py`, `streamkeep/job_spec.py`, `streamkeep/maintenance.py`, `streamkeep/lifecycle.py`, `streamkeep/tags.py`, `streamkeep/metadata.py` internals, and the `streamkeep/player/` package interactions. Performance was spot-checked and found clean (prior passes removed the known O(n²) hotspots); a dedicated profiling pass over large-archive History/Storage/Analytics rendering with 100k+ rows has not been done.
