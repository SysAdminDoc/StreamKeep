# Research — StreamKeep
Date: 2026-07-29 — replaces all prior research.

## Executive Summary

StreamKeep is already a capable local-first PyQt6 archive manager: it combines multi-source resolution, durable SQLite jobs, monitoring, recovery, playback, search, post-processing, automation, and a tightly scoped local API without adopting the operational weight of a server stack. Its highest-value direction is not another acquisition engine; it is making the existing archive trustworthy under concurrency, hostile manifests, expiring source credentials, browser pairing, and failed upgrades. The 2026-07-29 suite passed 1,025 tests plus 45 subtests at 55.99% coverage, and a foreground audit of all six desktop pages in System, Dark, Light, and High Contrast found no major clipping or contrast defect. The gaps below are therefore ordered around data safety and honest operability before feature breadth.

1. Prevent a GUI and headless server from deleting or double-running each other's same-profile queue jobs (`streamkeep/ui/main_window.py`, `streamkeep/db.py`, `streamkeep/headless_service.py`).
2. Repair the shipped browser companion's origin-bound session contract (`streamkeep/local_server.py`).
3. Constrain every URI derived from remote HLS/DASH manifests before FFmpeg sees it (`streamkeep/paths.py`, `streamkeep/hls.py`, `streamkeep/dash.py`).
4. Separate stable archival provenance from signed delivery URLs in public sidecars and share bundles (`streamkeep/metadata.py`, `streamkeep/postprocess/bundle_worker.py`).
5. Give monitor quality upgrades a stable media identity and non-destructive transaction (`streamkeep/ui/tabs/monitor.py`).
6. Add restart-safe, error-aware automatic retries over the existing failed-job ledger.
7. Replace the one-global-cookie-file model with explicitly selected, site-bound authentication profiles.
8. Make the implemented rotating backup routine actually schedulable and observable (`streamkeep/backup.py`).
9. Establish one unsigned local release gate that tests source, claims, advisories, builds, and artifacts together.
10. Prove the claimed platform surface with unsigned macOS/Linux artifacts and target-host smoke.

## Product Map

### Core workflows

- Resolve a URL, inspect formats/tracks/subtitles, apply a profile, then download and finalize media plus optional chat, chapters, thumbnails, NFO, and metadata.
- Queue, schedule, recur, pause, resume, cancel, retry, and recover jobs through the desktop, CLI, REST API, browser extension, or paired web companion.
- Monitor channels and podcast feeds, auto-record new/live media, apply retention rules, and reconcile the local archive.
- Search, filter, verify, rename, tag, play, clip, transcribe, analyze, export, back up, and restore the resulting library.

### User personas

- Personal media archivists who value durable files and metadata over a hosted service.
- Stream, channel, and podcast collectors who need unattended capture and recovery.
- Creators and researchers who search, clip, transcribe, or review offline material.
- Single-owner self-hosted operators who automate StreamKeep without turning it into a multi-user media server.

### Platforms and distribution

- Python 3.11+ and PyQt6; Python 3.12 is the reproducible Windows build target, while the Flatpak manifest currently uses Python 3.13 and Qt/PyQt 6.10.
- Windows ships through a PyInstaller one-file executable; an MSIX scaffold, Flatpak manifest, browser extension, and source execution exist. There is no tested macOS artifact and no installed Flatpak smoke.
- MIT licensed. FFmpeg/ffprobe 8.1.2 and curl 8.21 are runtime floors; full YouTube behavior requires Deno 2.3+, Node 22+, or another supported JavaScript runtime. DRM circumvention is explicitly out of scope.

### Key integrations and data flows

- Native Kick, Twitch, Rumble, SoundCloud, Reddit, Audius, podcast-RSS, HLS/DASH, and direct-media paths feed a yt-dlp fallback; gallery-dl and lux are optional engines.
- FFmpeg/ffprobe perform capture, muxing, probing, conversion, chat rendering, and post-processing; optional mpv provides embedded playback.
- SQLite WAL stores history, queue, monitor, failures, manifests, and FTS state; JSON retains preferences. OS credential stores, cookie files, media-server adapters, SponsorBlock, webhooks, and local-only helper services sit at explicit boundaries.
- GUI workers use QThread/signals; headless execution uses the same database but currently has no same-profile ownership lease.

