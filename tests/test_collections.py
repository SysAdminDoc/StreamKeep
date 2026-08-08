"""V168: a recording can belong to N collections with one copy on disk.

The season-folder layout gives a recording exactly one home, so something
belonging to two playlists had to be duplicated on disk or arbitrarily assigned
to one of them. Membership is now many-to-many, and the export gives each
collection an additional *entry* pointing at the same bytes.

Copying is deliberately not a fallback strategy: duplicating the bytes is the
problem this removes, so a home that can be neither hardlinked nor pointed at is
reported refused rather than quietly costing a second copy.
"""

import os

import pytest

from streamkeep import tags
from streamkeep.integrations import media_server


@pytest.fixture
def tagdb(tmp_path, monkeypatch):
    monkeypatch.setattr(tags, "DB_PATH", tmp_path / "tags.db")
    db = tags._connect()
    yield db
    db.close()


# ── Membership ──────────────────────────────────────────────────────

def test_a_recording_belongs_to_many_collections_at_once(tagdb):
    for name in ("Best of 2026", "Tutorials", "Watch Later"):
        tags.add_to_collection(tagdb, "/media/a.mp4", name)

    assert tags.get_collections_for_recording(tagdb, "/media/a.mp4") == [
        "Best of 2026", "Tutorials", "Watch Later",
    ]


def test_adding_to_one_collection_does_not_remove_the_others(tagdb):
    """The whole point: membership is additive, not a reassignment."""
    tags.add_to_collection(tagdb, "/media/a.mp4", "Tutorials")
    tags.add_to_collection(tagdb, "/media/a.mp4", "Watch Later")

    assert len(tags.get_collections_for_recording(tagdb, "/media/a.mp4")) == 2


def test_membership_is_idempotent(tagdb):
    tags.add_to_collection(tagdb, "/media/a.mp4", "Tutorials")
    tags.add_to_collection(tagdb, "/media/a.mp4", "Tutorials")

    assert tags.get_collection_members(tagdb, "Tutorials") == ["/media/a.mp4"]


def test_removing_one_membership_leaves_the_rest(tagdb):
    tags.add_to_collection(tagdb, "/media/a.mp4", "Tutorials")
    tags.add_to_collection(tagdb, "/media/a.mp4", "Watch Later")

    assert tags.remove_from_collection(tagdb, "/media/a.mp4", "Tutorials") is True

    assert tags.get_collections_for_recording(tagdb, "/media/a.mp4") == [
        "Watch Later",
    ]


def test_members_keep_the_operators_order(tagdb):
    tags.add_to_collection(tagdb, "/media/c.mp4", "Ordered", position=0)
    tags.add_to_collection(tagdb, "/media/a.mp4", "Ordered", position=1)
    tags.add_to_collection(tagdb, "/media/b.mp4", "Ordered", position=2)

    assert tags.get_collection_members(tagdb, "Ordered") == [
        "/media/c.mp4", "/media/a.mp4", "/media/b.mp4",
    ], "ordering must be the position, not the path"


def test_deleting_a_collection_does_not_touch_the_recordings(tagdb):
    tags.add_to_collection(tagdb, "/media/a.mp4", "Tutorials")
    tags.add_to_collection(tagdb, "/media/a.mp4", "Keep")

    assert tags.delete_collection(tagdb, "Tutorials") is True

    assert tags.get_collections_for_recording(tagdb, "/media/a.mp4") == ["Keep"]
    assert [name for name, _count in tags.get_all_collections(tagdb)] == ["Keep"]


def test_counts_are_reported_per_collection(tagdb):
    tags.add_to_collection(tagdb, "/media/a.mp4", "Two")
    tags.add_to_collection(tagdb, "/media/b.mp4", "Two")
    tags.get_or_create_collection(tagdb, "Empty")

    assert tags.get_all_collections(tagdb) == [("Empty", 0), ("Two", 2)]


