# StreamKeep Roadmap

StreamKeep is a Python/PyQt6 desktop downloader and archive manager for live streams, VODs, podcasts, and direct media URLs. This roadmap tracks pending work only.

## Planning Docs

- Research synthesis: `RESEARCH.md`
- Blocked work with its blocker reason: `Roadmap_Blocked.md`

## Current Baseline

- Current package version: v4.47.0.
- Current architecture is modular: extractors, declarative YAML source adapters, workers, post-processing, player, local server, SQLite library, plugin manager, upload adapters, intelligence helpers, and UI modules.
- History, monitor channels, and queue state live in SQLite (schema v21); user preferences remain in JSON config.
- The release lane and local release gate require Python 3.14.6 or newer (`packaging/release_gate.py`); source installs retain the 3.11+ floor.
- ID scheme: `V<n>`. Highest allocated ID is **V178**.

## Active Roadmap

### 0. Versatility Program (active drain queue)

Mission: any video or audio, from any website, in any format, at any quality the source offers, with full user control. DRM circumvention is out of bounds throughout.

- [ ] P3 — V42 — yt-dlp stable/nightly update channel toggle
  Why: YouTube fixes land in yt-dlp nightly days before stable; power users want to opt into nightly for fast-moving platforms without waiting for a StreamKeep release. StreamKeep bundles a frozen yt-dlp.
  Evidence: https://github.com/alexta69/metube/releases (nightly toggle); `streamkeep/capabilities.py` (`resolve_command_prefix`), `streamkeep/updater.py`. 2026-08-06: reinforced — stable cadence cannot track breakage (12-week gap between yt-dlp 2026.03.17 and 2026.06.09 while YouTube broke repeatedly), and the Kick extractor is broken upstream right now (https://github.com/yt-dlp/yt-dlp/issues/17284).
  Touches: capability resolution to allow a user-supplied/updatable yt-dlp, a stable/nightly channel setting, health surfacing of the active yt-dlp version/channel, Settings, tests.
  Acceptance: a setting lets the user point at an external/nightly yt-dlp or self-update the bundled one; the health panel shows the active version and channel; the bundled frozen yt-dlp remains the default; switching channels is reversible.
  Complexity: M
  > Any external/nightly binary must resolve through `capabilities.resolve_command_prefix` so a below-floor yt-dlp cannot enter download paths, and must be version-probed rather than trusted. The `Roadmap_Blocked.md` lock-bump entry remains the prerequisite for advancing the pinned lock itself.

### 1. Security and Reliability Hardening

- Continue audit passes across subprocess boundaries, path normalization, URL handling, local API auth, upload destinations, updater downloads, and credential storage.
- Keep subprocess URL/argument handling using explicit separators and platform-safe quoting.
- Keep local server endpoints localhost-only and token-gated.
- Add visible error propagation for background workers that still fail silently.

### 2. Future Feature Queue

- Remote/headless management polish for the REST server and local web gallery.
- More extractor-specific resilience for API churn on Kick, Twitch, Rumble, SoundCloud, Reddit, Audius, and yt-dlp fallback paths.
- Deeper library views for very large archives: filters, smart collections, transcript search, notes, bandwidth/storage trends, and channel statistics.
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

#### P2 — Later

- [ ] P2 — V177 — Track down the intermittent Qt access violation at end of suite
  Why: one full `pytest` run in five aborted with `Windows fatal exception: access violation` inside `_pytest.runner.runtestprotocol` at roughly 97% of the run, with no Python-level failure; the identical run passed on retry. A native crash that appears once per few thousand tests will eventually land on a release gate run and be misread as a code regression.
  Evidence: observed 2026-08-06 on Python 3.14.6 immediately after the GUI-heavy portion of the suite; `faulthandler` shows only the pytest frames because the C stack is unavailable on this host. Candidate area: Qt widget teardown in the offscreen `QApplication` shared session-wide by `tests/conftest.py`.
  Touches: `tests/conftest.py`, the GUI smoke tests, per-test widget teardown.
  Acceptance: the crash is reproduced under `-p no:randomly` with a narrowed test set and either fixed or pinned to a named upstream issue; a full suite run repeated ten times shows no access violation.
  Complexity: M

- [ ] P2 — V155 — Bound declarative HTML parsing depth and selector cost
  Why: the HTML walker is recursive against only an 8 MB body cap and selector matching re-walks each candidate's whole subtree per token with no dedupe, so a deeply nested response either raises an uncaught `RecursionError` or burns minutes of CPU in a worker.
  Evidence: `streamkeep/declarative.py:593-596` (`_walk_html`), `:545-549` (`_HTMLNode.text`), `:622-634` (`_select_html_nodes`), `MAX_RESPONSE_BYTES` at `:39`, except clause at `:1100-1103`.
  Touches: `streamkeep/declarative.py`, tests.
  Acceptance: parse depth is capped in `handle_starttag`, walking and text extraction are iterative, candidates are deduped per token, and a nested-`div` fixture completes within a bounded time without escaping an exception type the caller does not handle.
  Complexity: M

