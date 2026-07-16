# StreamKeep

![Version](https://img.shields.io/badge/version-4.31.7-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)

StreamKeep is a local-first desktop downloader and archive manager for live streams, VODs, podcasts, and direct media URLs. It combines native extractors, yt-dlp fallback, channel monitoring, queue management, post-processing, an embedded player, a local web gallery, upload adapters, and a CLI/server mode in one PyQt6 application.

![StreamKeep screenshot](https://github.com/user-attachments/assets/3b92c55c-9ae3-4025-8f44-3119b492fe8f)

## Supported Sources

| Source | VOD listing | Live capture | Method |
| --- | :---: | :---: | --- |
| Kick | Yes | Yes | Hybrid official + v2 API |
| Twitch | Yes | Yes | Native GraphQL + Usher HLS |
| Rumble | No | Yes | Native embed API |
| SoundCloud | No | No | Native API v2, progressive and HLS |
| Reddit | No | No | JSON API, DASH, MP4 fallback |
| Audius | No | No | Native discovery API |
| Podcast RSS | Yes | No | Feed enclosure parser |
| Direct media URLs | No | No | HEAD/content-type sniffing |
| YouTube and 1000+ sites | Varies | Varies | yt-dlp fallback |

## Core Workflows

### Download and Queue

- Paste a supported URL, fetch stream metadata, choose a quality, and download HLS, DASH, MP4, audio, or podcast media.
- Queue multiple items, reorder pending work, batch-import URLs from text, and resume interrupted segmented downloads from sidecar state.
- Persist fetch, download, and finalize failures to a retryable recovery ledger that survives restart and is exposed in the queue and web remote.
- Use parallel HTTP range downloads for direct files when the server supports ranges.
- Apply bandwidth windows, day/night/weekend speed scheduling, per-download rate limits, and lifecycle cleanup rules.

### Channel Monitor

- Monitor Kick and Twitch channels with per-channel intervals and auto-record rules.
- Override output directory, quality, filename template, schedule window, active days, and retention count per channel.
- Escalate polling around scheduled streams and avoid duplicate in-flight checks.

### Library, Storage, and Search

- Persist history, monitor entries, and queue state in `%APPDATA%\StreamKeep\library.db`.
- Keep user preferences in `%APPDATA%\StreamKeep\config.json`; portable mode uses `portable.txt` beside the executable and stores data under `data/`.
- Search across history, monitor entries, queue rows, transcripts, notes, and tags.
- Scan storage by platform/channel/title, detect orphaned files, and recycle selected recordings through the OS recycle bin.
- Capture SHA-256 archive manifests for completed recordings, then right-click History rows to verify or rescan the manifest when files intentionally change.

### Player and Clip Tools

- Play recordings in-app with libmpv, watch-position persistence, chapter navigation, bookmarks, EQ, playback speed, normalization, and picture-in-picture.
- Open a multi-stream sync viewer for 2-4 selected recordings.
- Trim or clip recordings with stream-copy mode or frame-accurate re-encode mode.

### Post-Processing and Intelligence

- Convert video and audio after download or through the standalone batch converter.
- Use GPU encoders when available: NVENC, Intel Quick Sync, AMD AMF, and VideoToolbox.
- Generate contact sheets, thumbnails, chapters, subtitle files, transcripts, summaries, highlights, silence-removed cuts, RSS feeds, and local gallery share pages.
- Integrate SponsorBlock markers, platform subtitles, Twitch/Kick chat capture, emote-aware chat rendering, and optional LLM summaries.

### Uploads, Backup, and Plugins

- Upload finished media to S3-compatible storage, Backblaze B2/MinIO, FTP/SFTP, and WebDAV.
- Create `.skbackup` archives containing config, database, archive-manifest state, tags, notifications, plugin metadata, and local state.
- Restore with a pre-restore backup and overwrite safety checks.
- Load plugin manifests only after trust consent; untrusted plugins are skipped.

Plugins live under the active config directory in `plugins/`. A plugin is a package or module directory containing `plugin.json`:

```json
{
  "id": "example-extractor",
  "name": "Example Extractor",
  "version": "1.0.0",
  "author": "You",
  "description": "Adds support for example.com",
  "enabled": true,
  "trusted": false
}
```

Supported extension points are custom extractors, post-processing filters, and upload destinations. Plugins run in-process with the same privileges as StreamKeep, so they should stay untrusted until the user has reviewed the source.

### CLI and Server Mode

```powershell
python StreamKeep.py --help
python StreamKeep.py --version
python StreamKeep.py extractors
python StreamKeep.py download "https://example.com/video" --quality best --output C:\Videos
python StreamKeep.py server --bind 127.0.0.1 --port 8765
```

Legacy flat flags remain supported for automation:

```powershell
python StreamKeep.py --list-extractors
python StreamKeep.py --url "https://example.com/video" --output C:\Videos
python StreamKeep.py --server --port 8765
```

The local server binds to localhost by default, validates bearer tokens in constant time, checks Host headers to resist DNS rebinding, and restricts browser companion access to local/chrome-extension origins.
When LAN access is enabled in Settings or with `server --bind 0.0.0.0`, StreamKeep only accepts Host and Origin values that match this machine's local interface names or IP addresses, and the token is still required for API calls.
Authenticated `/api/status` responses include retryable failure records, and `/api/failures/retry` plus `/api/failures/discard` update the recovery ledger.

## Browser Companion

The Chrome/Edge/Firefox companion extension lives in `browser-extension/`.

1. Load the extension unpacked from `browser-extension/`.
2. Open StreamKeep, go to Settings, enable Browser companion, and copy the pairing token and port.
3. Paste them into the extension popup.
4. Use **Send to Fetch** or **Send to Queue** from the browser toolbar.

Extension icons are shipped under `browser-extension/icons/`. Pairing tokens are generated per app launch and are never written to disk.

## Requirements

- Python 3.10 or newer.
- `ffmpeg` and `ffprobe` in `PATH`.
- `curl` in `PATH`.
- Python dependencies from `requirements.txt`, including `keyring` for secure credential storage on non-Windows systems.
- For full YouTube fallback support through yt-dlp: install the default yt-dlp extras (`pip install -U "yt-dlp[default]"`) and provide Deno 2.3+ or Node.js 22+ in `PATH`. Settings and onboarding report when yt-dlp, `yt-dlp-ejs`, or a JavaScript runtime is missing.
- Optional: `mpv`/`libmpv` for embedded playback, browser cookies libraries for cookie import, and platform-specific signing tools for distributable packages.

Install Python dependencies:

```powershell
pip install -r requirements.txt
```

Run the GUI:

```powershell
python StreamKeep.py
```

## Configuration Locations

| Mode | Config and database location |
| --- | --- |
| Windows installed/source | `%APPDATA%\StreamKeep\` |
| Windows portable | `data\` beside `StreamKeep.exe` when `portable.txt` exists |
| Linux | `$XDG_CONFIG_HOME/StreamKeep` or `~/.config/StreamKeep` |
| macOS | `~/Library/Application Support/StreamKeep` |

History, monitor channels, queue data, failed-job recovery records, and archive integrity manifests are stored in SQLite with WAL mode. Older JSON history/monitor/queue state migrates into SQLite on first launch when the database is empty.

## Packaging Notes

Source checkouts run directly with `python StreamKeep.py`. Release packaging currently has scaffolds for:

- PyInstaller single-file builds for Windows with `python -m PyInstaller --clean StreamKeep.spec`.
- MSIX packaging through `packaging/msix/build_msix.py` after a PyInstaller build.
- Flatpak packaging under `packaging/flatpak/`.
- Browser companion extension packaging from `browser-extension/`.

MSIX signing is automatic when `signtool.exe` is available and one of `STREAMKEEP_SIGN=1`, `STREAMKEEP_SIGN_PFX`, or `STREAMKEEP_SIGN_CERT_SUBJECT` is set.

Release packages must include:

- `StreamKeep.py` launcher and the `streamkeep/` package.
- `requirements.txt`.
- `LICENSE`.
- `icon.ico`, `icon.png`, and `assets/`.
- `browser-extension/` and `browser-extension/icons/`.
- `packaging/` manifests when building MSIX or Flatpak artifacts.
- Optional dependency notes for ffmpeg, curl, yt-dlp, PyQt6, Pillow, send2trash, websocket-client, mpv/libmpv, and platform signing tools.
- A `.sha256` sidecar for each downloadable executable; the in-app updater refuses to install releases without valid SHA-256 metadata.

## Validation

Run the lightweight validation bundle before release:

```powershell
python -m compileall StreamKeep.py streamkeep tests
python -m streamkeep.i18n.compile_translations
python -m pytest -q
python StreamKeep.py --version
python StreamKeep.py --list-extractors
python StreamKeep.py download --help
python StreamKeep.py server --help
```

When pyflakes is installed, also run:

```powershell
python -m pyflakes StreamKeep.py streamkeep tests
```

For UI-facing changes, launch the app and exercise the affected tab. For packaging changes, build the target artifact locally and smoke-launch it before publishing.

## Development Notes

- Keep the app local-first: no cloud sync by default and no DRM circumvention features.
- Keep local HTTP APIs bound to loopback and token-gated.
- Use `QThread`/signals for background work; do not block the GUI thread.
- Keep subprocess arguments explicit, use `--` separators for user URLs, restrict curl/ffmpeg protocols, and pass `-nostdin` to ffmpeg jobs.
- Preserve accessibility fundamentals in every UI change: named controls, keyboard-navigable dialogs, readable contrast, status text for long-running work, and log/toast feedback for failures.
- Do not add GitHub Actions workflows; builds, tests, audits, and release artifacts are produced locally for this repo.

## License

MIT. See [LICENSE](LICENSE).
