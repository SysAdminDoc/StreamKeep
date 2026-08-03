# Research — StreamKeep
Date: 2026-08-02 — replaces all prior research.

## Executive Summary

StreamKeep v4.44.0 is a mature local-first PyQt6 archive manager: 79k LOC across 194 modules, 23.5k LOC of tests, a durable SQLite library, recursive SSRF/scheme validation on remote manifests, a loopback-only REST surface with replay proofs, and a ten-stage local release gate. The 2026-07-29 pass's P0 set (manifest confinement, execution lease, provenance, browser pairing) is **shipped and verified in source** — this pass found no regression in any of it. The strongest current shape is *capture breadth plus data safety*; the 2026-08-02 commit burst pivoted further, adding raw-protocol capture, an MSE recorder, plugin contracts and publishing surfaces. Two things now cap value. First, a residual security seam: the yt-dlp argument-template validator is a **deny-list**, and templates are the one capability an imported config does **not** quarantine — together that is arbitrary-executable execution from a shared config file. Second, StreamKeep can acquire almost anything but cannot **adopt** anything: no importer exists for an existing folder tree, a `yt-dlp --download-archive`, or another tool's library — and Pinchflat's maintainer pause ([#800](https://github.com/kieraneglin/pinchflat/issues/800), 254 reactions) has stranded a large userbase that is shopping right now. Custody, not capability, is the open field.

Top opportunities, in priority order:

1. Replace the yt-dlp template deny-list with an allow-list and quarantine imported templates (`streamkeep/download_options.py:46`, `streamkeep/config.py:555`).
2. Raise `requirements.txt` floors to the values `requirements.lock` already pins — source installs currently get pre-fix `cryptography` and Qt.
3. Require mutation proof on the three POST routes that skip it (`streamkeep/local_server.py:1073,1081`).
4. Retire the unreachable Authenticode update **check** — it fails before it ever queries releases (`streamkeep/updater.py:251`).
5. Import an existing library: folder tree, `--download-archive`, Pinchflat/TubeArchivist/ytdl-sub state, without re-downloading.
6. Make the SQLite index rebuildable from on-disk sidecars so the filesystem is the source of truth.
7. Deletion tombstones, so media the user deliberately removed is never re-fetched.
8. Mid-capture manifest/token refresh on 403/410 instead of failing the job.
9. A quality-upgrade decision engine that records a per-candidate reason and versions every replaced file.
10. Retroactive re-templating — rename/move an existing archive when the naming scheme changes.

## Product Map

### Core workflows

- Resolve a URL through native extractors or the yt-dlp fallback, inspect formats/tracks/subtitles, apply a Smart Mode profile or automation rule, download, finalize with metadata/NFO/chapters/chat/thumbnails.
- Queue, schedule, recur, pause, resume, cancel, retry and recover jobs from the desktop, CLI, REST API, browser extension or paired web remote; a per-profile execution lease keeps the GUI and headless service from double-running.
- Monitor channels and podcast feeds, auto-record on go-live, apply retention and media-server layouts, reconcile the archive through the Archive Maintenance preview/apply audit.
- Capture outside yt-dlp entirely: raw FFmpeg protocol jobs (RTSP/RTMP-listen/SRT/multicast/ICY), a guarded Streamlink engine, a DRM-free MSE recorder, extension-sniffed media requests.
- Search, verify, tag, play, clip, transcribe, summarize, publish (gallery/RSS), upload, back up and restore.

### User personas

- Personal media archivists who want durable files and sidecars over a hosted service.
- Stream/channel/podcast collectors needing unattended capture and recovery.
- Creators and researchers who clip, transcribe or review offline material.
- Single-owner self-hosted operators automating StreamKeep without running a media server.

### Platforms and distribution

- Python 3.11+ (Windows builds on 3.12, Flatpak on 3.13), PyQt6 with `pyqt6-qt6==6.11.1` pinned in the lock. MIT.
- Unsigned by policy: PyInstaller onedir, Inno Setup installer, WinGet manifest, Flatpak, MSIX scaffold, browser extension. No macOS artifact (existing V53).
- Runtime floors: FFmpeg/ffprobe 8.1.2, curl 8.21.0, yt-dlp 2026.07.04, Deno 2.3+/Node 22+ for full YouTube. DRM circumvention is out of scope throughout.

