# StreamKeep - Project Notes

## Tech Stack
- Python 3.10+ with PyQt6 GUI (single-file app)
- ffmpeg for HLS/MP4 download (stream copy, no re-encode)
- curl for API/playlist fetching
- yt-dlp as optional fallback extractor

## Architecture
- **Extractor system**: `Extractor` base class with `__init_subclass__` auto-registry
  - `KickExtractor` — Kick API v2 (`/api/v2/channels/{slug}/videos`)
  - `TwitchExtractor` — GraphQL at `gql.twitch.tv/gql` + `usher.ttvnw.net` for m3u8
  - `RumbleExtractor` — Embed API (`/embedJS/u3/`) for HLS + MP4
  - `SoundCloudExtractor` — API v2 resolve + progressive/HLS transcodings
  - `RedditExtractor` — JSON API (`{url}.json`) + DASH/fallback MP4
  - `AudiusExtractor` — Public discovery API at `discoveryprovider.audius.co`
  - `PodcastRSSExtractor` — RSS/XML parser for podcast episode listing
  - `YtDlpExtractor` — Catch-all fallback, shells to `yt-dlp --dump-json`
- **Direct URL detection**: Content-Type sniffing via HEAD request for raw media URLs
- **Clipboard monitor**: `ClipboardMonitor(QObject)` polls clipboard every 800ms for new URLs
- **Data classes**: `StreamInfo`, `QualityInfo`, `VODInfo` (dataclasses)
- **Utility functions**: `_curl()`, `_curl_json()`, `_curl_post_json()`, `_parse_hls_master()`, `_parse_hls_duration()`
- **Workers**: `FetchWorker` (QThread) resolves URLs via extractors, `DownloadWorker` (QThread) runs ffmpeg
- **GUI**: `StreamKeep(QMainWindow)` with VOD table, segment table, quality/segment combos, batch download
- Catppuccin Mocha dark theme via global QSS

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
