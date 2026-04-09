# StreamForge - Project Notes

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
  - `YtDlpExtractor` — Catch-all fallback, shells to `yt-dlp --dump-json`
- **Data classes**: `StreamInfo`, `QualityInfo`, `VODInfo` (dataclasses)
- **Utility functions**: `_curl()`, `_curl_json()`, `_curl_post_json()`, `_parse_hls_master()`, `_parse_hls_duration()`
- **Workers**: `FetchWorker` (QThread) resolves URLs via extractors, `DownloadWorker` (QThread) runs ffmpeg
- **GUI**: `StreamForge(QMainWindow)` with VOD table, segment table, quality/segment combos, batch download
- Catppuccin Mocha dark theme via global QSS

## Key API Details
- **Kick**: `/api/v2/channels/{slug}/videos` returns VOD list with `source` field = DVR master.m3u8
- **Twitch**: GraphQL raw queries (not persisted), Client-ID `kimne78kx3ncx6brgo4mv6wki5h1ko`, access tokens from `streamPlaybackAccessToken`/`videoPlaybackAccessToken`
- **Rumble**: Page URL → scrape `embed/v{id}` → embedJS API returns `ua.hls` and `ua.mp4` URLs
- **yt-dlp**: `--dump-json --no-download` returns format list, mapped to QualityInfo

## Build/Run
```bash
python StreamForge.py
```
No build step. ffmpeg must be in PATH. PyQt6 and yt-dlp auto-installed.

## Version History
- v1.0.0 — Multi-platform rewrite: extractor plugin system, Kick/Twitch/Rumble/yt-dlp support, platform badges
- v0.4.0 — Batch VOD download with checkbox table
- v0.3.0 — Kick channel URL auto-resolve via API
- v0.2.0 — Configurable segment length
- v0.1.0 — Initial KickVODRipper release

## Roadmap (Planned)
- Phase 4: Enhanced download engine (speed/ETA tracking)
- Phase 5: Download queue manager
- Phase 6: TikTok Live, Trovo, DLive, Odysee extractors
- Phase 7: Tab-based UI (Download/Monitor/History/Settings)
- Phase 8: Channel monitoring + auto-record
- Phase 9: Metadata saving + polish
