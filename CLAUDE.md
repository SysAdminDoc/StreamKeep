# StreamKeep - Project Notes

## Tech Stack
- Python 3.10+ with PyQt6 GUI (modular package)
- ffmpeg for HLS/MP4 download (stream copy, no re-encode)
- curl for API/playlist fetching
- yt-dlp as optional fallback extractor

## Package Layout (v4.11.0 — modularized)

```
StreamKeep.py                    59 lines  — launcher only
streamkeep/
  __init__.py                           — VERSION, CURL_UA
  bootstrap.py                          — dependency auto-install
  crash_log.py                          — global exception handler
  paths.py                              — CONFIG_DIR, _CREATE_NO_WINDOW
  config.py                             — load/save config.json
  theme.py                              — CAT palette + QSS stylesheet
  models.py                             — QualityInfo/StreamInfo/VODInfo/HistoryEntry/MonitorEntry
  utils.py                              — fmt_*, safe_filename, default_output_dir, template rendering, cookie scan
  http.py                               — curl wrappers + parallel Range download (NATIVE_PROXY)
  hls.py                                — m3u8 master + duration parsing
  scrape.py                             — direct URL detection + regex + Playwright page scrape
  metadata.py                           — metadata.json + NFO + chapters writer
  monitor.py                            — ChannelMonitor (round-robin QTimer)
  clipboard.py                          — ClipboardMonitor (800ms URL poll)
  extractors/
    base.py                             — Extractor base + auto-registry
    kick.py / twitch.py / rumble.py / soundcloud.py / reddit.py / audius.py / podcast.py / ytdlp.py
  workers/
    fetch.py        FetchWorker         — URL resolve via extractor system
    download.py     DownloadWorker      — ffmpeg + parallel HTTP Range + yt-dlp
    playlist.py     PlaylistExpandWorker — yt-dlp --flat-playlist probe
    page_scrape.py  PageScrapeWorker    — headless + regex media scrape
  postprocess/
    codecs.py                           — VIDEO/AUDIO_CODECS + HW encoder probe
    processor.py    PostProcessor       — all presets + converter
    convert_worker.py ConvertWorker     — standalone batch converter
  ui/
    main_window.py                      — StreamKeep QMainWindow (3751 lines — Phase 2 god object)
```

## Architecture
- **Extractor system**: `Extractor` base class with `__init_subclass__` auto-registry (streamkeep/extractors/)
  - KickExtractor, TwitchExtractor, RumbleExtractor, SoundCloudExtractor, RedditExtractor, AudiusExtractor, PodcastRSSExtractor, YtDlpExtractor
  - YtDlpExtractor registers last as catch-all fallback
- **Direct URL detection**: streamkeep/scrape.py uses HEAD Content-Type sniffing
- **Data classes**: streamkeep/models.py
- **HTTP helpers**: streamkeep/http.py — `_build_curl_cmd()` factored out of curl/curl_json/curl_post_json for single-source proxy routing
- **Workers**: streamkeep/workers/ — FetchWorker, DownloadWorker, PlaylistExpandWorker, PageScrapeWorker
- **Post-processing**: streamkeep/postprocess/ — PostProcessor + ConvertWorker with HW encoder probe (NVENC/QSV/AMF/VT)
- **GUI**: streamkeep/ui/main_window.py — StreamKeep QMainWindow with 4-tab QStackedWidget (Download/Monitor/History/Settings)
- Catppuccin Mocha dark theme via global QSS
- Phase 1 (leaf modules, extractors, workers, postprocess, metadata, monitor, clipboard): commits bb2f1e1 → b906446
- Phase 2 (UI class split to streamkeep/ui/main_window.py): commit 2c03d1e
- Phase 3 (HTTP dedup + pyflakes sweep — caught 4 missing json/urllib imports): this commit

## Key API Details
- **Kick**: `/api/v2/channels/{slug}/videos` returns VOD list with `source` field = DVR master.m3u8
- **Twitch**: GraphQL raw queries (not persisted), Client-ID `kimne78kx3ncx6brgo4mv6wki5h1ko`, access tokens from `streamPlaybackAccessToken`/`videoPlaybackAccessToken`
- **Rumble**: Page URL → scrape `embed/v{id}` → embedJS API returns `ua.hls` and `ua.mp4` URLs
- **yt-dlp**: `--dump-json --no-download` returns format list, mapped to QualityInfo

