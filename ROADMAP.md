# StreamKeep Development Roadmap

> **Version at time of writing:** v4.17.0
> **Date:** 2026-04-13
> **Full feature specs:** See `features.md` for detailed What/Why/Touches/Risks per feature.
>
> This roadmap is designed to be followed across multiple implementation sessions.
> Each phase groups features by dependency, scope, and theme. Within a phase,
> features are ordered by recommended implementation sequence. Complete each
> feature fully (implement, test via `py_compile` + `pyflakes` + headless smoke,
> version bump, commit) before moving to the next.

---

## How to use this roadmap

1. **Start a session** by reading this file and `CLAUDE.md`.
2. **Pick the next unchecked feature** in the current phase.
3. **Read `features.md`** for the full spec (What/Why/Touches/Risks).
4. **Plan 3-8 implementation steps**, check in if scope grows.
5. **Implement, test, version bump, commit.**
6. **Check the box** below and update the version number.
7. Move to the next feature or end the session.

### Versioning convention

- Each **phase** bumps the minor version (v4.18.0, v4.19.0, ...).
- Multiple features in one phase share the same minor version.
- Hotfixes within a phase bump patch (v4.18.1).
- The commit message should list all features shipped in that version.

### Quality bar per feature

- `py_compile` clean on all touched files
- `pyflakes` clean (no unused imports, no undefined names)
- Headless smoke test covering the new code path where practical
- No regressions in existing features (verify tabs still load, config round-trips)
- Match existing code style, architecture, and Catppuccin Mocha theme

---

## Phase 1 — Quick Wins
> Small-scope, high-value features with no cross-feature dependencies.
> Goal: ship 6-8 improvements fast, build momentum.

- [x] **F6 — Drag-and-Drop URL & File Import** (Small, ~60-80 lines) ✓ v4.18.0
  Override `dragEnterEvent`/`dropEvent` on QMainWindow. URL drops -> `_on_fetch()`.
  File drops -> `_open_clip_dialog()`. Filter to http(s) URLs and media file extensions.

- [x] **F11 — Keyboard Shortcuts** (Small-Med, ~60-80 lines) ✓ v4.18.0
  Add `QShortcut` bindings: Ctrl+1-5 tabs, Ctrl+F search, Enter fetch/download,
  Escape stop, Delete remove selected, Ctrl+A select all. Show hints in tooltips.

- [x] **F12 — Filename Template Live Preview** (Small, ~40-50 lines) ✓ v4.18.0
  Add QLabel below template inputs in Settings. Wire `textChanged` to debounced
  `render_template()` with sample context. Show "Invalid template" in red on error.

- [x] **F14 — History Orphan Detection** (Small, ~40-60 lines) ✓ v4.18.0
  In `_refresh_history_table()`, check `Path(entry.path).exists()` per row.
  Warning icon + dim row if missing. "Remove missing" context menu action.

- [x] **F5 — One-Click Re-Download from History** (Small, ~50-80 lines) ✓ v4.18.0
  Add "Re-download" to History context menu. Pre-fill URL input + output path
  from `HistoryEntry` fields, trigger `_on_fetch()`.

- [x] **F28 — System Tray Badge + Live Dropdown** (Small, ~80-100 lines) ✓ v4.18.0
  `QSystemTrayIcon` with QPainter badge overlay (live count). Context menu:
  live channels, active downloads (% progress), last 5 notifications, Quit.

- [x] **F40 — Pre-Download Duplicate Detection** (Small, ~50-60 lines) ✓ v4.18.0
  After fetch resolves metadata, compare (channel, title, duration) against
  `self._history`. Fuzzy title match (70% token overlap). Advisory warning dialog.

- [x] **F10 — Import/Export Monitor Channel Lists** (Small, ~80-100 lines) ✓ v4.18.0
  Export: `json.dumps(cfg["monitor_channels"])` to file picker.
  Import: read, validate, merge (skip duplicates by `channel_id`).

