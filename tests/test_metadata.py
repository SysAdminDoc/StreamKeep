import ast
import time
from pathlib import Path

from streamkeep import db, tags
from streamkeep.importer import preview_adoption
from streamkeep.metadata import NFO_PARSE_ERROR, load_nfo_sidecar
from streamkeep.rebuild import plan_library_rebuild


_NESTED_ENTITY_NFO = b"""<!DOCTYPE movie [
<!ENTITY lol "lol">
<!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
<!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
]>
<movie><title>&lol2;</title></movie>
"""


def _write_hostile_recording(root):
    recording = root / "recording"
    recording.mkdir(parents=True)
    (recording / "video.mp4").write_bytes(b"media")
    nfo = recording / "video.nfo"
    nfo.write_bytes(_NESTED_ENTITY_NFO)
    return recording, nfo


def test_nfo_nested_entities_are_rejected_as_a_named_parse_issue(tmp_path):
    nfo = tmp_path / "hostile.nfo"
    nfo.write_bytes(_NESTED_ENTITY_NFO)
    issues = []

    started = time.perf_counter()
    assert load_nfo_sidecar(nfo, issue_fn=issues.append) == {}
    assert time.perf_counter() - started < 2
    assert issues
    assert issues[0]["kind"] == NFO_PARSE_ERROR
    assert "NFO sidecar parse error" in issues[0]["reason"]


def test_adoption_preview_reports_hostile_nfo_without_expanding_it(
    tmp_path, monkeypatch,
):
    database = tmp_path / "library.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    db.init_db()
    _recording, nfo = _write_hostile_recording(tmp_path / "library")

    started = time.perf_counter()
    plan = preview_adoption(tmp_path / "library", db_module=db)
    assert time.perf_counter() - started < 2

    assert len(plan.items) == 1
    item = plan.items[0]
    assert item["action"] == "conflict"
    issue = next(issue for issue in item["issues"] if issue["path"] == str(nfo))
    assert issue["kind"] == NFO_PARSE_ERROR
    assert "parse error" in issue["reason"]


def test_rebuild_preview_reports_hostile_nfo_without_expanding_it(
    tmp_path, monkeypatch,
):
    library_db = tmp_path / "library.db"
    tags_db = tmp_path / "tags.db"
    monkeypatch.setattr(db, "DB_PATH", library_db)
    monkeypatch.setattr(tags, "DB_PATH", tags_db)
    db.init_db()
    _recording, nfo = _write_hostile_recording(tmp_path / "library")

    started = time.perf_counter()
    plan = plan_library_rebuild(
        tmp_path / "library", db_module=db, tags_module=tags,
    )
    assert time.perf_counter() - started < 2

    issue = next(issue for issue in plan.issues if issue["path"] == str(nfo))
    assert issue["kind"] == NFO_PARSE_ERROR
    assert "parse error" in issue["reason"]


def test_streamkeep_has_no_direct_stdlib_elementtree_imports():
    root = Path(__file__).resolve().parents[1] / "streamkeep"
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "xml.etree.ElementTree" for alias in node.names
            ):
                offenders.append(str(path))
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module == "xml.etree.ElementTree"
            ):
                offenders.append(str(path))
    assert offenders == []
