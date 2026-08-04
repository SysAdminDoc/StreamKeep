"""Versioned plugin adapter contracts and trusted loader (F77/V roadmap).

Plugins are Python packages or modules in ``%APPDATA%/StreamKeep/plugins/``
(or ``data/plugins/`` in portable mode).  A manifest version 2 plugin declares
one or more adapter contracts explicitly::

    {
        "manifest_version": 2,
        "adapters": [
            {
                "type": "extractor",
                "entrypoint": "SampleExtractor",
                "interface_version": 1,
                "permissions": ["network"],
                "dependencies": [],
                "timeout_seconds": 30
            }
        ]
    }

``youtube_backend`` adapters implement ``solve(request)`` and
``health(request)``.  The host supplies only a YouTube URL, selected mode,
backend URL, and player-client label; the solve result is validated before it
can become yt-dlp extractor arguments.

The loader keeps imports package-scoped through ``ModuleSpec`` search
locations; it never appends a plugin directory to the process-wide
``sys.path``.  Trusted plugins still run in-process, so trust is explicit and
the adapter broker bounds calls with a cooperative cancellation context and a
daemon timeout thread.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import json
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Callable

from . import VERSION
from .paths import CONFIG_DIR

CURRENT_MANIFEST_VERSION = 2
SUPPORTED_MANIFEST_VERSIONS = frozenset({1, 2})
ADAPTER_INTERFACE_VERSION = 1
ADAPTER_TYPES = frozenset({
    "extractor", "postprocess", "upload", "youtube_backend",
})
ADAPTER_PERMISSIONS = frozenset({
    "credentials",
    "filesystem_read",
    "filesystem_write",
    "gui",
    "network",
    "subprocess",
})
DEFAULT_ADAPTER_TIMEOUT_SECONDS = 30.0
MAX_ADAPTER_TIMEOUT_SECONDS = 300.0
_REQUIRED_FIELDS = ("id", "name", "version")
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_SAFE_MODULE_RE = re.compile(r"[^A-Za-z0-9_]")

PLUGINS_DIR = CONFIG_DIR / "plugins"

_LOADED_MODULES: dict[str, ModuleType] = {}
_LOADED_ADAPTERS: dict[tuple[str, str, str], "PluginAdapterHandle"] = {}


class PluginLoadError(RuntimeError):
    """Raised when a plugin module or declared adapter cannot be loaded."""


class PluginCompatibilityError(RuntimeError):
    """Raised when a plugin declares an unsupported or unavailable contract."""


class PluginPermissionError(RuntimeError):
    """Raised when an adapter requests a permission it did not declare."""

    def __init__(self, permission: str):
        self.permission = permission
        super().__init__(f"Permission not declared: {permission}")


class PluginCancelledError(RuntimeError):
    """Raised inside an adapter when its cancellation event is set."""


class PluginTimeoutError(RuntimeError):
    """Raised when an adapter exceeds its declared execution budget."""


@dataclass(frozen=True)
class PluginDependency:
    """One import/distribution dependency declared by an adapter."""

    name: str
    minimum_version: str = ""

    def to_dict(self) -> dict[str, str]:
        result = {"name": self.name}
        if self.minimum_version:
            result["minimum_version"] = self.minimum_version
        return result


@dataclass(frozen=True)
class PluginAdapterSpec:
    """Validated, versioned declaration for one plugin adapter."""

    plugin_id: str
    adapter_type: str
    entrypoint: str
    interface_version: int
    permissions: tuple[str, ...]
    dependencies: tuple[PluginDependency, ...]
    timeout_seconds: float
    manifest_version: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.adapter_type,
            "entrypoint": self.entrypoint,
            "interface_version": self.interface_version,
            "permissions": list(self.permissions),
            "dependencies": [dependency.to_dict() for dependency in self.dependencies],
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class PluginAdapterHandle:
    """Resolved adapter target plus its immutable manifest contract."""

    spec: PluginAdapterSpec
    module: ModuleType
    target: Any
    plugin_info: dict[str, Any]


@dataclass(frozen=True)
class PluginOutcome:
    """Machine-readable result of a bounded adapter invocation."""

    ok: bool
    code: str
    adapter_type: str
    plugin_id: str
    value: Any = None
    error: str = ""
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "adapter_type": self.adapter_type,
            "plugin_id": self.plugin_id,
            "value": self.value,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }


class PluginExecutionContext:
    """Capability broker passed to adapters that opt into the context arg."""

    def __init__(
        self,
        spec: PluginAdapterSpec,
        cancel_event: threading.Event,
        progress_cb: Callable[[float], None] | None = None,
    ):
        self.spec = spec
        self.permissions = frozenset(spec.permissions)
        self.cancel_event = cancel_event
        self.progress_cb = progress_cb

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise PluginPermissionError(permission)

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise PluginCancelledError("Adapter execution cancelled")

    def report(self, progress: float) -> None:
        self.check_cancelled()
        if self.progress_cb is not None:
            self.progress_cb(max(0.0, min(1.0, float(progress))))


def _ensure_dir():
    """Create the plugins directory if it doesn't exist."""
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)


