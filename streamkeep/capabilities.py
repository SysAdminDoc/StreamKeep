"""Single runtime registry for security-gated modules and executables."""

from __future__ import annotations

import ast
import copy
import ctypes
import ctypes.util
import importlib.metadata
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .paths import _CREATE_NO_WINDOW
from .sqlite_runtime import runtime_status as sqlite_runtime_status


MINIMUM_VERSIONS = {
    "yt_dlp": "2026.07.04",
    "pillow": "12.3.0",
    "paramiko": "5.0.0",
    "python_mpv": "1.0.8",
    "boto3": "1.43.0",
    "libmpv": "0.41.0",
    "curl": "8.21.0",
    "ffmpeg": "8.1.2",
    "ffprobe": "8.1.2",
}

LIBMPV_ADVISORY = "GHSA-546v-22c3-7927"


@dataclass(frozen=True)
class ReachableProductPath:
    """A supported user entry point with an integration test that exercises it."""

    kind: str
    target: str
    test_nodeid: str


@dataclass(frozen=True)
class ProductCapabilityClaim:
    """One product capability's release-claim status and reachable paths."""

    id: str
    description: str
    status: str
    readme_token: str
    paths: tuple[ReachableProductPath, ...] = ()
    reason: str = ""


PRODUCT_CAPABILITY_CLAIMS = (
    ProductCapabilityClaim(
        "desktop-capture", "Desktop capture and queue workflow", "shipped",
        "Paste a supported URL",
        (ReachableProductPath(
            "gui", "Download",
            "tests/test_gui_smoke.py::test_main_window_tabs_dialogs_and_language_smoke",
        ),),
    ),
    ProductCapabilityClaim(
        "channel-monitor", "Desktop channel monitor", "shipped",
        "Monitor Kick and Twitch channels",
        (ReachableProductPath(
            "gui", "Monitor",
            "tests/test_gui_smoke.py::test_main_window_tabs_dialogs_and_language_smoke",
        ),),
    ),
    ProductCapabilityClaim(
        "archive-library", "History, storage, and archive inspection", "shipped",
        "Persist history, monitor entries, and queue state",
        (ReachableProductPath(
            "gui", "History",
            "tests/test_gui_smoke.py::test_main_window_tabs_dialogs_and_language_smoke",
        ),),
    ),
    ProductCapabilityClaim(
        "archive-maintenance", "Dry-run-first archive maintenance", "shipped",
        "Archive Maintenance",
        (ReachableProductPath(
            "gui", "Storage → Archive Maintenance",
            "tests/test_gui_smoke.py::test_main_window_tabs_dialogs_and_language_smoke",
        ),),
    ),
    ProductCapabilityClaim(
        "cli-download", "Headless download dispatch", "shipped",
        "StreamKeep.py download",
        (ReachableProductPath(
            "cli", "download",
            "tests/test_capability_reachability.py::test_download_cli_reaches_worker_dispatch",
        ),),
    ),
    ProductCapabilityClaim(
        "extractor-listing", "Extractor discovery from automation", "shipped",
        "StreamKeep.py extractors",
        (ReachableProductPath(
            "cli", "extractors",
            "tests/test_capability_reachability.py::test_extractor_cli_reaches_listing_dispatch",
        ),),
    ),
    ProductCapabilityClaim(
        "database-maintenance", "Headless database maintenance", "shipped",
        "StreamKeep.py db info",
        (ReachableProductPath(
            "cli", "db",
            "tests/test_cli.py::test_db_command_dispatches_headlessly_and_binds_config_root",
        ),),
    ),
    ProductCapabilityClaim(
        "diagnostic-snapshot", "Privacy-redacted diagnostic snapshot", "shipped",
        "StreamKeep.py snapshot",
        (ReachableProductPath(
            "cli", "snapshot",
            "tests/test_cli.py::test_snapshot_command_accepts_config_root_before_subcommand",
        ),),
    ),
    ProductCapabilityClaim(
        "backup", "Secret-free and encrypted-secret backup workflows", "shipped",
        "StreamKeep.py backup create",
        (ReachableProductPath(
            "cli", "backup",
            "tests/test_cli.py::test_backup_command_is_headless_and_secret_free",
        ),),
    ),
    ProductCapabilityClaim(
        "har-import", "HAR media-link import", "shipped",
        "StreamKeep.py import-har",
        (ReachableProductPath(
            "cli", "import-har",
            "tests/test_har.py::test_cli_import_har_prints_urls",
        ),),
    ),
    ProductCapabilityClaim(
        "podcast-sidecars", "Podcast transcript and chapter sidecars", "shipped",
        "StreamKeep.py podcast-sidecars",
        (ReachableProductPath(
            "cli", "podcast-sidecars",
            "tests/test_podcast_sidecars.py::test_cli_podcast_sidecars_downloads_and_reports",
        ),),
    ),
    ProductCapabilityClaim(
        "protocol-handoff", "streamkeep protocol and bookmarklet handoff", "shipped",
        "streamkeep://",
        (ReachableProductPath(
            "cli", "bookmarklet",
            "tests/test_protocol.py::test_cli_bookmarklet_command_prints_bookmarklet",
        ),),
    ),
    ProductCapabilityClaim(
        "durable-web-queue", "Authenticated durable web queue", "shipped",
        "POST /api/queue",
        (ReachableProductPath(
            "rest", "POST /api/queue",
            "tests/test_local_server.py::LocalServerTests::test_durable_queue_ack_is_observable_and_cancellable",
        ),),
    ),
    ProductCapabilityClaim(
        "failure-recovery", "Persisted failure retry and discard", "shipped",
        "/api/failures/retry",
        (ReachableProductPath(
            "rest", "POST /api/failures/retry",
            "tests/test_local_server.py::LocalServerTests::test_status_and_failure_actions_expose_retryable_jobs",
        ),),
    ),
    ProductCapabilityClaim(
        "browser-companion", "Scoped browser companion pairing", "shipped",
        "Send to Queue",
        (ReachableProductPath(
            "rest", "POST /pair",
            "tests/test_local_server.py::LocalServerTests::test_one_time_pairing_mints_origin_bound_scoped_token",
        ),),
    ),
    ProductCapabilityClaim(
        "packaged-startup", "Offscreen packaged startup contract", "shipped",
        "artifact suite exercises",
        (ReachableProductPath(
            "cli", "startup-check",
            "tests/test_artifact_startup.py::test_source_startup_contract_is_offscreen_and_isolated",
        ),),
    ),
    ProductCapabilityClaim(
        "gallery-publishing", "Authenticated local gallery publishing", "shipped",
        "Gallery/RSS publishing",
        (ReachableProductPath(
            "rest", "GET /gallery",
            "tests/test_local_server.py::LocalServerTests::test_authenticated_gallery_and_feed_routes",
        ),),
    ),
    ProductCapabilityClaim(
        "upload-delivery", "Secure upload and media-server delivery", "shipped",
        "Upload delivery",
        (ReachableProductPath(
            "rest", "POST /api/uploads",
            "tests/test_local_server.py::LocalServerTests::test_authenticated_upload_profiles_and_media_server_export",
        ),),
    ),
    ProductCapabilityClaim(
        "plugin-adapters", "Third-party plugin adapters", "shipped",
        "Plugin adapters",
        paths=(ReachableProductPath(
            "cli", "plugins",
            "tests/test_capability_reachability.py::test_plugins_cli_reaches_diagnostics_dispatch",
        ),),
    ),
    ProductCapabilityClaim(
        "operations-view", "Unified queue, monitor, and failure operations view", "shipped",
        "Operations view",
        (
            ReachableProductPath(
                "gui", "Operations",
                "tests/test_gui_smoke.py::test_main_window_tabs_dialogs_and_language_smoke",
            ),
            ReachableProductPath(
                "cli", "operations",
                "tests/test_capability_reachability.py::test_operations_cli_reaches_dispatch",
            ),
            ReachableProductPath(
                "rest", "GET /api/operations",
                "tests/test_local_server.py::LocalServerTests::test_authenticated_operations_view_actions_and_export",
            ),
        ),
    ),
    ProductCapabilityClaim(
        "llm-summaries", "Consent-aware local or cloud LLM summaries", "shipped",
        "LLM summaries",
        (ReachableProductPath(
            "cli", "intelligence",
            "tests/test_intelligence.py::test_summary_runtime_records_provider_and_supports_edit",
        ), ReachableProductPath(
            "rest", "POST /api/intelligence/summary",
            "tests/test_local_server.py::LocalServerTests::test_authenticated_intelligence_preview_and_summary",
        )),
    ),
    ProductCapabilityClaim(
        "smart-thumbnails", "Content-scored smart thumbnails", "shipped",
        "Smart thumbnails",
        (ReachableProductPath(
            "cli", "intelligence",
            "tests/test_intelligence.py::test_smart_thumbnail_preserves_original_and_enforces_limits",
        ),),
    ),
    ProductCapabilityClaim(
        "rss-publishing", "Recording RSS feed publishing", "shipped",
        "Gallery/RSS publishing",
        (ReachableProductPath(
            "rest", "GET /feed/{id}.xml",
            "tests/test_local_server.py::LocalServerTests::test_authenticated_gallery_and_feed_routes",
        ),),
    ),
    ProductCapabilityClaim(
        "native-notifications", "Native desktop notification adapter", "shipped",
        "Native notifications",
        (ReachableProductPath(
            "gui", "Notification events",
            "tests/test_native_notify.py::test_desktop_lifecycle_raises_a_native_toast",
        ),),
    ),
    ProductCapabilityClaim(
        "recording-notes", "Recording note authoring", "experimental",
        "Recording notes", reason="Note storage exists without a GUI, CLI, or REST editor.",
    ),
)


