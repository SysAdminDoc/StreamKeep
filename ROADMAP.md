# StreamKeep Roadmap

StreamKeep is a Python/PyQt6 desktop downloader and archive manager for live streams, VODs, podcasts, and direct media URLs. This roadmap tracks pending work only; historical feature lists are archived under `docs/archive/roadmap/`.

## Planning Docs

- Current completed state: `COMPLETED.md`
- Research synthesis: `RESEARCH.md`
- Legacy research: `RESEARCH_REPORT.md`
- Legacy roadmap archive: `docs/archive/roadmap/ROADMAP-legacy.md`
- Legacy feature-candidate archive: `docs/archive/roadmap/features.md`

## Current Baseline

- Current package version: v4.43.3.
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

- [ ] V14 — MSE buffer recorder (DRM-free only)
  What: Playwright init-script hook on SourceBuffer.appendBuffer teeing segments to disk; ffmpeg concat/remux; hard-refuse on any EME session; tab-open/playback-speed limitations documented.
  Effort: L

#### VP-P2 — Automation, lifecycle, and reach

- [ ] V16 — URL-pattern → profile auto-selection + zero-dialog Smart Mode toggle. Effort: M
- [ ] V18 — Media-server output layouts per monitor (Jellyfin/Plex/Kodi S/E naming + NFO). Effort: M
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

- [ ] P1 — V33 — YouTube PO-token: JS-runtime version gate + guided/managed provider
  Why: `ytdlp.py` only *detects* a PO-token provider and `capabilities.py` only checks JS-runtime *presence*; an out-of-date Deno/Node silently downgrades YouTube to storyboard-only. yt-dlp raised hard floors (Deno ≥2.3.0, Node ≥22) and PO tokens are now video-ID-bound.
  Evidence: `streamkeep/extractors/ytdlp.py` (`youtube_pot_provider_status`, `youtube_health_report`), `streamkeep/capabilities.py` (presence-only); https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide; https://github.com/Brainicism/bgutil-ytdlp-pot-provider; yt-dlp 2026.06.09 runtime floors. Complements V32 (remote backend) — share the provider/health-config surface.
  Touches: `capabilities.py` runtime version parsing, `ytdlp.py` health report + extractor-args injection, optional provider process lifecycle, Settings YouTube panel, tests.
  Acceptance: health doctor reports the actual Deno/Node version and flags it as unsupported below the yt-dlp floor with a one-line fix; when a provider is installed, its base_url extractor-arg is injected into every YouTube job; a "set up PO-token provider" action either installs+launches a local bgutil sidecar (127.0.0.1:4416) or gives copy-paste install steps; absence degrades to current behavior; unit tests cover version-below-floor and provider-injected paths.
  Complexity: M (version gate is S; managed sidecar is L — stage them)

- [ ] P1 — V35 — Windows distribution: PyInstaller onedir + installer (retire 520 MB onefile)
  Why: the onefile exe re-extracts its full ~520 MB payload to a temp dir on every launch (slow cold start), maximizes AV false-positive surface (unsigned), and races the `_MEIxxxx` temp dir on double-launch. Onedir removes all three.
  Evidence: `StreamKeep.spec` (single `EXE`, no `COLLECT`); https://www.pythonguis.com/faq/problems-with-antivirus-software-and-pyinstaller/; https://github.com/pyinstaller/pyinstaller/issues/6754; RESEARCH.md 2026-07-27 open question on installer format.
  Touches: `StreamKeep.spec` (add `COLLECT`), `packaging/build.py`, an Inno/NSIS script (unsigned), `updater.py` self-replace flow (must swap a directory/installer, not a single exe), release artifact + smoke test.
  Acceptance: `packaging/build.py` produces a onedir tree plus an unsigned installer; cold start is measurably faster than the onefile; the self-update path swaps the installed onedir/installer without leaving orphans; artifact smoke passes headless; double-launch no longer races temp extraction.
  Complexity: M

