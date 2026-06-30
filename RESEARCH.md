# Research - StreamKeep

## Executive Summary

StreamKeep is a mature local-first Python/PyQt6 archive workstation for live streams, VODs, podcasts, direct media URLs, and post-download media operations. Its strongest current shape is the combination of native extractors, yt-dlp fallback, SQLite-backed queue/library state, restart-surviving failed-job recovery, archive integrity manifests, local web/extension control, backup/restore, and rich media post-processing; the highest-value direction is to harden unattended archive operation rather than add another generic downloader surface. Top opportunities, in priority order: (1) scope and rotate local API tokens before the browser companion and remote recovery surfaces grow; (2) add disk-to-library import/reconcile so existing archive folders can repopulate SQLite safely; (3) normalize queue rows out of opaque JSON blobs; (4) build a protocol/extractor fixture corpus for upstream churn; (5) add a privacy-redacted support snapshot; (6) add host-scoped request profiles instead of global header injection; (7) extend the browser companion with clip-range handoff after packaging smoke tests land; (8) keep existing roadmap items for dynamic DASH/LL-HLS, plugin SDK, structured logging, SBOM/advisory checks, OPML, service mode, media-server sidecars, and SQLite maintenance.

## Product Map

- Core workflows: fetch a URL, choose quality, queue/download/resume, record monitored channels, retry failed jobs, verify archive manifests, search/play/clip/post-process recordings, and back up local state.
- User personas: unattended stream archivists, creators clipping their own recordings, media-library operators, and power users who want GUI control over yt-dlp/ffmpeg workflows without cloud-first storage.
- Platforms and distribution: Python 3.10+ source checkout; Windows is primary; Linux/macOS code paths exist; PyInstaller, MSIX, Flatpak, and MV3 browser companion packaging are scaffolded.
- Key integrations and data flows: curl fetches APIs/manifests; ffmpeg/ffprobe download and validate media; yt-dlp covers long-tail sites; SQLite stores history/monitor/queue/failed-job/archive-manifest state; JSON config stores preferences; `streamkeep/local_server.py` bridges the web remote and browser companion; plugins load from the config directory after trust consent.

## Competitive Landscape

- yt-dlp and Streamlink: best-in-class extractor/plugin ecosystems and rapid platform-churn response. StreamKeep should learn from explicit plugin contracts and extractor diagnostics; it should avoid becoming CLI-only because its library, GUI, monitor, and recovery surfaces are the differentiator.
- MeTube, Pinchflat, Tube Archivist, and TubeSync: self-hosted downloaders make durable queues, subscriptions, browser handoff, metadata portability, and service operation table-stakes. StreamKeep should learn from their unattended archive workflows and database-maintenance pain; it should avoid YouTube-only assumptions.
- Ganymede, LiveStreamDVR, and TwitchDownloader: Twitch archivers set expectations for chat capture/rendering, long-running recorders, mobile-friendly remote control, and disk-to-database import. StreamKeep should learn from their recovery/status needs; it should avoid platform lock-in.
- N_m3u8DL-RE and segmented-download tools: power users expect broad HLS/DASH handling, live edge cases, headers, retries, and low-latency manifest support. StreamKeep should implement safe protocol fixtures and host-scoped request profiles; it should avoid exposing unsafe global network knobs.
- HandBrake and adjacent transcode automation tools: adjacent media tools show mature presets, health checks, and post-processing pipelines. StreamKeep should borrow validation reports and named profiles; it should avoid burying users in codec-only controls without previews and safe defaults.
- 4K Video Downloader, Downie, and JDownloader: commercial tools normalize browser capture, resume, schedulers, account-aware downloads, installers, and remote/mobile handoff. StreamKeep should borrow onboarding and recovery polish; it should avoid adware, opaque cloud sync, and DRM-centered positioning.
- Jellyfin, restic, CycloneDX, and SQLite: adjacent library/backup/release systems emphasize sidecar portability, fixity checks, rebuild workflows, SBOMs, and explicit database integrity primitives. StreamKeep should keep archive truth portable and verifiable.

## Security, Privacy, and Reliability

- Verified: `streamkeep/local_server.py` token-gates requests and rejects bad Host/Origin values, but all authenticated endpoints share one launch token; `browser-extension/popup.js` stores that token and can call queue/failure actions once configured. Add rotation and capability scopes before expanding remote operations.
- Verified: `streamkeep/plugins.py` skips untrusted plugins, but trusted plugins execute in-process and the README warns they have StreamKeep's privileges. The existing plugin SDK roadmap should include manifest compatibility and permission guardrails before recommending third-party plugins broadly.
- Verified: `streamkeep/db.py` persists `download_queue` as `{position, data JSON}` while failed jobs and archive manifests are normalized. Queue status, recurrence, failure links, and recovery queries remain harder to inspect, migrate, and expose through the local API.
- Verified: `streamkeep/storage.py`, `streamkeep/metadata.py`, `streamkeep/verify.py`, and `streamkeep/db.py` can scan folders and verify known manifest rows, but there is no disk-to-library import/reconcile flow that creates missing history rows from existing archive folders.
- Verified: `streamkeep/dash.py` rejects dynamic MPDs and `streamkeep/hls.py` only models basic HLS duration/master playlists. The existing dynamic DASH/LL-HLS item remains valid; protocol changes should be backed by fixture manifests, not only live-site testing.
- Verified: `requirements.txt`, `StreamKeep.spec`, and `packaging/` have local packaging scaffolds, but the existing SBOM/advisory roadmap item remains important because bundled yt-dlp/Pillow/PyInstaller/PyQt6 dependency risk changes over time.
- Verified: `browser-extension/manifest.json` uses MV3 and localhost host permissions, but MeTube issue traffic shows browser extensions break in real deployments; the existing packaging smoke item should exercise `/ping`, permissions, icons, and stored-pairing failure modes.
- Verified: `CHANGELOG.md` records compiled translations and GUI smoke coverage. Broad i18n/accessibility rewrites are not recommended now; every new UI control from the roadmap should add labels, focus behavior, translated strings, and tests as it lands.
- Verified: `Roadmap_Blocked.md` correctly holds the blocked GitHub Actions idea. Local validation, not CI, remains the release strategy.