- [ ] P2 — V158 — Guard capability probing on unsupported host architectures
  Why: `host_target()` raises for anything outside five pinned triples and the capability probe has no guard, so Windows-on-ARM64 with no PATH JavaScript runtime raises out of `get_runtime_capabilities` instead of reporting a missing runtime.
  Evidence: `streamkeep/javascript_runtime.py:99-116`, `:182-186`; `streamkeep/capabilities.py:400-403`, `:1010-1019`.
  Touches: `streamkeep/capabilities.py`, tests.
  Acceptance: an unsupported host returns the existing "missing runtime" capability record with repair guidance; a test simulating an unknown `platform.machine()` does not raise.
  Complexity: S

- [ ] P2 — V159 — Make the power policy sleep instead of hibernate
  Why: `rundll32 powrprof.dll,SetSuspendState 0,1,0` ignores its first argument, so any machine with hibernation enabled hibernates when the user asked for sleep.
  Evidence: `streamkeep/power.py:189`.
  Touches: `streamkeep/power.py`, tests.
  Acceptance: the suspend path calls `SetSuspendState` through ctypes with an explicit hibernate/sleep intent, or documents and surfaces that hibernation is what will happen.
  Complexity: S

- [ ] P2 — V161 — Incremental mux and periodic flush for multi-hour live captures
  Why: a crash or power loss late in a long capture still costs the tail, and the final single mux is the fragile step. This is the most consistent live-capture complaint across the dead tools whose users are shopping.
  Evidence: https://github.com/Kethsar/ytarchive/issues/112 and https://github.com/Kethsar/ytarchive/issues/116 (no incremental mux; one giant final pass), https://github.com/Kethsar/ytarchive/issues/213 (silent stop, no resume), https://github.com/yt-dlp/yt-dlp/issues/9094 (no periodic flush); existing `chunk_long_captures` splitting and `streamkeep/resume.py` sidecars are the foundation.
  Touches: `streamkeep/workers/download.py`, `streamkeep/resume.py`, `streamkeep/workers/finalize.py`, tests.
  Acceptance: a capture killed at an arbitrary point yields a playable file covering everything captured up to that point without a manual repair step; the final mux is incremental or resumable; a test kills a simulated capture mid-run and asserts a playable result.
  Complexity: L

- [ ] P2 — V162 — Adaptive rate governance instead of hand-tuned limits
  Why: bulk archiving triggers 429s and soft blocks, and the only current answer is for the user to hand-tune sleep/limit-rate/fragment concurrency. StreamKeep owns the queue, so it can observe throttling and back off globally per host — the commercial tools paywall scheduling and none of them do this.
  Evidence: https://github.com/yt-dlp/yt-dlp/issues/13831 (subtitle fetch 429, 37 reactions); existing `streamkeep/scheduler.py` speed tiers and `bandwidth.py` caps are per-clock, not per-response.
  Touches: `streamkeep/scheduler.py`, `streamkeep/workers/download.py`, `streamkeep/headless_service.py` concurrency accounting, Settings, tests.
  Acceptance: a 429 or throttle signal from one job reduces concurrency and increases inter-request delay for that host across the whole queue, and recovers on sustained success; the active governor state is visible in the UI; behaviour is reproducible in a test with a synthetic throttling server.
  Complexity: L

- [ ] P2 — V163 — Move behaviour out of `db/_legacy.py` and `server/_legacy.py`
  Why: the 2026-08-04 "split" created facades over unchanged monoliths — `db/_legacy.py` is 6,624 LOC and actually grew during the split; `server/_legacy.py` is 2,748 — and the boundary test asserts the facade forwards the entire legacy surface, which makes the monolith a tested contract and the commit message read as finished work.
  Evidence: `streamkeep/db/__init__.py` (attribute-forwarding `ModuleType` subclass), `streamkeep/db/_legacy.py`, `streamkeep/server/_legacy.py`, `tests/test_architecture_boundaries.py:11-21`; sibling modules `db/history.py` (1.2 KB), `db/queue.py` (723 B) are re-export shims.
  Touches: `streamkeep/db/*`, `streamkeep/server/*`, `tests/test_architecture_boundaries.py`.
  Acceptance: each domain module owns its own statements rather than re-exporting; the boundary test asserts what each module implements (not that the facade is surface-identical); `_legacy.py` shrinks measurably per increment and the facade remains patch-compatible for existing tests.
  Complexity: XL