## Build/Run
```bash
python StreamKeep.py
```
No build step. ffmpeg must be in PATH. PyQt6 and yt-dlp auto-installed.

## Architecture (v2.0.0)
- **Tab-based UI**: QStackedWidget with Download, Monitor, History, Settings tabs
- **DownloadWorker**: Enhanced ffmpeg progress parsing with speed/ETA/size tracking
- **ChannelMonitor**: Round-robin polling via QTimer (15s tick), auto-record on live detection
- **MetadataSaver**: Writes metadata.json + downloads thumbnails alongside videos
- **HistoryEntry**: Tracks completed downloads, persisted in config.json
- **Config persistence**: JSON at `%APPDATA%\StreamKeep\config.json` — saves output dir, segment pref, history, monitor channels

## Version History
- v4.14.0 — **Major feature release: four S-tier user-facing features.**
  1. **Crash-safe resume for interrupted downloads.** New `streamkeep/resume.py` + `ResumeState` dataclass. `DownloadWorker` writes a `.streamkeep_resume.json` sidecar into the output dir on start, refreshes it on every completed segment, leaves it in place on cancel, and clears it on clean all-done. At app startup a deferred scan walks known output roots (the configured global dir + per-channel monitor overrides) looking for orphan sidecars; if any are found, a Download-tab banner shows "Interrupted download ready to resume" with **Resume** / **Discard** actions. Resume re-runs the extractor for the source URL before starting so expired playlist tokens (Kick/Twitch rotate ~24h) get refreshed, then hands only the *remaining* segments to a new DownloadWorker. Completed segments are never re-downloaded. yt-dlp path currently resumes from the seg-skip layer (the yt-dlp subprocess itself is started fresh — `--continue` is implicit when the output file already exists).
  2. **Lossless trim / clip.** New `streamkeep/postprocess/clip_worker.py` `ClipWorker(QThread)` + `streamkeep/ui/clip_dialog.py` modal dialog. Stream-copy mode (`-c copy`, keyframe-aligned) is near-instant; the "Frame-accurate (re-encode)" toggle switches to the user's chosen codec for frame-exact trim. Duration is probed via `ffprobe` so the range is constrained to the real file length. Entry points: right-click a History row → **Trim / Clip…**, or the new **Trim…** button next to **Open Folder** in the footer after a download completes. Input fields accept `HH:MM:SS`, `MM:SS`, or plain seconds.
  3. **Per-channel monitor profiles.** `MonitorEntry` extended with optional `override_output_dir`, `override_quality_pref`, `override_filename_template`, `schedule_start_hhmm` / `schedule_end_hhmm` / `schedule_days_mask`, and `retention_keep_last`. Config schema is forward-compatible (missing keys default to "use global"). New `streamkeep/ui/monitor_entry_dialog.py` is opened from a new **Edit** button in the Monitor table — each field shows "uses global: X" hints. `ChannelMonitor._poll_tick` now short-circuits when the entry's schedule window doesn't include the current time (handles midnight-wrap correctly). Auto-record path reads `override_output_dir` and `override_quality_pref` before dispatching. Schedule-window is also checked at auto-record start so a channel can't fire outside its declared window. Retention: after a successful auto-record, `_apply_retention_for_channel` groups per-channel subdirs under the output root, sorts by mtime desc, and recycle-bins anything beyond the keep-last count (via `send2trash`). Retention with no send2trash available is **logged + skipped** — never falls back to permanent delete.
  4. **Storage manager.** New **Storage** tab (now the 4th of 5 tabs). `streamkeep/storage.py` walks the output root up to 3 levels deep, groups recordings by folder, reads each `metadata.json` sidecar for authoritative platform/channel, and falls back to a directory-name heuristic when absent. `streamkeep/ui/tabs/storage.py` renders: total-size / file-count / platform / channel metric cards, a sortable table (Platform / Channel / Title / Files / Size / Path), and a **Recycle selected** action with a confirm dialog that shows count + total size + 5 sample paths. All deletes route through `send2trash` (recycle bin). Auto-scans on every visit to the tab; explicit **Rescan** button for manual refresh.
  - **Bootstrap update:** `send2trash` added to `bootstrap.py` optional-deps list. Frozen-exe safe (bootstrap early-returns when `sys.frozen`); for the shipped build it gets baked in by PyInstaller via the standard `pip install PyQt6 yt-dlp pyinstaller pillow send2trash` step in the build workflow.
  - **Theme:** small addition — `QFrame#resumeBanner` + `QLabel#resumeBannerLabel` for the new Download-tab resume banner. Still Catppuccin Mocha.
  - Verified end-to-end: py_compile + pyflakes clean across all new and modified files; headless instantiation checks the new `Storage` tab at index 3; `entry_in_schedule_window` unit tests cover in-window / out-of-window / midnight-wrap / days-mask cases; `scan_storage` test confirms metadata.json wins over dir-name heuristic; resume sidecar roundtrip test covers save → scan → remaining_segments → clear.
