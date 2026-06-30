# Research - StreamKeep

## Executive Summary

StreamKeep is a mature local-first Python/PyQt6 desktop archive workstation for live streams, VODs, podcasts, direct media URLs, and post-download media workflows. Its strongest current shape is the breadth around native extractors, queue/monitor automation, SQLite library state, embedded playback, local web surfaces, uploads, backup/restore, plugins, and packaging scaffolds; the highest-value direction is to make unattended long-term archiving more trustworthy rather than adding another generic downloader wrapper. Top opportunities, in priority order: (1) add archive integrity manifests and repair flows; (2) persist failed-job records for retry and remote recovery; (3) implement dynamic DASH/LL-HLS roadmap coverage; (4) publish the plugin SDK contract and sample plugin; (5) add SQLite maintenance and recovery diagnostics; (6) add local SBOM/advisory release checks; (7) package and smoke-test the browser companion; (8) add OPML portability; (9) document and test headless/service operation; (10) add media-server sidecar export profiles.

## Product Map

- Core workflows: URL fetch and quality selection; queued download/resume; channel/feed monitor and auto-record; library search/storage cleanup; playback, clipping, chat/subtitle rendering, summaries, uploads, feeds, and backup/restore.
- User personas: unattended stream archivists, creators clipping their own VODs, local media librarians, and power users who want GUI control over yt-dlp/ffmpeg workflows without surrendering local-first storage.
- Platforms and distribution: source checkout on Python 3.10+; Windows is primary; Linux/macOS have supported code paths; PyInstaller, MSIX, Flatpak, and browser companion packaging are scaffolded.
- Key integrations and data flows: curl fetches APIs/manifests; ffmpeg/ffprobe download and validate media; yt-dlp handles long-tail sites; SQLite stores history/monitor/queue state; JSON config stores preferences; browser companion and local web remote talk to `streamkeep/local_server.py`; plugins load from the config directory after trust gating.

## Competitive Landscape

- yt-dlp and Streamlink: best-in-class extractor/protocol/plugin ecosystems. Learn from their plugin contracts, runtime-readiness messaging, and rapid platform-churn response; avoid making StreamKeep CLI-only because the GUI/library/monitor surface is the differentiator.
- MeTube, Pinchflat, Tube Archivist, and TubeSync: self-hosted download managers make subscriptions, durable queues, retries, archive metadata, search, and web operation table-stakes. Learn from unattended-recovery and database-maintenance pain points; avoid narrowing StreamKeep to YouTube-only assumptions.
- Ganymede, LiveStreamDVR, and TwitchDownloader: Twitch-focused archivers set expectations for long-running recording, chat synchronization, rendered chat artifacts, and remote failure visibility. Learn from their recovery/status surfaces; avoid single-platform coupling.
- N_m3u8DL-RE and segmented-download tools: broader HLS/DASH handling, retries, and protocol edge cases are expected by power users. Learn from dynamic MPD, multi-period, low-latency, and retry behavior; avoid exposing raw protocol toggles before the UI can explain failures.
- HandBrake and Tdarr: adjacent media tools show mature preset, health-check, and post-processing workflows. Learn from profiles and validation reports; avoid burying users in codec-only controls.
- 4K Video Downloader and JDownloader: commercial tools normalize browser capture, scheduler/resume, polished installers, account-aware downloads, and remote management. Learn from onboarding and recovery polish; avoid adware, opaque cloud sync, and DRM-centered positioning.
- Jellyfin and restic: analogous long-term library and backup systems emphasize metadata portability, integrity checks, rebuild/repair workflows, and data that remains useful outside the app. Learn from checksum, sidecar, and restore-test discipline; avoid proprietary archive formats as the only source of truth.

## Security, Privacy, and Reliability

- Verified: `streamkeep/verify.py` uses ffprobe to validate media containers and rough duration, but there is no durable checksum manifest for media, thumbnails, captions, chat, metadata, or generated sidecars. Long-term archives can drift without detection.
- Verified: `streamkeep/backup.py` snapshots config, SQLite, tags, searches, notifications, and cookies, but not media-file integrity state; restore can recover app data without proving the referenced archive files are still intact.
- Verified: `streamkeep/db.py` stores history, monitor channels, and queue state, but there is no normalized failed-job ledger with URL, platform, stage, error, retry count, resume sidecar, and timestamps. `streamkeep/local_server.py` therefore cannot expose robust remote recovery without scraping queue/status text.
- Verified: `streamkeep/db.py` has no app-visible SQLite `integrity_check`, `PRAGMA optimize`, WAL checkpoint, or backup-before-vacuum maintenance path. Pinchflat and Tube Archivist issue traffic shows database growth/reindex pain is a real archive-ops failure mode, and SQLite documents explicit integrity/maintenance primitives.
- Verified: `streamkeep/dash.py` rejects dynamic/live MPD manifests and `streamkeep/hls.py` has minimal low-latency playlist modeling. This is safe fail-closed behavior, but remains a protocol-coverage gap versus N_m3u8DL-RE and yt-dlp.
- Verified: `streamkeep/plugins.py` discovers trusted local plugins and appends plugin parents to `sys.path`, but plugin manifest schema, compatibility checks, developer docs, and sample plugins are still roadmap work.
- Verified: `browser-extension/manifest.json` is MV3 and limited to localhost permissions, but release packaging tests do not yet prove extension ZIP contents, manifest permissions, icons, or `/ping` pairing.
- Verified: `requirements.txt`, `StreamKeep.spec`, and `packaging/` support local packaging, but there is no local SBOM/advisory gate for bundled PyQt6, Pillow, yt-dlp, PyInstaller, and related runtime dependencies.
- Verified: compiled translations and GUI smoke coverage were recently improved; keep future i18n and accessibility work test-driven rather than adding broad UI rewrites.

