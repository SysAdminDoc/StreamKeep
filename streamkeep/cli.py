"""CLI / headless mode for StreamKeep (F42).

Provides ``--url``, ``--server``, ``--list-extractors``, and plugin diagnostics.
Uses QCoreApplication (no display server required) so existing QThread-based
workers and pyqtSignal infrastructure work without modification.

Usage::

    python StreamKeep.py --url URL [--quality best|1080p|720p|...] [--output DIR]
    python StreamKeep.py --server [--port PORT] [--trusted-proxy-origin HTTPS_ORIGIN]
    python StreamKeep.py --list-extractors
    python StreamKeep.py plugins --json
"""

import argparse
import json
import os
import sys
from pathlib import Path

# QCoreApplication drives the event loop without requiring a display
# server.  This lets us reuse QThread workers and pyqtSignal infra.
from PyQt6.QtCore import QCoreApplication

from . import VERSION
from .capabilities import CapabilityUnavailableError, require_capability
from .extractors.base import Extractor as _ExtBase
from .models import default_media_tracks


def _get_output_stream():
    """Return a writable console stream, or ``None`` for windowed launches.

    PyInstaller's GUI build sets ``sys.stdout`` and ``sys.__stdout__`` to
    ``None``.  When a frozen CLI invocation has a parent console on Windows,
    attach to it and open ``CONOUT$``; double-clicked/windowed invocations
    simply run without console output instead of crashing.
    """
    for stream in (getattr(sys, "stdout", None), getattr(sys, "__stdout__", None)):
        if stream is not None and callable(getattr(stream, "write", None)):
            return stream

    if os.name != "nt":
        return None

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # ATTACH_PARENT_PROCESS. Failure is expected when there is no parent
        # console; opening CONOUT$ below is the definitive availability check.
        kernel32.AttachConsole(ctypes.c_uint(-1).value)
        stream = open(
            "CONOUT$",
            "w",
            encoding="utf-8",
            errors="replace",
            buffering=1,
        )
        sys.stdout = stream
        return stream
    except (AttributeError, OSError, ValueError):
        return None


def _print_progress(text):
    """Overwrite the current console line with *text*."""
    import shutil
    # shutil.get_terminal_size honors the `fallback` kwarg and works when
    # stdout is redirected (background/headless) — os.get_terminal_size on
    # Windows rejects the keyword and raises when there is no console.
    cols = shutil.get_terminal_size(fallback=(80, 24)).columns
    stream = _get_output_stream()
    if stream is None:
        return
    try:
        stream.write("\r" + text[:cols].ljust(cols) + "\r")
        stream.flush()
    except (AttributeError, OSError, ValueError):
        pass  # stdout closed/redirected — progress is best-effort


def _print_line(text):
    stream = _get_output_stream()
    if stream is None:
        return
    try:
        stream.write(text + "\n")
        stream.flush()
    except UnicodeEncodeError:
        # Legacy Windows consoles (cp1252) can't encode characters like
        # em-dashes in titles/URLs — degrade those characters instead of
        # printing mojibake or dropping the whole line.
        try:
            encoding = getattr(stream, "encoding", "") or "ascii"
            safe = text.encode(encoding, errors="replace").decode(encoding)
            stream.write(safe + "\n")
            stream.flush()
        except (AttributeError, OSError, ValueError):
            pass
    except (AttributeError, OSError, ValueError):
        pass


def _check_ffmpeg():
    try:
        require_capability("ffmpeg", refresh=True)
        return True
    except CapabilityUnavailableError:
        return False


def _record_cli_failure(url, stage, error, output_dir="", info=None):
    try:
        from . import db
        db.save_failed_job(
            url=url,
            platform=(info.platform if info else ""),
            title=(info.title if info else url),
            stage=stage,
            error=str(error or ""),
            output_dir=output_dir,
            resume_sidecar=os.path.join(output_dir, ".streamkeep_resume.json")
            if output_dir and os.path.isfile(os.path.join(output_dir, ".streamkeep_resume.json"))
            else "",
            queue_data={"url": url, "title": (info.title if info else url)},
        )
    except Exception:
        pass  # safe: best-effort fallback; preserve the primary operation


def _init_db_or_exit(db_module):
    """Initialize the library or report a newer-schema stop to the CLI."""
    try:
        db_module.init_db()
    except db_module.DatabaseSchemaError as error:
        _print_line(f"Error: {error}")
        raise SystemExit(2) from None


# ── --url handler ───────────────────────────────────────────────────

def _run_download(args):
    """Resolve *args.url* and download it."""
    from .auth_profiles import ensure_migrated
    ensure_migrated()
    if not _check_ffmpeg():
        try:
            require_capability("ffmpeg")
        except CapabilityUnavailableError as error:
            print(f"Error: {error}")
        sys.exit(1)

    from .download_options import (
        resolve_dubbed_format_spec, validate_download_options,
        validate_external_downloader_options,
        validate_sponsorblock_options,
        validate_subtitle_options, validate_ytdlp_transfer_options,
    )
    subtitle_requested = any((
        getattr(args, "sub_langs", ""),
        getattr(args, "auto_subs", False),
        getattr(args, "convert_subs", ""),
        getattr(args, "sub_delivery", ""),
    ))
    sponsorblock_requested = any((
        getattr(args, "sponsorblock_mark", ""),
        getattr(args, "sponsorblock_remove", ""),
        getattr(args, "sponsorblock_api", ""),
    ))
    transfer_requested = any((
        getattr(args, "concurrent_fragments", 0),
        getattr(args, "retries", ""),
        getattr(args, "fragment_retries", ""),
        getattr(args, "retry_sleep", ""),
        getattr(args, "unavailable_fragments", ""),
        getattr(args, "throttled_rate", ""),
        getattr(args, "live_from_start", False),
        getattr(args, "wait_for_video", ""),
    )) or any(
        getattr(args, name, None) is not None
        for name in ("embed_chapters", "embed_metadata", "embed_thumbnail")
    )
    template_requested = bool(getattr(args, "arg_template", ""))
    requested_ytdlp_output = any((
        getattr(args, "format_spec", ""),
        getattr(args, "format_sort", ""),
        getattr(args, "format_sort_preset", ""),
        getattr(args, "container", ""),
        getattr(args, "audio_format", ""),
        getattr(args, "audio_quality", ""),
        getattr(args, "dub_lang", ""),
        subtitle_requested,
        sponsorblock_requested,
        transfer_requested,
        template_requested,
    ))
    if getattr(args, "audio_format", "") and getattr(args, "container", ""):
        _print_line("Error: Choose either --container or --audio-format, not both.")
        sys.exit(2)
    try:
        output_options = validate_download_options(
            format_spec=getattr(args, "format_spec", ""),
            format_sort=getattr(args, "format_sort", ""),
            format_sort_preset=getattr(args, "format_sort_preset", ""),
            container=getattr(args, "container", ""),
            audio_format=getattr(args, "audio_format", ""),
            audio_quality=getattr(args, "audio_quality", ""),
            dub_lang=getattr(args, "dub_lang", ""),
            mute=getattr(args, "mute", False),
        )
        subtitle_options = validate_subtitle_options(
            enabled=subtitle_requested,
            languages=getattr(args, "sub_langs", ""),
            automatic=getattr(args, "auto_subs", False),
            convert=getattr(args, "convert_subs", ""),
            embed=(getattr(args, "sub_delivery", "") or "embed") == "embed",
        )
        sponsorblock_options = validate_sponsorblock_options(
            enabled=sponsorblock_requested,
            mark=getattr(args, "sponsorblock_mark", ""),
            remove=getattr(args, "sponsorblock_remove", ""),
            api_url=getattr(args, "sponsorblock_api", ""),
        )
        transfer_options = validate_ytdlp_transfer_options(
            concurrent_fragments=getattr(args, "concurrent_fragments", 0),
            retries=getattr(args, "retries", ""),
            fragment_retries=getattr(args, "fragment_retries", ""),
            retry_sleep=getattr(args, "retry_sleep", ""),
            unavailable_fragments=getattr(
                args, "unavailable_fragments", ""
            ),
            throttled_rate=getattr(args, "throttled_rate", ""),
            live_from_start=getattr(args, "live_from_start", False),
            wait_for_video=getattr(args, "wait_for_video", ""),
            embed_chapters=getattr(args, "embed_chapters", None),
            embed_metadata=getattr(args, "embed_metadata", None),
            embed_thumbnail=getattr(args, "embed_thumbnail", None),
        )
        external_downloader_options = validate_external_downloader_options(
            downloader=getattr(args, "external_downloader", ""),
            connections=getattr(args, "aria2c_connections", 0),
            splits=getattr(args, "aria2c_splits", 0),
            min_split_size=getattr(args, "aria2c_min_split_size", ""),
        )
        if (output_options["audio_format"] and subtitle_options["enabled"]
                and subtitle_options["embed"]):
            raise ValueError(
                "Audio extraction cannot embed subtitles; use --sub-delivery sidecar"
            )
    except ValueError as error:
        _print_line(f"Error: {error}")
        sys.exit(2)

    from . import db
    from .config import install_file_logging, load_config, write_log_line

    app = QCoreApplication(sys.argv)
    _init_db_or_exit(db)
    install_file_logging()

    from .workers import FetchWorker, DownloadWorker

    cfg = load_config()
    from .extractors.ytdlp import apply_resolve_timeout_config
    apply_resolve_timeout_config(cfg)
    transfer_overrides = {}
    for name in (
        "concurrent_fragments", "retries", "fragment_retries",
        "retry_sleep", "unavailable_fragments", "throttled_rate",
        "wait_for_video",
    ):
        value = getattr(args, name, "")
        if value not in ("", 0, None):
            transfer_overrides[f"ytdlp_{name}"] = value
    if getattr(args, "live_from_start", False):
        transfer_overrides["ytdlp_live_from_start"] = True
    for name in ("embed_chapters", "embed_metadata", "embed_thumbnail"):
        value = getattr(args, name, None)
        if value is not None:
            transfer_overrides[f"ytdlp_{name}"] = value
    from .download_options import (
        resolve_ytdlp_arg_template, resolve_ytdlp_transfer_options,
    )
    try:
        transfer_options = resolve_ytdlp_transfer_options(
            cfg, overrides=transfer_overrides,
        )
    except ValueError as error:
        _print_line(f"Error: Invalid yt-dlp settings: {error}")
        sys.exit(2)
    output_dir = args.output or cfg.get("output_dir", "")
    if not output_dir:
        from .utils import default_output_dir
        output_dir = default_output_dir()

    quality_pref = (args.quality or "best").lower()
    folder_template = getattr(args, "folder_template", "") or ""
    file_template = getattr(args, "file_template", "") or ""
    template_name = getattr(args, "arg_template", "") or ""
    auth_profile_id = getattr(args, "auth_profile", "") or ""
    proxy = str(cfg.get("proxy", "") or "")

    _print_line(f"StreamKeep v{VERSION} (CLI)")
    _print_line(f"URL:     {args.url}")
    _print_line(f"Output:  {output_dir}")
    _print_line(f"Quality: {quality_pref}")
    _print_line("")

    state = {
        "phase": "fetch",
        "info": None,
        "exit_code": 0,
        "fw": None,
        "dw": None,
        "source_url": args.url,
    }

    # ── Fetch ──
    fw = FetchWorker(args.url)
    state["fw"] = fw  # prevent GC while event loop runs

    def on_fetch_done(info):
        nonlocal output_dir, quality_pref, folder_template, file_template
        nonlocal template_name, auth_profile_id, proxy
        state["info"] = info
        state["phase"] = "download"
        _print_line(
            f"Resolved: {info.platform} / {info.channel} / {info.title}"
        )
        _print_line(
            f"Duration: {info.duration_str or 'live'}  |  "
            f"Qualities: {len(info.qualities)}"
        )
        if getattr(info, "subtitles", None):
            languages = ", ".join(
                track.language for track in info.subtitles[:12]
            )
            more = "..." if len(info.subtitles) > 12 else ""
            _print_line(f"Subtitles: {languages}{more}")
        if not info.qualities:
            _print_line("Error: No downloadable qualities found.")
            _record_cli_failure(args.url, "fetch", "No downloadable qualities found", output_dir, info)
            state["exit_code"] = 1
            app.quit()
            return

        # Smart Mode is resolved after metadata because the final webpage URL
        # and platform provide the strongest match. CLI defaults such as
        # ``best`` are treated as implicit, so a profile may replace them;
        # explicit values and paths always win.
        from .smart_mode import apply_smart_profile_to_job
        smart_job = apply_smart_profile_to_job({
            "url": args.url,
            "webpage_url": getattr(info, "webpage_url", "") or args.url,
            "platform": getattr(info, "platform", "") or "",
            "quality": "" if quality_pref in {"", "best"} else quality_pref,
            "output_dir": getattr(args, "output", "") or "",
            "folder_template": getattr(args, "folder_template", "") or "",
            "file_template": getattr(args, "file_template", "") or "",
            "arg_template": getattr(args, "arg_template", "") or "",
            "proxy": "",
            "auth_profile_id": getattr(args, "auth_profile", "") or "",
        }, cfg)
        quality_pref = str(smart_job.get("quality") or quality_pref or "best").lower()
        if not getattr(args, "output", ""):
            output_dir = str(smart_job.get("output_dir") or output_dir)
        folder_template = str(
            smart_job.get("folder_template") or getattr(args, "folder_template", "") or ""
        )
        file_template = str(
            smart_job.get("file_template") or getattr(args, "file_template", "") or ""
        )
        template_name = str(
            smart_job.get("arg_template") or getattr(args, "arg_template", "") or ""
        )
        auth_profile_id = str(
            smart_job.get("auth_profile_id")
            or getattr(args, "auth_profile", "")
            or ""
        )
        proxy = str(smart_job.get("proxy") or cfg.get("proxy", "") or "")
        if smart_job.get("_smart_profile"):
            _print_line(f"Smart profile: {smart_job['_smart_profile']}")

        try:
            ytdlp_template_args = resolve_ytdlp_arg_template(
                cfg.get("ytdlp_arg_templates", {}), template_name,
            )
        except ValueError as error:
            _print_line(f"Error: Invalid Smart Mode template: {error}")
            _record_cli_failure(args.url, "download", str(error), output_dir, info)
            state["exit_code"] = 2
            app.quit()
            return

        # Pick quality
        qi = _pick_quality(info.qualities, quality_pref)
        _print_line(f"Selected: {qi.name} ({qi.resolution or qi.format_type})")
        _print_line("")
        if (requested_ytdlp_output or bool(template_name)) and qi.format_type != "ytdlp_direct":
            message = (
                "Format/output/subtitle controls require a yt-dlp direct "
                "source; the selected quality uses " + qi.format_type + "."
            )
            _print_line(f"Error: {message}")
            _record_cli_failure(
                args.url, "download", message, output_dir, info
            )
            state["exit_code"] = 2
            app.quit()
            return

        # Build a single whole-stream segment. The DownloadWorker downloads
        # each (seg_idx, label, start, duration) tuple with ffmpeg, so one
        # segment spanning the full duration yields a single output file.
        # One shared template resolver across GUI, CLI, and monitor jobs
        # (V39) so headless naming matches the desktop app exactly.
        from .utils import resolve_output_paths
        job_output_dir, label = resolve_output_paths(
            info,
            output_dir,
            folder_template=folder_template,
            file_template=file_template,
            config=cfg,
        )
        segments = [(0, label, 0, info.total_secs)]

        # Resolve the named profile against this URL up front so a scope
        # mismatch is reported instead of silently sending no credentials.
        from . import auth_profiles as _ap
        requested_profile = str(auth_profile_id or "").strip()
        if requested_profile:
            profile = _ap.resolve_profile(
                info.webpage_url or args.url,
                info.platform or "",
                profile_id=requested_profile,
            )
            if profile is None:
                _print_line(
                    f"Error: authentication profile {requested_profile!r} is "
                    "unknown or is not allowed for this site."
                )
                state["exit_code"] = 2
                app.quit()
                return
            auth_profile_id = profile.profile_id

        from .job_spec import DownloadJobSpec
        spec = DownloadJobSpec(
            source_platform=info.platform or "",
            source_id=getattr(info, "source_id", "") or "",
            webpage_url=getattr(info, "webpage_url", "") or "",
            playlist_url=qi.url,
            segments=tuple(tuple(s) for s in [segments[0]]),
            output_dir=job_output_dir,
            format_type=qi.format_type,
            audio_url=qi.audio_url,
            selected_tracks=tuple(default_media_tracks(qi)),
            ytdlp_source=qi.ytdlp_source,
            ytdlp_format=(
                output_options["format_spec"]
                or (
                    resolve_dubbed_format_spec(
                        audio_format=output_options["audio_format"],
                        dub_lang=output_options["dub_lang"],
                    )
                    if output_options["dub_lang"]
                    else (
                        "bestaudio/best"
                        if output_options["audio_format"]
                        else qi.ytdlp_format
                    )
                )
            ),
            ytdlp_format_sort=output_options["format_sort"],
            ytdlp_container=output_options["container"],
            ytdlp_audio_format=output_options["audio_format"],
            ytdlp_audio_quality=output_options["audio_quality"],
            dub_lang=output_options["dub_lang"],
            mute=output_options["mute"],
            download_subs=subtitle_options["enabled"],
            capture_youtube_chat=bool(getattr(args, "youtube_chat", False)),
            subtitle_languages=subtitle_options["languages"],
            subtitle_auto=subtitle_options["automatic"],
            subtitle_convert=subtitle_options["convert"],
            subtitle_embed=subtitle_options["embed"],
            sponsorblock=sponsorblock_options["enabled"],
            sponsorblock_mark=sponsorblock_options["mark"],
            sponsorblock_remove=sponsorblock_options["remove"],
            sponsorblock_api=sponsorblock_options["api_url"],
            ytdlp_concurrent_fragments=transfer_options.get("concurrent_fragments", 0),
            ytdlp_retries=transfer_options.get("retries", ""),
            ytdlp_fragment_retries=transfer_options.get("fragment_retries", ""),
            ytdlp_retry_sleep=transfer_options.get("retry_sleep", ""),
            ytdlp_unavailable_fragments=transfer_options.get("unavailable_fragments", ""),
            ytdlp_throttled_rate=transfer_options.get("throttled_rate", ""),
            ytdlp_live_from_start=transfer_options.get("live_from_start", False),
            live_engine_fallback=bool(
                cfg.get("live_engine_fallback", False)
            ),
            streamlink_live_engine=bool(
                cfg.get("streamlink_live_engine", False)
            ),
            streamlink_hls_start_offset=(
                cfg.get("streamlink_hls_start_offset", 0) or 0
            ),
            streamlink_hls_live_restart=bool(
                cfg.get("streamlink_hls_live_restart", False)
            ),
            twitch_unmute=bool(
                getattr(args, "twitch_unmute", False)
                or cfg.get("twitch_unmute", False)
            ),
            ytdlp_wait_for_video=transfer_options.get("wait_for_video", ""),
            ytdlp_embed_chapters=transfer_options.get("embed_chapters"),
            ytdlp_embed_metadata=transfer_options.get("embed_metadata"),
            ytdlp_embed_thumbnail=transfer_options.get("embed_thumbnail"),
            ytdlp_external_downloader=external_downloader_options["downloader"],
            ytdlp_aria2c_connections=int(getattr(args, "aria2c_connections", 0) or 0),
            ytdlp_aria2c_splits=int(getattr(args, "aria2c_splits", 0) or 0),
            ytdlp_aria2c_min_split_size=getattr(args, "aria2c_min_split_size", "") or "",
            ytdlp_template_name=template_name,
            ytdlp_template_args=tuple(ytdlp_template_args),
            proxy=proxy,
            rate_limit=args.rate_limit or "",
            auth_profile_id=auth_profile_id,
        )
        dw = DownloadWorker.from_spec(spec)
        state["dw"] = dw  # prevent GC while event loop runs

        dw.progress.connect(lambda si, pct, txt: _print_progress(
            f"[{pct:3d}%] {txt}"
        ))
        dw.log.connect(lambda msg: write_log_line(msg))
        def on_download_error(_si, msg):
            _print_line(f"Error: {msg}")
            state["exit_code"] = 1
            _record_cli_failure(
                args.url, "download", msg, job_output_dir, state.get("info")
            )

        dw.error.connect(on_download_error)
        dw.segment_done.connect(lambda si, path: _print_line(
            f"  segment {si} done"
        ))
        dw.all_done.connect(lambda: _on_download_done(state, app, job_output_dir))
        # Always quit when the worker thread ends. `all_done` (success) and
        # `error` are delivered before `finished`, so their slots run first;
        # this is a backstop so the process can never hang if a worker path
        # ends without emitting a terminal signal.
        dw.finished.connect(app.quit)
        dw.start()

    def on_fetch_error(msg):
        _print_line(f"Fetch error: {msg}")
        _record_cli_failure(args.url, "fetch", msg, output_dir)
        state["exit_code"] = 1
        app.quit()

    def on_vods_found(vods, platform_name, _next_cursor):
        # A channel URL resolved to a list of VODs. In headless mode there is
        # no picker UI, so auto-select the most recent one and resolve it.
        if not vods:
            on_fetch_error("No VODs found for this URL")
            return
        chosen = vods[0]
        _print_line(
            f"{len(vods)} VOD(s) found; selecting most recent: {chosen.title}"
        )
        fw2 = FetchWorker(
            args.url,
            vod_source=chosen.source,
            vod_platform=getattr(chosen, "platform", platform_name),
            vod_title=getattr(chosen, "title", ""),
            vod_channel=getattr(chosen, "channel", ""),
        )
        state["fw"] = fw2  # prevent GC; replaces the finished first worker
        fw2.finished.connect(on_fetch_done)
        fw2.error.connect(on_fetch_error)
        fw2.vods_found.connect(
            lambda *_: on_fetch_error("Unexpected nested VOD listing")
        )
        fw2.log.connect(lambda msg: write_log_line(msg))
        fw2.start()

    fw.finished.connect(on_fetch_done)
    fw.error.connect(on_fetch_error)
    fw.vods_found.connect(on_vods_found)
    fw.log.connect(lambda msg: write_log_line(msg))
    _print_line("Fetching...")
    fw.start()

    ret = app.exec() or state["exit_code"]
    # Wait for any in-flight workers to finish before exit
    for key in ("fw", "dw"):
        w = state.get(key)
        if w is not None and w.isRunning():
            w.wait(3000)
    sys.exit(ret)


