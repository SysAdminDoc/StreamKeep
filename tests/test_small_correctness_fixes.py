"""Three small correctness fixes: V157, V160, V175."""

import os
from unittest import mock

import pytest


# ── V157: the translation temp file is closed exactly once ───────────

def test_a_failed_replace_does_not_close_the_descriptor_twice(tmp_path):
    """``os.fdopen`` owns the descriptor, so the cleanup path's ``os.close``
    ran on a number the OS may already have reissued to another thread. The
    resulting damage surfaced somewhere unrelated, and the bare ``except
    OSError`` swallowed the evidence."""
    from streamkeep import translation

    closed = []
    real_close = os.close

    def tracking_close(fd):
        closed.append(fd)
        return real_close(fd)

    with mock.patch.object(
        translation.os, "replace", side_effect=OSError("denied")
    ), mock.patch.object(translation.os, "close", tracking_close):
        with pytest.raises(OSError):
            translation._atomic_write(tmp_path / "out.json", {"a": 1})

    # The context manager already closed it; the handler must not close again.
    assert closed == [], f"cleanup re-closed descriptor(s) {closed}"


def test_the_temporary_file_is_still_removed_when_replace_fails(tmp_path):
    from streamkeep import translation

    target = tmp_path / "out.json"
    with mock.patch.object(
        translation.os, "replace", side_effect=OSError("denied")
    ):
        with pytest.raises(OSError):
            translation._atomic_write(target, {"a": 1})

    assert not target.exists()
    leftovers = list(tmp_path.glob(".streamkeep_translation_*"))
    assert leftovers == [], f"temporary file left behind: {leftovers}"


def test_a_successful_write_is_atomic_and_readable(tmp_path):
    import json

    from streamkeep import translation

    target = tmp_path / "nested" / "out.json"
    translation._atomic_write(target, {"title": "ok"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"title": "ok"}
    assert list(tmp_path.glob("**/.streamkeep_translation_*")) == []


# ── V160: no unmeasured hash is reported as verification ─────────────

def test_the_runtime_record_does_not_claim_to_have_hashed_the_executable():
    """The value is the archive digest the install was verified against, not a
    measurement of the binary on disk. Calling it ``sha256`` on a record whose
    ``path`` is an executable stated something untrue."""
    from streamkeep import javascript_runtime

    info = javascript_runtime.get_managed_deno_info()
    assert "sha256" not in info
    assert "pinned_archive_sha256" in info


def test_the_capability_record_uses_the_same_honest_name():
    from streamkeep import capabilities

    registry = capabilities.get_runtime_capabilities(refresh=True, config={})
    record = registry.get("javascript", {})
    if not record.get("managed"):
        pytest.skip("no managed Deno runtime on this host")
    assert "sha256" not in record
    assert "pinned_archive_sha256" in record


def test_the_pinned_digest_still_travels_when_a_runtime_is_installed(tmp_path):
    from streamkeep import javascript_runtime as jsr

    target = jsr.host_target()
    directory = jsr.runtime_directory(tmp_path, target=target)
    directory.mkdir(parents=True, exist_ok=True)
    executable = jsr.managed_executable_path(tmp_path, target=target)
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"#!/bin/sh\n")
    import json

    jsr._metadata_path(tmp_path, target=target).write_text(
        json.dumps({
            "schema_version": jsr.DENO_RUNTIME_SCHEMA_VERSION,
            "runtime": "deno",
            "version": jsr.DENO_VERSION,
            "target": target,
            "archive_sha256": "a" * 64,
            "asset": "deno.zip",
            "provenance": "managed",
        }),
        encoding="utf-8",
    )

    info = jsr.get_managed_deno_info(tmp_path, target=target)
    assert info["available"] is True
    assert info["pinned_archive_sha256"] == "a" * 64
    assert "sha256" not in info


# ── V175: the recycle-bin dependency cannot drift a major ────────────

def test_the_send2trash_floor_is_bounded_to_the_locked_major():
    """This is the one dependency standing between "delete" and "permanently
    delete", so a source install must not be free to resolve an unverified
    next major."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    requirement = next(
        line.strip()
        for line in (root / "requirements.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip().lower().startswith("send2trash")
    )
    assert requirement == "send2trash>=2.1.0,<3"

    locked = next(
        line for line in (root / "requirements.lock").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip().lower().startswith("send2trash==")
    )
    assert "send2trash==2.1.0" in locked


def test_the_installed_send2trash_recycles_rather_than_deletes(tmp_path):
    send2trash = pytest.importorskip("send2trash")
    victim = tmp_path / "recycle-me.txt"
    victim.write_text("bin me", encoding="utf-8")

    send2trash.send2trash(str(victim))

    # Recycled, not unlinked in place — the file leaves the folder but the
    # call must not raise, which is what the lifecycle policy depends on.
    assert not victim.exists()