- v4.13.1 — **Post-v4.13.0 QA audit fixes.** Cross-module audit pass found a small cluster of real defects in the background-finalize path plus pyflakes-level cleanup.
  - **[workers/finalize.py] Stale snapshot key no longer aborts finalize.** `FinalizeWorker` built `orig = {k: getattr(PostProcessor, k) for k in snapshot}` — a single stale/unknown config key (easy to hit after a downgrade or config-schema drift) raised `AttributeError` and aborted the entire finalize pass **before** metadata was even saved, silently eating post-download metadata.json/NFO/chapters for that run. Both the snapshot-capture and the snapshot-apply loop now guard with `hasattr`.
  - **[ui/main_window.py] Inverted `wait()` in `_clear_monitor_seed_worker`.** Waited on the seed worker only when it was *not* running — effectively a no-op. Fixed to wait when running, so teardown is deterministic.
  - **[ui/main_window.py] Dead `finalize_total` local removed** from `_refresh_download_summary` (never read).
  - **[ui/main_window.py] Removed unused `re` import** that survived the modularization pass.
  - **[extractors/ytdlp.py] Removed unused `subprocess` and `_CREATE_NO_WINDOW` imports** (dead after the `http.run_capture_interruptible` migration).
  - **[ui/main_window.py] Fixed 5 placeholder-free f-strings** (`f"[CLIPBOARD] Rejected malformed URL"`, `f"Audio merge: enabled (...)"`, etc.). Cosmetic / perf-adjacent; no behavior change.
  - Verified: `py_compile` clean, `pyflakes` clean across all touched files, headless `StreamKeep` instantiation + `FinalizeWorker` construction with an intentionally-poisoned snapshot (unknown key) both pass.
- v4.13.0 — **QA/UX/performance pass + background finalize.** Large cross-module polish round on top of v4.12.1 touching 18 existing files (+1669/-462) and adding two new worker modules.
  - **Background finalize worker** — New `streamkeep/workers/finalize.py` (`FinalizeWorker(QThread)`) moves metadata save, NFO writing, chapter export, Twitch chat download, and post-processing **off the UI thread** onto a dedicated worker. The main window tracks planned steps and emits a `finalize progress` signal so the Download tab now shows a non-blocking finalize progress bar after ffmpeg exits. Post-process settings are snapshotted per-task so mid-run Settings edits cannot corrupt an in-flight finalize. `closeEvent` cancels the finalize worker cleanly.
  - **Monitor ops moved off the UI thread** — New `streamkeep/workers/monitor_ops.py` with `SeedArchiveWorker` (fetches existing VOD sources for a newly-added monitored channel) and `AutoRecordResolveWorker` (resolves a live channel to a downloadable stream). These eliminate the remaining 15s UI hitches when adding a channel or when auto-record fired against a slow host. Both wrap their HTTP calls in `http_interruptible(...)` so app close cancels inflight curl immediately.
  - **Queue/auto-start correctness** — Queued, subscribed, and manually-selected VODs now preserve their direct-source metadata, so queued Twitch numeric VODs auto-start correctly and selected VOD title/channel metadata is no longer lost on dispatch. Queue is now locked while a download is active (prevents a reorder from re-entering dispatch on the running row).
  - **Auto-record retry + monitor responsiveness** — Auto-record now retries on transient resolve failures (backoff on per-channel `_in_flight` bookkeeping) and the monitor tick no longer stacks pokes on a channel whose previous poll is still running.
  - **Fetch/scrape cancellation** — `FetchWorker`, `PageScrapeWorker`, and `PlaylistExpandWorker` now check `isInterruptionRequested()` between stages and wrap `http.*` calls in `http_interruptible(...)`. Cancel during fetch/scrape returns immediately instead of finishing the inflight request.
  - **Live-capable channel URLs prefer live resolution** — Platforms that can serve both live streams and VOD listings (Kick, Twitch, Rumble) now probe live first and fall back to VOD listing, so clicking a bare channel URL during a live broadcast starts the live capture instead of enumerating old VODs.
  - **History/channel metadata persistence + analytics** — `models.HistoryEntry` tracks channel id/name explicitly, and Download/History/Monitor tabs now share a single metadata contract so analytics (channel breakdown, repeat-channel stats) are accurate across tabs.
  - **Premium UI polish** — Download, Monitor, History, and Settings tabs got a dense-mode refresh: tighter spacing, shimmer on primary CTAs, hover lifts, branded progress, and finalize-aware status messaging. No theme change — still Catppuccin Mocha.
  - **Cleanup** — `streamkeep/workers/__init__.py` now exports `FinalizeWorker`, `SeedArchiveWorker`, `AutoRecordResolveWorker`. No functional behavior change outside the items above.
