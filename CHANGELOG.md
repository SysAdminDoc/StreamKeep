# Changelog

All notable changes to StreamKeep are recorded here. This file and `README.md` are the tracked root Markdown files; the planning documents (`ROADMAP.md`, `RESEARCH.md`, `Roadmap_Blocked.md`, `CLAUDE.md`, `AGENTS.md`) are gitignored working notes. Corrected 2026-08-07: this file previously claimed it was itself ignored, which it never has been.

## Unreleased

- Queue and custom-preset removals, failed-operation discards, storage
  recycling, low-space warnings, and maintenance applies now follow the same
  immediate-action policy. Reversible queue, preset, and failure changes expose
  Undo through Notifications (and failure discard through the web remote),
  while irreversible history and credential deletion still require review.

- Empty Operations and VOD results, archive Analytics and Storage, and the
  data-driven Settings tables now use the shared empty-state card with a clear
  next action instead of showing unexplained zero metrics or blank grids.

- Batch Rename Studio now exposes a durable **Undo last rename** action in
  History and the notification menu. Undo restores every available directory,
  keeps failed conflicts queued for retry, updates History and manifest paths,
  and reports partial or journal failures instead of swallowing them.

- Compiled Qt catalogs are now generated during release validation and builds
  instead of being versioned binary artifacts, and ruff now gates product,
  packaging, and test code with intentional late-import exceptions scoped to
  the bootstrap/facade modules and extractor test harness that require them.

- The browser remote now clears stale loading/error status after recovery,
  reports library and monitor failures inside their panels, releases every
  `aria-busy` state on failure, and distinguishes an offline browser from an
  idle connection.

- Automatic-retention settings now consistently say "Recycle," explain that
  files remain recoverable through the Recycle Bin, and avoid implying that
  unattended cleanup permanently deletes recordings.

- Twitch and Kick chat capture now rejects unterminated or oversized transport
  input under explicit memory bounds, including fragmented WebSocket messages,
  and reports the endpoint failure through the existing capture log.

- Re-adding an existing collection member without an explicit position now
  preserves its curated order; callers can still move it by supplying a new
  position.

- Release artifact smoke checks now exercise the real bounded startup work:
  scheduler application, resume discovery, transcript indexing, companion
  configuration, and health probing report independent outcomes. Network
  update checks and tray UI remain explicitly suppressed in the headless
  contract, and a failed or unbounded subsystem now fails the release gate.

- Background storage, Settings, health, and clip workers now share an
  indeterminate busy indicator with an accessible live status, so slow work
  remains visibly active until it succeeds, fails, or is cancelled.

- Settings now has a searchable section rail: labels, help text, and setting
  keys filter the large form to matching controls, and clearing the search
  restores every setting.

- The local companion server now keeps a bounded, rotating JSONL request log
  with method, route, status, byte count, and duration while excluding bearer
  tokens, query values, and signed URLs.

- Theme changes now rebuild palette-driven widget surfaces through the central
  stylesheet, including the shell, clip editor, queue/status indicators, and
  player canvas, so open windows no longer retain colors from the previous
  theme.

- YouTube diagnostics now compare the installed yt-dlp build with the newest
  known release, show release age and stale warnings in Settings and
  `youtube-health`, and point to an external executable without downloading or
  installing anything; offline checks remain `unknown`.

- Companion-token CLI management no longer accepts master bearer tokens in
  argv. It supports the secure desktop configuration, `STREAMKEEP_COMPANION_TOKEN`,
  or one-line stdin input, and the release gate now checks the README's explicit
  loopback, constant-time, and master-token security claims against source.

- Raw-capture salvage now runs in an owned, cancellable background worker with
  per-capture progress and visible failure/cancellation notifications, so
  FFmpeg recovery no longer blocks the History window.

The seven desktop routes now share one archive-control-room visual system.

- Download, Monitor, History, Analytics, Storage, Settings, and Operations use
  raised ink-and-slate cards, a consistent blue/mint/amber state language,
  roomier shell spacing, and clearer primary versus secondary actions.
- Metric summaries are responsive cards instead of flat inline labels. Settings
  groups, filters, tables, and the Download work surface use the same hierarchy.
- Analytics has responsive grid-backed bars, a labelled platform ring, and
  polished ranked-channel tracks instead of the previous primitive plots.
- History and Storage show lightweight code-rendered preview art while a real
  local thumbnail loads, including an explicit missing-file cue.
- Storage keeps integrity scheduling and re-template controls available behind
  compact reveal actions, leaving the recording table visible at normal window
  sizes.
- Operations now loads its durable filters correctly, highlights state and
  failure rows without relying on colour alone, and presents its table in the
  same data-pane treatment as the rest of the app.
- The Windows bundle now includes the local web UI asset required by packaged
  startup and artifact-smoke checks.
- Qt test teardown now retires top-level widgets after each test, eliminating
  the V227 Windows access violation; the reproducer passed 10 consecutive
  times and the full suite completed with 2,223 passed and 1 skipped.
- Ordered rules are schema-validated at import and now apply consistently to
  desktop direct downloads, GUI queue enqueues, and the headless queue, with
  priority/hold metadata preserved for durable resume.
- History exposes portable recording-note sidecars through its context menu and
  global search, while Monitor now records live/offline transitions through the
  shared database facade and renders Channel Insights summaries.
- Upload destinations are now manageable end to end: CLI profiles can be
  created, listed, edited, connection-tested, and deleted with secure
  credentials removed from the OS store; Settings exposes the selected upload
  profile and post-import queue toggle, and the REST surface can delete a
  profile too.
- Native HLS validation now resolves RFC 8216bis `EXT-X-DEFINE` NAME/VALUE,
  IMPORT, and QUERYPARAM variables across recursive master/media playlists,
  rejects undefined references with named errors, records playlist version and
  VOD/EVENT metadata, and refuses protocol versions above the supported floor.
- Native DASH parsing now expands `SegmentTimeline` repeats and URL templates,
  preserves `SegmentList`/`SegmentBase` byte-range metadata, carries the MPD's
  `minimumUpdatePeriod`, and sends safe `Range` headers for selected
  single-file representations.
- Podcasting 2.0 archival now ranks guarded `alternateEnclosure` deliveries,
  fails over across declared sources, preserves and verifies publisher
  integrity declarations, renders locked/source metadata in published feeds,
  and turns pending `liveItem` entries into scheduled queue captures.
- Extraction failures now use an ordered, data-driven taxonomy for bot-check,
  rate-limited, geo-blocked, members-only, and genuinely-gone conditions.
  Host backoff carries the class and honors long `Retry-After` directives;
  standing health rows name the affected platform, while Settings shows cookie
  jar provenance/freshness and provides an explicit browser refresh action.
- Active foreground downloads, queue jobs, and auto-record captures now publish
  a readable Windows power request, keep the system awake for long captures,
  block OS shutdown with the active-work reason, and release every request on
  completion or app close.
- Semantic moments now use local `all-MiniLM-L6-v2` sentence embeddings when
  the optional bundle is available, and fuse vector and transcript FTS5 ranks
  with reciprocal-rank fusion so paraphrases can match without shared words.
  The derived vector table is a bounded rebuildable cache; source checkouts
  without the optional runtime retain a clearly labelled hashed fallback.
- Monitor schedule refresh now runs through a Qt worker, so cached Twitch
  schedules, log messages, errors, and the calendar's refreshing state return
  reliably to the GUI thread.
- DASH and HLS parser URL resolution now routes manifest-derived targets
  through the shared remote URL policy, rejecting unsafe schemes and private
  addresses before they enter the playable model.
- Queue position allocation now runs inside SQLite write transactions and a
  schema migration normalizes legacy duplicates before enforcing uniqueness.
  Queue snapshots, deletes, cancellations, publishing updates, failed-job
  resolution, and config migration now share the same cross-process safety.
- Media-server requests now validate configured URLs with the private-LAN
  policy before constructing token-bearing Plex, Emby, or Jellyfin requests.
- Windows command exports now track `list2cmdline` backslash-quote rules, so
  embedded quotes cannot hide later shell metacharacters from caret escaping.
- Pooled SQLite leases now defer transaction commit and rollback to the
  outermost context, preventing nested helpers from partially committing work.

## [4.57.0] - 2026-08-08

Failures now surface where you are looking, and keyboard focus is visible again.

- Transient messages appear in the window itself. The OS notification and the
  tray balloon deliberately stay quiet while StreamKeep is focused, so as not to
  interrupt you — but nothing replaced them, which meant a user watching the app
  was the user least likely to learn that anything had failed.
- Three History actions that returned silently now say why they cannot proceed:
  **Show chat highlights**, **Storyboard** and **Transcribe** each name the
  component that is missing instead of appearing to do nothing when clicked.
- Global search distinguishes a broken transcript index from an empty archive.
  It previously reported "No results found." for both.
- Storage cleanup names the folders it could not recycle and why. It reported
  "Recycled 3 of 5" without saying which two failed.
- Exporting the Operations view reports a failure in the window. Its status label
  sits at the bottom of a long scroll page and was usually off-screen.
- Clearing your notification history states how many entries it cleared and
  offers **Undo clear** in the bell menu. It was previously silent and permanent.
- The URL field now shows a visible focus indicator. It had none at all, which
  made keyboard navigation of the app's primary control invisible.
- A selected row in the search results is distinguishable from a hovered one.
  Both used the same background.
- The navigation rail's focus ring meets the contrast minimum for a UI component
  in every theme; in the light theme it previously measured 2.91:1 against a
  required 3:1.
- Global search no longer disappears in a narrow window. Below the width where
  the field is hidden it collapses to a labelled button that reveals it, so the
  control stays reachable at every supported window size.

## [4.56.0] - 2026-08-08

This release is about failures that used to happen quietly.

- A Settings save no longer erases the media-server keys the form has no controls
  for. The dialog rebuilt that whole config section from its widgets and returned
  13 of the 15 keys the schema allows, so `upload_profile_id`,
  `upload_after_import` and `sidecar_profile` — set through the REST API or a
  config import — were deleted every time anyone touched Settings.
- Automatic recording that cannot create its output folder, retention that cannot
  reach the Recycle Bin, and chat capture that will not start now say so instead
  of leaving the monitor reporting normal operation. Each raises a standing
  condition that clears on its own once the same work succeeds, because a health
  probe cannot rediscover a go-live that already came and went.
- A crash inside any background worker is now written to the crash log and shown.
  Previously only the main thread was covered, which in an app built from 41
  worker threads and shipped without a console meant a failing download, capture,
  transcription or backup left no trace anywhere.
- A download queue that cannot resume reports it. Four callers resumed the queue
  after a power, disk or Settings change and all four swallowed the failure; two
  of them had already announced "resuming queue" first.
- Crash recovery says when it fails. The three routines that repair a
  half-completed restore, rebuild or re-template were silenced, so the app
  continued against a mixed config directory with nothing to indicate why.
- Content summaries sent to a cloud provider are refused unless the endpoint is
  `https`, and every provider answer is read under a size limit. An `http://`
  base URL previously sent the API key in cleartext.
- Exporting a clip while its preview, waveform or scene detection is still
  running no longer risks taking the app down.
- A collection export will not overwrite a `.strm` file it did not write, and a
  re-export that can hardlink now removes the pointer an earlier export left
  behind instead of listing one recording twice.
- Transcribing non-English speech no longer fails on the text it produced. Text
  read back from ffmpeg, yt-dlp and the transcription engines was being decoded
  with the system locale's encoding, which raises on anything outside cp1252 on
  Windows; the same default was corrupting non-Latin filenames handed to ffmpeg.
- The media-server API and the deleted-VOD recovery page are read under a size
  cap rather than without limit.
- gallery-dl and faster-whisper now declare a supported version floor like every
  other engine, so an outdated install is named rather than silently used.

## [4.55.0] - 2026-08-07

- The download-queue and executor-lease family now owns its own code, which was
  the largest remaining piece of the database monolith. `db/queue.py` had been a
  pure forwarding shim; it now holds the 18 functions it always declared. The
  monolith is down to 4,013 lines from 6,737 at the start of this work.

## [4.54.0] - 2026-08-07

- A recording can now belong to as many collections as you like while still
  existing once on disk. The season-folder layout gave every recording exactly
  one home, so anything belonging to two playlists had to be duplicated or
  arbitrarily assigned to one of them. Membership is explicit and ordered, and
  adding a recording to a collection never removes it from another.
  On export the season layout stays exactly as it was — that remains the single
  real file — and each collection gets an additional entry under
  `Collections/<name>/` pointing at those same bytes: a hardlink where the
  filesystem allows one, and a `.strm` pointer (which Plex, Jellyfin, Emby and
  Kodi all follow) when it does not. **Copying is deliberately not a fallback**,
  because duplicating the bytes is the problem this removes; a home that can be
  neither linked nor pointed at is reported as refused. The export result now
  carries the strategy used for every home, including whether the primary copy
  was hardlinked, so the choice is visible rather than buried in a log line.
  Manage it with `StreamKeep.py collections list|show|of|add|remove|delete`.
  Memberships follow a recording that moves on disk.

- The monitor-channel table family now owns its own code, and the lock that
  serialises every database write moved to a shared leaf module so it stays a
  single object. Two locks would have serialised nothing while every write still
  appeared to succeed, so that invariant is now asserted directly. The database
  monolith is down to 4,785 lines from 6,737 at the start of this work.

## [4.53.0] - 2026-08-07

- Deleted-VOD recovery now says what happened instead of just failing. Every
  candidate CDN domain is probed and reported individually, because Twitch
  rotates those domains — so "nothing found" without a per-domain answer cannot
  tell a stale domain list from a VOD that is genuinely gone. Each outcome is
  named: served, not on this domain, unreachable, or gated.
  **Recovery now refuses a VOD the platform is gating.** It reconstructs URLs for
  segments the CDN still serves unauthenticated; a 401 or 403 means it does not,
  and the attempt stops there with the reason stated rather than trying the next
  domain, the next quality, and the other timestamp guesses until something
  answers. Working around an access control is not what this feature is for.
  Separately, a stream whose date could not be parsed used to be skipped in
  complete silence — the fallback scraper emits dates with seconds and ISO
  separators that the parser did not accept, producing no timestamps and so no
  probes at all. Those formats are now understood, and a date that genuinely
  cannot be read is reported instead of vanishing.

- Declarative source adapters now travel with a backup, so moving a profile to
  another machine no longer silently leaves your custom site definitions behind.
  They arrive **inert**: a restore strips the per-adapter contract approvals, so
  every definition is review-required on the machine it lands on. Carrying the
  approvals would have turned a backup file into a way to enable a third-party
  request description that nobody reviewed there — and because the stripping
  happens on restore rather than on create, backups made before this change are
  covered too. Plugins remain excluded, and the reason is now stated in the
  restore report: they are executable Python, and no review gate makes arbitrary
  code safe the way it can a data-only definition. Every exclusion is reported,
  so a missing directory is never something you discover later.

- The browser companion's security boundary is now a module you can read end to
  end. Bearer-token minting and validation, the scope vocabulary, the pairing
  code exchange, and the nonce replay ledger moved out of the 2,748-line request
  handler into `streamkeep/server/auth.py`, which previously only forwarded to
  it. Behaviour is unchanged.

- Fixed the same patch-reach defect in the companion server facade that the
  database facade had: once the auth layer owned its own definitions, patching a
  name on `streamkeep.local_server` reached the legacy module but not the module
  where the code actually lives. Reading the name back returned the patch either
  way, so nothing could see the gap.

- Host and origin validation — which `Host` header and which browser `Origin`
  the companion will accept, a security decision rather than a formatting one —
  now lives in `streamkeep/server/origins.py`, and the remote UI's language
  selection and template rendering moved next to the asset loader that already
  served it. The companion's request-handler module is down to 2,340 lines from
  2,748 at the start of this work. Behaviour is unchanged.

## [4.52.0] - 2026-08-07

- Fixed a release-metadata bug that had been deleting a release from the Linux
  package changelog on every version bump. The AppStream release list was
  stamped by rewriting the newest entry's version number in place, which left
  the description belonging to it untouched — so the entry began describing the
  previous release's work and that release vanished from the history. Nothing
  reported it because the consistency check performed the same rewrite and
  treated a successful rewrite as agreement. The release list is now verified
  rather than stamped: a bump fails loudly unless a new entry has been written
  for it. v4.51.0's entry, lost this way, is restored.

- Stopping a health probe no longer requires killing the thread it runs on. The
  probe polls for cancellation between its individual checks, so asking it to
  stop takes effect part-way through instead of only before and after the whole
  run. Previously the only way to stop one in flight was to terminate its
  thread, and terminating a thread sitting in a subprocess call is the same
  undefined behaviour that produced the crash above — just rarer. A cancelled
  probe now returns nothing and persists nothing, rather than writing a snapshot
  built from a half-finished scan that would report working dependencies as
  missing.

- The yt-dlp update channel is now a setting. StreamKeep still ships a frozen,
  version-checked yt-dlp and that stays the default, but a stable release
  cadence cannot track YouTube breakage — there was a 12-week gap between
  2026.03.17 and 2026.06.09 while YouTube broke repeatedly — so an operator can
  point at their own build and follow nightly. The build you name is
  version-probed exactly like any other tool rather than trusted, so one below
  the supported floor is refused by name and can never reach a download path.
  An external build that cannot be used falls back to the bundled one instead
  of taking downloads down over a settings typo, and the health panel reports
  the channel actually **in use** with the reason the request was refused —
  reporting the request instead would let you believe you were getting nightly
  extractor fixes you were not. Switching channels takes effect immediately and
  is reversible.

- Fixed the intermittent test-suite abort tracked as V179, which turned out to
  be a real threading defect rather than a harness quirk. A background health
  probe runs about ten executable version checks at a five-second timeout each;
  its `cancel()` is only observed before and after that work, and `quit()` does
  nothing to a thread that runs no event loop. On a starved CPU the probe was
  still inside a subprocess call when the window that owned it went away, and
  tearing down around a live thread aborted the process with an access
  violation and no summary. Load was never the bug — only what made the probe
  slow enough to outlive its parent.

- Fixed a defect the database decomposition introduced silently: patching a
  value on the `streamkeep.db` facade stopped reaching the code that uses it.
  While the package was one module a patch rebound the single binding every
  caller resolved; once definitions moved into domain modules a moved name had
  several bindings, and the facade wrote to whichever it found first. Reading
  the patched name back through the facade still returned the patch, so the
  assertions guarding this passed while the code under test went on calling the
  real function. A patch now reaches every module holding the name — but never
  a shim that only forwards, because writing a binding into one would freeze
  that forward. This matters most for `DB_PATH`, which 175 test sites use to
  redirect the library away from the operator's own: under the old behaviour
  that redirect stopped short of the code that opens the file.

- A fourth pass of the database decomposition. The connection layer — which
  database file is open, the per-thread pooled handle for the active profile,
  and the refusal to open a database written by a newer build — now lives in
  `streamkeep/db/connection.py` instead of sharing a module with the ~4,900
  lines that merely acquire a connection. `_legacy.py` is down to 4,904 lines
  from 5,107.

## [4.51.0] - 2026-08-07

- Channel backfill now fetches whatever is closest to being deleted, not
  whatever is newest. Kick keeps a replay 7 days unverified and 30 verified,
  and only 16 or 30 at a time, so working newest-first spent that budget
  backwards: the newest item had the whole window left while the oldest might
  have hours, and a long queue lost the oldest reachable VOD permanently. The
  queue row states why an item was ordered where it was, including how much of
  the window is left. Only platforms with a documented retention window are
  reordered — guessing a policy and rearranging someone's queue on it would be
  worse than doing nothing — and items with no usable publication date keep
  their original position rather than displacing one whose urgency is known.
  `backfill_oldest_first` turns it off.

- A third pass of the database decomposition. The history action log gained the
  replay, compaction and row-deletion work that maintains it; the user
  tombstone ledger became its own module; published-recording identifiers,
  full-text-search configuration, and two time helpers moved to the modules
  that should own them. `db/_legacy.py` is down from 5,477 to 5,107 lines
  (6,737 before this work began). `publishing.py` was a pure forwarding shim
  and now owns definitions, so its import of the legacy module became lazy —
  the boundary test's cycle guard covers it and every other domain module.

## [4.50.0] - 2026-08-07

- Translation coverage is visible where the choice is made. Spanish sits near
  15% translated, and the beta caveat lived only in the README — so switching
  language produced a mostly-English UI with nothing to explain it. The
  language selector now states each catalog's translated percentage and marks
  anything below 85% as beta, with the exact message counts in its tooltip.
  The release gate reports coverage too, so a catalog regressing is visible
  before a release rather than after a user switches. Coverage is reported,
  not gated: it falls legitimately whenever UI strings are added faster than
  they are translated.

- The database decomposition continued. `schema.py` now owns the migrations it
  had only been dispatching, so reviewing the schema no longer means walking
  the legacy module; the history action log and two shared leaf primitives also
  became modules that implement what they export. `db/_legacy.py` is down from
  6,461 to 5,477 lines (6,737 before this work started). The facade now
  composes the domain modules instead of requiring every name to be re-exported
  through the legacy one, and a new boundary test fails if any domain module
  imports the connection-owning module at module scope — that acyclic property
  is what lets them own their statements at all.

## [4.49.0] - 2026-08-07

- The database "split" started actually splitting. The 2026-08-04 change
  created facades over an unchanged 6,700-line module, and the boundary test
  asserted the facade forwarded the whole legacy surface — which made the
  monolith a tested contract rather than something to decompose. The row and
  view projections (14 functions, ~276 lines) now live in `db/projections.py`
  and are implemented there rather than re-exported, and the boundary test
  asserts ownership by `__module__` instead of identity with the facade, so a
  module cannot decay back into a shim. A line ratchet keeps `_legacy.py` from
  regrowing. The facade still serves every caller and stays patch-compatible.

- Bulk archiving now backs off on its own instead of needing hand-tuned
  limits. When a host answers with 429 or another throttle, StreamKeep halves
  how many jobs it will run against *that host* and spaces its requests out,
  then gives the capacity back after a run of clean transfers — multiplicative
  decrease, additive increase, because a throttle means you are already over
  the line while a run of successes only means you are not over it yet. A
  `Retry-After` header is honoured over the computed delay when it asks for
  longer, and never used to shorten a backoff already earned. The reaction is
  queue-wide but per host, so slowing down for one strict site leaves every
  other source at full speed, and a host nobody has upset for half an hour
  recovers without any traffic at all. Settings shows which hosts are being
  backed off from and can turn the whole thing off.

- A live capture that is killed now leaves a playable recording. An MP4 keeps
  its index in a `moov` atom the muxer writes when the file is *closed*, so a
  capture ended by a crash or a power loss produced a file with every captured
  byte on disk and none of it readable — measured against ffmpeg 8.1.2 by
  killing an 8-second capture, the file held 1,048,624 bytes and ffprobe
  reported "moov atom not found". Unbounded live captures into MP4-family
  containers are now written fragmented and flushed per packet, so the file is
  playable at whatever point it is cut; the same kill now plays back 7.5 of its
  8 seconds. Matroska and MPEG-TS already wrote their structure as they went
  and are untouched, and chunked capture already bounded the loss to the
  segment in flight, so it is deliberately unchanged.

- The final mux is no longer the step the whole capture depends on. A capture
  that ends cleanly is repacked into a plain, seekable file, but its input is
  already a complete recording and the result is swapped in only once it
  exists — so interrupting the repack, or running it twice, costs nothing worse
  than leaving the recording in its fragmented form, which still plays.

## [4.48.0] - 2026-08-07

- The broad-exception guardrail now measures error visibility rather than
  annotation compliance. It was satisfied by any `#` on or above the `pass`,
  and 150 of the 174 sites had converged on one identical sentence — a reason
  repeated 150 times says nothing about any of them. Rewriting all of them in
  bulk would only have produced 150 plausible sentences nobody verified, so the
  boilerplate is frozen instead: a per-file budget that may only fall, and any
  new or moved site must state a reason of its own and of real length. Three
  sites were converted as the first draw-down, including one in the fetch
  worker that was hiding a logic failure — a failure to apply captured replay
  headers left the extractor resolving as an anonymous client, and the download
  then failed somewhere that pointed nowhere near the cause.

- A broken extractor now reads as a broken source rather than a broken app.
  A platform that fails repeatedly raises a standing health condition naming
  the platform *and* the engine that failed, and offering only the alternate
  engines this machine can actually run. Settings > Sources switches one
  platform to a different engine in a click; the choice overrides the global
  live-engine switches for that platform only and is ignored if it names an
  engine that is not installed, rather than being honoured into a guaranteed
  failure. The failure ledger records which engine each circuit's failures came
  from (schema v23).

- An edit to a source adapter that leaves the file the same size is no longer
  missed. The registry cache keyed on name, size, and mtime, and Windows file
  timestamps advance on a coarse tick, so swapping a host for another of equal
  length could reproduce the previous signature exactly and the edit would
  silently do nothing. Definitions now contribute a content digest.