def get_product_capability_claims(*, status=None):
    """Return immutable release claims, optionally filtered by status."""
    if status is None:
        return PRODUCT_CAPABILITY_CLAIMS
    return tuple(claim for claim in PRODUCT_CAPABILITY_CLAIMS if claim.status == status)


def _test_node_exists(root, nodeid):
    parts = str(nodeid).split("::")
    path = Path(root) / parts[0]
    if not path.is_file() or len(parts) < 2:
        return False
    try:
        nodes = ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body
    except (OSError, SyntaxError, UnicodeError):
        return False
    for name in parts[1:]:
        match = next(
            (
                node for node in nodes
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ),
            None,
        )
        if match is None:
            return False
        nodes = getattr(match, "body", ())
    return True


def validate_product_capability_claims(root, *, claims=PRODUCT_CAPABILITY_CLAIMS):
    """Return release-gate errors for orphaned, untested, or undocumented claims."""
    root = Path(root)
    problems = []
    seen = set()
    try:
        readme = (root / "README.md").read_text(encoding="utf-8")
    except OSError as error:
        return [f"README.md could not be read: {error}"]

    from .cli import build_parser
    parser = build_parser()
    cli_paths = set()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            cli_paths.update(choices)
    try:
        from .local_server import PRODUCT_REST_PATHS
    except ImportError:
        PRODUCT_REST_PATHS = frozenset()

    for claim in claims:
        if claim.id in seen:
            problems.append(f"duplicate capability id: {claim.id}")
        seen.add(claim.id)
        if claim.status not in {"shipped", "experimental"}:
            problems.append(f"{claim.id}: unsupported status {claim.status!r}")
        if claim.readme_token not in readme:
            problems.append(f"{claim.id}: README token missing: {claim.readme_token!r}")
        if claim.status == "experimental":
            if claim.paths:
                problems.append(f"{claim.id}: experimental capability must not claim a path")
            if not claim.reason:
                problems.append(f"{claim.id}: experimental capability needs a reason")
            continue
        if not claim.paths:
            problems.append(f"{claim.id}: shipped capability has no reachable path")
            continue
        for path in claim.paths:
            if path.kind == "cli" and path.target not in cli_paths:
                problems.append(f"{claim.id}: CLI path {path.target!r} is not registered")
            elif path.kind == "rest" and path.target not in PRODUCT_REST_PATHS:
                problems.append(f"{claim.id}: REST path {path.target!r} is not registered")
            elif path.kind == "gui" and not path.target:
                problems.append(f"{claim.id}: GUI path is empty")
            elif path.kind not in {"cli", "rest", "gui"}:
                problems.append(f"{claim.id}: unsupported path kind {path.kind!r}")
            if not _test_node_exists(root, path.test_nodeid):
                problems.append(f"{claim.id}: integration test missing: {path.test_nodeid}")
    return problems