---

## Phase 2 — Download Core
> Strengthen the core download workflow. F1 (queue) is the centerpiece.
> Build supporting features first, then tackle the queue.

- [x] **F16 — Download Speed & ETA Dashboard** (Small-Med, ~80-100 lines) ✓ v4.19.0
  Speed label + ETA label in hero metrics row. Calculate from `_on_dl_progress()`
  byte deltas (5-sec smoothing window). Optional: 120x30 sparkline widget (60 samples).

- [x] **F21 — Pre-Download Time-Range Crop** (Small-Med, ~80-100 lines) ✓ v4.19.0
  Optional start/end HH:MM:SS inputs on Download tab (visible after fetch).
  Filter segment list to time range before handing to DownloadWorker.
  For yt-dlp: pass `--download-sections *start-end`.

- [x] **F3 — Keyword / Title Filters for Auto-Record** (Small, ~50-70 lines) ✓ v4.19.0
  Add `filter_keywords: str` to `MonitorEntry`. Text field in `monitor_entry_dialog.py`.
  Check in `_PollTask.run()` or auto-record trigger: skip if title doesn't match.
  Simple comma-separated keywords, case-insensitive substring match.

- [x] **F1 — Download Queue with Concurrent Downloads** (Large, ~300-400 lines) ✓ v4.19.0
  **This is the biggest single feature.** Implementation approach:
  1. Refactor `self.download_worker` from single instance to a worker pool (reuse
     the `_autorecord_workers` dict pattern, max 3 concurrent).
  2. Each queue item gets its own DownloadWorker + progress tracking.
  3. Queue table: per-row progress bar, pause/resume/cancel buttons, drag reorder.
  4. Global bandwidth sharing: divide `rate_limit` across active workers.
  5. FinalizeWorker stays single-threaded (sequential finalization is fine).
  6. Persist queue state in config for crash recovery.

- [x] **F17 — Deep Archive: VOD Listing Pagination** (Medium, ~100-150 lines) ✓ v4.19.0
  Add `offset`/`cursor` params to `list_vods()` in kick.py, twitch.py, ytdlp.py.
  "Load More" button below VOD table. Accumulate results across pages.

- [x] **F2 — Batch VOD Selection** (Medium, ~120-150 lines) ✓ v4.19.0
  *Benefits from F1 (queue) and F17 (pagination).*
  Enhance VOD list viewer: multi-select checkboxes, metadata columns (duration,
  date, views), "Download Selected" -> queue all with auto quality selection.

---

## Phase 3 — Trim, Clip & Post-Processing
> Make the editing pipeline powerful. Build low-level features first,
> then composite features that combine them.

- [x] **F34 — Audio Waveform in Trim Dialog** (Small, ~200 lines) ✓ v4.20.0
  `WaveformWidget(QWidget)` below filmstrip. FFmpeg extract PCM -> NumPy peaks
  -> QPainter fill. Cache as `.waveform.bin`. Sync cursor with filmstrip scrubber.
  Overlay silence regions as tinted bands.

- [x] **F26 — Silence / Dead Air Removal** (Small, ~80-100 lines) ✓ v4.20.0
  New PostProcessor preset `remove_silence`. FFmpeg `silencedetect` filter ->
  parse timestamps -> build concat segment list skipping silence -> re-mux.
  Expose as Settings checkbox + History right-click action.
  Config: noise floor slider (-20dB to -50dB), min silence duration.

- [x] **F7 — Post-Processing Presets** (Medium, ~150-200 lines) ✓ v4.20.0
  Named preset dicts in config. Settings UI: combo box + save/delete/rename.
  Ship 3 built-ins: "Archive Quality", "Quick Share", "Raw — No Processing".
  Per-channel preset selector in `monitor_entry_dialog.py`.
  Apply by overwriting PostProcessor snapshot at download start.