## Architecture Assessment

- `streamkeep/db.py` is the right boundary for new durable failure, integrity, and maintenance state. Add schema migrations and maintenance helpers there instead of encoding retry/integrity data only in JSON config or sidecar filenames.
- `streamkeep/workers/download.py` and `streamkeep/ui/tabs/download.py` should emit structured failure records while preserving current queue behavior and resume sidecars; this supports the existing remote web UI recovery item without duplicating it.
- `streamkeep/verify.py`, `streamkeep/backup.py`, and history/library UI should share one archive-integrity model so verification, backup/restore, and repair reports agree on missing/changed files.
- SQLite maintenance should be exposed as a safe operation: read-only integrity check first, backup snapshot before any vacuum/repair action, clear diagnostics, and no mutation of missing/corrupt DB files.
- `browser-extension/`, `StreamKeep.spec`, and `packaging/` need a release-artifact boundary for the browser companion that is deterministic and locally smoke-tested, with no GitHub Actions dependency.
- `streamkeep/extractors/podcast.py`, `streamkeep/monitor.py`, and `streamkeep/db.py` can support OPML import/export without changing the local-first model or adding accounts.
- Accessibility and i18n should stay defect-driven: compiled translations and GUI smoke coverage were recently improved, so new controls from this roadmap should add labels, focus-order checks, and language-switch smoke coverage rather than a broad UI rewrite.
- Multi-user/server-account work is not recommended now: `streamkeep/local_server.py` is a token-gated local/lan control plane for a single operator, and the proposed service profile should preserve that trust model.
- Upgrade strategy should remain manual and local: SBOM/advisory checks and release validation cover dependency drift without Dependabot or GitHub Actions, matching repo policy.
- Test gaps: checksum manifest drift/missing-file tests; failed-job migration/retry tests; SQLite maintenance tests for healthy, corrupt-copy, missing-file, backup-before-vacuum, and no-op paths; OPML import/export roundtrip and invalid XML tests; browser companion packaging tests; SBOM/advisory command smoke; service/headless server smoke; dynamic DASH/LL-HLS parser tests already covered by existing roadmap.
- Documentation gaps: README should eventually document plugin SDK, service/headless profile, browser companion packaging, OPML portability, SQLite maintenance, and local release verification as those items ship.

## Rejected Ideas

- DRM circumvention support, source: JDownloader/commercial downloader comparisons. Reason: legal and trust risk; StreamKeep should stay on user-owned, public, non-DRM, or otherwise authorized media.
- Electron/web rewrite, source: web-wrapper downloader comparisons. Reason: would discard working PyQt/mpv integration and not solve archive integrity, retries, or packaging reliability.
- Cloud account sync for library/config, source: commercial account-based tools. Reason: contradicts local-first storage; backup/restore, user-selected uploads, and service mode cover portable workflows.
- Native mobile app, source: remote-management patterns in Ganymede/LiveStreamDVR and commercial tools. Reason: browser-accessible remote recovery should land first and covers the near-term mobile-control case.
- Multi-user account/RBAC server, source: self-hosted archive managers. Reason: the local-first token-gated control plane fits single-operator use; durable recovery and service mode should land before shared accounts.
- Public plugin marketplace, source: yt-dlp and Streamlink plugin ecosystems. Reason: premature until local plugin schema, samples, compatibility checks, and trust UX are solid.
- User-editable global HTTP header injection, source: MeTube and N_m3u8DL-RE issue traffic. Reason: useful for some Referer-locked media but risky until host-scoped profiles, redaction, and extractor-specific guardrails exist.
- Built-in proxy/VPN product, source: JDownloader/account-manager comparisons. Reason: the existing per-platform proxy abstraction fits better and avoids operating a network service.
- Keyboard shortcut expansion, source: desktop downloader UX norms. Reason: project/user policy forbids adding keyboard shortcuts.
- GitHub Actions or Dependabot release automation, source: common OSS packaging practice. Reason: repo policy requires local builds and manual dependency updates.

## Sources

### OSS Competitors and Adjacent Tools
- https://github.com/yt-dlp/yt-dlp
- https://github.com/yt-dlp/yt-dlp/releases/tag/2026.06.09
- https://github.com/streamlink/streamlink
- https://streamlink.github.io/cli/plugin-sideloading.html
- https://github.com/alexta69/metube
- https://github.com/kieraneglin/pinchflat
- https://github.com/kieraneglin/pinchflat/issues/887
- https://github.com/tubearchivist/tubearchivist
- https://github.com/tubearchivist/tubearchivist/issues/915
- https://github.com/meeb/tubesync
- https://github.com/Zibbp/ganymede
- https://github.com/MrBrax/LiveStreamDVR
- https://github.com/lay295/TwitchDownloader
- https://github.com/nilaoda/N_m3u8DL-RE
- https://github.com/HaveAGitGat/Tdarr
- https://handbrake.fr/docs/en/latest/technical/official-presets.html
- https://github.com/restic/restic
- https://github.com/jellyfin/jellyfin
- https://github.com/awesome-selfhosted/awesome-selfhosted

### Commercial, Community, Standards, and Security
- https://www.4kdownload.com/products/videodownloader-42
- https://jdownloader.org/
- https://www.reddit.com/r/DataHoarder/
- https://datatracker.ietf.org/doc/html/rfc8216
- https://dashif.org/docs/
- https://developer.chrome.com/docs/extensions/develop/migrate/what-is-mv3
- https://opml.org/spec2.html
- https://cyclonedx.org/guides/sbom/obtain/
- https://pip-audit.readthedocs.io/
- https://www.sqlite.org/pragma.html
- https://github.com/python-pillow/Pillow/releases

## Open Questions

None.
