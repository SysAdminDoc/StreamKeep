from types import SimpleNamespace
from unittest import mock

from streamkeep import db, local_server
from streamkeep.db import history, queue, schema
from streamkeep.postprocess import PostProcessor
from streamkeep.server import routes, static_assets
from streamkeep.ui.tabs import settings_presets


#: Modules that must *implement* their exports rather than forward them, and
#: the functions that prove it. Identity with the facade is deliberately not
#: asserted: that is what a re-export shim satisfies, and asserting it made
#: the monolith a tested contract instead of something to decompose (V163).
_OWNED_BY = {
    "streamkeep.db.projections": (
        "failed_job_public_view", "_row_to_failed_job_dict", "_circuit_engine",
        "_queue_row_to_dict", "_row_to_history_dict", "_row_to_monitor_dict",
        "_canonical_history_entry", "_canonical_tombstone_fields",
    ),
    "streamkeep.db.schema": (
        "migrate_database", "_apply_schema", "_migrate_retry_v10",
        "_migrate_circuit_engine_v23", "_apply_upload_schema",
    ),
    "streamkeep.db.history_actions": (
        "_append_history_action_in_connection", "_history_action_record",
        "_history_action_identity_key", "_replay_history_actions_in_connection",
        "_compact_history_actions_in_connection",
        "_delete_history_rows_in_connection",
    ),
    "streamkeep.db.primitives": (
        "_utc_now_iso", "_sqlite_table_exists", "_utc_iso", "_iso_epoch",
    ),
    "streamkeep.db.tombstones": (
        "_upsert_tombstone_in_connection", "_find_tombstone_in_connection",
        "_normalize_tombstone_reason",
    ),
    "streamkeep.db.publishing": ("_publishing_id", "_new_publishing_id"),
    "streamkeep.db.connection": (
        "_connect", "_check_schema_version", "close_connections",
        "_profile_cache_key", "_profile_database_path",
        "_close_stale_profile_connections", "_connection_pool",
    ),
    "streamkeep.server.auth": (
        "generate_bearer_token", "valid_bearer_token",
        "TokenGrant", "TokenStore", "PairingStore", "ReplayStore",
    ),
}

#: The monolith may only shrink. Lower this when work moves out of it; a
#: KeyError-free pass with a smaller file means the ratchet needs updating.
_LEGACY_LINE_CEILING = 4905


def test_each_domain_module_implements_what_it_exports():
    """Ownership, not forwarding: the definition must live in the module."""
    import importlib

    for module_name, functions in _OWNED_BY.items():
        module = importlib.import_module(module_name)
        for name in functions:
            owned = getattr(module, name)
            assert owned.__module__ == module_name, (
                f"{module_name}.{name} is defined in {owned.__module__}; "
                "a domain module must own its statements, not re-export them"
            )


def test_the_legacy_module_only_ever_shrinks():
    """A ratchet, so the split cannot quietly regrow the monolith."""
    from pathlib import Path

    legacy = Path(db._implementation.__file__)
    count = len(legacy.read_text(encoding="utf-8").splitlines())

    assert count <= _LEGACY_LINE_CEILING, (
        f"{legacy.name} grew to {count} lines against a ceiling of "
        f"{_LEGACY_LINE_CEILING}; new behaviour belongs in a domain module"
    )
    assert count >= _LEGACY_LINE_CEILING - 200, (
        f"{legacy.name} is down to {count} lines - lower "
        f"_LEGACY_LINE_CEILING to {count} so the ratchet keeps holding"
    )


def test_the_facade_still_serves_the_whole_surface():
    """Decomposition must not break callers or patch-based tests."""
    assert history.load_history is db.load_history
    assert queue.enqueue_queue_job is db.enqueue_queue_job
    assert schema.migrate_database is not db.init_db
    # Moved definitions stay reachable from the facade callers already use,
    # even though the legacy module no longer re-exports them.
    assert callable(db.failed_job_public_view)
    assert callable(db._circuit_engine)
    assert callable(db._migrate_retry_v10)
    assert callable(db._append_history_action_in_connection)
    assert callable(db._utc_now_iso)