## Competitive Landscape

### Tartube

- Does well: multi-site channel/library management, creator grouping, live detection, imports, and visible retry behavior.
- Learn: explicit creator aliases, error-class-aware retries, and reviewable cross-platform duplicate candidates.
- Avoid: dense legacy configuration and implicit dependency installation.

### Tube Archivist

- Does well: indexed offline libraries, subtitle/comment search, playlists, playback state, and media-server companions.
- Learn: refresh into staging, validate, then atomically activate while retaining the known-good asset.
- Avoid: YouTube-only scope, Elasticsearch/Redis operating weight, and multi-user complexity.

### Pinchflat, TubeSync, and ytdl-sub

- Do well: subscription-PVR profiles, retention, gradual retry/backoff, media-server layouts, declarative presets, JSONL automation output, and optional metrics.
- Learn: persisted retry schedules, low-request feed reconciliation, versioned machine events, and low-cardinality observability.
- Avoid: Docker-only deployment and YAML-only interaction as the primary product surface.

### Youtarr

- Does well: Plex/Jellyfin/Emby playlists, portable M3U, selected watched-state workflows, NFO, library layouts, and guarded retention.
- Learn: expand existing V18 with previewed path/ID mapping, selected-user import, native playlists, and deletion safeguards.
- Avoid: coupling the desktop to Docker/MariaDB or importing every server user's state.

### YTPTube and MeTube

- Do well: per-link conditions, presets, browser handoff, live/upcoming handling, playlist picking, and credential overrides.
- Learn: named cookie/auth profiles selectable by job, rule, and monitor.
- Avoid: unauthenticated public binding, anti-bot bypass services, or unbounded free-form arguments.

### TwitchDownloader, BililiveRecorder, livestream_saver, and ytarchive

- Do well: recoverable live fragments, damaged-container repair, resumable capture state, synchronized chat, and gap-aware live recording.
- Learn: strengthen V36 with retained raw staging, missing-interval reports, and idempotent salvage to a new file.
- Avoid: hard-rendering chat into every video or replacing the general architecture with a platform-specific engine.

### Streamlink

- Does well: mature plugin-based live transport and a context-specific fix for nested HLS/DASH `file://` disclosure.
- Learn: make remote-manifest protocol confinement a prerequisite for V13.
- Avoid: expanding into encrypted/DRM capture.

### 4K Video Downloader, Downie, Stacher, Video DownloadHelper, IDM, and Audials

- Do well: polished intake, browser handoff, subscriptions, scheduling, batch workflows, conversion, and paid convenience around recurring downloads.
- Learn: users pay for predictable setup, recovery, platform coverage, and low-friction automation more than for another format toggle.
- Avoid: opaque subscription gating, telemetry-heavy cloud dependence, and claims that cannot be reproduced in unsigned local artifacts.

## Security, Privacy, and Reliability