- AI-upscaled video is no longer selectable by accident. Platforms publish an
  AI super-resolution rendition alongside the real one, and because it reports
  a taller resolution it wins any plain "best video" pick — so the archive
  quietly stored a synthesised copy of the thing it was meant to preserve.
  There is no format-sort field for it, so the preference is expressed as a
  format-selection filter applied to the video branches of the expression;
  audio branches and explicit format ids are left alone. `Allow AI-upscaled
  video` in the Advanced panel and `--allow-synthesised-tracks` on the CLI opt
  back in, and the choice travels with the job through the queue, resume
  sidecar, and restore path. The metadata sidecar now also records the stored
  track's `language` alongside its `format_note`.

- The intermittent native crash at the end of a test run is fixed. Roughly one
  full run in five died with `Windows fatal exception: access violation` after
  every test had already passed, which on a release gate reads as a code
  regression rather than a harness fault. pytest finalises session-scoped
  fixtures inside the last item's teardown, and the Qt fixture's local was the
  only reference to the `QApplication` — so the application was destroyed first
  and Qt then tore down still-live widgets underneath a dead application. The
  application is now held for the whole session, and leftover widgets and
  threads are retired while it is still alive. Against a two-module reproducer
  that crashed 7 runs in 10, the fault is gone.

## [4.47.0] - 2026-08-06

- Format-sort presets no longer rank a synthesised audio track above the
  original. yt-dlp *prepends* `-S` fields to its own default order rather than
  merging into it, so a preset's fields are compared before the default
  language preference is ever consulted — under "smallest" a platform's
  AI-dubbed rendition won on file size alone, and the archive quietly stored a
  synthesised track in place of the thing it was meant to preserve. Every
  preset now leads with `lang`, which reads the extractor's original-versus-dub
  preference, and still expresses its own intent after it.

- Repository hygiene restored. v4.45.0 and v4.46.0 shipped untagged, so the
  WinGet manifest's installer URL pointed at releases git had no ref for; both
  are now tagged. The tracked file set also matches the repo's own stated
  policy again: `README.md` and `CHANGELOG.md` ship with the repo, while
  `ROADMAP.md` and `RESEARCH.md` are planning state and no longer tracked.
  Three files the policy bans outright (`COMPLETED.md`, `RESEARCH_REPORT.md`,
  `ROADMAP-COMPLETED.md`) are gone; their substance is in this file and the
  repo's working notes.

- A queue-complete "sleep" now sleeps instead of hibernating. The action ran
  `rundll32 powrprof.dll,SetSuspendState 0,1,0`, but that entry point ignores
  its command line entirely — the hibernate flag never arrived, so any machine
  with hibernation enabled hibernated when the user asked for sleep, with the
  slow resume and full-memory disk write that implies. Windows sleep is now a
  direct `SetSuspendState` call with the flag actually passed, leaving
  applications their veto and scheduled wake intact. Every other action, and
  every other platform, still builds a plain command line.

- Asking what runtimes are installed no longer crashes on an unsupported
  architecture. A pinned Deno asset exists for five host triples, and on
  anything else `host_target` raised straight out of `get_runtime_capabilities`
  — so a niche architecture could not read its own capability registry at all,
  rather than simply being told the runtime is missing. It is now reported
  like any other absent runtime, and the repair text points at installing a
  runtime on PATH instead of at the managed installer, which has no asset for
  that host and would fail.

- The selected toggle is legible in the light theme, and stays legible with a
  custom accent. `QPushButton#toggleAccent:checked` rendered the accent on a
  light surface at 4.40:1 — below WCAG AA, in a *selected* state, which is
  exactly where the label most needs to be read. The colour is now computed
  against its own background rather than taken from the palette, which matters
  because the accent is user-supplied: a value chosen to pass with the default
  says nothing about the one the operator actually set. The visual-system test
  now walks the rendered stylesheet and checks every rule block that pairs a
  foreground with a background (excluding `:disabled`), instead of a fixed
  token list that could not see the pairing this rule introduced.

- Declarative adapters survive a hostile or malformed HTML response. The
  walker and the text extractor both recursed, so a deeply nested body raised
  `RecursionError` — a type the request path does not handle, so it escaped
  the adapter as a crash rather than a refused response. Both are now
  iterative, parsing stops descending past a depth cap (elements past it are
  still recorded, so no text or attribute is lost), and selector matching
  dedupes candidates per token. That last one mattered most: descendant
  combinators overlap, so a node beneath two matched ancestors was collected
  once per ancestor and every additional token multiplied both the result set
  and the work the next token had to redo. Text-extraction order is
  deliberately unchanged.

- The translation sidecar writer no longer closes its temporary file twice.
  `os.fdopen` takes ownership of the descriptor, so a failure after the write
  — a denied `os.replace` being the realistic one — ran `os.close` on a number
  the OS may already have reissued to another thread, and the bare
  `except OSError` around it hid that entirely, leaving the damage to surface
  somewhere unrelated.

- The managed-runtime record no longer reports a hash it never measured. The
  field carried the pinned archive digest the download was verified against,
  but sat on a record describing an executable, so the UI stated a verified
  hash for a binary that had not been hashed. It is now named
  `pinned_archive_sha256` everywhere it surfaces, which is what it actually is.

- `send2trash` is bounded to the locked major (`>=2.1.0,<3`). The source floor
  was `>=1.8` while the lock pinned 2.1.0, leaving a source install free to
  resolve an unverified future major of the one dependency standing between
  "recycle" and "permanently delete".

- Failures now carry a machine-readable reason code, and a permanently-gone
  item is marked terminal instead of being retried forever. The ledger
  recorded prose, so nothing could separate "come back later" from "gone
  for good" — the distinction that drives retry policy and that keeps one
  dead URL from poisoning a queue. Each failure now records a stable code
  (`geo_blocked`, `members_only`, `deleted`, `scheduled_not_live`,
  `throttled`, …) alongside the human sentence, and retry policy is read from
  a single table keyed by that code rather than re-derived by matching
  strings at each call site. `terminal` is deliberately narrower than "not
  retryable": a members-only video becomes downloadable once a subscribed
  session exists, so that stays operator-intervention, while a geo-block, a
  deletion, or DRM is terminal. Terminal rows are excluded from the
  due-for-retry query itself, so nothing can schedule them by forgetting to
  check. A scheduled broadcast is now retryable — it previously matched
  nothing, was classified unknown, and so the job gave up on a stream that
  had simply not started yet. Schema 21 → 22 adds `reason_code` and
  `terminal`; the local REST failure view exposes both.

- Kick VODs resolve and download again. Kick's site rework moved VOD delivery
  off the endpoint the extractor used, which has returned 404 for anything
  recent since — the same breakage that is still open upstream in yt-dlp
  (issue 17284), so the fallback could not cover it either. Metadata is now
  read from a channel-scoped listing keyed by the numeric creator id, and the
  media URL is minted by a playback POST rather than read off the video
  record. The legacy endpoint is still tried when the new path yields nothing,
  because it continues to answer for some older archives, and a VOD that
  cannot be resolved returns nothing rather than raising, so the yt-dlp
  fallback still gets its turn. A subscriber-only VOD now says so instead of
  reporting an empty source. The channel `/videos` listing enumerates past
  broadcasts through the same path when the older listing stops answering.

- Request headers an origin requires now travel with the stream to the
  downloader. Kick's reworked delivery host answers a manifest fetch that
  carries no `User-Agent` with a JSON security block, and its segments behave
  the same way, so resolving succeeded while every download failed. Extractors
  can report the headers their source needs, and FFmpeg is given them as
  `-user_agent`/`-headers` for remote inputs; a browser handoff captured for
  the same URL still wins where the two disagree.

- HLS variants are labelled with the name the manifest declares for them.
  Where a master playlist references a `TYPE=VIDEO` rendition group, that
  rendition's `NAME` is now the variant's label. Providers that number their
  variant playlists were previously listed as `0.m3u8`, `1.m3u8` and so on,
  which is not something a person can choose between; they now read `1080p60`,
  `480p30`. Manifests without video rendition groups are unchanged.

- Took an explicit position on FFmpeg 9.0 instead of inheriting one. The
  capability floor was `8.1.2` with no ceiling, so FFmpeg 9.0 — released
  2026-08-04, a different library ABI — was already being accepted on any
  user's PATH by a version-string comparison. Support is now decided on the
  reported `libavcodec` ABI major (62 for the 8.x line, 63 for 9.0); both are
  supported, and a build from an untested ABI is refused with a named reason
  rather than accepted silently. A build whose banner cannot be parsed still
  falls back to the version floor, because refusing it would be worse than the
  gap being reported.

- Certificate verification for remote FFmpeg inputs is now stated rather than
  inherited. FFmpeg 8.x defaults `tls_verify` to 0 and 9.0 hardcodes it to 1,
  so which build happened to be first in `PATH` silently decided whether
  remote certificates were checked at all — and on the documented 8.1.2 floor
  they were not. Every remote and filtered-HLS input now passes
  `-tls_verify 1`. The same omission made the raw-capture
  `allow_self_signed` opt-in a no-op on 8.x, where a self-signed origin
  already worked without it; a raw capture now verifies unless the opt-in is
  set, and the opt-in remains restricted to RTSPS and RTMPS endpoints.
  Hardware-encoder results are unaffected by the 9.0 removal of pre-11.1 NVENC
  SDK support: the probe runs a real one-frame encode, so an encoder the
  installed driver cannot actually use fails the probe and is hidden.

## [4.46.0] - 2026-08-06

- A declarative source adapter is now inert until its request contract is
  reviewed. A `.yaml` file dropped into the adapters directory defaulted to
  enabled and went live on the next URL detection, despite describing outbound
  requests and response mapping — while plugins required a fingerprinted
  contract review and imported yt-dlp templates stayed disabled until
  approved. An unreviewed definition is now parsed and listed but never
  dispatched, so it issues no request at all. Approval is keyed to a
  fingerprint of the reviewed surface only — the hosts, and per operation the
  method, URL, header names and query parameter names — so renaming the
  adapter or editing its response mapping keeps the approval, while
  repointing it at another host makes it inert again until the operator reads
  the change. Settings gains a **Source adapter review** panel listing every
  definition with its hosts, requests and state; approving opens a dialog
  spelling the contract out in plain language, and the contract is re-read
  from disk immediately before that dialog so the approval is for the file as
  it exists, not the row the table was built from. The CLI mirrors it:
  `streamkeep source-adapters` prints what is awaiting review, and
  `--approve`/`--revoke` act on it. Approvals never travel in a config
  import — a shared config could otherwise pre-approve a definition its
  recipient never read.

- The OpenAI-compatible translation endpoint no longer bypasses the guarded
  transport. It was the one outbound request in the tree that skipped
  `net_guard`, and it attaches a bearer API key, so a configured
  `http://10.0.0.5:8080` handed the operator's credential in cleartext to a
  host network policy would otherwise have refused. The base URL must now be
  `https://`, carry no embedded credentials, and pass address validation, and
  the request routes through the pinned loopback proxy like every other remote
  call. Configuration validation applies the scheme and shape checks only, so
  importing a config offline still works while the address policy is enforced
  where the connection is actually made.

- Bounded every translation provider response. All three backends read the
  body without a cap, including the unauthenticated local Ollama endpoint, so
  anything squatting that port could stream an unbounded body into the
  finalize path. Responses are now capped and an oversized one is refused with
  a named error instead of being buffered.

- Closing the window now joins every worker thread it owns, not only the ones
  named in the teardown list. Twenty-eight worker attributes were assigned on
  the main window and twenty-two were stopped, so the semantic index rebuild,
  scheduled backup, credential probe, highlight, media-server, scene,
  storyboard, thumbnail and update-check workers were destroyed while running
  — a `qFatal`, not a catchable exception, and for the semantic index a
  half-written database. The ordered list remains for the workers that need a
  specific timeout, cancel hook or deferred close; everything else is now
  swept by discovering the window's own `QThread` attributes, including those
  held one level deep in dicts and lists, so a worker added later is covered
  the day its attribute is assigned.

- Backups now carry the per-source `yt-dlp --download-archive` files. They
  were the one piece of state whose loss silently re-downloads an entire
  library: restoring a profile on a new machine left StreamKeep with no record
  of which playlist entries it already had. Members are bounded on count and
  total size, symlinks are skipped, and restore accepts only a single path
  segment under a known directory so a crafted archive cannot traverse out of
  the configuration directory. Archive files are merged rather than swapped
  per directory — deleting an entry the backup happens not to contain would
  cause exactly the re-download this is meant to prevent. `auth/`, `plugins/`
  and `source_adapters/` remain excluded, respectively as credential material,
  executable code, and definitions that would go live without review.

- Declarative source adapters can no longer supply a regular expression that
  wedges the interface. Adapter `path_regex` values were compiled from YAML
  with only a length check and then matched against the pasted URL on the
  calling thread — the GUI thread, once per keystroke — and Python's `re` has
  no timeout and cannot be interrupted. A shared adapter pack containing
  `(a+)+` therefore turned a 26-character path into 1.4 seconds of
  unstoppable backtracking. Patterns are now rejected at validation time when
  their shape permits catastrophic backtracking: backreferences, an unbounded
  quantifier nested inside another, and an alternation inside an unbounded
  quantifier. Ordinary path patterns — character classes, named groups, single
  and bounded quantifiers — are unaffected. Matching additionally refuses
  absurdly long paths.

- Memoised the declarative source-adapter registry. `Extractor.detect` is
  called for every URL it is handed, which on the desktop means every keystroke
  in the URL field, and the registry was re-read, re-parsed and re-compiled on
  each one — including a full config load. With twenty adapters installed,
  typing a forty-character URL cost 800 YAML parses and about 1.1 seconds of
  GUI-thread work; it now costs one parse and about 69 ms. The cache is keyed
  on a signature of the directory listing, each file's size and nanosecond
  mtime, and the config entries' contents, so editing, adding, removing or
  disabling a definition still takes effect without a restart.

- Advanced the managed Deno runtime pin from 2.3.1 to 2.9.5. The previous pin
  was affected by 17 published advisories — a critical `node:crypto`
  finalization bug, four Windows command-injection classes, a TLS-retry
  plaintext risk, and several `--allow-*`/`--deny-*` sandbox bypasses — in the
  one component that executes untrusted remote player JavaScript. Every asset
  digest is taken from the release's own published `.zip.sha256sum` files, and
  the PATH and managed floors both move to 2.8.1, the highest fixed version
  across that advisory set, so a vulnerable runtime is replaced rather than
  reused. Deno 2.9.5 also publishes a Windows arm64 build, so that host now
  resolves a runtime instead of failing.

- Added a `pinned-binaries` release-gate stage. `advisories` runs pip-audit
  over `requirements.lock` and by construction only sees Python wheels, so a
  downloaded executable pinned by version and hash was invisible to every
  stage — which is how the Deno runtime fell 17 advisories behind unnoticed.
  The new stage queries the OSV feed for each pinned external binary and fails
  closed when the feed is unreachable. `--skip <stage>` is now available and
  reports the omission as a visible SKIP rather than a pass.

- Fixed a failed managed-Deno re-install deleting the working runtime. The
  rollback guard tested for the absence of a backup directory, which is also
  true for every failure raised *before* the existing install is moved aside
  — extraction, the version probe, the metadata write — so a probe that timed
  out while anti-virus scanned the freshly written binary removed a perfectly
  good runtime and reported an error that never mentioned it. The rollback now
  only runs once the swap has actually started.

- Fixed the test suite writing into the operator's real configuration
  directory. Stateful modules capture their paths from `streamkeep.paths` at
  import time, and `tests/conftest.py` never rebound them, so running the
  suite created `library.db`, appended to `notifications.jsonl` and
  `security-events.jsonl`, and routed the crash handler's output into the
  operator's own `crash.log` — the release gate's `tests` stage included.
  The bind now happens before the first StreamKeep import, and
  `tests/test_config_isolation.py` fails if any module-level path addresses
  the real profile, if a test leaves a path rebound, or if a state file under
  the real directory is modified during a run.

## [4.45.0] - 2026-08-03

- Added opt-in gallery-dl image-set ingest. `--ingest` registers new image
  sets with public metadata and idempotent History rows, while `--package cbz`
  and `--package zip` preserve gallery-dl `info.json` sidecars and materialize
  a bounded cover for package-only sets so the authenticated gallery can
  render image media.

- Added opt-in local semantic moment search. A bounded, cancellable,
  dependency-free index combines timestamped transcript, scene, OCR, audio,
  and comment sidecars with confidence and provenance; exact FTS remains
  available, and the separate semantic index is excluded from portable
  backups by default.

- Added opt-in local-first translation for public metadata and chapter names.
  The configured app language selects the target, Ollama is the default local
  provider, translated metadata/chapter/NFO sidecars retain the originals, and
  cloud providers require explicit per-run consent before any request.

- Added opt-in YouTube VOD comment archival (V100). Jobs and monitor profiles
  can request bounded public comments; finalize writes versioned
  `*.comments.json` sidecars with published author names and text, logs source
  refusal/rate limiting without failing media downloads, and indexes comments
  for FTS search. Settings and the CLI expose the capture switch and count/
  byte limits.
- Added a capability-gated FFmpeg whisper transcription fallback. Settings can
  configure a local whisper.cpp model path when the resolved FFmpeg build
  exposes the filter; WhisperX, faster-whisper, and whisper.cpp remain ahead of
  it in the backend order, and all paths share the same transcript sidecars.

- Added versioned, data-only YAML source adapters with guarded JSON/HTML field
  mapping, VOD pagination, live checks, hot reload, diagnostics, and config
  import quarantine. Adapter requests and returned media URLs use the existing
  SSRF policy; definitions cannot execute code or access local capabilities.

- Added a schema-v20 append-only history action log for favorites, watched
  state, playback positions, bookmarks, and deletions. Materialized history
  state can be replayed after backup restore or library rebuild, and redundant
  actions are compactable without changing the existing history read shape.

- Added a persistent scheduled health surface across Settings, the `health`
  CLI command, and authenticated `/api/health`: runtime tools, credentials,
  archive roots, extractor retry circuits, and disk pressure are severity-
  ranked, repair-guided, persisted, and emitted through stable hook/webhook
  event names as conditions open or resolve.

- Corrected the agent-facing architecture notes for the seven-page shell,
  SQLite package layout, and side-effect-free dependency bootstrap. The release
  claims gate now detects drift between that documented page count and the
  shipped tab registry.

- Split the SQLite library, Browser Companion server, and post-processing
  Settings presets behind stable facades. Schema migration ordering, database
  table-family boundaries, server authentication/routes/static assets, and the
  external web UI now have explicit package-level ownership without changing
  existing import paths or endpoint behavior.

- Added subprocess/file-worker coverage for safe argv construction, player and
  companion-settings paths, and partial-output cleanup; transcript and summary
  sidecars now use atomic writes, and the measured coverage gate is 64.0%.

- Reused configured profile SQLite connections per thread, applying the
  runtime and journal policy once per physical handle while keeping temporary
  migration databases one-shot. GUI and headless shutdown now close cached
  handles explicitly, and profile switches invalidate stale connections.

- Added explicit plugin contract review in Settings and the CLI. Permissions,
  dependencies, compatibility ranges, and entry points are shown before an
  adapter is enabled; a changed manifest contract, including a new permission,
  must be reviewed again before a trusted plugin can load.

- Added unsigned Windows shell integration: aggregate queue progress, paused
  and failed states on the taskbar, an opt-in battery/Energy Saver queue hold
  that resumes on AC, and one optional progress-bound notification for long
  queues when the host already provides the WinRT bridge. No package identity,
  signing step, or required WinRT dependency was added.

- Narrowed Flatpak archive access by removing broad home filesystem permission.
  Native Qt folder choosers now use the XDG FileChooser and persistent
  Document Portals, with an explicit-path fallback when a portal is absent.

- Added category-specific remediation guidance for failed jobs. The queue and
  operations surfaces show the next safe step, the CLI and authenticated API
  expose the same URL/path-free guidance, and available actions jump to the
  relevant Storage, credentials, download, network, or YouTube health surface.

- Internationalized the embedded web remote through the shared Qt catalog,
  selecting the page language from `Accept-Language` or `?lang=` and falling
  back to English for unfinished translations.

- Declared the optional `python-mpv>=1.0.8`, platform-managed
  `libmpv>=0.41.0`, and `boto3>=1.43.0` runtimes outside the reproducible
  locks, recorded them in the SBOM, and added native libmpv version/advisory
  probing so player and S3 operations fail closed with repair guidance.

- Made broad exception fallbacks distinguishable: diagnostic paths now report
  through the structured log bridge, all intentional swallows carry safety
  reasons, and the main-window worker shutdown path uses one escalation helper.

- Hardened ordered rules: site criteria now honor host boundaries, malformed
  duration bounds fail closed, legacy `filename_template` actions migrate to
  `arg_template`, and rules can override folder and filename templates.

- Made copied download commands safe to paste into Windows `cmd.exe`,
  escaping metacharacters while preserving exact argv round-trips on Windows
  and POSIX shells.

- Cleared browser replay headers as queued service jobs become terminal,
  including pre-dispatch cancellation, tombstone skips, retry cancellation, and
  finalizer exits, and bounded the in-memory header cache with oldest-first
  eviction.

- Added labeled scoped API-token inventory and management. The master-only
  endpoint, CLI, and Browser Companion Settings panel expose redacted metadata,
  while one-token revocation takes effect immediately without displaying bearer
  values.

- Added opt-in BagIt 0.97 fixity export from the authoritative archive
  manifest, including SHA-256 payload/tag manifests, bag metadata, and a
  per-file SHA-384 SRI manifest that verifies through podcast integrity.

- Archived typed Twitch and Kick chat events in `chat.jsonl`, retaining raw
  unknown envelopes, rendering event rows distinctly in ASS and chat-video
  overlays, and giving raids, subscriptions, moderation and announcements
  extra highlight weight while leaving ordinary message rows unchanged.

- Parsed guarded HLS DATERANGES schedule documents into marker sidecars,
  resolving nested X-SCHEDULE-OFFSET rows with bounded cycle-safe traversal,
  preserving JSON scalar types and verbatim schedule bodies, and classifying
  low-latency preload hints by KEY versus PART/MAP.

- Made capture cancellation signal ffmpeg/yt-dlp gracefully in an isolated
  process group, wait for the container to close, and escalate through
  terminate/kill only when the process ignores the stop request.

- Made FTP/SFTP resume distinguish an absent remote partial from permission,
  transport, and unsupported-size-probe failures. Only explicit not-found
  responses start at byte zero; other probe failures stop the transfer with a
  diagnostic instead of silently restarting.

- Added a shared output-path preflight for templated downloads, native and
  yt-dlp workers, finalization, and media-server imports. Full paths are
  checked before writes, sidecar candidates are included, and Unicode path
  components are bounded by UTF-8 byte length with named `path_too_long`
  failures.

- Kept timed-out headless probe workers alive in a service-owned reaper until
  their `QThread` is finished, bounded concurrent probes, and return a
  retryable `429` with `Retry-After` when capacity is occupied.

- Held the executor lease while stopped queue workers drain, heartbeating it
  until every worker reports `isFinished()`, and counted active finalizers in
  the download concurrency budget.

- Added per-user `streamkeep://` registration for Linux XDG/`xdg-mime` and
  macOS LaunchServices, updated the Flatpak desktop entry for URI routing, and
  kept the existing HTTP(S)-only validation shared across every platform.

- Moved the Windows reproducible-build and release-gate lane to Python 3.14.6,
  regenerated the hash-locked build inputs under Python 3.14, and made frozen
  release entry points reject older interpreters. The Flatpak lock remains
  aligned with its Python 3.13 KDE BaseApp runtime.

- Scoped CLI completion manifests and failed-job retry metadata to each
  templated job output directory, preventing sibling recordings from being
  rehashed or resumed from the wrong level.

- Preserved Smart Mode regular-expression bodies while normalizing their
  prefixes, and matched regex patterns against the same URL candidates as
  glob patterns.

- Hardened re-template plans with bounded schema validation, re-derived action
  IDs, archive-root containment for source/destination and backups, and named
  invalid-plan refusals before any backup or move.

- Replaced rebuild staleness checks based on SQLite file bytes with read-only
  content fingerprints for history rows and tag assignments, so committed
  WAL-resident changes now refuse a stale apply.

- Made rebuild database swaps crash-recoverable with an fsynced marker,
  startup rollback, and orphaned staging cleanup before the database opens.

- Added an append-only re-template swap journal and startup finalizer. An
  interrupted directory move is now reversed or retained from the durable
  history decision, with empty staging parents cleaned and storage visibility
  restored after restart.

- Joined maintenance and re-template workers to the window close lifecycle;
  shutdown now interrupts and waits for audited batches and defers close until
  an unresponsive batch finishes.

- Hardened NFO sidecar parsing with `defusedxml`, named parser issues in adopt
  and rebuild previews, and removed every direct stdlib `ElementTree` import
  from `streamkeep/`.

- Refused to open databases written by a newer schema version before any
  migration or FTS configuration, with a blocking GUI message and a clear
  non-zero CLI error naming both schema versions.

- Kept sync-viewer cards owned by their stream slots and reused them across
  grid relayouts, preventing live mpv widgets from being destroyed during
  third-stream or audio-slot changes.

- Routed upgrade-version pruning through the recycle bin, refusing to delete
  when `send2trash` is unavailable and removing pruned history rows through
  the existing retention tombstone transaction.

