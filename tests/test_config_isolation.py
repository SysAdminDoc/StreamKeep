"""The suite must never read or write the operator's real config directory.

Stateful modules bind their paths from ``streamkeep.paths`` at import time, so
a module that captures ``CONFIG_DIR`` before ``conftest`` rebinds it — or one
that rebuilds a path from the platform default at call time — silently starts
operating on the real ``library.db``. These tests fail the moment that happens
again rather than after a suite run has already written to a live archive.
"""

import importlib
from pathlib import Path

from conftest import isolated_config_dir, real_config_dir, suite_started_at

from streamkeep import paths


# Every module that owns a durable path under the config directory. Add new
# entries here when a feature starts persisting state; the assertion below is
# what keeps that state out of the operator's profile during tests.
CONFIG_PATH_ATTRIBUTES = (
    ("streamkeep.db", "DB_PATH"),
    ("streamkeep.accounts", "DB_PATH"),
    ("streamkeep.auth_profiles", "AUTH_DIR"),
    ("streamkeep.bandwidth", "DB_PATH"),
    ("streamkeep.channel_stats", "DB_PATH"),
    ("streamkeep.cookies", "COOKIES_FILE"),
    ("streamkeep.declarative", "SOURCE_ADAPTERS_DIR"),
    ("streamkeep.notifications", "NOTIF_LOG"),
    ("streamkeep.notifications", "SECURITY_EVENT_LOG"),
    ("streamkeep.plugins", "PLUGINS_DIR"),
    ("streamkeep.search", "DB_PATH"),
    ("streamkeep.semantic", "DB_PATH"),
)

PATHS_MODULE_ATTRIBUTES = (
    "CONFIG_DIR",
    "CONFIG_FILE",
    "LOG_FILE",
    "LOG_FILE_BACKUP",
    "CRASH_LOG",
    "SERVER_REQUEST_LOG",
)


def _is_within(candidate, root) -> bool:
    try:
        Path(candidate).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return False
    return True


def test_paths_module_is_bound_to_the_isolated_directory():
    root = isolated_config_dir()
    for attribute in PATHS_MODULE_ATTRIBUTES:
        value = getattr(paths, attribute)
        assert _is_within(value, root), f"paths.{attribute} escaped isolation: {value}"


def test_no_stateful_module_path_points_into_the_real_config_directory():
    """The invariant that matters: nothing addresses the operator's profile.

    A test may legitimately redirect a module path at its own temp directory,
    so the assertion is on the real directory rather than on membership of the
    isolated one — ``test_paths_module_is_bound_to_the_isolated_directory``
    already proves the default binding.
    """
    real = real_config_dir()
    escaped = []
    for module_name, attribute in CONFIG_PATH_ATTRIBUTES:
        module = importlib.import_module(module_name)
        value = getattr(module, attribute)
        if _is_within(value, real):
            escaped.append(f"{module_name}.{attribute} = {value}")
    assert not escaped, (
        "config paths address the real profile: " + ", ".join(escaped)
    )


def test_no_earlier_test_left_a_module_path_rebound():
    """A test that redirects a module-level path must restore it.

    A leaked rebinding silently changes which database every later test reads,
    which is how an isolation regression hides in a green suite.
    """
    root = isolated_config_dir()
    leaked = []
    for module_name, attribute in CONFIG_PATH_ATTRIBUTES:
        module = importlib.import_module(module_name)
        value = getattr(module, attribute)
        if not _is_within(value, root):
            leaked.append(f"{module_name}.{attribute} = {value}")
    assert not leaked, (
        "module paths were left rebound by an earlier test: " + ", ".join(leaked)
    )


def test_runtime_derived_paths_follow_the_binding():
    root = isolated_config_dir()
    archive = paths.source_archive_path("https://example.com/watch?v=abc", create=False)
    assert _is_within(archive, root), archive


def test_the_real_config_directory_is_not_the_test_target():
    real = real_config_dir()
    assert Path(real).resolve() != Path(isolated_config_dir()).resolve()
    assert not _is_within(isolated_config_dir(), real)


def test_no_state_files_were_written_to_the_real_config_directory():
    """A pre-existing operator install legitimately holds these files.

    The check is therefore on modification time against the timestamp taken
    before the first StreamKeep import: nothing under the real directory may
    have been touched since the suite started.
    """
    real = Path(real_config_dir())
    if not real.is_dir():
        return
    started = suite_started_at()
    touched = []
    for name in ("library.db", "notifications.jsonl", "security-events.jsonl",
                 "crash.log", "streamkeep.log", "search.db", "semantic.db",
                 "config.json", "health.json"):
        candidate = real / name
        try:
            if candidate.exists() and candidate.stat().st_mtime > started:
                touched.append(name)
        except OSError:
            continue
    assert not touched, (
        "the suite wrote to the real config directory: " + ", ".join(touched)
    )