- v4.12.1 — **Hotfix: frozen-exe fork bomb.** The v4.12.0 build of `StreamKeep.exe` would spawn hundreds/thousands of itself on launch and wedge the machine. Root cause: `streamkeep/bootstrap.py` did `subprocess.check_call([sys.executable, "-m", "pip", "install", pkg], ...)` whenever an optional dep failed to import (`deno`, `playwright`, etc.). In a PyInstaller-frozen exe, `sys.executable` IS the exe itself, so every failed import re-ran `StreamKeep.exe`, which re-entered `bootstrap()`, which spawned another `StreamKeep.exe` — exponential process explosion. Three fixes in this release:
  1. **[bootstrap.py](streamkeep/bootstrap.py)**: early-return when `sys.frozen` or `sys._MEIPASS` is set. Frozen exes MUST have all deps baked in at build time; there is no valid path where pip-install-at-runtime makes sense for a packaged binary. Also dropped the bogus `deno` entry (standalone native binary, not a pip package — it was the most reliable trigger).
  2. **[StreamKeep.py](StreamKeep.py)**: call `multiprocessing.freeze_support()` as the very first thing in `main()`. Belt-and-suspenders so a future `multiprocessing` call can't re-enter the launcher in child processes.
  3. **[scrape.py ensure_playwright_browser](streamkeep/scrape.py)**: same `sys.executable -m playwright install chromium` pattern — would have bombed the first time a user triggered a page scrape in the frozen exe. Now no-ops the auto-install path when frozen and surfaces a "install Playwright browsers manually" message instead.

  Recovery: `taskkill /F /IM StreamKeep.exe /T` run twice in a row reliably kills every child and descendant.