- Made re-template file renames dependency-aware and case-insensitive, with
  named cycle conflicts and byte-preserving overlap coverage so a destination
  can never overwrite a source still awaiting its move.

- Constrained headless queue requests to a frozen safe field set, confined
  client-selected output directories to the configured root, and kept upgrade
  and archive controls under trusted server/config ownership. Rejected fields
  are logged without preventing the URL from being queued.

- Retired the unsupported MSIX packaging lane and removed its blocked roadmap
  entry. The shipped matrix is now the unsigned Inno installer, portable
  onedir zip, Flatpak manifest, and WinGet manifest because MSIX requires
  signing.

- Raised the cryptography runtime floor and all platform lock pins to 50.0.0,
  clearing the current pip-audit advisory against the previously locked 49.0.0.
  The advisory is not reachable in StreamKeep: the app uses x509, AESGCM,
  pkcs12, and signature verification, never `pkcs7_decrypt_*`; this is gate
  hygiene, not an incident.

- Sanitized XML 1.0-forbidden characters from feed and gallery metadata before
  escaping, preserving existing entity escaping while keeping strict podcast
  clients parseable when scraped titles contain C0 controls.

- Removed the obsolete in-memory gallery share registry. Gallery and share
  pages now consume explicit database-backed publishing entries, and the
  unused feed publishing-ID constant is gone.

- Added explicit, hash-verified Deno acquisition for full YouTube JavaScript
  support. Settings and `youtube-health` can install the pinned Deno 2.3.1
  runtime from the official release or a local ZIP, with no startup/import
  download; runtime preference, managed provenance, removal, and offline
  diagnostics are surfaced while PATH runtimes remain selectable.

- Added Podcasting 2.0-fidelity archiving. Podcast feeds now retain stable
  GUID-first episode identity, paged-feed traversal, namespace metadata,
  per-episode artwork, alternate enclosure/source declarations, and raw
  `podcast:value` declarations. Publisher integrity hashes are verified during
  finalization, and chapter references produce ffmetadata and WebVTT sidecars.

- Closed the WCAG 2.2 accessibility gaps in custom desktop controls. Clip
  handles, waveform/timeline scrubbers, and schedule blocks now meet the
  24px target contract and offer keyboard plus single-pointer alternatives;
  switch/slider role hints include orientation, focus reveal keeps controls
  reachable in compact layouts, and System theme follows live OS contrast
  preference changes.

- Made the offscreen startup contract tear down Qt windows explicitly before
  SIP interpreter shutdown, eliminating the packaged/source access violation
  while retaining the invisible one-window smoke guarantees.

- Updated native HLS handling for RFC 8216bis delta playlists and DATERANGE.
  Retained segments are merged across `EXT-X-SKIP`, SCTE-35 and unknown
  vendor classes survive in `hls.markers.json`, interstitial assets remain
  marker-only, and guarded `daterange-schedule` resources are archived beside
  the capture without persisting signed query material.

- Added rolling archive-integrity scrubbing. Storage scans now perform cheap
  manifest presence/size/mtime checks, while the desktop and headless
  schedulers hash a configurable fraction of overdue recordings with durable
  coverage checkpoints, byte/rate bounds, cancellation, offline-volume
  deferral, and notification-backed mismatch reports; no repair or deletion
  is automatic.

- Added archive-wide output re-templating with a strict preview/apply plan.
  Existing recording directories move as atomic units with media, notes, and
  sidecars; matching media sidecars can be renamed together, history,
  manifests, tags, and publication joins are updated, and every action is
  audited with rollback on failure. Reserved names, collisions, unresolved
  fields, and unsafe path lengths remain review-only conflicts.

- Added same-identity quality-upgrade decisions with explicit ordered ladders,
  cutoffs, scored matchers, named audit reasons, and per-item history tooltips.
  Verified replacements publish atomically beside the known-good recording and
  retain a bounded set of prior versions; automatic upgrades remain opt-in.

- Added preview-first external-library adoption for arbitrary media trees,
  yt-dlp download archives, and `.info.json`/NFO sidecars. Conflicts remain
  explicit, apply creates a backup and atomically adds history plus monitor
  archive seeds, and media files are never moved or rewritten.

- Added a preview-first `db rebuild` workflow that reconstructs history,
  sidecar-backed tags, and integrity manifests from an arbitrary on-disk
  library. Legacy sidecars migrate forward, missing history state and
  unsupported artifacts are reported explicitly, and apply backs up before
  activating staged library and tag indexes without touching media.

- Added bounded mid-capture HLS/DASH delivery refresh. A 403/410 playlist or
  segment failure now re-resolves the stable source page with jittered limits,
  preserves the existing resume sidecar and capture offset, remuxes refreshed
  parts into one recording, and writes a credential-free seam report when a
  discontinuity or codec change is observed.

- Added canonical deletion tombstones for user, retention, and lifecycle
  removals. Recycled user media is now skipped by monitored sources, playlist
  expansion, and queue dispatch until its tombstone is cleared; the Settings
  companion exposes a list and per-item clear action.

- Persisted a canonical media identity and public page URL across native
  extractors, immutable jobs, history rows, and metadata sidecars. URL keys now
  normalize host, tracking, query order, and provider-specific page forms;
  existing history rows migrate conservatively and leave unrecoverable IDs
  unknown.

- Narrowed the browser companion to Chrome 144+ with `activeTab`-scoped,
  user-started media capture and loopback-only host access. Popup pages now
  route pairing and send requests through the service worker; the extension no
  longer requests `tabs` or `<all_urls>` access, and navigating a captured tab
  stops its request listeners.

- Made startup update discovery work on unsigned builds. Stable GitHub
  releases now surface their published SHA-256 and a manual release-page
  link, while self-replacement remains limited to the operator-authenticated
  manifest and signature path.

- Added a privacy-safe local-server security audit trail for failed bearer
  validation, revoked clients, origin/scope/transport rejection, and replayed
  mutation proofs. Events use route-only paths and client hashes, are capped
  during bursts, surface through Notifications, and are included in diagnostic
  snapshots without tokens, URLs, or addresses.

- Added a separate SQLite 3.53.2 FTS5 safety floor. Source runtimes below it
  disable FTS5 and use bounded fallback search with degraded-mode repair
  guidance, while frozen builds refuse unsafe runtimes. Settings and
  diagnostics expose the FTS5 state.

- Created DPAPI and portable-secret temporary files with exclusive owner-only
  permissions before writing any secret payload. Portable-secret restore now
  commits cookies, config secrets, and account credentials transactionally and
  rolls every store back when a later write fails.

- Pinned the first paired browser-extension origin per local companion server,
  persisted that pin in Settings, and rejected other extension origins even
  when they present the valid master token. Session cookies now carry
  `Secure` only after the configured HTTPS reverse-proxy boundary is proven.

- Hardened ntfy and JSON webhook curl calls with `--` URL termination and
  `net_guard` validation. Unsafe private, loopback, link-local, metadata, and
  option-like targets are logged and refused before curl starts.

- Required fresh JSON mutation proof on the status-scoped media-server preview,
  intelligence preview, and operations export POST routes. The OpenAPI
  document now exposes the timestamp and one-use nonce headers for every POST
  operation.

- Raised source dependency floors to match the hashed runtime lock: Qt 6.11.1,
  cryptography 49.0.0, urllib3 2.7.0, and Paramiko 5.0.0 for optional SFTP.
  The capability registry now blocks unsupported Paramiko versions with repair
  guidance, and the release gate fails when a direct source floor exceeds its
  lock pin.

- Hardened named yt-dlp argument templates with an explicit allow-list and
  option-specific value validation. Executable paths, plugin/config delegation,
  output paths, update commands, and link writers are rejected; imported
  templates are now quarantined until individually approved during config
  import.

- Added per-download dubbed-audio language selection and a clean mute mode.
  ISO 639-1 language preferences are translated into yt-dlp audio selectors
  with multistream support, while muted native and yt-dlp video outputs strip
  audio mappings and pass ``-an`` through FFmpeg post-processing. Both choices
  are validated, persisted in job/resume state, exposed in the Advanced panel,
  and available through the download CLI.

- Added opt-in Twitch VOD audio recovery. Finished Twitch HLS playlists can
  probe same-format ``-muted`` fragment URLs through the guarded transport and
  substitute reachable unmuted fragments; unavailable sources remain muted and
  are reported. The Settings checkbox and ``download --twitch-unmute`` CLI
  flag share the same job-spec setting, while live captures remain unchanged.

- Added Twitch SSAI filtering for native VOD and live HLS captures. Twitch
  stitched-ad DATERANGEs, Amazon ad titles, cue markers, and post-discontinuity
  low-latency prefetch segments are excluded through a refreshed local media
  playlist; content resume discontinuities, media sequence identity, and the
  guarded remote segment transport are retained. VOD duration metadata now
  reflects the content-only timeline, and non-Twitch HLS jobs are unchanged.

- Added an optional yt-dlp-ytse SABR fallback for YouTube. The installed plugin
  is recognized only when its SABR module surface is present; detected
  storyboard/SABR-only resolves and direct downloads retry with
  `--extractor-args youtube:formats=sabr`. The YouTube health report, CLI, and
  Settings panel show availability and the plugin's unsupported
  `--download-sections`, `-N`, and resume limits; incompatible jobs skip the
  retry with an explicit hint.

- Added an optional manifest-v2 `youtube_backend` plugin contract for remote
  cipher/PO-token helpers. Settings and `youtube-health` expose redacted mode,
  endpoint, plugin, capability, and reachability state; yt-dlp resolve,
  playlist, browser-fallback, and download commands consume only validated
  YouTube extractor-argument pairs. Missing plugins, failed probes, and
  malformed responses fail open to the existing local path.

- Added a durable Operations view over queue, monitor, and failure state. The
  desktop, CLI, and authenticated local API share bounded paging, state/source/
  stage filters, aggregate size/duration and retry health, selected retry or
  discard actions, and URL/path-free JSON or CSV reports.

- Added versioned plugin adapter contracts for extractors, post-processors, and
  upload destinations. Manifest v2 declarations now include interface versions,
  permissions, dependencies, and bounded timeouts; compatibility diagnostics
  fail closed, package-scoped imports avoid global `sys.path` mutation, and the
  broker returns typed success, permission, cancellation, timeout, and error
  outcomes. `plugins --json` reports the contract and `--load-trusted` provides
  an explicit headless load path.

- Added reachable local-first intelligence workflows. History can preview the
  exact transcript payload and queue summaries or resource-bounded smart
  thumbnails; the CLI and authenticated REST API expose the same durable,
  cancellable jobs. Ollama remains the default local provider, cloud summaries
  require a one-use consent token bound to provider/model/payload, optional
  redaction is visible, provider credentials stay in the secure store, and
  summary metadata records provider/model/version for editing and rebuilding.
  Smart thumbnails are written beside, never over, an existing source thumbnail.

- Added secure upload and media-server export delivery. Authenticated REST
  clients can save redacted destination profiles, preview and materialize
  Plex/Jellyfin/Emby/Kodi layouts with sidecars, and queue durable per-file
  transfers with persisted progress, retries, cancellation, and restart
  recovery. SFTP now rejects unknown host keys, FTPS validates certificates,
  and plain FTP/HTTP WebDAV require explicit insecure opt-in.

- Added authenticated gallery and RSS publishing. History can publish or revoke
  selected recordings and channel/all-recording feeds; durable random ids survive
  restart, while gallery/media/feed routes enforce bearer access, canonical media
  paths, bounded Range delivery, stale-file handling, and immediate revocation.

- Added V20 pre-queue validation for desktop companion and headless REST flows.
  `POST /api/validate` resolves a URL into bounded, delivery-URL-free media
  picker metadata with per-item video/audio/photo/GIF types and optional
  background-audio choices. A short-lived server-side validation id binds the
  selected item to the subsequent durable queue job; the desktop VOD picker
  shows the same media type information.

- Added V18 media-server layouts for monitored channels. Imports now support
  Season or flat S/E naming for Plex, Jellyfin, Emby, and Kodi, write existing
  privacy-safe NFO sidecars, and can maintain a portable M3U or native server
  playlist. Settings can fetch one explicitly selected server user, show a
  watched-state preview, skip ambiguous matches, and apply only local watched
  metadata without triggering lifecycle deletion.

- Added V16 Smart Mode URL profiles. An ordered, shell-free profile resolver now
  selects URL-pattern preferences consistently for desktop downloads, queue
  jobs, CLI runs, and the local REST service, with explicit job values taking
  precedence. The desktop has a zero-dialog toggle and profile editor for
  output/templates/quality, proxy, and site-bound authentication references;
  imported profiles remain quarantined until approved.

- Added the V14 DRM-free MSE recorder. The headless `mse-capture` command
  injects a pre-navigation `SourceBuffer.appendBuffer` tee, writes bounded
  ordered chunks, remuxes them with FFmpeg, and hard-refuses EME requests or
  encrypted events. It uses the existing DNS-pinned Playwright network broker,
  one-tab/no-playback-speed contract, and retains staging when remux fails.

- Added the optional V13 Streamlink live engine for Twitch/Kick. When
  Streamlink 8.4+ is installed and enabled, live captures use mandatory Twitch
  ad filtering, low-latency HLS, DVR rewind options, and an in-process byte
  reader behind the shared SSRF-guarded proxy. A missing or old install stays
  non-fatal, and monitor polling can use the same guarded engine when a native
  platform API has no answer.

- Added V12 browser-companion media handoff: the MV3 extension can explicitly
  capture active-tab manifests and media requests, surface them in the popup,
  and send a bounded replay context to Fetch or Queue. StreamKeep keeps only
  Referer, Origin, User-Agent, Cookie, and Authorization headers for the
  active job; captured credentials are transient and excluded from durable
  queue/spec/resume serialization.

- Hardened parallel direct-MP4 downloads (V55): the worker now validates the
  source before starting parallel transfer, and both HEAD and range requests
  use the pinned loopback policy proxy with redirect and proxy-bypass guards.
  Unsafe loopback, private-LAN, link-local, and metadata targets are refused
  before any output bytes are written.

- Fixed resumed multi-segment downloads (V56): completion now treats the
  worker's remaining segment set as a subset of the already-completed resume
  state, so successful resumes emit finalization, clear their sidecar, and do
  not remain stuck as partial jobs.

- Hardened DASH, TTML, and OPML XML parsing (V57) with `defusedxml`, rejecting
  entity-expansion payloads before they can consume unbounded CPU or memory.

- Fixed transcript search for FTS5 punctuation (V58): user terms are quoted,
  embedded quotes are escaped, and matching is restricted to transcript text
  so strings such as `C++`, `foo:bar`, and quoted terms return their hits.

- Updated the Clip/Trim visual controls (V59) to use live theme and accent
  tokens for filmstrip placeholders, range handles, waveform paints, and crop
  overlays, including refreshes when the active palette changes.

- Hardened local-server JSON body handling (V60): valid JSON arrays and scalar
  values are rejected as empty request objects, producing clean validation
  responses on pairing and queue endpoints instead of dropped connections.

- Preserved per-monitor authentication profile references (V61) through the
  ChannelMonitor database save/load round-trip, so a future per-channel auth
  selector cannot silently lose its opaque profile ID.

- Improved first-run onboarding (V62) with a High Contrast appearance option,
  immediate theme application, and clearer "FFmpeg ready" status copy.

- Hardened RSS and gallery attribute rendering (V63) so media URLs, share IDs,
  and gallery base URLs cannot break out of quoted XML/HTML attributes.

- Cleared the Ruff F/E7 hygiene findings (V64), removing dead imports and
  replacing style-only lambdas and semicolon-packed statements with explicit
  definitions.

- Added validated raw-protocol capture jobs (V9) for RTSP, RTMP-listen, SRT,
  UDP/RTP multicast, and ICY radio, with bounded FFmpeg commands, listener
  modes, stdin-backed SRT passphrases, reconnect options, and ICY
  now-playing track manifests.

- Serialized startup schema migrations (V65) with SQLite `BEGIN IMMEDIATE`,
  re-reading the schema version under lock and preserving migration rollback
  behavior for concurrent GUI, service, and CLI starts.

- Cleared stale backup claims (V66) during not-due schedule updates, so a
  crashed process no longer leaves the operations view reporting a run as
  active until the next cadence.

## [4.44.0] - 2026-08-02

- Audio downloads now get their album-artist filled in (V41). SoundCloud,
  Audius, and podcast audio routinely arrived without `album_artist` — the
  exact field media libraries group by — so a whole back catalogue scattered
  into single-track "Unknown Artist" entries. Finalization now fills the
  missing artist fields from the uploader/channel the download already
  carries, and a podcast episode additionally gets its show as the album. An
  existing tag is never overwritten, nothing is re-encoded, and a failed write
  leaves the original file untouched.

- Fixed the ffmpeg-native output path hardcoding `.mp4` and exposed filename
  templating to headless runs (V39). A job configured for mkv or webm produced
  a file whose extension lied about its contents, including for chunked live
  captures; the configured container now decides the extension everywhere, with
  mp4 as the fallback for "original" since ffmpeg needs a concrete muxer.
  `streamkeep download` gains `--filename-template` and `--folder-template`,
  and one shared resolver in `streamkeep/utils.py` now serves GUI, CLI, and
  monitor jobs, so headless and desktop runs with the same configuration name
  a file identically.

- Retired the 520 MB one-file Windows executable for a onedir tree plus an
  unsigned Inno Setup installer (V35). The one-file build re-extracted its
  whole payload to a temp directory on every launch; measured cold start drops
  from 11.9s to 0.24s, and four concurrent launches now create zero temp
  extraction directories instead of four, removing the `_MEIxxxx` race
  entirely. `packaging/build.py` defaults to onedir and `--installer` compiles
  the unsigned installer; `--onefile` still builds the legacy shape.
  Reproducibility is now verified as a deterministic digest over the whole
  tree. The updater refuses self-replacement for a directory install outright —
  swapping one executable inside a tree cannot produce a consistent
  installation — and explains that updating is by verified download or package
  manager. The WinGet manifest's stale 4.38.0 version and placeholder hash are
  fixed: the version and download URL are now stamped from the package version,
  and `packaging/winget_hash.py` fills the release hash from the built
  installer. No signing step exists anywhere in this path.

- Added one local, unsigned release gate (V52): `python packaging/release_gate.py`
  runs compileall, pyflakes, deterministic translation extraction/compilation,
  the test suite, capability-claim reachability, release-claim consistency, a
  dependency advisory scan, the reproducible build, SBOM/license generation and
  artifact startup smoke — cheapest first, stopping at and naming the exact
  failed stage. No signing step and no CI workflow is introduced.
  The gate immediately caught real drift, now fixed: the README promised an
  Authenticode/PFX signing flow this project never performs (releases are
  unsigned and updating is manual or package-managed), Spanish was advertised
  without noting it is beta at 195/1427 strings, the Flatpak metainfo sold
  experimental upload and plugin capabilities as shipped, and native
  notifications were still registered as unreachable despite being wired into
  the desktop lifecycle — now a shipped claim with a reachability test.
  The advisory scan was auditing whatever happened to be installed in the
  developer's interpreter rather than StreamKeep's own hash-pinned
  `requirements.lock`; it now audits the lock, which is clean.

- Closed the YouTube PO-token provider loop (V33). StreamKeep previously only
  checked whether a provider plugin was importable, which looks identical
  whether the provider is answering or dead. It now probes the local endpoint
  (loopback addresses only — a provider handles account-bound tokens, so a
  non-loopback URL is refused outright), injects its `base_url` extractor
  argument into every YouTube job once a plugin is installed *and* the endpoint
  responds, and reports an installed-but-unreachable provider as a distinct
  warning. Adds a "Set up provider" action in Settings and
  `youtube-health --setup-pot-provider` on the CLI that installs the plugin
  where that is safe, launches an operator-configured local server, and
  otherwise prints copy-paste steps. Packaged builds never shell out to pip.

- Improved live-capture reliability (V36). yt-dlp's fragment losses are now
  parsed out of its output and reported as explicit missing intervals instead
  of passing silently, including on captures that exit successfully. When a
  live capture fails with reported gaps, its raw `.part`/segment staging is
  moved into a `<name>.rawcapture/` directory with a JSON gap report so later
  cleanup cannot reap it, and a new History action rebuilds a playable file
  from those fragments. Salvage is idempotent, stream-copy only, always writes
  a new `.salvaged.<ext>` file, and never modifies the raw capture or a
  known-good output. Adds an optional, opt-in ytarchive engine (detected on
  PATH like gallery-dl/lux) that re-captures a YouTube livestream from the
  start; without it, behaviour is unchanged.

- Added named, site-bound authentication profiles (V50). A profile pairs an
  operator label with the hosts and platforms it is allowed to authenticate and
  keeps its own permission-restricted cookie jar. Jobs, rules, and monitors
  persist only an opaque profile ID — never cookie material or a credential
  path. Resolution refuses cross-site fallback: naming a profile for a URL it
  does not cover sends no credential at all, and an ambiguous match sends
  nothing rather than guessing. Managed from Settings and from a new
  `streamkeep auth` CLI (list/create/import/check/delete), with a
  `--auth-profile` flag on `download`. The legacy shared cookies.txt is moved
  (not copied) into an explicit Default profile scoped to the domains it
  actually contains, and the global cookie setting is retired.
- Made rotating automatic backups reachable and observable (V51). Settings now
  expose enablement, destination, cadence (hourly/daily/weekly) and retention,
  plus a live status line and a "Back up now" action. Runs are claimed durably
  in SQLite and executed only by the process that holds the profile execution
  lease, so a desktop window and a headless service pointed at the same profile
  never overlap. Each archive is written to a staging file, validated, and only
  then renamed into its rotation slot — a failed or corrupt run leaves older
  backups intact. Failures back off instead of retrying every tick, and last
  success, size, next run and failure reason are visible in Settings and on
  `GET /api/status`.
- Added a persistent, error-aware automatic retry scheduler (V49). Failures are
  classified into retryable categories (network, timeout, rate limit, retryable
  5xx) and intervention categories (authentication, DRM, missing media, invalid
  config, permission, disk). Retryable rows persist attempt count, category,
  `next_attempt_at`, `Retry-After` and capped exponential backoff with stable
  per-source jitter, survive restart, and can be cancelled without discarding
  the failure record. Per-source circuit breaking defers scheduled work after
  repeated failures, and every surfaced reason is scrubbed of URLs and
  credentials.
- Re-imagined the desktop as an archive workstation with a persistent workflow
  rail, page-aware operational header, global search, and live local-tool
  health.
- Reworked Download around a source resolver, responsive queue/activity panes,
  and archive-health summary; added guided empty states to Monitor and History.
- Refined dark, light, and high-contrast palettes, responsive Settings controls,
  theme-aware icons, and Spanish catalog coverage for the new shell.
- Added GUI regression coverage for navigation, empty states, narrow History
  layout, system health, and translated shell copy.
- Added a single heartbeat-backed execution lease per library profile so the
  desktop and headless server cannot dispatch the same durable queue.
- Replaced live whole-table queue rewrites with row-level merge, explicit
  deletion, optimistic revisions, and owner-bound state transitions; stale
  windows now preserve concurrent enqueues and terminal results.
- Added expired-owner recovery, actionable second-executor refusal, schema-v8
  migration, and spawned-process race coverage.
- Repaired origin-bound browser sessions so the shipped web remote pairs and
  loads same-origin reads without weakening extension or reverse-proxy origin
  checks.
- Added explicit missing-origin, mismatched-origin, cross-site, same-origin
  mutation/replay, and trusted-proxy coverage; web-remote failures now display
  the server's actionable rejection reason.
- Added recursive HLS and DASH URI validation for variants, renditions,
  segments, keys, maps, templates, lists, initialization, bitstream-switching,
  timing, location, and content-steering references.
- Split trusted local FFmpeg protocols from remote-media protocols; remote
  jobs now exclude local file/pipe inputs and route every runtime HTTP(S)
  connection through an address-pinned loopback policy proxy.
- Closed loopback, link-local, metadata, private-address, alternate-IP,
  DNS-rebinding, redirect, and `NO_PROXY` bypasses with malicious-manifest,
  blocked-listener, public-CDN, real HLS capture, and reproducible artifact
  coverage.
- Versioned public metadata sidecars around stable platform/source identity
  and canonical webpage URLs; Twitch delivery manifests, request headers,
  cookies, signed queries, and remote thumbnail URLs are no longer persisted.
- Replaced NFO delivery URLs with stable source IDs and local thumbnail
  references, made sidecar writes atomic and observable, and migrated legacy
  sidecars through the same public-safe reader used by storage/import flows.
- Scrubbed JSON, JSONL, NFO, subtitle, and text sidecars during share-bundle
  export; malformed and oversized structured sidecars are now blocked instead
  of copied, and diagnostics use the same credential-aware URL redaction.
- Added platform-scoped source identity to VODs, immutable jobs, resume state,
  queue records, and schema-v9 history with safe legacy URL backfill.
- Made monitor upgrades compare the exact media identity, re-check resolved
  quality before download, and bypass a source archive only for the explicit
  upgrade job.
- Staged upgrades beside the known-good recording, verified media and SHA-256
  integrity, then atomically published a versioned directory; cancellation,
  disk, probe, checksum, rename, and history/manifest failures preserve the
  previous recording and transactionally avoid partial history rows.