- [x] **F8 — Chat-Spike Clip Markers** (Medium, ~150-200 lines)
  Read `.chat.jsonl`, bucket messages by 10-sec windows, find spikes >2 std dev
  above rolling average. Render as colored tick markers on ClipDialog filmstrip.
  History context menu: "Show chat highlights" -> list of timestamps.

- [x] **F9 — Multi-Range Highlight Reel** (Medium, ~200-250 lines)
  *Pairs with F8 (chat markers suggest the ranges).*
  ClipDialog: multiple draggable range pairs on filmstrip. Range list panel with
  add/remove/reorder. ClipWorker: ffmpeg concat demuxer for multi-range export.
  Start with hard-cut (stream-copy). Add crossfade toggle later (requires re-encode).

- [x] **F30 — Scene Detection + Storyboard** (Medium, ~150-180 lines)
  Optional dep: `scenedetect`. New `postprocess/scene_worker.py` runs
  `ContentDetector`, extracts thumbnail per scene boundary, caches as sprite sheet.
  ClipDialog: scrollable storyboard panel below filmstrip (click to jump).
  History right-click: "Generate storyboard".

- [ ] **F31 — Social Clip Export (Vertical / Platform Presets)** (Medium, ~200-250 lines)
  *Extends ClipDialog from F9.*
  Platform dropdown: TikTok/Shorts/Reels/Twitter with auto-resolution.
  Draggable 9:16 crop rectangle on preview frame.
  FFmpeg crop/scale filter chain. Optional Whisper caption burn-in.

---

## Phase 4 — Notifications, Hooks & Integration
> External connections — make StreamKeep play well with other tools.

- [ ] **F4 — Persistent Notification Log Viewer** (Medium, ~120-150 lines)
  File-backed notification persistence (JSON lines in `notifications.jsonl`).
  New log viewer dialog: filterable by severity, searchable, exportable.
  Wire to existing `_notify_center()` call sites.

- [ ] **F15 — Webhook Expansion (Slack / Telegram / Ntfy)** (Medium, ~100-120 lines)
  URL pattern detection in `_send_webhook()`. Formatters per target:
  Slack (blocks), Telegram (`sendMessage` + markdown), ntfy (headers).
  Settings: webhook type auto-detected from URL, indicator label.

- [ ] **F24 — Event Hook System** (Small-Med, ~100-120 lines)
  New `hooks.py`: `fire_hook(event, context)` -> subprocess with env vars
  (`$SK_TITLE`, `$SK_CHANNEL`, `$SK_PATH`, etc.). Config: `hooks` dict
  (event_name -> command). Settings: event/command table with add/edit/remove.
  Events: download_start, download_complete, download_error, channel_live,
  auto_record_start, auto_record_end, transcode_complete.

- [ ] **F19 — Browser Extension: Context Menu + Multi-Tab** (Small, ~60-80 lines)
  `manifest.json`: add `contextMenus` permission.
  `background.js`: register "Send link to StreamKeep" context menu.
  `popup.js`: "Send all tabs" button -> `chrome.tabs.query` -> POST each URL.

- [ ] **F33 — Media Server Auto-Import** (Small-Med, ~120-150 lines)
  New `integrations/media_server.py`. Plex: `GET /library/sections/{id}/refresh`.
  Jellyfin/Emby: `POST /Library/Refresh`. File copy/hardlink with naming template.
  Reuse NFO writer from `metadata.py`. Settings: server type/URL/token/library path.
  Wire to `_on_all_done()`.

---

## Phase 5 — Monitor Intelligence
> Make auto-record smarter. These features build on the existing
> ChannelMonitor and MonitorEntry infrastructure.

- [ ] **F18 — Per-Download Settings Override** (Medium, ~120-150 lines)
  Collapsible "Advanced" QFrame in Download tab. Override widgets: quality combo,
  post-process preset (from F7), rate limit, template. Merge into worker context
  on `_on_download()`. Clear after download starts. Badge when overrides active.