def _on_download_done(state, app, job_output_dir):
    _print_progress("")
    _print_line(f"\nDownload complete -> {job_output_dir}")
    from . import db
    db.mark_failed_jobs_resolved_for_url(state.get("source_url", ""))
    try:
        from .verify import create_archive_manifest
        manifest = create_archive_manifest(job_output_dir, write_sidecar=True)
        _print_line(
            "Integrity manifest -> "
            f"{len(manifest.get('files', []) or [])} file(s)"
        )
    except Exception as e:
        _print_line(f"Warning: integrity manifest was not created: {e}")
    app.quit()


def _run_bagit(args):
    """Export BagIt fixity tags from an existing archive manifest."""
    from .verify import export_bagit

    try:
        result = export_bagit(args.path)
    except (OSError, ValueError) as error:
        _print_line(f"Error: BagIt export failed: {error}")
        sys.exit(2)
    if getattr(args, "json", False):
        _print_line(json.dumps(result, indent=2, ensure_ascii=False))
        return
    _print_line(
        f"BagIt export -> {args.path} ({result['payload_files']} file(s), "
        f"{result['payload_bytes']} bytes)"
    )
    for entry in result["files"]:
        _print_line(f"  {entry['path']}: {entry['sha384_sri']}")


def _run_tokens(args):
    """Manage scoped companion tokens through a running local server."""
    import secrets
    import time
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    from .config import load_config

    base_url = str(getattr(args, "server_url", "") or "").rstrip("/")
    if not base_url:
        _print_line("Error: --server-url is required")
        sys.exit(2)
    token = str(getattr(args, "token", "") or "")
    if not token:
        token = str(load_config().get("companion_token", "") or "")
    if not token:
        _print_line("Error: pass --token or configure the companion master token")
        sys.exit(2)

    command = str(getattr(args, "tokens_command", "") or "")
    path = "/api/tokens"
    payload = None
    method = "GET"
    if command == "create":
        scopes = list(getattr(args, "scope", []) or [])
        raw_scopes = str(getattr(args, "scopes", "") or "")
        scopes.extend(item.strip() for item in raw_scopes.split(",") if item.strip())
        scopes = list(dict.fromkeys(scopes))
        if not scopes:
            _print_line("Error: pass at least one --scope or --scopes value")
            sys.exit(2)
        payload = {
            "label": str(args.label or "").strip(),
            "scopes": scopes,
        }
        if getattr(args, "origin", ""):
            payload["origin"] = str(args.origin).strip()
        if getattr(args, "expires_in", None) is not None:
            payload["expires_in"] = int(args.expires_in)
        method = "POST"
    elif command == "revoke":
        path = "/api/tokens/" + str(args.token_id)
        method = "DELETE"
        payload = {}
    elif command != "list":
        _print_line(f"Error: unsupported token command {command!r}")
        sys.exit(2)

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    if payload is not None:
        headers.update({
            "Content-Type": "application/json",
            "X-StreamKeep-Timestamp": str(int(time.time())),
            "X-StreamKeep-Nonce": secrets.token_urlsafe(18),
        })
    request = Request(
        base_url + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            result = json.loads(error.read().decode("utf-8"))
        except (OSError, ValueError):
            result = {"ok": False, "err": str(error.reason or error)}
        _print_line(json.dumps(result, indent=2) if getattr(args, "json", False)
                    else f"Error: {result.get('message') or result.get('err') or error}")
        sys.exit(2)
    except (OSError, URLError) as error:
        _print_line(f"Error: token server request failed: {error}")
        sys.exit(2)

    if getattr(args, "json", False):
        _print_line(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if command == "list":
        rows = result.get("tokens", [])
        if not rows:
            _print_line("No active scoped tokens.")
            return
        for row in rows:
            origin = row.get("origin") or "Any origin"
            last_used = row.get("last_used") or "Never"
            expiry = row.get("expires_at") or "Never"
            _print_line(
                f"  {row.get('label', ''):<24s} {row.get('id', '')} "
                f"[{', '.join(row.get('scopes', []))}] "
                f"origin={origin} created={row.get('created_at', '')} "
                f"last_used={last_used} expires={expiry}"
            )
    elif command == "create":
        _print_line(
            f"Created scoped token {result.get('label', args.label)} "
            f"({result.get('id', '')}); store this value securely:"
        )
        _print_line(str(result.get("token", "")))
    else:
        _print_line(f"Revoked scoped token {result.get('id', args.token_id)}.")


def _pick_quality(qualities, pref):
    """Pick a quality entry matching *pref*."""
    if not qualities:
        return None
    pref = pref.lower().strip()
    if pref in ("best", "source", "highest", ""):
        return qualities[0]
    if pref == "lowest":
        return qualities[-1]
    # Try matching by name (e.g. "1080p", "720p")
    for q in qualities:
        if pref in (q.name or "").lower() or pref in (q.resolution or "").lower():
            return q
    # Fallback to best
    return qualities[0]


# ── gallery-dl second engine (V10) ──────────────────────────────────

def _run_gallery(args):
    """Download an image gallery / social-media post via gallery-dl.

    Shares StreamKeep's output folder, per-source download-archive, cookies,
    and proxy. Degrades with a clear install hint when gallery-dl is absent.
    """
    import subprocess

    from .config import load_config
    from .integrations.gallery_dl import (
        GalleryDlUnavailable,
        build_gallery_dl_command,
        gallery_dl_available,
        gallery_dl_install_hint,
    )

    if not gallery_dl_available():
        _print_line(f"Error: {gallery_dl_install_hint()}")
        sys.exit(1)

    cfg = load_config()
    output_dir = getattr(args, "output", "") or cfg.get("output_dir", "")
    if not output_dir:
        from .utils import default_output_dir
        output_dir = default_output_dir()

    archive_path = ""
    if not getattr(args, "no_archive", False):
        from .paths import source_archive_path
        archive_path = source_archive_path(args.url)

    cookies_file = ""
    if cfg.get("cookies_file"):
        cookies_file = str(cfg.get("cookies_file"))

    proxy = ""
    if cfg.get("proxy"):
        proxy = str(cfg.get("proxy"))

    try:
        cmd = build_gallery_dl_command(
            args.url,
            output_dir,
            archive_path=archive_path,
            cookies_file=cookies_file,
            proxy=proxy,
            simulate=getattr(args, "simulate", False),
            rate_limit=getattr(args, "rate_limit", "") or "",
        )
    except (ValueError, GalleryDlUnavailable) as error:
        _print_line(f"Error: {error}")
        sys.exit(2)

    _print_line("Engine:  gallery-dl")
    _print_line(f"Output:  {output_dir}")
    _print_line(f"Source:  {args.url}")
    try:
        result = subprocess.run(cmd, check=False)
    except OSError as error:
        _print_line(f"Error: could not start gallery-dl: {error}")
        sys.exit(1)
    if result.returncode == 0 and not getattr(args, "simulate", False):
        _print_line(f"\nGallery download complete -> {output_dir}")
    sys.exit(result.returncode)


# ── raw-protocol capture jobs (V9) ─────────────────────────────────

def _run_capture(args):
    """Run one operator-selected raw-protocol capture without yt-dlp."""
    from .raw_capture import RawCaptureError, RawCaptureSpec, run_raw_capture

    passphrase = os.environ.get("STREAMKEEP_SRT_PASSPHRASE", "")
    if getattr(args, "passphrase_stdin", False):
        try:
            passphrase = sys.stdin.readline().rstrip("\r\n")
        except (AttributeError, OSError):
            passphrase = ""
    try:
        spec = RawCaptureSpec(
            protocol=args.protocol,
            endpoint=args.endpoint,
            output_path=args.output,
            transport=args.transport,
            duration_secs=args.duration,
            max_duration_secs=args.max_duration,
            split_tracks=args.split_tracks,
            allow_self_signed=args.allow_self_signed,
            passphrase=passphrase,
        )
        from .raw_capture import validate_raw_capture
        validated = validate_raw_capture(spec)
    except (RawCaptureError, ValueError) as error:
        _print_line(f"Error: {error}")
        sys.exit(2)

    public = validated.spec.to_public_dict()
    _print_line(f"StreamKeep v{VERSION} (raw capture)")
    _print_line(
        f"Protocol: {validated.protocol} | Endpoint: {public['endpoint']}"
    )
    _print_line(f"Output: {validated.spec.output_path}")
    _print_line(
        f"Duration cap: {validated.spec.effective_duration_secs}s"
    )
    try:
        result = run_raw_capture(
            validated.spec,
            on_line=lambda line: _print_line(f"[capture] {line}"),
        )
    except (RawCaptureError, CapabilityUnavailableError) as error:
        _print_line(f"Error: {error}")
        sys.exit(1)
    if result.success:
        _print_line(f"Capture complete: {result.output_path}")
        if result.tracks_manifest:
            _print_line(f"Track manifest: {result.tracks_manifest}")
        return
    _print_line(
        f"Capture failed (exit {result.exit_code}): "
        f"{result.lines[-1] if result.lines else 'no diagnostic output'}"
    )
    sys.exit(1)


# ── lux fallback engine for CN platforms (V25) ──────────────────────

def _run_lux(args):
    """Download from a Chinese platform via lux (Bilibili/Douyin/Youku/...).

    Shares StreamKeep's output folder and cookies. lux honours HTTP(S)_PROXY
    from the environment, so a configured proxy is injected there. Degrades
    with a clear install hint when lux is absent.
    """
    import subprocess

    from .config import load_config
    from .integrations.lux import (
        LuxUnavailable,
        build_lux_command,
        lux_available,
        lux_install_hint,
    )

    if not lux_available():
        _print_line(f"Error: {lux_install_hint()}")
        sys.exit(1)

    cfg = load_config()
    output_dir = getattr(args, "output", "") or cfg.get("output_dir", "")
    if not output_dir:
        from .utils import default_output_dir
        output_dir = default_output_dir()

    cookie = str(cfg.get("cookies_file", "") or "")

    try:
        cmd = build_lux_command(
            args.url,
            output_dir,
            cookie=cookie,
            info=getattr(args, "info", False),
            stream_format=getattr(args, "stream_format", "") or "",
        )
    except (ValueError, LuxUnavailable) as error:
        _print_line(f"Error: {error}")
        sys.exit(2)

    env = dict(os.environ)
    proxy = str(cfg.get("proxy", "") or "")
    if proxy:
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy

    _print_line("Engine:  lux")
    _print_line(f"Output:  {output_dir}")
    _print_line(f"Source:  {args.url}")
    try:
        result = subprocess.run(cmd, check=False, env=env)
    except OSError as error:
        _print_line(f"Error: could not start lux: {error}")
        sys.exit(1)
    if result.returncode == 0 and not getattr(args, "info", False):
        _print_line(f"\nDownload complete -> {output_dir}")
    sys.exit(result.returncode)


# ── --server handler ────────────────────────────────────────────────

def _run_server(args):
    """Start the REST API / web remote server headlessly."""
    config_dir = getattr(args, "config_dir", "") or ""
    if config_dir:
        from .paths import CONFIG_DIR
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    from .config import load_config, save_config
    cfg = load_config()
    output_dir = getattr(args, "output_dir", "") or str(cfg.get("output_dir", "") or "")
    if output_dir:
        cfg["output_dir"] = output_dir
        save_config(cfg)

    # Apply the YouTube player_client strategy (config default, --youtube-client
    # override) so the CLI/headless download honors it like the GUI does.
    from .extractors.ytdlp import (
        YOUTUBE_PLAYER_CLIENT_PRESETS,
        YtDlpExtractor,
        apply_resolve_timeout_config,
    )
    apply_resolve_timeout_config(cfg)
    yt_client = getattr(args, "youtube_client", "") or str(
        cfg.get("youtube_player_client", "") or ""
    )
    if yt_client and yt_client not in YOUTUBE_PLAYER_CLIENT_PRESETS:
        _print_line(
            f"Unknown --youtube-client '{yt_client}'. Valid: "
            + ", ".join(k for k in YOUTUBE_PLAYER_CLIENT_PRESETS if k)
        )
        sys.exit(2)
    YtDlpExtractor.youtube_player_client = yt_client

    from . import db
    app = QCoreApplication(sys.argv)
    _init_db_or_exit(db)
    from .config import install_file_logging
    install_file_logging()

    from .local_server import LocalCompanionServer
    from .headless_service import HeadlessJobService

    def _bounded_config_int(key, default, maximum):
        try:
            value = int(cfg.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(1, min(maximum, value))

    proxy_origin = str(getattr(args, "trusted_proxy_origin", "") or "").strip()
    bind_lan = bool(proxy_origin)
    if args.bind != "127.0.0.1" and not bind_lan:
        _print_line(
            "ERROR: Direct LAN HTTP binding is disabled. Configure an HTTPS "
            "reverse proxy and pass --trusted-proxy-origin instead."
        )
        raise SystemExit(2)

    from .local_server import generate_bearer_token, valid_bearer_token
    master_token = str(cfg.get("companion_token", "") or "")
    if not valid_bearer_token(master_token):
        master_token = generate_bearer_token()
    cfg["companion_token"] = master_token
    if not save_config(cfg):
        _print_line("ERROR: Secure credential storage is unavailable; server stayed off.")
        raise SystemExit(2)

    try:
        server = LocalCompanionServer(
            bind_lan=bind_lan,
            external_origin=proxy_origin,
            master_token=master_token,
            port=args.port or 0,
        )
    except ValueError as error:
        _print_line(f"ERROR: {error}")
        raise SystemExit(2) from None
    service = HeadlessJobService(
        output_dir=output_dir,
        max_concurrent=_bounded_config_int("max_concurrent_downloads", 3, 8),
        parallel_connections=_bounded_config_int("parallel_connections", 4, 16),
        config=cfg,
    )
    server.state_provider = service.state_snapshot
    server.probe_submitter = service.probe
    server.queue_submitter = service.enqueue
    server.job_canceller = service.cancel
    server.failure_retrier = service.retry_failure
    server.failure_retry_canceller = service.cancel_failure_retry
    server.failure_discarder = service.discard_failure

    server.url_received.connect(
        lambda url, action: _print_line(f"[{action}] {url}")
    )
    try:
        recovered = service.start()
    except RuntimeError as error:
        _print_line(f"ERROR: {error}")
        server.stop()
        raise SystemExit(2) from None
    server.start()

    _print_line(f"StreamKeep v{VERSION} - server mode")
    _print_line(f"Listening on 127.0.0.1:{server.port}")
    if bind_lan:
        _print_line(f"HTTPS reverse-proxy origin: {server.url}")
    _print_line(f"Web UI: {server.url}")
    if bool(getattr(args, "pairing_code_stdout", False)):
        _print_line(f"One-time pairing code (5 minutes): {server.create_pairing_code()}")
    else:
        _print_line("Pair clients from the GUI, or restart with --pairing-code-stdout.")
    if config_dir:
        _print_line(f"Config: {config_dir}")
    if output_dir:
        _print_line(f"Output: {output_dir}")
    if recovered:
        _print_line(f"Recovered jobs: {recovered}")
    _print_line("Press Ctrl+C to stop.")

    try:
        ret = app.exec()
    finally:
        server.stop()
        service.stop()
    sys.exit(ret)


# ── --list-extractors ───────────────────────────────────────────────

def _list_extractors():
    """Print all registered extractors and exit."""
    # Import all extractors so they auto-register
    __import__("streamkeep.extractors")
    _print_line(f"StreamKeep v{VERSION} - supported platforms:")
    _print_line("")
    for cls in _ExtBase._registry:
        patterns = ", ".join(
            getattr(p, "pattern", str(p)) for p in cls.URL_PATTERNS[:3]
        )
        _print_line(f"  {cls.NAME:<16s}  {patterns}")
    _print_line(f"\n  ({len(_ExtBase._registry)} extractors registered)")


def _print_plugin_diagnostics(diagnostics, *, heading="StreamKeep plugin contract diagnostics:"):
    """Print the same contract review data exposed by ``plugins --json``."""
    _print_line(heading)
    if not diagnostics:
        _print_line("  No plugins discovered.")
        return
    for plugin in diagnostics:
        state = "compatible" if plugin.get("compatible") else "incompatible"
        trusted = "trusted" if plugin.get("trusted") else "untrusted"
        review = (
            "current contract approved"
            if plugin.get("trust_reviewed")
            else "review required before enabling"
        )
        _print_line(
            f"  {plugin.get('id', '?')} v{plugin.get('version', '?')} - "
            f"{state} ({trusted}; {review})"
        )
        permissions = plugin.get("permissions") or []
        _print_line(
            "    Permissions: "
            + (", ".join(str(permission) for permission in permissions) or "none")
        )
        dependencies = plugin.get("dependencies") or []
        dependency_labels = []
        for dependency in dependencies:
            label = str(dependency.get("name", ""))
            minimum = str(dependency.get("minimum_version", "") or "")
            if minimum:
                label += f" >= {minimum}"
            dependency_labels.append(label)
        _print_line(
            "    Dependencies: " + (", ".join(dependency_labels) or "none")
        )
        compatibility = plugin.get("compatibility") or {}
        _print_line(
            "    Compatibility: "
            f"{compatibility.get('range', 'unspecified')} "
            f"(manifest v{compatibility.get('manifest_version', '?')}, "
            f"running {compatibility.get('current_app_version', VERSION)})"
        )
        entrypoints = plugin.get("entrypoints") or []
        entrypoint_labels = [
            f"{item.get('type', '?')}:{item.get('entrypoint', '?')}"
            for item in entrypoints
        ]
        _print_line(
            "    Entry points: "
            + (", ".join(entrypoint_labels) or "none")
        )
        for adapter in plugin.get("adapters", []):
            _print_line(
                f"    Adapter {adapter.get('type', '?')}:{adapter.get('entrypoint', '?')} "
                f"(interface {adapter.get('interface_version', '?')}, "
                f"timeout {float(adapter.get('timeout_seconds', 0)):g}s)"
            )
        for error in plugin.get("errors", []):
            _print_line(f"    ERROR: {error}")
        for warning in plugin.get("warnings", []):
            _print_line(f"    WARNING: {warning}")


def _run_plugins(args):
    """Report plugin adapter compatibility and optionally load trusted plugins."""
    from .plugins import discover_plugins, diagnose_plugin, load_all_plugins

    found = discover_plugins()
    diagnostics = []
    for plugin in found:
        report = diagnose_plugin(plugin)
        report.update({
            "enabled": bool(plugin.get("enabled", False)),
            "trusted": bool(plugin.get("trusted", False)),
            "path": plugin.get("path", ""),
            "error": plugin.get("error", ""),
        })
        diagnostics.append(report)

    if getattr(args, "load_trusted", False):
        if not getattr(args, "json", False):
            _print_plugin_diagnostics(
                diagnostics,
                heading=(
                    "StreamKeep plugin contract review (shown before loading):"
                ),
            )
        events = []
        loaded, errors = load_all_plugins(events.append)
        if getattr(args, "json", False):
            payload = {
                "plugins": diagnostics,
                "load": {"loaded": loaded, "errors": errors, "events": events},
            }
            _print_line(json.dumps(payload, indent=2, sort_keys=True))
            return
        _print_line(f"Trusted plugins loaded: {loaded}; errors: {errors}")
        for event in events:
            _print_line(event)
        return

    if getattr(args, "json", False):
        _print_line(json.dumps({"plugins": diagnostics}, indent=2, sort_keys=True))
        return
    _print_plugin_diagnostics(
        diagnostics,
        heading=f"StreamKeep v{VERSION} - plugin adapter diagnostics:",
    )


def _run_operations(args):
    """Read, act on, or export the durable operations view."""
    from .operations import (
        OperationsFilters,
        discard_failure_ids,
        query_operations,
        retry_failure_ids,
        write_operations_report,
    )

    filters = OperationsFilters.from_mapping(vars(args))
    actions = []
    retry_ids = getattr(args, "retry", []) or []
    discard_ids = getattr(args, "discard", []) or []
    if retry_ids:
        actions.extend({"action": "retry", **result}
                       for result in retry_failure_ids(retry_ids))
    if discard_ids:
        actions.extend({"action": "discard", **result}
                       for result in discard_failure_ids(discard_ids))

    output = str(getattr(args, "output", "") or "").strip()
    if output:
        report = write_operations_report(output, filters)
        if getattr(args, "json", False):
            _print_line(json.dumps({"output": output, "report": report}, indent=2))
        else:
            _print_line(
                f"Operations report written to {output} "
                f"({report['row_count']} row(s){', truncated' if report['truncated'] else ''})."
            )
        return

    page = query_operations(filters)
    if getattr(args, "json", False):
        payload = page.to_dict()
        if actions:
            payload["actions"] = actions
        _print_line(json.dumps(payload, indent=2))
        return
    summary = page.summary
    _print_line(
        f"Operations: {summary.total_count} item(s), {summary.active_count} active, "
        f"{summary.failure_count} failure(s), {summary.monitor_count} monitor(s)"
    )
    _print_line(
        f"Estimated: {summary.estimated_size_bytes} bytes, "
        f"{summary.estimated_duration_seconds:.0f}s duration; "
        f"last success {summary.last_success_at or '—'}; "
        f"next run {summary.next_run_at or '—'}"
    )
    if summary.retry_reason:
        _print_line(f"Latest retry reason: {summary.retry_reason}")
    for action in actions:
        _print_line(
            f"{action['action'].title()} failure {action['failure_id']}: "
            f"{'OK' if action['ok'] else 'not available'}"
        )
    for row in page.rows:
        _print_line(
            f"  {row.kind:<8} {row.state:<12} {row.source or 'Unknown':<16} "
            f"{row.title or row.item_id}"
        )
        if row.kind == "failure" and row.remediation.get("message"):
            _print_line(f"    What to do: {row.remediation['message']}")
            if row.remediation.get("action"):
                _print_line(f"    Action: {row.remediation['action']}")


def _run_snapshot(args):
    """Export a privacy-redacted diagnostic snapshot."""
    from .diagnostics import create_diagnostic_snapshot
    from datetime import datetime
    out = args.output or f"streamkeep_diag_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    ok, msg = create_diagnostic_snapshot(out)
    _print_line(f"Snapshot: {'OK' if ok else 'FAILED'} - {msg}")
    if ok:
        _print_line(f"File: {out}")
    else:
        sys.exit(1)


def _run_mse_capture(args):
    """Capture one DRM-free MSE page through the headless recorder."""
    from .config import load_config
    from .mse_capture import (
        MSECaptureError,
        MSEEncryptedError,
        record_mse_page,
    )

    cfg = load_config()
    output = str(args.output or "").strip()
    if not output:
        configured = str(cfg.get("output_dir", "") or "").strip()
        output = os.path.join(configured or os.getcwd(), "mse-capture.mp4")
    try:
        result = record_mse_page(
            args.url,
            output,
            wait_seconds=args.seconds,
            log_fn=_print_line,
            allow_private_network=bool(args.allow_lan),
            cleanup_on_success=not bool(args.keep_staging),
        )
    except MSEEncryptedError as error:
        _print_line(f"ERROR: {error}")
        raise SystemExit(2) from None
    except MSECaptureError as error:
        _print_line(f"ERROR: {error}")
        raise SystemExit(1) from None
    _print_line(
        f"MSE capture complete: {result.output_path} "
        f"({result.chunks} chunks, {result.bytes_written} bytes)"
    )
    if result.staging_dir:
        _print_line(f"Staging retained: {result.staging_dir}")


def _run_db_maintenance(args):
    """Run a database maintenance action."""
    import json as _json
    from . import db
    _init_db_or_exit(db)
    action = getattr(args, "action", "info")
    if action == "info":
        diag = db.db_diagnostics()
        _print_line(_json.dumps(diag, indent=2))
    elif action == "check":
        ok, detail = db.check_integrity()
        _print_line(f"Integrity: {'PASS' if ok else 'FAIL'}")
        _print_line(detail)
        if not ok:
            sys.exit(1)
    elif action == "optimize":
        result = db.run_optimize()
        _print_line(f"Optimize: {result}")
    elif action == "checkpoint":
        ok, detail = db.checkpoint_wal()
        _print_line(f"WAL checkpoint: {'OK' if ok else 'FAILED'} - {detail}")
        if not ok:
            sys.exit(1)
    elif action == "vacuum":
        ok, detail = db.vacuum_after_backup()
        _print_line(f"Vacuum: {'OK' if ok else 'FAILED'} - {detail}")
        if not ok:
            sys.exit(1)
    elif action == "rebuild":
        from .maintenance import apply_library_rebuild, plan_library_rebuild
        from .rebuild import load_rebuild_plan, save_rebuild_plan

        plan_path = Path(
            getattr(args, "plan", "") or
            Path(db.DB_PATH).parent / "maintenance" / "rebuild-plan.json"
        ).expanduser()
        try:
            if getattr(args, "apply", False):
                plan = load_rebuild_plan(plan_path)
                result = apply_library_rebuild(plan, db_module=db)
                if getattr(args, "json", False):
                    _print_line(_json.dumps(result.__dict__, indent=2))
                else:
                    _print_line(
                        f"Rebuild {result.status}: {result.rebuilt} rebuilt, "
                        f"{result.skipped} skipped, {result.conflicts} conflict(s)."
                    )
                    if result.backup_path:
                        _print_line(f"Backup: {result.backup_path}")
                    for error in result.errors:
                        _print_line(f"  ERROR: {error}")
                if result.status != "completed":
                    sys.exit(1)
                return
            root = getattr(args, "rebuild_from", "") or ""
            if not root:
                _print_line("Error: db rebuild preview requires --from <root>.")
                sys.exit(2)
            plan = plan_library_rebuild(root, db_module=db)
            save_rebuild_plan(plan, plan_path)
            if getattr(args, "json", False):
                payload = plan.to_dict()
                payload["plan_path"] = str(plan_path)
                _print_line(_json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                counts = plan.diagnostics
                _print_line(
                    f"Rebuild preview: {counts['rebuild']} rebuild, "
                    f"{counts['skip']} skip, {counts['conflict']} conflict; "
                    f"{counts['issues']} issue(s)."
                )
                for issue in plan.issues:
                    _print_line(
                        f"  {issue.get('kind', 'issue').upper():10s} "
                        f"{issue.get('path', '')} — {issue.get('reason', '')}"
                    )
                _print_line(f"Plan saved to {plan_path}")
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            _print_line(f"Error: {error}")
            sys.exit(2)


def _run_startup_check(args):
    """Construct the real application offscreen and emit a readiness file."""
    config_dir = getattr(args, "config_dir", "") or ""
    if not config_dir:
        _print_line("Error: startup-check requires --config-dir.")
        sys.exit(2)
    from .startup_check import run_startup_check
    result = run_startup_check(
        ready_file=args.ready_file,
        fixture=args.fixture,
    )
    sys.exit(0 if result.get("ready") else 1)


def _run_backup(args):
    """Create/restore ordinary or explicit portable-secret backups."""
    action = str(args.action)
    if action == "create":
        from .backup import create_backup
        ok, message = create_backup(args.path, include_logs=args.include_logs)
    elif action == "restore":
        from .backup import restore_backup
        ok, message = restore_backup(args.path)
    else:
        password = os.environ.get("STREAMKEEP_PORTABLE_SECRET_PASSWORD", "")
        if not password:
            import getpass
            password = getpass.getpass("Portable-secret backup password: ")
            if action == "secrets-export":
                confirmation = getpass.getpass("Confirm password: ")
                if password != confirmation:
                    _print_line("Backup failed: passwords do not match.")
                    sys.exit(1)
        from .portable_secrets import (
            create_portable_secret_backup,
            restore_portable_secret_backup,
        )
        if action == "secrets-export":
            ok, message = create_portable_secret_backup(args.path, password)
        else:
            ok, message = restore_portable_secret_backup(args.path, password)
    _print_line(message)
    if not ok:
        sys.exit(1)


def _run_har_import(args):
    """Extract media/manifest URLs (and replay headers) from a HAR capture."""
    from .har import har_entry_ytdlp_headers, parse_har

    path = str(getattr(args, "path", "") or "")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            raw = handle.read()
    except OSError as error:
        _print_line(f"Error: cannot read HAR file: {error}")
        sys.exit(2)
    try:
        links = parse_har(
            raw, include_segments=bool(getattr(args, "include_segments", False))
        )
    except ValueError as error:
        _print_line(f"Error: {error}")
        sys.exit(2)

    if not links:
        _print_line("No media or streaming-manifest requests found in the HAR capture.")
        return

    if getattr(args, "json", False):
        import json as _json
        _print_line(_json.dumps(links, indent=2, ensure_ascii=False))
        return

    for link in links:
        _print_line(link["url"])
        if getattr(args, "headers", False):
            header_argv = har_entry_ytdlp_headers(link)
            for i in range(0, len(header_argv), 2):
                _print_line(f"    {header_argv[i]} {header_argv[i + 1]!r}")


def _run_library_import(args):
    """Preview or apply adoption of an existing media library."""
    from . import db
    from .maintenance import (
        apply_library_adoption,
        plan_library_adoption,
    )
    from .importer import load_adoption_plan, save_adoption_plan

    _init_db_or_exit(db)
    default_plan = (
        Path(db.DB_PATH).parent / "maintenance" / "adoption-plan.json"
    )
    plan_path = Path(getattr(args, "plan", "") or default_plan).expanduser()
    action = str(getattr(args, "import_action", "preview") or "preview")
    as_json = bool(getattr(args, "json", False))
    try:
        if action == "preview":
            plan = plan_library_adoption(
                args.root,
                getattr(args, "archive", []) or [],
                archive_source_url=getattr(args, "archive_source_url", "") or "",
                db_module=db,
            )
            save_adoption_plan(plan, plan_path)
            if as_json:
                payload = plan.to_dict()
                payload["plan_path"] = str(plan_path)
                _print_line(json.dumps(payload, indent=2, ensure_ascii=False))
                return
            counts = plan.diagnostics
            _print_line(
                f"Adoption preview: {counts['adopt']} adopt, "
                f"{counts['skip']} skip, {counts['conflict']} conflict; "
                f"{counts['archive_entries']} archive id(s)."
            )
            for item in plan.items:
                _print_line(
                    f"  {item.get('action', 'conflict').upper():8s} "
                    f"{item.get('path', '')} — {item.get('reason', '')}"
                )
            for issue in plan.archive_issues:
                _print_line(
                    f"  CONFLICT archive {issue.get('path', '')}: "
                    f"{issue.get('reason', '')}"
                )
            _print_line(f"Plan saved to {plan_path}")
            return

        plan = load_adoption_plan(plan_path)
        result = apply_library_adoption(plan, db_module=db)
        if as_json:
            _print_line(json.dumps(result.__dict__, indent=2, ensure_ascii=False))
        else:
            _print_line(
                f"Adoption {result.status}: {result.adopted} adopted, "
                f"{result.skipped} skipped, {result.conflicts} conflict(s)."
            )
            if result.backup_path:
                _print_line(f"Backup: {result.backup_path}")
            for error in result.errors:
                _print_line(f"  ERROR: {error}")
        if result.status != "completed":
            sys.exit(1)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        _print_line(f"Error: {error}")
        sys.exit(2)


def _run_retemplate(args):
    """Preview or apply a strict archive output-template migration."""
    from . import db
    from .maintenance import (
        apply_library_retemplate,
        load_retemplate_plan,
        plan_library_retemplate,
        save_retemplate_plan,
    )

    _init_db_or_exit(db)
    default_plan = Path(db.DB_PATH).parent / "maintenance" / "retemplate-plan.json"
    plan_path = Path(getattr(args, "plan", "") or default_plan).expanduser()
    action = str(getattr(args, "retemplate_action", "preview") or "preview")
    as_json = bool(getattr(args, "json", False))
    try:
        if action == "preview":
            plan = plan_library_retemplate(
                args.root,
                getattr(args, "folder_template", "") or "",
                getattr(args, "file_template", "") or "",
                db_module=db,
            )
            save_retemplate_plan(plan, plan_path)
            if as_json:
                payload = plan.to_dict()
                payload["plan_path"] = str(plan_path)
                _print_line(json.dumps(payload, indent=2, ensure_ascii=False))
                return
            counts = plan.diagnostics["retemplate"]
            _print_line(
                f"Re-template preview: {counts['ready']} ready, "
                f"{counts['unchanged']} unchanged, {counts['conflicts']} conflict(s)."
            )
            for item in plan.actions:
                _print_line(
                    f"  {item.kind.upper():20s} {item.payload.get('old_path', '')}"
                    f" → {item.payload.get('new_path', '')}"
                    f" — {item.payload.get('reason', item.detail)}"
                )
            _print_line(f"Plan saved to {plan_path}")
            return

        plan = load_retemplate_plan(plan_path)
        requested = [str(value) for value in (getattr(args, "action_id", []) or [])]
        approved = requested or [
            item.action_id for item in plan.actions
            if item.kind == "retemplate" and item.payload.get("status") == "ready"
        ]
        result = apply_library_retemplate(plan, approved, db_module=db)
        if as_json:
            _print_line(json.dumps(result.__dict__, indent=2, ensure_ascii=False))
        else:
            _print_line(
                f"Re-template {result.status}: {result.applied} applied, "
                f"{result.failed} failed, {result.skipped} skipped."
            )
            if result.backup_path:
                _print_line(f"Backup: {result.backup_path}")
            for error in result.errors:
                _print_line(f"  ERROR: {error}")
        if result.status != "completed" or result.failed:
            sys.exit(1)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        _print_line(f"Error: {error}")
        sys.exit(2)


def _run_podcast_sidecars(args):
    """Discover and download an episode's transcript/chapter sidecars."""
    from .image_fetch import ImageFetchError, fetch_url_bytes
    from .podcast_sidecars import sync_podcast_sidecars
    from .utils import safe_filename

    out_dir = str(getattr(args, "out_dir", "") or "")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as error:
        _print_line(f"Error: cannot create output directory: {error}")
        sys.exit(2)
    try:
        feed_bytes = fetch_url_bytes(
            str(args.feed), max_bytes=16 * 1024 * 1024,
            accept="application/rss+xml, application/xml, text/xml, */*",
        )
    except (ImageFetchError, OSError) as error:
        _print_line(f"Error: could not fetch feed: {error}")
        sys.exit(2)
    feed_body = feed_bytes.decode("utf-8", errors="replace")

    base = str(getattr(args, "base", "") or "").strip()
    if not base:
        import urllib.parse
        name = urllib.parse.urlsplit(str(args.enclosure)).path.rsplit("/", 1)[-1]
        base = safe_filename(os.path.splitext(name)[0] or "episode")
    manifest = sync_podcast_sidecars(
        feed_body, str(args.enclosure), out_dir, base, log_fn=_print_line,
    )
    if not manifest:
        _print_line("No transcript or chapter sidecars found for this episode.")
        return
    for entry in manifest:
        _print_line(
            f"{entry['kind']}: {entry['file']} "
            f"({entry.get('language') or 'und'}) sha256={entry['sha256'][:12]}"
        )


def _run_credentials_check(args):
    """Validate stored platform credentials and the cookie profile.

    Exit code 1 when any probe reports a hard failure (invalid/expired/
    insufficient-scope); rate-limited, unsupported, network, and
    no-credential outcomes are advisory and keep exit code 0.
    """
    from . import credential_check as cc

    timeout = int(getattr(args, "timeout", 15) or 15)
    target = getattr(args, "platform", "all") or "all"
    if target == "all":
        results = cc.probe_all(timeout=timeout)
    else:
        results = [cc.probe_platform(target, timeout=timeout)]

    if getattr(args, "json", False):
        _print_line(json.dumps([r.as_dict() for r in results], indent=2))
    else:
        for r in results:
            extra = f" - {r.detail}" if r.detail else ""
            _print_line(f"  {r.platform:<10s} {r.label}{extra}")

    hard_fail = {cc.INVALID, cc.EXPIRED, cc.INSUFFICIENT_SCOPE}
    if any(r.status in hard_fail for r in results):
        sys.exit(1)


def _run_auth(args, parser):
    """Manage site-bound authentication profiles without printing secrets."""
    from . import auth_profiles as ap

    command = getattr(args, "auth_command", "") or "list"
    as_json = bool(getattr(args, "json", False))

    if command == "list":
        views = [ap.public_view(profile) for profile in ap.list_profiles()]
        if as_json:
            _print_line(json.dumps(views, indent=2))
        elif not views:
            _print_line("No authentication profiles configured.")
        else:
            for view in views:
                scope = ", ".join(view["hosts"] + view["platforms"]) or "(none)"
                state = "credentials" if view["has_credentials"] else "empty"
                _print_line(
                    f"  {view['name']:<20s} {view['profile_id']}  "
                    f"[{state}]  {scope}"
                )
        return

    if command == "create":
        try:
            profile = ap.create_profile(
                args.name, hosts=args.host, platforms=args.platform,
            )
        except ap.AuthProfileError as error:
            _print_line(f"Error: {error}")
            sys.exit(2)
        _print_line(f"Created profile {profile.name} ({profile.profile_id})")
        return

    if command in ("import", "check", "delete"):
        profile = ap.find_profile(args.profile)
        if profile is None:
            _print_line(f"Error: no authentication profile named {args.profile!r}")
            sys.exit(2)
        if command == "import":
            if args.browser:
                ok, message = ap.import_from_browser(profile.profile_id, args.browser)
            elif args.file:
                ok, message = ap.import_from_file(profile.profile_id, args.file)
            else:
                _print_line("Error: pass --browser or --file")
                sys.exit(2)
            _print_line(message)
            sys.exit(0 if ok else 1)
        if command == "check":
            from . import credential_check as cc
            path = ap.cookies_path(profile.profile_id)
            if not path:
                _print_line(f"{profile.name}: no credential material stored")
                sys.exit(1)
            result = cc.probe_cookies(path=path)
            if as_json:
                _print_line(json.dumps(result.as_dict(), indent=2))
            else:
                detail = f" - {result.detail}" if result.detail else ""
                _print_line(f"  {profile.name:<20s} {result.label}{detail}")
            hard_fail = {cc.INVALID, cc.EXPIRED, cc.INSUFFICIENT_SCOPE}
            sys.exit(1 if result.status in hard_fail else 0)
        ap.delete_profile(profile.profile_id)
        _print_line(f"Deleted profile {profile.name}")
        return

    parser.parse_args(["auth", "--help"])


def _run_youtube_health(args):
    """Report the YouTube capability picture (runtime, providers, client).

    Exit code 1 when the runtime is not ready (yt-dlp/JS runtime missing or
    blocked); a missing PO-token provider is advisory and keeps exit code 0.
    """
    from .config import load_config
    from .extractors.ytdlp import youtube_health_report

    config = load_config()
    runtime_actions = []
    preference = str(
        getattr(args, "javascript_runtime_preference", "") or ""
    ).strip().lower()
    if preference:
        from .config import save_config
        from .capabilities import invalidate_runtime_capabilities_cache

        config["javascript_runtime_preference"] = preference
        if not save_config(config):
            _print_line("Error: JavaScript runtime preference could not be saved.")
            sys.exit(1)
        invalidate_runtime_capabilities_cache()
        runtime_actions.append({
            "action": "set-preference",
            "preference": preference,
            "ok": True,
        })
    if getattr(args, "remove_deno", False):
        from .javascript_runtime import DenoRuntimeError, remove_managed_deno
        from .capabilities import invalidate_runtime_capabilities_cache

        try:
            removed = remove_managed_deno()
        except DenoRuntimeError as error:
            _print_line(f"Error: {error}")
            sys.exit(1)
        invalidate_runtime_capabilities_cache()
        runtime_actions.append({
            "action": "remove-deno",
            "removed": bool(removed),
            "ok": True,
        })
    archive_path = str(getattr(args, "deno_archive", "") or "").strip()
    if getattr(args, "install_deno", False) or archive_path:
        from .javascript_runtime import DenoRuntimeError, install_managed_deno
        from .capabilities import invalidate_runtime_capabilities_cache

        try:
            installed = install_managed_deno(archive_path or None)
        except DenoRuntimeError as error:
            _print_line(f"Error: {error}")
            sys.exit(1)
        invalidate_runtime_capabilities_cache()
        runtime_actions.append({
            "action": "install-deno",
            "ok": True,
            "path": installed.get("path", ""),
            "version": installed.get("version", ""),
            "source": installed.get("source", ""),
            "sha256": installed.get("sha256", ""),
        })
    if getattr(args, "setup_pot_provider", False):
        from .pot_provider import ensure_provider
        ok, message = ensure_provider(config, log_fn=_print_line)
        _print_line(message)
        if not ok:
            sys.exit(1)

    preset = str(config.get("youtube_player_client", "") or "")
    report = youtube_health_report(player_client=preset, config=config)
    if runtime_actions:
        report["runtime_actions"] = runtime_actions

    if getattr(args, "json", False):
        _print_line(json.dumps(report, indent=2))
    else:
        _print_line(f"YouTube capability: {report['summary'] or report['state']}")
        _print_line(f"  yt-dlp version : {report['yt_dlp_version'] or 'unknown'}")
        runtime = report.get("js_runtime") or {}
        _print_line(f"  JS runtime     : {runtime.get('name') or 'none'}")
        _print_line(
            f"  Runtime source : {runtime.get('source') or runtime.get('provenance') or 'none'}"
        )
        for action in runtime_actions:
            _print_line(f"  Runtime action : {action['action']} completed")
        _print_line(f"  EJS available  : {'yes' if report['ejs_available'] else 'no'}")
        _print_line(f"  player_client  : {report['player_client']}")
        endpoint = report.get("pot_endpoint") or {}
        _print_line(
            f"  PO-token       : "
            f"{'detected' if report['pot_provider']['available'] else 'not detected'}"
            f"{' (answering)' if endpoint.get('reachable') else ''}"
        )
        if endpoint.get("base_url"):
            _print_line(f"  PO-token URL   : {endpoint['base_url']}")
        ytse = report.get("ytse") or {}
        if ytse.get("available"):
            ytse_label = f"available ({ytse.get('version') or 'unknown'})"
        elif ytse.get("installed"):
            ytse_label = "installed, SABR unavailable"
        else:
            ytse_label = "not installed (optional)"
        _print_line(f"  SABR fallback  : {ytse_label}")
        if ytse.get("installed") or ytse.get("available"):
            _print_line(f"  SABR limits     : {', '.join(ytse.get('limitations') or [])}")
        remote = report.get("remote_backend") or {}
        remote_label = "disabled"
        if remote.get("configured"):
            remote_label = "reachable" if remote.get("reachable") else "unreachable"
        _print_line(f"  Remote backend : {remote_label}")
        if remote.get("plugin_id"):
            _print_line(f"  Backend plugin : {remote['plugin_id']}")
        for warning in report["warnings"]:
            _print_line(f"  ! {warning}")
        pot_setup = report.get("pot_setup") or {}
        if not pot_setup.get("provider_present", True):
            _print_line("  Fix YouTube SABR/PO-token gating:")
            for step in pot_setup.get("steps", []):
                _print_line(f"    {step}")

    if not report["healthy"]:
        sys.exit(1)


def _run_intelligence(args):
    """Run local-first summaries/thumbnails with explicit cloud consent."""
    from .intelligence.runtime import (
        IntelligenceError,
        delete_profile,
        get_runtime,
        list_profiles,
        save_profile,
    )

    command = str(getattr(args, "intelligence_command", "") or "jobs")
    profile_command = str(getattr(args, "profile_command", "") or "")
    as_json = bool(getattr(args, "json", False))

    if command == "profiles":
        if profile_command in ("", "list"):
            profiles = list_profiles()
            _print_line(json.dumps(profiles, indent=2) if as_json else (
                "No intelligence profiles configured."
                if not profiles else "\n".join(
                    f"  {item['label'] or item['profile_id']:<20s} "
                    f"{item['profile_id']} [{item['provider_label']}] "
                    f"model={item['model']} "
                    f"{'[key]' if item['has_api_key'] else '[no key]'}"
                    for item in profiles
                )
            ))
            return
        if profile_command == "delete":
            if not delete_profile(args.profile_id):
                _print_line("Error: intelligence profile was not found")
                sys.exit(2)
            _print_line(f"Deleted intelligence profile {args.profile_id}")
            return
        if profile_command == "save":
            api_key = ""
            if getattr(args, "api_key_stdin", False):
                api_key = sys.stdin.readline().rstrip("\r\n")
            config = {
                "model": args.model,
                "api_url": args.api_url,
                "redact_default": bool(args.redact_default),
            }
            if api_key:
                config["api_key"] = api_key
            try:
                profile = save_profile(
                    args.profile_id, args.provider, config, label=args.label,
                )
            except Exception as error:
                _print_line(f"Error: {error}")
                sys.exit(2)
            _print_line(json.dumps(profile, indent=2) if as_json else (
                f"Saved intelligence profile {profile['profile_id']} "
                f"({profile['provider_label']}, model={profile['model']})."
            ))
            return

    runtime = get_runtime()
    if command == "jobs":
        jobs = runtime.list_jobs(kind=getattr(args, "kind", ""), limit=100)
        _print_line(json.dumps(jobs, indent=2) if as_json else (
            "No intelligence jobs." if not jobs else "\n".join(
                f"  {job['job_id']} {job['kind']} {job['status']} "
                f"{int(float(job.get('progress', 0)) * 100)}% "
                f"{job.get('provider_label', '')}"
                for job in jobs
            )
        ))
        return
    if command == "cancel":
        if not runtime.cancel(args.job_id):
            _print_line("Error: intelligence job was not cancellable")
            sys.exit(2)
        _print_line(f"Cancellation requested for {args.job_id}")
        return
    if command == "edit":
        text = args.text
        if args.file:
            try:
                text = Path(args.file).read_text(encoding="utf-8")
            except OSError as error:
                _print_line(f"Error: {error}")
                sys.exit(2)
        try:
            job = runtime.edit_summary(args.job_id, text)
        except Exception as error:
            _print_line(f"Error: {error}")
            sys.exit(2)
        _print_line(json.dumps(job, indent=2) if as_json else "Summary updated.")
        return
    if command == "rebuild":
        try:
            job = runtime.rebuild_summary(
                args.job_id, consent_token=args.consent_token, wait=True,
            )
        except Exception as error:
            _print_line(f"Error: {error}")
            sys.exit(2)
        _print_line(json.dumps(job, indent=2) if as_json else (
            f"Summary rebuild {job.get('status', 'unknown')}: {job.get('job_id', '')}"
        ))
        return
    if command == "thumbnail":
        try:
            job = runtime.start_thumbnail(
                args.recording_dir, title=args.title, channel=args.channel,
                date=args.date, wait=True,
            )
        except Exception as error:
            _print_line(f"Error: {error}")
            sys.exit(2)
        _print_line(json.dumps(job, indent=2) if as_json else (
            f"Smart thumbnail {job.get('status', 'unknown')}: "
            f"{job.get('result_name', '')}"
        ))
        return
    if command == "preview":
        try:
            preview = runtime.preview(
                args.recording_dir, profile_id=args.profile_id,
                provider=args.provider, model=args.model, api_url=args.api_url,
                redact=args.redact,
            )
        except Exception as error:
            _print_line(f"Error: {error}")
            sys.exit(2)
        _print_line(json.dumps(preview, indent=2) if as_json else (
            f"Provider: {preview['provider_label']}\n"
            f"Model: {preview['model']}\n"
            f"Payload: {preview['payload_chars']} chars, "
            f"sha256={preview['payload_sha256']}\n"
            f"Redaction: {'applied' if preview['redaction_applied'] else 'off'}\n"
            f"Consent token: {preview['consent_token'] or '(not required)'}\n\n"
            f"{preview['payload']}"
        ))
        return
    if command == "summary":
        try:
            preview = runtime.preview(
                args.recording_dir, profile_id=args.profile_id,
                provider=args.provider, model=args.model, api_url=args.api_url,
                redact=args.redact,
            )
            if preview["requires_consent"] and not args.consent:
                _print_line(json.dumps(preview, indent=2) if as_json else (
                    f"Cloud summary requires explicit consent for "
                    f"{preview['provider_label']} ({preview['model']}).\n"
                    f"Exact payload ({preview['payload_chars']} chars, "
                    f"sha256={preview['payload_sha256']}):\n\n{preview['payload']}\n\n"
                    "Re-run with --consent after reviewing this payload."
                ))
                sys.exit(2)
            job = runtime.start_summary(
                args.recording_dir, profile_id=args.profile_id,
                provider=args.provider, model=args.model, api_url=args.api_url,
                consent_token=preview["consent_token"], redact=args.redact,
                wait=True,
            )
        except IntelligenceError as error:
            _print_line(f"Error: {error}")
            sys.exit(2)
        except Exception as error:
            _print_line(f"Error: {error}")
            sys.exit(1)
        _print_line(json.dumps({"preview": preview, "job": job}, indent=2)
                    if as_json else (
                        f"Summary {job.get('status', 'unknown')}: "
                        f"{job.get('result_name', '')}"
                    ))
        return

    _print_line("Use 'intelligence --help' for available commands.")


def _run_protocol_register(args):
    """Register the per-user streamkeep:// handler on the current OS."""
    from .protocol import register_protocol
    ok, message = register_protocol()
    _print_line(message)
    if not ok:
        sys.exit(1)


def _run_protocol_unregister(args):
    """Remove the per-user streamkeep:// handler on the current OS."""
    from .protocol import unregister_protocol
    ok, message = unregister_protocol()
    _print_line(message)
    if not ok:
        sys.exit(1)


def _run_bookmarklet(args):
    """Print a browser bookmarklet that hands the current page to StreamKeep."""
    from .protocol import build_bookmarklet
    _print_line(build_bookmarklet())


# ── Entry point ─────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(
        prog="StreamKeep",
        description=f"StreamKeep v{VERSION} - multi-platform stream/VOD downloader",
    )
    p.add_argument("--version", action="version", version=f"StreamKeep v{VERSION}")
    p.add_argument("--config-dir", default="",
                   help="Override the config/database directory")

    sub = p.add_subparsers(dest="command")

    # -- download --
    dl = sub.add_parser("download", aliases=["dl"], help="Download a URL")
    dl.add_argument("url", help="URL to download")
    dl.add_argument("-q", "--quality", default="best",
                    help="Quality preference: best, 1080p, 720p, 480p, lowest")
    dl.add_argument("-o", "--output", default="",
                    help="Output directory (default: config or ~/Videos/StreamKeep)")
    dl.add_argument("--rate-limit", default="",
                    help="Bandwidth limit (e.g. 5M, 500K)")
    dl.add_argument(
        "--filename-template", dest="file_template", default="",
        help=(
            "Filename template, e.g. \"{channel} - {title}\". Falls back to "
            "the configured global default."
        ),
    )
    dl.add_argument(
        "--folder-template", dest="folder_template", default="",
        help=(
            "Folder template under the output directory, e.g. "
            "\"{channel}/{date}\". Falls back to the configured default."
        ),
    )
    dl.add_argument(
        "--auth-profile", dest="auth_profile", default="",
        help=(
            "Named site-bound authentication profile to use. Ignored when "
            "the profile does not cover this URL."
        ),
    )
    dl.add_argument(
        "--youtube-client", dest="youtube_client", default="",
        choices=["", "web_safari", "android_vr", "tv", "ios", "mweb", "resilient"],
        help="YouTube player_client strategy (overrides the saved default)",
    )
    dl.add_argument(
        "-f", "--format", "--format-spec", dest="format_spec", default="",
        help="Raw yt-dlp format specification (passed verbatim to -f)",
    )
    sort_group = dl.add_mutually_exclusive_group()
    sort_group.add_argument(
        "--format-sort", default="",
        help="Custom yt-dlp format-sort expression (passed verbatim to -S)",
    )
    sort_group.add_argument(
        "--format-sort-preset", default="",
        choices=["prefer-av1", "cap-2160p", "cap-1080p", "cap-720p", "smallest"],
        help="Named yt-dlp format-sort preset",
    )
    dl.add_argument(
        "--container", default="", choices=["mp4", "mkv", "webm", "original"],
        help="Video merge/remux container (default: mp4)",
    )
    dl.add_argument(
        "--audio-format", default="",
        choices=["best", "mp3", "m4a", "opus", "flac", "wav"],
        help="Extract audio in the selected format instead of keeping video",
    )
    dl.add_argument(
        "--audio-quality", default="",
        help="Audio encoder quality (0-10 or bitrate such as 128K)",
    )
    dl.add_argument(
        "--dub-lang", default="",
        help="Prefer a dubbed yt-dlp audio track (ISO 639-1 code, e.g. en)",
    )
    dl.add_argument(
        "--mute", action="store_true",
        help="Strip audio and write a video-only output",
    )
    dl.add_argument(
        "--arg-template", default="",
        help="Named structured yt-dlp argument template from Settings",
    )
    dl.add_argument(
        "--sub-langs", default="",
        help="Comma-separated subtitle languages or yt-dlp regexes (e.g. en,es)",
    )
    dl.add_argument(
        "--auto-subs", action="store_true",
        help="Include automatically generated captions for --sub-langs",
    )
    dl.add_argument(
        "--convert-subs", default="", choices=["srt", "vtt", "ass"],
        help="Convert downloaded subtitles to this format",
    )
    dl.add_argument(
        "--sub-delivery", default="", choices=["embed", "sidecar"],
        help="Embed subtitles or keep sidecar files (default: embed)",
    )
    dl.add_argument(
        "--youtube-chat", action="store_true",
        help="For YouTube VODs, also fetch the live-chat replay "
             "(live_chat.json), normalized into the chat pipeline at finalize",
    )
    dl.add_argument(
        "--sponsorblock-mark", default="",
        help="Comma-separated SponsorBlock categories to mark as chapters",
    )
    dl.add_argument(
        "--sponsorblock-remove", default="",
        help="Comma-separated SponsorBlock categories to remove",
    )
    dl.add_argument(
        "--sponsorblock-api", default="",
        help="Custom SponsorBlock API base URL (HTTPS, or loopback HTTP)",
    )
    dl.add_argument(
        "-N", "--concurrent-fragments", type=int, default=0,
        help="Concurrent HLS/DASH fragments (1-32; default: yt-dlp default)",
    )
    dl.add_argument(
        "--retries", default="",
        help="Download retries (0-1000 or infinite)",
    )
    dl.add_argument(
        "--fragment-retries", default="",
        help="Fragment retries (0-1000 or infinite)",
    )
    dl.add_argument(
        "--retry-sleep", default="",
        help="yt-dlp retry sleep expression, e.g. fragment:exp=1:20",
    )
    dl.add_argument(
        "--unavailable-fragments", default="", choices=["skip", "abort"],
        help="Skip unavailable fragments or abort the download",
    )
    dl.add_argument(
        "--throttled-rate", default="",
        help="Treat sustained rates below this threshold as throttled",
    )
    dl.add_argument(
        "--live-from-start", action="store_true",
        help="Download a live stream from its beginning when supported",
    )
    dl.add_argument(
        "--twitch-unmute", action="store_true",
        help=(
            "For Twitch VODs, probe and restore copyright-muted fragments "
            "when the same-format unmuted CDN URL is available"
        ),
    )
    dl.add_argument(
        "--external-downloader", default="", choices=["", "aria2c"],
        help="Route direct HTTP downloads through aria2c; HLS/DASH sources "
             "use native -N instead since yt-dlp 2026.07.04 removed aria2c "
             "HLS/DASH support (source URL sanitized; CVE-2026-50574)",
    )
    dl.add_argument(
        "--aria2c-connections", type=int, default=0,
        help="aria2c connections per server (1-16; requires --external-downloader aria2c)",
    )
    dl.add_argument(
        "--aria2c-splits", type=int, default=0,
        help="aria2c download splits (1-16; requires --external-downloader aria2c)",
    )
    dl.add_argument(
        "--aria2c-min-split-size", default="",
        help="aria2c minimum split size, e.g. 1M (requires --external-downloader aria2c)",
    )
    dl.add_argument(
        "--wait-for-video", default="",
        help="Wait interval for scheduled streams: seconds or MIN-MAX",
    )
    dl.add_argument(
        "--embed-chapters", action=argparse.BooleanOptionalAction, default=None,
        help="Embed or explicitly do not embed chapters",
    )
    dl.add_argument(
        "--embed-metadata", action=argparse.BooleanOptionalAction, default=None,
        help="Embed or explicitly do not embed media metadata",
    )
    dl.add_argument(
        "--embed-thumbnail", action=argparse.BooleanOptionalAction, default=None,
        help="Embed or explicitly do not embed the thumbnail",
    )
    dl.add_argument("--config-dir", default=argparse.SUPPRESS,
                    help="Override the config/database directory")

    # -- raw-protocol capture jobs (V9) --
    capture_p = sub.add_parser(
        "capture",
        help="Capture a camera, listener, multicast, SRT, or ICY source",
    )
    capture_p.add_argument(
        "protocol",
        choices=[
            "rtsp", "rtmp-listen", "srt-caller", "srt-listener",
            "udp", "rtp", "icy",
        ],
        help="Raw input protocol/job type",
    )
    capture_p.add_argument("endpoint", help="Protocol endpoint or bind URI")
    capture_p.add_argument(
        "-o", "--output", required=True,
        help="Output file (or base filename for --split-tracks)",
    )
    capture_p.add_argument(
        "--transport", choices=["tcp", "udp"], default="tcp",
        help="RTSP transport (default: tcp)",
    )
    capture_p.add_argument(
        "--duration", type=int, default=0,
        help="Capture duration in seconds (default: until stopped/cap)",
    )
    capture_p.add_argument(
        "--max-duration", type=int, default=7 * 24 * 60 * 60,
        help="Hard duration cap in seconds (default: 604800)",
    )
    capture_p.add_argument(
        "--split-tracks", action="store_true",
        help="For ICY radio, split files when StreamTitle changes",
    )
    capture_p.add_argument(
        "--allow-self-signed", action="store_true",
        help="For RTSPS/RTMPS, disable TLS peer verification (FFmpeg 8+)",
    )
    capture_p.add_argument(
        "--passphrase-stdin", action="store_true",
        help="Read an SRT passphrase from one line of stdin",
    )
    capture_p.add_argument(
        "--config-dir", default=argparse.SUPPRESS,
        help="Override the config/database directory",
    )

    # -- server --
    srv = sub.add_parser("server", help="Start REST API / web remote UI")
    srv.add_argument("--port", type=int, default=0,
                     help="Port to bind (default: random)")
    srv.add_argument("--bind", default="127.0.0.1",
                     help="Deprecated compatibility option; only 127.0.0.1 is served")
    srv.add_argument("--trusted-proxy-origin", default="",
                     help="Exact HTTPS origin for a local reverse proxy")
    srv.add_argument("--pairing-code-stdout", action="store_true",
                     help="Print one short-lived pairing code for headless setup")
    srv.add_argument("--config-dir", default=argparse.SUPPRESS,
                     help="Override the config/database directory")
    srv.add_argument("--output-dir", default="",
                     help="Default output directory for queued downloads")

    # -- list-extractors --
    ext_p = sub.add_parser("extractors", help="List supported platforms")

    # -- plugins --
    plugin_p = sub.add_parser(
        "plugins", help="Inspect versioned plugin adapter contracts",
    )
    plugin_p.add_argument(
        "--load-trusted", action="store_true",
        help="Load enabled plugins explicitly marked trusted",
    )
    plugin_p.add_argument("--json", action="store_true", help="Emit JSON diagnostics")

    # -- operations --
    operations_p = sub.add_parser(
        "operations", help="Inspect the paged queue, monitor, and failure view",
    )
    operations_p.add_argument("--state", default="", help="Filter by state or failed/active")
    operations_p.add_argument("--source", default="", help="Filter by source/platform")
    operations_p.add_argument("--stage", default="", help="Filter by pipeline stage")
    operations_p.add_argument(
        "--kind", choices=["", "queue", "monitor", "failure"], default="",
        help="Filter by durable record kind",
    )
    operations_p.add_argument("--search", default="", help="Search title, source, or retry reason")
    operations_p.add_argument("--page", type=int, default=0)
    operations_p.add_argument("--page-size", type=int, default=50)
    operations_p.add_argument("--retry", nargs="*", default=[], metavar="FAILURE_ID")
    operations_p.add_argument("--discard", nargs="*", default=[], metavar="FAILURE_ID")
    operations_p.add_argument("--output", default="", help="Write a redacted JSON or CSV report")
    operations_p.add_argument("--json", action="store_true", help="Emit machine-readable output")
    ext_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                       help="Override the config/database directory")

    # -- gallery-dl second engine (V10) --
    gal_p = sub.add_parser(
        "gallery",
        help="Download an image gallery / social post via gallery-dl",
    )
    gal_p.add_argument("url", help="Gallery / social-media post URL")
    gal_p.add_argument("-o", "--output", default="",
                       help="Output directory (default: configured output dir)")
    gal_p.add_argument("--rate-limit", default="",
                       help="Maximum download rate (e.g. 500k, 2.5M)")
    gal_p.add_argument("-s", "--simulate", action="store_true",
                       help="List what would be downloaded without downloading")
    gal_p.add_argument("--no-archive", action="store_true",
                       help="Do not use the per-source download-archive (re-fetch all)")
    gal_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                       help="Override the config/database directory")

    # -- lux CN-platform fallback engine (V25) --
    lux_p = sub.add_parser(
        "lux",
        help="Download from a CN platform (Bilibili/Douyin/Youku) via lux",
    )
    lux_p.add_argument("url", help="Bilibili / Douyin / Youku / iQIYI / ... URL")
    lux_p.add_argument("-o", "--output", default="",
                       help="Output directory (default: configured output dir)")
    lux_p.add_argument("-f", "--stream-format", default="",
                       help="Select a specific lux stream format id")
    lux_p.add_argument("-i", "--info", action="store_true",
                       help="Show available streams without downloading")
    lux_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                       help="Override the config/database directory")

    # -- db maintenance --
    db_p = sub.add_parser("db", help="Database maintenance and diagnostics")
    db_p.add_argument("action", nargs="?", default="info",
                      choices=["info", "check", "optimize", "checkpoint", "vacuum", "rebuild"],
                      help="Action: info (default), check, optimize, checkpoint, vacuum")
    db_p.add_argument(
        "--from", dest="rebuild_from", default="",
        help="Library root for a sidecar rebuild preview",
    )
    db_p.add_argument(
        "--plan", default="",
        help="Rebuild plan path (default: config maintenance/rebuild-plan.json)",
    )
    db_p.add_argument(
        "--apply", action="store_true",
        help="Apply the saved rebuild plan instead of previewing",
    )
    db_p.add_argument("--json", action="store_true", help="Emit rebuild JSON")
    db_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                      help="Override the config/database directory")

    # -- diagnostic snapshot --
    diag_p = sub.add_parser("snapshot", help="Export a privacy-redacted diagnostic ZIP")
    diag_p.add_argument("-o", "--output", default="",
                        help="Output path (default: streamkeep_diag_<timestamp>.zip)")
    diag_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                        help="Override the config/database directory")

    # -- secret-free and explicit encrypted backups --
    backup_p = sub.add_parser("backup", help="Create or restore backups")
    backup_p.add_argument(
        "action",
        choices=["create", "restore", "secrets-export", "secrets-import"],
        help=(
            "create/restore excludes auth; secrets-export/secrets-import uses "
            "an Argon2id + AES-GCM password"
        ),
    )
    backup_p.add_argument("path", help="Backup file path")
    backup_p.add_argument(
        "--include-logs", action="store_true",
        help="Include redacted application logs in an ordinary backup",
    )
    backup_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                          help="Override the config/database directory")

    # -- BagIt fixity export --
    bagit_p = sub.add_parser(
        "bagit", help="Export BagIt fixity tags from an archive manifest",
    )
    bagit_p.add_argument("path", help="Recording directory containing the archive manifest")
    bagit_p.add_argument("--json", action="store_true", help="Emit the export summary as JSON")
    bagit_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                         help="Override the config/database directory")

    # -- scoped companion API tokens --
    tokens_p = sub.add_parser(
        "tokens", help="List, create, or revoke scoped companion API tokens",
    )
    tokens_sub = tokens_p.add_subparsers(dest="tokens_command")
    tokens_sub.required = True

    def add_token_connection_args(parser):
        parser.add_argument(
            "--server-url", "--url", dest="server_url",
            default="http://127.0.0.1:8787",
            help="Running StreamKeep companion URL (default: http://127.0.0.1:8787)",
        )
        parser.add_argument(
            "--token", default="",
            help="Master bearer token (defaults to the configured companion token)",
        )
        parser.add_argument("--json", action="store_true", help="Emit JSON")

    tokens_list = tokens_sub.add_parser("list", help="List redacted active token metadata")
    add_token_connection_args(tokens_list)
    tokens_create = tokens_sub.add_parser("create", help="Mint one scoped token")
    tokens_create.add_argument("--label", required=True, help="Operator label for the token")
    tokens_create.add_argument(
        "--scope", action="append", default=[], choices=["status", "queue", "recovery"],
        help="Granted scope; repeat for multiple scopes",
    )
    tokens_create.add_argument(
        "--scopes", default="",
        help="Comma-separated granted scopes (alternative to repeated --scope)",
    )
    tokens_create.add_argument("--origin", default="", help="Optional exact browser origin binding")
    tokens_create.add_argument(
        "--expires-in", type=int, default=None,
        help="Optional lifetime in seconds (60 to 2592000)",
    )
    add_token_connection_args(tokens_create)
    tokens_revoke = tokens_sub.add_parser("revoke", help="Revoke one scoped token by id")
    tokens_revoke.add_argument("token_id", help="Opaque token id from tokens list")
    add_token_connection_args(tokens_revoke)

    # -- HAR import: extract media/manifest links from a browser capture --
    har_p = sub.add_parser(
        "import-har",
        help="Extract media/manifest URLs and replay headers from a HAR capture",
    )
    har_p.add_argument("path", help="HAR file exported from a browser network panel")
    har_p.add_argument(
        "--json", action="store_true",
        help="Emit the full link table as JSON (URLs, type, and replay headers)",
    )
    har_p.add_argument(
        "--headers", action="store_true",
        help="Also print yt-dlp --add-header arguments beneath each URL",
    )
    har_p.add_argument(
        "--include-segments", action="store_true",
        help="Keep individual HLS/DASH segment URLs instead of collapsing to manifests",
    )
    har_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                       help="Override the config/database directory")

    # -- external library adoption --
    import_p = sub.add_parser(
        "import-library",
        aliases=["adopt"],
        help="Preview or adopt an existing media library without moving files",
    )
    import_sub = import_p.add_subparsers(dest="import_action")
    import_sub.required = True
    import_preview = import_sub.add_parser(
        "preview", help="Classify folders and archive ids without changing state",
    )
    import_preview.add_argument("root", help="Directory tree to inspect")
    import_preview.add_argument(
        "--archive", action="append", default=[],
        help="yt-dlp --download-archive file (repeatable)",
    )
    import_preview.add_argument(
        "--archive-source-url", default="",
        help="Source URL that owns archive ids without a matching monitor",
    )
    import_preview.add_argument(
        "--plan", default="",
        help="Preview plan path (default: config maintenance/adoption-plan.json)",
    )
    import_preview.add_argument("--json", action="store_true", help="Emit the full preview as JSON")
    import_apply = import_sub.add_parser(
        "apply", help="Apply an unchanged preview plan",
    )
    import_apply.add_argument(
        "--plan", default="",
        help="Preview plan path (default: config maintenance/adoption-plan.json)",
    )
    import_apply.add_argument("--json", action="store_true", help="Emit the result as JSON")
    import_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                          help="Override the config/database directory")

    # -- archive output-template migration --
    retemplate_p = sub.add_parser(
        "retemplate",
        help="Preview or apply an archive-wide output-template migration",
    )
    retemplate_sub = retemplate_p.add_subparsers(dest="retemplate_action")
    retemplate_sub.required = True
    retemplate_preview = retemplate_sub.add_parser(
        "preview", help="Show every proposed path before moving anything",
    )
    retemplate_preview.add_argument("root", help="Archive root to migrate")
    retemplate_preview.add_argument(
        "--folder-template", default="",
        help="New folder template, e.g. {channel}/{year}",
    )
    retemplate_preview.add_argument(
        "--filename-template", "--file-template", dest="file_template", default="",
        help="New filename template, e.g. {title}",
    )
    retemplate_preview.add_argument(
        "--plan", default="",
        help="Preview plan path (default: config maintenance/retemplate-plan.json)",
    )
    retemplate_preview.add_argument("--json", action="store_true", help="Emit the full preview as JSON")
    retemplate_apply = retemplate_sub.add_parser(
        "apply", help="Apply all ready actions from an unchanged preview",
    )
    retemplate_apply.add_argument(
        "--plan", default="",
        help="Preview plan path (default: config maintenance/retemplate-plan.json)",
    )
    retemplate_apply.add_argument(
        "--action-id", action="append", default=[],
        help="Apply only this action id (repeatable; default: all ready actions)",
    )
    retemplate_apply.add_argument("--json", action="store_true", help="Emit the result as JSON")
    retemplate_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                              help="Override the config/database directory")

    # -- DRM-free MSE capture (V14) --
    mse_p = sub.add_parser(
        "mse-capture",
        help="Record a DRM-free SourceBuffer page in a headless browser",
    )
    mse_p.add_argument("url", help="HTTP(S) page URL to capture")
    mse_p.add_argument(
        "-o", "--output", default="",
        help="Output media path (default: output_dir/mse-capture.mp4)",
    )
    mse_p.add_argument(
        "--seconds", type=float, default=30.0,
        help="How long to leave the one page open (default: 30 seconds)",
    )
    mse_p.add_argument(
        "--allow-lan", action="store_true",
        help="Allow RFC1918/ULA page and media targets; loopback stays blocked",
    )
    mse_p.add_argument(
        "--keep-staging", action="store_true",
        help="Keep the ordered SourceBuffer chunks after a successful remux",
    )
    mse_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                       help="Override the config/database directory")

    # -- podcast transcript/chapter sidecars (Podcast Namespace) --
    ps_p = sub.add_parser(
        "podcast-sidecars",
        help="Discover and download an episode's transcript/chapter sidecars",
    )
    ps_p.add_argument("feed", help="Podcast RSS feed URL")
    ps_p.add_argument("enclosure", help="Episode enclosure (media) URL")
    ps_p.add_argument("out_dir", help="Directory to write sidecars into")
    ps_p.add_argument(
        "--base", default="",
        help="Sidecar base filename (default: derived from the enclosure)",
    )
    ps_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                      help="Override the config/database directory")

    # -- streamkeep:// protocol handler + bookmarklet (V23) --
    sub.add_parser(
        "register-protocol",
        help="Register the per-user streamkeep:// handler on this OS",
    ).add_argument("--config-dir", default=argparse.SUPPRESS,
                   help="Override the config/database directory")
    sub.add_parser(
        "unregister-protocol",
        help="Remove the per-user streamkeep:// handler on this OS",
    ).add_argument("--config-dir", default=argparse.SUPPRESS,
                   help="Override the config/database directory")
    sub.add_parser(
        "bookmarklet",
        help="Print a browser bookmarklet that sends the current page to StreamKeep",
    ).add_argument("--config-dir", default=argparse.SUPPRESS,
                   help="Override the config/database directory")

    yth_p = sub.add_parser(
        "youtube-health",
        help="Report YouTube capability: yt-dlp/JS runtime, PO-token, player_client",
    )
    yth_p.add_argument("--json", action="store_true",
                       help="Emit the redacted report as JSON")
    yth_p.add_argument(
        "--setup-pot-provider", action="store_true",
        help=(
            "Set up the local PO-token provider: install the plugin where "
            "possible, launch a configured local server, then re-probe"
        ),
    )
    yth_p.add_argument(
        "--install-deno", action="store_true",
        help="Explicitly download and install the pinned Deno runtime",
    )
    yth_p.add_argument(
        "--deno-archive", default="",
        help="Install the pinned Deno ZIP from a local archive without network access",
    )
    yth_p.add_argument(
        "--remove-deno", action="store_true",
        help="Remove the managed Deno runtime installed by StreamKeep",
    )
    yth_p.add_argument(
        "--javascript-runtime-preference", choices=["path", "managed"], default="",
        help="Prefer a PATH runtime or StreamKeep-managed Deno for YouTube",
    )
    yth_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                       help="Override the config/database directory")

    cred_p = sub.add_parser(
        "credentials",
        help="Validate stored platform credentials and the cookie profile",
    )
    cred_p.add_argument(
        "platform", nargs="?", default="all",
        choices=["all", "twitch", "youtube", "kick", "cookies"],
        help="Which credential to check (default: all)",
    )
    cred_p.add_argument("--json", action="store_true",
                        help="Emit redacted results as JSON")
    cred_p.add_argument("--timeout", type=int, default=15,
                        help="Per-probe network timeout in seconds")
    cred_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                        help="Override the config/database directory")

    auth_p = sub.add_parser(
        "auth",
        help="Manage named, site-bound authentication profiles",
    )
    auth_sub = auth_p.add_subparsers(dest="auth_command")
    auth_sub.add_parser("list", help="List profiles and their declared scope")
    auth_create = auth_sub.add_parser("create", help="Create an empty profile")
    auth_create.add_argument("name", help="Operator label for the profile")
    auth_create.add_argument(
        "--host", action="append", default=[],
        help="Allowed host (repeatable); subdomains are included",
    )
    auth_create.add_argument(
        "--platform", action="append", default=[],
        help="Allowed platform name (repeatable)",
    )
    auth_import = auth_sub.add_parser(
        "import", help="Load credential material into a profile",
    )
    auth_import.add_argument("profile", help="Profile name or opaque ID")
    auth_import.add_argument("--browser", default="",
                             help="Import cookies from an installed browser")
    auth_import.add_argument("--file", default="",
                             help="Import an existing Netscape cookies.txt")
    auth_check = auth_sub.add_parser(
        "check", help="Validate one profile's stored cookies locally",
    )
    auth_check.add_argument("profile", help="Profile name or opaque ID")
    auth_delete = auth_sub.add_parser(
        "delete", help="Remove a profile and shred its material",
    )
    auth_delete.add_argument("profile", help="Profile name or opaque ID")
    auth_p.add_argument("--json", action="store_true",
                        help="Emit redacted results as JSON")
    auth_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                        help="Override the config/database directory")

    # -- local-first intelligence --
    intelligence_p = sub.add_parser(
        "intelligence",
        help="Run consent-aware summaries and smart thumbnail analysis",
    )
    intelligence_sub = intelligence_p.add_subparsers(
        dest="intelligence_command"
    )
    intelligence_sub.required = False

    intelligence_profiles = intelligence_sub.add_parser(
        "profiles", help="List, save, or delete provider profiles"
    )
    profile_sub = intelligence_profiles.add_subparsers(dest="profile_command")
    profile_sub.required = False
    profile_sub.add_parser("list", help="List redacted profiles")
    profile_save = profile_sub.add_parser("save", help="Save a provider profile")
    profile_save.add_argument("profile_id")
    profile_save.add_argument("--provider", choices=["ollama", "openai", "anthropic"],
                              default="ollama")
    profile_save.add_argument("--model", default="")
    profile_save.add_argument("--api-url", default="")
    profile_save.add_argument("--label", default="")
    profile_save.add_argument("--api-key-stdin", action="store_true",
                              help="Read the API key from one line of stdin")
    profile_save.add_argument("--redact-default", action="store_true")
    profile_delete = profile_sub.add_parser("delete", help="Delete a provider profile")
    profile_delete.add_argument("profile_id")
    intelligence_profiles.add_argument("--json", action="store_true")
    intelligence_profiles.add_argument("--config-dir", default=argparse.SUPPRESS)

    intelligence_preview = intelligence_sub.add_parser(
        "preview", help="Show the exact transcript boundary before analysis"
    )
    intelligence_preview.add_argument("recording_dir")
    intelligence_preview.add_argument("--profile-id", default="")
    intelligence_preview.add_argument("--provider", choices=["ollama", "openai", "anthropic"],
                                      default="ollama")
    intelligence_preview.add_argument("--model", default="")
    intelligence_preview.add_argument("--api-url", default="")
    intelligence_preview.add_argument("--redact", action="store_true")
    intelligence_preview.add_argument("--json", action="store_true")
    intelligence_preview.add_argument("--config-dir", default=argparse.SUPPRESS)

    intelligence_summary = intelligence_sub.add_parser(
        "summary", help="Generate a local or explicitly consented cloud summary"
    )
    intelligence_summary.add_argument("recording_dir")
    intelligence_summary.add_argument("--profile-id", default="")
    intelligence_summary.add_argument("--provider", choices=["ollama", "openai", "anthropic"],
                                      default="ollama")
    intelligence_summary.add_argument("--model", default="")
    intelligence_summary.add_argument("--api-url", default="")
    intelligence_summary.add_argument("--redact", action="store_true")
    intelligence_summary.add_argument("--consent", action="store_true",
                                      help="Confirm the displayed cloud transcript payload")
    intelligence_summary.add_argument("--json", action="store_true")
    intelligence_summary.add_argument("--config-dir", default=argparse.SUPPRESS)

    intelligence_thumbnail = intelligence_sub.add_parser(
        "thumbnail", help="Generate a resource-bounded local smart thumbnail"
    )
    intelligence_thumbnail.add_argument("recording_dir")
    intelligence_thumbnail.add_argument("--title", default="")
    intelligence_thumbnail.add_argument("--channel", default="")
    intelligence_thumbnail.add_argument("--date", default="")
    intelligence_thumbnail.add_argument("--json", action="store_true")
    intelligence_thumbnail.add_argument("--config-dir", default=argparse.SUPPRESS)

    intelligence_jobs = intelligence_sub.add_parser("jobs", help="List analysis jobs")
    intelligence_jobs.add_argument("--kind", choices=["", "summary", "thumbnail"], default="")
    intelligence_jobs.add_argument("--json", action="store_true")
    intelligence_jobs.add_argument("--config-dir", default=argparse.SUPPRESS)

    intelligence_cancel = intelligence_sub.add_parser("cancel", help="Cancel an analysis job")
    intelligence_cancel.add_argument("job_id")
    intelligence_cancel.add_argument("--config-dir", default=argparse.SUPPRESS)

    intelligence_edit = intelligence_sub.add_parser("edit", help="Edit a saved summary")
    intelligence_edit.add_argument("job_id")
    intelligence_edit.add_argument("--text", default="")
    intelligence_edit.add_argument("--file", default="")
    intelligence_edit.add_argument("--json", action="store_true")
    intelligence_edit.add_argument("--config-dir", default=argparse.SUPPRESS)

    intelligence_rebuild = intelligence_sub.add_parser(
        "rebuild", help="Rebuild a saved summary with fresh consent if needed"
    )
    intelligence_rebuild.add_argument("job_id")
    intelligence_rebuild.add_argument("--consent-token", default="")
    intelligence_rebuild.add_argument("--json", action="store_true")
    intelligence_rebuild.add_argument("--config-dir", default=argparse.SUPPRESS)

    # -- packaged startup contract --
    startup_p = sub.add_parser(
        "startup-check",
        help="Run the offscreen packaged-startup readiness contract",
    )
    startup_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                           help="Required isolated config/database directory")
    startup_p.add_argument("--ready-file", required=True,
                           help="Path for the atomic machine-readable result")
    startup_p.add_argument(
        "--fixture",
        choices=["empty", "migrated", "populated"],
        default="empty",
        help="Isolated startup state to prepare",
    )

    # Legacy flat args for backward compat
    p.add_argument("--url", dest="legacy_url", default="",
                   help=argparse.SUPPRESS)
    p.add_argument("--server", dest="legacy_server", action="store_true",
                   help=argparse.SUPPRESS)
    p.add_argument("--list-extractors", dest="legacy_list", action="store_true",
                   help=argparse.SUPPRESS)

    return p


