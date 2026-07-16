# Research — StreamKeep

## 2026-07-16 — Versatility & Capability-Breadth Pass

Mission: make StreamKeep the single most versatile video/audio download tool available — any video or audio, from any website, in any format, at any quality the source offers, with full user control. This pass surveyed the engine layer (yt-dlp, streamlink, N_m3u8DL-RE, aria2, ffmpeg-direct), the application layer (JDownloader 2, 4K Video Downloader+, Video DownloadHelper, Stacher, Parabolic, Seal, Open Video Downloader, ClipGrab, MediaHuman, Allavsoft, Downie/Permute, Persepolis, Motrix, XDM), alternative engines (gallery-dl, cobalt, lux, you-get, ytdl-sub), self-hosted archivers (Tube Archivist, Pinchflat, MeTube), and browser-side recon (The Stream Detector, cat-catch, FetchV, MSE/HAR capture techniques). All claims verified against official docs/repos fetched 2026-07-16; StreamKeep column verified against the codebase at v4.31.7 (364 tests green).

DRM boundary (hard): no Widevine/PlayReady/FairPlay decryption, no key extraction, no paywall bypass. CENC `--key` decryption features of N_m3u8DL-RE are explicitly excluded. AES-128/SAMPLE-AES clear-key HLS (keys served openly by the playlist/site) is standard HLS, in scope. MSE capture is in scope only for non-EME streams; any EME-session detection must refuse capture.

### Capability Matrix

● full, ◐ partial, ○ absent. SK = StreamKeep v4.31.7. Columns: yt-dlp (CLI), streamlink, N_m3u8DL-RE, gallery-dl, cobalt, JDownloader 2, Stacher, Downie, archivers (Pinchflat/TubeArchivist/ytdl-sub).

