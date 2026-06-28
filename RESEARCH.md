# Research - StreamKeep

## Executive Summary

StreamKeep is a mature local-first Python/PyQt6 desktop archiver for live streams, VODs, podcasts, direct media URLs, and post-download media workflows. The strongest current shape is not another generic yt-dlp wrapper: it combines native Kick/Twitch/Rumble/SoundCloud/Reddit/Audius/podcast extractors, queueing, monitoring, SQLite library state, an embedded mpv player, local web/gallery surfaces, uploads, plugins, and packaging scaffolds. The highest-value direction is to convert the rapid feature surface into a dependable installable archive workstation: fix LAN companion correctness, make packaged artifacts complete and signed, fail closed on updater integrity, remove the non-encrypted secrets fallback, ship real translation artifacts, add GUI smoke coverage, harden dynamic manifest support, and document/sample the plugin SDK.

Top opportunities, in priority order:
- Fix LAN companion mode so remote devices work without weakening DNS-rebinding protections.
- Require update integrity metadata and signed release artifacts before self-replace.
- Make PyInstaller/MSIX/Flatpak packaging reproducible and complete, including assets, browser companion files, translations, ffmpeg/curl notes, and signing steps.
- Add `keyring` to runtime dependencies or fail visibly when OS secure storage is unavailable.
- Compile and ship `.qm` translations, then cover language switching in tests.
- Add pytest-qt GUI smoke tests for startup, tab navigation, dialogs, empty/error states, and worker signal lifetimes.
- Upgrade DASH/HLS handling for dynamic MPD and low-latency live playlist patterns.
- Add a plugin developer guide, sample extractor, manifest validator, and compatibility contract.

## Product Map

- Core workflows: URL fetch and quality selection; queued download/resume; channel monitor and auto-record; library search/storage cleanup; playback, clipping, chat/subtitle rendering, summaries, uploads, feeds, and backup/restore.
- User personas: unattended stream archivists, creators clipping their own VODs, data-hoarder style local librarians, and power users who want GUI control over yt-dlp/ffmpeg workflows.
- Platforms and distribution: source checkout on Python 3.10+; Windows is primary; Linux/macOS are supported by code paths; PyInstaller, MSIX, Flatpak, and browser-extension packaging are scaffolded but not fully release-grade.
- Key integrations and data flows: curl fetches APIs/manifests; ffmpeg/ffprobe download and validate media; yt-dlp handles long-tail sites; SQLite stores history/monitor/queue; config JSON stores preferences; browser extension and local web remote talk to `streamkeep/local_server.py`; plugin packages load from the config directory after trust gating.

## Competitive Landscape

- Streamlink: best-in-class live stream CLI with a long-running plugin culture. Learn from its plugin documentation, per-plugin options, and protocol-specific stability; avoid becoming CLI-only because StreamKeep's GUI/library/monitor surface is the differentiator.
- yt-dlp: the ecosystem default for extractor breadth, cookies-from-browser, plugins, and frequent platform breakage response. Keep using it as fallback, but expose dependency/runtime readiness checks so YouTube JavaScript-runtime changes and extractor updates are visible to users.
- TwitchDownloader: focused Twitch GUI with strong VOD/chat rendering expectations. Learn from its chat render fidelity, emote/badge parity, and predictable export modes; avoid narrowing StreamKeep to one platform.
- Tartube and Parabolic: GUI wrappers that separate simple paste/download from larger channel/library management. Learn from their simpler first-run workflow and download archive concepts; avoid duplicating their "just a wrapper" ceiling.
- JDownloader, 4K Video Downloader, Downie, and Internet Download Manager: commercial tools normalize link grabbing, account/profile management, scheduling, browser capture, update channels, and polished installers. Learn from their onboarding and recovery flows; avoid adware, opaque cloud services, and DRM-centered positioning.
- Ganymede and LiveStreamDVR: self-hosted Twitch archive systems that prioritize long-running monitor reliability, chat synchronization, webhook notifications, and files that remain useful outside the app. Learn from their unattended-operation model; avoid forcing StreamKeep into a server-only product.
- N_m3u8DL-RE and aria2: advanced segmented-download tooling with richer HLS/DASH edge-case handling, retries, and protocol support. Learn from dynamic MPD, multi-period, and low-latency handling; avoid exposing raw protocol knobs before the GUI can explain failures.
- HandBrake and auto-editor: adjacent post-processing tools with mature preset and media-analysis UX. Learn from device/social presets and multi-signal edit decisions; avoid burying users in codec-only controls.

## Security, Privacy, and Reliability

