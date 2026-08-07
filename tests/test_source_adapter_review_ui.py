"""The Settings review surface for declarative source adapters (V147).

The gate itself lives in ``streamkeep.declarative`` and is covered by
``tests/test_declarative.py``. What is asserted here is the operator's half of
it: an unreviewed adapter has to be *visibly* inert and reviewable, the
approval dialog has to spell out the requests being approved, and cancelling
has to leave the adapter inert.
"""

from unittest import mock

import pytest

from streamkeep import declarative
from streamkeep.ui.tabs import settings_tools
from streamkeep.ui.tabs.settings_tools import SettingsToolsMixin
from tests.test_declarative import DEFINITION


class _FakeAdapterWindow(SettingsToolsMixin):
    """Handler host without the widgets, mirroring the config-import tests."""

    def __init__(self, adapter_dir):
        self._config = {}
        self._adapter_dir = adapter_dir
        self.logs = []
        self.statuses = []

    # The mixin reads diagnostics through this one seam, so pointing it at a
    # tmp_path directory keeps the test off the real adapters directory.
    def _source_adapter_reports(self):
        return declarative.declarative_adapter_diagnostics(
            directory=self._adapter_dir, config=self._config,
        )

    def _log(self, value):
        self.logs.append(value)

    def _set_status(self, message, tone):
        self.statuses.append((message, tone))


@pytest.fixture
def window(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "source_adapters"
    adapter_dir.mkdir()
    (adapter_dir / "example.yaml").write_text(DEFINITION, encoding="utf-8")
    monkeypatch.setattr(declarative, "SOURCE_ADAPTERS_DIR", adapter_dir)
    declarative.invalidate_registry_cache()
    return _FakeAdapterWindow(adapter_dir)


def _select(window, adapter_id):
    """Stand in for the table selection the real panel drives."""
    _report, rows = window._source_adapter_rows()
    window._source_adapter_rows_snapshot = rows
    match = next(row for row in rows if row["id"] == adapter_id)
    window._selected_source_adapter = lambda: match
    return match


def test_an_unreviewed_adapter_is_listed_as_needing_review(window):
    report, rows = window._source_adapter_rows()

    assert report["adapters"] == []
    assert [row["id"] for row in rows] == ["example-source"]
    assert rows[0]["reviewed"] is False
    # The row a reader sees names the hosts and the requests, not just an id.
    summary = window._source_adapter_request_summary(rows[0])
    assert "GET https://api.example.com/channels/{channel}/videos" in summary
    assert rows[0]["hosts"] == ["example.com"]


def test_the_approval_dialog_spells_out_every_request(window):
    _select(window, "example-source")

    with mock.patch.object(
        settings_tools, "ask_premium_confirmation", return_value=False
    ) as confirm, mock.patch.object(settings_tools, "_save_config") as save:
        window._on_source_adapter_approve_clicked()

    body = confirm.call_args.kwargs["body"]
    assert "Hosts it may contact: example.com" in body
    assert "GET https://api.example.com/channels/{channel}/videos" in body
    assert "resolve —" in body
    # Cancelling approves nothing and the adapter stays inert.
    save.assert_not_called()
    assert declarative.REVIEW_CONFIG_KEY not in window._config
    assert window.statuses[-1] == (
        "Source adapter review cancelled; it remains inert.", "idle",
    )


def test_approving_from_the_panel_activates_the_adapter(window):
    match = _select(window, "example-source")

    with mock.patch.object(
        settings_tools, "ask_premium_confirmation", return_value=True
    ), mock.patch.object(settings_tools, "_save_config", return_value=True):
        window._on_source_adapter_approve_clicked()

    assert window._config[declarative.REVIEW_CONFIG_KEY] == {
        "example-source": match["contract_fingerprint"],
    }
    report, rows = window._source_adapter_rows()
    assert [adapter["id"] for adapter in report["adapters"]] == ["example-source"]
    assert report["pending_review"] == []
    assert rows[0]["reviewed"] is True
    assert window.statuses[-1][1] == "success"
    assert "[ADAPTER] Approved" in window.logs[-1]


def test_revoking_from_the_panel_makes_it_inert_again(window):
    match = _select(window, "example-source")
    window._config = declarative.approve_source_adapter(
        "example-source", match["contract_fingerprint"], window._config,
    )
    _select(window, "example-source")

    with mock.patch.object(settings_tools, "_save_config", return_value=True):
        window._on_source_adapter_revoke_clicked()

    report, _rows = window._source_adapter_rows()
    assert report["adapters"] == []
    assert [item["id"] for item in report["pending_review"]] == ["example-source"]
    assert window.statuses[-1][1] == "warning"


def test_a_failed_save_does_not_report_an_approval(window):
    _select(window, "example-source")

    with mock.patch.object(
        settings_tools, "ask_premium_confirmation", return_value=True
    ), mock.patch.object(settings_tools, "_save_config", return_value=False):
        window._on_source_adapter_approve_clicked()

    assert window.statuses[-1] == (
        "Source adapter approval could not be saved.", "error",
    )
    assert window.logs == []


def test_the_contract_is_re_read_from_disk_before_the_dialog(window):
    """The panel may have sat open while the file changed underneath it."""
    _select(window, "example-source")
    path = window._adapter_dir / "example.yaml"
    path.write_text(
        DEFINITION.replace("api.example.com", "api.attacker.example"),
        encoding="utf-8",
    )

    with mock.patch.object(
        settings_tools, "ask_premium_confirmation", return_value=True
    ) as confirm, mock.patch.object(
        settings_tools, "_save_config", return_value=True
    ):
        window._on_source_adapter_approve_clicked()

    # The operator is shown, and approves, the host actually on disk.
    assert "api.attacker.example" in confirm.call_args.kwargs["body"]
    fingerprint = window._config[declarative.REVIEW_CONFIG_KEY]["example-source"]
    definitions, _errors = declarative.discover_source_adapters(
        window._adapter_dir, window._config,
    )
    assert [item.contract_fingerprint for item in definitions] == [fingerprint]


def test_an_approved_adapter_carries_its_contract_for_display(window):
    match = _select(window, "example-source")
    window._config = declarative.approve_source_adapter(
        "example-source", match["contract_fingerprint"], window._config,
    )

    _report, rows = window._source_adapter_rows()
    approved = rows[0]
    assert approved["reviewed"] is True
    # Revoking is only meaningful if the panel can still show what was approved.
    assert approved["operations"]
    assert "GET" in window._source_adapter_request_summary(approved)