| Capability | SK | yt-dlp | strmlnk | N_m3u8 | gal-dl | cobalt | JD2 | Stacher | Downie | archivers |
|---|---|---|---|---|---|---|---|---|---|---|
| Site breadth (1700+ via engine) | ● | ● | ◐ live-only | ○ | ◐ galleries | ◐ ~20 | ● hosts | ● | ● | ● |
| Image/gallery/social-post media | ○ | ◐ | ○ | ○ | ● ~650 sites | ◐ picker | ◐ | ○ | ◐ | ○ |
| Generic page sniffing (headless) | ● | ◐ generic IE | ○ | ○ | ○ | ○ | ● deep-scan | ○ | ● | ○ |
| User-guided browser extraction | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ● | ○ |
| Raw manifest paste (m3u8/mpd) | ● | ● | ● | ● +ISM | ○ | ○ | ◐ | ● | ◐ | ○ |
| Manifest multi-track select (audio/sub renditions) | ○ | ● | ◐ | ● track table | ○ | ○ | ○ | ◐ | ○ | ○ |
| Clear-key override (AES-128 non-DRM) | ○ | ● generic:hls_key | ● key-uri | ● | ○ | ○ | ○ | ○ | ○ | ○ |
| Live capture + auto-record monitors | ● | ◐ | ● | ◐ | ○ | ○ | ○ | ○ | ○ | ◐ |
| Live-from-start / DVR rewind | ○ | ◐ 4 sites | ● restart/offset | ◐ backfill | ○ | ○ | ○ | ○ | ○ | ○ |
| Live latency/segment-thread tuning | ○ | ○ | ● | ◐ | ○ | ○ | ○ | ○ | ○ | ○ |
| Twitch ad-segment filtering | ○ | ○ | ● mandatory | ◐ ad-keyword | ○ | ○ | ○ | ○ | ○ | ○ |
| Raw protocols (RTSP/RTMP/SRT/UDP/ICY radio) | ○ | ◐ via ffmpeg | ◐ | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| Playlist/channel ranges + filters | ◐ all-or-pick | ● -I/date/match | ○ | ○ | ◐ | ○ | ◐ | ◐ | ◐ | ● rules |
| Incremental sync (download-archive) | ◐ dedup db | ● | ○ | ○ | ● sqlite | ○ | ◐ | ◐ | ○ | ● |
| Cookie/auth-gated content | ● | ● +keyring/container | ● | ◐ headers | ● +OAuth | ● accounts | ● login | ◐ | ● guided | ◐ |
| Full quality picker (res/fps/codec/bitrate) | ◐ res+audio rows | ● -f/-S language | ◐ named | ● selectors | n/a | ◐ enums | ◐ | ● table | ◐ | ◐ |
| Raw format-spec / args escape hatch | ○ | ● | ◐ | ● | ● conf | ○ | ◐ | ● | ○ | ● YTDL_OPTIONS |
| Audio-only extraction (-x fmt/quality) | ◐ post-hoc mp3 | ● 8 formats | ○ | ◐ | ○ | ● | ◐ | ● | ● | ● |
| Container choice at download | ○ forced mp4 | ● remux rules | ○ | ● mp4/mkv | ○ | ◐ | ○ | ● | ● | ● |
| Trim/sections at download time | ● F21 crop | ● +chapter regex | ◐ offset/dur | ● range | ○ | ○ | ○ | ◐ | ○ | ○ |
| Subtitle langs/auto/convert/embed | ◐ en-only | ● | ◐ mux | ● in-segment | ○ | ◐ lang | ○ | ● | ◐ | ● |
| SponsorBlock categories mark/remove | ◐ 3 fixed, remove-only | ● 13 cat, mark+remove | ○ | ○ | ○ | ○ | ○ | ● | ○ | ● |
| Metadata/NFO/thumb/chapters | ● | ● embed suite | ○ | ◐ | ● sidecar | ○ | ○ | ◐ | ○ | ● NFO layouts |
| Filename templating | ◐ 10 fields | ● ~100 fields+ops | ◐ | ● save-pattern | ● | ◐ styles | ● packagizer | ● | ◐ | ● |
| Multi-connection acceleration | ◐ direct-HTTP only | ◐ -N frags/aria2c | ◐ seg threads | ● | ○ | ○ | ● | ◐ | ◐ | ○ |
| Resume + integrity verify | ● manifests | ◐ --continue | ○ | ● seg-count | ◐ | ○ | ● | ○ | ○ | ◐ |
| Proxy / TLS impersonation / geo | ● pool+curl_cffi | ● +xff | ● | ◐ | ● | ○ | ● +reconnect | ◐ | ○ | ◐ |
| Rules/profiles automation (URL→preset) | ○ | ◐ config | ○ | ○ | ● per-extractor | ○ | ● packagizer | ● URL→profile | ○ | ● presets |
| Retention / quality-upgrade lifecycle | ◐ keep-last N | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ● Pinchflat |
| GUI / CLI / REST / extension / player | ●●●●● | ○●○○○ | ○●○○○ | ○●○○○ | ○●○○○ | ●○●○○ | ●○◐●○ | ●○○◐○ | ●○○●○ | ●◐●◐◐ |

Reading: StreamKeep already leads the *application* field on interface breadth (only tool with GUI+CLI+REST+extension+player), reliability plumbing (resume manifests, failed-job ledger, integrity verify), and live auto-record. It trails **yt-dlp's own option surface** (format language, subtitles, SponsorBlock depth, playlist filters, archive sync, fragment concurrency), **streamlink** on live transport (latency, DVR, Twitch ads), **N_m3u8DL-RE** on raw-manifest depth (track tables, clear keys, MSS), **gallery-dl** on the non-video half of the internet, and **JDownloader/Stacher** on automation ergonomics (rules, URL→profile). No tool in the field does raw-protocol capture (RTSP/SRT/ICY) — an open leapfrog lane, as is Downie-style user-guided extraction on Windows.

### Ranked Feature Harvest (deduplicated; effort S/M/L/XL; risk = maintenance/legal/complexity)