- v4.12.0 — QA audit pass. Cross-module fixes for real, reproducible defects found in a top-to-bottom read of ~3800 lines across core/http/scrape/workers/extractors/postprocess/UI. Highlights:
  - **http.py** `parallel_http_download`: temp `.parts` directory is now cleaned up on cancel / size-mismatch / concat failure (previously orphaned on disk). Curl worker subprocesses have their stderr pipe closed in a `finally` block and are `wait()`'d after terminate so cancel no longer leaks file descriptors or leaves zombie procs.
  - **config.py** `save_config`: atomic write via `.json.tmp` + `os.replace` with an fsync and a one-deep `.json.bak` rotation — crashes mid-write no longer corrupt the config. Serialized behind a module lock. `load_config` now explicitly decodes UTF-8 (was using Windows `cp1252` default, which mangled non-ASCII titles/paths). `write_log_line` rotation guarded by a second lock.
  - **workers/download.py**: ffmpeg branch switched from `stdout=PIPE` (never drained — would deadlock any ffmpeg preset that emits to stdout) to `stdout=DEVNULL`. Added `encoding="utf-8", errors="replace"` to both ffmpeg and yt-dlp Popens so non-ASCII output paths can't raise `UnicodeDecodeError`. ffmpeg progress regex now tolerates malformed `time=` tokens via a localized `try/except`. Parallel-download progress `_cb` hardened against `ZeroDivisionError`/`OverflowError` when total size is unknown.
  - **hls.py** `parse_hls_master`: URL joining now uses `urllib.parse.urljoin` with a directory heuristic (no more `//double//slash` on CDN variants). Quality `name` now comes from the last path component (was `split("/")[0]` which returned `"https:"` or the wrong directory on absolute URLs).
  - **clipboard.py**: Regex is now anchored per-line, filters `<>"'\\`, and scans only the first non-empty line of the paste so multi-line garbage + stray whitespace can't be emitted as a URL.
  - **utils.py** `safe_filename`: strips `{` and `}` (template braces that leak through `render_template` fallbacks), and the trailing dot/space trim now runs *after* truncation so a cut mid-word can't reintroduce Windows-invalid trailing chars.
  - **StreamKeep.py**: `QApplication` now constructed before the ffmpeg probe so every error path has a valid Qt context. ffmpeg check broadened from bare `FileNotFoundError` to `(FileNotFoundError, PermissionError, OSError, TimeoutExpired)` and validates `returncode == 0`.
  - **extractors/twitch.py**: `list_vods` iterates GraphQL edges defensively — malformed/partial responses with missing `node` or `id` no longer `KeyError`-crash the FetchWorker. `_resolve_vod` EXTINF summation tolerates malformed float tokens (mid-rewrite playlists).
  - **extractors/ytdlp.py** `list_playlist_entries`: added `isinstance(data, dict)` check before `.get("_type")` — yt-dlp returning a list on certain edge cases no longer raises `AttributeError`.
  - **postprocess/processor.py**: `convert_delete_source` now verifies the destination file exists AND is non-zero bytes before deleting the source, for both video and audio paths. (ffmpeg can exit 0 with an empty output in rare interrupted-mux cases.)
  - **postprocess/convert_worker.py**: Progress signal now emits 1-based index (was 0-based → UI showed "0 of N" for the first file), and batch skips files that vanished between the file dialog and worker start.
  - **monitor.py**: `ChannelMonitor._poll_tick` no longer runs network calls on the Qt main thread — each tick dispatches a `QRunnable` to a dedicated 2-slot `QThreadPool`. Per-entry `_in_flight` set prevents a slow check from stacking duplicate pokes on the next tick. Entry list mutations are now guarded by an `RLock`. Fixes 15-second UI hitches when any monitored channel had a slow response.
  - **ui/main_window.py** `closeEvent`: now terminates workers if `cancel()`/`wait()` times out, hides + `deleteLater`s the tray icon (Windows was leaking a dead tray slot until explorer restart), and also stops the standalone `ConvertWorker` if running. `_log` panel now soft-caps at 5000 blocks and trims 1000 at a time so long monitor sessions can't grow the `QPlainTextEdit` unbounded. `_on_stop` resets per-segment progress-bar stylesheets so the next download starts from neutral. `_on_channel_live` waits + clears a stale (finished) `download_worker` reference before assigning a new one so the new worker's signals don't collide with the old object.