def test_an_empty_name_is_refused(tagdb):
    for bad in ("", "   ", None):
        with pytest.raises(ValueError):
            tags.get_or_create_collection(tagdb, bad)


def test_removing_from_a_collection_that_does_not_exist_is_not_an_error(tagdb):
    assert tags.remove_from_collection(tagdb, "/media/a.mp4", "Nope") is False
    assert tags.delete_collection(tagdb, "Nope") is False


def test_a_moved_recording_keeps_every_membership(tmp_path, monkeypatch):
    """Membership is keyed by path, so a re-template would otherwise drop the
    recording out of every collection silently."""
    monkeypatch.setattr(tags, "DB_PATH", tmp_path / "tags.db")
    db = tags._connect()
    try:
        tags.add_to_collection(db, "/old/a.mp4", "Tutorials")
        tags.add_to_collection(db, "/old/a.mp4", "Watch Later")
    finally:
        db.close()

    moved = tags.relocate_collection_memberships("/old/a.mp4", "/new/a.mp4")

    db = tags._connect()
    try:
        assert moved == 2
        assert tags.get_collections_for_recording(db, "/new/a.mp4") == [
            "Tutorials", "Watch Later",
        ]
        assert tags.get_collections_for_recording(db, "/old/a.mp4") == []
    finally:
        db.close()


def test_a_rebuilt_tags_database_still_has_the_collection_tables(tmp_path):
    """A rebuild that omitted them would leave the app hitting a missing table."""
    target = tmp_path / "rebuilt.db"
    tags.build_rebuilt_tags_database(target, [])

    from streamkeep.sqlite_runtime import connect as sqlite_connect
    db = sqlite_connect(str(target))
    try:
        names = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        db.close()

    assert {"collections", "collection_members"} <= names


# ── The command is actually reachable ───────────────────────────────

def test_the_collections_command_is_registered_in_both_trigger_sets():
    """A subcommand argparse knows about is still unreachable without these.

    ``StreamKeep.py`` decides CLI-vs-GUI from its own hardcoded trigger set
    before importing anything Qt-backed, and ``cli.has_cli_args`` keeps a second
    one. A command missing from either silently launches the GUI instead --
    which is what happened here: the handler worked in-process while the
    launcher sat in an event loop waiting for a window.
    """
    import re
    from pathlib import Path

    from streamkeep import cli

    root = Path(cli.__file__).resolve().parents[1]
    launcher = (root / "StreamKeep.py").read_text(encoding="utf-8")
    triggers = re.search(
        r"cli_triggers = \{(.*?)\}", launcher, re.DOTALL,
    ).group(1)
    assert '"collections"' in triggers, (
        "StreamKeep.py would start the GUI for `collections`"
    )

    module_triggers = re.search(
        r"cli_triggers = \{(.*?)\}",
        (root / "streamkeep" / "cli.py").read_text(encoding="utf-8"),
        re.DOTALL,
    ).group(1)
    assert '"collections"' in module_triggers


def test_the_cli_reports_membership_across_collections(tmp_path, monkeypatch,
                                                       capsys):
    import types

    from streamkeep import cli

    monkeypatch.setattr(tags, "DB_PATH", tmp_path / "tags.db")

    for name in ("Tutorials", "Watch Later"):
        rc = cli._run_collections(types.SimpleNamespace(
            collections_command="add", path="/media/a.mp4", name=name,
            position=None,
        ))
        assert rc == 0
    out = capsys.readouterr().out
    assert "Now in 2 collection(s)" in out

    rc = cli._run_collections(types.SimpleNamespace(
        collections_command="of", path="/media/a.mp4", json=False,
    ))
    assert rc == 0
    listed = capsys.readouterr().out
    assert "Tutorials" in listed and "Watch Later" in listed


