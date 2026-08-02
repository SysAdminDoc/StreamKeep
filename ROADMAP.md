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

- [ ] V9 — Raw-protocol capture jobs (leapfrog)
  What: job types for RTSP (cameras, transport tcp/udp), RTMP-listen (receive OBS pushes), SRT caller/listener (+passphrase), UDP/RTP multicast (IPTV), ICY internet radio with now-playing capture and per-track splitting; ffmpeg reconnect family; duration caps; version-gate ffmpeg 8 options.
  Verify: real capture of a public RTSP/SRT test feed and an ICY radio stream with track split.
  Effort: L
  > 2026-07-27: yt-dlp removed RTSP/MMS support in 2026.07.04 — this lane must drive ffmpeg directly, never the yt-dlp path. FFmpeg 8 now verifies TLS peer certs by default, so RTMPS/RTSPS/SRT self-signed origins require a per-source "allow self-signed" toggle injecting `-tls_verify 0`. WHIP is publish-only in ffmpeg 8; do not promise WHEP capture. (RESEARCH.md 2026-07-27)

- [ ] V11 — User-guided extraction (leapfrog; Downie-class)
  What: visible Playwright window; user navigates/logs in/plays; response sniffer surfaces manifests/media with variant picker; queue with captured request headers/cookies; refuse when EME/DRM session detected.
  Effort: L

- [ ] V12 — Extension network sniffer + header handoff
  What: MV3 webRequest capture of m3u8/mpd/media URLs + request headers on the active tab; one-click send-to-StreamKeep with full request context.
  Effort: M