_CACHE = None
_CACHE_KEY = None
_CACHE_LOCK = threading.Lock()


class CapabilityUnavailableError(RuntimeError):
    """Raised before an unavailable or unsafe dependency can execute."""

    def __init__(self, record):
        self.record = copy.deepcopy(record)
        super().__init__(format_capability_problem(record))


def parse_version(value):
    """Return a numeric semantic/calendar version tuple from tool output."""
    match = re.search(r"(?<!\d)(\d+(?:[.\-]\d+){1,3})(?!\d)", str(value or ""))
    if not match:
        return ()
    try:
        return tuple(int(part) for part in match.group(1).replace("-", ".").split("."))
    except ValueError:
        return ()


def version_at_least(value, minimum):
    current = parse_version(value)
    required = parse_version(minimum)
    if not current or not required:
        return False
    length = max(len(current), len(required))
    return current + (0,) * (length - len(current)) >= required + (0,) * (
        length - len(required)
    )


def get_runtime_capabilities(*, refresh=False, config=None):
    """Return exact runtime identities, versions, provenance, and readiness."""
    global _CACHE, _CACHE_KEY
    from .javascript_runtime import read_runtime_preference

    whisper_model_path = str(
        (config or {}).get("whisper_model_path", "") or ""
    ).strip()
    cache_key = (read_runtime_preference(config), whisper_model_path)
    with _CACHE_LOCK:
        if _CACHE is None or refresh or _CACHE_KEY != cache_key:
            _CACHE = _probe_registry(
                preference=cache_key[0], whisper_model_path=cache_key[1],
            )
            _CACHE_KEY = cache_key
        return copy.deepcopy(_CACHE)


def invalidate_runtime_capabilities_cache():
    """Force the next capability read to re-probe managed runtimes."""
    global _CACHE, _CACHE_KEY
    with _CACHE_LOCK:
        _CACHE = None
        _CACHE_KEY = None


def get_capability(name, *, refresh=False, config=None):
    registry = get_runtime_capabilities(refresh=refresh, config=config)
    record = registry.get(str(name))
    if record is None:
        raise KeyError(f"unknown runtime capability: {name}")
    return record


def require_capability(name, *, refresh=False, config=None):
    record = get_capability(name, refresh=refresh, config=config)
    if not record.get("supported"):
        raise CapabilityUnavailableError(record)
    return record


def resolve_tool_command(name, *, refresh=False, config=None):
    """Return the exact supported executable path for a PATH-backed tool."""
    record = require_capability(name, refresh=refresh, config=config)
    command = record.get("command") or []
    if not command:
        raise CapabilityUnavailableError(record)
    return str(command[0])


