from collections import deque
from unittest import mock

from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtWidgets import QTableView

from streamkeep import db
from streamkeep.models import HistoryEntry
from streamkeep.ui.history_model import HistoryTableModel
from streamkeep.ui.thumb_loader import ThumbLoader


MAX_TENFOLD_COST_RATIO = 25


def _seed_history(count, *, start=0):
    connection = db._connect()
    try:
        rows = []
        for index in range(start, start + count):
            marker = " needle" if index % 10_000 == 0 else ""
            rows.append((
                f"2026-01-{(index % 28) + 1:02d} 12:00",
                "Twitch" if index % 2 else "YouTube",
                f"Archive item {index}{marker}",
                f"channel-{index % 20}",
                "1080p",
                "1.0 GB",
                f"C:/archive/{index}",
                f"https://example.com/{index}",
                0, 0, 0.0, "[]",
            ))
        connection.executemany(
            """
            INSERT INTO history
                (date, platform, title, channel, quality, size, path, url,
                 favorite, watched, watch_position_secs, bookmarks)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def test_100k_archive_uses_bounded_snapshot_paging_and_fts(
    tmp_path, monkeypatch, qt_application, measure_process_cost,
):
    monkeypatch.setattr(db, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "library.db")
    db.init_db()
    _seed_history(10_000)

    small_open_cost, _ = measure_process_cost(
        lambda: HistoryTableModel(page_size=128),
    )
    small_model = HistoryTableModel(page_size=128)
    small_filter_cost, _ = measure_process_cost(
        lambda: small_model.set_filter("needle"),
    )

    _seed_history(90_000, start=10_000)

    open_cost, model = measure_process_cost(
        lambda: HistoryTableModel(page_size=128),
    )
    assert model.total_count == 100_000
    assert model.loaded_count == 128
    assert open_cost < small_open_cost * MAX_TENFOLD_COST_RATIO

    view = QTableView()
    view.setModel(model)
    selected_id = model.entry_at(10).db_id
    view.selectionModel().select(
        model.index(10, 0),
        QItemSelectionModel.SelectionFlag.Select
        | QItemSelectionModel.SelectionFlag.Rows,
    )
    model.fetchMore()
    assert model.loaded_count == 256
    assert model.entry_at(10).db_id == selected_id
    assert [index.row() for index in view.selectionModel().selectedRows()] == [10]

    # A row added after the model snapshot cannot reshuffle existing pages.
    new_id = db.save_history_entry(HistoryEntry(title="post-snapshot").to_dict())
    while model.canFetchMore() and model.loaded_count < 512:
        model.fetchMore()
    assert model.row_for_id(new_id) == -1

    filter_cost, _ = measure_process_cost(lambda: model.set_filter("needle"))
    assert model.total_count == 10
    assert model.loaded_count == 10
    assert all("needle" in model.entry_at(row).title for row in range(10))
    assert filter_cost < small_filter_cost * MAX_TENFOLD_COST_RATIO

    model.set_filter("")
    assert model.entry_at(0).db_id == new_id

    connection = db._connect(readonly=True)
    try:
        keyset_plan = " ".join(
            row[3] for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM history "
                "WHERE id <= ? AND id < ? ORDER BY id DESC LIMIT ?",
                (100_000, 100_001, 128),
            )
        )
        if db._fts5_enabled():
            search_plan = " ".join(
                row[3] for row in connection.execute(
                    "EXPLAIN QUERY PLAN SELECT h.* FROM history h "
                    "JOIN history_fts ON history_fts.rowid = h.id "
                    "WHERE history_fts MATCH ? AND h.id <= ? ORDER BY h.id DESC LIMIT ?",
                    ('"needle"*', 100_000, 128),
                )
            )
        else:
            search_plan = " ".join(
                row[3] for row in connection.execute(
                    "EXPLAIN QUERY PLAN SELECT h.* FROM history h "
                    "WHERE h.title LIKE ? COLLATE NOCASE AND h.id <= ? "
                    "ORDER BY h.id DESC LIMIT ?",
                    ("%needle%", 100_000, 128),
                )
            )
    finally:
        connection.close()
    assert "INTEGER PRIMARY KEY" in keyset_plan
    if db._fts5_enabled():
        assert "VIRTUAL TABLE INDEX" in search_plan
    else:
        assert "history_fts" not in search_plan
        assert "INTEGER PRIMARY KEY" in search_plan


def test_history_aggregates_and_exact_lookup_stay_in_sqlite(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(db, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "library.db")
    db.init_db()
    _seed_history(2_000)

    summary = db.history_summary()
    assert summary["total"] == 2_000
    assert summary["top_platform"][1] == 1_000
    assert summary["top_channel"][1] == 100
    assert db.find_latest_history(channel="CHANNEL-3")["channel"] == "channel-3"

    analytics = db.history_analytics("2026-01-15")
    assert analytics["total"] > 0
    assert analytics["size_gb"] == analytics["total"]
    assert len(analytics["platforms"]) == 2
    assert len(analytics["channels"]) == 8


def test_thumbnail_loader_cancels_stale_page_work(qt_application):
    loader = ThumbLoader()
    keep_worker = mock.Mock()
    stale_worker = mock.Mock()
    loader._wanted = {1, 2}
    loader._pending = deque([(1, "keep.mp4"), (2, "stale.mp4")])
    loader._in_flight = {1: keep_worker, 2: stale_worker}

    loader.retain({1})

    assert list(loader._pending) == [(1, "keep.mp4")]
    assert loader._wanted == {1}
    keep_worker.cancel.assert_not_called()
    stale_worker.cancel.assert_called_once_with()
