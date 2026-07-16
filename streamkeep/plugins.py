"""Plugin / Extension SDK — load community plugins from a directory (F77).

Plugins are Python packages or modules in ``%APPDATA%/StreamKeep/plugins/``
(or ``data/plugins/`` in portable mode). Each plugin declares metadata in
a ``plugin.json`` manifest:

    {
        "id": "my-extractor",
        "name": "My Custom Extractor",
        "version": "1.0.0",
        "author": "User",
        "description": "Adds support for example.com",
        "min_app_version": "4.0.0",
        "manifest_version": 1,
        "enabled": true
    }

Required manifest fields: ``id``, ``name``, ``version``.
Optional: ``manifest_version`` (int, currently 1), ``min_app_version``
(semver string — plugin is skipped if the running app version is older).

Plugin types (detected by what the module imports/subclasses):
  - Custom extractors: subclass ``streamkeep.extractors.base.Extractor``
  - Post-processing filters: hook into PostProcessor pipeline
  - Upload destinations: subclass ``streamkeep.upload.base.UploadDestination``

Plugins run in the same process — no sandbox. Trust-based, like yt-dlp.
"""

import contextlib
import importlib.util
import json
import os
import re
import sys

from . import VERSION
from .paths import CONFIG_DIR

CURRENT_MANIFEST_VERSION = 1
_REQUIRED_FIELDS = ("id", "name", "version")
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")

PLUGINS_DIR = CONFIG_DIR / "plugins"


def _ensure_dir():
    """Create the plugins directory if it doesn't exist."""
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)


def _parse_semver(version_str):
    """Parse a version string into a (major, minor, patch) tuple or None."""
    m = _SEMVER_RE.match(str(version_str or ""))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def validate_manifest(meta, entry_name=""):
    """Validate a plugin manifest dict. Returns a list of error strings."""
    errors = []
    if not isinstance(meta, dict):
        return ["Manifest is not a JSON object"]
    for field in _REQUIRED_FIELDS:
        if not meta.get(field):
            errors.append(f"Missing required field: {field}")
    if meta.get("version") and _parse_semver(meta["version"]) is None:
        errors.append(f"Invalid version format: {meta['version']!r} (expected X.Y.Z)")
    mv = meta.get("manifest_version")
    if mv is not None:
        try:
            mv_int = int(mv)
        except (TypeError, ValueError):
            errors.append(f"Invalid manifest_version: {mv!r}")
            mv_int = -1
        if mv_int > CURRENT_MANIFEST_VERSION:
            errors.append(
                f"Unsupported manifest_version {mv_int} "
                f"(app supports up to {CURRENT_MANIFEST_VERSION})"
            )
    min_ver = meta.get("min_app_version", "")
    if min_ver:
        required = _parse_semver(min_ver)
        current = _parse_semver(VERSION)
        if required and current and required > current:
            errors.append(
                f"Requires StreamKeep >= {min_ver} (running {VERSION})"
            )
    return errors


def discover_plugins():
    """Scan the plugins directory and return metadata for each plugin.

    Returns list of dicts: ``[{id, name, version, author, description,
    enabled, path, error}, ...]``
    """
    _ensure_dir()
    plugins = []
    for entry in sorted(os.listdir(str(PLUGINS_DIR))):
        plugin_path = PLUGINS_DIR / entry
        manifest_path = plugin_path / "plugin.json"

        if not plugin_path.is_dir() or not manifest_path.is_file():
            continue

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            plugins.append({
                "id": entry, "name": entry, "version": "?",
                "author": "", "description": "",
                "enabled": False, "path": str(plugin_path),
                "error": f"Invalid plugin.json: {e}",
            })
            continue

        validation_errors = validate_manifest(meta, entry)
        error_msg = "; ".join(validation_errors) if validation_errors else ""
        plugins.append({
            "id": meta.get("id", entry),
            "name": meta.get("name", entry),
            "version": meta.get("version", "0.0.0"),
            "author": meta.get("author", ""),
            "description": meta.get("description", ""),
            "enabled": bool(meta.get("enabled", True)) and not validation_errors,
            "trusted": bool(meta.get("trusted", False)),
            "path": str(plugin_path),
            "error": error_msg,
        })

    return plugins


