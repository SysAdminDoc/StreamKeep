# StreamKeep

![Version](https://img.shields.io/badge/version-4.34.0-blue)
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

- Paste a supported URL, fetch stream metadata, choose a quality, and download HLS, DASH, MP4, audio, or podcast media. Native HLS and DASH sources expose a media-track table for selecting one video Representation plus multiple alternate audio and subtitle renditions; the same explicit track map is retained for resume and command export.
- Queue multiple items, reorder pending work, batch-import URLs from text, and resume interrupted segmented downloads from sidecar state.
- Scan webpages in a sandboxed headless browser whose HTTP(S) requests are DNS-validated and pinned to globally routable addresses. The one-shot **Allow LAN for this scan** control permits only RFC1918/ULA targets; loopback, link-local, metadata, and other special addresses remain blocked.
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
- Create secret-free `.skbackup` archives containing preferences, database/archive state, tags, notifications, and optional redacted logs. Account credentials and cookies are excluded from ordinary create/restore operations.
- Transfer authentication state only with an explicit `.sksbackup` protected by Argon2id and AES-256-GCM; wrong passwords and modified backups fail authentication before restore.
- Export/import preferences through a versioned, size-bounded JSON format. Imports show a redacted diff and keep hooks, webhooks, proxies, cookie sources, control servers, media-server auto-import, and lifecycle cleanup disabled until each capability is approved separately.
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
python StreamKeep.py download "https://example.com/video" --format "bv*+ba/b" --format-sort-preset prefer-av1 --container mkv
python StreamKeep.py download "https://example.com/video" --audio-format opus --audio-quality 128K
python StreamKeep.py download "https://example.com/video" --sponsorblock-mark intro,chapter --sponsorblock-remove sponsor
python StreamKeep.py download "https://example.com/video" --sub-langs en,es --auto-subs --convert-subs srt --sub-delivery sidecar
python StreamKeep.py download "https://example.com/live" -N 4 --retries infinite --fragment-retries 20 --retry-sleep "fragment:exp=1:20" --live-from-start
python StreamKeep.py server --bind 127.0.0.1 --port 8765
python StreamKeep.py server --trusted-proxy-origin https://streamkeep.example.lan --port 8765
python StreamKeep.py backup create C:\Backups\StreamKeep.skbackup
python StreamKeep.py backup restore C:\Backups\StreamKeep.skbackup
python StreamKeep.py backup secrets-export C:\Backups\StreamKeep-secrets.sksbackup
python StreamKeep.py backup secrets-import C:\Backups\StreamKeep-secrets.sksbackup
```

For sources resolved through yt-dlp direct mode, the desktop Advanced panel and `download` CLI also support verbatim `--format` specifications, custom or named format sorting, MP4/MKV/WebM/original containers, and best/MP3/M4A/Opus/FLAC/WAV audio extraction. Resolution-cap presets are available at 2160p, 1080p, and 720p. Resolved manual and automatic subtitle languages appear in a per-download multi-select; subtitles can be converted to SRT/VTT/ASS and embedded or retained as sidecars. SponsorBlock offers a 13-category mark/remove matrix, including mark-only enforcement for highlights and community chapters, plus an optional custom HTTPS API base. Fragment concurrency, retry counts and backoff, unavailable-fragment handling, throttling thresholds, start-from-beginning live capture, scheduled-stream polling, and chapter/metadata/thumbnail embedding can be set globally or per download. Settings also manages named, one-argument-per-line yt-dlp templates that can be attached to downloads, queued jobs, CLI runs, and monitor profiles; command/config delegation and executable boundaries are rejected. After a job is prepared, **Copy command** exports its standalone yt-dlp or FFmpeg invocation, including the selected cookie source and structured header arguments. Use `python StreamKeep.py download --help` for the complete option list. Native HLS/direct-media jobs continue to use their existing output path.

Playlist/channel expansion can be narrowed in Advanced with yt-dlp item ranges, after/before dates, match filters, and a maximum download count. Incremental archive sync stores a private archive per source, stops expansion when it reaches previously downloaded entries, and is also applied automatically to monitor VOD subscriptions.

Portable-secret commands prompt for a password. For non-interactive automation, provide it through `STREAMKEEP_PORTABLE_SECRET_PASSWORD`; passwords are never accepted in command-line arguments or written to logs.

Legacy flat flags remain supported for automation:

```powershell
python StreamKeep.py --list-extractors
python StreamKeep.py --url "https://example.com/video" --output C:\Videos
python StreamKeep.py --server --port 8765
```

The local server always binds to `127.0.0.1`, validates bearer tokens in constant time, rejects duplicate or unconfigured Host headers, and binds paired client tokens to their browser origin and scopes. Every mutating request requires JSON plus a fresh 128-bit nonce and timestamp; replays, stale requests, cross-site fetches, and unapproved origins are rejected.
LAN access is opt-in and only operates through an explicitly configured HTTPS reverse proxy. The proxy must run on the StreamKeep PC, be the only process exposed to the network, forward to the displayed loopback port, and set exact `X-Forwarded-Proto: https` and `X-Forwarded-Host` values matching the configured HTTPS origin. Direct `0.0.0.0` HTTP control is refused. Headless setup can explicitly request one five-minute code with `server --pairing-code-stdout`; bearer tokens are never accepted in argv, printed, placed in URLs, or written to logs.
In server mode, `POST /api/queue` writes a durable SQLite job before returning `202` with a `job_id`. Use `GET /api/jobs/{job_id}` or `/api/status` to observe fetch, download, finalization, and terminal state; `POST /api/jobs/cancel` persists cancellation. Eligible interrupted jobs resume on restart, completed jobs appear in `/api/library`, and `/api/failures/retry` creates an observable retry job with its own durable acknowledgement.

## Browser Companion

The Chrome/Edge/Firefox companion extension lives in `browser-extension/`.

1. Load the extension unpacked from `browser-extension/`.
2. Open StreamKeep, go to Settings, enable Browser companion, and select **New code**.
3. Enter the displayed loopback port and one-time code in the extension popup, then select **Pair**.
4. Use **Send to Fetch** or **Send to Queue** from the browser toolbar.

Extension icons are shipped under `browser-extension/icons/`. The 256-bit master token is stored through the operating-system credential backend and never shared with clients. One-time pairing codes expire after five minutes; successful pairing returns a scoped, origin-bound client token. **Revoke all** invalidates every client and rotates the stored master token.

## Requirements

- Python 3.10 or newer.
- FFmpeg and ffprobe 8.1.2 or newer in `PATH`.
- curl 8.21.0 or newer in `PATH`.
- Python dependencies from `requirements.txt`, including `keyring`/Windows DPAPI for secure credential storage plus `argon2-cffi` and `cryptography` for authenticated portable-secret backups.
- The pinned Python security floors are yt-dlp 2026.07.04 and Pillow 12.3.0. For full YouTube fallback support, install the default yt-dlp extras (`pip install -U "yt-dlp[default]"`) and provide Deno 2.3+ or Node.js 22+ in `PATH`; the installed `yt-dlp-ejs` version must exactly match yt-dlp's package requirement. StreamKeep also rejects raw argument templates that create shortcut/link files or delegate to executable command boundaries.
- StreamKeep records the exact path, version, provenance, and enabled capabilities for each runtime dependency. Settings, onboarding, and diagnostic snapshots expose that registry; missing or below-floor tools block only the dependent operation and include repair guidance. Startup never installs packages implicitly.
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

History, monitor channels, queue data, failed-job recovery records, and archive integrity manifests are stored in SQLite. WAL is enabled only when the runtime contains SQLite's WAL-reset fix; older source runtimes automatically use safe rollback journaling and report that degraded mode in diagnostics. Frozen releases refuse to start with an unsafe SQLite. Older JSON history/monitor/queue state migrates into SQLite on first launch when the database is empty.
Credential values are stored outside `config.json` in the operating-system credential store (with a Windows DPAPI-protected fallback); config and account rows contain only `secretref:` handles. Legacy plaintext values migrate only after secure storage succeeds.

## Packaging Notes

Source checkouts run directly with `python StreamKeep.py`. Release packaging currently has scaffolds for:

- PyInstaller single-file builds for Windows with `python packaging/build.py --clean --noconfirm`. The release builder pins and SHA3-verifies an upstream SQLite runtime containing the WAL-reset fix; the spec rejects unsafe frozen builds.
- MSIX packaging through `packaging/msix/build_msix.py` after a PyInstaller build.
- Flatpak packaging under `packaging/flatpak/`.
- Browser companion extension packaging from `browser-extension/`.

MSIX signing is automatic when `signtool.exe` is available and one of `STREAMKEEP_SIGN=1`, `STREAMKEEP_SIGN_PFX`, or `STREAMKEEP_SIGN_CERT_SUBJECT` is set.

In-app updates require `StreamKeep.exe`, `StreamKeep-update.json`, and `StreamKeep-update.json.sig` on the same stable GitHub release. Generate them with `python packaging/update_manifest.py --version X.Y.Z --sequence N --asset dist/StreamKeep.exe` (add the MSIX with another `--asset` when published). The command requires `STREAMKEEP_SIGN_PFX`, signs each asset by default, and signs the canonical manifest with the same publisher key. The MSIX builder signs its contained executable before packaging so installed builds retain the updater trust anchor. Release sequences must increase monotonically.

Release packages must include:

- `StreamKeep.py` launcher and the `streamkeep/` package.
- `requirements.txt`.
- `LICENSE`.
- `icon.ico`, `icon.png`, and `assets/`.
- `browser-extension/` and `browser-extension/icons/`.
- `packaging/` manifests when building MSIX or Flatpak artifacts.
- Optional dependency notes for ffmpeg, curl, yt-dlp, PyQt6, Pillow, send2trash, websocket-client, mpv/libmpv, and platform signing tools.
- An offline-signed update manifest and detached signature produced by `packaging/update_manifest.py`; the updater rejects unsigned assets, publisher changes, path substitution, replayed sequences, downgrades, and signed size/digest mismatches.

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

For a Windows one-file release, build and run the hidden artifact-boundary smoke suite:

```powershell
python packaging/build.py --clean --noconfirm
python packaging\artifact_smoke.py --executable .\dist\StreamKeep.exe
```

The artifact suite exercises empty, legacy-migrated, and populated libraries offscreen, writes machine-readable readiness records, checks embedded yt-dlp and thumbnail initialization, rejects process re-entry fanout, and enforces a bounded clean exit. For UI-facing changes, exercise the affected tab only when a non-disruptive test desktop is available.

## Development Notes

- Keep the app local-first: no cloud sync by default and no DRM circumvention features.
- Keep local HTTP APIs bound to loopback and token-gated.
- Use `QThread`/signals for background work; do not block the GUI thread.
- Keep subprocess arguments explicit, use `--` separators for user URLs, restrict curl/ffmpeg protocols, and pass `-nostdin` to ffmpeg jobs.
- Resolve external media/network tools through the shared runtime capability registry so below-floor executables cannot enter download, inspection, post-processing, or webhook paths.
- Preserve accessibility fundamentals in every UI change: named controls, keyboard-navigable dialogs, readable contrast, status text for long-running work, and log/toast feedback for failures.
- Do not add GitHub Actions workflows; builds, tests, audits, and release artifacts are produced locally for this repo.

## License

MIT. See [LICENSE](LICENSE).