- [ ] **F25 — Quality Auto-Upgrade** (Medium, ~120-150 lines)
  *Requires F3 (keyword filters) to be in place for the MonitorEntry pattern.*
  After VOD detection in monitor, compare quality to existing `metadata.json`.
  If better by threshold, queue re-download. `MonitorEntry` gets `auto_upgrade: bool`,
  `min_upgrade_quality: str`. Atomic swap: download new, verify, recycle old.

- [ ] **F32 — Auto-Cleanup Lifecycle Policies** (Medium, ~150-200 lines)
  *Benefits from F38 (watch status) for "delete after watched" rules.*
  New `lifecycle.py`. Rules: max_days, delete_watched, max_total_gb, favorites exempt.
  Per-channel overrides. Cleanup preview dialog (table + size totals).
  Always `send2trash`, never permanent delete. Wire to post-auto-record path.

- [ ] **F39 — Stream Schedule Calendar** (Medium, ~300-400 lines)
  New `schedule.py`: Twitch Schedule API fetch + cache.
  New `ui/calendar_widget.py`: week-view grid (day columns, hour rows, colored blocks).
  Monitor tab toggle: List | Calendar. Block click -> stream details + auto-record button.
  Timer checks scheduled starts +-5 min -> triggers auto-record.

---

## Phase 6 — Library & Organization
> Transform History from a download log into a browsable media library.
> F38 (watch status) enables F32 (lifecycle cleanup) from Phase 5.

- [ ] **F38 — Watch Queue + Playback Progress** (Small-Med, ~150-200 lines)
  `HistoryEntry` gets `watched`, `watch_position_secs`, `bookmarks` fields.
  History tab: progress bar overlay on thumbnails, "Continue Watching" section,
  right-click "Mark as watched/unwatched", "Add bookmark" dialog.
  **Implement this before F32** so lifecycle policies can use watch status.

- [ ] **F13 — Storage Filtering & Analytics** (Medium, ~150-200 lines)
  Filter combos (Platform, Channel) above Storage table. Date range picker.
  Sparkline: persist daily size snapshots in config (rolling 90 days),
  render as QPainter polyline. Client-side filtering on cached scan results.

- [ ] **F35 — Tags & Smart Collections** (Medium, ~300-400 lines)
  SQLite for tags (many-to-many). Auto-tag on download (channel, platform,
  game/category, resolution, duration bucket). Tag chips on History rows.
  Filter sidebar with clickable tags. Smart collection editor (rule builder).

- [ ] **F36 — Batch Rename Studio** (Small-Med, ~200-250 lines)
  New `ui/rename_dialog.py`. Template input with token autocomplete.
  Live preview table (old name | new name). Conflict detection (red highlight).
  Read `metadata.json` for token values. Undo log to disk.
  Update `HistoryEntry.path` after rename.

- [ ] **F27 — Full-Text Transcript Search** (Medium, ~150-180 lines)
  *Benefits from F29 (WhisperX) for word-level timestamps.*
  SQLite FTS5 index built on transcript generation. History tab: "Search transcripts"
  toggle on search bar. Results: text snippet + timestamp + recording title.
  Double-click -> ClipDialog at timestamp. Index build runs async.

---

## Phase 7 — Advanced & Ambitious
> Larger features that add differentiated capabilities.
> Each is independent and can be tackled in any order.

- [ ] **F29 — WhisperX Upgrade** (Medium, ~120-150 lines)
  WhisperX as preferred backend in `transcribe_worker.py` (fall back to faster-whisper).
  Word-level timestamps via wav2vec2 forced alignment.
  Speaker diarization via pyannote-audio (opt-in, needs HuggingFace token).
  New `.transcript.json` schema: `{word, start, end, speaker}`.
  Settings: "Enable speaker diarization" toggle.

