from pathlib import Path
from unittest import mock

from streamkeep import db
from streamkeep.integrations import media_server
from streamkeep.models import StreamInfo
from streamkeep.upload.ftp import FTPDestination
from streamkeep.upload.runtime import UploadRuntime, profile_view, resolve_profile, save_profile
from streamkeep.upload.webdav import WebDAVDestination


def _init_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "library.db")
    db.init_db()


def test_plain_transports_are_disabled_by_default():
    ftp_ok, ftp_message = FTPDestination({"host": "ftp.example"}).test_connection()
    dav_ok, dav_message = WebDAVDestination({"url": "http://dav.example/root"}).test_connection()
    assert not ftp_ok
    assert "disabled by default" in ftp_message
    assert not dav_ok
    assert "disabled by default" in dav_message


def test_profile_keeps_credentials_out_of_sqlite(monkeypatch, tmp_path):
    _init_db(tmp_path, monkeypatch)
    secret_values = {}

    def fake_set(secret_id, value):
        secret_values[secret_id] = value
        return f"secretref:{secret_id}"

    with mock.patch("streamkeep.upload.runtime.set_secret_value", side_effect=fake_set), \
         mock.patch("streamkeep.upload.runtime.delete_secret_value"):
        saved = save_profile(
            "dav-prod", "WebDAV",
            {
                "url": "https://dav.example/root",
                "username": "alice",
                "password": "not-in-sqlite",
            },
            label="Production DAV",
        )

    row = db.load_upload_profile("dav-prod")
    assert saved["has_credentials"] is True
    assert row["config"] == {"url": "https://dav.example/root"}
    assert "not-in-sqlite" not in str(row)
    assert secret_values["upload-profile:dav-prod"] == {
        "username": "alice", "password": "not-in-sqlite",
    }
    with mock.patch(
        "streamkeep.upload.runtime.get_secret_value",
        return_value=secret_values["upload-profile:dav-prod"],
    ):
        assert resolve_profile("dav-prod")["config"]["password"] == "not-in-sqlite"
    assert profile_view("dav-prod")["config"] == {"url": "https://dav.example/root"}


def test_interrupted_upload_is_recoverable_without_false_completion(monkeypatch, tmp_path):
    _init_db(tmp_path, monkeypatch)
    source = tmp_path / "clip.bin"
    source.write_bytes(b"payload")
    with mock.patch(
        "streamkeep.upload.runtime.UploadDestination.all_adapters",
        return_value={"Fake": object},
    ), mock.patch("streamkeep.upload.runtime.delete_secret_value"), \
         mock.patch("streamkeep.upload.runtime.set_secret_value", return_value=""):
        save_profile("fake", "Fake", {}, label="Fake")

    job = db.create_upload_job("fake", "Fake", str(source))
    db.start_upload_job(job["upload_id"])
    db.update_upload_progress(job["upload_id"], 3, source.stat().st_size)
    assert db.recover_upload_jobs() == 1
    recovered = db.load_upload_job(job["upload_id"])
    assert recovered["status"] == "retryable"
    assert recovered["bytes_sent"] == 3
    assert recovered["completed_at"] == ""
    assert db.retry_upload_job(job["upload_id"])
    assert db.start_upload_job(job["upload_id"])["status"] == "uploading"
    failed = db.finish_upload_job(
        job["upload_id"], success=False, message="connection lost",
    )
    assert failed["status"] == "retryable"
    assert failed["completed_at"] == ""


def test_runtime_persists_success_and_redacts_adapter_output(monkeypatch, tmp_path):
    _init_db(tmp_path, monkeypatch)
    source = tmp_path / "clip.bin"
    source.write_bytes(b"payload")

    class FakeAdapter:
        def __init__(self, config):
            self.config = config

        def upload(self, file_path, metadata=None, progress_cb=None):
            progress_cb(7, 7)
            return True, "Uploaded to https://alice:password@remote.example/file"

    with mock.patch("streamkeep.upload.runtime.UploadDestination.all_adapters", return_value={"Fake": FakeAdapter}), \
         mock.patch("streamkeep.upload.runtime.delete_secret_value"), \
         mock.patch("streamkeep.upload.runtime.set_secret_value", return_value=""):
        save_profile("fake", "Fake", {"password": "password"})
        runtime = UploadRuntime()
        job = db.create_upload_job("fake", "Fake", str(source))
        runtime._run(job["upload_id"])

    result = db.load_upload_job(job["upload_id"])
    assert result["status"] == "completed"
    assert result["bytes_sent"] == 7
    assert "password" not in result["remote_uri"]
    assert result["remote_uri"] == "[private URL removed]"


def test_media_server_export_preview_and_materialization_are_deterministic(tmp_path):
    out_dir = tmp_path / "capture"
    out_dir.mkdir()
    (out_dir / "capture.mp4").write_bytes(b"media")
    library = tmp_path / "library"
    library.mkdir()
    info = StreamInfo(
        platform="Twitch", channel="Show", title="Episode",
        source_id="vod:123", start_time="2026-08-02T12:00:00Z",
    )
    config = {
        "enabled": True, "server_type": "jellyfin",
        "library_path": str(library), "sidecar_profile": "jellyfin",
        "portable_m3u": True, "playlist_name": "Archive",
    }
    preview = media_server.preview_media_import(config, out_dir, info)
    assert preview["ok"] is True
    assert preview["relative_media_path"].endswith("Episode.mp4")
    assert {item["kind"] for item in preview["files"]} >= {
        "media", "nfo", "metadata", "thumbnail", "playlist",
    }
    exported = media_server.materialize_media_import(config, out_dir, info)
    assert exported["ok"] is True
    assert all(Path(item["path"]).is_file() for item in exported["files"])
    assert (library / "Archive.m3u").is_file()
    assert any(item["kind"] == "nfo" for item in exported["files"])