- **[Verified] Same-profile queue loss and duplicate execution:** the GUI snapshots the queue at `streamkeep/ui/main_window.py:783-789`, and every `_persist_config()` calls `db.save_queue()` at `streamkeep/ui/main_window.py:821-836`; that method deletes the entire table before reinserting the stale in-memory list at `streamkeep/db.py:823-854`. The headless service independently recovers active rows at `streamkeep/headless_service.py:63-70` and dispatches queued work at `streamkeep/headless_service.py:187-202`, while `streamkeep/single_instance.py` guards GUI instances only. An isolated two-writer reproduction deleted a headless-enqueued row when the GUI persisted its stale snapshot. Required guardrail: one same-profile executor/lease plus row-level transactional mutations.
- **[Verified] Browser companion cannot complete pairing:** `streamkeep/local_server.py::_handle_pair()` origin-binds the new token, while `_token_grant()` requires that `Origin` on every later request. A real browser's same-origin GET omits that header. On 2026-07-29, pairing returned 201, `GET /ping` without `Origin` returned 401 `token_invalid`, and the same request with a synthetic matching `Origin` returned 200. The shipped SPA therefore falls back to its pairing error after a successful exchange. Preserve origin binding for requests that carry an origin, but authenticate same-origin browser GETs through an invariant browsers actually send and add a real-browser regression.
- **[Verified] Nested manifest local-file/SSRF boundary:** `streamkeep/paths.py:23-26` globally allows `file,pipe,http,https,tcp,tls,crypto`; `streamkeep/hls.py` and `streamkeep/dash.py` resolve nested URIs without scheme/host policy; `streamkeep/workers/download.py` passes remote manifests to FFmpeg with that whitelist. A local malicious HLS fixture referencing `file:C:/.../sentinel.ts` caused the exact StreamKeep FFmpeg invocation to copy the local sentinel into output. Nested HTTP can similarly bypass the top-level `streamkeep/net_guard.py` check. Validate recursive HLS variants, segments, keys, maps, renditions and DASH BaseURL/template/list/init URLs; apply SSRF/DNS-rebinding policy after every resolution; use a remote-only FFmpeg protocol whitelist that excludes `file` and `pipe`.
- **[Verified] Credential-bearing public sidecars:** Twitch constructs `usher.ttvnw.net` URLs containing `token` and `sig` at `streamkeep/extractors/twitch.py:166-175` and `:224-233`. `streamkeep/metadata.py:17-22` writes `stream_info.url` verbatim to `metadata.json`, and `:143-146` writes it as NFO `<trailer>` alongside a remote `<thumb>`. `streamkeep/postprocess/bundle_worker.py:21-52` includes JSON/NFO in share ZIPs. A temporary reproduction confirmed both files retained a synthetic token and both were selected for export. Public metadata needs a versioned schema, canonical source ID/page URL, local thumbnail reference, query/header/cookie stripping, legacy migration, and surfaced write failures.
- **[Verified] Quality-upgrade identity error:** `streamkeep/ui/tabs/monitor.py:1415-1442` compares a candidate against `db.find_latest_history(channel=channel_id)`, not the same media item, then marks it as an upgrade and bypasses normal duplicate handling. There is no current destructive replacement branch, so present data loss is not claimed. Before implementing replacement, persist a canonical `(platform, source_id)` through VOD, job, and history; stage and validate the new media/sidecars; atomically commit; retain the known-good version on any failure.
- **[Verified] Update path conflicts with the mandatory unsigned distribution policy:** `README.md:216-227` and `streamkeep/updater.py` require Authenticode publisher continuity and a PFX-signed detached manifest. V35 must disable that unreachable path for unsigned builds and use explicit manual/package-manager updates plus published hashes; signing is not an acceptable release dependency.
- **[Verified, point-in-time] Dependency posture is currently strong:** the 2026-07-29 OSV batch query returned no matches for the pinned core PyPI set. yt-dlp 2026.07.04, Pillow 12.3, PyQt6/Qt 6.11, requests 2.34.2, urllib3 2.7.0, cryptography 49, and PyInstaller 6.21 meet the researched 2026 security floors. Keep advisory scanning in the release gate; a negative query is not a permanent guarantee.

## Architecture Assessment