- [ ] P2 — V164 — Fix the light-theme checked-toggle contrast
  Why: the one rendered QSS rule block that fails WCAG AA is a *selected* state, which is exactly where the user needs to read the label.
  Evidence: rendering all three palettes and checking every rule block pairing `color:` with `background-color:` produced zero failures in dark and high contrast and one in light — `QPushButton#toggleAccent:checked` at 4.40:1 (`#2563d9` on `#e1e8ef`), `streamkeep/theme.py:594`.
  Touches: `streamkeep/theme.py`, `tests/test_visual_system.py`.
  Acceptance: the checked toggle reaches ≥ 4.5:1 in every palette, and the visual-system test checks rendered rule blocks (fg/bg pairs in the same block, excluding `:disabled`) rather than a fixed token list.
  Complexity: S

- [ ] P2 — V165 — Surface per-source extractor health and make the engine swap one click
  Why: extractor breakage is monthly on YouTube and recurring on Kick, and today a broken source looks like a broken app. Making the "rent" visible turns a platform change into a degraded archive instead of a dead one.
  Evidence: https://github.com/yt-dlp/yt-dlp/issues/17284 (Kick extractor broken upstream, updated 2026-08-04), https://github.com/yt-dlp/yt-dlp/issues/16212 and https://github.com/yt-dlp/yt-dlp/issues/15750 (2026 YouTube 403/SABR waves); `streamkeep/health.py` already models standing conditions and `streamkeep/capabilities.py` already resolves engines. Complements V42 (which owns the yt-dlp channel toggle) — do not duplicate that scope here.
  Touches: `streamkeep/health.py`, `streamkeep/capabilities.py`, `streamkeep/extractors/*`, Settings and the operations header, tests.
  Acceptance: repeated resolve failures for one platform raise a named standing condition naming the platform and the engine; the UI offers switching that source to an alternate engine (yt-dlp, Streamlink, declarative adapter) without editing config; the condition clears on success.
  Complexity: M

- [ ] P2 — V166 — Do not silently prefer AI-upscaled or AI-dubbed tracks
  Why: platforms now expose AI "Super Resolution" video and AI-dubbed audio that format-sorting picks over the original, so an archive quietly stores a synthesised version of the thing it was meant to preserve — a direct contradiction of the custody thesis.
  Evidence: https://github.com/yt-dlp/yt-dlp/issues/15433 and https://github.com/yt-dlp/yt-dlp/issues/11834; existing `--dub-lang` preference and format sorting in `streamkeep/download_options.py`.
  Touches: `streamkeep/download_options.py`, `streamkeep/extractors/ytdlp.py`, the track-selection UI, `streamkeep/metadata.py` (record what was chosen), tests.
  Acceptance: original-language and non-upscaled tracks are preferred by default, a synthesised track is chosen only on explicit opt-in, and the metadata sidecar records which was stored.
  Complexity: M