def _parse_semver(version_str):
    """Parse a version string into a (major, minor, patch) tuple or None."""
    match = _SEMVER_RE.match(str(version_str or ""))
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _manifest_version(meta: dict[str, Any]) -> int:
    value = meta.get("manifest_version", 1)
    if isinstance(value, bool) or not isinstance(value, int):
        return -1
    return value


def _parse_dependency(raw: Any, index: int, adapter_label: str):
    errors: list[str] = []
    if isinstance(raw, str):
        name = raw.strip()
        minimum = ""
    elif isinstance(raw, dict):
        name = raw.get("name", "")
        minimum = raw.get("minimum_version", "") or ""
        if not isinstance(name, str) or not name.strip():
            errors.append(
                f"Adapter {adapter_label} dependency {index} requires a non-empty name"
            )
            name = ""
        if not isinstance(minimum, str):
            errors.append(
                f"Adapter {adapter_label} dependency {index} minimum_version must be a string"
            )
            minimum = ""
    else:
        errors.append(
            f"Adapter {adapter_label} dependency {index} must be a string or object"
        )
        name = ""
        minimum = ""

    name = name.strip() if isinstance(name, str) else ""
    minimum = minimum.strip() if isinstance(minimum, str) else ""
    if name and minimum and _parse_semver(minimum) is None:
        errors.append(
            f"Adapter {adapter_label} dependency {index} has invalid minimum_version "
            f"{minimum!r}"
        )
    return PluginDependency(name, minimum), errors


def _parse_adapter_specs(meta: dict[str, Any], plugin_id: str = ""):
    """Return ``(specs, structural_errors)`` without importing plugin code."""
    manifest_version = _manifest_version(meta)
    if manifest_version != 2:
        return [], []

    raw_adapters = meta.get("adapters")
    if not isinstance(raw_adapters, list) or not raw_adapters:
        return [], ["Manifest version 2 requires a non-empty adapters list"]

    specs: list[PluginAdapterSpec] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_adapters):
        label = str(index)
        if not isinstance(raw, dict):
            errors.append(f"Adapter {label} must be a JSON object")
            continue

        adapter_type = raw.get("type", "")
        entrypoint = raw.get("entrypoint", "")
        if not isinstance(adapter_type, str) or adapter_type not in ADAPTER_TYPES:
            errors.append(
                f"Adapter {label} has unsupported type {adapter_type!r}; "
                f"expected one of {sorted(ADAPTER_TYPES)}"
            )
        if not isinstance(entrypoint, str) or not entrypoint.strip():
            errors.append(f"Adapter {label} requires a non-empty entrypoint")
            entrypoint = ""
        else:
            entrypoint = entrypoint.strip()

        interface_version = raw.get("interface_version")
        if isinstance(interface_version, bool) or not isinstance(interface_version, int):
            errors.append(f"Adapter {label} requires integer interface_version 1")
            interface_version = -1
        elif interface_version != ADAPTER_INTERFACE_VERSION:
            errors.append(
                f"Unsupported adapter interface_version {interface_version} for adapter {label}"
            )

        permissions = raw.get("permissions", [])
        if not isinstance(permissions, list):
            errors.append(f"Adapter {label} permissions must be a list")
            permissions = []
        clean_permissions: list[str] = []
        for permission in permissions:
            if not isinstance(permission, str) or permission not in ADAPTER_PERMISSIONS:
                errors.append(f"Adapter {label} has unsupported permission {permission!r}")
            elif permission not in clean_permissions:
                clean_permissions.append(permission)

        dependencies = raw.get("dependencies", [])
        if not isinstance(dependencies, list):
            errors.append(f"Adapter {label} dependencies must be a list")
            dependencies = []
        clean_dependencies: list[PluginDependency] = []
        for dependency_index, dependency_raw in enumerate(dependencies):
            dependency, dependency_errors = _parse_dependency(
                dependency_raw, dependency_index, label,
            )
            errors.extend(dependency_errors)
            if dependency.name:
                clean_dependencies.append(dependency)

        timeout = raw.get("timeout_seconds", DEFAULT_ADAPTER_TIMEOUT_SECONDS)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            errors.append(f"Adapter {label} timeout_seconds must be a number")
            timeout = -1.0
        timeout = float(timeout)
        if timeout <= 0 or timeout > MAX_ADAPTER_TIMEOUT_SECONDS:
            errors.append(
                f"Adapter {label} timeout_seconds must be between 0 and "
                f"{MAX_ADAPTER_TIMEOUT_SECONDS:g}"
            )

        key = (str(adapter_type), entrypoint)
        if key in seen:
            errors.append(f"Duplicate adapter declaration: {adapter_type}:{entrypoint}")
        seen.add(key)
        if isinstance(adapter_type, str) and adapter_type in ADAPTER_TYPES \
                and entrypoint and interface_version == 1:
            specs.append(PluginAdapterSpec(
                plugin_id=plugin_id,
                adapter_type=adapter_type,
                entrypoint=entrypoint,
                interface_version=interface_version,
                permissions=tuple(clean_permissions),
                dependencies=tuple(clean_dependencies),
                timeout_seconds=timeout,
                manifest_version=manifest_version,
            ))
    return specs, errors


