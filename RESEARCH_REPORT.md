# StreamKeep Research Report

This report summarizes the rationale behind the current roadmap. Historical feature specs are archived under `docs/archive/roadmap/`.

## Product Direction

StreamKeep has evolved from a VOD downloader into a local-first stream archive workstation. The most durable product shape is:

- Native extractors first, yt-dlp as broad fallback.
- SQLite-backed archive/library state.
- Worker-based background execution for fetch, download, post-process, conversion, uploads, transcription, and intelligence tasks.
- Local companion/server features that stay token-gated and localhost-first.
- Optional integrations that do not make core downloading depend on remote services.

## Research-Backed Priorities

- Download queue and concurrent workers were the highest-value core workflow because users commonly archive multiple VODs or channels.
- Library organization and metadata are necessary once the app handles large archives.
- Clip/highlight/transcript tooling turns raw recordings into useful media assets.
- Browser companion and CLI/server modes cover the two main automation paths: browser-to-desktop and headless/server use.
- Plugin SDK and local web gallery are the extension points for user-specific workflows.

## Ongoing Risk Areas

- Extractor APIs can change without notice; every native extractor needs tight error reporting and fallback behavior.
- Subprocess boundaries need explicit argument separators and predictable quoting.
- Local API endpoints must stay localhost-only and token-gated.
- Upload destinations and web gallery routes must keep path traversal and range-streaming guards.
- Credential and account stores should remain encrypted where supported and visibly downgraded where not.
- Optional AI/LLM backends must surface failures instead of silently returning empty output.

## Archived Source Material

- `docs/archive/roadmap/ROADMAP-legacy.md`
- `docs/archive/roadmap/features.md`