P0 — depth of control (the "full user control" mandate; all pure yt-dlp passthrough + UI):
1. **Per-download Format & Output panel** — raw yt-dlp format-spec field, format-sort (-S) presets ("prefer AV1", "cap 1080p", "smallest"), container choice mp4/mkv/webm/original (--merge-output-format/--remux-video), audio-extract mode (-x --audio-format best/mp3/m4a/opus/flac/wav + --audio-quality). Proof: yt-dlp, Stacher. Approach: extend DownloadWorker cmd builder + advanced panel + CLI flags. Effort M. Risk low.
2. **Subtitle suite** — language multi-select fed by resolve()'s subtitle listing, auto-subs toggle, --convert-subs format, embed vs sidecar. Proof: yt-dlp, Stacher. Approach: settings + per-download override; extend `_build_ytdlp_download_cmd`. Effort S-M. Risk low.
3. **SponsorBlock category matrix** — mark vs remove per 13 categories, custom API URL. Proof: yt-dlp. Effort S. Risk low.
4. **Playlist ranges & filters** — -I item ranges, date before/after, --match-filters, --max-downloads; per-item picker already exists for VOD lists. Proof: yt-dlp, lux. Effort M. Risk low.
5. **Download-archive sync** — per-monitor/per-source --download-archive + --break-on-existing for incremental channel pulls. Proof: yt-dlp, ytdl-sub, gallery-dl. Effort S-M. Risk low.
6. **Fragment concurrency + retry matrix** — -N concurrent-fragments, --retries/--fragment-retries/--retry-sleep, --skip-unavailable-fragments policy, --throttled-rate. Proof: yt-dlp. Effort S. Risk low.
7. **Named yt-dlp argument templates** — user-managed raw-arg snippets attachable per download/monitor (power escape hatch), plus per-job "export as yt-dlp/ffmpeg command" with headers/cookies. Proof: Seal, The Stream Detector. Effort M. Risk low (validate/deny-list dangerous flags).
8. **Live depth** — --live-from-start toggle (YouTube/Twitch), --wait-for-video polling, chapter/metadata embed toggles (--embed-chapters/--embed-metadata/--embed-thumbnail). Effort S-M. Risk low.

P1 — breadth (new source classes):
9. **HLS rendition groups + DASH multi-representation** — parse EXT-X-MEDIA audio/sub renditions and all DASH Representations into a track table (video+audio+subs multi-select); mux selected tracks. Proof: N_m3u8DL-RE. Effort L. Risk medium (parser correctness; fixtures needed).
10. **Clear-key override for mis-declared HLS** — UI fields mapping to yt-dlp `generic:hls_key=URI|KEY[,IV]` and native AES-128 key/IV override. Proof: yt-dlp, streamlink, N_m3u8DL-RE. Effort S. Risk low-medium (document non-DRM scope).
11. **Raw-protocol capture jobs** — RTSP (cameras), RTMP-listen (receive OBS), SRT caller/listener, UDP/RTP multicast (IPTV), ICY radio with now-playing track splitting; ffmpeg reconnect family; duration caps. Proof: ffmpeg. Effort L. Risk low (ffmpeg is bundled ally). **Leapfrog: no competing GUI has this.**
12. **gallery-dl second engine** — route image/gallery/social-post URLs (Twitter media, Instagram posts, Pixiv, boorus, Patreon-free) to gallery-dl with shared folder/archive config. Proof: gallery-dl. Effort M. Risk medium (new dep; optional install).
13. **User-guided extraction** — open a visible Playwright window; user navigates/logs in/plays; response sniffer surfaces manifests/media with variant picker; queue with captured headers/cookies. Proof: Downie (macOS-only, closed). Effort L. Risk medium. **Leapfrog: only living Windows implementation.**
14. **Extension network sniffer + header handoff** — MV3 webRequest capture of m3u8/mpd/media + request headers; one-click send with full request context. Proof: VDH v10, Stream Detector (dead — users unserved). Effort M. Risk medium (MV3 limits).
15. **streamlink engine for live** — optional in-process engine for Twitch/Kick live: mandatory ad-filtering, low-latency, DVR rewind (--hls-start-offset), stream-up polling. Proof: streamlink. Effort L. Risk medium (second engine surface).
16. **MSE buffer recorder (DRM-free)** — Playwright init-script hook on SourceBuffer.appendBuffer teeing segments; ffmpeg concat; hard-refuse when EME session detected. Proof: FetchV/cat-catch. Effort L. Risk high (legal-line engineering must be explicit).

