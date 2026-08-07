"""V178: source adapters travel with a backup; approvals and plugins do not.

`download-archives/` already travelled. `source_adapters/` could not, because a
definition went live on the next URL detection. The enable-time review (v4.46.0)
changed that: an adapter whose contract fingerprint is not approved is inert, so
a definition can travel as a definition.

The security property is that it arrives *unapproved*. `config.json` is part of
the archive and holds the approvals, so restoring it verbatim would deliver
pre-approved third-party request descriptions -- a backup file as a way to
enable an adapter nobody reviewed on this machine.
"""

import json
import zipfile
from pathlib import Path

from streamkeep import backup


# ── Policy ──────────────────────────────────────────────────────────

def test_adapters_travel_and_plugins_do_not():
    assert "source_adapters" in backup.BACKUP_DIRECTORIES
    assert "download-archives" in backup.BACKUP_DIRECTORIES
    assert "plugins" not in backup.BACKUP_DIRECTORIES
    assert "auth" not in backup.BACKUP_DIRECTORIES
    # Executable Python has no opt-in: a review gate cannot make it safe.
    assert "plugins" in backup.EXCLUDED_DIRECTORIES
    assert "auth" in backup.EXCLUDED_DIRECTORIES


def test_a_backup_member_path_is_still_confined_to_the_allowed_directories():
    """Adding a directory must not widen what an archive name can address."""
    assert backup._safe_directory_member("source_adapters/site.yaml") == (
        "source_adapters", "site.yaml",
    )
    for hostile in (
        "plugins/evil.py",                     # not an allowed directory
        "source_adapters/../config.json",      # traversal
        "source_adapters/sub/dir.yaml",        # nested
        "source_adapters/",                    # no filename
        "source_adapters/..",
        "auth/cookies.txt",
        "unknown/file.yaml",
    ):
        assert backup._safe_directory_member(hostile) is None, hostile


# ── The security property ───────────────────────────────────────────

def test_restoring_strips_adapter_approvals_so_definitions_arrive_inert():
    """The one assertion this whole item rests on.

    A restored `config.json` carrying `reviewed_source_adapters` would mean a
    definition that arrived in the same archive is already approved.
    """
    payload = json.dumps({
        "output_dir": "D:/media",
        backup.ADAPTER_REVIEW_CONFIG_KEY: {
            "third-party-site": "fingerprint-from-another-machine",
        },
    }).encode("utf-8")

    restored = json.loads(
        backup._secret_free_config_bytes(payload, strip_adapter_reviews=True)
    )

    assert backup.ADAPTER_REVIEW_CONFIG_KEY not in restored, (
        "a restore delivered pre-approved adapter contracts"
    )
    assert restored["output_dir"] == "D:/media", "unrelated settings still travel"


def test_creating_a_backup_keeps_approvals_because_restore_is_what_strips_them():
    """Stripping only at create time would leave every existing backup able to
    deliver pre-approved adapters, so the restore side is load-bearing."""
    payload = json.dumps({
        backup.ADAPTER_REVIEW_CONFIG_KEY: {"site": "fingerprint"},
    }).encode("utf-8")

    created = json.loads(backup._secret_free_config_bytes(payload))

    assert backup.ADAPTER_REVIEW_CONFIG_KEY in created


def test_a_restored_adapter_is_not_approved_by_the_review_gate(tmp_path,
                                                              monkeypatch):
    """End to end against the real gate, not just the config shape."""
    from streamkeep import declarative

    definition = type("Definition", (), {
        "adapter_id": "third-party-site",
        "contract_fingerprint": "fingerprint-from-another-machine",
    })()

    # As it would arrive from another machine's backup, then stripped.
    carried = {backup.ADAPTER_REVIEW_CONFIG_KEY: {
        definition.adapter_id: definition.contract_fingerprint,
    }}
    assert declarative.adapter_review_state(definition, carried) is True, (
        "the gate must accept a matching fingerprint, or this proves nothing"
    )

    stripped = json.loads(backup._secret_free_config_bytes(
        json.dumps(carried).encode("utf-8"), strip_adapter_reviews=True,
    ))

    assert declarative.adapter_review_state(definition, stripped) is False


# ── What the operator is told ───────────────────────────────────────