def resolve_command_prefix(name, *, refresh=False, config=None):
    """Return an exact command prefix for a supported module/executable."""
    record = require_capability(name, refresh=refresh, config=config)
    command = record.get("command") or []
    if not command:
        raise CapabilityUnavailableError(record)
    return [str(part) for part in command]


def format_capability_problem(record):
    name = record.get("display_name") or record.get("name") or "Dependency"
    if not record.get("available"):
        reason = f"{name} was not found."
    else:
        version = record.get("version") or "unknown version"
        minimum = record.get("minimum") or "a supported release"
        maximum = record.get("maximum") or ""
        if record.get("name") == "yt_dlp_ejs" and minimum:
            reason = f"{name} {version} does not match yt-dlp requirement {minimum}."
        elif maximum and parse_version(version) > parse_version(maximum):
            reason = f"{name} {version} exceeds the supported maximum {maximum}."
        else:
            reason = f"{name} {version} is below the required minimum {minimum}."
    repair = str(record.get("repair") or "").strip()
    return f"{reason} {repair}".strip()


def capability_state(record):
    if record.get("supported"):
        return "ready"
    return "unsafe" if record.get("available") else "missing"


def _probe_registry(*, preference="path", whisper_model_path=""):
    sqlite = _probe_sqlite_runtime()
    yt_dlp = _probe_yt_dlp()
    pillow = _probe_module(
        "pillow", "Pillow", "PIL", MINIMUM_VERSIONS["pillow"],
        ["thumbnail-decode", "chat-render", "image-export"],
        "Install Pillow 12.3.0 or newer from the signed StreamKeep dependency set.",
    )
    paramiko = _probe_module(
        "paramiko", "Paramiko", "paramiko", MINIMUM_VERSIONS["paramiko"],
        ["sftp-upload"],
        "Install Paramiko 5.0.0 or newer from the signed StreamKeep dependency set.",
    )
    python_mpv = _probe_module(
        "python_mpv", "python-mpv", "mpv", MINIMUM_VERSIONS["python_mpv"],
        ["embedded-playback"],
        "Install the optional python-mpv 1.0.8 or newer extra.",
    )
    libmpv = _probe_libmpv()
    mpv = _aggregate_mpv(python_mpv, libmpv)
    boto3 = _probe_module(
        "boto3", "boto3", "boto3", MINIMUM_VERSIONS["boto3"],
        ["s3-upload"],
        "Install the optional boto3 1.43.0 or newer extra for S3 uploads.",
    )
    curl = _probe_executable(
        "curl", ["curl"], ["--version"], MINIMUM_VERSIONS["curl"],
        ["https-fetch", "range-download", "webhook"],
        "Install curl 8.21.0 or newer and ensure that executable is first in PATH.",
    )
    ffmpeg = _probe_executable(
        "ffmpeg", ["ffmpeg"], ["-version"], MINIMUM_VERSIONS["ffmpeg"],
        ["media-download", "decode", "transcode", "mux"],
        "Install FFmpeg 8.1.2 or newer and ensure that executable is first in PATH.",
    )
    ffmpeg_whisper = _probe_ffmpeg_whisper(ffmpeg, whisper_model_path)
    ffprobe = _probe_executable(
        "ffprobe", ["ffprobe"], ["-version"], MINIMUM_VERSIONS["ffprobe"],
        ["media-inspection", "duration-probe"],
        "Install the ffprobe 8.1.2 companion binary from the same FFmpeg build.",
    )
    ejs = _probe_ejs(yt_dlp)
    javascript = _probe_javascript_runtime(preference=preference)
    youtube = _aggregate_youtube(yt_dlp, ejs, javascript)
    return {
        "sqlite": sqlite,
        "yt_dlp": yt_dlp,
        "yt_dlp_ejs": ejs,
        "javascript": javascript,
        "youtube": youtube,
        "pillow": pillow,
        "paramiko": paramiko,
        "python_mpv": python_mpv,
        "libmpv": libmpv,
        "mpv": mpv,
        "boto3": boto3,
        "curl": curl,
        "ffmpeg": ffmpeg,
        "ffmpeg_whisper": ffmpeg_whisper,
        "ffprobe": ffprobe,
    }


def _probe_sqlite_runtime():
    status = sqlite_runtime_status()
    record = _base_record(
        "sqlite", "SQLite", "python-runtime", status["minimum"],
        ["library-database", "backup", "search", "queue"],
        status.get(
            "repair",
            "Use a StreamKeep build bundled with a fixed SQLite runtime.",
        ),
        path=sys.executable,
        version=status["version"],
        available=True,
        supported=status["supported"],
        provenance="bundled" if status["frozen"] else "python-runtime",
        detail=status["detail"],
    )
    record.update({
        "wal_reset_fixed": status["wal_reset_fixed"],
        "degraded": status["degraded"],
        "journal_mode": status["journal_mode"],
        "fts5_fixed": status.get("fts5_fixed", True),
        "fts5_supported": status.get("fts5_supported", True),
        "fts5_degraded": status.get("fts5_degraded", False),
        "fts5_minimum": status.get("fts5_minimum", ""),
    })
    if status["degraded"]:
        record["state"] = "degraded"
    return record


