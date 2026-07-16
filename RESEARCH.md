# Research — StreamKeep
Date: 2026-07-16 — replaces all prior research.

## Executive Summary

[Verified] StreamKeep 4.32.0 is a mature local-first PyQt6 downloader and archive manager whose strongest shape is its combination of native/yt-dlp extraction, durable queueing, monitoring, post-processing, offline library, signed updates, and GUI/CLI/headless surfaces. The highest-value direction is not another breadth wave: close three current trust/data-safety gaps, make one job contract authoritative across every surface, and finish the integrations already claimed before expanding. Existing V5–V25 items already cover most competitor parity.

Priority opportunities:

1. Raise the yt-dlp security floor to 2026.07.04 and block unsafe write-link/raw-argument combinations.
2. Avoid the documented SQLite WAL-reset corruption race by requiring a patched runtime or degrading safely to rollback journaling.
3. Enable Chromium sandboxing and block private/special-address SSRF throughout Playwright navigation, redirects, and subrequests.
4. Replace mutable, duplicated download-worker configuration with a versioned `DownloadJobSpec` shared by GUI, CLI, headless, monitor, queue, and resume.
5. Bound and validate every remote image fetch/decode, including metadata thumbnails and chat emotes.
6. Split portable self-update from package-managed MSIX update/repair.
7. Replace raw shell hook strings with structured executable-plus-arguments actions before V24 expands hook use.
8. Add non-downloading credential/cookie probes with actionable failure classification.
9. Add an operations view over the durable queue/failure ledger, then close YouTube chat and bilingual subtitle export gaps.

## Product Map

- Core workflows: paste/probe/download; monitor channels and queue new/live media; resume/finalize/verify recordings; search/play/manage the local archive; operate through desktop, CLI, or authenticated loopback server.
- User personas: one-off downloader, long-running channel archivist, livestream recorder, offline-library curator, and local automation operator.
- Platforms and distribution: Python 3.10+ source on Windows/Linux/macOS; PyInstaller portable EXE and MSIX scaffolding on Windows; Flatpak on Linux. No current macOS signing/notarization pipeline.
- Key integrations and data flows: native extractors or yt-dlp to curl/FFmpeg; SQLite plus JSON config and resume sidecars; optional browser companion, Playwright scraper, upload/media-server adapters, RSS/gallery, plugins, and local intelligence workers.

## Competitive Landscape

- **yt-dlp / imsyy yt-dlp GUI** — Best at extractor currency, safe option depth, playlist inspection, chat/subtitle export, and independent engine evolution. Learn: expose typed, validated engine capability. Avoid: mirroring every flag directly or permitting executable options without policy.
- **Parabolic** — Best desktop reference for preview, playlist totals, failed-job filtering, translations, and bundled capability negotiation. Learn: make preflight state and failure recovery visible. Avoid: media-enhancement features before reliability and accessibility land.
- **Pinchflat / TubeSync / ytdl-sub** — Best hands-off source rules, retry/backoff, retention, delayed quality upgrades, and media-server layouts. Learn: keep automation declarative and inspectable. Avoid: YouTube-only assumptions in StreamKeep's broader engine model.
- **Tube Archivist** — Best searchable offline-library experience, subscriptions, playback state, import/rescan, and companion integrations. Learn: treat archive operations as durable workflows. Avoid: replacing Plex/Jellyfin or requiring its heavier server stack.
- **Media Downloader / Streamlink / gallery-dl** — Best evidence for specialized engine adapters rather than one universal implementation. Learn: capability-brokered adapters with explicit provenance. Avoid: unbounded engine/plugin installation or silent fallback changes.
- **4K Video Downloader Plus** — Best simple-versus-manual mode, private-content setup, and valid codec/container filtering. Learn: preflight and profiles should prevent invalid combinations. Avoid: paywalls, cloud accounts, and opaque update provenance.
- **Downie / Video DownloadHelper** — Best browser intake, guided extraction, per-job post-processing, and native-companion boundaries. Learn: user-guided capture must be scoped, explicit, and sandboxed. Avoid: DRM extraction and unrestricted browser/session access.
- **JDownloader** — Best LinkGrabber filtering, package rules, remote operation, and plugin breadth. Learn: operational filtering and failure isolation matter at scale. Avoid: default clipboard surveillance, dated complexity, and a general-purpose download-manager pivot.