def test_the_cli_refuses_an_empty_collection_name(tmp_path, monkeypatch, capsys):
    import types

    from streamkeep import cli

    monkeypatch.setattr(tags, "DB_PATH", tmp_path / "tags.db")

    rc = cli._run_collections(types.SimpleNamespace(
        collections_command="add", path="/media/a.mp4", name="   ",
        position=None,
    ))

    assert rc == 2
    assert "Refused" in capsys.readouterr().out


def test_removing_a_membership_that_is_absent_exits_nonzero(tmp_path,
                                                           monkeypatch):
    import types

    from streamkeep import cli

    monkeypatch.setattr(tags, "DB_PATH", tmp_path / "tags.db")

    rc = cli._run_collections(types.SimpleNamespace(
        collections_command="remove", path="/media/a.mp4", name="Nope",
    ))

    assert rc == 1


# ── The export keeps one copy ───────────────────────────────────────

def _plan(library, destination):
    return media_server.MediaImportPlan(
        media_path=str(library / "src.mp4"),
        destination=str(destination),
        nfo_path=str(destination.with_suffix(".nfo")),
        library_path=str(library),
        channel="Chan", title="Title", year="2026", episode=1,
        layout_mode="seasoned",
    )


def test_the_primary_home_is_the_existing_season_layout(tmp_path):
    library = tmp_path / "lib"
    destination = library / "Chan" / "Season 2026" / "Chan - S2026E01 - Title.mp4"

    homes = media_server.plan_collection_homes(
        _plan(library, destination), ["Tutorials", "Watch Later"],
    )

    assert homes[0].is_primary
    assert homes[0].destination == str(destination), (
        "the season-folder layout must be unchanged"
    )
    assert [home.collection for home in homes[1:]] == ["Tutorials", "Watch Later"]


def test_a_collection_home_lands_under_a_collections_directory(tmp_path):
    library = tmp_path / "lib"
    destination = library / "Chan" / "Season 2026" / "ep.mp4"

    homes = media_server.plan_collection_homes(_plan(library, destination), ["Best/Of"])

    home = homes[1]
    assert "Collections" in home.destination
    assert "Best_Of" in home.destination, "the name must be path-sanitised"
    assert home.destination.endswith("ep.mp4")


def test_duplicate_and_blank_collection_names_are_collapsed(tmp_path):
    library = tmp_path / "lib"
    destination = library / "ep.mp4"

    homes = media_server.plan_collection_homes(
        _plan(library, destination), ["Same", "Same", "", "   ", None],
    )

    assert len(homes) == 2, [h.collection for h in homes]


def test_secondary_homes_are_hardlinks_to_the_one_copy(tmp_path):
    """N homes, one set of bytes -- verified by inode, not by file count."""
    library = tmp_path / "lib"
    destination = library / "Chan" / "Season 2026" / "ep.mp4"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"video-bytes")

    homes = media_server.materialize_collection_homes(
        media_server.plan_collection_homes(
            _plan(library, destination), ["Tutorials", "Watch Later"],
        )
    )

    secondaries = [home for home in homes if not home.is_primary]
    assert [home.strategy for home in secondaries] == ["hardlink", "hardlink"]
    primary_inode = os.stat(destination).st_ino
    for home in secondaries:
        assert os.path.isfile(home.destination)
        assert os.stat(home.destination).st_ino == primary_inode, (
            "a second copy of the bytes was made"
        )


def test_a_strm_pointer_is_written_when_hardlinking_is_impossible(tmp_path,
                                                                 monkeypatch):
    """Crossing a filesystem is the ordinary reason a hardlink fails."""
    library = tmp_path / "lib"
    destination = library / "ep.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"video-bytes")

    def refuse_link(src, dst):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(media_server.os, "link", refuse_link)

    homes = media_server.materialize_collection_homes(
        media_server.plan_collection_homes(_plan(library, destination), ["Tutorials"])
    )

    home = homes[1]
    assert home.strategy == "strm"
    assert home.destination.endswith(".strm")
    assert "cross-device" in home.reason
    with open(home.destination, encoding="utf-8") as handle:
        assert handle.read().strip() == str(destination), (
            "the pointer must name the one real copy"
        )