def _base_record(
    name, display_name, kind, minimum, capabilities, repair,
    *, path="", version="", available=False, supported=False, command=None,
    provenance="missing", detail="",
):
    record = {
        "name": name,
        "display_name": display_name,
        "kind": kind,
        "path": str(path or ""),
        "version": str(version or ""),
        "minimum": str(minimum or ""),
        "provenance": provenance,
        "available": bool(available),
        "supported": bool(supported),
        "capabilities": list(capabilities),
        "command": list(command or []),
        "repair": repair,
        "detail": detail,
    }
    record["state"] = capability_state(record)
    if not record["detail"]:
        record["detail"] = (
            f"{display_name} {record['version']} at {record['path']}"
            if record["supported"] else format_capability_problem(record)
        )
    return record


def _probe_module(name, distribution, module, minimum, capabilities, repair):
    spec = None
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, AttributeError, ValueError):
        pass
    path = str(getattr(spec, "origin", "") or "") if spec else ""
    try:
        version = importlib.metadata.version(distribution) if spec else ""
    except importlib.metadata.PackageNotFoundError:
        version = ""
    available = bool(spec and path and version)
    return _base_record(
        name, distribution, "python-module", minimum, capabilities, repair,
        path=path, version=version, available=available,
        supported=available and version_at_least(version, minimum),
        provenance=_path_provenance(path, module=True) if available else "missing",
    )


def _probe_libmpv():
    """Load libmpv without importing the optional Python wrapper.

    The native runtime owns the security-relevant release version.  The
    python-mpv module only exposes its API version at import time, and older
    libraries can make that import raise before the player has a chance to
    report a useful diagnostic.  A no-video/no-audio client reads the native
    ``mpv-version`` property and is destroyed before returning.
    """
    minimum = MINIMUM_VERSIONS["libmpv"]
    repair = (
        f"Install libmpv {minimum} or newer; versions below {minimum} are "
        f"affected by {LIBMPV_ADVISORY}."
    )
    library, path, load_detail = _load_libmpv()
    if library is None:
        record = _base_record(
            "libmpv", "libmpv", "native-library", minimum,
            ["embedded-playback"], repair,
            provenance="missing", detail=load_detail or "libmpv was not found.",
        )
        record["advisory"] = LIBMPV_ADVISORY
        return record

    try:
        version, api_version = _read_libmpv_version(library)
    except Exception as error:
        # Capability discovery must fail closed without making an optional
        # native runtime prevent the rest of the application from starting.
        version = ""
        api_version = ""
        detail = f"libmpv loaded but its version could not be read: {error}"
    else:
        detail = f"libmpv {version} (client API {api_version}) loaded from {path}"

    supported = bool(version) and version_at_least(version, minimum)
    record = _base_record(
        "libmpv", "libmpv", "native-library", minimum,
        ["embedded-playback"], repair,
        path=path, version=version, available=True, supported=supported,
        provenance=_path_provenance(path), detail=detail,
    )
    record["api_version"] = api_version
    record["advisory"] = LIBMPV_ADVISORY
    if not supported:
        record["state"] = "degraded"
        record["detail"] = (
            f"libmpv {version or 'unknown'} is below the required {minimum}; "
            f"upgrade before embedded playback ({LIBMPV_ADVISORY})."
        )
    return record


def _load_libmpv():
    """Return ``(library, path, detail)`` for the first loadable libmpv."""
    if os.name == "nt":
        names = ("mpv-2.dll", "libmpv-2.dll", "mpv-1.dll")
    elif sys.platform == "darwin":
        names = ("mpv", "libmpv.dylib")
    else:
        names = ("mpv", "libmpv.so.2", "libmpv.so")

    candidates = []
    for name in names:
        try:
            found = ctypes.util.find_library(name)
        except (OSError, TypeError):
            found = None
        if found:
            candidates.append(str(found))

    try:
        spec = importlib.util.find_spec("mpv")
    except (ImportError, AttributeError, ValueError):
        spec = None
    module_path = str(getattr(spec, "origin", "") or "") if spec else ""
    if module_path:
        module_dir = Path(module_path).parent
        candidates.extend(str(module_dir / name) for name in names)

    seen = set()
    last_error = ""
    for candidate in candidates:
        key = os.path.normcase(candidate)
        if key in seen:
            continue
        seen.add(key)
        try:
            return ctypes.CDLL(candidate), candidate, ""
        except OSError as error:
            last_error = str(error)

    if last_error:
        return None, "", f"libmpv could not be loaded: {last_error}"
    return None, "", "libmpv was not found."