## Architecture Assessment

- `streamkeep/local_server.py` should own API capability checks, token rotation, and endpoint-level authorization so GUI/server/headless behavior stays consistent.
- `streamkeep/db.py` is still the right migration boundary. Normalize queue rows with a schema bump and compatibility loader rather than spreading more recovery state through JSON blobs.
- `streamkeep/storage.py`, `streamkeep/metadata.py`, `streamkeep/verify.py`, and `streamkeep/ui/tabs/storage.py` should share one import/reconcile model: preview first, create DB rows second, write manifests only after the user accepts, and never overwrite user-edited sidecars.
- `tests/test_extractors.py`, `tests/test_scrape.py`, `streamkeep/dash.py`, `streamkeep/hls.py`, and `streamkeep/extractors/` need a curated offline fixture corpus for API samples, HLS/DASH manifests, error bodies, and DRM/unsupported cases.
- `streamkeep/http.py`, `streamkeep/proxy.py`, `streamkeep/accounts.py`, and extractor modules should support host-scoped request profiles only after redaction, allowlist validation, and per-host tests exist.
- `streamkeep/backup.py`, `streamkeep/config.py`, `streamkeep/db.py`, and log/crash files can produce a support snapshot without adding telemetry or cloud upload; redact tokens, cookies, credentials, absolute secrets, and full bearer values.
- `browser-extension/` should not gain clip-range handoff until the existing deterministic packaging and pairing smoke tests are in place.
- Multi-user/RBAC server work is rejected for now: the product is a local-first single-operator archive tool, and token-scoped service mode is the safer next step.
- Native mobile is rejected for now: a responsive local web remote and browser companion workflows cover the practical phone/tablet control case first.
- Upgrade strategy should stay local and manual: SBOM/advisory checks, SQLite schema migrations, and release validation fit the repo policy better than GitHub Actions or Dependabot.

## Rejected Ideas

- DRM circumvention support, source: commercial downloader comparisons and JDownloader-style account workflows. Reason: legal/trust risk and not aligned with StreamKeep's README policy.
- Electron/web rewrite, source: MeTube/self-hosted web apps. Reason: would discard working PyQt/mpv/local-desktop integration without solving reliability gaps.
- Cloud account sync for library/config, source: commercial account-based tools. Reason: contradicts local-first storage; backup/restore, uploads, and service mode cover portability.
- Public plugin marketplace, source: yt-dlp/Streamlink plugin ecosystems. Reason: premature until the local SDK, compatibility checks, and permission UX are solid.
- Global user-editable HTTP headers, source: N_m3u8DL-RE and downloader issue traffic. Reason: too risky without host-scoped profiles, redaction, and extractor guardrails.
- Multi-user account/RBAC server, source: self-hosted archive managers. Reason: token-scoped single-operator control is the correct near-term trust model.
- Native mobile app, source: Ganymede/LiveStreamDVR remote-use patterns. Reason: responsive local web recovery and extension handoff should land first.
- GitHub Actions/Dependabot automation, source: common OSS release practice. Reason: repo policy requires local builds/tests/releases and manual dependency updates.
- Keyboard shortcut expansion, source: desktop downloader norms. Reason: project policy forbids adding keyboard shortcuts.

## Sources

### Project and GitHub API
- https://github.com/SysAdminDoc/StreamKeep

### OSS Competitors and Issues
- https://github.com/yt-dlp/yt-dlp
- https://github.com/yt-dlp/yt-dlp/releases/tag/2026.06.09
- https://github.com/streamlink/streamlink
- https://streamlink.github.io/cli/plugin-sideloading.html
- https://github.com/alexta69/metube/issues/987
- https://github.com/alexta69/metube/issues/966
- https://github.com/kieraneglin/pinchflat/issues/887
- https://github.com/tubearchivist/tubearchivist/issues/915
- https://github.com/tubearchivist/tubearchivist/issues/265
- https://github.com/meeb/tubesync
- https://github.com/Zibbp/ganymede
- https://github.com/Zibbp/ganymede/issues/1043
- https://github.com/MrBrax/LiveStreamDVR
- https://github.com/lay295/TwitchDownloader
- https://github.com/nilaoda/N_m3u8DL-RE

### Commercial, Adjacent, Standards, and Security
- https://www.4kdownload.com/products/videodownloader-42
- https://jdownloader.org/
- https://software.charliemonroe.net/downie/
- https://www.internetdownloadmanager.com/
- https://handbrake.fr/docs/en/latest/technical/official-presets.html
- https://jellyfin.org/docs/general/server/media/shows/
- https://restic.readthedocs.io/en/latest/045_working_with_repos.html
- https://www.sqlite.org/pragma.html
- https://datatracker.ietf.org/doc/html/rfc8216
- https://dashif.org/docs/
- https://opml.org/spec2.html
- https://developer.chrome.com/docs/extensions/develop/concepts/declare-permissions
- https://cyclonedx.org/guides/sbom/obtain/
- https://pypi.org/project/pip-audit/

## Open Questions

None.