def test_the_package_has_no_import_cycle_back_into_the_monolith():
    """The domain modules must not import the connection-owning module.

    That is the constraint that lets them own their statements at all: the
    moment one of them imports _legacy at module scope, the extraction has to
    be undone.
    """
    import ast
    from pathlib import Path

    root = Path(db._implementation.__file__).parent
    for name in ("schema", "history_actions", "primitives", "projections",
                 "tombstones", "publishing", "connection"):
        tree = ast.parse((root / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level and node.module:
                assert node.module != "_legacy", (
                    f"db/{name}.py imports _legacy at module scope"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.endswith("_legacy"), name

    original_connect = db._connect
    with mock.patch.object(db, "_connect") as patched:
        assert db._connect is patched
        assert db._implementation._connect is patched
    assert db._connect is original_connect
    assert db._implementation._connect is original_connect


def test_patching_the_facade_reaches_the_module_that_defines_the_name():
    """Identity with the facade is not reach.

    Before V163 the package was one module, so patching ``db.X`` rebound the
    single binding every caller resolved. The split gave each moved name two
    bindings -- one in the module that defines it, one in each module that
    imported it -- and the facade wrote to whichever it found first. The
    identity assertions above still passed while the code under test went on
    calling the real function, which is the failure mode where a check
    authorises everything and reads like one that ran.

    ``_utc_now_iso`` is the witness: defined in ``primitives``, imported by
    ``tombstones``, and reachable through the facade.
    """
    from streamkeep.db import primitives, tombstones

    with mock.patch.object(db, "_utc_now_iso", return_value="PATCHED"):
        assert db._utc_now_iso() == "PATCHED", "the facade itself"
        assert primitives._utc_now_iso() == "PATCHED", "the defining module"
        assert tombstones._utc_now_iso() == "PATCHED", "an importing sibling"

    # And every binding is restored, or the patch leaks into later tests.
    assert db._utc_now_iso() != "PATCHED"
    assert primitives._utc_now_iso() != "PATCHED"
    assert tombstones._utc_now_iso() != "PATCHED"


def test_patching_db_path_redirects_the_code_that_actually_opens_the_file():
    """The heaviest patch contract in the suite, asserted end to end.

    175 test sites redirect the library with ``patch.object(db, "DB_PATH")``.
    Once the connection layer moved out of ``_legacy`` that name had two
    bindings, and only an assertion that a real connection lands on the
    patched path can tell the difference between a redirect and a test
    quietly writing to the operator's own library.
    """
    import tempfile
    from pathlib import Path

    from streamkeep.db import connection

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "redirected.db"
        with mock.patch.object(db, "DB_PATH", target):
            assert connection.DB_PATH == target, "the module that opens it"
            assert db._implementation.DB_PATH == target, "and the legacy one"

            handle = db._connect()
            try:
                handle.execute("CREATE TABLE probe (x INTEGER)")
                handle.commit()
            finally:
                handle.close()
                db.close_connections()

        assert target.is_file(), "the connection went somewhere else entirely"

    assert connection.DB_PATH != target
    assert db._implementation.DB_PATH != target


def test_a_forwarding_shim_is_not_given_a_binding_of_its_own():
    """Writing into a shim would freeze its forward at today's value.

    ``publishing`` resolves part of its surface through ``__getattr__``. If a
    patch wrote those names into its ``__dict__``, unpatching would restore a
    *static copy* of the forwarded value and the shim would stop tracking the
    module it forwards to.
    """
    from streamkeep.db import publishing

    forwarded = "publish_recording"  # served by __getattr__, not defined here
    assert forwarded not in vars(publishing)

    with mock.patch.object(db, forwarded):
        pass

    assert forwarded not in vars(publishing), (
        "the patch gave the shim a binding; its forwarding is now frozen"
    )


#: ``server/_legacy.py`` may only shrink, same ratchet as the db monolith.
_SERVER_LEGACY_LINE_CEILING = 2548


def test_the_server_legacy_module_only_ever_shrinks():
    from pathlib import Path

    from streamkeep.server import _legacy as server_legacy

    legacy = Path(server_legacy.__file__)
    count = len(legacy.read_text(encoding="utf-8").splitlines())

    assert count <= _SERVER_LEGACY_LINE_CEILING, (
        f"server/{legacy.name} grew to {count} lines against a ceiling of "
        f"{_SERVER_LEGACY_LINE_CEILING}; new behaviour belongs in a sibling"
    )
    assert count >= _SERVER_LEGACY_LINE_CEILING - 200, (
        f"server/{legacy.name} is down to {count} lines - lower "
        f"_SERVER_LEGACY_LINE_CEILING to {count} so the ratchet keeps holding"
    )


def test_the_server_auth_module_does_not_import_the_monolith():
    """The constraint that lets it own its statements at all."""
    import ast
    from pathlib import Path

    from streamkeep.server import _legacy as server_legacy

    source = (Path(server_legacy.__file__).parent / "auth.py").read_text(
        encoding="utf-8",
    )
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level and node.module:
            assert node.module != "_legacy", "server/auth.py imports _legacy"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.endswith("_legacy")


def test_patching_the_server_facade_reaches_the_module_that_defines_the_name():
    """Same contract as the db facade, and it broke the same way.

    Moving the auth layer out of ``server/_legacy`` gave its names two bindings.
    A facade that wrote to only the first left ``server.auth`` -- where the code
    actually lives -- still running the original, while reading the name back
    through the facade returned the patch and made it look applied.
    """
    from streamkeep.server import _legacy as server_legacy
    from streamkeep.server import auth

    with mock.patch.object(local_server, "generate_bearer_token",
                           return_value="PATCHED"):
        assert local_server.generate_bearer_token() == "PATCHED", "the facade"
        assert auth.generate_bearer_token() == "PATCHED", "the defining module"
        assert server_legacy.generate_bearer_token() == "PATCHED", "the importer"

    for module in (auth, server_legacy):
        assert module.generate_bearer_token() != "PATCHED", "restore leaked"


def test_the_scope_vocabulary_is_owned_by_auth_and_reachable_from_the_facade():
    """Constants have no ``__module__``, so ownership is asserted by identity."""
    from streamkeep.server import auth

    assert local_server.ALL_SCOPES is auth.ALL_SCOPES
    assert auth.ALL_SCOPES == {
        auth.SCOPE_STATUS, auth.SCOPE_QUEUE, auth.SCOPE_RECOVERY,
    }
    assert local_server.TOKEN_TTL_MAX_SECONDS == auth.TOKEN_TTL_MAX_SECONDS


def test_server_facade_exposes_route_table_and_external_web_ui():
    assert local_server.PRODUCT_REST_PATHS == set(routes.ROUTE_TABLE)
    html = static_assets.load_web_ui()
    assert html == local_server._WEB_UI_HTML
    assert "{{web_i18n}}" in html


def test_web_remote_template_is_mobile_accessible_and_has_error_states():
    html = static_assets.load_web_ui()
    assert 'role="tablist"' in html
    assert 'role="tabpanel"' in html
    assert 'aria-selected="true"' in html
    assert 'aria-live="assertive"' in html
    assert 'aria-busy="true"' in html
    assert "button:focus-visible" in html
    assert "@media (max-width:600px)" in html
    assert 'autocomplete="one-time-code"' in html
    assert "message.className='status-region error'" in html
    assert 'type="button"' in html


class _PresetCombo:
    def __init__(self, current=""):
        self.items = []
        self.current = current

    def blockSignals(self, _blocked):
        return None

    def clear(self):
        self.items.clear()

    def addItem(self, label, userData=""):
        self.items.append((label, userData))

    def setCurrentIndex(self, index):
        self.current = self.items[index][1]

    def currentData(self):
        return self.current

    def findData(self, value):
        for index, (_label, data) in enumerate(self.items):
            if data == value:
                return index
        return -1


def test_settings_presets_are_owned_by_the_extracted_module():
    original = settings_presets._pp_snapshot()
    window = SimpleNamespace(
        _config={"pp_presets": {"Custom": {"extract_audio": True}}},
        pp_preset_combo=_PresetCombo(),
    )
    try:
        settings_presets._populate_pp_presets(window)
        assert [item[1] for item in window.pp_preset_combo.items] == [
            "",
            *settings_presets.BUILTIN_PRESETS,
            "Custom",
        ]

        window.pp_preset_combo.current = "Custom"
        settings_presets._on_pp_preset_selected(window)
        assert PostProcessor.extract_audio is True

        settings_presets._save_user_presets(
            window, {"Saved": {"convert_video": True}},
        )
        assert settings_presets._get_user_presets(window) == {
            "Saved": {"convert_video": True},
        }
    finally:
        settings_presets._pp_apply_snapshot(original)
