# KickVODRipper - Project Notes

## Tech Stack
- Python 3.10+ with PyQt6 GUI
- ffmpeg for HLS download (stream copy, no re-encode)
- curl for API/playlist fetching
- Kick API v2 (`/api/v2/channels/{slug}/videos`) for VOD source resolution

## Key Architecture
- Single-file app: `KickVODRipper.py`
- `_bootstrap()` auto-installs PyQt6 on first run
- `FetchWorker` (QThread): resolves Kick URLs via API, fetches/parses m3u8 playlists
- `DownloadWorker` (QThread): runs ffmpeg per segment, parses stderr for progress
- Batch VOD download: iterates checked VODs sequentially, each gets a subfolder
- Catppuccin Mocha dark theme via global QSS stylesheet

## Kick API Details
- `GET /api/v2/channels/{slug}/videos` — returns array of VODs with `source` field containing the full DVR master.m3u8 URL
- DVR URL pattern: `https://stream.kick.com/{hex_prefix}/ivs/v1/{account_id}/{channel_id}/{date_path}/{session_id}/media/hls/master.m3u8`
- The hex prefix is NOT derivable from other API fields — must come from the `source` field
- `GET /api/v2/channels/{slug}/livestream` — returns `playback_url` (live m3u8, not DVR)

## Build/Run
```bash
python KickVODRipper.py
```
No build step. ffmpeg must be in PATH.

## Version History
- v0.4.0 — Batch VOD download with checkbox table, select all
- v0.3.0 — Kick channel URL auto-resolve via API, VOD picker
- v0.2.0 — Configurable segment length (15m/30m/1h/2h/4h/full)
- v0.1.0 — Initial release, m3u8 download with hourly segments
