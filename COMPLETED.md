# StreamKeep Completed Work

This file summarizes shipped state. Release-level detail remains in `CHANGELOG.md` and the archived legacy roadmap.

## Core Application

- Python/PyQt6 desktop GUI launched through `StreamKeep.py`.
- Modular `streamkeep/` package with extractors, workers, post-processing, player, local server, upload adapters, intelligence helpers, and UI modules.
- Native extractors for Kick, Twitch, Rumble, SoundCloud, Reddit, Audius, Podcast RSS, direct media URLs, and yt-dlp fallback.
- Download queue, concurrent downloads, segmented HLS/MP4 handling, parallel HTTP range downloads, resume sidecars, speed/ETA metrics, bandwidth scheduling, and integrity verification.
- SQLite library database for history, monitor channels, and queue persistence.

## Media and Library Features

- VOD listing and pagination, batch selection, quality defaults, platform account manager, cookie import, proxy routing, DASH/MPD support, browser companion, CLI/headless mode, and REST server mode.
- Post-processing presets, lossless trim/clip, waveform and storyboard views, silence removal, multi-range highlight reels, social clip exports, loudness normalization, subtitles, smart thumbnails, and content summaries.
- Embedded mpv player, picture-in-picture, multi-stream sync viewer, chapters/bookmarks, playback speed, EQ, and watch-progress persistence.
- Analytics, bandwidth tracking, channel statistics, disk health alerts, tags/smart collections, transcript search, notes, backup/restore, and recording annotations.

## Distribution and Integrations

- Upload destinations for S3/B2/MinIO, FTP/SFTP, and WebDAV.
- Local web gallery and share pages with range streaming.
- RSS feed generation for recordings.
- Browser companion extension and local API bridge.
- Plugin SDK, i18n scaffold, theme density modes, high-contrast palette, encrypted config storage, and native OS notifications.

## Hardening Baseline

- v4.31.1 and v4.31.2 audit passes fixed queue worker wiring, upload crashes/path traversal, media streaming memory hazards, updater hash checks, TLS chat capture, GraphQL login validation, subprocess URL separator handling, worker lifetime leaks, and multiple performance/reliability issues.

## Documentation Consolidation

- Root planning is consolidated into `ROADMAP.md`, `COMPLETED.md`, and `RESEARCH_REPORT.md`.
- Legacy roadmap and feature candidates are archived under `docs/archive/roadmap/`.