- **Execution boundary:** seven call paths construct `DownloadJobSpec` across GUI, CLI, headless, queue, monitor, VOD, and resume. Centralize spec creation and durable state transitions behind one queue/execution service while implementing the ownership fix; do not perform a broad rewrite of `streamkeep/ui/main_window.py` or `streamkeep/db.py`.
- **Server boundary:** `streamkeep/local_server.py` combines token/replay/CORS/SSRF policy, routing, server lifecycle, and an embedded SPA. Split auth policy and static UI only as required to make the fixed browser contract independently testable.
- **Provenance boundary:** `FinalizeWorker` knows `history_url` but passes only ephemeral `StreamInfo` into `MetadataSaver` (`streamkeep/workers/finalize.py:135-178`). Introduce a typed archival-provenance value rather than asking serializers to infer whether a URL is public.
- **Recovery boundary:** `streamkeep/backup.py:229-259` implements rotating `auto_backup()`, but no production caller schedules it; its module header references nonexistent `schedule_backup()`. Wire a profile-scoped schedule with destination health, last-success/next-run state, and failure notification.
- **Retry boundary:** build automatic retries over the durable failure ledger, not worker-local loops: persist category, attempt count, `next_attempt_at`, last reason, jittered exponential delay, `Retry-After`, and per-source circuit state. Authentication, DRM, missing media, configuration, and disk failures must stop for intervention.
- **Credential boundary:** `streamkeep/cookies.py` exposes one global cookie file and `streamkeep/rules.py` cannot select credentials. Store only opaque profile references in jobs/rules/monitors; bind each profile to allowed sites; keep secret material in restricted files/secure storage; never fall back across sites.
- **Reachability and claim drift:** native notifications are called by `streamkeep/ui/main_window.py:1890-1904` but remain “experimental” in `streamkeep/capabilities.py:205-208`; Flatpak metadata advertises unreachable upload/plugin surfaces; Spanish has 139 translated messages out of 1,325 while `README.md:154` presents an undifferentiated language choice. Replace token-presence capability checks with call-path/contract probes and label Spanish as core/beta rather than duplicating the blocked translation-expansion item.
- **Testing:** the 2026-07-29 offscreen suite passed, but the shipped `streamkeep/player/` package, `streamkeep/feed.py`, and several intelligence/post-process modules have no direct coverage. Add the P0 browser, manifest, sidecar, and concurrent-writer regressions first; fold full-page theme/density/state rendering, translation compilation, lint, advisory audit, and artifact startup into one local release command because repository policy intentionally excludes GitHub Actions.
- **UI/accessibility:** foreground rendering of all six pages across four themes at 1120×900 found consistent hierarchy, empty states, spacing, and contrast. High Contrast also has a 200% scale test. The remaining quick gap is that System theme does not consume Qt 6.10+ `QAccessibilityHints::contrastPreference`; follow OS changes live while keeping explicit user-selected themes sticky.
- **[Needs live validation] Distribution:** Windows artifact smoke is substantive; Flatpak/MSIX checks are mostly static and macOS has no artifact. V35 already owns Windows onedir/installer work. A separate later item should build and smoke unsigned macOS x64/arm64 bundles and one portable Linux artifact, publish hashes, and make support claims conditional on those probes.
- **Documentation:** `CLAUDE.md` still describes older tab counts, storage, styling, and dependency behavior; `README.md`, `capabilities.py`, Flatpak metadata, and locale labels disagree. The release gate should fail on machine-checkable claim drift; prose cleanup remains bounded documentation work, not a new architecture project.
- **Existing-lane disposition:** the current operations-view item already owns human observability; the plugin-adapter item owns extension contracts/isolation; V35/V42 own Windows packaging and yt-dlp upgrade channels; the responsive web-remote item owns mobile access; blocked translation work owns additional human-quality locales. The 2026-07-29 roadmap adds prerequisites or scope notes to those lanes instead of duplicates.

## Rejected Ideas

- **Native Android/iOS clients** — Seal/YTDLnis show demand, but a second codebase duplicates the existing paired responsive remote; fix and finish that surface first.
- **Cloud sync or hosted accounts** — commercial products normalize them, but they expand the privacy and support boundary beyond StreamKeep's local-first purpose.
- **Full multi-user RBAC/LDAP** — Tube Archivist-style tenancy is an architecture pivot; paired scoped clients are access grants, not users.
- **Docker as the primary distribution** — Pinchflat/TubeSync demonstrate headless demand, but Docker-first delivery would displace the native desktop and complicate the loopback/reverse-proxy trust model.
- **DRM capture, EME bypass, FlareSolverr, or anti-bot bypass services** — direct conflict with project policy and a high security/maintenance burden.
- **Full WARC/OCFL/C2PA preservation stack** — IIPC/ArchiveBox patterns are useful analogies, but whole-page custody and provenance standards are outside a media downloader's scope.
- **Elasticsearch/Redis search** — Tube Archivist's operating cost is disproportionate when StreamKeep already has local SQLite FTS and a P3 semantic-search item.
- **Another semantic/multimodal search item** — WISE, MUVR, ModaRoute, and Digital Collections Explorer reinforce the existing P3 item; they do not justify a duplicate.
- **Automatic metadata translation expansion** — already a P3 item, while additional human-quality UI locales are blocked; do not disguise machine output as finished localization.
- **Generic RSS monitoring** — podcast enclosure monitoring already ships. A linked-video Atom path is not prioritized until telemetry shows direct platform polling is a material problem.
- **Hard-rendered chat by default** — TwitchDownloader shows value in synchronized replay, but rendering every chat wastes CPU/storage; retain optional rendering and revisit a lightweight panel after player contract coverage improves.
- **Automatic fuzzy deduplication across platforms** — Tartube's creator model is useful, but silent suppression/deletion is unsafe until stable source identity and an explicit review queue exist.
- **Per-job “incognito” mode** — YTDLnis provides precedent, but omitting history/manifests undermines StreamKeep's recovery contract; use explicit cleanup/export controls instead.
- **Unmeasured download/post-process pipelining** — yt-dlp issue #1918 is plausible, but StreamKeep already has concurrent jobs and separate finalization; profile contention before adding pipeline complexity.