## Security, Privacy, and Reliability

- [Verified] `requirements.txt`, `streamkeep/capabilities.py`, README, and Flatpak still admit yt-dlp 2026.06.09; CVE-2026-55404 is fixed in 2026.07.04. The installed research environment is fixed and `pip-audit -r requirements.txt` reported no other known advisories on 2026-07-16, but the declared floor remains unsafe.
- [Verified] the inspected Python runtime embeds SQLite 3.45.1. `streamkeep/db.py`, `search.py`, `accounts.py`, `bandwidth.py`, and `channel_stats.py` open multiple connections while the main library uses WAL; SQLite documents a rare WAL-reset corruption race fixed in 3.51.3 and backports 3.50.7/3.44.6.
- [Verified] `streamkeep/scrape.py::_launch_scrape_browser` claims sandboxing but omits `chromium_sandbox=True`, whose Playwright default is false. `_safe_headless_url` accepts loopback, RFC1918, link-local, metadata, redirect-to-private, and DNS-rebinding targets.
- [Verified] `streamkeep/metadata.py` follows redirects and writes thumbnails without a size or type cap; `postprocess/chat_render_worker.py` uses unbounded `urlretrieve`; image paths lack one shared redirect, magic, pixel, frame, and allocation policy.
- [Verified] `streamkeep/hooks.py` runs trusted user command strings through `shell=True`. Remote metadata is correctly environment-only and bounded, but shell semantics, inherited environment, descendant cleanup, and future V24 expansion remain unnecessary risk.
- [Verified] backup activation remains multi-file and non-atomic in `streamkeep/backup.py`; the existing staged-restore roadmap item is still required.
- [Verified] the browser/local API has strong Host, Origin, scoped-token, nonce, and loopback controls. Preserve these boundaries; do not broaden them for hosted or default-LAN operation.
- [Needs owner decision] the repo and Flatpak metadata say MIT while PyQt6 is distributed by Riverbank under GPLv3 or a commercial license. Public binary distribution needs an explicit licensing basis and matching notices/source obligations before its posture can be considered resolved.

## Architecture Assessment

- [Verified] `DownloadWorker` is a mutable property bag created at seven call sites across `streamkeep/cli.py`, `headless_service.py`, `ui/main_window.py`, `ui/tabs/download.py`, and `ui/tabs/monitor.py`. Manual propagation of format, subtitle, archive, proxy, rate, and SponsorBlock fields is the root cause of cross-surface parity and recovery drift.
- [Verified] claimed capabilities such as plugin loading, gallery/RSS publication, uploads, sidecar profiles, summaries, smart thumbnails, statistics, and native notifications lack production callers or complete routes. The existing reachability gate should precede more breadth.
- [Verified] `ui/tabs/download.py` (4,512 lines), settings (3,182), main window (2,013), monitor UI (1,432), `db.py` (1,282), and `local_server.py` (1,265) remain high-churn orchestration boundaries. Extract coordinators only when the shared job/event contracts make the boundary testable; do not rewrite the UI stack.
- [Verified] `StreamKeep.spec` builds one-file output, `packaging/msix/build_msix.py` requires a directory build, and `streamkeep/updater.py` self-replaces `sys.executable`. Portable and MSIX artifacts therefore need separate build/update contracts.
- [Verified] 58 test files and 476 test functions provide strong core coverage, but only three GUI smoke tests exist; there is no type-check, fuzz, or parser property-test configuration. The 2026-07-16 offscreen GUI smoke passed 3/3.
- [Verified] accessibility, i18n, archive pagination, HLS semantics, plugin isolation/contracts, reproducible packaging, and staged restore are already actionable in ROADMAP.md and should not be duplicated.
- [Verified] Category disposition: security, observability, testing, packaging, migrations, and upgrade strategy have new or existing tasks; accessibility, i18n, plugins, and offline resilience already have active tasks; documentation changes belong in each implementation item; native mobile and hosted multi-user scope are rejected below.