- [ ] V13 — streamlink live engine (optional)
  What: in-process streamlink for Twitch/Kick live: mandatory ad-filtering, low-latency mode, DVR rewind (--hls-start-offset/--hls-live-restart), stream-up polling for monitors.
  Effort: L
  > 2026-07-27: streamlink ≥7.5.0 removed `--twitch-disable-ads` — ad-filtering is now default/mandatory, so assume it is always on. Pairs with V37 (SSAI ad-segment stripping for VODs, which streamlink's live pre-mux grab does not cover). (RESEARCH.md 2026-07-27)
  > 2026-07-29: V46 is a security prerequisite. Streamlink 8.4 fixed CVE-2026-44353 after nested HLS/DASH `file://` URIs disclosed local files; every engine must share StreamKeep's remote-manifest URI/SSRF policy rather than trusting engine defaults. (RESEARCH.md 2026-07-29)

- [ ] V14 — MSE buffer recorder (DRM-free only)
  What: Playwright init-script hook on SourceBuffer.appendBuffer teeing segments to disk; ffmpeg concat/remux; hard-refuse on any EME session; tab-open/playback-speed limitations documented.
  Effort: L

#### VP-P2 — Automation, lifecycle, and reach

- [ ] V16 — URL-pattern → profile auto-selection + zero-dialog Smart Mode toggle. Effort: M
- [ ] V18 — Media-server output layouts per monitor (Jellyfin/Plex/Kodi S/E naming + NFO). Effort: M
  > 2026-07-29: Expand acceptance with native server playlists plus portable M3U and an optional, previewed watched-state import from one explicitly selected Plex/Jellyfin/Emby user. Ambiguous mappings must be skipped, and imported watched state must never trigger lifecycle deletion without a separate opt-in. (Youtarr v1.77.0; RESEARCH.md 2026-07-29)
- [ ] V20 — Pre-queue validation probe + multi-media picker responses (cobalt-style) in GUI and REST. Effort: M
  > 2026-07-27: cobalt's picker also carries per-item type (photo/video/gif) and a separate background-audio track; extend the picker payload accordingly. Dubbed-audio-language selection and a clean audio-strip `mute` mode are tracked separately as V40. (RESEARCH.md 2026-07-27)

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


#### P2 — Later

- [ ] P2 — Add an operations view over queue, monitor, and failure state
  Why: Durable jobs and retries exist, but operators lack one filterable view of failed-only work, retry reasons, source health, totals, and next actions.
  Evidence: `streamkeep/db.py` queue/failed-job tables, `streamkeep/ui/tabs/download.py`, `ui/tabs/monitor.py`; Parabolic failed filtering and TubeSync task visibility.
  Touches: typed job/event queries, queue/monitor UI model, local server reads, thumbnails, batch actions and tests.
  Acceptance: Users can filter by state/source/stage, see batch count/duration/size estimates plus last success/next run/retry reason, retry or discard selected failures, and export a redacted report; 100,000 seeded jobs remain paged and responsive; state matches CLI/server reads after restart.
  Complexity: L


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

- [ ] P2 — V57 — XML entity-expansion (billion-laughs) DoS in DASH / TTML / OPML parsing
  Category: security
  Where: `streamkeep/dash.py:51` (`validate_dash_manifest`) and `streamkeep/dash.py:179` (`parse_mpd_xml`); `streamkeep/subtitles.py:65` (`ttml_to_srt`); `streamkeep/opml.py:87` (`import_opml`). No `defusedxml` dependency anywhere.
  Problem: All four sites call raw `xml.etree.ElementTree.fromstring()` on externally-sourced XML. `parse_mpd_xml`/`validate_dash_manifest` parse a DASH MPD manifest fetched from a remote, attacker-controllable URL during normal quality resolution — before any download. `ttml_to_srt` parses `.ttml/.dfxp/.xml` subtitle content originating from the source platform. `import_opml` parses a user-imported OPML file (lower threat, but shareable subscription lists are a vector). CPython's ElementTree expands internal entity definitions, so a small nested-entity ("billion laughs") DTD OOMs/hangs the app.
  Evidence: Empirically confirmed on this machine's `py -3.12`: a 3-level nested-entity document expands (`ET.fromstring` returned 1000 chars from a tiny bomb; deeper nesting scales exponentially). External-entity file disclosure (classic XXE) is BLOCKED here — a `SYSTEM "file://…"` entity raised `ParseError: undefined entity` — so this is a DoS, not a file-read. `grep` confirms no `defusedxml` in `requirements*.lock/txt/in` and no DTD-forbidding parser is configured at any of the four sites. The DASH path is the most reachable: `fetch_dash_manifest` → `parse_mpd_xml` runs for any DASH source during resolve.
  Fix: Add `defusedxml` to requirements and replace the four `ET.fromstring(...)` calls with `defusedxml.ElementTree.fromstring(...)` (which forbids DTDs/entities by default); or configure an expat parser with `DefaultHandler`/entity limits and reject any document containing a DOCTYPE. Keep the existing `ParseError` handling.
  Acceptance: A unit test feeds a billion-laughs DTD to each of `parse_mpd_xml`, `ttml_to_srt`, and `import_opml` and asserts it returns empty/raises a handled parse error quickly (bounded time/memory) instead of expanding; well-formed manifests/subtitles/OPML still parse correctly.
  Confidence: Verified (expansion reproduced; sites confirmed unguarded).
  Effort: S

- [ ] P2 — V58 — Transcript / global search silently returns nothing for queries containing FTS5 special characters
  Category: ux
  Where: `streamkeep/search.py:251-280` (`search_transcripts`, the `WHERE transcript_fts MATCH ?` query and its `except sqlite3.Error: rows = []`); call sites `streamkeep/ui/main_window.py:2665` (global unified search bar) and `streamkeep/ui/tabs/history.py:481` (History transcript-search mode).
  Problem: The raw user query is passed straight into FTS5 `MATCH`, whose query grammar interprets `+ : " ( ) * - ^` and the bare keywords `AND/OR/NOT/NEAR`. Any query using those characters raises `sqlite3.OperationalError`, which is swallowed to an empty result set — so searching for very common literal strings (`C++`, `3:00`, `AT&T`, a stray double-quote, a trailing `AND`) returns zero hits with no error shown. Users conclude the archive doesn't contain the term.
  Evidence: Confirmed against a live in-memory FTS5 table on `py -3.12`: `'C++'` → `fts5: syntax error near "+"`; `'foo:bar'` → `no such column: foo`; `'"unbalanced'` → `unterminated string`; `'a AND'` → `syntax error`; plain `'love'` → 1 hit. Read both call sites (main_window.py:2664-2665, history.py:479-481): neither sanitizes or quotes the query before passing it to `search_transcripts`; the `except sqlite3.Error` at search.py:273 turns the error into `[]`.
  Fix: Sanitize the query into a safe FTS5 form before `MATCH` — tokenize on whitespace and wrap each token as a double-quoted string (escaping embedded `"` as `""`), producing an implicit-AND phrase query (e.g. `"C++" "syntax"`); optionally append `*` to the final token for prefix search. This makes punctuation literal instead of operative.
  Acceptance: A unit test asserts `search_transcripts('C++')`, `search_transcripts('foo:bar')`, and `search_transcripts('"quote')` return the matching indexed rows (not `[]`) and never raise; existing multi-word searches still behave as implicit-AND.
  Confidence: Verified.
  Effort: S

- [ ] P2 — V59 — Clip/Trim dialog paints with hardcoded stale palette literals — broken in Light and High Contrast themes and ignores the accent picker
  Category: visual
  Where: `streamkeep/ui/clip_dialog.py` — the custom-painted `ScrubberView` (filmstrip, `self.scrubber`) and `WaveformWidget` (`paintEvent`) plus the crop-preview overlay. Literal sites include lines 138-139, 147, 151, 156-157, 161-162, 175, 193-198, 497, 502, 510-511, 525, 527, 1075, 1081.
  Problem: These live widgets paint with hardcoded RGB literals from the old Catppuccin Mocha palette instead of reading the live `CAT` theme dict. The dialog is reachable from any History row → "Trim / Clip…". Effects: in Light theme, `WaveformWidget.paintEvent` fills its background with `QColor(24,24,37)` (near-black) inside a light card, and filmstrip placeholders fill `QColor(30,30,46)` — dark rectangles in a light dialog; in High Contrast theme, handles/waveform stay muted Mocha blue/green (`#89b4fa`/`#a6e3a1`) instead of the accessibility palette; the start/end handles and range overlays never track `CAT["accent"]` in any theme; and even in the default Dark theme the literals are stale versus the current `STREAMKEEP_DARK` tokens (blue `#6aa6ff`, green `#68d391`, crust `#060b11`), so the dialog is off-palette everywhere.
  Evidence: Read the cited lines — e.g. line 156 `self._start_handle.setBrush(QBrush(QColor(137, 180, 250)))   # CAT blue`; line 497 `p.fillRect(0, 0, w, h, QColor(24, 24, 37))  # CAT crust`; line 510 `in_color = QColor(137, 180, 250, 180)    # CAT blue`. The `# CAT blue`/`# CAT crust` comments confirm these were meant to be palette values but were frozen as literals. Contrast with the sibling `download.py` spinner widget which correctly uses `QColor(CAT["accent"])`. Both themes are user-selectable (settings.py appearance controls).
  Fix: Replace each literal with the live token: `QColor(CAT["accent"])` for the start handle / in-range waveform / crop rect, `QColor(CAT["green"])` for the end handle, `QColor(CAT["crust"])` for the waveform/preview background, `QColor(CAT["surface1"])`/`CAT["base"]` for placeholders, `QColor(CAT["peach"])` for spike ticks, and the range-overlay set from `CAT["blue"/"mauve"/"teal"/"yellow"/"pink"/"green"]`. The pure-black dim overlays over video (`QColor(0,0,0,160)`) are legitimately theme-independent and may stay.
  Acceptance: With Light and then High Contrast selected, opening Trim/Clip on a recording shows a filmstrip/waveform whose background and handles match the active theme and accent; no near-black box appears in Light theme; handles change color when the accent is changed.
  Confidence: Verified (widgets instantiated and painted; literals and their intended tokens confirmed in source).
  Effort: M

- [ ] P3 — V60 — Local server `_read_body` returns non-dict JSON, causing an AttributeError (dropped connection) on network handlers including pre-auth `/pair`
  Category: security
  Where: `streamkeep/local_server.py:803-814` (`_read_body`) and its consumers: `_handle_pair` (line ~923, `data.get("code")`, reachable pre-authentication), `_handle_send_url` (~984), `_handle_api_queue` (~1088), `_handle_api_job_cancel` (~1111), `_read_failure_id` (~1129).
  Problem: `_read_body` returns `json.loads(raw)` verbatim. A body that is valid JSON but not an object — `[]`, `"x"`, or `5` — parses without error and is returned as a list/str/int; every consumer then calls `.get(...)` on it. `[1,2].get("code")` raises `AttributeError`, which is not caught in the handler and propagates to `socketserver.process_request_thread`, which logs a traceback to stderr and drops the connection with no HTTP response. Not a crash/DoS (threaded per-connection), but a network-facing endpoint returns a dropped connection instead of a clean 400, and `/pair` is reachable without a token given only valid freshness headers + `Content-Type: application/json`.
  Evidence: Read `_read_body` (803-814) — it returns `json.loads(raw) if raw else {}` with no `isinstance` check. Read `_handle_pair` (918-928) — `data = self._read_body(max_bytes=4096)` then `pairing_store.consume(data.get("code"))` with no dict guard. The codebase already applies `isinstance(data, dict)` guards for yt-dlp JSON responses elsewhere, so this is an inconsistency.
  Fix: Coerce in one place: `data = json.loads(raw); return data if isinstance(data, dict) else {}` inside `_read_body`. All consumers then safely see `{}` and return their existing validation errors (400/401).
  Acceptance: A test posts `[]` (and `5`) to `/pair` and to `/api/queue` and asserts a clean 400/401 JSON response with no unhandled exception and no dropped connection.
  Confidence: Verified.
  Effort: S

- [ ] P3 — V61 — Monitor `auth_profile_id` column is dropped by the ChannelMonitor save/load round-trip
  Category: correctness
  Where: `streamkeep/monitor.py` — `_entry_to_dict` (~292-311) and `_load_channel_dict` (~352-385); field defined at `streamkeep/models.py:246`; column written by `db.save_all_monitor_channels` (db.py ~1096) and `migrate_from_config` (db.py ~3233).
  Problem: `auth_profile_id` (schema v11, the V50 per-monitor site-bound auth profile) is the one monitor field that `_entry_to_dict` omits and `_load_channel_dict` never rehydrates. `ChannelMonitor.save_to_db()` does a full `DELETE`+re-`INSERT` from in-memory entries, so it writes `auth_profile_id=''`, and `load_from_db()` never restores it. This is latent today (no live UI path populates a per-monitor auth profile), but the moment a per-monitor auth-profile control is wired up, the next monitor save silently wipes it.
  Evidence: `grep -n auth_profile_id streamkeep/monitor.py` returns nothing (the field never appears in monitor.py), while it appears in db.py's save/migrate paths and in models.py:246. No `ui/**` code sets `entry.auth_profile_id`.
  Fix: Add `"auth_profile_id": e.auth_profile_id or ""` to `_entry_to_dict` and load it in `_load_channel_dict`, mirroring the adjacent `auto_upgrade`/`min_upgrade_quality` handling.
  Acceptance: A round-trip test sets `entry.auth_profile_id = "ap_…"`, calls `save_to_db()` then `load_from_db()`, and asserts the value survives.
  Confidence: Verified (the drop is certain; impact is currently latent because no populating path exists yet).
  Effort: S

- [ ] P3 — V62 — First-run onboarding omits High Contrast theme and shows internal "security-ready" microcopy
  Category: a11y
  Where: `streamkeep/ui/onboarding.py:180-183` (theme radios) and `streamkeep/ui/onboarding.py:224` (`title = "FFmpeg is security-ready"`).
  Problem: (a) The Appearance step offers only Dark / Light / Follow-system radios; the WCAG-AAA High Contrast theme — the app's one accessibility affordance, fully wired via `THEMES["high_contrast"]` and selectable in Settings — is undiscoverable during onboarding, so a user who needs it must hunt through Settings. (b) The first-run success banner title reads "FFmpeg is security-ready", surfacing an internal capability-registry concept ("security-ready") to a brand-new user who has no context for why security relates to a media encoder being present; the paired failure title "FFmpeg needs repair" is clearer.
  Evidence: Read onboarding.py:180-183 — exactly three `QRadioButton`s (Dark/Light/Follow system), no high-contrast option; `_finish` writes the selected value to `config["theme"]` and passes it to `apply_visual_system`, which already accepts `"high_contrast"`. Read line 224 — `title = "FFmpeg is security-ready"` shown via the welcome-page status banner.
  Fix: Add a fourth radio mapping to `"high_contrast"` in the theme step; change the banner title to plain phrasing such as "FFmpeg is ready" / "FFmpeg detected".
  Acceptance: Onboarding shows a High Contrast option that, when chosen and finished, leaves `config["theme"] == "high_contrast"` and applies it; the success banner no longer contains the word "security-ready".
  Confidence: Verified.
  Effort: S

- [ ] P3 — V63 — feed.py / gallery.py XML/HTML attribute escaping does not escape quotes (latent; harden with the gallery/RSS publishing item)
  Category: security
  Where: `streamkeep/feed.py:80` (`<enclosure url="{escape(media_url)}" …/>`) and `streamkeep/gallery.py:87` (`href="{base_url}/share/{sid}"`, `base_url` injected unescaped).
  Problem: `xml.sax.saxutils.escape` escapes `& < >` but NOT `"`, and the gallery `href` interpolates `base_url` with no escaping at all. Both are attribute contexts, so an unescaped `"` would break out of the attribute — attribute injection. Currently non-exploitable because share IDs are hex tokens and `base_url` is server-derived, and these modules are dead code (no server route dispatches them; `capabilities.py` already flags gallery.py as unreachable). This refines, not duplicates, the existing "Complete authenticated gallery and RSS publishing" P2 item: whoever wires those routes must fix the escaping and add the auth/scope/Host/origin gate the `/api/*` routes have (these handlers have none).
  Evidence: Read feed.py:80 (uses `escape` from `xml.sax.saxutils`, which per the stdlib does not escape quotes unless passed an entities map) and gallery.py:87 (`base_url` interpolated raw). `grep` confirms `serve_media_range`/`render_gallery_html`/`generate_rss`/`register_shared` are referenced only by tests, and `local_server._build_handler` dispatches no `/gallery`, `/share`, `/media`, or `/feed` route.
  Fix: Use `escape(value, {'"': "&quot;"})` (or `xml.sax.saxutils.quoteattr`) for every attribute-context value, and escape `base_url`; when the publishing item is implemented, put these routes behind the same token/scope/Host/origin gate as `/api/*`.
  Acceptance: A unit test renders a feed enclosure and a gallery share link with a value containing `"` and `<` and asserts both are entity-escaped in the output; the routes, once wired, reject unauthenticated requests.
  Confidence: Verified (escaping gap confirmed; exploitation is latent because the modules are unreachable and inputs are currently trusted).
  Effort: S

- [ ] P3 — V64 — Test suite carries dead imports and a placeholderless f-string (ruff-clean-up)
  Category: testing
  Where: `tests/test_bandwidth.py:9` (`_LazyTracker` unused), `tests/test_channel_stats.py:2,5` (`time`, `unittest.mock` unused), `tests/test_podcast_sidecars.py:6` (`pytest` unused), `tests/test_credential_check.py:106` (F541 f-string with no placeholder); also `StreamKeep.py:73` (E702 two statements on one line), `streamkeep/metadata.py:397` and `streamkeep/ui/tabs/settings.py:109` (E731 lambda assigned to a name).
  Problem: `ruff check .` reports 50 items; excluding the 42 intentional `E402` launcher-import-ordering entries, the remainder are genuine small cleanups. Dead imports in tests obscure real dependencies and mildly slow collection; the F541 and E731/E702 are style nits the project's own hygiene rules would flag. pyflakes (the project's configured linter) is already clean, so these are ruff-only.
  Evidence: `py -3.12 -m ruff check . --output-format=concise` lists each with file:line. There is no ruff config in the repo (pyflakes is the enforced linter), so these are advisory.
  Fix: Remove the four unused imports, add a placeholder or drop the `f` prefix at test_credential_check.py:106, and (optionally) split the E702 line and convert the two E731 lambdas to `def`. Do not attempt to "fix" the 42 `E402` items — the launcher deliberately orders imports after runtime guards.
  Acceptance: `py -3.12 -m ruff check . --select F,E7` reports no errors (E402 left as-is); pyflakes and the full test suite remain green.
  Confidence: Verified.
  Effort: S