def test_the_restore_report_names_every_exclusion():
    notes = backup.restore_exclusion_notes()

    joined = " ".join(notes)
    assert "plugins were not restored" in joined
    assert "executable Python" in joined
    assert "authentication state was not restored" in joined


def test_the_report_says_restored_adapters_need_review():
    staged = [
        ("source_adapters", "a.yaml", Path("a.yaml")),
        ("source_adapters", "b.yaml", Path("b.yaml")),
        ("download-archives", "x.txt", Path("x.txt")),
    ]

    notes = backup.restore_exclusion_notes(staged)

    assert "2 source adapter definition(s)" in notes[0]
    assert "inert" in notes[0] and "approve" in notes[0].lower()


def test_no_adapter_note_when_none_arrived():
    notes = backup.restore_exclusion_notes(
        [("download-archives", "x.txt", Path("x.txt"))]
    )

    assert not any("source adapter" in note for note in notes)


# ── Round trip ──────────────────────────────────────────────────────

def test_a_created_backup_carries_adapter_definitions(tmp_path, monkeypatch):
    config_dir = tmp_path / "cfg"
    (config_dir / "source_adapters").mkdir(parents=True)
    (config_dir / "source_adapters" / "site.yaml").write_text(
        "id: site\n", encoding="utf-8",
    )
    (config_dir / "plugins").mkdir()
    (config_dir / "plugins" / "evil.py").write_text("x = 1\n", encoding="utf-8")
    (config_dir / "config.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(backup, "CONFIG_DIR", config_dir)

    out = tmp_path / "out.skbackup"
    ok, _message = backup.create_backup(str(out))
    assert ok

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()

    assert "source_adapters/site.yaml" in names
    assert not any(name.startswith("plugins/") for name in names), (
        "executable Python must never be in the archive"
    )


def test_a_restore_leaves_the_definition_present_but_unapproved(tmp_path,
                                                               monkeypatch):
    """The full journey: it comes back, and it comes back inert."""
    source = tmp_path / "src"
    (source / "source_adapters").mkdir(parents=True)
    (source / "source_adapters" / "site.yaml").write_text(
        "id: site\n", encoding="utf-8",
    )
    (source / "config.json").write_text(json.dumps({
        "output_dir": "D:/media",
        backup.ADAPTER_REVIEW_CONFIG_KEY: {"site": "approved-elsewhere"},
    }), encoding="utf-8")

    monkeypatch.setattr(backup, "CONFIG_DIR", source)
    archive = tmp_path / "round.skbackup"
    ok, _message = backup.create_backup(str(archive))
    assert ok

    target = tmp_path / "dst"
    target.mkdir()
    monkeypatch.setattr(backup, "CONFIG_DIR", target)
    ok, message = backup.restore_backup(str(archive))
    assert ok, message

    assert (target / "source_adapters" / "site.yaml").is_file(), (
        "the definition should travel"
    )
    restored = json.loads((target / "config.json").read_text(encoding="utf-8"))
    assert backup.ADAPTER_REVIEW_CONFIG_KEY not in restored, (
        "the approval travelled with it"
    )
    assert restored["output_dir"] == "D:/media"
    assert "source adapter definition(s) were restored" in message
    assert "plugins were not restored" in message


def test_an_adapter_member_over_the_size_limit_aborts_the_restore(tmp_path,
                                                                 monkeypatch):
    """A new directory must not become an unbounded-write path."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(backup, "CONFIG_DIR", source)
    archive = tmp_path / "big.skbackup"
    ok, _m = backup.create_backup(str(archive))
    assert ok

    # Append an oversized adapter member directly to the archive.
    monkeypatch.setattr(backup, "MAX_BACKUP_DIRECTORY_BYTES", 32)
    with zipfile.ZipFile(archive, "a") as zf:
        zf.writestr("source_adapters/huge.yaml", "y" * 512)

    target = tmp_path / "dst"
    target.mkdir()
    monkeypatch.setattr(backup, "CONFIG_DIR", target)
    ok, message = backup.restore_backup(str(archive))

    assert not ok
    assert "size limit" in message
    assert not (target / "source_adapters").exists(), (
        "a rejected restore must not have written anything"
    )