## Rejected Ideas

- DRM or paid-streaming circumvention — StreamFab/DownloadHelper market it, but it contradicts StreamKeep's explicit non-DRM philosophy and creates legal/security risk.
- Hosted public downloader or mandatory cloud sync — cobalt shows the abuse/rate-limit burden; this conflicts with local-first privacy.
- Native mobile client now — Seal proves demand, but a responsive authenticated local web surface fits the current architecture without a second codebase.
- Full media-server replacement — Tube Archivist already serves that niche; StreamKeep should export to Plex/Jellyfin/Kodi instead.
- Torrent/general site-grabber pivot — JDownloader/FDM breadth would dilute the media archive focus and expand protocol risk.
- AI upscaling/frame interpolation — Parabolic and commercial tools expose it, but cost, model distribution, and weak fit rank below archive trust and reachability.
- Public plugin marketplace before isolation — competitor breadth does not justify distributing in-process Python code before the existing capability broker and namespace work.
- Default clipboard surveillance — JDownloader demonstrates convenience, but the privacy and accidental-capture cost is wrong for a local-first default.
- GitHub Actions — technically useful but explicitly prohibited by repository policy and already recorded in `Roadmap_Blocked.md`.

## Sources

### OSS and adjacent projects

- https://github.com/yt-dlp/yt-dlp/blob/master/README.md
- https://github.com/yt-dlp/yt-dlp/releases/tag/2026.07.04
- https://github.com/imsyy/yt-dlp-gui
- https://github.com/NickvisionApps/Parabolic/releases
- https://github.com/kieraneglin/pinchflat
- https://github.com/tubearchivist/tubearchivist
- https://github.com/meeb/tubesync
- https://ytdl-sub.readthedocs.io/en/latest/introduction.html
- https://github.com/mhogomchungu/media-downloader
- https://streamlink.github.io/

### Commercial, community, and curated signal

- https://www.4kdownload.com/products/videodownloader
- https://software.charliemonroe.net/help/downie/overview.html
- https://software.charliemonroe.net/help/downie/?article=postprocessing
- https://support.jdownloader.org/en/knowledgebase/article/linkgrabber-filters-and-views
- https://downloadhelper.net/
- https://news.ycombinator.com/item?id=40919571
- https://stackoverflow.com/questions/78335337/yt-dlp-redownload-better-quality-while-respecting-download-archive
- https://github.com/awesome-selfhosted/awesome-selfhosted

### Standards, security, dependencies, and packaging

- https://github.com/yt-dlp/yt-dlp/security/advisories/GHSA-6v4j-43gg-vj32
- https://sqlite.org/wal.html
- https://playwright.dev/python/docs/api/class-browsertype
- https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
- https://pillow.readthedocs.io/en/stable/handbook/security.html
- https://doc.qt.io/qt-6/qaccessible.html
- https://www.w3.org/TR/2026/CRD-webvtt1-20260520/
- https://developer.chrome.com/docs/extensions/develop/migrate/what-is-mv3
- https://learn.microsoft.com/en-us/windows/msix/app-installer/auto-update-and-repair--overview
- https://www.riverbankcomputing.com/software/pyqt/intro/
- https://peps.python.org/pep-0751/
- https://www.usenix.org/conference/usenixsecurity25/presentation/agarwal-shubham

## Open Questions

- [Needs owner decision] Is StreamKeep's public PyQt6 distribution covered by a commercial Riverbank license, or must release artifacts and notices adopt a GPLv3-compatible posture? This blocks correct license metadata and binary-release compliance; it is not inferable from the repository.