- Verified: `streamkeep/local_server.py` binds to `0.0.0.0` when LAN access is enabled, but `_host_ok()` only accepts `127.0.0.1`, `localhost`, or empty Host. This preserves rebinding defense but likely breaks legitimate LAN access advertised in `streamkeep/ui/tabs/settings.py`; remote Host values must be allowlisted intentionally.
- Verified: `streamkeep/updater.py` treats release SHA-256 as optional. If no `.sha256` asset exists, `DownloadUpdateWorker` accepts the binary after a size sanity check. Self-replacing an executable should fail closed on missing integrity metadata and should surface signing status.
- Verified: `streamkeep/secrets.py` falls back to `b64:` when DPAPI/keyring is unavailable, but `requirements.txt` does not include `keyring`. On non-Windows source installs this makes encrypted config fields reversible unless the user independently installed a backend.
- Verified: `StreamKeep.spec` has `datas=[]` and `runtime_hooks=[]`, while README packaging notes require assets, browser extension files, and packaging manifests. The launcher calls `multiprocessing.freeze_support()`, but the Python memory guidance still recommends a PyInstaller runtime hook as a second guard.
- Verified: `packaging/flatpak/com.github.SysAdminDoc.StreamKeep.yml` contains `sha256: PLACEHOLDER` for ffmpeg, so Flatpak builds are not reproducible.
- Verified: `streamkeep/i18n` ships `.ts` sources but no compiled `.qm` files; `available_languages()` only exposes compiled `.qm` languages plus English, so the shipped language selector cannot provide Spanish until compilation artifacts exist.
- Verified: `streamkeep/dash.py` explicitly rejects dynamic/live MPD manifests. This is correct fail-closed behavior, but live/DVR DASH sources are a visible gap versus N_m3u8DL-RE and yt-dlp.
- Likely: many broad `except Exception: pass` sites remain across UI and media helpers. Recent commits improved logging in core modules, but GUI smoke tests and structured error reporting are still needed to keep unattended monitor/download failures visible.

## Architecture Assessment

- `streamkeep/ui/main_window.py` has been reduced to roughly 2k lines and tab builders live in `streamkeep/ui/tabs/`, but tab modules such as `streamkeep/ui/tabs/download.py` are still very large. Future refactors should extract controllers around queue dispatch, finalize, companion server, storage, and monitor operations rather than restarting a broad rewrite.
- Tests have improved materially: extractor, local server, updater, plugin, upload, config, resume, search, and model tests now exist. Remaining high-value gaps are GUI smoke coverage, packaging smoke coverage, i18n compilation tests, LAN-mode server tests, and end-to-end worker signal lifecycle tests.
- Packaging boundaries are inconsistent: source mode is well documented, but PyInstaller/MSIX/Flatpak artifacts still need deterministic data inclusion, signing guidance, translation compilation, extension bundling, and smoke-launch checks.
- The plugin manager is trust-gated and avoids prepending plugin paths, which is good. The missing pieces are developer-facing documentation, a sample plugin, manifest schema validation, compatibility/version checks, and clearer failure reporting when a plugin is skipped.
- Observability is halfway migrated: some modules now use `logging`, but the GUI still relies heavily on status strings and silent fallback paths. A small logging bridge into the existing log panel would improve diagnostics without changing the local-first model.

## Rejected Ideas

- DRM circumvention support, source: StreamFab/JDownloader-style commercial downloader comparisons. Reason: legal and trust risk; StreamKeep should stay on user-owned/public/non-DRM media.
- Electron/web rewrite, source: Stacher and web-wrapper competitors. Reason: would discard PyQt/mpv integration and increase packaging weight without solving current reliability gaps.
- Cloud sync service for library/config, source: commercial account-based downloaders. Reason: contradicts local-first design; backup/restore and user-selected upload destinations already cover portable data movement.
- Native mobile app, source: remote-management patterns in Ganymede/LiveStreamDVR and commercial tools. Reason: the web remote/LAN companion should be fixed first and covers the near-term mobile-control case.
- Built-in VPN/proxy product, source: JDownloader/account-manager feature comparisons. Reason: the existing per-platform proxy pool is the right abstraction; operating a network service would add liability and support load.
- Public plugin marketplace, source: yt-dlp/Streamlink plugin ecosystems. Reason: premature until local plugin schema, samples, compatibility checks, and trust UX are solid.
- Captcha-solving integrations, source: JDownloader commercial feature set. Reason: high abuse/support risk and weak fit for a local archive tool.

## Sources

### OSS and Adjacent Tools
- https://github.com/streamlink/streamlink
- https://streamlink.github.io/
- https://github.com/yt-dlp/yt-dlp
- https://github.com/yt-dlp/yt-dlp/wiki/Plugins
- https://github.com/lay295/TwitchDownloader
- https://github.com/axcore/tartube
- https://github.com/NickvisionApps/Parabolic
- https://github.com/nilaoda/N_m3u8DL-RE
- https://github.com/Zibbp/ganymede
- https://github.com/MrBrax/LiveStreamDVR
- https://github.com/aria2/aria2
- https://handbrake.fr/
- https://github.com/WyattBlue/auto-editor

### Commercial and Community
- https://jdownloader.org/
- https://my.jdownloader.org/
- https://www.4kdownload.com/products/videodownloader-42
- https://software.charliemonroe.net/downie/
- https://www.internetdownloadmanager.com/
- https://www.reddit.com/r/DataHoarder/

### Platform, Standards, Security, Packaging
- https://docs.kick.com/
- https://dev.twitch.tv/docs/eventsub/
- https://developers.soundcloud.com/docs/api/guide
- https://datatracker.ietf.org/doc/html/rfc8216
- https://dashif.org/docs/
- https://github.blog/security/application-security/dns-rebinding-attacks-explained/
- https://curl.se/docs/security.html
- https://ffmpeg.org/security.html
- https://pyinstaller.org/en/stable/hooks.html
- https://learn.microsoft.com/windows/msix/package/signing-package-overview
- https://pytest-qt.readthedocs.io/
- https://docs.python.org/3/library/logging.html

## Open Questions

None. Current priorities are implementable from repository evidence and public documentation.