- [ ] **F22 — Chat-to-Video Render Engine** (Large, ~400-500 lines)
  New `postprocess/chat_render_worker.py`. Parse `.chat.jsonl`, fetch emote images
  (BTTV/FFZ/7TV APIs with local cache), render frames via Pillow or FFmpeg
  drawtext filter chain, pipe to ffmpeg for video encoding.
  History/Storage right-click: "Render chat overlay".
  Settings: width, font size, message duration, background opacity.
  Offer "preview first 60s" before full render.

- [ ] **F23 — Deleted VOD Recovery Wizard** (Medium, ~200-250 lines)
  New `extractors/twitch_recover.py`. Scrape TwitchTracker for stream IDs
  given channel + date range. Construct CDN URL candidates. Test with HEAD.
  Return valid URLs as StreamInfo. Download tab: "Recover VOD" action ->
  small dialog (channel name + date range). Maintenance note: CDN patterns
  rotate; this feature needs periodic updates.

- [ ] **F37 — REST API + Web Remote UI** (Medium, ~300-400 lines server + ~400-500 lines HTML)
  Extend `local_server.py`: endpoints for status, queue CRUD, library search,
  monitor status. Serve single-page responsive HTML at `/`.
  Vanilla JS + CSS, no framework. Token auth on all endpoints.
  LAN-only by default; opt-in 0.0.0.0 binding with warning.

- [ ] **F20 — Dark / Light Theme Toggle** (Medium, ~100-120 lines)
  `theme.py`: `CAT_LATTE` dict + `build_stylesheet(palette)` function.
  Settings: theme combo (Dark / Light / System). Instant apply via
  `app.setStyleSheet()`. Audit `main_window.py` for hardcoded colors.
  Test every tab in both themes.

---

## Dependency Graph (cross-feature)

```
F17 (Pagination) ──> F2 (Batch VOD) ──> F1 (Queue) enables full batch flow
F8 (Chat markers) ──> F9 (Multi-range highlight reel)
F9 (Multi-range) ──> F31 (Social clip export) extends the dialog
F7 (Presets) ──> F18 (Per-download override) uses preset picker
F38 (Watch status) ──> F32 (Lifecycle cleanup) uses "delete after watched"
F29 (WhisperX) ──> F27 (Transcript search) benefits from word-level timestamps
F34 (Waveform) ──> improves F9 (Multi-range) and F31 (Social export) editing UX
F30 (Scene detect) ──> F8 (Chat markers) can combine signals for better clip detection
F4 (Notification log) ──> F15 (Webhook expansion) more targets for same events
F24 (Event hooks) ──> F33 (Media server) can be wired via hooks as alternative
```

---

## Version Plan

| Phase | Target Version | Features | Theme |
|-------|---------------|----------|-------|
| 1 | v4.18.0 | F6, F11, F12, F14, F5, F28, F40, F10 | Quick wins |
| 2 | v4.19.0 | F16, F21, F3, F1, F17, F2 | Download core |
| 3 | v4.20.0 | F34, F26, F7, F8, F9, F30, F31 | Editing pipeline |
| 4 | v4.21.0 | F4, F15, F24, F19, F33 | Integration |
| 5 | v4.22.0 | F18, F25, F32, F39 | Monitor intelligence |
| 6 | v4.23.0 | F38, F13, F35, F36, F27 | Library |
| 7 | v4.24.0 | F29, F22, F23, F37, F20 | Advanced |

---

## Session Resumption Notes

When resuming in a new conversation:

1. Read `ROADMAP.md` (this file) to see what's done and what's next.
2. Read `CLAUDE.md` for project conventions and architecture.
3. Read `features.md` for the full spec of the feature you're implementing.
4. Check `git log --oneline -5` to see what was last shipped.
5. The user prefers autonomous implementation — don't ask for confirmation,
   just implement, test, version bump, and commit.
6. Each feature should be a single commit with message format:
   `v{X.Y.Z} - {Feature title} (F{N})`
7. Always sign release APKs — wait, this is a Python desktop app.
   For this project: always run `py_compile` + `pyflakes` clean before committing.
