"""CLI / headless mode for StreamKeep (F42).

Provides ``--url``, ``--server``, and ``--list-extractors`` subcommands.
Uses QCoreApplication (no display server required) so existing QThread-based
workers and pyqtSignal infrastructure work without modification.

Usage::

    python StreamKeep.py --url URL [--quality best|1080p|720p|...] [--output DIR]
    python StreamKeep.py --server [--port PORT] [--trusted-proxy-origin HTTPS_ORIGIN]
    python StreamKeep.py --list-extractors
"""

import argparse
import os
import sys

# QCoreApplication drives the event loop without requiring a display
# server.  This lets us reuse QThread workers and pyqtSignal infra.
from PyQt6.QtCore import QCoreApplication

from . import VERSION
from .capabilities import CapabilityUnavailableError, require_capability
from .extractors.base import Extractor as _ExtBase


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
        pass


# ── --url handler ───────────────────────────────────────────────────

def _run_download(args):
    """Resolve *args.url* and download it."""
    if not _check_ffmpeg():
        try:
            require_capability("ffmpeg")
        except CapabilityUnavailableError as error:
            print(f"Error: {error}")
        sys.exit(1)

    from .download_options import validate_download_options
    requested_ytdlp_output = any((
        getattr(args, "format_spec", ""),
        getattr(args, "format_sort", ""),
        getattr(args, "format_sort_preset", ""),
        getattr(args, "container", ""),
        getattr(args, "audio_format", ""),
        getattr(args, "audio_quality", ""),
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
        )
    except ValueError as error:
        _print_line(f"Error: {error}")
        sys.exit(2)

    from . import db
    from .config import install_file_logging, load_config, write_log_line

    app = QCoreApplication(sys.argv)
    db.init_db()
    install_file_logging()

    from .workers import FetchWorker, DownloadWorker

    cfg = load_config()
    output_dir = args.output or cfg.get("output_dir", "")
    if not output_dir:
        from .utils import default_output_dir
        output_dir = default_output_dir()

    quality_pref = (args.quality or "best").lower()

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
        state["info"] = info
        state["phase"] = "download"
        _print_line(
            f"Resolved: {info.platform} / {info.channel} / {info.title}"
        )
        _print_line(
            f"Duration: {info.duration_str or 'live'}  |  "
            f"Qualities: {len(info.qualities)}"
        )
        if not info.qualities:
            _print_line("Error: No downloadable qualities found.")
            _record_cli_failure(args.url, "fetch", "No downloadable qualities found", output_dir, info)
            state["exit_code"] = 1
            app.quit()
            return

        # Pick quality
        qi = _pick_quality(info.qualities, quality_pref)
        _print_line(f"Selected: {qi.name} ({qi.resolution or qi.format_type})")
        _print_line("")
        if requested_ytdlp_output and qi.format_type != "ytdlp_direct":
            message = (
                "Format/container/audio controls require a yt-dlp direct "
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
        from .utils import safe_filename
        label = safe_filename(info.title or info.channel or "stream")
        segments = [(0, label, 0, info.total_secs)]

        # Start download
        dw = DownloadWorker(qi.url, segments, output_dir, qi.format_type)
        dw.audio_url = qi.audio_url
        dw.ytdlp_source = qi.ytdlp_source
        dw.ytdlp_format = (
            output_options["format_spec"]
            or ("bestaudio/best" if output_options["audio_format"] else qi.ytdlp_format)
        )
        dw.ytdlp_format_sort = output_options["format_sort"]
        dw.ytdlp_container = output_options["container"]
        dw.ytdlp_audio_format = output_options["audio_format"]
        dw.ytdlp_audio_quality = output_options["audio_quality"]
        if args.rate_limit:
            dw.rate_limit = args.rate_limit
        state["dw"] = dw  # prevent GC while event loop runs

        dw.progress.connect(lambda si, pct, txt: _print_progress(
            f"[{pct:3d}%] {txt}"
        ))
        dw.log.connect(lambda msg: write_log_line(msg))
        def on_download_error(_si, msg):
            _print_line(f"Error: {msg}")
            state["exit_code"] = 1
            _record_cli_failure(args.url, "download", msg, output_dir, state.get("info"))

        dw.error.connect(on_download_error)
        dw.segment_done.connect(lambda si, path: _print_line(
            f"  segment {si} done"
        ))
        dw.all_done.connect(lambda: _on_download_done(state, app, output_dir))
        dw.finished.connect(lambda: app.quit() if state.get("exit_code") else None)
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


def _on_download_done(state, app, output_dir):
    _print_progress("")
    _print_line(f"\nDownload complete -> {output_dir}")
    from . import db
    db.mark_failed_jobs_resolved_for_url(state.get("source_url", ""))
    try:
        from .verify import create_archive_manifest
        manifest = create_archive_manifest(output_dir, write_sidecar=True)
        _print_line(
            "Integrity manifest -> "
            f"{len(manifest.get('files', []) or [])} file(s)"
        )
    except Exception as e:
        _print_line(f"Warning: integrity manifest was not created: {e}")
    app.quit()


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

    from . import db
    app = QCoreApplication(sys.argv)
    db.init_db()
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
    server.queue_submitter = service.enqueue
    server.job_canceller = service.cancel
    server.failure_retrier = service.retry_failure
    server.failure_discarder = service.discard_failure

    server.url_received.connect(
        lambda url, action: _print_line(f"[{action}] {url}")
    )
    recovered = service.start()
    server.start()

    _print_line(f"StreamKeep v{VERSION} — server mode")
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
    _print_line(f"StreamKeep v{VERSION} — supported platforms:")
    _print_line("")
    for cls in _ExtBase._registry:
        patterns = ", ".join(
            getattr(p, "pattern", str(p)) for p in cls.URL_PATTERNS[:3]
        )
        _print_line(f"  {cls.NAME:<16s}  {patterns}")
    _print_line(f"\n  ({len(_ExtBase._registry)} extractors registered)")


def _run_snapshot(args):
    """Export a privacy-redacted diagnostic snapshot."""
    from .diagnostics import create_diagnostic_snapshot
    from datetime import datetime
    out = args.output or f"streamkeep_diag_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    ok, msg = create_diagnostic_snapshot(out)
    _print_line(f"Snapshot: {'OK' if ok else 'FAILED'} — {msg}")
    if ok:
        _print_line(f"File: {out}")
    else:
        sys.exit(1)


def _run_db_maintenance(args):
    """Run a database maintenance action."""
    import json as _json
    from . import db
    db.init_db()
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
        _print_line(f"WAL checkpoint: {'OK' if ok else 'FAILED'} — {detail}")
        if not ok:
            sys.exit(1)
    elif action == "vacuum":
        ok, detail = db.vacuum_after_backup()
        _print_line(f"Vacuum: {'OK' if ok else 'FAILED'} — {detail}")
        if not ok:
            sys.exit(1)


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


# ── Entry point ─────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(
        prog="StreamKeep",
        description=f"StreamKeep v{VERSION} — multi-platform stream/VOD downloader",
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
    dl.add_argument("--config-dir", default=argparse.SUPPRESS,
                    help="Override the config/database directory")

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
    ext_p.add_argument("--config-dir", default=argparse.SUPPRESS,
                       help="Override the config/database directory")

    # -- db maintenance --
    db_p = sub.add_parser("db", help="Database maintenance and diagnostics")
    db_p.add_argument("action", nargs="?", default="info",
                      choices=["info", "check", "optimize", "checkpoint", "vacuum"],
                      help="Action: info (default), check, optimize, checkpoint, vacuum")
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
            "audio_format", "audio_quality",
        ):
            if not hasattr(args, name):
                setattr(args, name, "")

    if args.command in ("download", "dl"):
        _run_download(args)
    elif args.command == "server":
        _run_server(args)
    elif args.command == "extractors":
        _list_extractors()
    elif args.command == "db":
        _run_db_maintenance(args)
    elif args.command == "snapshot":
        _run_snapshot(args)
    elif args.command == "backup":
        _run_backup(args)
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
        "download", "dl", "server", "extractors", "db", "snapshot", "backup",
        "startup-check",
        "--url", "--server", "--list-extractors", "--version", "--help", "-h",
    }
    return any(arg in cli_triggers for arg in sys.argv[1:])