def _read_libmpv_version(library):
    """Read the native release and client API versions from a loaded library."""
    api = library.mpv_client_api_version
    api.restype = ctypes.c_ulong
    api_value = int(api())
    api_version = f"{api_value >> 16}.{api_value & 0xFFFF}"

    create = library.mpv_create
    create.restype = ctypes.c_void_p
    handle = create()
    if not handle:
        raise RuntimeError("mpv_create returned a null handle")

    initialized = False
    try:
        set_option = getattr(library, "mpv_set_option_string", None)
        if set_option is not None:
            set_option.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
            set_option.restype = ctypes.c_int
            for name, value in ((b"config", b"no"), (b"vo", b"null"), (b"ao", b"null")):
                set_option(handle, name, value)

        initialize = library.mpv_initialize
        initialize.argtypes = [ctypes.c_void_p]
        initialize.restype = ctypes.c_int
        result = int(initialize(handle))
        if result != 0:
            raise RuntimeError(f"mpv_initialize returned {result}")
        initialized = True

        get_property = library.mpv_get_property_string
        get_property.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        get_property.restype = ctypes.c_void_p
        pointer = get_property(handle, b"mpv-version")
        if not pointer:
            raise RuntimeError("mpv-version property was empty")
        try:
            value = ctypes.cast(pointer, ctypes.c_char_p).value
            if not value:
                raise RuntimeError("mpv-version property was empty")
            version = value.decode("utf-8", errors="replace").strip()
        finally:
            free = library.mpv_free
            free.argtypes = [ctypes.c_void_p]
            free(pointer)
        return version, api_version
    finally:
        destroy_name = "mpv_terminate_destroy" if initialized else "mpv_destroy"
        destroy = getattr(library, destroy_name, None)
        if destroy is not None:
            destroy.argtypes = [ctypes.c_void_p]
            destroy(handle)


def _aggregate_mpv(python_mpv, libmpv):
    """Expose one player capability while retaining wrapper/native evidence."""
    supported = bool(python_mpv.get("supported") and libmpv.get("supported"))
    available = bool(python_mpv.get("available") and libmpv.get("available"))
    repair = " ".join(
        item.get("repair", "") for item in (python_mpv, libmpv) if item.get("repair")
    )
    problems = " ".join(
        format_capability_problem(item)
        for item in (python_mpv, libmpv)
        if not item.get("supported")
    )
    record = _base_record(
        "mpv", "Embedded mpv player", "aggregate", MINIMUM_VERSIONS["libmpv"],
        ["embedded-playback"], repair,
        path=libmpv.get("path", ""), version=libmpv.get("version", ""),
        available=available, supported=supported,
        provenance="deterministic-local-components",
        detail=(
            f"python-mpv {python_mpv.get('version')} with libmpv "
            f"{libmpv.get('version')} at {libmpv.get('path')}"
            if supported else problems
        ),
    )
    record.update({
        "python_mpv": python_mpv,
        "libmpv": libmpv,
        "advisory": LIBMPV_ADVISORY,
    })
    if libmpv.get("state") == "degraded":
        record["state"] = "degraded"
    return record


def _probe_yt_dlp():
    minimum = MINIMUM_VERSIONS["yt_dlp"]
    repair = (
        'Install or update the signed dependency set with '
        '"yt-dlp[default]>=2026.07.04".'
    )
    module_record = _probe_module(
        "yt_dlp", "yt-dlp", "yt_dlp", minimum,
        ["site-extraction", "direct-download", "youtube"], repair,
    )
    if module_record.get("available"):
        module_record["command"] = (
            [sys.executable, "--internal-ytdlp"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "yt_dlp"]
        )
        return module_record
    external = _probe_executable(
        "yt_dlp", ["yt-dlp"], ["--version"], minimum,
        ["site-extraction", "direct-download", "youtube"], repair,
        display_name="yt-dlp",
    )
    return external


def _probe_ejs(yt_dlp_record):
    record = _probe_module(
        "yt_dlp_ejs", "yt-dlp-ejs", "yt_dlp_ejs", "",
        ["youtube-js-challenge-scripts"],
        'Install the matching version through pip install -U "yt-dlp[default]".',
    )
    required = _yt_dlp_ejs_requirement() if yt_dlp_record.get("available") else ""
    compatible = bool(record.get("available"))
    if compatible and required:
        compatible = _version_matches_specifier(record.get("version", ""), required)
    record["minimum"] = required
    record["required_by_ytdlp"] = required
    record["supported"] = compatible
    record["state"] = capability_state(record)
    if compatible:
        record["detail"] = (
            f"yt-dlp-ejs {record['version']} matches yt-dlp requirement "
            f"{required or '(unspecified)'} at {record['path']}"
        )
    else:
        record["detail"] = format_capability_problem(record)
    return record


def _yt_dlp_ejs_requirement():
    try:
        for requirement in importlib.metadata.requires("yt-dlp") or []:
            if requirement.lower().startswith("yt-dlp-ejs"):
                base = requirement.split(";", 1)[0].strip()
                return base[len("yt-dlp-ejs"):].strip()
    except importlib.metadata.PackageNotFoundError:
        pass
    return ""


def _version_matches_specifier(version, specifier):
    if not specifier:
        return True
    clauses = [part.strip() for part in specifier.split(",") if part.strip()]
    current = parse_version(version)
    if not current or not clauses:
        return False
    for clause in clauses:
        match = re.fullmatch(r"(===|==|!=|>=|<=|>|<)\s*([0-9][0-9.\-]*)", clause)
        if not match:
            return False
        operator, required_text = match.groups()
        required = parse_version(required_text)
        if not required:
            return False
        length = max(len(current), len(required))
        left = current + (0,) * (length - len(current))
        right = required + (0,) * (length - len(required))
        matched = {
            "===": left == right,
            "==": left == right,
            "!=": left != right,
            ">=": left >= right,
            "<=": left <= right,
            ">": left > right,
            "<": left < right,
        }[operator]
        if not matched:
            return False
    return True


