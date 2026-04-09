# KickVODRipper

A desktop GUI tool for downloading Kick.com VODs and live DVR streams in configurable segments.

<img width="1457" height="1014" alt="2026-04-08 23_45_10-KickVODRipper v0 1 0" src="https://github.com/user-attachments/assets/3b92c55c-9ae3-4025-8f44-3119b492fe8f" />


Paste a Kick channel URL (e.g., `kick.com/fishtank`) or a direct m3u8 playlist URL — KickVODRipper resolves stream sources via the Kick API, lets you pick quality and segment length, and downloads everything with ffmpeg.

## Features

- **Kick URL auto-resolve** — paste `kick.com/username`, the app fetches all available VODs via the Kick API
- **Batch download** — select individual VODs or check all at once, download them all sequentially
- **Quality picker** — choose from all available qualities (1080p, 720p, 480p, 360p, 160p)
- **Configurable segments** — split downloads into 15min, 30min, 1hr, 2hr, 4hr chunks, or download as a single file
- **Direct m3u8 support** — works with any HLS playlist URL, not just Kick
- **Resume-friendly** — skips already-downloaded segments on re-run
- **Per-segment progress** — progress bars for each segment with overall batch progress
- **Catppuccin Mocha dark theme**

## Requirements

- **Python 3.10+**
- **ffmpeg** in PATH
- **PyQt6** (auto-installed on first run)

## Usage

```bash
python KickVODRipper.py
```

1. Paste a Kick channel URL (`kick.com/fishtank`) or m3u8 URL
2. Click **Fetch**
3. If multiple VODs are found, check the ones you want
4. Select quality and segment length
5. Click **Download All Checked** or **Load Selected** to preview segments first

## How It Works

- For Kick URLs, the app queries `kick.com/api/v2/channels/{slug}/videos` to get the DVR/VOD source URLs
- VOD streams use Amazon IVS with full HLS playlists (not live-only) so the complete stream history is downloadable
- Downloads use `ffmpeg -c copy` (no re-encoding) for maximum speed