### Key integrations and data flows

- Native Kick/Twitch/Rumble/SoundCloud/Reddit/Audius/podcast-RSS/HLS/DASH/direct paths feed a yt-dlp fallback; gallery-dl, lux, Streamlink, yt-dlp-ytse and an optional remote `youtube_backend` are opt-in engines.
- All seven job-construction sites build a frozen `DownloadJobSpec` and call `DownloadWorker.from_spec`.
- SQLite (schema v9, WAL when the runtime has the reset fix) holds history, queue, monitor, failures, manifests, publishing and FTS; JSON holds preferences only; credentials live in the OS store behind `secretref:` handles.
- Remote manifests are validated recursively (`streamkeep/hls.py:146`, `streamkeep/dash.py:145`) through `net_guard.validate_remote_url` and handed to FFmpeg with `FFMPEG_REMOTE_SAFETY`, which blacklists `file,pipe,concat,concatf,subfile,unix,data` (`streamkeep/paths.py:34`).

## Competitive Landscape

### Sonarr / Radarr (adjacent — the closest analog to "monitor and upgrade")

- Does well: a Quality Profile is an *ordered* ladder plus `Upgrades Allowed`, an `Upgrade Until` cutoff, scored Custom Format matchers where a negative score is a hard veto, and ~29 independent accept/reject specifications each returning a **named reason**. ([TRaSH guides](https://trash-guides.info/Radarr/radarr-setup-quality-profiles/), [decision specs](https://github.com/Sonarr/Sonarr/tree/develop/src/NzbDrone.Core/DecisionEngine/Specifications))
- Learn: the per-candidate rejection log is what makes the *arr stack trusted. StreamKeep's monitor "upgrade" path compares against `db.find_latest_history(channel=...)` rather than the same media item, records no reason, and has no versioned undo.
- Avoid: indexer/protocol scoring and health-check sprawl that assumes a server deployment.

### ArchiveBox / browsertrix-crawler (adjacent — custody model)

- Does well: one self-describing directory per snapshot with `index.json` beside plain outputs; the SQLite index is explicitly a rebuildable cache — "all data is readable without needing to run ArchiveBox". Browsertrix normalizes URLs (ignore `www`, scheme, query order) *before* dedupe. ([ArchiveBox](https://github.com/ArchiveBox/ArchiveBox), [crawl scope](https://crawler.docs.browsertrix.com/user-guide/crawl-scope/))
- Learn: StreamKeep already writes `.streamkeep_manifest.json` and `metadata.json` per recording — the missing half is a `rebuild` that reconstructs the library purely from them, plus URL canonicalization before the archive key is hashed.
- Avoid: whole-page WARC custody; that is a different product.

### restic / borg / Syncthing (adjacent — integrity and undo)

- Do well: structure verification is separated from data verification, and `--read-data-subset n/t` scrubs a rolling fraction so a full library is verified over time at negligible cost; Syncthing's staggered versioning gives bounded-cost undo. ([restic](https://restic.readthedocs.io/en/stable/045_working_with_repos.html), [Syncthing](https://docs.syncthing.net/users/versioning.html))
- Learn: `verify.py` already computes SHA-256 manifests on demand — add a scheduled rolling scrub and staggered retention of anything an upgrade replaces.
- Avoid: an auto-`--repair` path; borg flags its own as dangerous, and a media archive has no redundancy to repair from.

### Streamlink

- Does well: the reference SSAI classifier — `EXT-X-DATERANGE` with `classname == "twitch-stitched-ad"`, segment titles containing `Amazon`, and unconditional ad-treatment of prefetch segments after a discontinuity because date extrapolation is unreliable there. Ad filtering is mandatory since Twitch moved MPEG-TS → fMP4. ([plugin source](https://raw.githubusercontent.com/streamlink/streamlink/master/src/streamlink/plugins/twitch.py), [docs](https://streamlink.github.io/cli/plugins/twitch.html))
- Learn: those exact heuristics belong in existing item V37; also its reload/attempt/timeout defaults are the model for treating a mid-capture 403 as continuation rather than failure.
- Avoid: `--stream-passthrough-encrypted` (8.4.0) and any encrypted-segment path.

### BililiveRecorder / LiveStreamDVR

- Do well: BililiveRecorder runs a dedicated timestamp-repair pass and states that it **cannot repair anything FFmpeg has already touched**; LiveStreamDVR deliberately writes `.ts` during capture so a crash still yields a playable file. ([repair.md](https://github.com/BililiveRecorder/website/blob/main/src/content/docs/user/repair.md), [LiveStreamDVR](https://github.com/MrBrax/LiveStreamDVR))
- Learn: StreamKeep's `live_capture.preserve_raw_capture` already embodies half of this. The remaining lesson is ordering — never let FFmpeg be the first parser of a damaged live stream.
- Avoid: platform-specific engine rewrites.

### Parabolic / media-downloader

- Do well: Parabolic bundles and self-updates Deno, ships portable and arm64 builds, and cuts timeframes with FFmpeg rather than yt-dlp's `--download-sections`; media-downloader fronts eight engines with 16 UI languages. ([Parabolic 2026.5.0](https://github.com/NickvisionApps/Parabolic/releases/tag/2026.5.0), [media-downloader](https://github.com/mhogomchungu/media-downloader))
- Learn: a managed JS runtime is now existential — yt-dlp requires an external JS runtime for full YouTube support ([#15012](https://github.com/yt-dlp/yt-dlp/issues/15012)), and StreamKeep only *detects* Deno/Node (`streamkeep/capabilities.py:689`).
- Avoid: implicit install-at-startup; the v4.12.1 fork bomb is the standing counter-example. Acquisition must be explicit, hash-verified and user-initiated.

### TubeArchivist / Pinchflat / ytdl-sub / Youtarr

- Do well: TubeArchivist rebuilds its index from embedded file metadata and ships filesystem rescan with metadata embed (v0.5.9/v0.5.10); ytdl-sub added retention sorting and fixed 4-byte-Unicode path-length truncation; Youtarr syncs watched state and generates per-channel M3U.
- Learn: the unserved ask is migration. Youtarr [#531](https://github.com/DialmasterOrg/Youtarr/issues/531) asks to import an existing library or migrate from Pinchflat/TubeArchivist; Pinchflat [#408](https://github.com/kieraneglin/pinchflat/issues/408) (17👍) and ytdl-sub [#536](https://github.com/jmbannon/ytdl-sub/issues/536) ask for retroactive re-templating; Pinchflat [#805](https://github.com/kieraneglin/pinchflat/issues/805) and TubeSync [#431](https://github.com/meeb/tubesync/issues/431) ask not to re-download deleted media. Nobody ships any of the three.
- Avoid: Docker-first delivery and Elasticsearch-class operating weight — the desktop posture is the differentiator.

### Commercial set (4K Video Downloader+, Downie, Stacher, IDM, JDownloader)

- Do well: what they **paywall** is the signal — 4K VD+ charges for channel subscriptions and Smart Mode presets; Stacher's Premium is literally "a media library"; Downie sells iCloud-synced history so the same URL is not re-downloaded on another Mac; IDM sells scheduling with connect/download/shutdown semantics. ([4K compare](https://www.4kdownload.com/products/videodownloader/compare), [Stacher](https://stacher.io/), [Downie](https://software.charliemonroe.net/downie/))
- Learn: StreamKeep already ships subscriptions, Smart Mode, a library and power actions for free. The one paywalled idea it lacks is a portable, exportable *seen-content ledger* — which is the same primitive the importer needs.
- Avoid: StreamFab, CleverGet, KeepStreams and the analog-hole capture category entirely; they are DRM-removal products.

## Security, Privacy, and Reliability

- **[Verified] Arbitrary-executable execution through yt-dlp argument templates.** `YTDLP_TEMPLATE_DENIED_OPTIONS` (`streamkeep/download_options.py:46-65`) is a deny-list of 18 options; `validate_ytdlp_template_args` (`:102-127`) rejects only membership in that set. `--ffmpeg-location`, `--plugin-dirs`, `-P`/`--paths`, `-o`/`--output`, `--cache-dir` and `--update-to` all pass. Templates reach argv unfiltered at `streamkeep/workers/download.py:479`. `_quarantine_import_capabilities` (`streamkeep/config.py:555-634`) holds back hooks, webhooks, proxies, cookie sources, media-server auto-import, companion server, lifecycle cleanup and Smart Mode profiles — but **not** `ytdlp_arg_templates`, which is only schema-validated (`config.py:405`). A shared config plus one template selection is code execution. A deny-list cannot enclose yt-dlp's option surface.
- **[Verified] Source installs receive pre-fix dependencies.** `requirements.txt:10` floors `cryptography>=48.0.1` while `requirements.lock:346` pins `49.0.0`; 49.0.0 is the fix for CVE-2026-39892 and CVE-2026-34073 ([changelog](https://cryptography.io/en/stable/changelog/)). `requirements.txt:4` floors `PyQt6>=6.6` while the lock pins `pyqt6-qt6==6.11.1`; 6.11.1 is the fix for [CVE-2026-6210](https://www.qt.io/blog/security-advisory-type-confusion-and-heap-buffer-overflow-vulnerability-in-qt-svg-marker-handling) and CVE-2026-9499. `urllib3` (lock 2.7.0, the fix for [GHSA-qccp-gfcp-xxvc](https://github.com/advisories/GHSA-qccp-gfcp-xxvc)) has no floor at all, and `paramiko` — imported by `streamkeep/upload/ftp.py:278` for SFTP — appears in neither file, so nothing keeps it above the SHA-1 RSA removal in 5.0.0 ([GHSA-r374-rxx8-8654](https://github.com/advisories/GHSA-r374-rxx8-8654)).
- **[Verified] Three POST routes skip mutation proof.** `/api/media-server/preview` (`streamkeep/local_server.py:1073`), `/api/intelligence/preview` (`:1081`) and `/api/operations/export` use `_require_auth(SCOPE_STATUS)` without `mutating=True`, so `_require_mutation_proof` (`:812-849`) never runs: no `Origin`/`Sec-Fetch-Site` check, no JSON content-type enforcement, no replay nonce. `preview` enumerates filesystem layout and `export` returns a full operations report.
- **[Verified] Session cookie omits `Secure`.** `_set_auth_cookie` (`streamkeep/local_server.py:914-917`) emits `HttpOnly; SameSite=Strict` only, and is called from `_json_response`, `_html_response` and `_bytes_response` — including error responses. LAN mode is explicitly HTTPS-only (`:421-426`), so the attribute is free to add.
- **[Verified] Webhook/ntfy curl has no `--` separator and no URL policy.** `streamkeep/ui/main_window.py:1329-1338` and `:1367-1377` place the config-supplied URL as a trailing positional. A URL beginning with `-` is parsed by curl as an option (`-K`, `-o`), and `net_guard.validate_remote_url` is never called, so a webhook can target loopback or cloud-metadata addresses. `webhook_url` *is* import-quarantined (`config.py:571`), which caps severity.
- **[Verified] Secret stores are briefly world-readable.** `streamkeep/secrets.py:146-159` and `streamkeep/portable_secrets.py:193-202` write the temp file under the default umask, `os.replace`, then `chmod 0o600` — the DPAPI blob / encrypted portable envelope is mode 0644 for its whole lifetime on POSIX.
- **[Verified] Portable-secret restore applies partially on failure.** `streamkeep/portable_secrets.py:111-121` writes config secrets, then each account credential, then cookies; a failure at the cookie step returns `False` with the credential store already half-overwritten and no rollback.
- **[Verified] FTS5 is created without a version gate.** `streamkeep/db.py:151` creates `history_fts` unconditionally. `streamkeep/sqlite_runtime.py:15` gates only on the WAL-reset fix (3.51.3); [CVE-2026-11822](https://sqlite.org/releaselog/3_53_2.html) is an FTS5 heap overflow fixed in 3.53.2. Frozen builds are safe — `packaging/sqlite_runtime.py:13` pins 3.53.3 with a SHA3-256 check — but a source install on an ambient 3.51.3–3.53.1 runtime enables FTS5 below the fix.
- **[Verified] The update *check* is dead, not just self-replacement.** `UpdateCheckWorker.run` calls `require_authenticode(...)` as its first statement (`streamkeep/updater.py:251`), before fetching the release list. Releases are unsigned by policy, so `update_security.py:166` raises and every check ends at `_empty_payload("Update blocked: …")`. Refusing to self-replace is correct and documented; refusing to *tell the user a version exists* is not. ~360 LOC across `updater.py`/`update_security.py`/`update_runtime.py` is verified-but-unreachable.
- **[Verified] Browser extension is over-permissioned for its stated purpose.** `browser-extension/manifest.json` requests `webRequest`, `tabs` and `host_permissions: ["<all_urls>", "http://127.0.0.1/*"]`, and `background.js:230-239` registers `onBeforeSendHeaders`/`onHeadersReceived` across `<all_urls>` — reading request/response headers on every site. Chrome Web Store's 2026 Limited Use policy (enforced from 2026-08-01) requires data handling "strictly necessary to the disclosed single purpose" ([policy update](https://developer.chrome.com/blog/cws-policy-updates-2026)). Separately, Local Network Access shipped in Chrome 142 and covers `127.0.0.0/8`; extensions are exempt *when they declare explicit loopback host permissions*, and two Chromium bugs broke that exemption before 144.0.7512.0 ([LNA](https://developer.chrome.com/blog/local-network-access)) — so `minimum_chrome_version: "144"` is warranted.
- **[Verified] Server-side auth events leave no trace.** `log_message` is a deliberate no-op (`streamkeep/local_server.py:625`) and nothing else records auth failures or mutation rejections, so a token compromise is forensically invisible.
- **[Verified] `feed.generate_rss` can emit non-well-formed XML.** `streamkeep/feed.py:82-83,128,147-149` uses `xml.sax.saxutils.escape`, which handles `& < >` but not C0 control characters; scraped titles reach the feed unfiltered while `safe_filename` strips controls only for paths.
- **[Verified] Dependency posture is otherwise current.** yt-dlp 2026.07.04 is at the fix for CVE-2026-55404/50019/50023/50574; Pillow 12.3.0, FFmpeg 8.1.2 (CVE-2026-8461) and curl 8.21.0 are each exactly at their fix. Qt CVE-2026-15037 (QDom XML injection, unfixed before 6.12) is **not applicable** — there is no `QtXml`/`QDom` import anywhere in the tree.
- **[Verified, no action] The 2026-07-29 manifest/SSRF P0 is closed.** Streamlink's [CVE-2026-44353](https://github.com/streamlink/streamlink/security/advisories) class — `file://` URIs inside remote HLS/DASH — is already defended by recursive `validate_remote_url` in `hls.py`/`dash.py` plus the remote-only FFmpeg protocol blacklist. Do not re-open.

## Architecture Assessment

- **Custody boundary (the strategic gap):** the SQLite library is authoritative and there is no path from disk back to the DB beyond `storage.import_folders` (orphan adoption under the configured root) and the Archive Maintenance import-candidate preview. Make the filesystem the source of truth: a `rebuild` that reconstructs history, tags, manifests and archive keys from `metadata.json` + `.streamkeep_manifest.json` sidecars. That single property is also what makes importing *other* tools' libraries tractable and what removes the lock-in users have already been burned by.
- **Identity boundary:** there is no canonical `(platform, source_id)` persisted through VOD → job → history, no URL canonicalization before the archive key is computed, and no deletion tombstone. All three of the unserved community asks (migration, re-templating, don't-re-download-deleted) reduce to this one missing value. Build it before, not after, the upgrade engine.
- **Upgrade boundary:** `streamkeep/ui/tabs/monitor.py` compares an upgrade candidate against the channel's latest history row rather than the same media item. There is no destructive replacement branch today, so no current data loss is claimed — but a decision must be recordable (accept/reject with a named reason) and every replaced file must be versioned before that branch is ever written.
- **Live-capture boundary:** the HLS parser is unusually complete — media/discontinuity sequence, GAP, BYTERANGE, PROGRAM-DATE-TIME, `EXT-X-PART` and `EXT-X-PRELOAD-HINT` (`streamkeep/hls.py:63,317,338-399`). Two spec gaps remain: `EXT-X-SKIP` delta playlists (mis-detects removed segments) and `EXT-X-DATERANGE`, which is now the carrier for both SSAI markers and Apple's 2026 `com.apple.hls.daterange-schedule` sidecar JSON ([What's new in HLS](https://developer.apple.com/streaming/Whats-new-HLS.pdf)). Operationally there is no 403/410 handler in `workers/download.py` — an expired Usher token mid-capture ends the job instead of triggering a re-resolve.
- **Module boundaries:** `db.py` (4409 LOC) and `local_server.py` (2429) are the two god-modules; `ui/main_window.py:5` still carries the deferred-decomposition comment at 2760 LOC. Natural seams: `db/` split per table family behind a `db/schema.py` that owns the 15 migrations; `local_server.py`'s 40-branch `do_GET`/`do_POST` into a route table with the ~200-LOC embedded `_WEB_UI_HTML` moved to a data file.
- **Performance:** `db._connect` (`streamkeep/db.py:43-52`) is called from 57 sites and each call runs `busy_timeout`, `foreign_keys` and a `journal_mode` round-trip plus `require_safe_runtime()`. History paging, queue polling and the monitor tick all pay it. A thread-local connection is the fix. No profiling pass over 100k-row History/Storage/Analytics rendering has been done.
- **Dead code:** the entire in-memory share registry in `streamkeep/gallery.py:23-77` (`_shared`, `register_shared`, `generate_share_id`, `get_shared`, `all_shared`) is unreachable from `streamkeep/` — `local_server.py:1386-1448` imports only the renderers — and it keeps two misleading fallback branches alive (`gallery.py:87`, `:132`). `_PUBLISHING_ID_RE` (`streamkeep/feed.py:20`) is unused.
- **Testing:** 121 test files; `1390 passed, 115 subtests` at **59.63%** coverage against a 47.5% floor — the floor is 12 points stale and no longer constrains anything. 26 modules have no test reference at all. The highest-risk untested set writes files and spawns subprocesses: `postprocess/clip_worker.py`, `transcribe_worker.py`, `chat_render_worker.py`, `thumb_worker.py`, `scene_worker.py`, `normalization.py`, `codecs.py`, `convert_worker.py`, `integrations/auto_editor.py`, `intelligence/summarize.py`. The whole `player/` package (1489 LOC) is untested despite a recorded use-after-free in `sync_viewer._relayout_grid`. `ui/main_window_jobs.py` — which selects the yt-dlp argument template — is untested and is the trigger for the deny-list finding above.
- **Accessibility:** the offscreen matrices (theme × density × pseudo-locale × high contrast, plus 200% scale) are genuinely good. The pinned Qt 6.11.1 exposes `QAccessible::Switch`, the `Orientation` attribute and AT-SPI Collection support ([what's new in 6.11](https://doc.qt.io/qt-6/whatsnew611.html)) — toggles currently report as checkboxes. WCAG 2.2 AA adds 2.5.7 (a dragging alternative for the clip timeline), 2.5.8 (24×24 targets) and 2.4.11 (focus not obscured), none of which the current tests assert.
- **i18n:** infrastructure is complete and the release gate checks catalog drift. Actual Spanish coverage is **187 of 1585 messages** (11.8%; 1393 empty) — the README's "beta" label is honest, and expansion stays correctly blocked on human translators.
- **Distribution:** MSIX cannot install without a signed package ([MSIX behind the scenes](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes)), which the no-signing policy forbids permanently — the scaffold and its blocked roadmap entry should be retired rather than carried. Flathub now additionally prohibits AI-generated or AI-assisted code with permanent bans ([requirements](https://docs.flathub.org/docs/for-app-authors/requirements)); given this repo's development method, publishing to Flathub is a policy decision the maintainer must make consciously — the Flatpak manifest remains fine for self-hosted builds either way. The `Roadmap_Blocked.md` entry for V29 (ffmpeg-8 TLS-verify toggle) is **stale**: its blocker was "V9 unbuilt", V9 shipped, and README:164 already documents opt-in self-signed TLS for RTSPS/RTMPS.
- **Documentation structure:** `ROADMAP.md:7-11` links to `COMPLETED.md`, `RESEARCH_REPORT.md` and `ROADMAP-COMPLETED.md`, all three of which `AGENTS.md` lists under "Never create". `CLAUDE.md` still describes a 4-tab UI and states "PyQt6 and yt-dlp auto-installed" — the exact behavior removed in v4.12.1 to stop the frozen-exe fork bomb. It is the agent-facing source of truth and is actively misleading.
- **Repo state (2026-08-02):** the ytse SABR feature landed as `a3c7317` during this pass, closing the gap where `README.md:74` documented untracked code. Two items remain: the last tag is `v4.41.0` against package v4.44.0, so v4.42–v4.44 shipped untagged and `packaging/winget_hash.py` has no tag to point at; and three stale agent worktree branches (`worktree-agent-*`) plus their `.claude/worktrees/` checkouts persist, each holding a full copy of the tree that `grep` and `ruff` will walk. Not roadmap work, but both mislead tooling.

## Rejected Ideas

- **Docker-first or multi-user deployment** — eight competitors occupy that space; adopting it would displace the desktop posture that is StreamKeep's actual differentiator, and complicate the loopback trust model. (Source: OSS competitor sweep.)
- **Native Android/iOS clients** — the existing P3 responsive web-remote item already owns mobile access; a second codebase does not.
- **C2PA / Content Credentials** — now ISO/IEC 22144, but emitting a manifest requires a certificate chain (conflicts with no-signing) and a re-encoded capture is a derivative whose provenance StreamKeep cannot substantiate. Correct behavior is to preserve inbound manifests byte-for-byte and never generate one. ([spec.c2pa.org](https://spec.c2pa.org/))
- **`--stream-passthrough-encrypted` / any EME or DRM path** — direct conflict with project policy. StreamFab, CleverGet, KeepStreams and Audials-style analog-hole capture are named and excluded.
- **Apprise as a notification dependency** — 100+ services for one import is dependency bloat against an existing structured-hook + webhook surface; a documented event vocabulary reaches the same targets.
- **Elasticsearch/Redis search** — SQLite FTS plus the existing P3 semantic-search item cover this at a fraction of the operating cost. (Tube Archivist precedent.)
- **Another semantic/multimodal search item** — the existing P3 item stands; WISE/MUVR reinforce it rather than justify a duplicate.
- **Bundling faster-whisper's replacement wholesale** — FFmpeg 8's `whisper` filter is attractive but is a build-flag-dependent capability; it belongs behind the runtime registry as an *alternate* backend, not a replacement. ([FFmpeg 8.0](https://linuxiac.com/ffmpeg-8-0-arrives-with-whisper-filter-vulkan-encoders/))
- **`nativeMessaging` transport for the extension now** — immune to Local Network Access changes but costs a per-user, per-browser host-manifest install; loopback remains correct while the extension exemption holds. Structure the transport as one swappable module and revisit if Chrome narrows the exemption.
- **Automatic fuzzy cross-platform deduplication** — unsafe until canonical source identity and an explicit review queue exist; it is downstream of the identity-boundary work, not parallel to it.
- **PostgreSQL or an external metadata database** — requested in competitors (Youtarr #302, Pinchflat #790), but the answer for a single-user desktop app is a rebuildable index and exportable sidecars, not a second database engine.
- **Re-opening remote-manifest scheme confinement** — already implemented and verified; listed here so a future pass does not re-investigate CVE-2026-44353 against this codebase.

## Sources

### Direct OSS competitors
- https://github.com/yt-dlp/yt-dlp/releases/tag/2026.07.04
- https://github.com/yt-dlp/yt-dlp/issues/15012
- https://github.com/yt-dlp/yt-dlp/issues/7271
- https://github.com/NickvisionApps/Parabolic/releases/tag/2026.5.0
- https://github.com/kieraneglin/pinchflat/issues/800
- https://github.com/kieraneglin/pinchflat/issues/408
- https://github.com/kieraneglin/pinchflat/issues/805
- https://github.com/tubearchivist/tubearchivist/releases/tag/v0.5.9
- https://github.com/tubearchivist/tubearchivist/issues/265
- https://github.com/meeb/tubesync/issues/431
- https://github.com/jmbannon/ytdl-sub/issues/536
- https://github.com/jmbannon/ytdl-sub/issues/1483
- https://github.com/DialmasterOrg/Youtarr/issues/531
- https://github.com/arabcoders/ytptube
- https://github.com/alexta69/metube/releases/tag/2026.07.27
- https://github.com/jely2002/youtube-dl-gui/releases/tag/app-v3.2.1
- https://github.com/mhogomchungu/media-downloader

### Live capture and archiving specialists
- https://github.com/streamlink/streamlink/releases/tag/8.4.0
- https://streamlink.github.io/cli/plugins/twitch.html
- https://github.com/BililiveRecorder/website/blob/main/src/content/docs/user/repair.md
- https://github.com/MrBrax/LiveStreamDVR
- https://github.com/Kethsar/ytarchive
- https://github.com/DmitryScaletta/twitch-dlp
- https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide
- https://github.com/coletdjnz/yt-dlp-ytse

### Adjacent domains
- https://trash-guides.info/Radarr/radarr-setup-quality-profiles/
- https://github.com/Sonarr/Sonarr/tree/develop/src/NzbDrone.Core/DecisionEngine/Specifications
- https://github.com/ArchiveBox/ArchiveBox
- https://crawler.docs.browsertrix.com/user-guide/crawl-scope/
- https://restic.readthedocs.io/en/stable/045_working_with_repos.html
- https://docs.syncthing.net/users/versioning.html
- https://manual.calibre-ebook.com/plugins.html
- https://beets.readthedocs.io/en/stable/guides/tagger.html
- https://gpoddernet.readthedocs.io/en/latest/api/reference/events.html
- https://docs.photoprism.app/user-guide/library/originals/

### Standards and platform APIs
- https://datatracker.ietf.org/doc/draft-pantos-hls-rfc8216bis/
- https://developer.apple.com/streaming/Whats-new-HLS.pdf
- https://developer.apple.com/streaming/GettingStartedWithHLSInterstitials.pdf
- https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md
- https://www.rfc-editor.org/rfc/rfc9559.html
- https://developer.chrome.com/blog/local-network-access
- https://developer.chrome.com/blog/cws-policy-updates-2026
- https://doc.qt.io/qt-6/whatsnew611.html
- https://www.w3.org/TR/WCAG22/
- https://docs.flathub.org/docs/for-app-authors/requirements
- https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes

### Security advisories and dependency releases
- https://cryptography.io/en/stable/changelog/
- https://www.qt.io/blog/security-advisory-type-confusion-and-heap-buffer-overflow-vulnerability-in-qt-svg-marker-handling
- https://github.com/advisories/GHSA-qccp-gfcp-xxvc
- https://github.com/advisories/GHSA-r374-rxx8-8654
- https://sqlite.org/releaselog/3_53_2.html
- https://github.com/streamlink/streamlink/security/advisories
- https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html
- https://linuxiac.com/ffmpeg-8-0-arrives-with-whisper-filter-vulkan-encoders/

### Community signal
- https://news.ycombinator.com/item?id=45898407
- https://github.com/yt-dlp/yt-dlp/issues/13831
- https://github.com/yt-dlp/yt-dlp/issues/13650
- https://github.com/AntennaPod/AntennaPod/issues/1946
- https://github.com/advplyr/audiobookshelf/issues/1723
- https://codeberg.org/janw/podcast-archiver

## Open Questions

- Does the maintainer intend to publish to Flathub? Flathub's 2026 requirements prohibit AI-generated or AI-assisted code with permanent bans, while this repository is developed with AI assistance. The answer determines whether the Flatpak lane is a distribution channel or a self-build convenience, and therefore whether V53's Linux artifact scope includes a Flathub submission.