@contextlib.contextmanager
def _scoped_plugin_path(plugin_path):
    """Expose a plugin's own directory for imports only while it executes.

    The directory is appended to the end of ``sys.path`` (so it can never
    shadow stdlib or app modules) and is always removed afterward, so no
    plugin directory — and never a parent that could expose sibling plugins —
    persists on the global import path.
    """
    added = plugin_path not in sys.path
    if added:
        sys.path.append(plugin_path)
    try:
        yield
    finally:
        if added:
            try:
                sys.path.remove(plugin_path)
            except ValueError:
                pass


def load_plugin(plugin_info, log_fn=None):
    """Load a single plugin by importing its Python module.

    Returns True on success, False on error.
    """
    if not plugin_info.get("enabled", True):
        return False

    plugin_path = plugin_info.get("path", "")
    plugin_id = plugin_info.get("id", "unknown")
    if not plugin_path or not os.path.isdir(plugin_path):
        return False

    module_name = os.path.basename(plugin_path)
    try:
        with _scoped_plugin_path(plugin_path):
            # Try loading as a package (has __init__.py)
            init_py = os.path.join(plugin_path, "__init__.py")
            if os.path.isfile(init_py):
                spec = importlib.util.spec_from_file_location(
                    f"sk_plugin_{module_name}", init_py,
                    submodule_search_locations=[plugin_path],
                )
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = mod
                    spec.loader.exec_module(mod)
                    if log_fn:
                        log_fn(f"[PLUGIN] Loaded: {plugin_id} v{plugin_info.get('version', '?')}")
                    return True

            # Try loading a single .py file
            for fname in os.listdir(plugin_path):
                if fname.endswith(".py") and fname != "__init__.py":
                    fpath = os.path.join(plugin_path, fname)
                    spec = importlib.util.spec_from_file_location(
                        f"sk_plugin_{module_name}_{fname[:-3]}", fpath,
                    )
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        sys.modules[spec.name] = mod
                        spec.loader.exec_module(mod)
                        if log_fn:
                            log_fn(f"[PLUGIN] Loaded: {plugin_id} ({fname})")
                        return True

    except Exception as e:
        plugin_info["error"] = str(e)
        if log_fn:
            log_fn(f"[PLUGIN] Error loading {plugin_id}: {e}")

    return False


def load_all_plugins(log_fn=None):
    """Discover and load all enabled+trusted plugins.

    Returns ``(loaded_count, error_count)``.
    """
    plugins = discover_plugins()
    loaded = 0
    errors = 0
    for p in plugins:
        if not p.get("enabled", True):
            continue
        if not p.get("trusted", False):
            if log_fn:
                log_fn(f"[PLUGIN] Skipped untrusted: {p.get('id', '?')}")
            continue
        if load_plugin(p, log_fn):
            loaded += 1
        elif p.get("error"):
            errors += 1
    if log_fn and (loaded or errors):
        log_fn(f"[PLUGIN] {loaded} loaded, {errors} error(s)")
    return loaded, errors


def untrusted_plugins():
    """Return plugins that are enabled but not yet trusted by the user."""
    return [p for p in discover_plugins()
            if p.get("enabled", True) and not p.get("trusted", False)
            and not p.get("error")]


def mark_trusted(plugin_id, trusted=True):
    """Set or clear the ``trusted`` flag in a plugin's manifest."""
    _ensure_dir()
    for entry in os.listdir(str(PLUGINS_DIR)):
        plugin_path = PLUGINS_DIR / entry
        manifest_path = plugin_path / "plugin.json"
        if not manifest_path.is_file():
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("id", entry) == plugin_id:
                meta["trusted"] = bool(trusted)
                if not trusted:
                    meta["enabled"] = False
                tmp_path = manifest_path.with_suffix(".json.tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=2)
                os.replace(tmp_path, manifest_path)
                return True
        except (OSError, json.JSONDecodeError):
            continue
    return False


def set_plugin_enabled(plugin_id, enabled):
    """Update a plugin's enabled state in its manifest."""
    _ensure_dir()
    for entry in os.listdir(str(PLUGINS_DIR)):
        plugin_path = PLUGINS_DIR / entry
        manifest_path = plugin_path / "plugin.json"
        if not manifest_path.is_file():
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("id", entry) == plugin_id:
                meta["enabled"] = bool(enabled)
                tmp_path = manifest_path.with_suffix(".json.tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=2)
                os.replace(tmp_path, manifest_path)
                return True
        except (OSError, json.JSONDecodeError):
            continue
    return False


def plugins_dir_path():
    """Return the plugins directory path (for 'Open folder' button)."""
    _ensure_dir()
    return str(PLUGINS_DIR)
