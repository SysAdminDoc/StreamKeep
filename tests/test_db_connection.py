import tempfile
from pathlib import Path
from unittest import mock

import pytest

from streamkeep import db


def test_nested_pooled_lease_defers_rollback_to_outermost_context():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        database_path = root / "library.db"
        with (
            mock.patch.object(db, "CONFIG_DIR", root),
            mock.patch.object(db, "DB_PATH", database_path),
        ):
            db.init_db()
            try:
                with pytest.raises(RuntimeError, match="outer failure"):
                    with db._connect() as outer:
                        assert hasattr(outer, "_state")
                        outer.execute(
                            "INSERT INTO history (title) VALUES (?)",
                            ("outer",),
                        )
                        with db._connect() as inner:
                            assert inner._state is outer._state
                            inner.execute(
                                "INSERT INTO history (title) VALUES (?)",
                                ("inner",),
                            )
                        assert outer.execute(
                            "SELECT COUNT(*) FROM history"
                        ).fetchone()[0] == 2
                        raise RuntimeError("outer failure")

                reader = db._connect(readonly=True)
                try:
                    assert reader.execute(
                        "SELECT COUNT(*) FROM history"
                    ).fetchone()[0] == 0
                finally:
                    reader.close()
            finally:
                db.close_connections()