- v4.11.0 — Full modularization. `StreamKeep.py` went from 7784 lines to 59 (a launcher). All code now lives in the `streamkeep/` package split across 30+ files organized by concern. Phase 1 extracted leaf modules, extractors, workers, post-processing, metadata, monitor, clipboard; Phase 2 moved the `StreamKeep` QMainWindow into `streamkeep/ui/main_window.py`; Phase 3 deduplicated curl command building via `_build_curl_cmd()` and ran pyflakes which found 4 latent bugs (`json`/`urllib.parse` missing from the split UI module). No functional changes — the refactor is a pure structural move verified by full-app instantiation tests at every phase boundary.
- v4.10.0 — Standalone manual converter. `ConvertWorker(QThread)` runs the video/audio converter on arbitrary files/folders off the UI thread. Settings → Post-Processing gets two new buttons: **Convert Files...** (multi-select file picker) and **Convert Folder...** (recursive walk). Snapshots converter settings at launch so mid-run edits don't corrupt an in-flight batch. Progress reported per-file via status bar + log, with success/fail tally at the end and a tray notification. Cancel button finishes the current file then stops. Skips `.converted.*` files so repeated runs don't chain.
- v4.9.0 — Converter resize/FPS/sample-rate controls. Video converter gains Scale (original/2160p/1440p/1080p/720p/480p/360p, `-vf scale=-2:N`) and FPS (original/60/30/24, `-r N`) combos. Audio converter gains Sample rate (original/48000/44100/22050, `-ar N`). Setting any of these auto-upgrades the codec from 'copy' to the default encoder since re-encode is required. Config keys: `pp_convert_video_scale`, `pp_convert_video_fps`, `pp_convert_audio_samplerate`. Verified 1920x1080@60fps → 1280x720@30fps transcode via ffprobe.
- v4.8.0 — GPU hardware encoder support for the video converter. Adds NVENC (NVIDIA), QSV (Intel Quick Sync), AMF (AMD), and VideoToolbox (Apple) variants of h264/h265/av1. `_detect_ffmpeg_encoders()` parses `ffmpeg -encoders` output (cached), and `_available_video_codec_keys()` runs a parallel 1-frame probe of each HW encoder (via ThreadPoolExecutor, <2s total) to verify the GPU driver is actually present — compile-time presence alone is insufficient. Settings combo hides unusable encoders. Codec-arg dispatch moved into `_video_codec_extra_args()` so `_run_video_convert()` stays clean. WebM incompatibility guard rewritten to allow AV1 encoders.
- v4.7.0 — Output converter post-processing. `PostProcessor._run_video_convert()` re-muxes/transcodes to user-selected container + codec (containers: mp4, mkv, webm, mov, avi, ts, flv; codecs: copy, h264, h265, vp9, av1, mpeg4). `_run_audio_convert()` same for audio (containers: mp3, m4a, ogg, opus, flac, wav, aac; codecs: copy, mp3, aac, opus, vorbis, flac, pcm). Audio bitrate picker (96k-320k). Optional "delete source after success". Refactored `process_directory()` to scan both video and audio files. Config keys: `pp_convert_video`, `pp_convert_video_format`, `pp_convert_video_codec`, `pp_convert_audio`, `pp_convert_audio_format`, `pp_convert_audio_codec`, `pp_convert_audio_bitrate`, `pp_convert_delete_source`. UI lives in Settings → Post-Processing block.
- v4.6.0 — Multi-connection parallel download for direct MP4 URLs. `_parallel_http_download()` splits files via HTTP Range requests across N threads (default 4, max 16), 3-5x speedup on CDN-hosted content. Probes feasibility via `_http_head()` (status + Content-Length + Accept-Ranges), skips when file <8MB or server lacks ranges. Falls back to ffmpeg on any failure. Settings spinner in Network block. Persists in config as `parallel_connections`.
- v4.5.0 — Headless-browser page scraper for lazy-loaded players (Playwright)
- v4.4.0 — Queue reorder, scheduled downloads, bandwidth window, page scraper
- v4.3.0 — Playlist expansion, chapter split, contact sheet, config i/o
- v4.2.0 — Chapters, stats dashboard, log file, recent URLs
- v4.1.0 — Twitch chat replay, Kodi NFO export, post-processing presets
- v4.0.0 — Templates, queue, subscriptions, webhooks, dedup
- v3.0.0 — Universal media support: SoundCloud, Reddit, Audius, Podcast RSS extractors. Direct URL detection via Content-Type sniffing. Clipboard monitoring for auto-URL capture.
- v2.0.0 — Tab UI (Download/Monitor/History/Settings), channel monitoring + auto-record, download history, metadata saving, config persistence, enhanced speed/ETA tracking
- v1.0.0 — Multi-platform rewrite: extractor plugin system, Kick/Twitch/Rumble/yt-dlp support, platform badges
- v0.4.0 — Batch VOD download with checkbox table
- v0.3.0 — Kick channel URL auto-resolve via API
- v0.2.0 — Configurable segment length
- v0.1.0 — Initial KickVODRipper release