- [ ] P2 — V167 — Restore repo documentation and release hygiene
  Why: the repo has drifted from its own `AGENTS.md` rules, and v4.45.0 shipped untagged so the WinGet hash tool has no tag to point at.
  Evidence: `AGENTS.md` ("README.md is the ONLY .md tracked in git"; "Never create: COMPLETED.md") versus tracked `RESEARCH.md`/`ROADMAP.md` and root-level `COMPLETED.md`, `RESEARCH_REPORT.md` (2026-07-era, duplicates `RESEARCH.md`'s purpose); `git tag` latest is `v4.44.0` against `streamkeep/__init__.py` VERSION 4.45.0; `packaging/winget_hash.py`.
  Touches: `.gitignore`, root `.md` files, `packaging/versioning.py`, tag creation.
  Acceptance: `COMPLETED.md` and `RESEARCH_REPORT.md` are folded into `CHANGELOG.md`/`RESEARCH.md` and removed; tracked-versus-ignored state matches `AGENTS.md`; v4.45.0 is tagged and `packaging/winget_hash.py` resolves.
  Complexity: S

#### P3 — Under Consideration

- [ ] P3 — V178 — Portable plugins and source adapters across profiles
  Why: `download-archives/` now travels with a backup, but `plugins/` and `source_adapters/` deliberately do not — the first is executable Python, and the second would arrive carrying third-party request descriptions. The enable-time review has since landed (v4.46.0), so an adapter can now travel as a definition that restores in its unreviewed, inert state; what remains is deciding whether a restore is allowed to carry the operator's approvals with it (it must not) and whether plugins get an opt-in at all.
  Evidence: `streamkeep/backup.py` `BACKUP_DIRECTORIES` and its documented exclusions; `streamkeep/plugins.py:448 _trust_review_matches`; `streamkeep/declarative.py` `adapter_review_state` and `streamkeep/config.py` `_quarantine_import_capabilities` (approvals are already refused on config import).
  Touches: `streamkeep/backup.py`, `streamkeep/declarative.py`, `streamkeep/plugins.py`, tests.
  Acceptance: adapters restore in a disabled, review-required state and plugins either stay excluded with the reason surfaced in the restore report or restore behind an explicit, separately-confirmed opt-in.
  Complexity: M

- [ ] P3 — V176 — Sub-only Twitch VOD recovery
  Why: the canonical implementation of URL-reconstruction VOD recovery has been unmaintained since 2024-07 and the live forks have single-digit stars, so the niche is currently unowned; StreamKeep already has `extractors/twitch_recover.py` and the CDN-hash timestamp machinery this depends on.
  Evidence: https://github.com/TwitchRecover/TwitchRecover (1,176★, last commit 2024-07-12); https://github.com/yt-dlp/yt-dlp/issues/1830 and https://github.com/lay295/TwitchDownloader/issues/979 (persistent unmet ask); `streamkeep/extractors/twitch_recover.py` (`recover_channel_vods`, `_unix_timestamp_variants`).
  Touches: `streamkeep/extractors/twitch_recover.py`, the recovery UI, `streamkeep/cli.py`, tests.
  Acceptance: a recovery attempt against a known-removed VOD enumerates candidate CDN domains and reports which resolved; failures name the reason; nothing in the path circumvents DRM or paid access controls — this reconstructs URLs for content the platform still serves unauthenticated, and refuses when it does not.
  Complexity: M

- [ ] P3 — V168 — Let a recording belong to more than one collection
  Why: the season-folder model forces one home per video, so a recording that belongs to two playlists or two user collections has to be duplicated or arbitrarily assigned — the top unresolved library-model complaint in the adjacent tools.
  Evidence: https://github.com/jmbannon/ytdl-sub/discussions/826; existing `streamkeep/tags.py` and media-server layout logic in `streamkeep/integrations/media_server.py`.
  Touches: `streamkeep/db` schema, `streamkeep/tags.py`, `streamkeep/integrations/media_server.py`, History/Storage UI, tests.
  Acceptance: a recording can be a member of N collections with one on-disk copy; the media-server export still produces a valid single-home layout (hardlink or `.strm` for the secondary homes) and says which strategy it used.
  Complexity: L

- [ ] P3 — V169 — Order monitor backfill by reachability deadline
  Why: Kick retention is 7 days unverified / 30 days verified with a hard cap of 16 or 30 stored replays, so backfilling newest-first loses the oldest reachable VOD first. This answers the prior pass's open question about whether the numbers justify reordering.
  Evidence: https://help.kick.com/en/articles/7112432-kick-stream-replays-vods and https://help.kick.com/en/articles/14994284-my-vod-is-missing-or-not-appearing-after-my-stream; `streamkeep/workers/monitor_ops.py` `SeedArchiveWorker`.
  Touches: `streamkeep/workers/monitor_ops.py`, `streamkeep/monitor.py`, queue ordering, tests.
  Acceptance: seed/backfill enqueues oldest-reachable first for platforms with a known retention window, the ordering is per-platform and configurable, and the reason appears in the queue row. Open: whether cap eviction or age eviction dominates on Kick in practice.
  Complexity: M

- [ ] P3 — V170 — Make the broad-exception guardrail assert a reason, not a comment
  Why: the new guardrail is satisfied by any `#` on or above the `pass`, and 170 sites now carry largely identical boilerplate. The count did not fall and no previously-hidden failure became visible, so the test currently measures annotation compliance rather than error visibility.
  Evidence: `tests/test_exception_annotations.py:11-28`; 170 `except Exception:` + `pass` sites in `streamkeep/`, 19 in `ui/main_window.py`.
  Touches: `tests/test_exception_annotations.py`, the swallow sites that hide logic failures.
  Acceptance: the guardrail rejects boilerplate-only annotations (for example, requires a distinct reason or an allow-list with a review date), and the sites identified as hiding logic failures log or surface instead of swallowing.
  Complexity: S

- [ ] P3 — V171 — Show translation coverage in the app, not only the README
  Why: Spanish is 14.8% translated (1,564 of 1,836 messages unfinished) and the beta caveat lives only in the README, so a user switching language in Settings sees a mostly-English UI with no explanation. Adding languages is blocked on human translators (`Roadmap_Blocked.md`); showing coverage is not.
  Evidence: `streamkeep/i18n/streamkeep_es.ts` (1,836 messages, 1,564 `type="unfinished"`); `README.md:240`; `Roadmap_Blocked.md` "Expand i18n beyond English and Spanish".
  Touches: `streamkeep/i18n/__init__.py`, `streamkeep/ui/tabs/settings_preferences.py`, the release gate's `translations` stage.
  Acceptance: the language selector shows each catalog's translated percentage and marks anything below a threshold as beta in-app; the gate reports coverage so a regression is visible.
  Complexity: S