def _probe_executable(
    name, candidates, version_args, minimum, capabilities, repair,
    *, display_name=None,
):
    for candidate in candidates:
        path = shutil.which(candidate)
        if not path:
            continue
        path = str(Path(path).resolve())
        output, returncode = _run_version_command(path, version_args)
        version = ".".join(str(part) for part in parse_version(output))
        available = returncode == 0 and bool(version)
        return _base_record(
            name, display_name or name, "executable", minimum, capabilities, repair,
            path=path, version=version, available=available,
            supported=available and version_at_least(version, minimum),
            command=[path], provenance=_path_provenance(path),
            detail=output.splitlines()[0][:240] if output else "",
        )
    return _base_record(
        name, display_name or name, "executable", minimum, capabilities, repair,
    )


def _probe_ffmpeg_whisper(ffmpeg, model_path):
    """Probe FFmpeg's optional whisper filter without starting a job."""
    minimum = MINIMUM_VERSIONS["ffmpeg"]
    repair = (
        f"Install FFmpeg {minimum} or newer with the whisper filter and "
        "configure a local whisper.cpp model file in Settings."
    )
    ffmpeg_path = str(ffmpeg.get("path") or "")
    command = list(ffmpeg.get("command") or [])
    model_path = str(model_path or "").strip()
    model_path = os.path.abspath(os.path.expanduser(model_path)) if model_path else ""
    available = bool(ffmpeg.get("available"))
    filter_available = False
    supported = False
    detail = ""
    filter_output = ""

    if not available:
        detail = "The resolved FFmpeg executable is unavailable."
    elif not ffmpeg.get("supported"):
        detail = (
            f"FFmpeg {ffmpeg.get('version') or 'unknown'} does not meet the "
            f"{minimum} whisper-filter floor."
        )
    else:
        filter_output, returncode = _run_capture_command(
            ffmpeg_path, ["-hide_banner", "-filters"], timeout=5,
        )
        if returncode != 0:
            detail = "Could not inspect the resolved FFmpeg filter registry."
        elif not _filter_listing_contains(filter_output, "whisper"):
            detail = "The resolved FFmpeg build does not expose the whisper filter."
        else:
            filter_available = True
            if not model_path:
                detail = "Configure a local whisper.cpp model path in Settings."
            elif not Path(model_path).is_file():
                detail = f"Configured Whisper model was not found: {model_path}"
            else:
                supported = True
                detail = f"whisper filter ready with model {model_path}"

    if ffmpeg.get("supported") and not filter_available:
        available = False

    record = _base_record(
        "ffmpeg_whisper", "FFmpeg whisper filter", "ffmpeg-filter", minimum,
        ["transcription", "transcript-sidecars"], repair,
        path=ffmpeg_path,
        version=ffmpeg.get("version", ""),
        available=available,
        supported=supported,
        command=command,
        provenance=ffmpeg.get("provenance", "missing"),
        detail=detail,
    )
    record.update({
        "filter": "whisper",
        "filter_available": filter_available,
        "model_path": model_path,
        "ffmpeg_path": ffmpeg_path,
    })
    return record


def _filter_listing_contains(output, filter_name):
    """Match a filter name column, not a description mentioning the name."""
    expected = str(filter_name)
    for line in str(output or "").splitlines():
        fields = line.strip().split()
        if len(fields) >= 3 and fields[1] == expected:
            return True
    return False


def _run_capture_command(path, args, *, timeout=5):
    try:
        result = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", creationflags=_CREATE_NO_WINDOW,
        )
        output = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        return output, int(result.returncode)
    except (OSError, subprocess.SubprocessError, ValueError):
        return "", -1


def _run_version_command(path, args):
    return _run_capture_command(path, args, timeout=5)


def _probe_managed_javascript_runtime():
    from .javascript_runtime import (
        DENO_VERSION,
        DENO_MINIMUM_VERSION,
        bundled_deno_path,
        get_managed_deno_info,
    )

    bundled = bundled_deno_path()
    managed = get_managed_deno_info() if not bundled else {}
    info = managed or {
        "available": bool(bundled),
        "path": bundled,
        "version": "",
        "provenance": "bundled" if bundled else "",
        "source": "bundled" if bundled else "",
        "asset": "",
        "sha256": "",
        "detail": "No bundled or managed Deno runtime is installed.",
    }
    path = str(info.get("path") or "") if info.get("available") else ""
    if not path:
        record = _base_record(
            "javascript", "Deno", "managed-executable", DENO_MINIMUM_VERSION,
            ["youtube-js-runtime"],
            "Install the pinned Deno runtime from StreamKeep Settings or "
            "`python StreamKeep.py youtube-health --install-deno`.",
            provenance="missing",
            detail=info.get("detail", ""),
        )
        record.update({
            "runtime": "deno",
            "managed": True,
            "runtime_source": "",
        })
        return record
    output, returncode = _run_version_command(path, ["--version"])
    version = ".".join(str(part) for part in parse_version(output))
    available = returncode == 0 and bool(version)
    source = str(info.get("source") or info.get("provenance") or "managed")
    record = _base_record(
        "javascript", "Deno", "managed-executable", DENO_MINIMUM_VERSION,
        ["youtube-js-runtime"],
        "Reinstall the pinned Deno runtime from StreamKeep Settings or "
        "`python StreamKeep.py youtube-health --install-deno`.",
        path=path,
        version=version,
        available=available,
        supported=(
            available
            and version == DENO_VERSION
            and version_at_least(version, DENO_MINIMUM_VERSION)
        ),
        command=[path],
        provenance=source,
        detail=output.splitlines()[0][:240] if output else info.get("detail", ""),
    )
    record.update({
        "runtime": "deno",
        "managed": True,
        "runtime_source": source,
        "asset": str(info.get("asset") or ""),
        "sha256": str(info.get("sha256") or ""),
    })
    return record