def plugin_contract_details(
    meta: dict[str, Any],
    specs: list[PluginAdapterSpec] | None = None,
) -> dict[str, Any]:
    """Return the plain-language, reviewable contract declared by a plugin.

    The fingerprint covers the declared capabilities and adapter shape, but
    not the plugin version or current StreamKeep version.  Implementation
    fixes with an unchanged contract do not need a second prompt; any
    permission, dependency, compatibility, entrypoint, interface, or timeout
    change does.
    """
    if not isinstance(meta, dict):
        meta = {}
    plugin_id = str(meta.get("id", ""))
    if specs is None:
        raw_specs = meta.get("adapters")
        if not isinstance(raw_specs, list) and isinstance(meta.get("adapter_specs"), list):
            raw_specs = meta.get("adapter_specs")
            meta = dict(meta)
            meta["manifest_version"] = 2
            meta["adapters"] = raw_specs
        specs, _ = _parse_adapter_specs(meta, plugin_id)

    permissions = sorted({
        permission
        for spec in specs
        for permission in spec.permissions
    })
    dependency_map: dict[tuple[str, str], PluginDependency] = {}
    for spec in specs:
        for dependency in spec.dependencies:
            dependency_map[(dependency.name, dependency.minimum_version)] = dependency
    dependencies = [
        dependency.to_dict()
        for _, dependency in sorted(dependency_map.items(), key=lambda item: item[0])
    ]

    raw_min = meta.get("min_app_version", "")
    raw_max = meta.get("max_app_version", "")
    min_app_version = str(raw_min).strip() if raw_min else ""
    max_app_version = str(raw_max).strip() if raw_max else ""
    if min_app_version and max_app_version:
        compatibility_range = f">= {min_app_version} and <= {max_app_version}"
    elif min_app_version:
        compatibility_range = f">= {min_app_version}"
    elif max_app_version:
        compatibility_range = f"<= {max_app_version}"
    else:
        compatibility_range = "Any StreamKeep version"
    compatibility = {
        "manifest_version": _manifest_version(meta),
        "min_app_version": min_app_version,
        "max_app_version": max_app_version,
        "current_app_version": VERSION,
        "range": compatibility_range,
    }
    adapters = [spec.to_dict() for spec in specs]
    entrypoints = [
        {
            "type": spec.adapter_type,
            "entrypoint": spec.entrypoint,
            "interface_version": spec.interface_version,
        }
        for spec in specs
    ]
    fingerprint_payload = {
        "manifest_version": compatibility["manifest_version"],
        "min_app_version": min_app_version,
        "max_app_version": max_app_version,
        "permissions": permissions,
        "dependencies": dependencies,
        "adapters": adapters,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "permissions": permissions,
        "dependencies": dependencies,
        "compatibility": compatibility,
        "entrypoints": entrypoints,
        "adapters": adapters,
        "contract_fingerprint": fingerprint,
    }


def _trust_review_matches(meta: dict[str, Any], contract: dict[str, Any]) -> bool:
    """Return whether a trusted manifest approved its current contract."""
    if not isinstance(meta, dict) or not meta.get("trusted", False):
        return False
    review = meta.get("trust_review")
    if not isinstance(review, dict):
        return False
    if review.get("contract_fingerprint") != contract.get("contract_fingerprint"):
        return False
    reviewed_permissions = review.get("permissions")
    return (
        isinstance(reviewed_permissions, list)
        and sorted(str(permission) for permission in reviewed_permissions)
        == list(contract.get("permissions", []))
    )