## Sources

### Direct OSS and adjacent projects

- https://github.com/yt-dlp/yt-dlp
- https://github.com/axcore/tartube
- https://github.com/axcore/tartube/issues/735
- https://github.com/tubearchivist/tubearchivist
- https://github.com/tubearchivist/tubearchivist/issues/915
- https://github.com/kieraneglin/pinchflat
- https://github.com/meeb/tubesync
- https://github.com/meeb/tubesync/issues/73
- https://github.com/jmbannon/ytdl-sub
- https://github.com/jmbannon/ytdl-sub/issues/746
- https://github.com/DialmasterOrg/Youtarr/releases/tag/v1.77.0
- https://github.com/arabcoders/ytptube/pull/641
- https://github.com/alexta69/metube/issues/767
- https://github.com/NickvisionApps/Parabolic
- https://github.com/jely2002/youtube-dl-gui
- https://github.com/mhogomchungu/media-downloader
- https://github.com/lay295/TwitchDownloader/issues/1070
- https://github.com/BililiveRecorder/BililiveRecorder
- https://github.com/glubsy/livestream_saver/releases/tag/v2.0.0
- https://github.com/Kethsar/ytarchive

### Commercial products

- https://www.4kdownload.com/products/videodownloader
- https://software.charliemonroe.net/downie/
- https://www.stacher.io/
- https://downloadhelper.net/
- https://www.internetdownloadmanager.com/
- https://audials.com/en/

### Community and discovery

- https://www.reddit.com/r/DataHoarder/comments/xopxll/
- https://www.reddit.com/r/DataHoarder/comments/1e5g9m2/
- https://www.reddit.com/r/DataHoarder/comments/13tnkn0/
- https://www.reddit.com/r/DataHoarder/comments/1dbpoaz/
- https://news.ycombinator.com/item?id=36744395
- https://news.ycombinator.com/item?id=44768714
- https://stackoverflow.com/questions/tagged/yt-dlp
- https://lobste.rs/search?q=yt-dlp&what=stories&order=newest
- https://github.com/awesome-selfhosted/awesome-selfhosted
- https://github.com/iipc/awesome-web-archiving
- https://github.com/stax76/awesome-mpv

### Standards, security, and dependency releases

- https://github.com/streamlink/streamlink/security/advisories/GHSA-hgqw-6m45-hw5f
- https://nvd.nist.gov/vuln/detail/CVE-2026-44353
- https://ffmpeg.org/ffmpeg-protocols.html
- https://www.rfc-editor.org/rfc/rfc8216
- https://dashif.org/docs/DASH-IF-IOP-v4.2-clean.htm
- https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
- https://owasp-aasvs4.readthedocs.io/en/latest/8.3.1.html
- https://jellyfin.org/docs/general/server/metadata/nfo/
- https://github.com/yt-dlp/yt-dlp/blob/master/README.md
- https://github.com/yt-dlp/yt-dlp/releases/tag/2026.07.04
- https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html
- https://doc.qt.io/qt-6/qaccessibilityhints.html
- https://www.w3.org/TR/WCAG22/
- https://developer.chrome.com/docs/extensions/reference/api/webRequest
- https://www.w3.org/TR/media-source-2/
- https://api.osv.dev/v1/querybatch

### Academic and engineering research

- https://arxiv.org/abs/2602.12819
- https://arxiv.org/abs/2510.21406
- https://arxiv.org/abs/2507.13374
- https://www.cambridge.org/core/journals/computational-humanities-research/article/digital-collections-explorer-an-opensource-multimodal-viewer-for-searching-digital-collections/D43D8DEC5B011B6E65F787323C3FFBF5

## Open Questions

- None. Repository evidence and public sources are sufficient to implement and order the recommended work.
