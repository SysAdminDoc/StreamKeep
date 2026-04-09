# StreamKeep

A multi-platform desktop GUI tool for downloading VODs and live streams with native extractors, segmented downloads, and batch operations.

<img width="1457" height="1014" alt="2026-04-08 23_45_10-KickVODRipper v0 1 0" src="https://github.com/user-attachments/assets/3b92c55c-9ae3-4025-8f44-3119b492fe8f" />

## Supported Platforms

| Platform | VOD Listing | Live Download | Method |
|----------|:-----------:|:-------------:|--------|
| **Kick** | Yes | Yes | Native API (`/api/v2/channels`) |
| **Twitch** | Yes | Yes | Native GraphQL + Usher m3u8 |
| **Rumble** | - | Yes | Native Embed API (HLS + MP4) |
| **YouTube, Facebook, 1000+ sites** | - | Varies | yt-dlp fallback |

## Features

- **Multi-platform** — native extractors for Kick, Twitch, Rumble + yt-dlp fallback for everything else
- **Auto-detect** — paste any URL, StreamKeep identifies the platform and resolves the stream
- **Platform badge** — colored badge shows which extractor matched your URL
- **VOD browser** — list all VODs for a channel, check the ones you want, batch download
- **Quality picker** — choose from all available qualities (1080p, 720p, 480p, etc.)
- **Configurable segments** — split downloads into 15min, 30min, 1hr, 2hr, 4hr chunks, or full stream
- **Direct m3u8 support** — works with any HLS playlist URL
- **Format support** — HLS streams and direct MP4 downloads
- **Resume-friendly** — skips already-downloaded segments on re-run
- **Per-segment progress** — progress bars for each segment with overall batch progress
- **Catppuccin Mocha dark theme**

## Requirements

- **Python 3.10+**
- **ffmpeg** in PATH
- **PyQt6** (auto-installed on first run)
- **yt-dlp** (auto-installed, optional — needed for non-native platforms)

## Usage

```bash
python StreamKeep.py
```

### Quick Start

1. Paste a URL:
   - `kick.com/fishtank` — lists all Kick VODs
   - `twitch.tv/xqc` — lists Twitch VODs or records live
   - `rumble.com/v...` — downloads Rumble video
   - Any video URL — falls back to yt-dlp
2. Click **Fetch**
3. Select quality and segment length
4. Click **Download Selected** or **Download All Checked** for batch

### Extractor Architecture

StreamKeep uses a plugin-style extractor system. Each platform is a self-contained class that auto-registers via `__init_subclass__`. Native extractors provide VOD listing, live detection, and direct API access. The yt-dlp fallback catches any URL not handled by native extractors.
