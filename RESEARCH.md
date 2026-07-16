# Research — StreamKeep
Date: 2026-07-15 — replaces all prior research.

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
