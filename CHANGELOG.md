# Changelog

All notable changes to StreamKeep are recorded here for local release hygiene. `README.md` is the only tracked root Markdown file in this repo; this file is intentionally ignored by git per repo policy.

## [Unreleased]

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