def _decorate_javascript_record(record, runtime):
    record["runtime"] = runtime
    record["managed"] = False
    record["runtime_source"] = "user-supplied"
    return record


def _probe_javascript_runtime(*, preference="path"):
    candidates = [
        ("deno", ["deno"], "2.3.0", ""),
        ("node", ["node", "nodejs"], "22.0.0", ""),
        ("quickjs", ["qjs"], "2023.12.9", ""),
        ("bun", ["bun"], "1.2.11", "1.3.14"),
    ]
    first_unsafe = None

    def consider(record, name, maximum=""):
        nonlocal first_unsafe
        if record.get("managed"):
            record["runtime"] = name
        else:
            record = _decorate_javascript_record(record, name)
        record["maximum"] = maximum
        if maximum and parse_version(record.get("version")) > parse_version(maximum):
            record["supported"] = False
            record["state"] = "unsafe"
            record["detail"] = (
                f"{name} {record['version']} exceeds the supported maximum {maximum}."
            )
        if record.get("supported"):
            return record
        if first_unsafe is None and record.get("available"):
            first_unsafe = record
        return None

    def probe_path_runtimes():
        for name, commands, minimum, maximum in candidates:
            record = _probe_executable(
                "javascript", commands, ["--version"], minimum,
                ["youtube-js-runtime"],
                "Install Deno 2.3+ (recommended) or Node.js 22+ and add it to PATH.",
                display_name=name,
            )
            selected = consider(record, name, maximum)
            if selected is not None:
                return selected
        return None

    if preference == "managed":
        selected = consider(_probe_managed_javascript_runtime(), "deno")
        if selected is not None:
            return selected
        selected = probe_path_runtimes()
        if selected is not None:
            return selected
    else:
        selected = probe_path_runtimes()
        if selected is not None:
            return selected
        selected = consider(_probe_managed_javascript_runtime(), "deno")
        if selected is not None:
            return selected

    if first_unsafe:
        return first_unsafe
    missing = _base_record(
        "javascript", "JavaScript runtime", "executable", "Deno 2.3 / Node 22",
        ["youtube-js-runtime"],
        "Install Deno 2.3+ (recommended) or Node.js 22+ and add it to PATH.",
    )
    missing["runtime"] = ""
    missing["maximum"] = ""
    missing["managed"] = False
    missing["runtime_source"] = ""
    return missing


def _aggregate_youtube(yt_dlp, ejs, javascript):
    components = [yt_dlp, ejs, javascript]
    supported = all(item.get("supported") for item in components)
    problems = [format_capability_problem(item) for item in components if not item.get("supported")]
    paths = [item.get("path", "") for item in components if item.get("path")]
    versions = [
        f"{item.get('display_name')} {item.get('version')}"
        for item in components if item.get("version")
    ]
    return _base_record(
        "youtube", "YouTube support", "aggregate", "", ["full-youtube"],
        " ".join(item.get("repair", "") for item in components if not item.get("supported")),
        path="; ".join(paths), version=" + ".join(versions), available=bool(yt_dlp.get("available")),
        supported=supported, provenance="deterministic-local-components",
        detail=(
            "Full YouTube support uses local matching yt-dlp/EJS and the exact "
            f"{javascript.get('runtime') or 'JavaScript'} executable."
            if supported else " ".join(problems)
        ),
    )


def _path_provenance(path, *, module=False):
    if not path:
        return "missing"
    resolved = os.path.normcase(os.path.abspath(path))
    bundle_root = str(getattr(sys, "_MEIPASS", "") or "")
    if getattr(sys, "frozen", False) and bundle_root:
        try:
            if os.path.commonpath([resolved, os.path.normcase(os.path.abspath(bundle_root))]) == os.path.normcase(os.path.abspath(bundle_root)):
                return "bundled"
        except ValueError:
            pass
    if module:
        return "bundled" if getattr(sys, "frozen", False) else "python-environment"
    system_root = os.path.normcase(os.environ.get("SystemRoot", ""))
    if system_root and resolved.startswith(system_root + os.sep):
        return "operating-system"
    return "PATH"