def validate_manifest(meta, entry_name=""):
    """Validate a plugin manifest dict. Returns a list of error strings."""
    errors: list[str] = []
    if not isinstance(meta, dict):
        return ["Manifest is not a JSON object"]
    for field in _REQUIRED_FIELDS:
        if not meta.get(field):
            errors.append(f"Missing required field: {field}")
    if meta.get("version") and _parse_semver(meta["version"]) is None:
        errors.append(f"Invalid version format: {meta['version']!r} (expected X.Y.Z)")

    manifest_version = _manifest_version(meta)
    if manifest_version not in SUPPORTED_MANIFEST_VERSIONS:
        shown = meta.get("manifest_version")
        if not isinstance(shown, int) or isinstance(shown, bool):
            errors.append(f"Invalid manifest_version: {shown!r}")
        else:
            errors.append(
                f"Unsupported manifest_version {shown} "
                f"(app supports {sorted(SUPPORTED_MANIFEST_VERSIONS)})"
            )
    elif manifest_version == 1 and "adapters" in meta:
        errors.append("Adapter declarations require manifest_version 2")
    elif manifest_version == 2:
        _, adapter_errors = _parse_adapter_specs(meta, str(meta.get("id", entry_name)))
        errors.extend(adapter_errors)

    min_ver = meta.get("min_app_version", "")
    max_ver = meta.get("max_app_version", "")
    current = _parse_semver(VERSION)
    minimum = None
    if min_ver:
        minimum = _parse_semver(min_ver)
        if minimum is None:
            errors.append(f"Invalid min_app_version format: {min_ver!r}")
        elif current and minimum > current:
            errors.append(
                f"Requires StreamKeep >= {min_ver} (running {VERSION})"
            )
    maximum = None
    if max_ver:
        maximum = _parse_semver(max_ver)
        if maximum is None:
            errors.append(f"Invalid max_app_version format: {max_ver!r}")
        elif current and maximum < current:
            errors.append(
                f"Supports StreamKeep <= {max_ver} (running {VERSION})"
            )
    if minimum and maximum and minimum > maximum:
        errors.append(
            f"Invalid app version range: min_app_version {min_ver!r} exceeds "
            f"max_app_version {max_ver!r}"
        )
    return errors


def _dependency_diagnostics(specs: list[PluginAdapterSpec]):
    errors: list[str] = []
    warnings: list[str] = []
    for spec in specs:
        for dependency in spec.dependencies:
            found = False
            installed_version = ""
            try:
                found = importlib.util.find_spec(dependency.name) is not None
            except (ImportError, AttributeError, ValueError):
                found = False
            try:
                installed_version = importlib.metadata.version(dependency.name)
                found = True
            except (importlib.metadata.PackageNotFoundError, ValueError):
                pass
            if not found:
                errors.append(
                    f"Missing dependency {dependency.name!r} for "
                    f"{spec.adapter_type}:{spec.entrypoint}"
                )
                continue
            if dependency.minimum_version:
                installed = _parse_semver(installed_version)
                minimum = _parse_semver(dependency.minimum_version)
                if installed is None:
                    warnings.append(
                        f"Could not compare installed version for dependency "
                        f"{dependency.name!r}"
                    )
                elif minimum and installed < minimum:
                    errors.append(
                        f"Dependency {dependency.name!r} is {installed_version}, "
                        f"requires >= {dependency.minimum_version}"
                    )
    return errors, warnings


