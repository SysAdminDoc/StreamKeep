"""Leaf helpers shared by the database table-family modules (V163).

Two primitives with no table knowledge and no connection of their own. They
live here so ``schema`` and ``history_actions`` can use them without importing
the connection-owning module, which is what would make the package cyclic.
"""

from __future__ import annotations

from datetime import datetime, timezone


def _sqlite_table_exists(connection, table_name: str) -> bool:
    """Return whether a table exists without interpolating its name."""
    return bool(connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(table_name),),
    ).fetchone())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