def test_a_copy_is_never_a_fallback(tmp_path, monkeypatch):
    """Duplicating the bytes is the problem V168 removes."""
    library = tmp_path / "lib"
    destination = library / "ep.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"video-bytes")

    monkeypatch.setattr(
        media_server.os, "link",
        lambda src, dst: (_ for _ in ()).throw(OSError(18, "cross-device")),
    )
    copied = []
    monkeypatch.setattr(media_server.shutil, "copy2",
                        lambda *a, **k: copied.append(a))

    media_server.materialize_collection_homes(
        media_server.plan_collection_homes(_plan(library, destination), ["Tutorials"])
    )

    assert copied == [], "the bytes were duplicated instead of pointed at"


def test_a_home_that_cannot_be_created_is_reported_refused(tmp_path, monkeypatch):
    library = tmp_path / "lib"
    destination = library / "ep.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"video-bytes")

    monkeypatch.setattr(
        media_server.os, "link",
        lambda src, dst: (_ for _ in ()).throw(OSError(18, "cross-device")),
    )

    real_open = open

    def refuse_strm(path, *args, **kwargs):
        if str(path).endswith(".strm"):
            raise OSError(13, "Permission denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", refuse_strm)

    homes = media_server.materialize_collection_homes(
        media_server.plan_collection_homes(_plan(library, destination), ["Tutorials"])
    )

    assert homes[1].strategy == "refused"
    assert "cannot hardlink" in homes[1].reason
    assert "Permission denied" in homes[1].reason


def test_an_occupied_collection_home_is_refused_not_overwritten(tmp_path):
    library = tmp_path / "lib"
    destination = library / "ep.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"video-bytes")

    planned = media_server.plan_collection_homes(
        _plan(library, destination), ["Tutorials"],
    )
    occupied = planned[1].destination
    os.makedirs(os.path.dirname(occupied), exist_ok=True)
    with open(occupied, "w", encoding="utf-8") as handle:
        handle.write("someone else's file")

    homes = media_server.materialize_collection_homes(planned)

    assert homes[1].strategy == "refused"
    with open(occupied, encoding="utf-8") as handle:
        assert handle.read() == "someone else's file"


def test_materialize_requires_a_primary():
    with pytest.raises(ValueError):
        media_server.materialize_collection_homes([
            media_server.CollectionHome("Tutorials", "/x/y.mp4", "hardlink"),
        ])


# ── The export says which strategy it used ──────────────────────────

def test_the_strategy_is_described_per_home(tmp_path):
    library = tmp_path / "lib"
    destination = library / "ep.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"video-bytes")

    homes = media_server.materialize_collection_homes(
        media_server.plan_collection_homes(_plan(library, destination), ["Tutorials"])
    )
    lines = media_server.describe_collection_homes(homes)

    assert "primary layout: primary" in lines[0]
    assert lines[1].startswith("Tutorials: hardlink")


def test_the_export_result_carries_the_strategies_not_only_a_log_line(tmp_path):
    """A log line is not something a UI or CLI can render."""
    out_dir = tmp_path / "rec"
    out_dir.mkdir()
    (out_dir / "video.mp4").write_bytes(b"x" * 2048)
    library = tmp_path / "lib"
    library.mkdir()

    config = {"library_path": str(library), "layout_mode": "flat"}
    result = media_server.materialize_media_import(
        config, out_dir, None, collections=["Tutorials", "Watch Later"],
    )

    assert result["primary_strategy"] in media_server.HOME_STRATEGIES + ("copy",)
    assert len(result["collection_homes"]) == 3, "primary plus two collections"
    assert len(result["collection_strategies"]) == 3
    assert any("Tutorials" in line for line in result["collection_strategies"])


