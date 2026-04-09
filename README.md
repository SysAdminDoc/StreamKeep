# StreamKeep

A multi-platform desktop GUI tool for downloading VODs and live streams with native extractors, channel monitoring, and segmented downloads.

<img width="1457" height="1014" alt="2026-04-08 23_45_10-KickVODRipper v0 1 0" src="https://github.com/user-attachments/assets/3b92c55c-9ae3-4025-8f44-3119b492fe8f" />

## Supported Platforms

| Platform | VOD Listing | Live Download | Method |
|----------|:-----------:|:-------------:|--------|
| **Kick** | Yes | Yes | Native API (`/api/v2/channels`) |
| **Twitch** | Yes | Yes | Native GraphQL + Usher m3u8 |
| **Rumble** | - | Yes | Native Embed API (HLS + MP4) |
| **YouTube, Facebook, 1000+ sites** | - | Varies | yt-dlp fallback |

## Features

### Download
- **Multi-platform** — native extractors for Kick, Twitch, Rumble + yt-dlp fallback for everything else
- **Auto-detect** — paste any URL, StreamKeep identifies the platform and resolves the stream
- **Platform badge** — colored label shows which extractor matched your URL
- **VOD browser** — list all VODs for a channel, check the ones you want, batch download
- **Quality picker** — choose from all available qualities (1080p, 720p, 480p, etc.)
- **Configurable segments** — split downloads into 15min, 30min, 1hr, 2hr, 4hr chunks, or full stream
- **HLS + MP4** — supports both HLS streams and direct MP4 downloads
- **Resume-friendly** — skips already-downloaded segments on re-run
- **Speed/ETA tracking** — real-time download speed, ETA, and file size in progress bars
- **Metadata saving** — writes `metadata.json` + thumbnail alongside every download

### Channel Monitor
- **Watch channels** — add Kick or Twitch channels to monitor for live status
- **Auto-record** — automatically start recording when a monitored channel goes live
- **Configurable polling** — per-channel interval (30-600 seconds)
- **Round-robin** — checks one channel per tick to avoid API hammering

### History & Settings
- **Download history** — persistent log of all completed downloads, double-click to open folder
- **Config persistence** — output directory, segment preference, history, and monitor channels saved to `%APPDATA%\StreamKeep\config.json`
- **Settings tab** — shows ffmpeg/yt-dlp versions, configure default output directory

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

### Channel Monitoring

1. Switch to the **Monitor** tab
2. Paste a channel URL and set poll interval
3. Check **Auto-Record** to automatically capture when the channel goes live
4. StreamKeep checks channels in round-robin and starts recording automatically

## Architecture

StreamKeep uses a plugin-style extractor system with `__init_subclass__` auto-registration. Each platform is a self-contained class. Native extractors provide VOD listing, live detection, and direct API access. The yt-dlp fallback catches any URL not handled by native extractors.

```
URL Input
  -> Extractor.detect(url)  — matches URL against registered patterns
    -> Native Extractor (Kick, Twitch, Rumble)
    -> YtDlpExtractor fallback (everything else)
  -> StreamInfo (qualities, duration, metadata)
  -> DownloadWorker (ffmpeg -c copy, segmented)
  -> metadata.json + history entry
```
