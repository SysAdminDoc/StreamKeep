# Research — StreamKeep
Date: 2026-07-27 — replaces all prior research.

## Executive Summary

StreamKeep (v4.43.2) is a mature ~70K-line, local-first PyQt6 downloader and archive manager with 8 native extractors plus a yt-dlp catch-all (1000+ sites), optional external engines (gallery-dl, lux), durable SQLite queue/history/monitor, channel-monitor auto-record, embedded mpv player, deep post-processing (trim/clip/transcode/highlights/SponsorBlock mark+remove/emote-aware chat render), and parity across desktop GUI, CLI/headless, and an authenticated loopback REST server. No single competitor matches that combination on desktop. Test suite: 1019 passing, 55.9% coverage against a 47.5% floor.

Since the 2026-07-20 research pass the team shipped the items that pass flagged as highest value: storage-health disk alerts and native OS notifications are now wired (v4.42.0), an SSRF address policy covers REST-submitted URLs (v4.42.0), a SponsorBlock-delay archival heuristic landed (v4.42.0), and gallery-dl + lux external engines were added (v4.43.0). Two crash/data-loss fixes shipped this week: live yt-dlp captures are no longer deleted on Stop (v4.43.1), and the `adv_override_badge` AttributeError that aborted every download is fixed (v4.43.2). Dependencies meet every 2026 CVE floor (yt-dlp 2026.7.4, cryptography 49.0.0, urllib3 2.7.0, requests 2.34.2, Pillow 12.3.0, pyqt6-qt6 6.11.1).

The remaining high-value direction is **engine-layer survivability and distribution**, not feature breadth. A do-everything tool that silently fails on YouTube (SABR/PO-token) or splices ads into Twitch VODs loses more trust than a narrow tool that fails on one platform. Top opportunities in priority order:

1. **Close the YouTube PO-token/JS-runtime gap.** StreamKeep *detects* a PO-token provider and *presence* of a JS runtime but never validates the runtime **version** (Deno ≥2.3, Node ≥22 are now hard floors) and cannot install/run a provider. An out-of-date Deno silently downgrades YouTube to storyboard-only. (Verified: `ytdlp.py` detection-only; `capabilities.py` presence-only.)
2. **Switch distribution from a 520 MB onefile to onedir + installer.** Onefile re-extracts the entire payload to a temp dir on every launch (slow cold start), maximizes AV false-positive surface (no code-signing), and races on double-launch. (Verified: `StreamKeep.spec` single `EXE`, no `COLLECT`.)
3. **Live-capture reliability: fragment-gap recovery + an optional ytarchive engine.** `--live-from-start` drops fragments on unstable streams (open yt-dlp issues #13359/#15921/#16673); the field routes to ytarchive/streamlink for reliability. StreamKeep is yt-dlp/ffmpeg-only for live.
4. **Twitch VOD integrity: SSAI ad-segment stripping and auto-unmute.** Twitch bakes ads into the m3u8 and mutes copyright segments; downloads inherit both. `twitch_recover.py` recovers deleted VODs but does neither.
5. **Optional SABR fallback engine (yt-dlp-ytse)** for the growing set of videos where only SABR formats remain.
6. **Small parity/quick wins:** expose the output filename-template in CLI/config (GUI + monitor-channel overrides already exist; CLI only takes `-o DIR`, and the ffmpeg-native path hardcodes `.mp4`); dubbed-audio-language + `mute` output mode (cobalt); album-artist auto-fill for SoundCloud/Audius/podcast audio (MeTube).

## Product Map

- **Core workflows:** (1) paste/queue a URL → resolve → pick quality → download (HLS/DASH/MP4/audio) with resume; (2) monitor channels and auto-record on live; (3) post-process (trim/clip/transcode/highlights/subs/chat render/SponsorBlock); (4) browse/search/verify a SQLite-backed library and play in the embedded mpv player; (5) drive it all from GUI, CLI/headless, REST server, or browser extension.
- **User personas:** privacy-conscious data hoarder archiving creators; live-stream capturer (Twitch/Kick/YouTube); podcast/music collector; power user scripting via CLI/REST.
- **Platforms/distribution:** Windows (PyInstaller onefile exe, unsigned), Linux (Flatpak, KDE/PyQt 6.10 base + FFmpeg 8.1.2); Python 3.11+.
- **Key integrations/data flows:** yt-dlp + ffmpeg engines; gallery-dl/lux optional CLI engines; SQLite `library.db` (history/monitor/queue) + JSON config; upload adapters (S3/B2/MinIO, FTP/SFTP, WebDAV); local web gallery + RSS; SponsorBlock; browser MV3 companion via loopback token.

## Competitive Landscape

- **yt-dlp (the bundled engine).** Learn: track its release cadence closely — 2026.07.04 removed RTSP/MMS support and raised JS-runtime floors (Deno ≥2.3, Node ≥22); PO tokens are now video-ID-bound and no longer reliably bypass the bot check. Avoid: assuming any single `player_client` stays viable — `android_vr` degraded to 360p-only in 2026. (https://github.com/yt-dlp/yt-dlp/releases/tag/2026.07.04, https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)
- **MeTube.** Learn: user-facing `OUTPUT_TEMPLATE` for channel downloads, an album-artist post-processor for music metadata, and a stable/nightly yt-dlp channel toggle. Avoid: web-only scope with no channel monitoring (StreamKeep's auto-record is a genuine differentiator MeTube users still request in #76/#962). (https://github.com/alexta69/metube/releases)
- **cobalt.** Learn: `picker` multi-item responses, `mute` (clean audio-strip) mode, named filename styles, and per-service knobs like `youtubeDubLang`/`youtubeBetterAudio`. Avoid: its stateless server model — StreamKeep's value is a persistent local library, not a tunnel. (https://github.com/imputnet/cobalt/blob/main/docs/api.md)
- **Pinchflat.** Learn: media profiles with per-profile subtitle policy and non-destructive SponsorBlock chapter-marking (StreamKeep already has mark-mode — parity confirmed), plus serving an RSS feed of the archive and Apprise notification fan-out. Avoid: YouTube-only scope. (https://noted.lol/pinchflat/, https://github.com/kieraneglin/pinchflat/wiki)
- **twitch-dlp / TwitchDownloader.** Learn: deleted/hidden VOD recovery via trackers (StreamKeep has this in `twitch_recover.py`), auto-unmute of muted segments (StreamKeep lacks), and chat capture+render (StreamKeep has emote-aware `chat_render_worker.py` — parity). Avoid: single-platform lock-in. (https://github.com/DmitryScaletta/twitch-dlp, https://github.com/lay295/TwitchDownloader)
- **ytarchive / N_m3u8DL-RE.** Learn: purpose-built livestream-from-start reliability and live HLS/DASH manifest capture with custom headers — strong optional-engine candidates now that yt-dlp dropped aria2c for HLS/DASH. Avoid: making either a hard dependency; keep them opt-in like gallery-dl/lux. (https://github.com/Kethsar/ytarchive, https://github.com/nilaoda/N_m3u8DL-RE)
- **Downie (macOS).** Learn: user-guided extraction via a built-in browser for unsupported sites (StreamKeep's V11 lane) and user shell-script post-processing hooks. Avoid: macOS-only, closed. (https://software.charliemonroe.net/downie/)
- **JDownloader 2.** Learn: clipboard link-grabber with pre-download validity check and package grouping before download. Avoid: heavyweight Java/plugin sprawl and its dated UX. (https://www.rapidseedbox.com/blog/jdownloader-tips-and-tricks)

## Security, Privacy, and Reliability

- **No open security defect found.** All core dependencies meet 2026 CVE floors: yt-dlp 2026.7.4 (≥2026.07.04 covers CVE-2026-55404/50574/50023/50019), cryptography 49.0.0 (≥46.0.7), urllib3 2.7.0 (≥2.6.3, CVE-2026-21441), requests 2.34.2 (≥2.32.4, CVE-2024-47081), Pillow 12.3.0. (Verified against `requirements.lock`.)
- **Qt XML injection CVE-2026-15037 (Qt6 < 6.12) does NOT apply** — StreamKeep uses no `QtXml`/`QDomDocument` (MPD/RSS use Python `xml`/string builders). A Qt 6.12 bump is routine currency only, not a security driver. (Verified: no QtXml import.)
- **FFmpeg 8 TLS-verify default is a latent live-capture regression.** FFmpeg 8 verifies TLS peer certs by default; self-signed RTMPS/RTSPS/SRT origins that worked on FFmpeg 7 now fail. The planned raw-capture jobs (V9) must expose a per-source "allow self-signed" toggle injecting `-tls_verify 0`. (https://ffmpeg.org/ffmpeg-protocols.html, https://www.mail-archive.com/ffmpeg-devel@ffmpeg.org/msg184820.html)
- **Distribution risk:** onefile double-launch shares/clobbers the `_MEIxxxx` temp extraction. The GUI already has a per-profile instance lock, but onedir removes the race class entirely and cuts cold-start latency. (Verified: `StreamKeep.spec` onefile.)
- **Recovery/rollback:** resume sidecars, failed-job ledger, archive-maintenance dry-run coordinator, and `.skbackup` all present and mature. Live yt-dlp capture keep-on-Stop was fixed 2026-07-27 (v4.43.1). No new recovery gap found.

## Architecture Assessment

- **Engine layer is the single point of fragility.** yt-dlp/ffmpeg are invoked directly; there is no engine-abstraction that lets a job fall back yt-dlp ⇄ ytse ⇄ ytarchive ⇄ streamlink ⇄ N_m3u8DL-RE. The optional `integrations/gallery_dl.py` and `integrations/lux.py` are the closest pattern and should be generalized into a small typed "download engine" interface so new engines (and V13/V33/V34/V36) plug in uniformly. (Verified: `workers/download.py`, `integrations/`.)
- **Output-naming is split and under-exposed.** yt-dlp path uses `.%(ext)s`; the ffmpeg-native path hardcodes `.mp4` at `workers/download.py:988`. GUI (`download_controls.py` `adv_file_tpl_input`/`adv_folder_tpl_input`) and monitor-channel overrides (`models.py:213 override_filename_template`) exist, but the CLI exposes only `-o DIR`. Unify on one template resolver and surface it in CLI/config.
- **YouTube capability code is detection-heavy, action-light.** `ytdlp.py youtube_health_report()` + `YOUTUBE_PLAYER_CLIENT_PRESETS` are solid, but `capabilities.py` validates JS-runtime **presence** not **version**, and there is no lifecycle for a PO-token provider process. This is the highest-leverage refactor.
- **Test/doc gaps:** no engine-fallback tests exist yet (they would land with V33/V34/V36); README's per-download override docs don't mention the CLI template gap. Coverage floor (47.5%) is healthy; keep new engine code above it.

## Rejected Ideas

- **Add SponsorBlock "mark as chapters" mode** — already implemented (`download.py:407-409 --sponsorblock-mark`, `download_options.py SPONSORBLOCK_NON_REMOVABLE`). Source: Pinchflat comparison. (Verified present.)
- **Make subtitle languages configurable** — already configurable per-download and globally (`download_options.py validate_subtitle_options`, `download_controls.py adv_subtitle_list`). Source: Tube Archivist comparison. (Verified present.)
- **Add Twitch/emote chat render to video** — already present (`postprocess/chat_render_worker.py`, 14 KB; COMPLETED.md "emote-aware chat rendering"). Source: TwitchDownloader comparison. (Verified present.)
- **Wire disk-health alerts / native completion notifications** — shipped v4.42.0 (`main_window.py` DiskMonitor + native_notify wired). Source: prior RESEARCH.md #2. (Verified present.)
- **SSRF policy on REST-submitted URLs / SponsorBlock-delay heuristic** — shipped v4.42.0. Source: prior RESEARCH.md #4/#5. (Verified via CHANGELOG/metainfo.)
- **Bump Qt to 6.12 for CVE-2026-15037** — StreamKeep uses no QtXml/QDom; not applicable. Source: Qt advisory. (Verified no usage.)
- **Native mobile app** — rejected as a second codebase; the responsive web-remote item (existing P3) is the sanctioned path. Source: Parabolic #1694.
- **cobalt-style stateless tunnel API** — contradicts the local-first persistent-library philosophy. Source: cobalt docs.

## Sources

yt-dlp / YouTube resilience:
- https://github.com/yt-dlp/yt-dlp/releases/tag/2026.07.04
- https://github.com/yt-dlp/yt-dlp/releases/tag/2026.06.09
- https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide
- https://github.com/Brainicism/bgutil-ytdlp-pot-provider
- https://pypi.org/project/yt-dlp-ytse/
- https://github.com/yt-dlp/yt-dlp/issues/14390
- https://github.com/yt-dlp/yt-dlp/issues/16150
- https://github.com/yt-dlp/yt-dlp/issues/13359

Competitors / adjacent tools:
- https://github.com/alexta69/metube/releases
- https://github.com/imputnet/cobalt/blob/main/docs/api.md
- https://github.com/kieraneglin/pinchflat/wiki
- https://github.com/DmitryScaletta/twitch-dlp
- https://github.com/lay295/TwitchDownloader
- https://github.com/Kethsar/ytarchive
- https://github.com/nilaoda/N_m3u8DL-RE
- https://software.charliemonroe.net/downie/

FFmpeg / protocols:
- https://ffmpeg.org/ffmpeg-protocols.html
- https://www.phoronix.com/news/FFmpeg-Lands-WHIP-Muxer
- https://getblockify.com/blog/how-to-block-twitch-ads/
- https://streamlink.github.io/cli/plugins/twitch.html

Security / packaging:
- https://nvd.nist.gov/vuln/detail/CVE-2026-50023
- https://cryptography.io/en/stable/changelog/
- https://www.qt.io/blog/security-advisory-cve-2026-15037-xml-injection
- https://www.pythonguis.com/faq/problems-with-antivirus-software-and-pyinstaller/

## Open Questions

- **PO-token provider distribution:** is shipping a Node native-script sidecar (spawned on 127.0.0.1:4416) acceptable given the no-Docker, no-code-signing, ~520 MB onefile constraints, or should StreamKeep stop at one-click install + version validation? (Blocks V33 scope.)
- **Onedir installer format:** Inno Setup vs NSIS vs plain onedir zip for the Windows target, given unsigned-only policy and the existing update-flow (`updater.py` self-replace expects a single exe). (Blocks V35 — the self-update path must be reworked for onedir.)
