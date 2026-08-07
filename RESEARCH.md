# Research — StreamKeep
Date: 2026-08-06 — replaces all prior research.

Baseline measured at `fead7d6` (package v4.45.0): **release gate GREEN** on Python 3.14.6 (`py -3.14 packaging/release_gate.py --fast` → every non-build stage PASS, `tests` 312.2s, `advisories` clean); 1,712 tests collected; coverage floor raised to **64.0** (`.coveragerc`); pyflakes clean; ruff 47 findings, all `E402` (test files + launcher import ordering). 206 modules / 103.2k LOC in `streamkeep/`, 134 test files / 32.1k LOC. Schema v21.

Confidence is stated per finding. Items marked **Reproduced** were executed on this machine; **Verified** were traced to a reachable path in current source; **Assumption** is reasoning not yet proven.

## Executive Summary

StreamKeep is a local-first PyQt6 desktop archiver for live streams, VODs, podcasts and direct media, with native HLS/DASH extractors, a yt-dlp fallback, a SQLite library, and a ten-stage local release gate. **The entire 2026-08-04 finding set shipped the same day** across 54 commits: the remote queue payload is now allow-listed (`preflight.filter_remote_queue_payload`, called at `headless_service.py:499`), `db.init_db` refuses a newer schema (`db/_legacy.py:288`), `metadata.py:13` uses `defusedxml`, `smart_mode.normalize_pattern` no longer lower-cases regex bodies, upgrade pruning recycles, re-template orders renames, the web remote has real ARIA and i18n, scoped tokens can be listed (`server/_legacy.py:665`, `GET /api/tokens`), and BagIt fixity export exists. All verified in source — do not re-open them.

That burst is also the problem. Eleven new feature modules landed in one day with no subsequent audit, and the new code repeats the failure shapes the project has already paid for three times. Two things now cap value:

1. **The release gate mutates the user's real library.** Running `pytest` writes into `%APPDATA%\StreamKeep\` — the same directory holding `library.db`. Reproduced.
2. **The 2026-08-04 cohort is unaudited and has real defects**, including one that deletes a working runtime, one that runs a full YAML+regex registry parse on the GUI thread on every keystroke, and one that ships a bearer API key to an unvalidated plaintext URL.