## [4.43.5] - 2026-07-27

Deep audit pass: resolve/playlist correctness, live-stop sidecar hygiene, CLI
output robustness.

- **[BUG] extractors/ytdlp.py** — resolve commands now pin `--no-playlist`
  (primary, fast-print, and cookie-retry paths). Without it, a
  `watch?v=X&list=Y` URL made yt-dlp enumerate the whole playlist: the fast
  `--print` path silently resolved an arbitrary entry (two output lines per
  video) and the `--dump-json` path failed `json.loads` on multi-object
  output. The download command already pinned `--no-playlist`; resolve now
  matches. Verified live: the watch+list repro now resolves the requested
  video.
- **[BUG] workers/download.py** — a live capture kept on Stop now clears its
  resume sidecar (both the yt-dlp and native ffmpeg paths). The recording is
  finalized to History by the stop handler, so the leftover sidecar only
  produced a bogus "Interrupted download ready to resume" banner next launch.
- **[UX] extractors/ytdlp.py** — fetching a multi-entry container URL (e.g.
  `/playlist?list=`) no longer runs the doomed full extraction (which fully
  extracted every entry and then failed anyway); the fast probe detects the
  multi-entry shape and logs "use the playlist expansion action" instead.
  Resolve also logs a heartbeat when the quick probe hands off to the slow
  full extraction, and no longer spawns the fallback subprocess when the
  fast attempt was interrupted by cancel/shutdown.
- **[UX] cli.py** — em-dashes in banner/maintenance strings printed as
  mojibake on cp1252 consoles in the frozen exe; replaced with ASCII hyphens.
  Worse, `UnicodeEncodeError` is a `ValueError` subclass, so `_print_line`
  silently dropped ENTIRE lines for titles with CJK/emoji characters — it now
  degrades unencodable characters and keeps the line.
- **[PARITY] extractors/ytdlp.py** — new shared `apply_resolve_timeout_config`
  applies the `ytdlp_resolve_timeout` clamp in the GUI, the CLI download
  path, and server mode (previously GUI-only).
- **[CLEANUP]** removed dead imports flagged by pyflakes
  (`headless_service.py` refactor leftovers, `job_spec.py`
  `dataclasses.field`).
- Audit notes: gallery-dl/lux integrations, lifecycle retention (keep-last-N
  channel keying), web-remote HTML escaping, capabilities JS-runtime version
  floors, and theme-token usage were re-verified clean — no fixes needed.
  ROADMAP V33 corrected: the JS-runtime version gate half was already
  implemented in `capabilities.py`.
- Tests: 4 new regression tests (no-playlist pinning, multi-entry rejection,
  sidecar clear on kept live stop, fast/fallback routing). Suite: 1025.

## [4.43.4] - 2026-07-27

Fix: fetch hung ~2 minutes on "fetching stream info…" for former-livestream
YouTube VODs (follow-up to 4.43.3).

- `extractors/ytdlp.py` — 4.43.3 raised the resolve timeout so post-live
  manifestless VODs would eventually resolve, but the full `--dump-json`
  extraction still took ~2 minutes (it generates every format's fragment
  list), so the fetch appeared stuck. Resolve now uses a **fast field-projected
  `--print`** first (two JSON lines: metadata, then a fragment-free formats
  array), which returns the same data in ~1-2s because it never requests
  `fragments`. The full `--dump-json` path (with its Cloudflare/auth-cookie
  retries) is retained as an automatic fallback when the projection can't
  resolve a URL. End-to-end fetch for the repro VOD dropped from ~120s to ~2s.
- Refactor: the `--dump-json` resolve is now `_dump_json_resolve_data()`; the
  new fast path is `_fast_resolve_data()`/`_build_print_cmd()`.
- Tests: `test_fast_print_resolve_used_when_two_json_lines`,
  `test_resolve_falls_back_to_dump_json`.

## [4.43.3] - 2026-07-27