- [ ] P3 — V65 — `init_db()` schema migration has no cross-process serialization; concurrent first-start after an upgrade can crash on duplicate `ALTER TABLE`
  Category: reliability
  Where: `streamkeep/db.py:51-97` (`init_db`) and the migration helpers it calls (`_migrate_queue_v4/v5`, `_migrate_monitor_v6`, `_migrate_execution_v8`, `_migrate_identity_v9`, `_migrate_retry_v10`, `_migrate_auth_profiles_v11`).
  Problem: `init_db` reads `PRAGMA user_version`, runs the migration chain, and sets the new version with no `BEGIN IMMEDIATE` and no `_write_lock`; each `ALTER TABLE ADD COLUMN` autocommits individually. The GUI (`main_window.py`), headless service (`headless_service.py`), and CLI all call `init_db` at startup, and the GUI + headless service are designed to run simultaneously on the same profile (the executor-lease model), with `init_db` executing before any lease is acquired. If both processes start together right after an app upgrade that bumps `SCHEMA_VERSION`, both read the pre-migration columns via `PRAGMA table_info` (the `if col not in existing_cols` guard is evaluated on each process's own snapshot before either ALTERs), both attempt the same `ALTER TABLE`, and the loser raises `sqlite3.OperationalError: duplicate column name` out of `init_db`, failing that process's startup. Data stays consistent (fails closed), but it is a one-time visible crash on the exact launch users hit after updating.
  Evidence: Read `init_db` (db.py:51-97) — the migration calls at lines 64-80 and the `PRAGMA user_version` write at line 94 run with no transaction until the trailing `db.commit()` at line 95; only the two index-creation statements have `try/except`, and the `ADD COLUMN` migrations do not (SQLite has no `ADD COLUMN IF NOT EXISTS`). No lock file or `BEGIN IMMEDIATE` appears anywhere in the function.
  Fix: Wrap the version check + migration chain in `BEGIN IMMEDIATE … COMMIT`, re-reading `user_version` after the reserved lock is held and early-returning if another process already migrated; or catch `duplicate column name` from the `ADD COLUMN` migrations as benign and continue.
  Acceptance: A test runs `init_db` from two threads on separate connections against a v10 database with a barrier; both return without exception and the DB lands at v11.
  Confidence: Verified (code path confirmed; the race window itself was not reproduced).
  Effort: S

- [ ] P3 — V66 — A dead process's in-progress backup claim reports "running" in the ops view until the next cadence-due claim
  Category: correctness
  Where: `streamkeep/db.py:2700-2727` (`claim_due_backup`, the not-due branch) and `streamkeep/db.py` `backup_state_public_view` (~2892-2913).
  Problem: When a process dies mid-backup, `backup_runs.running_owner`/`running_since` stay set. The staleness gate at the top of `claim_due_backup` only prevents a stale claim from *blocking* a new claim (it rolls back and returns None only when the existing claim is younger than `BACKUP_CLAIM_STALE_SECONDS`); it does not clear a stale owner. On the not-due path (`current < next_run_at`), the `ON CONFLICT DO UPDATE` writes only `next_run_at`/`cadence_seconds`/`updated_at`, leaving the stale `running_owner` in place. `backup_state_public_view` computes `"running": bool(running_owner)`, so the `/api` backup status (and any UI reading it) reports a backup as running for up to a full cadence — a week on weekly cadence — after a crash. It self-heals only when a claim actually becomes due (the due branch overwrites `running_owner`).
  Evidence: Read the not-due upsert (db.py:2719-2727) — its `DO UPDATE SET` list contains no `running_owner=` assignment; only the due-claim branch (2731-2745), `finish_backup_run`, or `release_backup_claim` overwrite it, none of which run until the next due time on a machine where the crashed process never calls finish. The staleness comparison (`current - running_since < BACKUP_CLAIM_STALE_SECONDS`) only guards the early rollback/return, not the persisted owner.
  Fix: In `claim_due_backup`, when `running_owner` is stale (age ≥ `BACKUP_CLAIM_STALE_SECONDS`), clear it in the same write even on the not-due path; or have `backup_state_public_view` treat a `running_since` older than the stale threshold as not running.
  Acceptance: After simulating a claim with `running_since = now - 2h` and calling `claim_due_backup` when nothing is due, `backup_state_public_view(load_backup_state())["running"]` is False.
  Confidence: Verified (static analysis of both branches).
  Effort: S

### Unaudited — needs a dedicated pass

The 2026-08-02 audit prioritized security/subprocess boundaries, the download/worker/data layer, and UI theme/UX/a11y, tracing across module boundaries and running the suite/linters to confirm findings (the GUI was traced from source, not driven live — no display was launched). The following larger modules received only a smell-level sweep and warrant their own pass: `streamkeep/cli.py` (~67 KB), `streamkeep/headless_service.py` (~42 KB) beyond its SSRF entry point, `streamkeep/download_options.py`, `streamkeep/job_spec.py`, `streamkeep/maintenance.py`, `streamkeep/lifecycle.py`, `streamkeep/tags.py`, `streamkeep/metadata.py` internals, and the `streamkeep/player/` package interactions. Performance was spot-checked and found clean (prior passes removed the known O(n²) hotspots); a dedicated profiling pass over large-archive History/Storage/Analytics rendering with 100k+ rows has not been done.