**Two findings came from outside the repository and outrank most of the code list below.** *(a)* The SHA-pinned Deno runtime is **17 published advisories behind** — `javascript_runtime.py:28 DENO_VERSION = "2.3.1"` against a current 2.9.5, including a Critical `node:crypto` finalization bug ([GHSA-5379-f5hf-w38v](https://github.com/denoland/deno/security/advisories/GHSA-5379-f5hf-w38v), fixed 2.6.0), four Windows command-injection classes ([GHSA-m3c4-prhw-mrx6](https://github.com/denoland/deno/security/advisories/GHSA-m3c4-prhw-mrx6), [GHSA-7xh3-mhg9-jcw8](https://github.com/denoland/deno/security/advisories/GHSA-7xh3-mhg9-jcw8), [GHSA-m2gf-x3f6-8hq3](https://github.com/denoland/deno/security/advisories/GHSA-m2gf-x3f6-8hq3)), a TLS-retry plaintext risk ([GHSA-chqv-56wv-7564](https://github.com/denoland/deno/security/advisories/GHSA-chqv-56wv-7564)) and several `--allow-*`/`--deny-*` sandbox bypasses. This is the one component that executes untrusted remote player JavaScript, and the sandbox bypasses are precisely its threat model. The pip-audit `advisories` stage cannot see it — Deno is a downloaded binary, not a locked wheel. *(b)* **FFmpeg 9.0 shipped 2026-08-04** and `capabilities.py:32` declares a floor of `8.1.2` with **no upper bound**, so a 9.0 binary already in a user's PATH is accepted today, silently changing two behaviours: `tls_verify` now defaults on (`libavformat/tls.h` hardcodes `{.i64 = 1}` in `n9.0`, versus `TLS_VERIFY_DEFAULT 0` in `n8.1.2`), and pre-11.1 NVENC SDK support is removed, which can change what the hardware-encoder probe reports on older drivers. StreamKeep's own NVENC arguments are already modern (`codecs.py:157` uses `-preset p5 -rc vbr`, not the removed aliases), so this is a runtime-compatibility question, not an argument-string bug.

Top opportunities within the codebase, in priority order:

1. **`pytest` writes into the production config directory.** *Reproduced.* `tests/conftest.py` never rebinds `paths.CONFIG_DIR`, and every stateful module captures it at import (`db/_legacy.py:36`, `notifications.py:21,24`, `search.py:41`, `semantic.py:26`, `plugins.py:74`, `declarative.py:35`). Running a subset of the suite against a pristine `APPDATA` created `library.db`, `notifications.jsonl` and 12 KB of `security-events.jsonl`. The user's real directory shows `security-events.jsonl` at 1.6 MB against a 2 MB rotation cap, and `crash.log` containing a **pytest teardown traceback** — the crash handler is live during test runs. Nothing prevents a destructive test from reaching the real `library.db`.
2. **A failed Deno re-install deletes the working one.** *Verified.* `javascript_runtime.py:455-457` rolls back with `if target.exists() and not backup.exists(): shutil.rmtree(target)`, but `backup` is only created by the `os.replace` at `:442` — so any exception raised *before* that point (extraction, or the 8-second `_probe_executable` timeout on a freshly-written ~100 MB binary that Defender will scan) deletes the previously working runtime and reports an error that never mentions it.
3. **Every keystroke in the URL field re-parses the whole adapter registry on the GUI thread.** *Verified.* `download.py:319` wires `url_input.textChanged` → `download_single.py:246 _on_url_changed` → `Extractor.detect` → `extractors/base.py:49 detect_declarative_extractor` → `declarative.py:1045 discover_source_adapters`, which does `iterdir()` + `read_text()` + YAML parse + `re.compile` for up to 128 files of up to 256 KiB each, plus `load_config()` per call. There is no cache and no signature, despite the module docstring claiming "Definitions are parsed afresh when the registry signature changes."
4. **Adapter regexes are compiled from YAML and matched on the GUI thread with no ReDoS bound.** *Verified.* `declarative.py:813-829` checks only `len ≤ 512` and that it compiles; `DeclarativeDefinition.match:125` then runs `fullmatch` against the pasted path. `path_regex: "^(a+)+b$"` plus a crafted URL wedges the UI thread with no cancel path.
5. **Backup does not carry the state that prevents re-downloading.** *Verified.* `backup.py:35-41 BACKUP_FILES` is `config.json`, `library.db`, `tags.db`, `search.db`, `notifications.jsonl`. It omits `download-archives/` (written by `paths.source_archive_path`, passed to `yt-dlp --download-archive` at `workers/download.py`), `auth/`, `plugins/`, and `source_adapters/`. Restoring on a new machine loses the yt-dlp archive state and silently re-downloads everything — the exact complaint class of Pinchflat [#805](https://github.com/kieraneglin/pinchflat/issues/805).
6. **The worker-teardown list is still hand-maintained, and has drifted again.** *Verified.* 28 worker attributes are assigned on the main window; `closeEvent` (`ui/main_window.py:1455-1560`) names 22. `_semantic_index_worker`, `_backup_worker`, `_cred_probe_worker`, `_highlight_worker`, `_media_server_worker`, `_scene_worker`, `_storyboard_worker`, `_thumb_worker` and `_update_check_worker` are absent. This is the third instance of this exact bug (`_maintenance_worker` 2026-08-04, `sync_viewer` before it); the list is the defect, not the entries.
7. **The OpenAI-compatible translation endpoint bypasses `net_guard` and does not require TLS.** *Verified.* `translation.py:98-117` builds `api_url.rstrip("/") + "/v1/chat/completions"` and attaches `Authorization: Bearer {api_key}` with no scheme check and no `validate_remote_url` — the only remote fetch in the tree that skips the guarded transport. `_query_anthropic:129` correctly hardcodes HTTPS. All three providers also `json.loads(response.read())` with no size cap (`:93,:115,:136`).
8. **Post-download indexing blocks the GUI thread.** *Verified.* `ui/tabs/download_finalize.py:442 _index_finalized_recording` is called from the finalize-done slot (its own comment: "after the worker has finished") and synchronously runs `search.index_recording(out_dir)` and `semantic.index_recording(out_dir)` — sidecar reads, vector computation and SQLite writes — on the UI thread, after every download. A dedicated `SemanticIndexWorker` exists and this path does not use it.
9. **The database and server "split" is a rename.** *Verified.* `refactor(architecture): split database and server facades` (`f5652b6`) moved `db.py` → `db/_legacy.py` and `local_server.py` → `server/_legacy.py` behind attribute-forwarding facades. `db/_legacy.py` is **6,624** LOC (it was 5,962 on 2026-08-04 — it *grew* during the split) and `server/_legacy.py` is 2,748. `tests/test_architecture_boundaries.py:11` asserts the facade forwards the entire legacy surface, which locks the monolith in place and makes the commit message read as done work.
10. **The declarative-adapter surface has no review gate, unlike every comparable surface.** *Verified.* Plugins now require a fingerprinted contract review (`plugins.py:448 _trust_review_matches`), imported yt-dlp templates stay disabled until approved, and `config.py:677-683` quarantines `source_adapters` on config import — but a `.yaml` file dropped into `%APPDATA%\StreamKeep\source_adapters\` defaults to `enabled=True` (`declarative.py:876`) and is live on the next URL detection.

## Product Map

### Core workflows

- Resolve a URL via native extractors, YAML source adapters, or yt-dlp; inspect formats/tracks/subtitles; apply a Smart Mode profile or automation rule; download; finalize with metadata/NFO/chapters/chat/comments/thumbnails/manifest/BagIt.
- Queue, schedule, recur, pause, resume, retry and recover from desktop, CLI, REST API, browser extension or paired web remote, behind a per-profile execution lease and an allow-listed remote payload.
- Monitor Kick/Twitch channels and podcast feeds, auto-record on go-live, apply retention, media-server layouts and audited quality upgrades with recycled pruning.
- Adopt an existing library (`importer.py`), rebuild the index from on-disk sidecars (`rebuild.py`), re-template an archive (`maintenance.py`), scrub integrity on a schedule (`integrity.py`), replay history actions (`db` action log).
- Capture outside yt-dlp: raw FFmpeg protocol jobs, guarded Streamlink, DRM-free MSE recorder, gallery-dl image sets, lux.
- Search, verify, tag, play, clip, transcribe (faster-whisper or gated FFmpeg backend), summarize, translate metadata, publish (gallery/RSS), upload, back up, restore.

### User personas

- Personal media archivists who want durable files and sidecars over a hosted service.
- Stream/channel/podcast collectors needing unattended capture and recovery.
- Creators and researchers who clip, transcribe or review offline material.
- Single-owner self-hosted operators automating StreamKeep without a media server.

### Platforms and distribution

- Python 3.11+ for source; **the release lane and gate now require Python 3.14.6+** (`packaging/release_gate.py:34 MIN_RELEASE_PYTHON`). PyQt6 with `pyqt6-qt6==6.11.1`. MIT.
- Unsigned by policy: PyInstaller onedir, Inno Setup installer, WinGet manifest, Flatpak self-build manifest, browser extension. MSIX lane retired (`d776863`). No macOS/Linux artifact (existing blocked V53).
- Runtime floors: FFmpeg/ffprobe 8.1.2, curl 8.21.0, yt-dlp 2026.07.04, Deno 2.3.1 pinned and SHA-verified. DRM circumvention out of scope throughout.
- Desktop languages: English (1,836 messages, 100%) and Spanish (**14.8% translated**, 1,564 of 1,836 unfinished), labelled beta in `README.md:240`. Web remote is `Accept-Language`-driven with English fallback.

### Key integrations and data flows

- Native Kick/Twitch/Rumble/SoundCloud/Reddit/Audius/podcast-RSS/HLS/DASH/direct paths, then YAML declarative adapters, then a yt-dlp catch-all (`extractors/base.py:35-52`). gallery-dl, lux, Streamlink, yt-dlp-ytse and an optional remote `youtube_backend` are opt-in engines.
- SQLite schema v21 holds history, queue, monitor, failures, manifests, tombstones, publishing, the history action log and FTS; `search.db`, `tags.db` and `semantic.db` are separate; JSON holds preferences only; credentials live in the OS store behind `secretref:` handles.
- Remote URLs route through `net_guard`, which resolves DNS, checks every returned address, and pins each connection through a short-lived loopback proxy — with the two documented exceptions in this report (`translation.py`, `javascript_runtime.py`).

## Competitive Landscape

The 2026-08-04 pass established the field; this pass re-tested it against community signal rather than re-describing it.

### Pinchflat (5,204★ — the stranded userbase)

- Does well: single-container simplicity, which is why users chose it over TubeArchivist.
- Learn: **still on an indefinite pause** (last upstream commit 2025-12-16, [#800](https://github.com/kieraneglin/pinchflat/issues/800) with 255 reactions, 212 open issues). Its two flagship unmet asks — [#408](https://github.com/kieraneglin/pinchflat/issues/408) retroactive re-templating and [#805](https://github.com/kieraneglin/pinchflat/issues/805) don't-re-download-deleted — both already ship in StreamKeep. Make the importer's Pinchflat path explicit and documented; then make backup carry the archive state so the migration does not regress on restore (opportunity 5).
- Avoid: assuming the window stays open.

### Ganymede (Go, Docker-only — closest direct competitor)

- Does well: chat rendered with typed events, live-vs-VOD quality per channel, named API-key management.
- Learn: both gaps are now closed on StreamKeep's side (`57580cb` typed chat events, `5b8ab36` scoped token management with `GET /api/tokens`). The remaining lesson is Ganymede's *per-channel* live/VOD quality split.
- Avoid: Docker-only delivery and Twitch-only scope — Ganymede declined Kick ([#311](https://github.com/Zibbp/ganymede/issues/311)) and that is StreamKeep's defensible ground.

### yt-dlp itself (the dependency that is also the competitor's ceiling)

- Does well: 1000+ sites, and a maintainer team that says no to scope.
- Learn: the highest-value roadmap in this space is the list of things yt-dlp has **declined or deferred for years**, because a desktop app owning its own queue can build them and a CLI structurally will not: parallel download+process ([#1918](https://github.com/yt-dlp/yt-dlp/issues/1918), 46 reactions, open since 2021), a machine-readable error taxonomy ([#1659](https://github.com/yt-dlp/yt-dlp/issues/1659), [#457](https://github.com/yt-dlp/yt-dlp/issues/457)), a persistent failed-download log ([#7832](https://github.com/yt-dlp/yt-dlp/issues/7832)), stable parseable progress ([#2197](https://github.com/yt-dlp/yt-dlp/issues/2197), [#1317](https://github.com/yt-dlp/yt-dlp/issues/1317)). StreamKeep already has the queue, the failure ledger and the progress model — the missing piece is the **typed error taxonomy** that tells "retry later" from "gone forever".
- Learn (second): platforms now expose AI-synthesised tracks that ordinary format sorting prefers — "Super Resolution" upscaled video ([#15433](https://github.com/yt-dlp/yt-dlp/issues/15433)) and AI-dubbed audio ([#11834](https://github.com/yt-dlp/yt-dlp/issues/11834)). An archiver that silently stores the synthesised version of the thing it was asked to preserve has broken its own custody promise; original-first selection with an explicit opt-in is the correct default and nobody in the field does it yet.
- Avoid: owning extractors as the *only* path. The Kick extractor is broken upstream right now ([#17284](https://github.com/yt-dlp/yt-dlp/issues/17284), open, updated 2026-08-04); the YouTube 403/SABR cycle ran roughly monthly through 2026 ([#15569](https://github.com/yt-dlp/yt-dlp/issues/15569), [#15750](https://github.com/yt-dlp/yt-dlp/issues/15750), [#16212](https://github.com/yt-dlp/yt-dlp/issues/16212)); and there was a 12-week gap between stable releases (2026.03.17 → 2026.06.09) while YouTube broke repeatedly. Rent the extractors, own the queue and the library, and make the rent **visible** in the UI.

### ytarchive / LiveStreamDVR / TwitchDownloader (the dead and the narrow)

- Do well: nothing StreamKeep lacks; they are cited here for their *failure reports*.
- Learn: the recurring live-capture complaints are silent stops with no resume ([ytarchive#213](https://github.com/Kethsar/ytarchive/issues/213), [#272](https://github.com/Kethsar/ytarchive/issues/272), [#227](https://github.com/Kethsar/ytarchive/issues/227)), one giant fragile mux at the end ([#112](https://github.com/Kethsar/ytarchive/issues/112), [#116](https://github.com/Kethsar/ytarchive/issues/116)), and post-processing blocking the next queue item ([TwitchDownloader#807](https://github.com/lay295/TwitchDownloader/issues/807)). StreamKeep solved the third (finalize is a worker) and partly the first (resume sidecars); **incremental mux and periodic flush for multi-hour captures is still open** — a crash mid-capture still costs the whole tail.
- Avoid: their scope. LiveStreamDVR and ytarchive are unmaintained; do not port their architecture.

### Sonarr / Radarr (adjacent — the demand shape)

- Does well: ordered quality ladders, upgrade cutoff, ~29 accept/reject specifications each returning a **named reason**.
- Learn: the demand is explicitly "*arr for YouTube/streams" ([HN 43670401](https://news.ycombinator.com/item?id=43670401), [HN 45365310](https://news.ycombinator.com/item?id=45365310)). StreamKeep shipped the audited upgrade engine; the missing half is the *named reason* on rejection, which is the same primitive as the error taxonomy above.
- Avoid: indexer/protocol scoring and health-check sprawl that assumes a server deployment.

### Media-server ingestion (the cottage industry that proves the gap)

- Does well: nothing — it exists because yt-dlp metadata does not survive into Jellyfin/Plex/Kodi. At least five separate projects patch this: [ytdl-nfo](https://pypi.org/project/ytdl-nfo), [yt-dlp-metadata2nfo](https://github.com/unacro/yt-dlp-metadata2nfo), [jf-ytdlp-info-reader-plugin](https://github.com/arabcoders/jf-ytdlp-info-reader-plugin), [jellyfin-youtube-metadata-plugin](https://github.com/ankenyr/jellyfin-youtube-metadata-plugin), ytdl-sub.
- Learn: StreamKeep writes NFO at capture time, which is the correct answer and already a differentiator. The unsolved neighbour is a video belonging to **more than one collection** ([ytdl-sub#826](https://github.com/jmbannon/ytdl-sub/discussions/826)) — the season-folder model forces one.
- Avoid: building a media server.

### Commercial set (4K Video Downloader+, Stacher, Downie, IDM, JDownloader)

- Do well: what they **paywall** is the signal. Stacher's Premium is literally a media library; 4K reserves channel subscriptions + auto-download for its Pro tier and meters concurrency; IDM and JDownloader sell scheduling with time windows.
- Learn: StreamKeep gives all of those away. The remaining paywalled primitive worth taking is **adaptive rate governance** — users currently hand-tune `--sleep-interval`/`--limit-rate`/`--concurrent-fragments` and still hit 429s ([yt-dlp#13831](https://github.com/yt-dlp/yt-dlp/issues/13831)).
- Avoid: StreamFab/CleverGet/KeepStreams and the DRM-removal category entirely.

## Security, Privacy, and Reliability

Every item below was traced to a reachable path in current source. **Reproduced** items were executed.

- **[Reproduced — data safety] The test suite writes into the production config directory.** `tests/conftest.py` sets only `QT_QPA_PLATFORM` and a `QApplication` fixture; it never calls `paths.bind_config_dir`. Running `pytest -k "notification or security or server or local_server"` with `APPDATA` pointed at an empty directory created `StreamKeep/library.db` (0 bytes), `StreamKeep/notifications.jsonl` (148 B) and `StreamKeep/security-events.jsonl` (11,962 B). On the real profile the same paths are live: `security-events.jsonl` at 1,635,832 B (cap 2 MB, `notifications.py:25`), and `crash.log` containing a `_pytest/pathlib.py` traceback — so `crash_log.py`'s global handler is installed during test runs and writes to the user's file. The release gate's `tests` stage inherits all of this.
- **[Verified — data loss] `install_managed_deno` deletes the working runtime on any pre-swap failure.** `javascript_runtime.py:455-457`. The `except` guard tests `not backup.exists()`, which is true for every exception raised before `os.replace(target, backup)` at `:442` — including `_extract_executable` and the 8 s `_probe_executable` timeout at `:349`. Fix: set a `moved_existing` flag immediately after the replace and gate the `rmtree` on it.
- **[Verified — availability] Full adapter-registry parse on the GUI thread, per keystroke.** `declarative.py:1045 detect_declarative_extractor` → `discover_source_adapters` with no cache, reached from `url_input.textChanged`. Each call: `iterdir()`, up to 128 × `read_text()` + `yaml.load` + `re.compile`, plus `_config_entries` → `load_config()` (a disk read and secret resolution). The docstring's "registry signature" does not exist anywhere in the module.
- **[Verified — availability] Adapter `path_regex` is an unbounded ReDoS vector on the GUI thread.** `declarative.py:813-829` validates length and compilability only; `match:125` runs `fullmatch` on the pasted path. Config-imported adapters are schema-validated (`config.py:440`) but the schema does not restrict regex shape.
- **[Verified] `MAX_DEFINITION_BYTES` is enforced after the whole file is read.** `declarative.py:990` calls `path.read_text()` and the 256 KiB check happens inside `parse_definition_text:891`. `path.is_file()` follows symlinks, so a link to a multi-GB file causes `MemoryError` on every keystroke. Fix: `stat().st_size` check before reading.
- **[Verified] The custom YAML loader drops PyYAML's own guards.** `declarative.py:659-669` replaces `SafeConstructor.construct_mapping` wholesale, so `flatten_mapping` never runs (a `<<:` merge key becomes a literal `"<<"` field) and an unhashable key raises `TypeError` instead of `ConstructorError`. `TypeError` is caught by neither `except yaml.YAMLError:675` nor `except (OSError, UnicodeError, DeclarativeAdapterError):999`, so it escapes as a raw traceback from Settings diagnostics and config import.
- **[Verified] Adapter load errors are invisible on the only path users hit.** `detect_declarative_extractor:1046` discards the `errors` list and `extractors/base.py:53-56` wraps the whole call in `except Exception: pass`. A typo'd YAML file silently falls through to the yt-dlp catch-all; the error appears only in Settings diagnostics or the CLI.
- **[Verified] Declarative adapters have no enable-time review.** `enabled` defaults to `True` (`declarative.py:876`), files are discovered by `iterdir()` and go live on next detection. Plugins require a contract fingerprint review (`plugins.py:448`) and imported templates require approval; adapters — which describe outbound requests and response mapping — do not.
- **[Verified] A single adapter response can force ~2,000 blocking DNS lookups.** `declarative.py:292-311` validates `source`, `webpage_url`, `thumbnail_url` and `feed_url` per item across up to `MAX_ITEMS = 500` items, and `validate_remote_url` performs `socket.getaddrinfo` synchronously (`net_guard.py:86`). A hostile source stalls the fetch worker for hours and turns the app into a DNS oracle.
- **[Verified] The declarative HTML path has unbounded recursion and super-linear selection.** `_walk_html:593` and `_HTMLNode.text():545` are recursive against an 8 MB body cap; `_select_html_nodes:622` re-walks each candidate's whole subtree per selector token with no dedupe. A `RecursionError` is not in the caught set at `:1100-1103`.
- **[Verified] The declarative HTTP response is never closed** (`declarative.py:1076-1099`) — the socket to the guarded proxy is reclaimed only at GC.
- **[Verified — credential exposure] The OpenAI-compatible translation path bypasses `net_guard` and permits plaintext.** `translation.py:98-117`: no `validate_remote_url`, no scheme check, `Authorization: Bearer` attached. A config-supplied `http://10.0.0.5:8080` sends the key in cleartext to a host `net_guard.address_allowed` would reject.
- **[Verified] Translation provider responses are read unbounded.** `translation.py:93,115,136` — `json.loads(response.read())` with no cap, including the unauthenticated `http://localhost:11434` ollama endpoint. Any process squatting that port can OOM the finalize path. The module defines `MAX_TRANSLATION_CHARS` and applies it only to inputs.
- **[Verified] `translation.py:253-275` double-closes a file descriptor.** `os.fdopen(fd)` inside `with` closes it; if the subsequent `os.replace` fails the `except` calls `os.close(fd)` again, and in a multi-threaded Qt process that number may already have been reissued. The resulting `OSError` is swallowed.
- **[Verified — availability] Post-download indexing runs on the UI thread.** `ui/tabs/download_finalize.py:442-465`, called from the finalize-done slot at `:186` and directly at `:440`. Runs `search.index_recording` and `semantic.index_recording` synchronously.
- **[Verified] Nine window-attached QThreads are missing from `closeEvent`.** 28 assigned vs 22 named. Missing: `_semantic_index_worker`, `_backup_worker`, `_cred_probe_worker`, `_highlight_worker`, `_media_server_worker`, `_scene_worker`, `_storyboard_worker`, `_thumb_worker`, `_update_check_worker`. Destroying a running `QThread` is `qFatal`, and `semantic.db` is mid-transaction.
- **[Verified — data safety] Backup omits four live state directories.** `backup.py:35-41`. Missing: `download-archives/` (drives yt-dlp skip decisions), `auth/` (`auth_profiles.py:36`), `plugins/` (`plugins.py:74`), `source_adapters/` (`declarative.py:35`). `semantic.db` is documented as intentionally excluded, but nothing reconciles it after a restore either — `_prune_paths` runs only inside `SemanticIndexWorker.run():601`, so a restored older `library.db` leaves semantic hits pointing at recordings the library no longer knows.
- **[Verified] Browser-cookie import reports the wrong cause.** `cookies.py:66-73` swallows the rookiepy exception with `except Exception: cj = None`. When rookiepy is installed but fails on a locked or app-bound-encrypted store, the user is told "No cookie loader found for 'chrome'. Install rookiepy…" — a diagnosis that is both wrong and unactionable. This is the single highest-reaction open yt-dlp issue ([#7271](https://github.com/yt-dlp/yt-dlp/issues/7271), 61 reactions, 105 comments).
- **[Verified] Taskbar terminal state has a dead conditional.** `windows_integration.py:71` — `"completed": 0 if failed else 0`, both arms zero, while `total` is an item count on that branch and hundredths elsewhere (`:105-106`). A batch that ends with a failure paints a zero-width red bar.
- **[Verified] `capabilities` probing can raise on an unsupported host.** `javascript_runtime.host_target()` raises `DenoRuntimeError` for anything outside five pinned triples; `capabilities.py:400-403 get_runtime_capabilities` has no guard, so Windows-on-ARM64 with no PATH runtime raises instead of returning a "missing runtime" record.
- **[Verified] `power.py:189` hibernates instead of sleeping.** `rundll32.exe powrprof.dll,SetSuspendState 0,1,0` — rundll32 ignores the hibernate argument, so any machine with hibernation enabled hibernates.
- **[Verified] The "managed runtime verified" claim is metadata-only.** `javascript_runtime.py:187-193` trusts `runtime.json` and `executable.is_file()`; the binary is never re-hashed, and the displayed `sha256` is copied from the descriptor. Same-privilege only, but the label is misleading — call it `pinned_archive_sha256`.
- **[Verified — supply chain] The pinned Deno runtime is 17 advisories behind.** `javascript_runtime.py:28-29` pins `DENO_VERSION = "2.3.1"` with a `DENO_MINIMUM_VERSION` of 2.3.0 and five SHA-256-pinned assets (`:45-65`); Deno 2.9.5 is current as of 2026-08-06. OSV reports 17 advisories affecting 2.3.1, including one Critical and four Windows command-injection classes, plus `--allow-*`/`--deny-*` sandbox bypasses and a fetch/WebSocket DNS-check bypass. The `advisories` gate stage runs pip-audit over `requirements.lock` and structurally cannot see a downloaded binary — this is a blind spot in the gate, not just a stale pin.
- **[Verified — compatibility] FFmpeg 9.0 is accepted by the current floor.** `capabilities.py:32` declares `"ffmpeg": "8.1.2"` with no ceiling; FFmpeg 9.0 released 2026-08-04. Two silent behaviour changes follow: `tls_verify` defaults on in `n9.0/libavformat/tls.h` (it was `TLS_VERIFY_DEFAULT 0` in `n8.1.2`), which affects capture through an inspecting proxy or a self-signed origin and makes the blocked V29 item newly live; and the removal of pre-11.1 NVENC SDK support changes what `postprocess/codecs.py:82 _probe_hw_encoder` reports on older drivers. StreamKeep's encoder arguments are unaffected — `codecs.py:157` already uses `-preset p5 -rc vbr`, and the `-global_quality` at `:159` is the QSV path, not the NVENC one the 9.0 changelog deprecates.
- **[Verified — low] The `send2trash` source floor crosses a major boundary.** `requirements.txt:8` declares `send2trash>=1.8` while `requirements.lock:798` pins `send2trash==2.1.0` (major bump released 2026-01-14). Frozen and locked installs are correct and the `dependency-floors` gate keeps the source floor from exceeding the lock, so the exposure is a source install resolving a future 3.x. No advisory affects either version.
- **[Verified, no action] Dependency and gate posture is otherwise sound.** The `advisories` stage (pip-audit over the hash-pinned `requirements.lock`, not the ambient environment) and `dependency-floors` both PASS on 2026-08-06. The `cryptography` floor moved to 50.0.0, closing CVE-2026-69247. `pyflakes` is clean, closing the 2026-08-04 gate failure. **Scope limit:** this is gate-verified against the pinned lock; an upstream-currency sweep for packages released 2026-08-05/06 was not completed this pass, so "no known advisory" is stronger evidence here than "on the latest version". The blocked lock-bump entry in `Roadmap_Blocked.md` still owns advancing the pins.
- **[Verified, no action] The release gate cannot run below Python 3.14.6.** `packaging/release_gate.py:34 MIN_RELEASE_PYTHON = (3, 14, 6)` fails the first stage on 3.12 (reproduced), while `README.md:277` keeps a 3.11+ source floor. Deliberate per `914def6`, but it means a contributor on the documented floor cannot run any part of the project's own check. See Open Questions.
- **[Verified, no action] Theme contrast is in good shape.** Rendering all three palettes and checking every QSS rule block that pairs `color:` with `background-color:` produced **zero** failures in dark and high-contrast, and one marginal failure in light: `QPushButton#toggleAccent:checked` at **4.40:1** (`#2563d9` on `#e1e8ef`). `tests/test_visual_system.py:62` already enforces ≥ 4.5:1 for four text tokens on two surfaces; every `overlay0`-as-text use is a `:disabled` state, which WCAG 1.4.3 exempts. The earlier "text on accent" concern is a non-issue — `theme.py:124 _accent_text` picks black or white by measured contrast.
- **[Verified] The broad-exception guardrail checks for a comment, not a reason.** `tests/test_exception_annotations.py:11` requires any `#` on or above the `pass`; 170 `except Exception: pass` sites now satisfy it, many with the identical boilerplate `# safe: best-effort fallback; preserve the primary operation`. The count did not fall and no failure became visible.

## Architecture Assessment

- **The decomposition is now nominal.** `f5652b6` split `db` and `local_server` into packages whose implementation is a single `_legacy.py` — 6,624 and 2,748 LOC — with sibling modules that are 400–2,800 byte re-export shims. `db/_legacy.py` grew by 662 lines across the same commit range. `tests/test_architecture_boundaries.py:11-21` asserts the facade forwards the whole legacy surface and that patching `db._connect` reaches `_implementation`, which makes the monolith a tested contract. The remaining unsplit files are `cli.py` (3,148), `ui/main_window.py` (3,083), `ui/tabs/settings.py` (3,050), `workers/download.py` (2,996), `ui/tabs/download_queue.py` (2,527). Real progress requires moving *behaviour* out of `_legacy.py`, with the boundary test asserting what each domain module owns rather than that nothing changed.
- **Worker lifecycle needs a registry, not a longer list.** Three separate shipped bugs share one shape: a `QThread` attached to the main window that `closeEvent` does not know about. There are 40 `QThread` subclasses and a hand-maintained 22-name teardown block. A `register_worker(attr, label, timeout)` helper called at start, with `closeEvent` draining the registry, converts a recurring defect into a structural invariant and would have caught all three.
- **Test isolation is the missing seam under everything else.** The suite runs against ambient `CONFIG_DIR` because `paths` is imported for its module-level constants and `bind_config_dir` exists only for the CLI. An autouse session fixture that calls `bind_config_dir(tmp_path)` before `streamkeep.db`/`config`/`notifications` are imported — plus a test asserting no writes land outside it — is a small change that makes the 64% coverage floor trustworthy and stops the gate from touching user data.
- **The 2026-08-04 cohort has no audit pass.** `declarative.py` (1,109), `semantic.py` (607), `health.py` (551), `javascript_runtime.py` (506), `translation.py` (372), `windows_integration.py` (293) all landed on one day. `health.py` is clean on inspection (`_write_snapshot:155-166` is temp + fsync + `os.replace`, conditions are bounded, transitions are stable). `windows_integration.py`'s ctypes/COM mechanics are correct (`_GUID` layout, `bytes_le`, `argtypes`/`restype`, full-width HWNDs, balanced `CoUninitialize` for the `S_FALSE` case). The defects concentrate in `declarative.py` and `translation.py`.
- **Declarative adapters are a plugin system without the plugin system's controls.** SSRF containment is genuinely good — `_guarded_request:1053-1104` validates, proxies, re-validates each redirect target after `urljoin`, restricts methods to GET/HEAD and headers to a seven-name allowlist with CRLF rejection, and adapters cannot outrank native extractors. What is missing is everything *around* the request: no enable-time review, no cache, no ReDoS bound, no size pre-check, no error surfacing. `_VOD_FIELDS` and `_STREAM_FIELDS` (`:70-79`) are defined and referenced nowhere, so `response.fields` names are never checked against the model contract they document.
- **Testing:** 1,712 tests, 134 files, floor raised to 64.0 — a real improvement over the stale 47.5. New coverage landed for `main_window_jobs`, `player`, `postprocess` workers, `metadata`, `declarative`, `semantic`, `health`, `translation`, `comments`. The untested-and-important remainder is now the *interaction* layer: nothing exercises `closeEvent` teardown against a live worker set, and nothing asserts that a test run leaves the config directory untouched.
- **Observability is good but under-used.** Logging rotates, redacts and bridges `logging`; `health.py` produces a bounded standing-conditions snapshot exposed to desktop, CLI and API. The gap is that the newest failure paths (adapter parse errors, cookie-import cause, translation provider failure) do not reach it.
- **Cross-platform parity improved.** `7b39d59` registered the `streamkeep://` scheme cross-platform, closing the Windows-only hole the 2026-08-04 pass found in `protocol.py`. The Flatpak lane now routes archive folders through portals (`f7d7c0a`).
- **Repo hygiene has drifted from the repo's own rules.** `AGENTS.md` states README.md is the only tracked `.md` and that `COMPLETED.md` must never exist; both `COMPLETED.md` and `RESEARCH_REPORT.md` are present at root, and `RESEARCH.md`/`ROADMAP.md` are **tracked in git** (they show as modified, with the entire 2026-08-04 pass uncommitted). `RESEARCH_REPORT.md` duplicates `RESEARCH.md`'s purpose with 2026-07-era content. The last tag is `v4.44.0` against package v4.45.0 — v4.45.0 shipped untagged, so `packaging/winget_hash.py` has no tag to point at.

## Rejected Ideas

- **A plugin/adapter store or catalog** — youwee ships one; fetching and executing third-party definitions from a remote catalog is incompatible with this trust model. Take the *review* UI, not the store. (Source: 2026-08-04 pass; unchanged.)
- **Flathub submission** — Flathub prohibits AI-generated/AI-assisted code and AI-opened submission PRs with permanent bans. The Flatpak lane is a self-build convenience. (Source: docs.flathub.org requirements.)
- **European Accessibility Act compliance work** — Directive (EU) 2019/882 Article 2's product and service lists are closed and include neither desktop applications nor a local archiving tool. Claim "WCAG 2.2 AA as interpreted by WCAG2ICT" instead.
- **C2PA / ISO 22144 manifest generation** — its trust model relies on signer identity, which the no-signing policy forbids permanently. Reading inbound manifests is worthwhile; generating one is not.
- **Raising the FFmpeg floor to 9.0** — keep 8.1.2. But the 2026-08-04 pass's framing was incomplete: because the floor has no *ceiling*, 9.0 is already accepted, so "don't adopt" is not a decision the project gets to make unilaterally. Detect and adapt instead (see the Security section and V173); discriminate on the `libavcodec` major (62 for 8.1.2, 63 for 9.0) rather than a version string.
- **AV2** — spec-final 2026-05-28 but zero playback surface. AV1 remains the correct re-encode target; remux-without-transcode remains correct for archiving.
- **Docker-first or multi-user deployment** — occupied by eight competitors; adopting it displaces the desktop posture that is the differentiator and complicates the loopback trust model.
- **PostgreSQL or an external metadata database** — requested in competitors (Youtarr #302, Pinchflat #790); the answer for a single-user desktop app is a rebuildable index plus exportable sidecars, which both exist.
- **A native mobile client** — the web remote is now responsive and keyboard/screen-reader accessible (`8837d62`, `a5dd266`), and it inherits the existing token model. A separate mobile app would duplicate the entire trust boundary for a surface the responsive remote already covers, on a platform where the app cannot hold the archive anyway.
- **Native messaging for the browser extension** — loopback→loopback is not gated by WICG Local Network Access; a per-user host manifest plus an on-disk host binary is worse install friction and drags the unsigned-binary problem into the browser.
- **Windows long-path and reserved-name work** — already shipped. `utils.py:135 WINDOWS_SAFE_PATH_LENGTH = 240`, `MAX_PATH_COMPONENT_BYTES`, `truncate_utf8_bytes`, and `_TEMPLATE_RESERVED_NAMES:190` covering CON/PRN/AUX/NUL/COM*/LPT*, with trailing dot/space and traversal rejection. Listed so the recurring yt-dlp complaint ([#1136](https://github.com/yt-dlp/yt-dlp/issues/1136), [#12014](https://github.com/yt-dlp/yt-dlp/issues/12014)) is not mistaken for a gap.
- **Streaming the chat converter** — Ganymede had to rewrite its chat conversion in v4.19.0 because it loaded multi-GB chat files into memory. StreamKeep's renderer already streams (`postprocess/chat_render_worker.py:90` iterates the file line by line). No action.
- **Twitch VOD chat-replay download** — already shipped (`workers/finalize.py:193,498-504`, `extractors/twitch.py:327`), plus YouTube live-chat replay normalization (`chat/youtube_replay.py`). Do not re-add from [yt-dlp#1551](https://github.com/yt-dlp/yt-dlp/issues/1551).
- **Per-source download archives** — already shipped: `paths.source_archive_path` hashes the canonical URL into `download-archives/<sha256>.txt`. [yt-dlp#953](https://github.com/yt-dlp/yt-dlp/issues/953) does not apply.
- **A "text on accent" contrast fix** — measured and false. `theme.py:124 _accent_text` selects black or white by contrast for every accent-backed control.
- **Re-opening the 2026-08-04 finding set** — queue-payload confinement, schema-downgrade guard, `defusedxml` in `metadata.py`, smart-mode regex preservation, graceful capture stop, re-template ordering and recovery, rebuild fingerprint/swap recovery, recycled upgrade pruning, `sync_viewer` card retention, upload resume probes, scoped-token listing, BagIt export, typed chat events, nested DATERANGE schedules, cross-platform scheme registration, web-remote a11y and i18n are all verified shipped in source.
- **`hooks.py` POSIX process-tree kill** — inspected in a prior pass and found correct (`os.killpg(os.getpgid(...))`). No action.
- **Audited and found clean this pass — do not re-investigate:** `health.py` snapshot atomicity and condition lifecycle; `windows_integration.py` COM/ctypes mechanics; `javascript_runtime.py` archive handling (URL fixed, hash verified *before* extraction, single-member extraction rejecting absolute paths, drive prefixes, `..`, symlinks and directories, with entry-count and size caps, staged install published by `os.replace`); `declarative.py` SSRF containment, YAML `SafeLoader` derivation, extractor precedence and duplicate-id rejection; `translation.py` output safety (bounded fields, chapter count and timings copied from the original, sidecars written to new `metadata.{lang}.json` names via fsync + `os.replace`, language segment regex-constrained, NFO overrides XML-escaped) and the fact that cloud consent gating cannot be bypassed on any real path; theme contrast across all three palettes.

## Sources

### Direct OSS competitors and analogues
- https://github.com/kieraneglin/pinchflat/issues/800
- https://github.com/kieraneglin/pinchflat/issues/408
- https://github.com/kieraneglin/pinchflat/issues/805
- https://github.com/kieraneglin/pinchflat/issues/648
- https://github.com/Zibbp/ganymede/issues/311
- https://github.com/jmbannon/ytdl-sub/discussions/826
- https://github.com/lay295/TwitchDownloader/issues/807
- https://github.com/lay295/TwitchDownloader/issues/721
- https://github.com/Kethsar/ytarchive/issues/112
- https://github.com/Kethsar/ytarchive/issues/213
- https://github.com/tubearchivist/tubearchivist/issues/265
- https://github.com/tubearchivist/tubearchivist/issues/915
- https://github.com/MrBrax/LiveStreamDVR
- https://pypi.org/project/ytdl-nfo
- https://github.com/arabcoders/jf-ytdlp-info-reader-plugin

### yt-dlp: declined/deferred asks and platform churn
- https://github.com/yt-dlp/yt-dlp/issues/1918
- https://github.com/yt-dlp/yt-dlp/issues/1659
- https://github.com/yt-dlp/yt-dlp/issues/457
- https://github.com/yt-dlp/yt-dlp/issues/7832
- https://github.com/yt-dlp/yt-dlp/issues/2197
- https://github.com/yt-dlp/yt-dlp/issues/7271
- https://github.com/yt-dlp/yt-dlp/issues/13831
- https://github.com/yt-dlp/yt-dlp/issues/9094
- https://github.com/yt-dlp/yt-dlp/issues/15433
- https://github.com/yt-dlp/yt-dlp/issues/11834
- https://github.com/yt-dlp/yt-dlp/issues/17284
- https://github.com/yt-dlp/yt-dlp/issues/14189
- https://github.com/yt-dlp/yt-dlp/issues/16766
- https://github.com/yt-dlp/yt-dlp/pull/13515
- https://github.com/yt-dlp/yt-dlp/issues/16212
- https://github.com/streamlink/streamlink/issues/6109
- https://github.com/streamlink/streamlink/issues/2936

### Platform retention and behaviour
- https://help.kick.com/en/articles/7112432-kick-stream-replays-vods
- https://help.kick.com/en/articles/14994284-my-vod-is-missing-or-not-appearing-after-my-stream
- https://github.com/pixeltris/TwitchAdSolutions

### Community signal
- https://news.ycombinator.com/item?id=45358980
- https://news.ycombinator.com/item?id=43373242
- https://news.ycombinator.com/item?id=43670401
- https://news.ycombinator.com/item?id=45365310
- https://news.ycombinator.com/item?id=47588658

### Missed-project scan (desktop and niche prior art)
- https://github.com/mhogomchungu/media-downloader
- https://github.com/giantpinkrobots/varia
- https://github.com/axcore/tartube
- https://github.com/SamTV12345/PodFetch
- https://github.com/Brisppy/twitch-archiver
- https://github.com/xenova/chat-downloader
- https://github.com/TwitchRecover/TwitchRecover
- https://github.com/TheNestorHD/BetterKick
- https://github.com/ihmily/DouyinLiveRecorder

### Competitor movement 2026-08-01 → 2026-08-06
- https://github.com/Zibbp/ganymede/releases/tag/v4.19.0
- https://github.com/vanloctech/youwee/releases/tag/v0.20.1
- https://github.com/alexta69/metube/releases/tag/2026.08.04
- https://github.com/meeb/tubesync/releases/tag/v0.18.3
- https://github.com/NickvisionApps/Parabolic/issues/1947
- https://github.com/yt-dlp/yt-dlp/pull/17322

### Dependency and runtime currency (checked 2026-08-06)
- https://github.com/denoland/deno/releases
- https://github.com/denoland/deno/security/advisories/GHSA-5379-f5hf-w38v
- https://github.com/denoland/deno/security/advisories/GHSA-7xh3-mhg9-jcw8
- https://github.com/denoland/deno/security/advisories/GHSA-chqv-56wv-7564
- https://github.com/FFmpeg/FFmpeg/blob/n9.0/Changelog
- https://ffmpeg.org/download.html
- https://curl.se/docs/vuln.json
- https://www.python.org/downloads/release/python-3147/
- https://endoflife.date/python
- https://pypi.org/project/Send2Trash/

### Standards, policy and advisories (carried forward, still current)
- https://datatracker.ietf.org/doc/draft-pantos-hls-rfc8216bis/
- https://www.rfc-editor.org/rfc/rfc8493.html
- https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md
- https://www.w3.org/TR/wcag2ict-22/
- https://docs.flathub.org/docs/for-app-authors/requirements
- https://github.com/pyca/cryptography/security/advisories/GHSA-g6cj-pr64-35w5
- https://github.com/mpv-player/mpv/security/advisories/GHSA-546v-22c3-7927
- https://github.com/FFmpeg/FFmpeg/releases/tag/n9.0

## Open Questions

- **Should the semantic index be reconciled or discarded on restore?** `semantic.db` is deliberately outside `BACKUP_FILES`, so a restore leaves it describing a library that no longer exists. Discarding it on restore is simple and correct but throws away a potentially expensive index; reconciling requires a path-set diff against the restored `library.db`. This decides the acceptance criteria for V148, not the diagnosis.
- **Does the release lane's Python 3.14 floor apply to the CI-less contributor path?** `README.md:277` keeps a 3.11+ floor for source installs while `release_gate.py:34` refuses to run below 3.14.6. A contributor on 3.12 cannot run the project's own gate at all, and there is no documented lighter check. Whether that is intentional gatekeeping or an oversight changes whether V152 is a docs fix or a gate change.
- **Kick VOD retention is now documented and the prior open question is answered:** 7 days unverified / 30 days verified, with a hard cap of 16 or 30 stored replays (Kick Help Center). What remains open is whether *cap* eviction or *age* eviction dominates in practice, which decides whether monitor backfill should prioritise oldest-reachable or highest-index VODs. Answerable only by observing real channels.