Fix: could not resolve/download a former-livestream YouTube VOD ("post-live
manifestless" mode).

- `extractors/ytdlp.py` — the `--dump-json` resolve was capped at a hardcoded
  60s. A YouTube VOD that was previously a livestream is served in "post-live
  manifestless" mode with no DASH manifest, so yt-dlp must **generate the full
  fragment list for every format** during `--dump-json`; for a multi-hour VOD
  this takes ~2 minutes and produces ~45 MB of JSON. Resolve timed out at 60s
  and reported "Failed to resolve stream URL", making the download impossible.
  (Repro: a 3h30m former livestream resolved in 112s.)
- The resolve timeout is now a configurable `YtDlpExtractor.resolve_timeout`
  (default 300s, config key `ytdlp_resolve_timeout`, clamped 30-1800s) applied
  to the primary resolve, the Cloudflare-impersonation retry, and the
  cookie-retry path. Normal videos still resolve in 1-3s. The timeout message
  is now actionable and names the config key.
- Tests: `test_resolve_uses_configurable_timeout`.

## [4.43.2] - 2026-07-27

Fix: every download crashed with `'StreamKeep' object has no attribute
'adv_override_badge'`.

- `ui/tabs/download_controls.py` — `_reset_adv_overrides()` (run from
  `_on_download()` on every "Download selected") called
  `win.adv_override_badge.setVisible(False)`, but that badge widget was removed
  when the per-download overrides "· Modified" indicator moved onto the
  `adv_overrides_action` menu label. The stale reference raised AttributeError
  and aborted the download before the worker could start. Reset now clears the
  marker via `adv_overrides_action.setText(OVERRIDES_LABEL)`.
- Added shared `OVERRIDES_LABEL` / `OVERRIDES_LABEL_MODIFIED` constants so
  `download.py`'s badge updater and the reset path can't drift apart again.
- `tests/test_subtitle_ui.py`: dropped the fake `adv_override_badge` QLabel that
  was masking this in tests; added `test_reset_adv_overrides_clears_fields_and_modified_marker`
  exercising the real reset path.

## [4.43.1] - 2026-07-27

Fix: live YouTube (and any yt-dlp) stream captured while in progress was
deleted on Stop.

- `workers/download.py` — the yt-dlp download path now knows when a segment is
  an in-progress **live capture** (`duration <= 0`). Previously, stopping a
  running live recording hit `_stream_ytdlp_download`'s cancel branch, which
  called `_remove_ytdlp_outputs()` and **deleted the entire file just
  recorded**. Since Stop (or the stream ending) is the normal way to finish a
  live capture, the user always ended up with nothing.
- For live captures, a user Stop or a non-zero yt-dlp exit (YouTube serves live
  as muxed HLS through ffmpeg, which exits 143 on terminate / non-zero when the
  stream ends) that still left a usable recording (> 64 KB) is now finalized as
  a completed download — mirroring the native-ffmpeg live path's
  keep-partial-on-stop behavior. Non-live (VOD) cancels still discard the
  partial so the resume path can restart cleanly.
- New `DownloadWorker._finalize_ytdlp_success()` helper; `_download_with_ytdlp`
  and `_stream_ytdlp_download` gained an `is_live` flag threaded from `run()`.
- Tests: `test_live_capture_kept_when_stopped`,
  `test_live_capture_kept_on_nonzero_exit`,
  `test_non_live_cancel_still_discards_partial`.

## [4.43.0] - 2026-07-21

Versatility Program: optional external download engines.

- **lux fallback engine for Chinese platforms (V25).** New `streamkeep/integrations/lux.py` and a `StreamKeep lux <url>` CLI subcommand route Bilibili / Douyin / Youku / iQIYI / Tencent / Weibo / AcFun URLs to an optional lux process, sharing the configured output folder and cookies (proxy is applied via `HTTP(S)_PROXY` since lux has no proxy flag). Supports `--output`, `--stream-format`, and `--info` (list streams without downloading). lux is never bundled — when absent, callers get a clear `go install` hint. Leading-dash URLs are rejected. +11 tests.
- **gallery-dl second engine (V10).** New `streamkeep/integrations/gallery_dl.py` routes image-gallery and social-media-post URLs (Twitter/X, Instagram, Pixiv, boorus, DeviantArt, Tumblr, Reddit galleries, and more) to an optional gallery-dl process, sharing StreamKeep's configured output folder, per-source download-archive, cookies, and proxy. New `StreamKeep gallery <url>` CLI subcommand with `--output`, `--rate-limit`, `--simulate`, and `--no-archive`. gallery-dl is never bundled — when absent, callers get a clear `pip install` hint instead of an opaque failure. Leading-dash URLs are rejected (no option smuggling). +14 tests.

## [4.42.0] - 2026-07-20

Reachability and resilience pass: wire dead features, harden the server, and improve YouTube survivability.

- **SponsorBlock aggregation delay for subscribed downloads (V31).** New `sponsorblock_deferred_start()` heuristic holds auto-discovered (subscribed-channel) YouTube VODs for a configurable number of hours *after their publish date* before downloading, so SponsorBlock crowd-sourced segments have time to accumulate. The delay reuses the queue's existing `start_at` scheduling (held items show their release time and dispatch automatically). Because it measures from publish date, already-old VODs and quality upgrades download immediately (built-in max-age cap). Off by default via a new `sponsorblock_delay_hours` setting in the YouTube section; only applies when SponsorBlock is enabled and the source is YouTube. +8 tests.
- **SSRF address policy extended to the REST/companion server (V30).** Factored the headless-scraper's address policy into a shared `streamkeep/net_guard.py` (`url_target_allowed`, `address_allowed`, `resolve_host_addresses`) and enforced it on user-submitted URLs at the `/send_url` and `/api/queue` endpoints. A URL that resolves to loopback, link-local, cloud-metadata (169.254.169.254 / 100.100.100.200 / fd00:ec2::254), or private-LAN space is now refused (`url_not_allowed`) before any fetch — closing an SSRF vector when the server is exposed on the LAN. DNS is resolved and every returned address checked, so a public hostname pointing at an internal IP is still blocked. A new `companion_allow_private_network` opt-in (default off) permits legitimate LAN targets and restarts the server on change. `scrape.py`/`image_fetch.py` now consume the shared module unchanged. +9 net_guard tests, +2 server SSRF tests.
- **Guided YouTube PO-token / SABR remediation (V26, deepens V19).** The YouTube capability layer now goes beyond detection: a new `youtube_pot_setup_guidance()` returns the exact `bgutil-ytdlp-pot-provider` install command and player_client advice, surfaced everywhere it matters. When a YouTube download fails with SABR/PO-token gating (the "storyboard images only" failure mode), the download worker now emits actionable `[HINT]` remediation lines instead of an opaque `[FAIL]`. The `youtube-health` CLI prints the install steps when no provider is present, and Settings → YouTube gains a "Test capability" button that runs the local health report and shows runtime/provider status plus remediation. New `looks_like_sabr_or_pot_failure()` heuristic and `pot_setup` field on the health report. +8 tests.
- **Storage-health disk monitor wired in (F67, V27).** The previously-dead `disk_monitor` module now runs in the main window: it polls the active download drive every 30s, shows remaining free space (color-coded green/amber/red) in the status bar, and warns/alerts through the notifications center as space crosses configurable thresholds. When "Auto-pause on critically low space" is enabled, hitting the critical threshold stops the active download and holds the queue (`_advance_queue` gate), then resumes automatically once space recovers. New Settings → Preferences controls (enable, warn-under GB, critical-under GB, auto-pause) and config keys (`disk_monitor_enabled`, `disk_warning_gb`, `disk_critical_gb`, `disk_auto_pause`), plus 6 dedicated tests.
- **Native OS notifications on notable events (F80, V28).** Wired the previously-dead `native_notify` module into the universal `_notify_center` event sink. When enabled (Settings → Preferences, off by default), download-complete, monitor-live, and job-failure events raise a desktop notification (Windows Toast / macOS / Linux) in addition to the in-app bell, falling back to the tray icon when no native backend is installed. Suppressed while the StreamKeep window is focused. New `native_notifications` boolean config key (import-validated) and 6 dedicated tests for the notification layer.

## [4.41.0] - 2026-07-19

Roadmap drain: rules engine, retention, REST spec, and test coverage.

- **Keep-last-N per-source retention (V17).** The lifecycle cleanup engine now supports a `keep_last_per_source` policy that keeps only the newest N recordings per source channel and recycles the rest (oldest first, favorites still exempt). Per-monitor-channel `retention_keep_last` values override the policy-wide default via a new `keep_last_map` (built from monitor entries by `keep_last_map_from_monitor`). Added a Settings → lifecycle spinner and config-schema validation. This completes V17 alongside the existing delete-after-N-days, delete-watched, and quality-upgrade redownload passes. 6 new tests. (VP-P2 V17)
- **Ordered rules engine (V15, Packagizer-class).** New `streamkeep/rules.py` evaluates user-defined rules top-to-bottom against a download context (site, uploader, title regex, duration bounds, media type) and folds matching actions — output folder, filename/argv template, post-processing preset, quality, per-job proxy, priority, auto-start — into the job. `all`/`any` match modes, `stop`-on-match, fail-closed regex handling, and last-write-wins accumulation. Wired into the headless queue's `enqueue` path (rules fill gaps without clobbering caller-set overrides), and the executor now honors a rule-set per-job proxy over the global proxy. Rules persist as plain dicts under the `rules` config key. 23 tests. (VP-P2 V15)
- **Dedicated test coverage for previously-untested QThread modules.** Added `tests/test_monitor.py` (schedule-window inclusion/exclusion, midnight-wrap, day-mask, imminent-stream escalation, and the round-robin in-flight/interval dedup guards), `tests/test_chat_readers.py` (Twitch IRC parsing + PING/PONG + cancel loop over a fake socket; Kick Pusher envelope parsing + pusher:ping keepalive + drop rules over a fake WebSocket), and `tests/test_finalize.py` (FinalizeWorker step planning, chat-VOD detection, podcast-feed resolution, output-size labels, and interrupt flag). +44 tests, no production changes. (2026-07-18 P2)
- **OpenAPI 3.1 spec for the REST server.** The local server now publishes its full contract at `GET /api/spec` (unauthenticated — API shape only, no data), generated in-process from a single source of truth in `streamkeep/openapi.py`. A consistency test asserts the documented operation set is identical to the server's dispatch table, so the spec can never drift from the implementation. Enables Swagger UI/Redoc rendering and generated third-party clients. (2026-07-18 P2)

## [4.40.0] - 2026-07-18

Architecture refactor and polish pass.

- **Immutable DownloadJobSpec replaces mutable property-bag pattern.** All seven download construction sites (GUI single/queue/VOD/monitor, CLI, headless, resume) now build a frozen `DownloadJobSpec` dataclass and call `DownloadWorker.from_spec(spec)`. The spec supports schema-versioned serialization (`to_dict`/`from_dict`), excludes secrets from persistence, rejects unsupported future versions, and eliminates cross-surface option drift. 10 new tests cover the spec contract.
- **Sentence case normalization.** Swept every multi-word button label and section title across Download, Monitor, History, Storage, Settings, and Analytics to use consistent Sentence case. Translation catalogs regenerated.

## [4.39.0] - 2026-07-18

Security, reliability, and UX hardening pass driven by the 2026-07-18 research audit.

- **Gate aria2c routing to direct HTTP only.** yt-dlp 2026.07.04 removed aria2c support for HLS/DASH downloads (CVE-2026-50574). StreamKeep now checks the source URL before applying `--downloader aria2c` and falls back to native `-N` concurrent fragments for streaming manifest sources. CLI help text updated.
- **Raise Python minimum to 3.11.** Python 3.10 reaches end-of-life Oct 2026 and yt-dlp has raised its minimum. README badge, requirements comment, and project notes updated.
- **Fix SFTP host key verification (MITM fix).** Replaced raw `paramiko.Transport` with `SSHClient` that loads system known-hosts and enforces `RejectPolicy` by default. Optional trust-on-first-use via `sftp_trust_on_first_use` config key.
- **Cap gallery Range-request memory reads to 8 MB.** Prevents LAN-client OOM via unbounded Range requests. Multi-range requests now return 416 instead of raising ValueError.
- **Thread-safe gallery share registry.** `_shared` dict reads and writes are serialized behind a `threading.Lock`; iteration uses a snapshot copy. Prevents `RuntimeError` from concurrent HTTP-thread reads and UI-thread mutations.
- **Fix DB connection leaks in channel_stats and bandwidth.** All `channel_stats.py` DB connections now use `try/finally`. `BandwidthTracker` is lazily initialized on first access instead of at import time. `daily_history` also fixed.
- **Pre-download thumbnail preview.** After a URL is fetched, the Download tab shows a thumbnail (80x60) next to the title/metadata hero section when the source provides a `thumbnail_url`. Uses bounded SSRF-safe `image_fetch` (2 MB cap, 8s timeout) in a background QThread.
- **Guard PostProcessor.has_any_preset() with PP_LOCK.** Prevents read-during-write race when Settings UI checks preset state while FinalizeWorker is running.
- **WinGet community manifest.** Added `packaging/winget/SysAdminDoc.StreamKeep.yaml` template for WinGet distribution.
- **Test coverage.** 24 new tests covering scheduler speed tiers, notes CRUD/search, bandwidth tracking/caps, and channel stats. Coverage rose from 53.3% to 54.0%.

## [4.38.0] - 2026-07-17

- **YouTube health doctor & player-client strategy presets (V19).** Added a local, network-free YouTube capability report — `python StreamKeep.py youtube-health [--json]` and the Settings yt-dlp panel — aggregating yt-dlp version, JavaScript-runtime (Deno/EJS) readiness, PO-token provider presence, the active player-client strategy, and plain-language degraded-capability warnings. Added curated `player_client` strategy presets (Automatic, Web Safari, Android VR, TV, iOS, Mobile web, Resilient) selectable in Settings, per-download via `--youtube-client`, and honored across the GUI, CLI, and headless resolve/download paths (only applied to YouTube URLs). This is the single most effective knob when YouTube caps quality, demands sign-in, or a working download breaks after a server-side change.

## [4.37.0] - 2026-07-17

- **Automatic podcast sidecars at finalize.** When a podcast episode is downloaded from a browsed RSS feed (single-load, queue, or batch), StreamKeep now carries the originating feed URL through the pipeline and, at finalize, fetches the episode's transcript/chapter sidecars next to the recording via the existing bounded, hashed `sync_podcast_sidecars` module. Missing feed context, network errors, or an empty result are non-fatal. `VODInfo`/`StreamInfo` gained a `feed_url` field and the download-queue payload round-trips it.

- **Stable resumed-download speed readout.** `parallel_http_download` now credits already-complete (resumed) parts into the byte total and snapshots the speed/ETA baseline *before* starting the worker threads, so the first progress sample no longer spikes or reads zero depending on thread scheduling.

- **Crash-consistent backup restore.** `restore_backup` now writes a restore marker before the destructive activation phase and swaps `config.json` last. If the process dies mid-activation, the next startup (`finalize_interrupted_restore`, invoked from `init_db` before the database opens) rolls every file back to its `.pre-restore` copy, returning the config directory to its prior self-consistent state instead of a mixed new-DB/old-config mix. On clean completion the marker and `.pre-restore` copies are removed. The docstring now states the real per-file-atomic + marker-rollback guarantee.

- **Live credential and cookie validation.** Added `streamkeep/credential_check.py`: non-downloading probes that validate a Twitch OAuth token (official `id.twitch.tv/oauth2/validate`), a YouTube Data API key (minimal `videos.list`), and the imported cookie profile (local Netscape parse with per-cookie expiry). Each returns a structured result — valid, expired/revoked, insufficient-scope, rate-limited, unsupported, network-error, or no-credential — recording only redacted metadata, never the secret. Kick reports unsupported (no public introspection endpoint). Surfaced through a cancellable **Check** button in Settings → Platform Accounts / Cookies and a new `python StreamKeep.py credentials [platform] [--json]` command (exit 1 on invalid/expired/insufficient-scope).

## [4.36.5] - 2026-07-17

Audit hardening pass — correctness, security, and accessibility.

- **Live-capture never hangs callers.** A live recording that ended on a non-zero ffmpeg exit previously emitted neither `all_done` nor `error`, so the headless CLI blocked on `app.exec()` forever and GUI/queue jobs stuck in the "downloading" state. It now finalizes a usable partial recording (`all_done`) or surfaces a terminal `error` when nothing was captured; the CLI also always quits when the worker thread ends.
- **Extractors survive null API fields.** SoundCloud/Reddit resolves crashed (`None / 1000`) when the platform returned an explicit `"duration": null`, silently degrading to the yt-dlp fallback. Kick VOD pagination stopped early — dropping every later page — when a full API page contained a source-less VOD.
- **Secrets fail closed.** `unprotect()` no longer returns the stored ciphertext/reference string as plaintext when a `dpapi:`/`kr:`/`b64:` value can't be decrypted (wrong machine/user, missing keyring), which previously let a garbage value be cached and used as a token or URL.
- **Deleted-VOD recovery validates the channel** (3–25 word chars) before building request paths, rejecting malformed input early and avoiding hundreds of junk HEAD probes.
- **Storage import keeps its dates.** Fixed an operator-precedence bug that dropped a valid `downloaded_at` timestamp whenever a recording's newest mtime was falsy.
- **Size-cap cleanup is no longer over-eager.** The library size rule summed exempt favorites into total usage, so it could recycle every non-favorite recording and still leave the library over cap. It now credits bytes already freed by the age/watched rules and skips pruning entirely when exempt favorites alone exceed the cap.
- **Destructive actions confirm first.** Clear History (wipes the library DB), Clear queue, Clear all platform tokens, and Clear cookies were one-click irreversible data loss — each now prompts, consistent with the existing Storage/import confirmation pattern.
- **Accessibility.** Darkened the light theme's secondary/hint text (`subtext0`/`muted`) to meet WCAG AA (4.5:1) on the primary surfaces, and gave the clip-range glyph buttons (+, −, ▲, ▼) accessible names so screen readers announce the action instead of the raw symbol.
- **Microcopy.** Aligned the Resolve button's tooltip with its label and fixed the clip export button's runtime case-flip ("Export clip" → "Export Clip").

## [4.36.4] - 2026-07-17

- Brought the Download workspace into close parity with the new production mockup: compact global navigation and intake controls sit higher in the viewport, the queue is a dense responsive transfer table with selection and grouped commands, and the Activity pane presents calm timestamped events instead of status-card noise. The same typography, spacing, hierarchy, and responsive density pass carries through Monitor, History, Analytics, Storage, and Settings.
- Fixed a startup access violation in `Qt6Gui.dll`. Transcript indexing can finish on a Python worker thread, so the shared activity logger now marshals every UI mutation through a Qt signal onto the GUI thread; the regression is covered by an offscreen background-thread fixture.
- Replaced the dated cloud/filmstrip branding with a compact StreamKeep ribbon/download mark. The checked-in PNG master now deterministically derives multi-resolution Windows ICOs, the Flatpak icon, and every browser-companion icon; the README and companion package use the same identity.

## [4.36.3] - 2026-07-17

- Reworked the six-page desktop hierarchy against a second image-generated production mockup. Cozy type is now 16px with 28px page titles, primary work areas use quiet tonal depth instead of outlined boxes, and borders are reserved for fields, focus, and hairline structure. Download separates the URL/Paste/Fetch composer from borderless Import/Advanced commands; Monitor, History, and Storage lift their data surfaces higher; idle archive maintenance collapses until requested; Analytics charts provide deliberate empty states; and Settings adds category shortcuts above its long-form controls. A fresh six-page offscreen render plus GUI, accessibility, and visual-token assertions verify the implementation.

## [4.36.2] - 2026-07-17

- Prevented duplicate desktop launches from racing over the shared SQLite library during startup. The GUI now acquires a non-blocking, stale-process-aware profile lock before opening configuration or database state; a second click exits cleanly instead of surfacing a misleading `database is locked` crash dialog. CLI and protocol-handler commands remain independent.

## [4.36.1] - 2026-07-17

- Tightened the image-generated desktop direction into a second implementation pass. Download now uses one compact URL/Paste/Fetch/Import/Advanced command row; scan, queue, LAN, playlist, recovery, clipboard, time-range, download settings, and expert overrides are grouped into one accessible menu. The composer no longer paints a container behind the controls, default per-job settings stay collapsed until requested, and the available-media/queue work surface starts materially higher. A second six-page offscreen audit, GUI/accessibility fixtures, and updated English/Spanish catalogs cover the refinement.
- Reworked the desktop visual system against a generated production mockup and a six-tab offscreen before/after audit. Default body type is now 15px, controls use a restrained six-pixel radius, open tables and hairline dividers replace nested outlined containers, metric cards collapse into single-line operational facts, redundant eyebrow labels and settings metadata are removed, page copy is concise, and shell/page spacing brings primary controls materially higher in every viewport. Download, Monitor, History, Storage, Analytics, and Settings now share compact headers and a quieter hierarchy; status remains text-led instead of pill-led. Theme/density screenshot matrices, high-contrast/200%-scale coverage, and explicit token assertions guard the new system.
- Added a single dry-run-first Archive Maintenance workflow to Storage. Its cancellable background preview classifies orphaned disk folders, import candidates, missing library rows, and unique moved-recording relinks while reporting database integrity, the latest backup, free-space warning/critical thresholds, note-sidecar coverage, and the exact History FTS/planner-statistics rebuild effect. The immutable preview is persisted across restart and protected by a full library fingerprint. Apply accepts only individually checked action IDs, leaves destructive missing-row cleanup unchecked by default, refuses stale plans, creates a secret-free backup before mutation, commits between cancellation points, preserves recording sidecars, and writes every outcome to a durable append-only JSONL audit. Offscreen GUI and integration tests cover preview persistence, imports, relinks, missing rows, in-place staleness, pre-change backup, cancellation, audit records, and sidecar preservation.
- Activated the shared visual system across the desktop. Settings now exposes persistent System/Dark/Light/High Contrast themes, Compact/Cozy/Spacious density, and theme-default or named accents; changes refresh open widgets immediately. The token/component stylesheet now owns typography, spacing, control height, radii, focus, interactive states, table rows, template feedback, and contrast-aware accent foregrounds, replacing the duplicate legacy stylesheet and static appearance overrides. Density changes release constrained text controls before they clip. Offscreen acceptance covers every theme/density combination as a screenshot-hash matrix, persistent GUI settings, high-contrast contrast ratios, and the existing separate 200%-scale overflow path.
- Completed desktop internationalization across the hand-authored Qt shell, dialogs, and player surfaces. A deterministic extractor now inventories 1,274 UI/player messages into TS catalogs before lrelease compilation; frozen builds ship matching QM assets. English, core-workflow Spanish, and a layout-expanding pseudo locale retranslate open windows without restart, while scoped translatable dialog/widget bases cover surfaces opened after a language switch. Status and History plural messages use explicit contexts, model/view headers translate, and offscreen tests cover live shell/dialog switching, singular/plural Spanish, catalog drift, packaged assets, and pseudo-locale clipping detection.
- Replaced unbounded archive tables with scalable Qt model/view paths. History now opens on a stable SQLite snapshot, fetches 100 newest-first rows at a time through keyset pagination, filters metadata through an FTS5 external-content index, and computes shell/history/analytics/global-search/duplicate checks with bounded indexed queries instead of loading every row into Python. Storage uses a source model plus platform/channel proxy filtering, and its filesystem scan runs in an interruptible background thread. History and Storage request thumbnails only for visible/near-visible rows; scroll/filter changes prune pending work and cancel stale workers. The headless state snapshot caps embedded history to 100 rows and reports the total separately. Acceptance fixtures seed 100,000 History rows and 100,000 Storage groups, verify bounded model population, indexed query plans, stable selection across `fetchMore`, snapshot isolation, FTS filtering, aggregate results, and stale thumbnail cancellation.
- Restored keyboard and assistive-technology operation across the desktop shell. All tables retain strong focus and keyboard navigation; Enter toggles VOD, media-track, and segment choices; all six tabs expose checked/current state and Ctrl+1–6 focus their primary workflow; Ctrl+L focuses the source URL. Shared helpers now provide explicit names/descriptions, buddy labels, textual async status severity/revisions, and named progress controls. Clip timeline/waveform and the weekly schedule have arrow/Space/Enter keyboard equivalents, analytics/storage canvases expose text summaries, and focus outlines cover buttons, tables, lists, checks, radios, and sliders. Offscreen acceptance covers the main workflows and dialogs plus a separate 200%-scale high-contrast process with reachable overflow.
- Made release contents reproducible and auditable: exact hash-checked runtime and PyInstaller build locks now feed a clean Python 3.12 environment; the release gate compares two frozen executables, emits a runtime-only CycloneDX SBOM, third-party license inventory, and hashed release manifest, and runs the hidden three-fixture artifact smoke suite. Flatpak now targets the supported KDE/PyQt 6.10 base with a Python 3.13 Linux-specific hash lock and generated offline source manifest instead of ambient version ranges and an invalid `pip install .` path.
- Split Download-tab orchestration into cohesive mixins while retaining the existing `StreamKeep` method contract. `DownloadQueueMixin` now owns persistent queue CRUD, scheduling, durable fetch/download transitions, failure retry/discard, queue-complete power actions, resume attachment, and queue preflight helpers; `DownloadVodMixin` owns VOD paging, selection, batch fetch, and bounded batch completion; `DownloadFinalizeMixin` serializes finalize tasks and owns duplicate/history/metadata persistence; `DownloadSingleMixin` owns fetch, segment, transfer, playlist expansion, page scan, and companion handoff. Shared advanced-option/media-track helpers moved to `download_controls.py`, leaving `download.py` as a 1,066-line builder/composition surface instead of a 5,087-line god-file.
- Split browser-companion trust state, lifecycle cleanup, local-server snapshots, and update orchestration from the Settings god-file into `SettingsCompanionMixin`, preserving inherited `SettingsTabMixin` compatibility for existing callers and tests.
- Split cookie discovery/import, account tokens, proxy pools, global option persistence, theme, and language handlers into `SettingsPreferencesMixin`, keeping the Settings builder and its inherited public handler contract unchanged.
- Split yt-dlp template and hook editors, manual conversion, and validated configuration export/import into `SettingsToolsMixin`. `settings.py` is now a section-builder/composition surface backed by three focused handler mixins rather than one 1,590-line handler class.
- Split resume-sidecar discovery, resumable worker construction, and serialized background-job dispatch from `main_window.py` into `MainWindowJobsMixin`, reducing the shell's cross-cutting transfer responsibilities while preserving the `StreamKeep` API.
- Added first-class coverage measurement with `pytest-cov`. Full-suite runs now report missing lines and enforce a 47.5% floor against the measured 48.49% baseline (30,814 statements); development dependencies are declared separately in `requirements-dev.txt` so test tooling does not become an application runtime dependency.
- Added a release gate for product capability claims. `streamkeep/capabilities.py` now records each shipped capability's GUI/CLI/REST entry point and the integration test that exercises it; the gate rejects missing paths, missing tests, duplicate claims, or undocumented claims. The README no longer presents gallery/RSS publishing, uploads, plugins, LLM summaries, smart thumbnails, native notifications, or recording-note authoring as shipped; those existing but unreachable modules are explicitly experimental until their roadmap wiring lands.
- Made `streamkeep/__init__.py::VERSION` the application version source. A packaging helper now checks or stamps the README badge, MSIX identity, Flatpak release metadata, and roadmap baseline; both portable and MSIX builders stamp those targets before packaging, and the release test gate fails on any drift.

## [4.36.0] - 2026-07-17

- Added an opt-in YouTube live-chat replay download trigger. The replay normalizer and finalize ingest already flattened any `*.live_chat.json` into the shared chat model, but nothing made yt-dlp fetch it; a new "Capture YouTube live-chat replay" Settings toggle and a `--youtube-chat` CLI flag now fold `live_chat` into yt-dlp's `--write-subs`/`--sub-langs` for YouTube sources only (added to existing subtitle languages, or fetched standalone without embed/convert). The captured replay is normalized by the existing pipeline at finalize; a non-YouTube URL ignores the flag and unavailable replay is non-fatal. The option persists to config and the resume sidecar. Added worker command-construction fixtures (fold-in, standalone, non-YouTube, default-off) and an offscreen Settings round-trip.
- Surfaced the bilingual-subtitle merge and LRC-export post-processing options in Settings. The already-implemented `pp_bilingual_*`/`pp_lrc_*` transforms had no GUI controls; Settings → Post-processing now exposes an enable toggle plus primary/secondary language fields and an SRT/ASS format choice for bilingual merge, and an enable toggle plus language field for LRC export. Values drive the existing PostProcessor step and now round-trip through config (they previously loaded but were never written back on save). Verified with an offscreen Settings round-trip.
- Added Podcast Namespace transcript/chapter sidecar acquisition. A new `streamkeep/podcast_sidecars.py` discovers `<podcast:transcript>`/`<podcast:chapters>` references (URL + MIME type + language) for a given episode from its RSS feed, downloads each into a hashed sidecar next to the recording through the shared SSRF-safe fetch policy, records a `<base>.sidecars.json` manifest, and skips re-download when a refreshed file's SHA-256 is unchanged (idempotent refresh). The written `.vtt`/chapter-JSON files feed the existing WebVTT and `parse_podcast_chapters_json` parsers; malformed or absent metadata is non-fatal. Exposed through a new `podcast-sidecars <feed> <enclosure> <out-dir>` CLI command. Refactored `image_fetch` to expose a generic `fetch_url_bytes` (bounded, address-pinned, redirect-revalidating) so images and sidecars share one connection policy. Added discovery, enclosure-matching, hashing/refresh, non-fatal-failure, manifest, and CLI fixtures.

## [4.35.0] - 2026-07-16

- Added a `streamkeep://` protocol handler, a browser bookmarklet, and per-user Windows registration so a page or media URL can be sent to StreamKeep with one click. A new `streamkeep/protocol.py` parses the URI (percent-encoded `?url=` query, `download/<URL>`, `//<URL>`, and bare `streamkeep:<URL>` forms), validating the inner target as a credential-free HTTP(S) URL and dropping unknown quality hints; `run_cli` translates an incoming `streamkeep://` argv into a download of the validated target (also fixing `has_cli_args`/the launcher so `import-har` and the new commands route to the CLI instead of the GUI). Added `register-protocol`/`unregister-protocol` (reversible HKCU writes, no elevation) and `bookmarklet` CLI commands; the registry plan and URI parsing are separated from any OS mutation and unit-tested. An iOS Shortcut can call the same URI. Added parsing, bookmarklet, registry-plan, and CLI-dispatch fixtures.
- Added an optional queue-complete power action. When the whole download queue drains, StreamKeep can now notify, run a `queue_complete` hook, lock the workstation, sleep, hibernate, or shut down — chosen from a new Settings dropdown and defaulting to "do nothing". Destructive actions (sleep/hibernate/shutdown) are issued with a native cancellable grace period (Windows `shutdown /t 60`; abort with `shutdown /a`) and the scheduled command is logged. A dedicated `streamkeep/power.py` module separates command construction from execution (platform-parameterized argv for Windows and Linux) so the mapping is fully unit-tested without ever suspending the machine; the queue fires the action once per drained batch via an armed edge-trigger. Added command-construction, dispatch (notify/hook/dry-run destructive), and offscreen Settings round-trip fixtures.
- Added HAR (HTTP Archive) import to recover media and streaming-manifest URLs from a browser network capture. A new bounded parser (`streamkeep/har.py`) scans a `.har` export's request/response entries, classifies each as a streaming manifest (`.m3u8`/`.mpd` or an HLS/DASH/Smooth content type), a whole-file media container, or a segment, and returns a deduplicated link table ordered manifests-first. Segment URLs are collapsed away when a manifest is present (yt-dlp/ffmpeg rebuild them), non-GET and non-HTTP(S)/`blob:`/`data:` requests are ignored, and only the replay-relevant request headers (Referer, Origin, User-Agent, Cookie, Authorization) are carried forward — HTTP/2 pseudo-headers and control-character-bearing values are dropped so each survives as safe yt-dlp `--add-header` argv. Exposed through a new `import-har` CLI command (plain URL list, `--json` link table, `--headers` add-header preview, `--include-segments`) and wired into the desktop "Import URLs" dialog, which now accepts `.har` files and extracts the media URLs in place. Added parser, header-argv, and CLI fixtures.
- Added optional aria2c external-downloader routing for yt-dlp direct downloads with mandatory URL sanitization (CVE-2026-50574). A new typed control (`validate_external_downloader_options`) owns yt-dlp's otherwise-reserved `--downloader`/`--downloader-args` and exposes only a fixed set of aria2c performance knobs (connections-per-server, splits, minimum split size); the argv is generated solely from validated numeric/rate values, so no aria2c control/RPC/exec/path option can be injected through this surface. When routing is active, the source URL is gated at the input boundary by `sanitize_download_target_url`, which enforces an HTTP(S)-only, control-free, whitespace-free, credential-free, non-dash-leading URL — aria2c would otherwise read a leading-dash token as an option or a newline/space as an additional download target. Wired reachably through the desktop download/queue/monitor paths, the resume sidecar, the headless service, and new `--external-downloader aria2c` / `--aria2c-connections` / `--aria2c-splits` / `--aria2c-min-split-size` CLI flags; a stale or hostile config value disables routing instead of raising. Added validation, URL-sanitization, apply-helper, and worker-command fixtures.

## [4.34.0] - 2026-07-16

- Normalized YouTube live-chat replay into the shared chat pipeline. A new parser flattens yt-dlp `*.live_chat.json` replay envelopes into the same message shape the Twitch/Kick readers produce (timestamp, replay offset, nick, message with emoji shortcuts, and owner/moderator/member flags), handling text, superchat/sticker (amount-prefixed), and membership renderers while skipping deletions and truncated lines non-fatally. Regex/user filters and CSV export are included, and a finalize step ingests any downloaded replay into `chat.jsonl` (+`chat.csv`) so the existing spike-detection, highlight, and ASS-render tools consume YouTube replays unchanged. Added fixtures covering normalization, flags/emoji, filters, superchats, CSV, spike consumption, and directory ingest.
- Added bilingual subtitle merge and LRC export as subtitle post-processing. A new cue model plus `.srt`/`.vtt` parsers (spec timestamp forms, markup stripping, malformed-cue isolation) feed a deterministic overlap-based bilingual merge — the primary track anchors timing and order while overlapping secondary-language cues stack beneath — rendered to either a stacked SRT or a two-style ASS that keeps each language's placement. LRC export emits validated monotonic `[mm:ss.xx]` timestamps for audio listening. A PostProcessor step (gated by `pp_bilingual_subs`/`pp_lrc_export` config, run in finalize) writes `subtitles.<a>-<b>.bilingual.srt/.ass` and `lyrics.<lang>.lrc` from downloaded sidecars, always preserving originals; missing tracks are non-fatal. Added parser/merge/LRC/ASS and end-to-end PostProcessor fixtures.
- Corrected WebVTT transcript indexing and imported Podcast Namespace metadata. The `.vtt` parser is now W3C-spec-correct: it accepts both minute-only (`MM:SS.mmm`) and hour (`HH:MM:SS.mmm`) cue timestamps (minute-only cues were previously dropped entirely), ignores cue identifiers and trailing cue settings, strips inline markup (voice/class/italic/timestamp spans), and isolates malformed cues instead of losing the file. Transcript-JSON indexing now also accepts the Podcast Namespace shape (`segments[].body`/`startTime`/`endTime`/`speaker`), and a new `parse_podcast_chapters_json` reads the `application/json+chapters` format into the player chapter model (ordered, `toc:false` excluded, end times filled from the next chapter). Added WebVTT and podcast transcript/chapter fixtures including an indexed-hit-jumps-to-correct-offset check.
- Made HLS parsing and resume identity standards-complete (RFC 8216). The master-playlist parser now surfaces `FRAME-RATE`, `VIDEO-RANGE` (SDR/PQ/HLG HDR), and both peak `BANDWIDTH` and `AVERAGE-BANDWIDTH` on each variant and video track, and the download quality picker shows frame rate and an HDR tag. Added a typed media-playlist parser (`parse_hls_media_playlist`) that tracks `EXT-X-MEDIA-SEQUENCE`/`EXT-X-DISCONTINUITY-SEQUENCE` per segment, captures `EXT-X-GAP`, `EXT-X-BYTERANGE`, and `EXT-X-PROGRAM-DATE-TIME`, distinguishes VOD (`EXT-X-ENDLIST`) from live, and isolates malformed `EXTINF` entries. Resume state now records the media playlist's strong validator, media sequence, discontinuity sequence, and segment count, and a `resume_identity_matches` helper forces a full restart when a live window has rolled past our segments, crossed a discontinuity, or changed validator. Added fixtures/tests for alternate renditions, HDR/FPS variants, live rollover, gaps, discontinuities, and malformed playlists.

## [4.33.0] - 2026-07-16

- Removed the insecure base64 secret-write path: `secrets.protect()` no longer accepts `allow_insecure_fallback` and always fails closed when no OS-level secure backend (DPAPI/keyring) is available, so a reversible `b64:` value can never be written. `unprotect()` still recognizes legacy `b64:` values on read so pre-existing configs migrate forward.
- Isolated plugin imports from the global namespace: `load_plugin` no longer permanently appends the plugin's parent directory to `sys.path` (which exposed sibling plugins and risked shadowing). The plugin's own directory is now added only for the duration of its execution — appended at the end so it cannot shadow stdlib/app modules — and always removed afterward, with intra-package imports served by the module spec's search locations.
- Hardened three smaller surfaces: clipboard URL capture now trims prose punctuation the matcher over-captures (trailing `.`, `,`, `!`, and unbalanced brackets) while preserving balanced brackets inside links; local web-gallery share IDs use 128 bits of entropy (`secrets.token_hex(16)`) instead of a 48-bit truncated UUID so LAN-bound share URLs are not brute-forceable; and FTP/SFTP uploads reject filenames containing control characters (notably CR/LF, which could inject a second FTP control-channel command) or invalid dot/path names. Added clipboard, gallery-entropy, and FTP-filename fixtures.
- Centralized every remote-image pull (metadata thumbnails, third-party BTTV/FFZ/7TV chat emotes) behind one bounded, SSRF-safe policy in `streamkeep/image_fetch.py`. Requests and redirects are HTTP(S)-only and credential-free, each hop re-resolves and re-validates the host, and the connection is pinned to a globally-routable address so a hostname cannot rebind to a loopback/private/metadata target mid-request. Payloads are byte- and time-bounded with partial-file cleanup, accepted only when their magic bytes match an allowlisted raster format (the wire Content-Type is ignored), and decoded under Pillow decompression-bomb-as-error handling with pixel and animation-frame caps. Thumbnail downloads and emote decoding now flow through this policy, and the emote cache enforces a 256 MiB quota with oldest-first eviction. Added fixtures for spoofed types, oversized bodies, redirects, animated-frame/pixel caps, SSRF loopback rejection, and atomic no-partial-file writes.
- Replaced raw `shell=True` event hooks with structured, bounded actions. A hook is now an executable plus an explicit argument array, executed with no shell, a minimal allowlisted environment (unrelated env/secret variables are withheld), discarded stdout, capped stderr capture, a wall-clock timeout, and process-tree termination so a runaway hook leaves no orphaned descendants. Lifecycle context stays data-only in `SK_*` environment variables and is never concatenated into the command line. Legacy shell-string hooks are retained but disabled and never run until re-created as a structured action; the Settings editor authors executable/arguments per event, shows a redacted preview of any legacy command, and persists each action immediately. Config import already quarantines hooks as disabled and cannot activate them implicitly. Reworked the hook test suite to cover argv construction, environment minimization, legacy refusal, invalid-hook rejection, real bounded-stderr execution, and timeout termination.
- Connected the browser clip-range handoff end to end. The extension already sent validated start/end bounds and the local companion server emitted them, but the desktop never listened; the GUI now binds `clip_received` and prefills the download crop range (HH:MM:SS) and URL before the fetch that immediately follows reads them, so a paired browser action opens a ready-to-clip workflow exactly once. Added a signal-level server test and a GUI prefill assertion.
- Hardened `.skbackup` restore into a validate-before-activate operation. Backup contents now extract to a private staging directory and are checked before any live file is touched: metadata must be present and parseable, each SQLite database is scrubbed of auth state and then validated under a `trusted_schema=OFF` connection with `quick_check` and `foreign_key_check`, databases with a schema newer than this build are refused, and the transcript FTS index is rebuilt. Only after every staged file validates are the files swapped into place atomically; any failure leaves the current config directory byte-for-byte intact and returns a redacted report. Added fixtures for corrupt databases, newer-schema refusal, unparseable metadata, mixed valid/invalid file sets, and FTS rebuild.
- Added an expert clear-key override for mis-declared non-DRM HLS playlists. The Download Advanced panel accepts an authorized AES-128 key URI or a 32-hex-digit literal key plus an optional 1–32-digit hex IV, both masked and validated (HTTP/HTTPS-only URIs with no user-info or fragment). The value maps to yt-dlp's native `generic:hls_key=URI|KEY[,IV]` extractor argument and routes the job through the yt-dlp downloader. The key is strictly job-local: it is never written to config, SQLite, logs, or resume sidecars, and enabling it disables the resume sidecar for that job. The override is refused for non-HLS sources and for custom multi-track selections (load the media playlist directly instead); crop/`--download-sections` still applies. Offline fixtures cover key/URI/IV normalization and command construction.
- Added selectable native HLS/DASH media tracks. HLS masters now retain alternate audio, subtitle, and closed-caption rendition groups; DASH manifests expose every video, audio, and subtitle Representation with language, codec, role, and stream identity. The Download media-track table supports one video plus multiple audio/subtitle selections, preserves them across resume, and applies safe defaults to batch, queue, monitor, CLI, and headless jobs. Standalone FFmpeg exports use the same explicit maps; offline fixtures cover alternate renditions, live rollover/discontinuity, multi-period DASH, and multi-representation manifests, and a real mux preserved two language-tagged audio streams.
- Added secure named yt-dlp argument templates with a one-argv-element-per-line editor, Download Advanced/CLI/queue/monitor attachment, deny-listed executable/config/link boundaries, and exact resume-by-name behavior. Template contents live in the secure credential backend rather than config, SQLite, or resume sidecars. Each prepared job can copy a standalone yt-dlp or FFmpeg command that retains its cookie selector and structured headers; a guarded real HLS fixture reproduced successfully from the export itself.
- Added a complete yt-dlp transfer-depth matrix across Settings, per-download Advanced controls, CLI, headless service, monitor capture, queue, and resume state: concurrent fragments, retry and fragment-retry counts, retry backoff, unavailable-fragment policy, throttling thresholds, live-from-start, scheduled-stream polling, and independent chapter/metadata/thumbnail embedding. Command-plan tests cover every flag, and a real five-fragment HLS fixture completed with `-N 4`.
- Sandboxed Playwright page scans and replaced direct Chromium networking with a bounded route broker that pins TLS connections to stable, globally routable DNS answers. Initial URLs, redirects, subrequests, and static-scraper fallback now reject private/special addresses and DNS changes; browser DNS, WebSockets, QUIC, service workers, downloads, and unbounded media responses cannot bypass the broker. Added a visibly labeled one-scan LAN override limited to RFC1918/ULA destinations while loopback, link-local, metadata, multicast, unspecified, reserved, and IPv4-mapped IPv6 targets remain blocked.
- Centralized every application SQLite connection behind a WAL-reset-aware policy. Fixed runtimes use WAL; vulnerable source runtimes remain usable with enforced rollback journaling and an actionable degraded diagnostic; vulnerable frozen runtimes fail closed. Windows release builds now acquire a pinned SHA3-verified upstream SQLite 3.53.3 DLL, replace PyInstaller's inherited DLL, and verify the frozen runtime during the hidden startup contract. Concurrent writer/checkpoint/backup and interrupted-transaction tests cover queue durability, foreign keys, and `quick_check` integrity.
- Raised the enforced yt-dlp security floor to 2026.07.04 across source requirements, the runtime capability registry, Flatpak packaging, and release documentation. Structured raw-argument templates now reject shortcut/link writers and secondary command/config execution boundaries. Frozen startup checks require the fixed yt-dlp runtime plus an exact yt-dlp-ejs match, and the PyInstaller spec now bundles EJS distribution metadata so packaged capability validation remains fail-closed and auditable.

## [4.32.0] - 2026-07-16

- Added playlist/channel range and sync controls to Advanced: yt-dlp item expressions, after/before dates, match filters, maximum download counts, and private per-source incremental archives with break-on-existing. Expansion passes the policy through its bounded background probe, queued yt-dlp items update the archive after success, resume retains it, and monitor VOD subscriptions use the same per-source archive. A real four-entry fixture downloaded exactly range 2–3; its second sync left both files unchanged and reported the archived entries.
- Replaced the fixed SponsorBlock removal set with a 13-category mark/remove matrix in Settings and Advanced per-download controls, with mark-only enforcement for highlights/community chapters and an optional HTTPS API override. CLI, headless, queue, batch, and resume paths preserve the policy. The generated yt-dlp command keeps mark/remove sets distinct, and a loopback SponsorBlock integration fixture produced an ffprobe-confirmed `[SponsorBlock]: Sponsor` chapter in a real MKV download.
- Added a complete yt-dlp subtitle workflow. Resolve results now expose bounded manual/automatic language metadata; Advanced offers a source-fed language multi-select, auto-caption toggle, SRT/VTT/ASS conversion, embedded or sidecar delivery, and an explicit per-download disable. Settings provide configurable defaults instead of a fixed English command, the CLI adds matching subtitle flags, and resume/headless/queue/batch paths preserve the policy. A real two-language HTML5 source produced English and Spanish SRT sidecars, then ffprobe-confirmed embedded `eng`/`spa` subtitle streams in MKV.
- Added per-download yt-dlp format and output control in the Advanced panel and CLI: verbatim `-f` specifications, AV1/resolution/small-file `-S` presets, MP4/MKV/WebM/original video containers, and best/MP3/M4A/Opus/FLAC/WAV audio extraction with encoder quality. yt-dlp outputs now use an extension template and verify the actual produced container instead of assuming `.mp4`; resume sidecars preserve the complete option set, and audio files are visible to integrity, storage, history, gallery, thumbnail, and media-server discovery. A real local yt-dlp run was ffprobe-verified as Matroska video and MP3-only audio with the supported FFmpeg 8.1.2 toolchain.
- Replaced direct LAN bearer control with an explicit loopback-only trust boundary. The desktop master token is now 256-bit and persisted only through the operating-system credential backend; clients exchange five-minute one-use codes for scoped, origin-bound, expiring tokens and hold extension access in browser session memory. Optional remote control requires an exact HTTPS reverse-proxy origin and verified forwarding headers. Every mutation now enforces JSON, approved Host/Origin/fetch metadata, timestamp freshness, and cryptographic nonce replay protection; token rotation revokes all clients, malformed/duplicate Host authorities fail closed, and early POST rejections drain bounded bodies so Windows clients reliably receive the error response.
- Replaced hash-only self-updates with a publisher-authenticated release chain. Stable releases now carry a canonical offline-signed manifest, monotonic sequence, exact repository paths, signed size/digest metadata, and Authenticode-verified portable EXE/MSIX assets bound to the installed publisher certificate. Installation uses an atomic last-known-good watchdog, exact SQLite/config snapshots, post-initialization health confirmation, automatic binary/state rollback, and a persistent recovery log surfaced in the relaunched app.
- Added a single fail-closed runtime capability registry for yt-dlp/EJS/JavaScript, Pillow, curl, FFmpeg, and ffprobe. It records exact path, semantic version, provenance, and enabled operations; enforces security floors across download, HTTP, inspection, thumbnail, clip, post-processing, webhook, CLI, onboarding, Settings, and diagnostics paths; and provides repair guidance instead of executing vulnerable tools. Startup remains available in degraded mode without bypassing the registry, dependency checks are side-effect free, requirements carry the Python floors, and the Flatpak manifest builds FFmpeg 8.1.2 from a verified upstream archive.
- Replaced raw configuration import with a versioned, bounded schema and redacted diff review. Executable and outbound capabilities are quarantined independently, library/queue payloads and local secret handles are rejected, invalid files leave the active config untouched, and hook metadata remains bounded environment data instead of command interpolation.
- Unified configuration and account credentials behind secure-store references, with atomic migration from legacy plaintext and fail-closed saves when no secure backend is available. Normal config exports, diagnostics, logs, SQLite snapshots, and `.skbackup` create/restore flows now remove authentication state and signed URL credentials; cookies are excluded. Added explicit Argon2id + AES-256-GCM portable-secret export/import with wrong-password and tamper rejection, available through the headless `backup` CLI without password arguments.
- Replaced headless server URL acknowledgements with durable SQLite jobs that execute through the shared fetch, download, metadata, and integrity-finalization workers. Queue/retry acknowledgements now return stable job IDs; status, job, library, and failure reads reflect persisted state; cancellation is terminal; and interrupted fetch/download/finalization work is re-queued on restart.
- Added a hidden source/frozen startup contract with atomic JSON readiness markers plus an artifact harness for empty, legacy-migrated, and populated libraries. The check proves SQLite/history/thumbnail initialization, bundled yt-dlp availability, one application window, expected PyInstaller bootloader/runtime isolation, no re-entry fanout, no visible windows, and bounded clean exit. It also fixed fresh installs reading monitor tables before schema initialization.
- Bound parallel HTTP resume parts to hashed URL identity, exact byte layout, content length, and a strong ETag or valid Last-Modified validator. Range requests now send `If-Range`, reject non-exact `206 Content-Range` responses, invalidate unverifiable/stale parts, and verify advertised `Content-Digest`/`Repr-Digest` values before publishing the output.
- Fixed `db` and `snapshot` launcher dispatch so source and frozen CLI invocations stay headless, and made `--config-dir` bind config, log, crash, and database paths before stateful imports. Added isolated subprocess coverage and hidden one-file artifact smoke verification.

### 4.31.7 (2026-07-16) — Broader site coverage & resilience

Probed a spread of popular video/livestream hosts (YouTube, TikTok, Vimeo, Dailymotion, Streamable, PeerTube, SoundCloud, Rumble, Bitchute, Facebook, Bilibili, X/Twitter) through the real dispatch path and fixed the systemic weaknesses that surfaced:

- **[HIGH]** **yt-dlp fallback for native extractors.** When a platform-specific extractor (Kick, Twitch, Rumble, SoundCloud, Reddit, Audius) fails to resolve — sites change their markup/API constantly — `FetchWorker` now retries the URL through the yt-dlp catch-all (1700+ supported sites) before surfacing a hard error. A one-off native breakage no longer becomes a dead end. Applies to the direct-permalink, live, and channel resolve paths, and to the CLI (which shares `FetchWorker`).
- **[HIGH]** **Auth-error detection no longer false-positives on `webpage`.** A bare `"age"` phrase matched the `age` inside "web**page**", so every "Unable to download webpage" error kicked off the full multi-browser cookie scan (up to 60s per installed browser). A Facebook probe that should have failed in <1s took **86s**. Auth phrases are now specific, and transport failures (DNS/connection/timeout/5xx) short-circuit the scan entirely.
- **[MED]** **Cloudflare / anti-bot resilience via TLS impersonation.** More hosts (Rumble, Bitchute, some PeerTube mirrors) gate pages behind Cloudflare. When resolve or download fails with a Cloudflare/403 signature and `curl_cffi` is available, StreamKeep retries once with `--impersonate chrome`.
- **[LOW]** **Quality labels no longer read "None".** Extractors such as TikTok set `format_note`/`ext` to an explicit `None`; the label builder used `dict.get(key, default)`, which does not replace a present-but-`None` value, yielding "None (mp4)". Switched to `or` chaining.
- Added `tests/test_fetch_fallback.py` (fallback routing, auth-error guards, Cloudflare detection, impersonation args). Full suite: 361 passing.

### 4.31.6 (2026-07-15) — YouTube opus+mp4 merge fix

- **[HIGH]** YouTube (and any site) downloads that pair an mp4 video track with a webm/opus audio track no longer fail with "yt-dlp download failed" despite yt-dlp exiting 0. yt-dlp auto-switches the merged container to `.mkv` for opus-in-mp4, writing `<label>.mkv` instead of the `<label>.mp4` the worker expects. `_download_with_ytdlp` now passes `--merge-output-format mp4` so the merge is deterministic and matches the app's `.mp4` output path (opus/vp9/av1 mux into mp4 fine). Reproduced with `youtube.com/watch?v=oshdvLLtl3U`, whose highest-bitrate audio is opus (133k) over m4a (129k), so the video+audio pair was `137+251`.
- **[MED]** Added a defense-in-depth `_reconcile_output` safety net: if yt-dlp exits 0 but the exact `.mp4` is missing, the worker adopts the largest sibling merge output (`<label>.mkv`, `<label>.mp4.mkv`, …) into the expected path so resume/manifest stay consistent.
- Added `tests/test_download_worker.py` regression coverage (merge flag present; sibling reconciliation for single/double-extension and largest-wins cases).

### 4.31.5 (2026-07-15) — Kick VOD permalinks + CLI download fixes

- Reworked the desktop shell around a compact text-led navigation, flatter work surfaces, denser download controls, a higher primary URL action, and explicit queue/activity empty states.
- Bundled yt-dlp and yt-dlp-ejs into the one-file executable and added an internal frozen runner so YouTube downloads no longer depend on a separately installed yt-dlp command.
- Made windowed frozen CLI output tolerate missing standard streams and verified the built executable through hidden version, extractor, and embedded yt-dlp smoke commands.
- Restored the Chromium sandbox for headless media discovery and constrained each scrape to an ephemeral, download-free context with safe schemes, request/media budgets, bounded navigation, and deterministic teardown.
- **[HIGH]** Kick extractor now handles VOD permalink URLs (`kick.com/<channel>/videos/<uuid>` and `kick.com/video/<uuid>`). Previously only bare channel URLs matched `URL_PATTERNS`, so a direct VOD link failed with "Failed to resolve stream URL". Resolves the UUID to its HLS master via the undocumented `/api/v1/video/<uuid>` endpoint (v2's video-by-uuid route is gone).
- **[HIGH]** `FetchWorker` short-circuits direct permalinks (new `Extractor.is_direct_url`) instead of running channel live-check + channel-wide VOD listing. A VOD link on a channel with multiple recent VODs used to emit `vods_found`, which the CLI never handled — the app hung forever.
- **[HIGH]** CLI `download` no longer crashes on a non-existent `parse_hls_playlist` import; builds a single whole-stream segment (`safe_filename`-labelled) that the DownloadWorker writes as one file.
- **[HIGH]** `_print_progress` used `os.get_terminal_size(fallback=...)`, which raises on Windows (`nt.get_terminal_size` takes no kwargs), crashing the parent on the first progress tick and orphaning ffmpeg. Now uses `shutil.get_terminal_size`; both writers guard `OSError` for redirected/headless stdout.
- CLI now auto-selects the most recent VOD when a channel URL resolves to a list, rather than hanging.

### Deep Audit Pass (2026-07-01)

- **[CRITICAL]** Fixed `schedule.py` wrong kwarg `extra_headers` that silently broke every Twitch schedule fetch via caught TypeError.
- **[HIGH]** Fixed `monitor_ops.py` `list_vods` tuple unpacking — was assigning `(vods, cursor)` tuple causing AttributeError on `.source` access.
- **[HIGH]** Fixed `download.py` spurious error signal on successful chunked captures — `error.emit` was outside the `else` block.
- **[HIGH]** Fixed `download.py` missing `_proc_lock` around Popen in chunked capture, preventing cancel race.
- **[HIGH]** Fixed `local_server.py` double `_require_auth` calls that could send duplicate HTTP responses.
- **[HIGH]** Fixed `config.py` GuiLogHandler calling Qt widgets from worker threads — now marshals to main thread.
- **[HIGH]** Fixed `bandwidth.py` month_bytes double-counting today's traffic after persist.
- **[HIGH]** Fixed `subtitles.py` TTML `dur` attribute treated as absolute end time instead of relative duration.
- **[SECURITY]** `gallery.py`: reject symlinks in `serve_media_range` to prevent directory traversal.
- **[SECURITY]** `updater.py`: tightened update size check from 90% to exact match.
- **[SECURITY]** `media_server.py`: moved Plex token from URL query string to `X-Plex-Token` header.
- **[SECURITY]** `cookies.py`: restricted `cookies.txt` file permissions to owner-only on POSIX.
- **[SECURITY]** `diagnostics.py`: expanded config redaction to cover nested keys (token, api_key, secret, password).
- **[MEDIUM]** Fixed `feed.py` unescaped guid in RSS XML items.
- **[MEDIUM]** Fixed `twitch_irc.py` socket leak on TLS/send failure.
- **[MEDIUM]** Fixed `normalization.py` zero-byte output permanently blocking re-normalization.
- **[MEDIUM]** Fixed `clip_worker.py` unescaped single quotes in ffmpeg concat list filenames.
- **[MEDIUM]** Fixed `scheduler.py` day_end >= 24 and midnight-wrap edge cases in speed scheduling.
- **[MEDIUM]** Fixed `bandwidth.py` SQLite connection leaks in all four DB access methods (try/finally).
- **[LOW]** Fixed `storage.py` naive local timestamp in import_folders — now uses UTC.

- Rewrote the README as a current v4.31.x user/release guide covering downloader, queue, monitor, player, intelligence tools, uploads, web gallery, RSS/feed outputs, CLI, server mode, browser companion, backup/restore, plugins, configuration paths, packaging contents, and validation commands.
- Normalized release hygiene notes around the current v4.31.x implementation history without duplicating the legacy F1-F80 roadmap text.
- Documented plugin manifest expectations, trust gating, browser companion pairing, release packaging contents, and accessibility expectations in the tracked README.
- Stabilized updater and upload worker regression tests by using direct Qt signal connections for synchronous worker runs, narrower updater `sys` patching, and PyQt test stubs that only activate when the real toolkit is unavailable.
- Fixed launcher bootstrap ordering so `multiprocessing.freeze_support()` runs before PyQt imports, and CLI invocations skip optional dependency auto-installs.
- Fixed the monitor schedule-click handler by removing a stale `box` reference left after the premium dialog migration.
- Fixed the Download tab `QUrl` import so package collection imports `streamkeep.ui` cleanly.
- Added local server regression tests for Host-header rejection, bearer-token enforcement, localhost CORS echoing, and untrusted-origin fallback.
- Added updater regression tests for SHA-256 mismatch rejection and verified-hash acceptance.
- Added plugin manifest and trust-gating tests for invalid manifests, untrusted plugin skips, and manifest trust updates.
- Hardened remaining curl subprocess paths with `--proto =http,https` and `--max-redirs 5` for HEAD probes, parallel range downloads, and thumbnail fetches.
- Restored secure LAN browser-companion access by allowlisting local interface Host/Origin values while keeping localhost-only mode DNS-rebinding resistant.
- Made updater installs fail closed when release SHA-256 metadata is missing or malformed, and documented the required `.sha256` sidecar for executable releases.
- Made release packaging reproducible by tracking `StreamKeep.spec`, adding the PyInstaller runtime hook/data collection, replacing the Flatpak ffmpeg placeholder hash, fixing the Flatpak icon path, and enabling configured MSIX signing.
- Removed silent base64 credential fallback for new secrets, added `keyring` to source dependencies, and surfaced secure-store failures in the Settings account-token flow.
- Added headless Qt smoke tests for main-window startup, tab navigation, key dialogs, translator fallback, and playlist-worker lifecycle signals.
- Added yt-dlp readiness checks for version, `yt-dlp-ejs`, and JavaScript runtime support, with Settings/onboarding status and actionable YouTube fallback warnings.
- Compiled English and Spanish Qt translation artifacts, added a Settings language selector, and verified Spanish translator loading in tests.

## [v4.31.4] - 2026-06-29

- Added a SQLite failed-job recovery ledger for fetch, download, and finalize failures, including retry count, output directory, resume sidecar, queue context, and timestamps.
- Exposed retryable failures in the desktop queue and authenticated web remote, with retry/discard actions through `/api/failures/retry` and `/api/failures/discard`.
- Added CLI/headless failure persistence plus regression tests for database persistence and local-server recovery endpoints.

## [v4.31.3] - 2026-06-29

- Added archive integrity manifests for completed recordings, including DB-backed manifest rows, portable `.streamkeep_manifest.json` sidecars, History verify/rescan actions, CLI sidecar creation, and backup preservation tests.

## [v4.31.2] - 2026-06

- Fixed audit findings across extractor, subprocess, chat, post-processing, summary, cache, podcast, and CLI worker lifetimes.
- Hardened yt-dlp command construction with explicit URL separators.
- Upgraded Twitch IRC chat capture to TLS.
- Added visible error propagation for summary backends.
- Fixed Kick, Reddit, SoundCloud, Twitch recovery, thumbnail, preview-cache, and podcast metadata edge cases.

## [v4.31.1] - 2026-06

- Fixed critical queue dispatch, S3 upload, sync-viewer, worker, PiP, monitor-refresh, upload path, CORS, gallery, and post-processing defects found during an engineering audit.
- Improved subprocess cleanup, resume correctness, API restrictions, and background worker safety.

## [v4.31.0] - 2026-06

- Completed the legacy F73-F80 phase: plugin SDK/trust flow, browser companion, updater scaffolding, notification center, lifecycle cleanup, i18n source pipeline, packaged-platform scaffolds, and accessibility/onboarding improvements.

## Historical Summary

- v4.30.x: upload destinations, local web gallery, RSS feeds, notes, backup/restore.
- v4.29.x: analytics, bandwidth tracking, integrity verification, channel statistics, disk alerts.
- v4.28.x: highlights, SponsorBlock, subtitles, summaries, thumbnails, loudness normalization.
- v4.27.x: embedded player, picture-in-picture, sync viewer, chapters/bookmarks, speed/EQ controls.
- v4.26.x: browser cookies, account manager, proxy pool, DASH, speed scheduling.
- v4.25.x: SQLite migration, CLI/headless mode, portable mode, URL import, global search, hover previews.
- v1.0-v4.24: native extractor system, monitor, queue, templates, post-processing, chat, storage, and modularization groundwork.

## Roadmap archive — 2026-08-10 — ROADMAP.md

<details>
<summary>Original roadmap snapshot</summary>

```markdown
# StreamKeep Roadmap

StreamKeep is a Python/PyQt6 desktop downloader and archive manager for live streams, VODs, podcasts, and direct media URLs. This roadmap tracks pending work only.

## Planning Docs

- Research synthesis: `RESEARCH.md`
- Blocked work with its blocker reason: `Roadmap_Blocked.md`

## Current Baseline

- Current package version: v4.57.0.
- Current architecture is modular: extractors, declarative YAML source adapters, workers, post-processing, player, local server, SQLite library, plugin manager, upload adapters, intelligence helpers, and UI modules.
- History, monitor channels, and queue state live in SQLite (schema v24); user preferences remain in JSON config.
- The release lane and local release gate require Python 3.14.6 or newer (`packaging/release_gate.py`); source installs retain the 3.11+ floor.
- ID scheme: `V<n>`. Highest allocated ID is **V255** (2026-08-08 second research pass).

## Active Roadmap

### 0. Versatility Program (active drain queue)

Mission: any video or audio, from any website, in any format, at any quality the source offers, with full user control. DRM circumvention is out of bounds throughout.

### 1. Security and Reliability Hardening

- Continue audit passes across subprocess boundaries, path normalization, URL handling, local API auth, upload destinations, updater downloads, and credential storage.
- Keep subprocess URL/argument handling using explicit separators and platform-safe quoting.
- Keep local server endpoints localhost-only and token-gated.
- Add visible error propagation for background workers that still fail silently.
  > Specified by the 2026-08-08 research: V185 (crash recovery and queue resume), V196 (in-window channel for 32 log-only sites), V217 (unhandled exceptions in threads). This generic line is the parent of those, not a separate task.

### 2. Future Feature Queue

- Remote/headless management polish for the REST server and local web gallery.
- More extractor-specific resilience for API churn on Kick, Twitch, Rumble, SoundCloud, Reddit, Audius, and yt-dlp fallback paths.
- Deeper library views for very large archives: filters, smart collections, transcript search, notes, bandwidth/storage trends, and channel statistics.
  > 2026-08-08 research: V190 wired the existing notes and channel-statistics helpers into supported History and Monitor paths. Transcript search exists; whether it is *semantic* is V202.
- Optional integrations where they stay local-first and user-controlled.

### 3. Unaudited — needs a dedicated pass

The modules added on 2026-08-04 received their first audit on 2026-08-06 and their findings are itemised below. Still without a dedicated pass: a profiling run over large-archive History/Storage/Analytics rendering with 100k+ rows, and the `player/` package's interaction with the new taskbar/power surfaces.

## Definition of Done

- Active planning remains in this file.
- Shipped state is recorded in `CHANGELOG.md`.
- Research and rationale are summarized in `RESEARCH.md`.
- Legacy planning artifacts stay archived and out of the repo root.

## Research-Driven Additions

### 2026-08-06 Research-Driven Additions

Evidence synthesis in `RESEARCH.md` (2026-08-06). Baseline at `fead7d6` (v4.45.0): release gate **GREEN** on Python 3.14.6, 1,712 tests, coverage floor 64.0, pyflakes clean, 47 ruff findings (all `E402`). The whole 2026-08-04 finding set is verified closed in source — do not re-open it; `RESEARCH.md` "Rejected Ideas" also lists what was audited and found clean this pass. New IDs continue the V-scheme (highest prior = V137).

#### P1 — Next

#### P1 — Next

#### P2 — Later

- [ ] P2 — V181 — Make the performance guards measure the code, not the machine
  Why: nine wall-clock assertions across five test files report host load rather than the property they guard, so a contended machine produces false failures that a drain has to spend time disproving — and that could mask a real regression sitting next to them.
  Evidence: observed 2026-08-07 with three unrelated batch jobs holding the CPU at 100% (suite 1,155s against a normal ~310s). Five tests failed and **all five passed in isolation**: `tests/test_live_capture_truncation.py` (3, plus one in the baseline run), `tests/test_operations.py::test_operations_page_stays_bounded_for_one_hundred_thousand_jobs` (`assert elapsed < 5.0`), and a `/api/queue` host-substitution subtest in `tests/test_local_server.py`. The other budgets are `tests/test_declarative.py:868` (2.0s), `tests/test_history_paging.py:58,85` (5.0s), `tests/test_metadata.py:36,52,76` (2s).
  Touches: the five test modules above, plus a shared helper if one earns its place.
  Acceptance: each guard asserts the property it was proxying rather than an absolute duration — the boundedness ones already assert `len(rows) == 50`, so what the timing adds is "no full scan", which a **ratio against a small-N measurement taken in the same process** captures load-invariantly. Inflating the budgets is not acceptable: that blinds the guard instead of fixing it. A deliberately quadratic implementation must still fail the rewritten guard (bait it), and the suite must stay green on a machine held at 100% CPU.
  Complexity: M

- [ ] P2 — V163 — Move behaviour out of `db/_legacy.py` and `server/_legacy.py`
  Why: the 2026-08-04 "split" created facades over unchanged monoliths — `db/_legacy.py` is 6,624 LOC and actually grew during the split; `server/_legacy.py` is 2,748 — and the boundary test asserts the facade forwards the entire legacy surface, which makes the monolith a tested contract and the commit message read as finished work.
  Evidence: `streamkeep/db/__init__.py` (attribute-forwarding `ModuleType` subclass), `streamkeep/db/_legacy.py`, `streamkeep/server/_legacy.py`, `tests/test_architecture_boundaries.py:11-21`; sibling modules `db/history.py` (1.2 KB), `db/queue.py` (723 B) are re-export shims.
  Touches: `streamkeep/db/*`, `streamkeep/server/*`, `tests/test_architecture_boundaries.py`.
  Acceptance: each domain module owns its own statements rather than re-exporting; the boundary test asserts what each module implements (not that the facade is surface-identical); `_legacy.py` shrinks measurably per increment and the facade remains patch-compatible for existing tests.
  Complexity: XL
  > Note (2026-08-08 research): the largest monoliths are no longer the `_legacy` pair. `cli.py` is 3,360, `ui/tabs/settings.py` 3,291, `ui/main_window.py` 3,133, `workers/download.py` 3,132 against `db/_legacy.py` 4,013 and `server/_legacy.py` 2,340. Churn analysis puts `settings.py` at 47 touches in 250 commits — one per gated subsystem, making it the app's de-facto feature registry and the natural next target after the `_legacy` pair.
  > Progress: eight increments done. `db/_legacy.py` 6,737 -> 4,013 (projections, schema/migrations, history-log + tombstones + publishing ids, connection layer, monitor family + the shared write lock into `primitives`, the 18-function download-queue/executor-lease family); `server/_legacy.py` 2,748 -> 2,340 (auth/token/replay layer, host+origin validation, remote-UI rendering). Both facades now use the same `_owner`/`_holders` split: a write reaches every module holding the name, a read resolves through the first that can serve it. Two traps are banked in CLAUDE.md — `ClassDef.lineno` excludes decorators (a hardcoded-span move orphaned a `@dataclass` onto the next class and still parsed clean), and a name moving out of a monolith vanishes from the public surface unless its new module is added to `_DOMAINS`. Remaining: `_build_handler` is 1,867 of the 2,340 remaining server lines — one function, so the next server cut is by route group inside it, not by top-level name. In `db/_legacy.py` the remaining families by size are history (~1,190 lines / 35 fns), uploads (~300), intelligence (~250), backup (~240). Each needs the shared `_write_lock` from `primitives`, never re-imported from `_legacy`. **Scope a family from its re-export shim's `_EXPORTED` set, never from a regex over `_legacy`** — that mistake made the queue family look like 6 functions when it was 18.

#### P3 — Under Consideration

### 2026-08-08 Research-Driven Additions

Evidence synthesis in `RESEARCH.md` (2026-08-08). Baseline v4.55.0, schema v24, 380 modules / 108.6k LOC. No full suite ran (machine held at 100% CPU by unrelated batch jobs), so **no item below rests on a coverage number** — the `.coverage` artefact on disk is a targeted 84-test run. The whole 2026-08-06 finding set is verified closed in source and is listed in `RESEARCH.md` "Rejected Ideas" — do not re-open it. Every claim below was re-verified against current source; three agent findings were discarded as false (pre-migration snapshot, CELT gating, Reddit credential UI) and are recorded there too. New IDs continue the V-scheme from V181.

#### P0 — Now

#### P1 — Next

#### P2 — Later

- [ ] P2 — V203 — Move global search and analytics off the UI thread
  Why: typing in the header search runs four database queries including the semantic index synchronously on the UI thread, so the whole window stutters on a large library — and there is no busy indicator anywhere in the app to explain it.
  Evidence: `streamkeep/ui/main_window.py:3000-3095 _on_global_search` calls `_db.search_history`, `search_transcripts`, `search_comments` and `search_moments` from a 300ms debounce timer with no worker. `ui/tabs/analytics.py:326-385 _refresh_analytics` runs `_db.history_analytics()` synchronously on `currentIndexChanged` (`:261`) with no feedback and no `try/except`. Grep confirms zero `WaitCursor`/`setOverrideCursor` and zero indeterminate `setRange(0, 0)` anywhere in `streamkeep/ui/`.
  Touches: `streamkeep/ui/main_window.py`, `streamkeep/ui/tabs/analytics.py`, `streamkeep/workers/`.
  Acceptance: both run on a worker whose result is discarded if superseded, with a visible busy state. Any new worker must be discoverable by `_owned_workers` (assign it to a window attribute) or it reintroduces V186. This is the natural place to adopt the scan/analyse/refresh phase split before the unprofiled 100k-row path in section 3 is profiled — see `RESEARCH.md` on Komga/Immich.
  Complexity: M

- [ ] P2 — V204 — Author a source adapter from a HAR capture
  Why: the Versatility Program's main lever is unusable. The declarative adapter system is hardened, cached, ReDoS-bounded, review-gated and tested — and zero adapters ship, there is no serializer or template, and `path_regex` appears nowhere in README or docs. HAR capture is already wired, so this closes "any website" with no new trust surface.
  Evidence: `streamkeep/declarative.py:1218 parse_definition_text` is read-only with no writer; `find . -name "*.yaml"` returns only packaging manifests; `grep path_regex README.md docs/` returns nothing. HAR is reachable at `cli.py:1676` and `ui/tabs/download_queue.py:105`. The review gate that makes this safe already exists (`declarative.py` inert-until-reviewed, `backup.py:101 ADAPTER_REVIEW_CONFIG_KEY`).
  Touches: `streamkeep/har.py`, `streamkeep/declarative.py`, `streamkeep/cli.py`, `README.md`, `tests/test_declarative.py`.
  Acceptance: a HAR containing a media request produces a draft adapter that round-trips through `parse_definition_text`, lands **unapproved** in the existing review queue, and is documented in README with one worked example. The draft must never be auto-enabled — the review gate is the whole reason this is safe to add. Ship at least one reference adapter so the format has a worked example in-tree.
  Complexity: M

- [ ] P2 — V205 — Recognise competitor archive layouts during adoption
  > 2026-08-08 (second pass): **raise to P1 on new market evidence.** Pinchflat (5,217★) has been dead since 2025-12-16 (issue #800, 255 reactions); its community fork Tubeless was deleted with full git history ~2026-08-05 and rebuilt at 6★, whose maintainer wrote they doubt it will go far (#897). Youtarr #531 ("import an existing library from disk — migrate off Pinchflat/TubeArchivist") is open and unclaimed. `importer.py` already does ~90% of this. The window closes the moment a competitor lands an importer. Bound layout detection to fixtures (data, not code).
  Why: Pinchflat has been dead since 2025-12-16 with 255 reactions on its pause notice, TubeArchivist and Youtarr users are asking for the same migration path, and no maintained desktop alternative exists. Adoption is already built — it just cannot read anyone else's tree.
  Evidence: `streamkeep/importer.py` is preview-first and transactional with `plan_library_adoption`/`apply_library_adoption` wired to a threaded Storage-tab worker (`ui/tabs/storage.py:122-140`), and `grep -ic "pinchflat\|tubearchivist\|tubesync\|youtarr\|ganymede" importer.py` returns 0. Demand: https://github.com/DialmasterOrg/Youtarr/issues/531 , https://github.com/Zibbp/ganymede/issues/1043 , https://github.com/kieraneglin/pinchflat/issues/800
  Touches: `streamkeep/importer.py`, `tests/test_importer.py`, `README.md`.
  Acceptance: pointing adoption at a Pinchflat, TubeArchivist, tubesync or Youtarr tree recognises its sidecar shape and naming convention and proposes correct history rows, with the detection driven by **fixtures, not code**, so a layout change upstream is a data edit. Add one real captured tree per competitor as a test fixture. Do not extend scope to importing their databases — the on-disk tree plus sidecars is the durable contract.
  Complexity: M

- [ ] P2 — V206 — Fix the web remote's stuck status and swallowed failures
  Why: the remote UI reports "Loading…" permanently, keeps `aria-busy="true"` forever on two panels, and stays red after a single transient error — so a working remote looks broken and a broken one looks busy.
  Evidence: `streamkeep/server/web_ui.html:213` sets `app-status` to `T('loading')` every 5s cycle and nothing clears it on success; `:243-257` swallows `/api/library` and `/api/monitor` failures with `.catch(()=>{})` while only `renderItems:196` ever clears `aria-busy`; `:244` sets `className = 'status-region error'` and never resets it. No `navigator.onLine` or `online`/`offline` listener anywhere, so with a 5s poll a dropped connection is indistinguishable from an idle app. Resumable items are listed at `:228-230` with no Resume button.
  Touches: `streamkeep/server/web_ui.html`, `tests/test_local_server.py`.
  Acceptance: status clears on success, a failed panel shows an error and drops `aria-busy`, the error class resets, and an offline state is indicated distinctly from idle. The remote's accessibility work is otherwise good (roles, `aria-live`, roving tabindex, `:focus-visible`) — do not regress it.
  Complexity: S

- [ ] P2 — V207 — Make runtime-composed strings translatable and stop fixed-width clipping
  > 2026-08-08 (second pass): scope measured — ~1,200 explicit `tr()` calls against roughly **700 bare user-facing literals** (~480 in widget constructors, ~114 `.setText(...)`, ~107 `setToolTip`/`setPlaceholderText`). The runtime widget-tree walker catches many, so the practical gap is smaller, but any f-string status message is unreachable by it. Weakest files: `calendar_widget.py` (1 `tr`), `ui/tabs/download.py` (8 `tr` for 1,512 LOC), `monitor_entry_dialog.py` (4), `notification_log_dialog.py` (0). Fix those four first.
  Why: every footer status message in the app is untranslatable, so switching to Spanish leaves the most frequently-read surface in English — and the labels that *are* translated will clip, because their containers are fixed-width in pixels.
  Evidence: 68 `_set_status(f"...")` call sites and 44 `setText(f"...")`/`setToolTip(f"...")` sites across `streamkeep/ui/`; the runtime walker (`i18n/__init__.py:305 translate_widget_tree`) re-captures these as new sources on every `setText` and can never match a catalog entry. `ui/tabs/analytics.py` has zero `tr()` calls and its combo items bypass `_translate_combo:243`. Fixed widths that will clip a 20-30% longer string: `ui/tabs/settings.py:469,521,539`, `ui/tabs/download.py:573,581`, `ui/tabs/monitor.py:187,192,197`, `ui/main_window.py:2149`.
  Touches: `streamkeep/ui/` (broadly), `streamkeep/i18n/`, `tests/test_i18n.py`.
  Acceptance: status and composed strings use `tr_format()` with placeholders so one catalog entry covers every value, and the named fixed widths become minimums or size-to-content. Note `tests/test_i18n.py` fails after any edit to a file containing UI strings until the catalogs are regenerated — see `CLAUDE.md`. Spanish coverage itself stays blocked on human translators; this item is about making the strings *reachable* by a catalog, which is the prerequisite.
  Complexity: M

- [ ] P2 — V208 — Give the seven tables without an empty state one
  Why: a channel that resolves to zero VODs, an Operations page with nothing durable, and five Settings tables all paint a blank grid with headers, which reads as a failure rather than an empty collection. The styled empty-state card already exists and is used correctly in eight other places.
  Evidence: no empty state at `ui/tabs/operations.py:309`, `ui/tabs/download_vod.py:78,245`, `ui/tabs/settings.py:573,652,736,1741,2057`; `ui/tabs/analytics.py` metric cards show `0 / 0 GB / - / -` with no explanation. `ui/tabs/storage.py:622-628 storage_empty_label` is a bare `QLabel` with no `objectName`, so it gets none of the `emptyStateCard`/`emptyStateTitle`/`emptyStateBody` styling the others do. The pattern to copy is `make_empty_state` as used at `ui/tabs/history.py:278` and `ui/tabs/monitor.py:251`.
  Touches: `streamkeep/ui/tabs/operations.py`, `download_vod.py`, `settings.py`, `analytics.py`, `storage.py`.
  Acceptance: each renders an informative empty state with a next action where one exists, and `storage_empty_label` is converted to the shared component. `ui/main_window.py:2632-2634` fakes one with a spanned cell — convert it too rather than copying that approach. Scope note: the Operations table does populate correctly (`operations.py:104 setRowCount(len(result.rows))`, summary replaced at `:99`), so this is purely a missing zero-row state, not a broken query.
  Complexity: S

- [ ] P2 — V209 — Reconcile the destructive-action surfaces with the project's own policy
  Why: the policy is immediate action plus a toast and, where possible, an undo — no confirmation dialogs. The app currently does the opposite in both directions: eleven modal confirms (including one on an action whose own badge says "Reversible"), and four destructions with no confirm, no toast and no undo.
  Evidence: modal confirms at `ui/tabs/history.py:689`, `download_queue.py:308,2164`, `storage.py:702,1018,1225`, `settings_preferences.py:650,675,763`, `settings_companion.py:450`, `settings_tools.py:245,665,1481,1503`, plus the only raw `QMessageBox.question` left at `download_single.py:785`. Silent destructions at `ui/main_window.py:2947-2949` (clears the notification ring buffer), `ui/tabs/settings_presets.py:188-196` (deletes a preset with no feedback, where `settings_tools.py:987,1136` do the same job correctly), `ui/tabs/operations.py:160-172`, and `server/web_ui.html:208-211`. Also `ui/rename_dialog.py:292` swallows the `rename_undo.json` write *after* renaming at `:269`, and nothing in the tree ever reads that file.
  Touches: `streamkeep/ui/tabs/` (several), `streamkeep/ui/main_window.py`, `streamkeep/ui/rename_dialog.py`, `streamkeep/server/web_ui.html`.
  Acceptance: destructive actions act immediately with a visible confirmation of what happened and an undo where the operation is reversible; the confirms that remain are only those that are genuinely irreversible, and each states what cannot be undone. Either implement the rename undo the journal was written for or stop writing the journal — a file written for a feature that does not exist is worse than neither. Depends on V196 for the toast surface.
  Complexity: M

- [ ] P2 — V210 — Untrack the compiled translation catalogs and add ruff to the gate
  > 2026-08-08 (second pass): both halves confirmed still open. `git ls-files "*.qm"` returns `streamkeep_en.qm` and `streamkeep_es.qm`. `ruff check streamkeep packaging` on the PATH ruff reports **32 findings — 31× E402 (module-level import not at top of file, e.g. `workers/download.py:50,54,55`) and 1× E731 (lambda assignment)** — with no `[tool.ruff]` section anywhere, so these are default-rule results. Decide the E402 policy (the re-exec launcher genuinely needs late imports) before wiring the stage, or it fails on landing.
  Why: two compiled binaries churn 57 times per 250 commits as tracked build artefacts, and the linter that would catch the 31 standing findings is not a gate stage, so those findings persist indefinitely with nothing raising them.
  Evidence: `git ls-files` lists `streamkeep/i18n/streamkeep_en.qm` and `streamkeep_es.qm` alongside their `.ts` sources; `streamkeep_en.qm` appears in 57 of the last 250 commits. `packaging/release_gate.py:313-325 STAGES` has 13 entries and no lint stage.
  > Corrected 2026-08-08 (audit): the ruff count in this item was stale. Measured with ruff 0.15.22: **48 findings repo-wide** — 46 `E402`, 1 `E731` (`streamkeep/db/_legacy.py:949`), and 1 `F401` (`tests/test_retention_backfill.py:9`, `time` imported but unused, auto-fixable). Scoped to `streamkeep packaging StreamKeep.py` it is 36. The `F401` is real dead code rather than an import-order convention, so fix it regardless of how the `E402` policy is settled.
  Touches: `.gitignore`, `packaging/release_gate.py`, `packaging/build.py`, `streamkeep/i18n/compile_translations.py`, `tests/test_release_gate.py`.
  Acceptance: `.qm` files are gitignored and compiled by the build and the `translations` gate stage instead of being committed — verify a clean checkout can still run and package before removing them, since a frozen build must carry its catalogs. Ruff runs as a gate stage; either fix the 31 findings or configure the ignores deliberately, but do not add a stage that passes on findings it was added to catch.
  Complexity: S

#### P3 — Under Consideration

- [ ] P3 — V211 — Windows shell integration a downloader should have
  Why: two cheap platform wins are absent — a taskbar Tasks list, and notifications that survive in Action Center rather than vanishing when they are dismissed.
  Evidence: `streamkeep/windows_integration.py` implements only `ITaskbarList3` progress (no `ICustomDestinationList`). Unpackaged apps need a registered AUMID **plus** a stub COM CLSID on the shortcut for Action Center persistence, which nothing here does, so `native_notify.py` toasts vanish after display. https://learn.microsoft.com/en-us/windows/apps/design/shell/tiles-and-notifications/toast-desktop-apps
  Touches: `streamkeep/windows_integration.py`, `streamkeep/native_notify.py`.
  Acceptance: a jump list offers at least "Paste URL and capture" and "Open library"; a completed-capture notification remains clickable in Action Center after dismissal. Keep both failure-tolerant in the style of the existing `TaskbarProgress` wrapper — neither is worth an exception on a machine where the COM call fails.
  Rejected sub-idea, recorded so it is not re-proposed: **ReFS block cloning for collection homes.** It looks like a free win (independent file, near-zero cost and space) but it contradicts a guarantee v4.54.0 shipped deliberately — `integrations/media_server.py:289` names the primary "the single on-disk copy", `HOME_STRATEGIES = ("hardlink", "strm")` at `:251`, and copying is explicitly never a fallback. A block clone is semantically a copy: edit one and the other no longer matches, which is exactly what the hardlink/`.strm` pair exists to prevent. Revisit only if the single-copy guarantee is itself reconsidered.
  Complexity: M

- [ ] P3 — V212 — Add `libsvtav1` and a faster transcription backend
  > 2026-08-08 (second pass): the transcription half now has a named target — **Parakeet TDT 0.6B v3** leads Open ASR at 6.32% avg WER vs Whisper's 7.44% and is ~4× faster on CPU with native punctuation, but covers 25 languages to Whisper's 99. Ship it as the English default with faster-whisper retained for everything else, not as a replacement. Alternative if a GPU-free bundle matters more: whisper.cpp 1.8.3 gained real iGPU Vulkan (up to 12× on integrated graphics). Do **not** target AV2 — its bitstream spec froze 2026-05-28 but hardware decode is 2027-28.
  Why: the two slowest post-processing paths both have an order-of-magnitude faster option that the ladder does not offer. `libaom-av1` is the difference between "AV1 archive transcode is theoretical" and "it runs overnight".
  Evidence: `streamkeep/postprocess/codecs.py:20` maps `"av1": "libaom-av1"` with no `libsvtav1` entry; SVT-AV1 3.0 gives ~15-25% speedup at M3-M10 https://aomedia.org/blog%20posts/SVT-AV1-V3_0-is-Now-Available/ . `postprocess/transcribe_worker.py:34-65` ladders whisperx → faster-whisper → whisper.cpp → ffmpeg-whisper with no TDT option; Parakeet TDT 0.6B v3 leads the Open ASR Leaderboard at ~6.34% avg WER against Whisper large-v3's ~7.4% at much higher throughput https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks
  Touches: `streamkeep/postprocess/codecs.py`, `streamkeep/postprocess/transcribe_worker.py`, `streamkeep/capabilities.py`.
  Acceptance: `libsvtav1` is probed and preferred over `libaom-av1` when present with sensible presets (6 for VOD), falling back cleanly; a Parakeet backend joins the ladder without displacing Whisper, which keeps the multilingual breadth. Both must degrade to current behaviour when absent — the existing runtime probe pattern (`_probe_hw_encoder`) is correct, keep it. Any new floor needs a `MINIMUM_VERSIONS` entry (see V194).
  Complexity: M

- [ ] P3 — V213 — Verify a resumed download against the server's own digest
  Why: a resumed multi-connection download that silently received a *different* representation — a rotated CDN, a re-encode — passes every local size check at v4.55.0. RFC 9530 exists specifically to validate a representation reassembled from Range requests.
  Evidence: `streamkeep/verify.py` verifies post-hoc against locally computed digests; `streamkeep/http.py` does parallel Range downloads with no representation check. https://www.rfc-editor.org/rfc/rfc9530.html
  Touches: `streamkeep/http.py`, `streamkeep/verify.py`, `streamkeep/resume.py`, `tests/test_http.py`.
  Acceptance: `Want-Repr-Digest` is sent on the first request and a returned `Repr-Digest` is verified against the reassembled file; a mismatch fails the download by name rather than storing corrupt bytes. Pair with strong-ETag `If-Range` and treat a weak or absent ETag as "resume is unsafe, restart" — that decision matters more than the digest.
  Complexity: M

- [ ] P3 — V214 — Give the fixed-size dialogs room to breathe
  Why: at `spacious` density and 125% DPI several dialogs and embedded tables clip with no scroll affordance and, in one case, no resize handle at all.
  Evidence: `ui/onboarding.py:34 setFixedSize(640, 500)` non-resizable with no `QScrollArea`, wrapping four pages of body copy plus two status banners whose bodies include a version, provenance and path (`:236-238`). Containers that do not participate in density scaling while their contents do (`widgets.py:791` scales row height by `get_density()["scale"]`): `ui/tabs/settings.py:1752` (`setFixedHeight(170)`, ~2 visible rows at spacious), `ui/tabs/download.py:272`, `ui/clip_dialog.py:769,879`. `ui/tabs/settings.py` has 66 fixed-size calls in total. `ui/main_window.py:1914 nav_rail.setFixedWidth(220)` hard-clips longer translated tab labels with no elide.
  Touches: `streamkeep/ui/onboarding.py`, `streamkeep/ui/tabs/settings.py`, `streamkeep/ui/tabs/download.py`, `streamkeep/ui/clip_dialog.py`, `streamkeep/ui/widgets.py`.
  Acceptance: dialogs are resizable or scrollable, embedded tables scale with density or scroll, and clipped labels elide with a tooltip carrying the full text. A test renders the affected surfaces at `spacious` and asserts no widget's `sizeHint` exceeds its allocation. Overlaps V207 — do the fixed-width labels once.
  Complexity: M

- [ ] P3 — V215 — Correct the BagIt second-algorithm manifest
  Why: `sha384-sri` is not a valid BagIt algorithm token, so a standards-conformant BagIt consumer will not treat the file as a payload manifest. BagIt explicitly permits multiple payload manifests with different algorithms, so the fix is to emit a real one.
  Evidence: `streamkeep/verify.py` recognises `manifest-sha384-sri.txt` alongside `manifest-sha256.txt` and `tagmanifest-sha256.txt`. RFC 8493 §2.1.3: https://www.rfc-editor.org/rfc/rfc8493.html
  Touches: `streamkeep/verify.py`, `streamkeep/integrity.py`, `tests/test_verify.py`.
  Acceptance: a second payload manifest uses a registered algorithm token (`manifest-sha512.txt`), and the SRI file is retained as a tag file only, which BagIt tolerates. Keep reading the old name so existing bags still verify.
  Complexity: S

- [ ] P3 — V216 — Report post-processing outcomes per stage, with per-stage retry
  Why: a failed chat render or transcription currently colours the whole item as failed, so the user cannot see which part broke or redo just that part. ArchiveBox's per-extractor result rows are the proven shape for exactly this.
  Evidence: the finalize chain runs stages in sequence (`streamkeep/workers/finalize.py`, `streamkeep/postprocess/processor.py`) with a single outcome per item; the swallow sites at `ui/tabs/download_finalize.py:181` and `workers/finalize.py:652` leave an upgrade decision stuck in its pre-failure state, so Operations can report an upgrade as still running after it failed. Demand for an auditable journal: https://github.com/jmbannon/ytdl-sub/issues/746 . Model: https://github.com/ArchiveBox/ArchiveBox/wiki/Configuration
  Touches: `streamkeep/workers/finalize.py`, `streamkeep/postprocess/processor.py`, `streamkeep/db/schema.py`, `streamkeep/ui/tabs/history.py`.
  Acceptance: each finalize stage records success/fail/skip independently and is individually re-runnable from History, and one failed stage does not mark the recording failed. This needs a schema migration — follow the existing guarded-`ALTER` pattern in `db/schema.py`. Fix the two stuck-state swallows in the same pass; they are the reason Operations currently lies about outcomes.
  Complexity: L

## Audit Findings — 2026-08-08

Full-repository audit pass (correctness, security, reliability, UX, a11y, visual, perf, maintainability, testing). **Baseline measured this pass, and it is clean:** `py -3.14 -m pytest -p no:randomly -q` → **2157 passed, 1 skipped, 169 subtests passed in 375.07s**, coverage **66.66%** against a 64.0 floor. **There are no pre-existing test failures.** `py -3.14 -m compileall` clean; `py -3.14 -m pyflakes streamkeep packaging StreamKeep.py` clean; `py -3.12 -m ruff check .` → 48 findings (see the correction on V210); all version strings agree at 4.55.0.

Areas checked and confirmed genuinely clean — **do not re-investigate**: FTP/FTPS/SFTP transport (`upload/ftp.py` disables plain FTP by default, pins an SSL context, rejects unknown host keys), S3 and WebDAV `allow_insecure_http` opt-in guards, ffmpeg filter-graph construction (`clip_dialog._build_social_vf` uses preset constants and a numeric slider only, no user text), Twitch GQL query interpolation (`schedule.py` gates on `_SAFE_LOGIN` first), IRC TLS (port 6697 with a verified default context), `single_instance.py` (`QLockFile` non-blocking, reference held on the `QApplication`), OpenAPI drift (gated in *both* directions by `tests/test_openapi.py:99,104`), theme contrast (`tests/test_visual_system.py` already measures **every rendered QSS rule block in every palette**, not a token list), `datetime.utcnow()` (absent), mutable default arguments (absent), and `db/{transfers,integrity,maintenance}.py` (legitimate `_EXPORTED` re-export shims pending V163). One suspected perf issue was disproved: `youtube_backend._backend_config` would load the config three times when passed `None`, but both production callers pass a config.

#### P1 — Next

- [ ] P1 — V220 — Make the artifact smoke contract exercise the startup work it currently disables
  Category: testing
  Where: `streamkeep/ui/main_window.py:431,469,475,479,1063,1173,2560` (`self._startup_check` branches); `streamkeep/startup_check.py`; `packaging/artifact_smoke.py:95`; `packaging/release_gate.py:279-295` (`stage_artifact_smoke`)
  Problem: the gate stage whose docstring is "Start the built executable and prove it comes up on a clean profile" runs the app with `startup_check=True`, and that flag suppresses precisely the deferred startup work most likely to fail: the scheduler timer and immediate bandwidth-rule application (`:431`), the orphan resume-sidecar disk scan (`:469` — a shipped bug class, v4.43.5), the update check (`:475`), the companion server socket (`:479`), the transcript index build (`:1063`), tray-icon creation and its documented fallback (`:1173`), and the health monitor timer plus its immediate probe (`:2560` — the exact subsystem behind the V177/V179/V180 access-violation chain). So the release contract certifies that the window constructs with seven subsystems switched off, and cannot see a regression in any of them. `startup_check.py` itself has **0% coverage** (109 statements), so the harness that issues this certificate is also unverified.
  Evidence: `packaging/artifact_smoke.py:95` passes the `startup-check` subcommand; `cli.py:3337` dispatches it to `startup_check.run_startup_check`, which constructs `MainWindow(startup_check=True)`. Each of the seven line numbers above is a `if not self._startup_check:` / `if self._startup_check: return` guard, read and confirmed. Coverage from the clean full-suite run this pass: `streamkeep\startup_check.py 109 statements, 0.0%`.
  Fix: split the flag by intent instead of using one blanket suppressor. Keep suppressing what genuinely cannot run in a smoke context (the update check's network call, the tray icon on a headless session) and **start** the rest under the contract with a short bounded deadline, asserting each reached a known state — scheduler tick applied, resume scan completed, index build started, health probe completed or cleanly cancelled. The health probe especially must run, since it is the documented cause of three shipped crashes. Add tests for `startup_check.py`'s own assertions, including one that makes a fixture count mismatch and confirms the harness fails rather than passing.
  Acceptance: `packaging/artifact_smoke.py` reports per-subsystem startup outcomes rather than a single boot flag; a deliberately broken health probe or resume scan makes the `artifact-smoke` stage fail; `startup_check.py` has coverage and a test proving a mismatched fixture count is rejected.
  Confidence: Verified
  Effort: M

#### P2 — Later

- [ ] P2 — V221 — Keep `add_to_collection` idempotent for position, not just membership
  Category: correctness
  Where: `streamkeep/tags.py:405-426` (`add_to_collection`)
  Problem: the docstring says "Add a recording to a collection. Idempotent." Membership is idempotent, but ordering is not. With `position=None` (the default, and what the CLI uses) the function computes `MAX(position) + 1` and then issues `INSERT OR REPLACE`, so re-adding a recording that is **already** in the collection rewrites its row with a new trailing position — silently moving it from wherever the operator put it to the end of the list. Ordering is an explicit product promise for this feature ("Membership is explicit and ordered"), so a repeated `collections add` quietly reorders a curated list.
  Evidence: `:413-419` computes the next position unconditionally, without checking whether the row exists; `:420-423` is `INSERT OR REPLACE INTO collection_members (collection_id, recording_path, position)`, and the table's primary key is `(collection_id, recording_path)` (`_COLLECTION_SCHEMA`), so the REPLACE overwrites the existing member's position. `tests/test_collections.py:48 test_membership_is_idempotent` calls `add_to_collection` twice and asserts only the member **list**, never a position, so the reorder is invisible to the suite; `test_members_keep_the_operators_order:66` always passes explicit positions and so never exercises this path.
  Fix: when `position is None`, look up the existing row first and keep its position if the member is already present; only compute `MAX(position) + 1` for a genuinely new member. An explicit `position` argument should still move the member deliberately.
  Acceptance: extend `test_membership_is_idempotent` to assert the member's position is unchanged after a second `add_to_collection`, and add a case where a member added at position 0 of three stays first after being re-added.
  Confidence: Verified
  Effort: S

- [ ] P2 — V222 — Bound the chat readers' accumulation
  Category: reliability
  Where: `streamkeep/chat/twitch_irc.py:197-213` (`iter_messages`); `streamkeep/chat/kick_ws.py:196,221,236`
  Problem: both chat readers accumulate without any cap, in threads that are expected to run for the length of a multi-hour capture. In `twitch_irc.py` the loop does `buf += data.decode(...)` and only drains `buf` while `"\r\n" in buf`, so a peer that sends data containing no line terminator grows the string until memory is exhausted — there is no length limit anywhere in the file (the only `CAP` match is the IRC `CAP REQ` command). In `kick_ws.py` the connection is created with `websocket.create_connection(url, timeout=10)` and no `max_size`, and `recv()`'s result goes straight into `json.loads(raw)`, so a single oversized or endlessly fragmented frame is assembled in full before any size check. Both connections are TLS with verification, so this is not remotely attacker-injectable; the realistic trigger is a malfunctioning or hostile endpoint, and the cost is the capture process dying with the recording in progress.
  Evidence: `twitch_irc.py:197` initialises `buf = ""`; `:209` appends; `:212` drains only on `"\r\n"`; `grep -nE "MAX|CAP|_LIMIT" streamkeep/chat/twitch_irc.py` matches only the `CAP REQ` string at `:172`. `kick_ws.py:196` creates the socket with no `max_size`; `:221` is a bare `recv()`; `:236` is `json.loads(raw)` with no length check.
  Fix: cap `buf` in `twitch_irc.py` — an IRC line is bounded by the protocol, so if `buf` exceeds a generous limit (say 64 KiB) with no terminator, log it and reset the buffer or drop the connection rather than continuing to append. For `kick_ws.py`, pass a `max_size` to `create_connection` and reject an oversized payload before parsing. Both should surface the condition rather than failing quietly (see V196 for the channel).
  Acceptance: a test feeding a fake socket 1 MB of terminator-free data asserts memory does not grow unboundedly and that the reader reports the condition; a test delivering an oversized websocket frame asserts it is rejected rather than parsed.
  Confidence: Verified
  Effort: S

- [ ] P2 — V223 — Say "Recycle" where the operation recycles
  Category: ux
  Where: `streamkeep/ui/tabs/settings.py:2558` and `:2595`
  Problem: the two controls that configure automatic retention read "**Delete** recordings older than" and "**Delete** watched recordings automatically", but the operation they enable never deletes — `lifecycle.py:4` states the invariant outright ("always uses `send2trash` (recycle bin) — never permanent") and `:247-273` sends each directory to the Recycle Bin. Every other string describing the same operation says so correctly: "would be moved to the Recycle Bin", "You can still restore the folders later from the system Recycle Bin", "[LIFECYCLE] Recycled {removed} recording(s)". The result is that the one place a user decides whether to switch on unattended cleanup uses the most alarming available verb for the single operation the codebase guarantees is reversible — which both suppresses adoption of a safe feature and is inconsistent with fourteen sibling strings.
  Evidence: `grep -rn "Delete recordings older than\|Delete watched recordings automatically" streamkeep/ui/` → `settings.py:2558,2595`. `streamkeep/lifecycle.py:4,247-273` confirms the recycle-only path. `grep -rhoE "\"[^\"]*Recycle[^\"]*\"" streamkeep/ui/` returns 14 distinct strings using the correct term for the same concept.
  Fix: reword both to "Recycle recordings older than" and "Recycle watched recordings automatically", and add the reassurance already used elsewhere ("Files stay recoverable through the Recycle Bin…") as helper text on the section. Use `tr_format()` rather than an f-string so the strings are catalog-reachable (see V207). Sweep the surrounding retention section for the same substitution.
  Acceptance: no user-facing string in the retention settings says "Delete" for a recycle operation; the i18n catalogs regenerate with the new sources; the strings agree with the terminology used in the preview and log output.
  Confidence: Verified
  Effort: S

- [ ] P2 — V224 — Test the two zero-coverage surfaces that carry startup and crash behaviour
  Category: testing
  Where: `streamkeep/startup_check.py` (109 statements, 0.0%); `streamkeep/ui/intelligence_dialog.py` (103 statements, 0.0%); `streamkeep/crash_log.py` (36 statements, 27.8%)
  Problem: measured on the clean full-suite run this pass, three modules that decide whether the app boots, whether a crash is recorded, and a whole user-facing dialog are effectively unexercised. `startup_check.py` is the release contract itself (see V220). `crash_log.py` is the only crash-recording path (see V217) and no test asserts it writes anything. `ui/intelligence_dialog.py` is reachable from History (`ui/tabs/history.py:1568`) and has no test at all — it is also one of the dialogs with zero `configure_accessibility` coverage.
  Evidence: coverage lines from the 2026-08-08 baseline run: `streamkeep\startup_check.py 109 109 0.0%`, `streamkeep\ui\intelligence_dialog.py 103 103 0.0%`, `streamkeep\crash_log.py 36 26 27.8%`. `grep -rln "setup_crash_logging" tests/` matches nothing.
  Fix: add a test module per surface following the existing style — `tests/test_gui_smoke.py` is the pattern for constructing a dialog under `QT_QPA_PLATFORM=offscreen` and driving it, and `tests/test_release_gate.py` is the pattern for harness assertions. Minimum viable coverage: `crash_log` writes a rotated entry for a raised exception and survives an unwritable log directory; `startup_check` rejects a fixture-count mismatch; `intelligence_dialog` constructs, renders each state, and its workers are joined on close (which also feeds V186).
  Acceptance: all three modules exceed the 64% floor individually, and the new crash-log test fails if `setup_crash_logging` is stubbed out.
  Confidence: Verified
  Effort: M

#### P3 — Under Consideration

- [ ] P3 — V225 — Correct the stale port in the IRC reader's docstring
  Category: maintainability
  Where: `streamkeep/chat/twitch_irc.py:3`
  Problem: the module docstring says it "Connects to `irc.chat.twitch.tv:6667`", which is the **plaintext** IRC port. The code actually uses `PORT = 6697` (`:19`) and wraps the socket with `ssl.create_default_context()` and `server_hostname=SERVER` (`:169-170`). A reader auditing the transport sees a docstring describing an unencrypted connection and either wastes time confirming it or, worse, "fixes" the port to match the comment.
  Evidence: `:3` reads `6667`; `:18-19` are `SERVER = "irc.chat.twitch.tv"` / `PORT = 6697`; `:169-170` perform the TLS wrap with hostname verification.
  Fix: update the docstring to `irc.chat.twitch.tv:6697` and state that the connection is TLS with certificate and hostname verification, so the security posture is documented where it is read.
  Acceptance: the docstring names 6697 and TLS; no other occurrence of 6667 remains in the module.
  Confidence: Verified
  Effort: S

- [ ] P3 — V226 — Correct the v4.55.0 release date in CHANGELOG and the AppStream metainfo
  Category: docs
  Where: `CHANGELOG.md:5` (`## [4.55.0] - 2026-08-07`); `packaging/flatpak/com.github.SysAdminDoc.StreamKeep.metainfo.xml:32` (`<release version="4.55.0" date="2026-08-07">`)
  Problem: both documents date v4.55.0 to 2026-08-07, but the release commit landed 2026-08-08. The AppStream date is user-facing store metadata and the CHANGELOG is the shipped release history, so the published record is off by a day. This is a version-metadata accuracy issue, deliberately logged rather than fixed here because touching version strings is out of scope for an audit pass.
  Evidence: `git log -3 --format='%ad %s' --date=short` → `2026-08-08 chore(release): bump to v4.55.0 and sync every version string`, while `CHANGELOG.md:5` and the metainfo `<release>` block both say `2026-08-07`. Note the preceding entry (v4.54.0, 2026-08-07) is correct, so the stamp appears to have carried the previous day forward.
  Fix: set both to 2026-08-08. Check whether `packaging/versioning.py` derives the date from the commit or from the previous entry — if it copies the prior release's date, that is the root cause and the stamper should read the current date (or take it as an argument) so the next release is not wrong the same way.
  Acceptance: both files read 2026-08-08 for 4.55.0, and `packaging/versioning.py`'s date handling is either confirmed correct or fixed so a subsequent bump stamps the actual release date.
  Confidence: Verified
  Effort: S

### Unaudited — needs a dedicated pass

Recorded honestly so the next pass knows where the gaps are. This audit prioritised correctness, security, data safety and the newest code; the following received no more than incidental inspection:

- **`streamkeep/workers/download.py` (3,132 lines) and `streamkeep/cli.py` (3,360 lines) internals.** The two largest non-`_legacy` modules. Only their subprocess-encoding and argv-quoting surfaces were checked.
- **`streamkeep/headless_service.py` (1,719 lines)** — executor-lease acquisition, renewal and expiry under concurrency, and the REST-to-worker handoff.
- **The extractor family** (`extractors/kick.py`, `twitch.py`, `rumble.py`, `soundcloud.py`, `reddit.py`, `audius.py`, `podcast.py`) — untrusted API response parsing and field bounds were not systematically reviewed.
- **`streamkeep/player/`** — mpv widget lifecycle, PiP window, and the sync viewer's interaction with the taskbar/power surfaces (this repeats a gap already noted in section 3 above).
- **`streamkeep/backup.py`, `rebuild.py`, `maintenance.py` internals** — the crash-consistency logic itself, as opposed to the swallowed finalizers logged as V185.
- **`browser-extension/`** — only the manifest's `host_permissions` and `minimum_chrome_version` were verified (during the 2026-08-08 research pass); the content script and service worker were not read.
- **Live GUI observation.** No finding in this pass came from driving the running application: the machine was under sustained load from unrelated batch jobs and the standing rule is that visible work runs on the isolated virtual display. All UX, visual and a11y findings here and in the research additions above are traced from source. A pass that actually drives the UI on the virtual display — every tab, every dialog, all three palettes, both densities — is the single highest-value remaining audit activity.
- **`streamkeep/governor.py`, `retry.py`, `bandwidth.py`** — backoff, rate and budget arithmetic was not verified against its own edge cases.

## Research-Driven Additions — 2026-08-08 (second pass)

Evidence synthesis in `RESEARCH.md` (2026-08-08, second pass). Baseline **v4.57.0**, HEAD `795c213`, schema v23, 219 modules / 110,861 LOC, 156 test files / 2,023 test functions. The full suite was not run (~19 min, and a loaded machine manufactures timing failures — V181), so **no item below rests on a coverage number**. Everything from the first 2026-08-08 pass that has since landed (V182–V188, V190, V195) is verified closed in source and recorded in `RESEARCH.md`; do not re-open it. Items shipped that competitors are still asking for — season/episode NFO, RSS output, SponsorBlock chapters, chat-to-ASS, watch-state sync, start-from-beginning capture, first-run Deno/JS-runtime provisioning, `curl_cffi` impersonation — are listed in `RESEARCH.md` "Rejected Ideas" as *already built*; market them, do not build them. New IDs continue the V-scheme from V227.

#### P0 — Now

#### P1 — Next

#### P2 — Later


- [ ] P2 — V243 — Bring filter, sort and search to the views that lack them
  Why: History is well served and nothing else is. Storage has platform/channel combos but no text search; Monitor, Analytics, Operations and the Queue have no filtering or sorting at all — which is precisely the "very large archive" case the roadmap's own Future Feature Queue names.
  Evidence: History search at `ui/tabs/history.py:191-194` and `ui/history_model.py:195`; `StorageFilterProxyModel` at `ui/storage_model.py:98`, applied `ui/tabs/storage.py:193-205`, with no text field; sorting explicitly disabled at `ui/main_window.py:2682`. Competitor expectation is set by TubeArchivist's Elasticsearch-backed search.
  Touches: `streamkeep/ui/tabs/storage.py`, `monitor.py`, `operations.py`, `download_queue.py`, `streamkeep/ui/storage_model.py`, `tests/test_history_paging.py` (pattern to copy).
  Acceptance: Storage gains text search over path, channel and title; Monitor and Operations gain a filter field; the Queue and Monitor tables sort by their meaningful columns. All of it stays on the keyset-paged model path, not per-row widgets.
  Complexity: M

- [ ] P2 — V244 — Give the keyboard a defined path through Settings and the dialogs
  Why: keyboard access is claimed as a feature, but traversal order falls back entirely to widget construction order — across ~175 Settings controls that is technically functional and practically unusable.
  Evidence: `setTabOrder`, `setBuddy`, `setShortcut` and `QShortcut` total **17 occurrences** across all of `streamkeep/ui/`; `setFocusPolicy` appears 5 times; `setAccessibleDescription` 22 times against ~40 `setAccessibleName`. The helpers already exist (`ui/widgets.py:442-468 set_accessible`, `update_accessible_status`) and `_FocusRevealFilter` at `ui/widgets.py:764` already scrolls focus into view. README "Desktop Keyboard Access" makes the claim.
  Touches: `streamkeep/ui/tabs/settings*.py`, `streamkeep/ui/monitor_entry_dialog.py`, `rename_dialog.py`, `recover_dialog.py`, `notification_log_dialog.py`, `tests/test_accessibility.py`.
  Acceptance: an offscreen test walks Tab from each page's first control and asserts the order matches the visual order and that every interactive control is reachable and carries an accessible name.
  Complexity: M

- [ ] P2 — V245 — Build a large-library fixture and a scale test
  Why: nothing in the repo establishes a baseline for the 100k-row History/Storage/Analytics path, so V181 (perf guards that measure the code, not the machine) and V203 (move search and analytics off the UI thread) both have to invent one before they can start. Runaway memory in unattended archivers is a documented category failure (Pinchflat #866: 30.8 GB RES, recurring every 1–2 days).
  Evidence: no `tests/test_*perf*`, `*bench*`, `*scale*` or `*large*` file exists; `ui/tabs/analytics.py:360 _refresh_analytics` calls `_db.history_analytics(...)` synchronously with no worker (that is V203's target — do not fix it here).
  Touches: `tests/conftest.py`, `tests/test_scale.py` (new), `streamkeep/db/`.
  Acceptance: a seeded fixture builds a 100k-row history plus manifests deterministically and is reusable; a test records query and model-load timings and peak RSS as a recorded baseline, not a pass/fail threshold, so V181 can decide the guard afterwards.
  Complexity: M

- [ ] P2 — V246 — Retire the aria2c external-downloader path
  Why: aria2 has had no release since 1.37.0 (2023-11-15); yt-dlp removed its manifest support after CVE-2026-50574 (RCE via manifest downloads with aria2c), so the remaining surface is plain HTTP where StreamKeep's own parallel range downloader already applies. Fifteen modules still carry the option.
  Evidence: `aria2` appears in `cli.py`, `config.py`, `download_options.py`, `extractors/ytdlp.py`, `headless_service.py`, `job_spec.py`, `models.py`, `resume.py`, `ui/main_window.py`, `ui/main_window_jobs.py`, `ui/tabs/download_queue.py`, `download_single.py`, `download_vod.py`, `monitor.py`, `workers/download.py`; commit `b456462` already gated it to direct HTTP. `streamkeep/http.py parallel_http_download` is the replacement.
  Touches: all fifteen modules above, `streamkeep/capabilities.py`, `README.md`, `tests/test_download_options.py`.
  Acceptance: the option, its config key and its capability probe are removed; an existing config naming aria2c migrates to the built-in parallel downloader with a one-line notice rather than failing.
  Complexity: M

- [ ] P2 — V247 — Ratchet the coverage floor and cover the four largest untested UI modules
  Why: `fail_under = 64.0` leaves a wide slack band in which a real regression passes, and fifteen modules are never imported or named by any test — led by the one that owns four QThread workers, which is exactly the class of code that produced V228.
  Evidence: `.coveragerc:6`. Never-imported modules by size: `ui/tabs/storage.py` (65 KB, workers at `:33,53,84,122`), `ui/tabs/download_vod.py` (27 KB), `ui/tabs/analytics.py` (16 KB), `player/chapter_panel.py` (10 KB), `intelligence/highlight.py`, `ui/intelligence_dialog.py`, `workers/monitor_ops.py`, `db/channel_stats.py`, `db/transfers.py`, `db/integrity.py`, `db/maintenance.py`, `workers/backup.py`, `ui/preview_art.py`, `i18n/compile_translations.py`. V224 already covers `startup_check.py` and the crash surface — do not duplicate it here.
  Touches: `.coveragerc`, `tests/test_storage_ui.py`, `test_analytics.py`, `test_download_vod.py`, `test_channel_stats.py` (new).
  Acceptance: the four largest untested modules have tests that construct and drive their real code paths offscreen; `fail_under` is raised to the measured value minus one and a note records the ratchet policy.
  Complexity: M

- [ ] P2 — V248 — Build the rename undo the journal implies, or stop writing it
  Why: files are renamed, then a journal is written for an undo that does not exist and that nothing ever reads — and the journal write is swallowed, so even the record can fail silently after the rename has already happened.
  Evidence: `streamkeep/ui/rename_dialog.py:283` is the only repo-wide reference to `rename_undo.json`; the write at `:292` is inside a swallowing handler and the rename at `:269` precedes it. The project's own destructive-action policy prefers toast + undo, and notification clearing already implements that shape correctly (`ui/main_window.py:2980-3015`).
  Touches: `streamkeep/ui/rename_dialog.py`, `streamkeep/ui/main_window.py`, `tests/test_rename_dialog.py`.
  Acceptance: either an "Undo rename" action restores every file the journal names and reports what it could not restore, or the journal is removed and the dialog stops claiming reversibility. Related to V209 (destructive-action policy) but distinct — this one is a dead artefact, not a modal.
  Complexity: S

#### P3 — Under Consideration

- [ ] P3 — V249 — Verify C2PA Content Credentials on downloaded media
  Why: no media archiver in the surveyed field records provenance, and an archive's value rests on being able to say what a file is. C2PA verification became practical in 2026: spec 2.3 (2026-01-05) extends provenance to live streaming via CMAF segment signing, a public Trust List and conformance programme exist, and a maintained Python SDK ships. EU AI Act Art. 50 machine-readable AI-content marking took effect 2026-08-02, so the number of signed assets is about to rise sharply.
  Evidence: zero `c2pa` hits across `streamkeep/`. `spec.c2pa.org` 2.3; `opensource.contentauthenticity.org/docs/c2pa-python/`; `c2patool` v0.26.27 (2026-02-09). Distinct from the previously rejected C2PA *generation* — StreamKeep is not the authoring tool.
  Touches: `streamkeep/verify.py`, `streamkeep/metadata.py`, `streamkeep/capabilities.py`, `streamkeep/ui/tabs/history.py`, `tests/test_verify.py`.
  Acceptance: when the optional dependency is present, a completed recording's manifest is read, validated against the Trust List, and the verdict (signed/unsigned/invalid, signer, assertions) is written to the public sidecar and shown in History. Absent the dependency, nothing fails. Never mutate the media.
  Complexity: L

- [ ] P3 — V250 — Emit CMCD v2 on native HLS and DASH captures
  Why: CDNs treat requests carrying Common Media Client Data as player traffic; StreamKeep's native captures currently look like a bare fetcher, and 403/429 on non-YouTube hosts is a top-five category complaint. Cheap patch, measurable effect.
  Evidence: zero `cmcd` hits across `streamkeep/`. CTA-5004-A published February 2026 with structured-field encoding and event-mode reporting. Category evidence: yt-dlp #16142 (Kick), #16535 (Rumble), #13831 (429s). **Overlaps V201** (treat extraction identity and throttling as managed state): CMCD is the standardised *client-hint* half of that surface, not the identity/rotation half. Land V201 first if both are taken, or fold this in as its transport-hint component.
  Touches: `streamkeep/hls.py`, `streamkeep/dash.py`, `streamkeep/net_guard.py`, `streamkeep/http.py`, `tests/test_hls.py`.
  Acceptance: session and object keys are emitted per CTA-5004-A as request headers, off by default and toggleable; the payload carries no user identifier beyond a per-session random id, and a test asserts no stable cross-session identifier is sent.
  Complexity: M

- [ ] P3 — V251 — Add an Internet Archive destination preset over the existing S3 adapter
  Why: offsite push to a permanent public archive is an explicit competitor ask, and the transport already works — IA speaks S3 and the adapter already accepts a custom endpoint. What is missing is the item-metadata convention, which is a preset, not an adapter.
  Evidence: `streamkeep/upload/s3.py:6` documents `endpoint_url`, validated at `:78`, `:89-99`; `upload_file` at `:52` sets no `x-archive-meta-*` headers and IA's bucket-is-the-item-identifier convention is unmodelled. Ganymede #807 asks for exactly this.
  Touches: `streamkeep/upload/s3.py`, `streamkeep/upload/runtime.py`, `streamkeep/ui/tabs/settings.py`, `tests/test_upload_adapters.py`.
  Acceptance: an "Internet Archive" profile type pre-fills the endpoint, maps recording metadata to `x-archive-meta-*` headers, and requires explicit per-profile confirmation that uploads are public and permanent. Reuses the existing credential storage; adds no new dependency.
  Complexity: S

- [ ] P3 — V252 — Rename one of the two `HighlightWorker` classes
  Why: two unrelated QThread classes share a name, so `from … import HighlightWorker` reads correctly and can be wrong.
  Evidence: `streamkeep/intelligence/highlight.py:180` detects highlight ranges and emits `list`; `streamkeep/postprocess/clip_worker.py:194` concatenates ranges into a reel and emits `(bool, str)`.
  Touches: `streamkeep/postprocess/clip_worker.py`, `streamkeep/intelligence/highlight.py`, callers, `tests/`.
  Acceptance: names state what each does (for example `HighlightDetectWorker` and `HighlightReelWorker`); no import in the tree is ambiguous.
  Complexity: S

- [ ] P3 — V253 — Refresh the optional-engine currency claims
  Why: the README recommends installing two engines whose upstream state has moved — one is migrating off GitHub, the other has had no release in over two years — and the capability registry cannot refuse an outdated copy of either.
  Evidence: `streamkeep/integrations/gallery_dl.py:4` points at `github.com/mikf/gallery-dl`, which is moving to Codeberg (issue #9374, 128 reactions); `README.md:297` recommends `go install github.com/iawia002/lux@latest`, whose last release is v0.24.1 (2024-05-06) with 545 open issues. V194 added floors for `gallery_dl` and `transcribe_worker` — verify that claim actually covers both and that `faster-whisper` has one.
  Touches: `streamkeep/integrations/gallery_dl.py`, `streamkeep/integrations/lux.py`, `streamkeep/capabilities.py`, `README.md`.
  Acceptance: source URLs point at the maintained upstream, every optional engine has a declared floor the registry can enforce, and the README states lux's maintenance status rather than recommending it unqualified.
  Complexity: S

- [ ] P3 — V254 — Measure and publish time-to-capture for monitored channels
  Why: detection latency is the axis competitors advertise on and nobody in the field measures — LiveStreamDVR documents capture starting roughly two minutes after a stream goes live. StreamKeep already has escalated polling and start-from-beginning capture, so this is likely a strength that is currently invisible.
  Evidence: `streamkeep/monitor.py` `ChannelMonitor` polls on a QTimer tick through a two-slot `QThreadPool`; `db/channel_stats.py` already records live/offline transitions (wired by `795c213`), which is where the measurement belongs. Confidence: **Needs live validation** — no measurement was taken this pass.
  Touches: `streamkeep/monitor.py`, `streamkeep/db/channel_stats.py`, `streamkeep/ui/tabs/monitor.py` (Channel Insights), `README.md`.
  Acceptance: Channel Insights reports median and worst observed seconds between a channel going live and capture starting, over a bounded window; the number is derived from recorded transitions, not estimated.
  Complexity: M

- [ ] P3 — V255 — Move the installer to Inno Setup 7 x64 with extended-length path support
  Why: deep archive trees plus long channel and title templates hit MAX_PATH, which is a recurring failure mode for downloaders (yt-dlp #1136, 32 reactions). IS7 removes the limit at the installer level and installs alongside IS6, so the migration is reversible. Distinct from the previously rejected application-level long-path work.
  Evidence: `packaging/installer/streamkeep.iss` targets the IS6 lane; Inno Setup 7.0.2 (2026-07-13) adds a 64-bit edition (`SetupArchitecture=x64`), extended-length path support, and high-entropy ASLR.
  Touches: `packaging/installer/streamkeep.iss`, `packaging/build.py`, `tests/test_packaging.py`.
  Acceptance: the installer builds unsigned under IS7 x64, installs to a path longer than 260 characters, and the artifact smoke suite passes from that install. The unsigned posture is unchanged — no certificate, no signing step.
  Complexity: M
```

</details>