- [ ] P1 — V36 — Live-capture reliability: fragment-gap recovery + optional ytarchive engine
  Why: yt-dlp `--live-from-start` drops fragments on unstable streams (open issues #13359/#15921/#16673) and users route to ytarchive/streamlink for reliability-critical captures; StreamKeep is yt-dlp/ffmpeg-only for live.
  Evidence: https://github.com/yt-dlp/yt-dlp/issues/13359, /15921, /16673; https://github.com/Kethsar/ytarchive; existing optional-engine pattern in `streamkeep/integrations/`. Pairs with V13 (streamlink) as multi-engine live fallback.
  Touches: a generalized typed download-engine interface (factor out of `integrations/gallery_dl.py`/`lux.py`), an `ytarchive` engine adapter, live-fragment gap detection/retry in `workers/download.py`, capability detection in `capabilities.py`, Settings engine preference, tests.
  Acceptance: when yt-dlp live capture reports fragment gaps, the job either recovers the missing fragments or (opt-in) falls back to ytarchive for a from-start capture; the engine is optional and detected like gallery-dl/lux; absence degrades to current yt-dlp behavior; a fixture reproduces a gap and asserts recovery/fallback.
  Complexity: L

#### P2 — Later

- [ ] P2 — V44 — Fast field-filtered resolve for post-live manifestless VODs
  Why: resolving a former-livestream YouTube VOD via `--dump-json` forces yt-dlp to generate every format's full fragment list (~2 min, ~45 MB JSON for a multi-hour VOD). v4.43.3 raised the timeout so it *works*, but every resolve of such a URL still blocks for minutes. A field-filtered `--print "%(formats.:.{format_id,vcodec,acodec,height,width,tbr,ext,format_note,abr,url})j"` returns the same metadata StreamKeep needs in ~1.3s because it never requests fragments (measured 112s → 1.3s on a 3h30m VOD).
  Evidence: verified locally 2026-07-27 (`yt-dlp` 2026.07.04, video F_eSJadnEh4 in "Post-Live Manifestless mode"); `streamkeep/extractors/ytdlp.py` `resolve()` currently parses full `--dump-json`. Fragments are regenerated at download time regardless, so deferring them costs nothing.
  Touches: `ytdlp.py` resolve — replace/augment `--dump-json` with a `--print` field projection (formats + chapters/subtitles via their own `%(...)j` fields), keep the full-json path as a fallback, tests for both paths.
  Acceptance: resolving a post-live manifestless VOD completes in seconds instead of minutes; quality list, audio pairing, chapters, and subtitles match the current `--dump-json` output; a fixture asserts equivalence between the fast and full paths.
  Complexity: M

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

- [ ] P2 — V39 — Expose output filename-template in CLI/config + fix ffmpeg-path `.mp4` hardcode
  Why: the GUI (`adv_file_tpl_input`/`adv_folder_tpl_input`) and monitor-channel overrides (`models.py:213 override_filename_template`) support templating, but the CLI takes only `-o DIR` and the ffmpeg-native path hardcodes `.mp4` at `workers/download.py:988`, so headless/scripted users cannot control naming and non-mp4 ffmpeg captures get a wrong extension.
  Evidence: `streamkeep/cli.py` (only `-o/--output`), `streamkeep/workers/download.py:988`, `streamkeep/ui/tabs/download_controls.py`; MeTube per-channel `OUTPUT_TEMPLATE` (https://github.com/alexta69/metube/releases/tag/2026.07.10).
  Touches: `cli.py` args, one shared template resolver, `workers/download.py` ffmpeg output path (honor container), config default key, README, tests.
  Acceptance: CLI accepts a filename/folder template and a global config default applies to GUI+CLI+monitor jobs; the ffmpeg-native path names output by the chosen container, not always `.mp4`; template resolution has one code path with a unit test.
  Complexity: S

- [ ] P2 — V40 — Dubbed-audio-language selection + clean `mute` (audio-strip) output mode
  Why: cobalt exposes `youtubeDubLang` and a `mute` mode; StreamKeep's per-download overrides cannot pick a dubbed audio track or produce a video-only output cleanly.
  Evidence: https://github.com/imputnet/cobalt/blob/main/docs/api.md; `streamkeep/ui/tabs/download_controls.py`, `streamkeep/download_options.py`. Extends V20.
  Touches: `download_options.py` (dub-lang + mute validation), `download.py` yt-dlp/ffmpeg args (`--audio-multistreams`/language selection; `-an` for mute), `download_controls.py` UI, tests.
  Acceptance: a per-download control selects a dubbed audio language when the source offers one, and a `mute` toggle produces a video-only file without a stray empty audio track; both round-trip through the override payload with unit tests.
  Complexity: M

- [ ] P2 — V41 — Music metadata auto-fill (album-artist) for SoundCloud/Audius/podcast audio
  Why: StreamKeep writes metadata but audio-only outputs frequently lack album-artist, which breaks media-library grouping; MeTube added a dedicated post-processor for exactly this.
  Evidence: https://github.com/alexta69/metube/releases/tag/Release%202026.07.16; `streamkeep/metadata.py`, `streamkeep/extractors/{soundcloud,audius,podcast}.py`, `streamkeep/postprocess/`.
  Touches: a metadata post-processor that fills album-artist (and album where derivable) from uploader/channel fields, audio extractors, tests.
  Acceptance: audio downloads from SoundCloud/Audius/podcast RSS get album-artist populated from source fields when absent; existing tags are never overwritten; a unit test covers fill + no-overwrite.
  Complexity: S

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