P2 — automation & lifecycle:
17. **Rules engine (Packagizer-style)** — ordered rules: match site/uploader/title-regex/duration/type → set folder, template, preset, priority, proxy, auto-start. Proof: JDownloader. Effort L. Risk low.
18. **URL-pattern → profile auto-selection** — paste decides the config profile; zero-dialog Smart Mode toggle. Proof: Stacher 7, 4K VD+. Effort M. Risk low.
19. **Quality-upgrade redownload + retention policies** — re-probe recent items for better formats; delete after N days/watched/keep-last. Proof: Pinchflat. Effort M. Risk low.
20. **Media-server output layouts** — per-monitor Jellyfin/Plex/Kodi season/episode naming + NFO (builds on existing NFO writer). Proof: ytdl-sub. Effort M. Risk low.
21. **YouTube health doctor** — detect Deno/EJS runtime (required since yt-dlp 2025.11.12), PO-token provider plugin status, player_client strategy presets; surface degraded-capability warnings. Proof: yt-dlp ecosystem. Effort M. Risk medium (moving target — that's the point).
22. **Pre-queue validation probe + picker responses** — probe on add: title/duration/formats/warnings (geo, login, video password) before commit; cobalt-style picker for multi-media posts. Proof: Parabolic, cobalt. Effort M. Risk low.
23. **aria2c external downloader routing** — optional per-protocol --downloader with sanitized URLs (CVE-2026-50574 precedent). Proof: yt-dlp, Seal. Effort S. Risk medium (URL sanitization mandatory).
24. **HAR import** — parse DevTools/Playwright HAR, extract media/manifest URLs + headers into link table. Proof: Playwright ecosystem. Effort S-M. Risk low.
25. **Protocol handler + bookmarklet + iOS Shortcut** — `streamkeep://add?url=`, JS bookmarklet, documented Shortcut against REST server. Proof: MeTube. Effort S. Risk low.
26. **Queue-complete power actions** — notify/sleep/shutdown/run-hook on queue drain. Proof: XDM. Effort S. Risk low.
27. **lux fallback for CN platforms** — optional engine routing for bilibili/douyin/youku when yt-dlp fails. Proof: lux. Effort M. Risk medium (regional QA hard from here).

### 2026 engine-state facts that shape the work
- yt-dlp ≥2025.11.12 REQUIRES an external JS runtime (Deno ≥2.3.0 recommended) for full YouTube; without it YouTube silently degrades. PO tokens are video-ID-bound (~12h), provider plugins (bgutil et al.) are the sanctioned path; defaults `android_vr,web_safari` currently dodge SABR-only enforcement. StreamKeep must surface runtime health or ship "broken YouTube" invisibly.
- yt-dlp CVEs this cycle: 2026-50019 (curl downloader cookie leak), 2026-50023 (filename sanitization), 2026-50574 (aria2c manifest RCE — sanitize handoff), 2026-55404 (--write-link injection). Keep bundled yt-dlp ≥2026.06.09.
- streamlink 7.5.0+ made Twitch ad-filtering mandatory (ads now MPEG-4 segments); 8.x generalized segment tuning. curl-impersonate v2.0.0rc2 adds newer Chrome fingerprints — track curl_cffi releases.
- ffmpeg 8.x http options (`reconnect_on_http_error`, `respect_retry_after`, `seg_max_retry`, `live_start_index`) are the live-capture resilience layer; version-gate their use.

### Sources
Engine docs: yt-dlp README + wiki (EJS, PO-Token-Guide) + releases 2025.10.22→2026.07.04; streamlink.github.io/cli.html + plugins; github.com/nilaoda/N_m3u8DL-RE (+v0.5.0/v0.6.0 notes); aria2.github.io/manual; ffmpeg.org/ffmpeg-protocols.html + ffmpeg-formats.html. Apps: jdownloader.org wiki; 4kdownload.com; downloadhelper.net + VDH-V10 wiki; github.com/54ac/stream-detector; stacher.io; github.com/NickvisionApps/Parabolic; github.com/JunkFood02/Seal; github.com/jely2002/youtube-dl-gui; clipgrab.org; mediahuman.com; allavsoft.com; software.charliemonroe.net/downie + /permute; github.com/persepolisdm/persepolis; github.com/agalwood/Motrix; github.com/subhra74/xdm. Alt engines: github.com/mikf/gallery-dl (+supportedsites); github.com/imputnet/cobalt (+docs/api.md); github.com/iawia002/lux; github.com/soimort/you-get; github.com/jmbannon/ytdl-sub (+readthedocs); github.com/tubearchivist/tubearchivist; github.com/kieraneglin/pinchflat; github.com/alexta69/metube. Browser recon: github.com/xifangczy/cat-catch; fetchv.net/bufferrecorder; web.dev MSE docs; playwright.dev network/HAR docs.

---

# Prior research (2026-07-15) — hardening & product-quality pass

## Executive Summary

StreamKeep is already a broad local-first PyQt6 archive workstation: native and yt-dlp extraction, live monitoring, durable SQLite state, media processing, playback, a local server, and Windows packaging are its strongest shape. Its highest-value direction is not more surface area; it is proving that existing capabilities are secure, reachable, recoverable, and operable in the frozen application. Priority mapping is P0 = Now, P1 = Next, P2 = Later, and P3 = Under Consideration.

1. Re-enable the Chromium sandbox and constrain headless scraping.
2. Put every credential, export, and backup behind one secure-storage boundary.
3. Make parallel HTTP resume representation-safe.
4. Establish a frozen-executable startup contract and crash-safe signed updates.
5. Make CLI and headless service commands perform what they acknowledge.
6. Enforce dependency/runtime security floors through an explicit health/repair flow.
7. Gate release claims on reachable, integration-tested capability paths.
8. Complete keyboard, screen-reader, high-contrast, density, and scaling behavior.
9. Replace all-row convenience tables with paged model/view data access and complete real i18n.
10. Finish archive maintenance, publishing/export, and intelligence as explicit vertical slices before adding new feature families.

## Product Map

- Core workflows: inspect a URL, choose formats, queue/download/resume, monitor or record channels, search/play/clip/post-process media, and verify or recover an archive.
- User personas: individual stream archivists, creators preserving and clipping their own media, media-library operators, and power users who want local GUI/CLI automation over yt-dlp and ffmpeg.
- Platforms and distribution: Windows-primary Python 3.10+ source and a one-file PyInstaller executable; Linux/macOS paths plus MSIX and Flatpak scaffolds exist but are not release-equivalent.
- Key integrations and data flows: curl/ffmpeg/ffprobe/yt-dlp/Playwright ingest remote media; SQLite stores queue, history, monitors, failures, and search; JSON stores preferences; the local HTTP server and MV3 extension hand URLs to the desktop process.
- Local-first boundaries: no mandatory cloud account, no telemetry requirement, no DRM circumvention, and local/manual release infrastructure under repository policy.

## Competitive Landscape

- **yt-dlp and Parabolic:** excel at rapid extractor/runtime updates and intelligible GUI exposure of yt-dlp capabilities. Learn deterministic EJS/runtime readiness, failure filtering, and update channels; avoid mirroring every raw option into the default UI.
- **MeTube:** keeps browser-to-queue intake simple and exposes a compact remote surface. Learn predictable queue semantics and extension handoff; avoid optional authentication or broad remote arguments for a desktop archive with local secrets.
- **Pinchflat and ytdl-sub:** make rules, retention, RSS, and media-server layouts first-class unattended workflows. Learn previewable source policies and portable sidecars; avoid YouTube-only assumptions and container-only operation.
- **Tube Archivist and TubeSync:** treat indexing, subscriptions, repair, and gradual retry as archive fundamentals. Learn reindex/reconcile and large-library behavior; avoid Elasticsearch-scale infrastructure and multi-service deployment for a single-user desktop tool.
- **Tartube:** demonstrates deep channel monitoring, deduplication, scheduling, and per-source control. Learn policy granularity and cross-site identity; avoid its accumulated UI complexity.
- **Streamlink and N_m3u8DL-RE:** model explicit plugin/protocol contracts and robust HLS/DASH handling. Learn fixture-driven protocol behavior and typed capabilities; avoid unsafe global headers, shell hooks, or DRM-centered scope.
- **4K Video Downloader and Downie:** commercialize private-content login, presets, browser capture, bulk workflows, and polished recovery. Learn low-friction setup and trustworthy status; avoid opaque cloud coupling, paywall-shaped UX, and automatic broad cookie collection.
- **JDownloader, [IDM](https://www.internetdownloadmanager.com/features2.html), and [HandBrake](https://handbrake.fr/docs/en/latest/technical/official-presets.html):** adjacent tools make durable queues, resume validation, schedulers, diagnostics, and named processing presets table-stakes. Borrow reliability and preview patterns; avoid torrent/general-download expansion and expert-only density as the default.

## Security, Privacy, and Reliability

- **Verified:** `streamkeep/scrape.py:173` launches Playwright Chromium with `--no-sandbox` against user-supplied sites. Remove the flag, use ephemeral contexts, deny downloads and unsafe schemes, and bound pages, bytes, and execution time.
- **Verified:** `streamkeep/ui/tabs/settings.py`, `streamkeep/ui/main_window.py`, and `streamkeep/backup.py` can persist/export tokens, webhooks, media-server credentials, `config.json`, and `cookies.txt` outside the established DPAPI/keyring path in `streamkeep/accounts.py`. Default exports/backups must be credential-free; an explicit portable secret backup needs password-authenticated encryption.
- **Verified:** `streamkeep/http.py:345-439` reuses range parts by size alone and does not bind them to a strong ETag/Last-Modified validator or require exact `206 Content-Range`; an origin change can silently assemble mixed representations.
- **Verified:** configuration import in `streamkeep/ui/tabs/settings.py:378-430` immediately activates an arbitrary dictionary, while `streamkeep/hooks.py:27-58` executes configured strings with `shell=True`. Imported hooks and outbound automations need schema validation, limits, quarantine, and explicit review.
- **Verified:** optional LAN control is plaintext HTTP with bearer credentials, and `server --token` can expose the secret in the process list. Keep loopback as default; require strong stored tokens, origin/pairing checks, and a documented TLS boundary before LAN activation.
- **Verified:** `streamkeep/updater.py` verifies a digest obtained from the same release channel but not publisher authenticity, downgrade resistance, or post-replacement health. A bad build can strand the user; the packaged startup failures observed on 2026-07-15 make last-known-good rollback a Now item.
- **Verified on 2026-07-15:** dependency lower bounds permit versions older than security releases: yt-dlp before 2026.06.09 and Pillow before 12.3.0. PATH-resolved curl/ffmpeg/SQLite/Python also need execution-time version/provenance diagnostics rather than assuming the build machine state.
- **Verified:** backup restore replaces active state before `quick_check`, foreign-key checks, and FTS validation. Restore to staging, validate with `trusted_schema=OFF`, then swap atomically.
- **Verified on 2026-07-15:** the existing roadmap's base64-secret row is stale: `streamkeep/secrets.py` defaults `allow_insecure_fallback=False` and tests require explicit opt-in. Do not duplicate it; verify eventual removal during the broader credential migration.

## Architecture Assessment

- **Verified:** `StreamKeep.py:40-45` and `streamkeep/cli.py:510-518` omit the implemented `db` and `snapshot` commands from CLI dispatch; `server --config-dir` mutates `paths.CONFIG_DIR` after `config.CONFIG_FILE` and `db.DB_PATH` were bound.
- **Verified:** `streamkeep/cli.py:335-337` acknowledges remote queue requests but only prints URLs; it neither persists nor executes jobs and exposes no real library/state provider. This concretizes the existing remote/headless roadmap item.
- **Verified:** the extension sends clip ranges and `streamkeep/local_server.py` emits `clip_received`, but `streamkeep/ui/tabs/settings.py:1176-1181` never connects it. LAN host entry also exceeds the extension's localhost-only MV3 permission.
- **Verified:** backup/restore, disk alerts, bandwidth/channel statistics, RSS, gallery, notes, summaries, smart thumbnails, native notifications, plugin startup, sidecar profiles, host profiles, and storage reconcile/import have modules/tests but no reachable application call path. Public claims need a reachability gate, then bounded vertical-slice integration.
- **Verified:** `streamkeep/ui/widgets.py:530-535` disables focus on every table; UI/player code has no accessible-name/description, buddy-label, or explicit tab-order calls. High-contrast/density/accent functions in `streamkeep/theme.py` are also unconsumed.
- **Verified:** the translation loader exists, but UI/player code has no `tr()`/`translate()` calls; English and Spanish catalogs cover only 23 messages while hundreds of labels remain hard-coded.
- **Verified:** `db.load_history()` loads all rows and `streamkeep/ui/tabs/history.py` creates a `QTableWidget` row/widget/thumbnail path for every result. Qt's model/view incremental-fetch path is the correct boundary for large archives; this refines, rather than duplicates, the existing large-library item.
- **Verified:** `streamkeep/ui/tabs/download.py` (3,915 lines), `settings.py` (2,808), `main_window.py` (1,986), and duplicate legacy/active styles in `theme.py` concentrate orchestration and visual state. Extract controllers/models while completing the related workflows, not as a rewrite.
- **Verified:** `StreamKeep.spec` collects all application/yt-dlp modules from an ambient environment; there is no lock/hash set or isolated full-bundle manifest, the SBOM inventories the environment rather than the artifact, Flatpak invokes `pip install .` without package metadata, and MSIX/Flatpak still say 4.31.4 while the app is 4.31.5.
- **Verified:** 346 source/offscreen tests pass on 2026-07-15, but the historical failures cluster in frozen startup, worker lifetime, subprocess cleanup, and packaging. Release acceptance must test the actual artifact with empty, migrated, and populated state.
- **Verified:** structured logs, redacted support snapshots, local diagnostics, durable queue/failure state, and offline-capable packaging already exist. Net-new observability is therefore limited to job IDs/terminal outcomes and exact tool provenance; always-on telemetry is excluded, while migration/upgrade safety belongs in credential migration, staged restore, and updater rollback.

## Rejected Ideas

- **DRM or paid-OTT circumvention** — rejected; [GitHub's youtube-dl policy explanation](https://github.blog/news-insights/policy-news-and-insights/standing-up-for-developers-youtube-dl-is-back/) and StreamKeep's README depend on legitimate, non-circumventing uses.
- **Smaller executable as a goal** — rejected; the user explicitly prefers a powerful self-contained bundle. Determinism, provenance, and startup proof matter more than size.
- **Electron/QML/web rewrite** — rejected; no source shows that replacing PyQt solves the verified lifecycle, security, accessibility, or integration defects.
- **Cloud-first accounts or mandatory sync** — rejected; commercial competitors offer it, but it contradicts the local-first single-operator philosophy.
- **Multi-user RBAC or a native mobile app** — rejected; authenticated responsive LAN control should be proven before expanding identity and platform scope.
- **Public plugin marketplace** — rejected; Streamlink-style extension is useful only after the existing namespace-isolation item and explicit adapter/permission contracts land.
- **AI upscaling/interpolation as core functionality** — rejected; [VideoProc markets it](https://www.videoproc.com/video-converting-software/feature-video-downloader.htm), but it alters archival fidelity and adds GPU/model maintenance before existing intelligence modules are reachable.
- **Elasticsearch-class search** — rejected; Tube Archivist's server architecture is disproportionate. SQLite FTS plus optional local embeddings fits the desktop boundary.
- **Torrent/general download-manager expansion** — rejected; borrow queue/retry patterns from JDownloader/IDM without diluting media-archive scope.
- **Silent dependency installation, broad cookie scraping, remote shell arguments, or default remote EJS/PO-token providers** — rejected; community convenience does not justify local mutation, credential exposure, or supply-chain expansion.
- **GitHub Actions/Dependabot** — rejected; repository policy requires local build, test, signing, and release evidence.

## Sources

### OSS competitors and ecosystem

- https://github.com/yt-dlp/yt-dlp/releases
- https://github.com/yt-dlp/yt-dlp/wiki/ejs
- https://github.com/NickvisionApps/Parabolic/releases
- https://github.com/alexta69/metube
- https://github.com/kieraneglin/pinchflat
- https://github.com/tubearchivist/tubearchivist
- https://github.com/meeb/tubesync
- https://github.com/axcore/tartube
- https://github.com/jmbannon/ytdl-sub
- https://streamlink.github.io/cli/plugin-sideloading.html
- https://github.com/nilaoda/N_m3u8DL-RE

### Commercial and community signals

- https://www.4kdownload.com/products/videodownloader
- https://software.charliemonroe.net/help/downie/?article=extensions
- https://my.jdownloader.org/developers/index.html
- https://www.reddit.com/r/selfhosted/comments/1en5fg8/i_tried_some_of_the_many_youtube_downloaders/
- https://www.reddit.com/r/DataHoarder/comments/1gk6wv4/a_somewhatcomprehensive_review_of_popular_youtube/

### Standards, platform guidance, and security

- https://playwright.dev/docs/docker
- https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata
- https://www.rfc-editor.org/info/rfc9106/
- https://www.rfc-editor.org/rfc/rfc9110.html
- https://www.rfc-editor.org/rfc/rfc9530.html
- https://curl.se/docs/security.html
- https://ffmpeg.org/security.html
- https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html
- https://devguide.python.org/versions/
- https://www.w3.org/TR/WCAG22/
- https://doc.qt.io/qt-6/model-view-programming.html
- https://doc.qt.io/qt-6/accessible.html
- https://doc.qt.io/qt-6/localization.html
- https://www.usenix.org/conference/usenixsecurity26/presentation/wan

## Open Questions

None.