def _read_manifest(plugin_path: str):
    manifest_path = os.path.join(plugin_path, "plugin.json")
    with open(manifest_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _manifest_for_info(plugin_info: dict[str, Any]):
    manifest = plugin_info.get("manifest")
    if isinstance(manifest, dict):
        return manifest
    path = str(plugin_info.get("path", "") or "")
    if path and os.path.isfile(os.path.join(path, "plugin.json")):
        try:
            return _read_manifest(path)
        except (OSError, json.JSONDecodeError):
            return None
    return None


def diagnose_plugin(plugin_info):
    """Return compatibility diagnostics without importing or executing code."""
    if not isinstance(plugin_info, dict):
        contract = plugin_contract_details({})
        return {
            "compatible": False,
            "errors": ["Plugin info is not an object"],
            "warnings": [],
            **contract,
            "trust_reviewed": False,
            "review_required": True,
        }
    meta = _manifest_for_info(plugin_info)
    if meta is None:
        # Direct loader callers may provide the legacy metadata shape without a
        # manifest file.  Treat it as a v1 discovery record.
        meta = {
            "id": plugin_info.get("id", ""),
            "name": plugin_info.get("name", plugin_info.get("id", "")),
            "version": plugin_info.get("version", "0.0.0"),
            "manifest_version": plugin_info.get("manifest_version", 1),
        }
        if "adapter_specs" in plugin_info:
            meta["manifest_version"] = 2
            meta["adapters"] = plugin_info.get("adapter_specs", [])
    errors = validate_manifest(meta, str(plugin_info.get("id", "")))
    specs, spec_errors = _parse_adapter_specs(meta, str(meta.get("id", "")))
    errors.extend(error for error in spec_errors if error not in errors)
    dependency_errors, warnings = _dependency_diagnostics(specs)
    errors.extend(dependency_errors)
    contract = plugin_contract_details(meta, specs)
    trust_reviewed = _trust_review_matches(meta, contract)
    return {
        "id": plugin_info.get("id", meta.get("id", "")),
        "name": plugin_info.get("name", meta.get("name", "")),
        "version": plugin_info.get("version", meta.get("version", "0.0.0")),
        "manifest_version": _manifest_version(meta),
        "compatible": not errors,
        "errors": errors,
        "warnings": warnings,
        **contract,
        "trusted": bool(meta.get("trusted", plugin_info.get("trusted", False))),
        "trust_reviewed": trust_reviewed,
        "review_required": not trust_reviewed,
    }


def discover_plugins():
    """Scan the plugins directory and return metadata and compatibility data."""
    _ensure_dir()
    plugins = []
    for entry in sorted(os.listdir(str(PLUGINS_DIR))):
        plugin_path = PLUGINS_DIR / entry
        manifest_path = plugin_path / "plugin.json"

        if not plugin_path.is_dir() or not manifest_path.is_file():
            continue

        try:
            meta = _read_manifest(str(plugin_path))
        except (OSError, json.JSONDecodeError) as error:
            contract = plugin_contract_details({})
            plugins.append({
                "id": entry, "name": entry, "version": "?",
                "author": "", "description": "", "manifest_version": -1,
                "enabled": False, "trusted": False, "path": str(plugin_path),
                "adapter_specs": [],
                **contract,
                "trust_reviewed": False,
                "review_required": True,
                "error": f"Invalid plugin.json: {error}",
            })
            continue

        validation_errors = validate_manifest(meta, entry)
        specs, spec_errors = _parse_adapter_specs(meta, str(meta.get("id", entry)))
        compatibility_errors, warnings = _dependency_diagnostics(specs)
        all_errors = list(validation_errors)
        all_errors.extend(error for error in spec_errors if error not in all_errors)
        all_errors.extend(compatibility_errors)
        error_msg = "; ".join(all_errors)
        contract = plugin_contract_details(meta, specs)
        trust_reviewed = _trust_review_matches(meta, contract)
        plugins.append({
            "id": meta.get("id", entry),
            "name": meta.get("name", entry),
            "version": meta.get("version", "0.0.0"),
            "author": meta.get("author", ""),
            "description": meta.get("description", ""),
            "manifest_version": _manifest_version(meta),
            "enabled": bool(meta.get("enabled", True)) and not all_errors,
            "trusted": bool(meta.get("trusted", False)),
            "path": str(plugin_path),
            "adapter_specs": [spec.to_dict() for spec in specs],
            **contract,
            "trust_reviewed": trust_reviewed,
            "review_required": not trust_reviewed,
            "warnings": warnings,
            "error": error_msg,
        })

    return plugins


def _module_name_for_path(plugin_path: str) -> str:
    base = _SAFE_MODULE_RE.sub("_", os.path.basename(os.path.abspath(plugin_path)))
    base = base.strip("_") or "plugin"
    name = f"sk_plugin_{base}"
    existing = sys.modules.get(name)
    if existing is not None:
        existing_path = os.path.abspath(str(getattr(existing, "__file__", "") or ""))
        if existing_path and not existing_path.startswith(os.path.abspath(plugin_path)):
            digest = hashlib.sha256(os.path.abspath(plugin_path).encode("utf-8")).hexdigest()[:8]
            name = f"{name}_{digest}"
    return name


def _load_module(plugin_info: dict[str, Any], log_fn=None) -> ModuleType:
    plugin_path = str(plugin_info.get("path", "") or "")
    plugin_id = str(plugin_info.get("id", "unknown"))
    if not plugin_path or not os.path.isdir(plugin_path):
        raise PluginLoadError(f"Plugin path is not a directory: {plugin_id}")
    key = os.path.abspath(plugin_path)
    if key in _LOADED_MODULES:
        return _LOADED_MODULES[key]

    module_name = _module_name_for_path(plugin_path)
    init_py = os.path.join(plugin_path, "__init__.py")
    if os.path.isfile(init_py):
        spec = importlib.util.spec_from_file_location(
            module_name,
            init_py,
            submodule_search_locations=[plugin_path],
        )
        label = "package"
    else:
        candidates = sorted(
            fname for fname in os.listdir(plugin_path)
            if fname.endswith(".py") and fname != "__init__.py"
        )
        if not candidates:
            raise PluginLoadError(f"Plugin {plugin_id} contains no Python entrypoint")
        fname = candidates[0]
        spec = importlib.util.spec_from_file_location(
            f"{module_name}_{fname[:-3]}",
            os.path.join(plugin_path, fname),
        )
        label = fname
    if spec is None or spec.loader is None:
        raise PluginLoadError(f"Could not create import spec for plugin {plugin_id}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    _LOADED_MODULES[key] = module
    if log_fn:
        log_fn(f"[PLUGIN] Loaded: {plugin_id} v{plugin_info.get('version', '?')} ({label})")
    return module


def _legacy_extractor_specs(module: ModuleType, plugin_id: str):
    try:
        from .extractors.base import Extractor
    except ImportError:
        return []
    specs = []
    for name, target in vars(module).items():
        if not inspect.isclass(target) or target is Extractor:
            continue
        try:
            is_extractor = issubclass(target, Extractor)
        except TypeError:
            is_extractor = False
        if is_extractor:
            specs.append(PluginAdapterSpec(
                plugin_id=plugin_id,
                adapter_type="extractor",
                entrypoint=name,
                interface_version=ADAPTER_INTERFACE_VERSION,
                permissions=(),
                dependencies=(),
                timeout_seconds=DEFAULT_ADAPTER_TIMEOUT_SECONDS,
                manifest_version=1,
            ))
    return specs


def _resolve_entrypoint(module: ModuleType, entrypoint: str):
    if ":" in entrypoint:
        module_part, target_name = entrypoint.split(":", 1)
        module_part = module_part.strip()
        target_name = target_name.strip()
        if not target_name:
            raise PluginLoadError(f"Entrypoint has no target: {entrypoint}")
        if module_part:
            if module_part.startswith("."):
                module_part = module_part[1:]
            target_module = importlib.import_module(f".{module_part}", module.__name__)
        else:
            target_module = module
    else:
        target_module = module
        target_name = entrypoint
    target = target_module
    for part in target_name.split("."):
        target = getattr(target, part)
    return target


def _validate_adapter_target(spec: PluginAdapterSpec, target: Any) -> None:
    if spec.adapter_type == "extractor":
        from .extractors.base import Extractor
        if not inspect.isclass(target) or not issubclass(target, Extractor):
            raise PluginCompatibilityError(
                f"{spec.entrypoint} is not an Extractor subclass"
            )
        return
    if spec.adapter_type == "youtube_backend":
        valid = all(
            callable(getattr(target, method_name, None))
            for method_name in ("solve", "health")
        )
        if not valid:
            raise PluginCompatibilityError(
                f"{spec.entrypoint} does not implement solve() and health()"
            )
        return
    method_name = "process" if spec.adapter_type == "postprocess" else "upload"
    if inspect.isclass(target):
        valid = callable(getattr(target, method_name, None))
    else:
        valid = callable(getattr(target, method_name, None)) or callable(target)
    if not valid:
        raise PluginCompatibilityError(
            f"{spec.entrypoint} does not implement {method_name}()"
        )


def _specs_from_plugin_info(plugin_info: dict[str, Any], module: ModuleType):
    raw_specs = plugin_info.get("adapter_specs")
    if isinstance(raw_specs, list) and raw_specs:
        meta = {
            "id": plugin_info.get("id", "unknown"),
            "manifest_version": 2,
            "adapters": raw_specs,
        }
        specs, errors = _parse_adapter_specs(meta, str(plugin_info.get("id", "unknown")))
        if errors:
            raise PluginCompatibilityError("; ".join(errors))
        return specs
    return _legacy_extractor_specs(module, str(plugin_info.get("id", "unknown")))


def load_plugin_adapters(plugin_info, log_fn=None):
    """Load and resolve all declared adapters for one trusted plugin."""
    if not plugin_info.get("enabled", True):
        return []
    if plugin_info.get("error"):
        return []
    try:
        module = _load_module(plugin_info, log_fn)
        specs = _specs_from_plugin_info(plugin_info, module)
        handles = []
        for spec in specs:
            dependency_errors, _ = _dependency_diagnostics([spec])
            if dependency_errors:
                raise PluginCompatibilityError("; ".join(dependency_errors))
            target = _resolve_entrypoint(module, spec.entrypoint)
            _validate_adapter_target(spec, target)
            handle = PluginAdapterHandle(spec, module, target, plugin_info)
            _LOADED_ADAPTERS[(spec.plugin_id, spec.adapter_type, spec.entrypoint)] = handle
            handles.append(handle)
        plugin_info["loaded_adapters"] = [handle.spec.to_dict() for handle in handles]
        return handles
    except Exception as error:
        plugin_info["error"] = str(error)
        if log_fn:
            log_fn(f"[PLUGIN] Error loading {plugin_info.get('id', '?')}: {error}")
        return []


def load_plugin(plugin_info, log_fn=None):
    """Load one plugin module and register its validated adapter contracts."""
    if not plugin_info.get("enabled", True):
        return False
    if not plugin_info.get("path", ""):
        return False
    handles = load_plugin_adapters(plugin_info, log_fn)
    if plugin_info.get("error"):
        return False
    # A legacy plugin with no extractor classes is still a valid importable
    # module; v2 plugins are required to declare at least one adapter.
    return bool(handles) or bool(_LOADED_MODULES.get(os.path.abspath(plugin_info["path"])))


def load_all_plugins(log_fn=None):
    """Discover and load all enabled+trusted plugins.

    Returns ``(loaded_count, error_count)``.
    """
    plugins = discover_plugins()
    loaded = 0
    errors = 0
    for plugin in plugins:
        if not plugin.get("enabled", True):
            continue
        if not plugin.get("trusted", False):
            if log_fn:
                log_fn(f"[PLUGIN] Skipped untrusted: {plugin.get('id', '?')}")
            continue
        if not plugin.get("trust_reviewed", False):
            if log_fn:
                log_fn(
                    f"[PLUGIN] Skipped contract review required: "
                    f"{plugin.get('id', '?')}"
                )
            errors += 1
            continue
        if load_plugin(plugin, log_fn):
            loaded += 1
        elif plugin.get("error"):
            errors += 1
    if log_fn and (loaded or errors):
        log_fn(f"[PLUGIN] {loaded} loaded, {errors} error(s)")
    return loaded, errors


def registered_adapters(*, plugin_id: str = ""):
    """Return currently resolved adapter handles, optionally for one plugin."""
    handles = list(_LOADED_ADAPTERS.values())
    if plugin_id:
        handles = [handle for handle in handles if handle.spec.plugin_id == plugin_id]
    return handles


def declarative_adapter_diagnostics(directory=None, config=None):
    """Expose the no-code source-adapter surface beside plugin diagnostics.

    Declarative definitions intentionally do not become trusted Python
    plugins. This small facade lets CLI and Settings diagnostics report both
    adapter families through one integration boundary without importing or
    executing definition content.
    """
    from .declarative import declarative_adapter_diagnostics as _diagnostics

    return _diagnostics(directory=directory, config=config)


def _call_adapter_target(
    handle: PluginAdapterHandle,
    operation: str | None,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    context: PluginExecutionContext,
):
    target = handle.target() if inspect.isclass(handle.target) else handle.target
    default_operation = {
        "extractor": "resolve",
        "postprocess": "process",
        "upload": "upload",
        "youtube_backend": "solve",
    }[handle.spec.adapter_type]
    method = getattr(target, operation or default_operation, None)
    if method is None and callable(target):
        method = target
    if not callable(method):
        raise PluginCompatibilityError(
            f"Adapter target has no callable operation {operation or default_operation}"
        )

    call_kwargs = dict(kwargs)
    try:
        signature = inspect.signature(method)
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if "context" in signature.parameters or accepts_kwargs:
            call_kwargs.setdefault("context", context)
        if "cancel_event" in signature.parameters or accepts_kwargs:
            call_kwargs.setdefault("cancel_event", context.cancel_event)
        if "progress_cb" in signature.parameters or accepts_kwargs:
            call_kwargs.setdefault("progress_cb", context.report)
    except (TypeError, ValueError):
        call_kwargs.setdefault("context", context)
    context.check_cancelled()
    result = method(*args, **call_kwargs)
    context.check_cancelled()
    return result


def _outcome_error(error: BaseException) -> str:
    # Do not echo arbitrary plugin traceback text into CLI/API output.  Keep
    # the contract useful while avoiding accidental credential/path leakage.
    return str(error).replace("\r", " ").replace("\n", " ")[:500]


def execute_plugin_adapter(
    adapter: PluginAdapterHandle,
    *args,
    operation: str | None = None,
    cancel_event: threading.Event | None = None,
    timeout_seconds: float | None = None,
    required_permissions: tuple[str, ...] | list[str] = (),
    progress_cb: Callable[[float], None] | None = None,
    **kwargs,
) -> PluginOutcome:
    """Execute an adapter with typed success, cancellation, timeout, or error."""
    spec = adapter.spec
    started = time.monotonic()
    for permission in required_permissions:
        if permission not in spec.permissions:
            return PluginOutcome(
                False, "permission_denied", spec.adapter_type, spec.plugin_id,
                error=f"Permission not declared: {permission}",
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
    event = cancel_event or threading.Event()
    try:
        timeout = spec.timeout_seconds if timeout_seconds is None else float(timeout_seconds)
    except (TypeError, ValueError):
        timeout = spec.timeout_seconds
    timeout = max(0.001, min(timeout, spec.timeout_seconds, MAX_ADAPTER_TIMEOUT_SECONDS))
    if event.is_set():
        return PluginOutcome(
            False, "cancelled", spec.adapter_type, spec.plugin_id,
            error="Adapter execution cancelled",
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
    context = PluginExecutionContext(spec, event, progress_cb)

    def worker():
        try:
            result = _call_adapter_target(adapter, operation, args, kwargs, context)
            result_queue.put(("ok", result))
        except PluginCancelledError as error:
            result_queue.put(("cancelled", error))
        except PluginPermissionError as error:
            result_queue.put(("permission_denied", error))
        except Exception as error:  # trusted plugin boundary: return a typed outcome
            result_queue.put(("error", error))

    thread = threading.Thread(
        target=worker,
        name=f"streamkeep-plugin-{spec.plugin_id}-{spec.adapter_type}",
        daemon=True,
    )
    thread.start()
    try:
        code, payload = result_queue.get(timeout=timeout)
    except queue.Empty:
        event.set()
        return PluginOutcome(
            False, "timeout", spec.adapter_type, spec.plugin_id,
            error=f"Adapter exceeded {timeout:g}s timeout",
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    if event.is_set() and code == "ok":
        return PluginOutcome(
            False, "cancelled", spec.adapter_type, spec.plugin_id,
            error="Adapter execution cancelled", elapsed_ms=elapsed_ms,
        )
    if code == "ok":
        return PluginOutcome(True, "ok", spec.adapter_type, spec.plugin_id,
                             value=payload, elapsed_ms=elapsed_ms)
    return PluginOutcome(
        False, code, spec.adapter_type, spec.plugin_id,
        error=_outcome_error(payload), elapsed_ms=elapsed_ms,
    )


def untrusted_plugins():
    """Return enabled plugins that need trust or contract review."""
    return [plugin for plugin in discover_plugins()
            if plugin.get("enabled", True)
            and (
                not plugin.get("trusted", False)
                or not plugin.get("trust_reviewed", False)
            )
            and not plugin.get("error")]


def mark_trusted(plugin_id, trusted=True, review=None):
    """Set or clear trust and the reviewed contract in a plugin manifest."""
    _ensure_dir()
    for entry in os.listdir(str(PLUGINS_DIR)):
        plugin_path = PLUGINS_DIR / entry
        manifest_path = plugin_path / "plugin.json"
        if not manifest_path.is_file():
            continue
        try:
            meta = _read_manifest(str(plugin_path))
            if meta.get("id", entry) == plugin_id:
                meta["trusted"] = bool(trusted)
                if trusted:
                    specs, _ = _parse_adapter_specs(meta, str(plugin_id))
                    contract = plugin_contract_details(meta, specs)
                    approved = review if isinstance(review, dict) else {}
                    meta["trust_review"] = {
                        "contract_fingerprint": approved.get(
                            "contract_fingerprint", contract["contract_fingerprint"]
                        ),
                        "permissions": list(approved.get(
                            "permissions", contract["permissions"]
                        )),
                    }
                else:
                    meta.pop("trust_review", None)
                    meta["enabled"] = False
                tmp_path = manifest_path.with_suffix(".json.tmp")
                with open(tmp_path, "w", encoding="utf-8") as handle:
                    json.dump(meta, handle, indent=2)
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
            meta = _read_manifest(str(plugin_path))
            if meta.get("id", entry) == plugin_id:
                meta["enabled"] = bool(enabled)
                tmp_path = manifest_path.with_suffix(".json.tmp")
                with open(tmp_path, "w", encoding="utf-8") as handle:
                    json.dump(meta, handle, indent=2)
                os.replace(tmp_path, manifest_path)
                return True
        except (OSError, json.JSONDecodeError):
            continue
    return False


def plugins_dir_path():
    """Return the plugins directory path (for 'Open folder' button)."""
    _ensure_dir()
    return str(PLUGINS_DIR)