def run_cli(argv=None):
    """Parse args and dispatch to the appropriate handler."""
    source_argv = list(sys.argv[1:] if argv is None else argv)
    # A streamkeep:// URI (from the OS protocol handler) is translated into a
    # download of its validated target before argparse sees it.
    if source_argv:
        from .protocol import is_protocol_uri, parse_streamkeep_uri
        if is_protocol_uri(source_argv[0]):
            try:
                request = parse_streamkeep_uri(source_argv[0])
            except ValueError as error:
                _print_line(f"Error: {error}")
                sys.exit(2)
            rewritten = ["download", request["url"]]
            if request.get("quality"):
                rewritten += ["--quality", request["quality"]]
            argv = rewritten

    p = build_parser()
    args = p.parse_args(argv)

    config_dir = getattr(args, "config_dir", "") or ""
    if config_dir:
        from .paths import bind_config_dir
        bind_config_dir(config_dir)

    # Import only after the optional root override is bound so crash/config/
    # database modules all capture the same filesystem boundary.
    from .crash_log import setup_crash_logging
    setup_crash_logging()

    # Handle legacy flat args
    if args.legacy_list:
        _list_extractors()
        sys.exit(0)
    if args.legacy_server:
        args.command = "server"
        if not hasattr(args, "port"):
            args.port = 0
        if not hasattr(args, "bind"):
            args.bind = "127.0.0.1"
    if args.legacy_url:
        args.command = "download"
        args.url = args.legacy_url
        if not hasattr(args, "quality"):
            args.quality = "best"
        if not hasattr(args, "output"):
            args.output = ""
        if not hasattr(args, "rate_limit"):
            args.rate_limit = ""
        for name in (
            "format_spec", "format_sort", "format_sort_preset", "container",
            "audio_format", "audio_quality", "sub_langs", "convert_subs",
            "sub_delivery", "dub_lang",
        ):
            if not hasattr(args, name):
                setattr(args, name, "")
        if not hasattr(args, "auto_subs"):
            args.auto_subs = False
        if not hasattr(args, "mute"):
            args.mute = False
        for name, default in (
            ("concurrent_fragments", 0),
            ("retries", ""),
            ("fragment_retries", ""),
            ("retry_sleep", ""),
            ("unavailable_fragments", ""),
            ("throttled_rate", ""),
            ("live_from_start", False),
            ("wait_for_video", ""),
            ("embed_chapters", None),
            ("embed_metadata", None),
            ("embed_thumbnail", None),
        ):
            if not hasattr(args, name):
                setattr(args, name, default)

    if args.command in ("download", "dl"):
        _run_download(args)
    elif args.command == "server":
        _run_server(args)
    elif args.command == "extractors":
        _list_extractors()
    elif args.command == "plugins":
        _run_plugins(args)
    elif args.command == "operations":
        _run_operations(args)
    elif args.command == "gallery":
        _run_gallery(args)
    elif args.command == "capture":
        _run_capture(args)
    elif args.command == "lux":
        _run_lux(args)
    elif args.command == "db":
        _run_db_maintenance(args)
    elif args.command == "snapshot":
        _run_snapshot(args)
    elif args.command == "backup":
        _run_backup(args)
    elif args.command == "bagit":
        _run_bagit(args)
    elif args.command == "tokens":
        _run_tokens(args)
    elif args.command == "import-har":
        _run_har_import(args)
    elif args.command in ("import-library", "adopt"):
        _run_library_import(args)
    elif args.command == "retemplate":
        _run_retemplate(args)
    elif args.command == "mse-capture":
        _run_mse_capture(args)
    elif args.command == "podcast-sidecars":
        _run_podcast_sidecars(args)
    elif args.command == "credentials":
        _run_credentials_check(args)
    elif args.command == "auth":
        _run_auth(args, p)
    elif args.command == "youtube-health":
        _run_youtube_health(args)
    elif args.command == "intelligence":
        _run_intelligence(args)
    elif args.command == "register-protocol":
        _run_protocol_register(args)
    elif args.command == "unregister-protocol":
        _run_protocol_unregister(args)
    elif args.command == "bookmarklet":
        _run_bookmarklet(args)
    elif args.command == "startup-check":
        _run_startup_check(args)
    else:
        p.print_help()
        sys.exit(0)


def has_cli_args():
    """Return True if sys.argv contains CLI subcommands or legacy flags."""
    if len(sys.argv) <= 1:
        return False
    cli_triggers = {
        "download", "dl", "capture", "server", "extractors", "plugins", "operations", "gallery", "lux", "db",
        "snapshot", "backup", "bagit", "tokens", "startup-check", "import-har", "import-library",
        "retemplate",
        "adopt", "podcast-sidecars",
        "credentials", "auth", "youtube-health", "mse-capture", "register-protocol",
        "unregister-protocol", "bookmarklet", "intelligence",
        "--url", "--server", "--list-extractors", "--version", "--help", "-h",
    }
    if any(arg in cli_triggers for arg in sys.argv[1:]):
        return True
    # A streamkeep:// URI (from the OS protocol handler) is a headless action.
    from .protocol import is_protocol_uri
    return is_protocol_uri(sys.argv[1])