def test_an_export_without_collections_reports_no_homes(tmp_path):
    out_dir = tmp_path / "rec"
    out_dir.mkdir()
    (out_dir / "video.mp4").write_bytes(b"x" * 2048)
    library = tmp_path / "lib"
    library.mkdir()

    result = media_server.materialize_media_import(
        {"library_path": str(library), "layout_mode": "flat"}, out_dir, None,
    )

    assert result["collection_homes"] == []
    assert result["collection_strategies"] == []
    assert result["primary_strategy"] in ("hardlink", "copy")


# ── V219: both outputs of a collection home are guarded ──────────────

def _blocked_link(monkeypatch):
    """Make os.link fail so materialization takes the .strm fallback."""
    def _refuse(*_args, **_kwargs):
        raise OSError("cross-device link not permitted")
    monkeypatch.setattr(media_server.os, "link", _refuse)


def test_a_foreign_strm_at_the_home_path_is_refused_not_overwritten(
    tmp_path, monkeypatch,
):
    """The pointer path was unguarded while the media path was refused.

    ``materialize_collection_homes`` writes either a hardlink at the media path
    or a ``.strm`` beside it. Only the media path was checked, so a re-export
    truncated whatever was at the pointer path (V219).
    """
    library = tmp_path / "lib"
    destination = library / "ep.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"video-bytes")

    planned = media_server.plan_collection_homes(
        _plan(library, destination), ["Tutorials"],
    )
    strm = os.path.splitext(planned[1].destination)[0] + ".strm"
    os.makedirs(os.path.dirname(strm), exist_ok=True)
    with open(strm, "w", encoding="utf-8") as handle:
        handle.write("someone else's playlist\nsecond line\n")

    _blocked_link(monkeypatch)
    homes = media_server.materialize_collection_homes(planned)

    assert homes[1].strategy == "refused"
    assert ".strm" in homes[1].reason
    with open(strm, encoding="utf-8") as handle:
        assert handle.read() == "someone else's playlist\nsecond line\n"


def test_re_exporting_refreshes_our_own_pointer(tmp_path, monkeypatch):
    """A pointer this module wrote must be replaceable, e.g. after a move."""
    library = tmp_path / "lib"
    destination = library / "ep.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"video-bytes")
    planned = media_server.plan_collection_homes(
        _plan(library, destination), ["Tutorials"],
    )

    _blocked_link(monkeypatch)
    first = media_server.materialize_collection_homes(planned)
    assert first[1].strategy == "strm"

    second = media_server.materialize_collection_homes(planned)
    assert second[1].strategy == "strm", (
        "our own pointer must be refreshable, not treated as a blocker"
    )
    with open(second[1].destination, encoding="utf-8") as handle:
        assert handle.read().strip() == str(destination)


def test_a_successful_hardlink_removes_a_stale_pointer(tmp_path, monkeypatch):
    """One recording must not appear twice in a collection folder.

    An export that fell back to a pointer, followed by one that could hardlink,
    left both behind -- the second aimed at a stale primary (V219).
    """
    library = tmp_path / "lib"
    destination = library / "ep.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"video-bytes")
    planned = media_server.plan_collection_homes(
        _plan(library, destination), ["Tutorials"],
    )

    _blocked_link(monkeypatch)
    fallback = media_server.materialize_collection_homes(planned)
    strm = fallback[1].destination
    assert os.path.exists(strm)

    monkeypatch.undo()
    linked = media_server.materialize_collection_homes(planned)

    assert linked[1].strategy == "hardlink"
    assert not os.path.exists(strm), "the stale .strm pointer survived"
    assert "stale .strm" in linked[1].reason
    home_dir = os.path.dirname(linked[1].destination)
    entries = sorted(os.listdir(home_dir))
    assert len(entries) == 1, f"one recording, one entry; found {entries}"
