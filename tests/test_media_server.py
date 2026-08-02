from pathlib import Path
from unittest import mock

import pytest

from streamkeep import config
from streamkeep import db
from streamkeep.integrations import media_server
from streamkeep.models import StreamInfo


def _recording(tmp_path, name="capture.mp4", contents=b"media"):
    out_dir = tmp_path / "capture"
    out_dir.mkdir()
    media = out_dir / name
    media.write_bytes(contents)
    return out_dir


def test_media_import_plan_supports_season_and_flat_layouts_without_collisions(tmp_path):
    out_dir = _recording(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    info = StreamInfo(
        channel="My/Channel",
        title="A: live? show",
        start_time="2026-07-31T20:00:00Z",
    )
    config_data = {"library_path": str(library), "layout_mode": "seasoned"}

    first = media_server.plan_media_import(config_data, out_dir, info)
    assert first is not None
    assert first.relative_path == (
        "My_Channel/Season 2026/My_Channel - S2026E01 - A_ live_ show.mp4"
    )

    Path(first.destination).parent.mkdir(parents=True)
    Path(first.destination).write_bytes(b"existing")
    second = media_server.plan_media_import(config_data, out_dir, info)
    assert second is not None
    assert second.episode == 2

    flat = media_server.plan_media_import(
        {"library_path": str(library), "layout_mode": "flat"}, out_dir, info
    )
    assert flat is not None
    assert flat.relative_path.startswith("My_Channel/My_Channel - S2026E01")


def test_portable_m3u_is_relative_atomic_and_excludes_external_paths(tmp_path):
    library = tmp_path / "library"
    episode_dir = library / "Channel" / "Season 2026"
    episode_dir.mkdir(parents=True)
    media = episode_dir / "Channel - S2026E01 - Episode.mp4"
    media.write_bytes(b"x")
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")

    playlist = media_server.write_portable_m3u(
        library,
        "Watch List",
        [{"path": str(media), "title": "Episode", "duration": 12}, str(outside)],
    )
    assert Path(playlist).name == "Watch List.m3u"
    assert Path(playlist).read_text(encoding="utf-8") == (
        "#EXTM3U\n#EXTINF:12,Episode\nChannel/Season 2026/"
        "Channel - S2026E01 - Episode.mp4\n"
    )
    assert not (library / ".Watch List.m3u.tmp").exists()


def test_import_writes_nfo_and_updates_portable_playlist(tmp_path):
    out_dir = _recording(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    info = StreamInfo(
        platform="Twitch",
        channel="Channel",
        title="Episode",
        source_id="vod:123",
        webpage_url="https://www.twitch.tv/videos/123",
        start_time="2026-07-31T20:00:00Z",
    )
    logs = []
    media_server._do_import(
        {
            "library_path": str(library),
            "layout_mode": "seasoned",
            "portable_m3u": True,
            "playlist_name": "Archive",
            "server_type": "kodi",
        },
        out_dir,
        info,
        logs.append,
    )
    imported = library / "Channel" / "Season 2026" / "Channel - S2026E01 - Episode.mp4"
    assert imported.is_file()
    assert imported.with_suffix(".nfo").is_file()
    playlist = library / "Archive.m3u"
    assert playlist.is_file()
    assert "Channel/Season 2026/Channel - S2026E01 - Episode.mp4" in playlist.read_text(encoding="utf-8")
    assert any("Kodi" in message for message in logs)


def test_watched_preview_uses_strong_identity_and_skips_ambiguous_matches():
    history = [
        {"id": 1, "source_id": "vod:123", "title": "Episode", "channel": "Show", "year": "2026"},
        {"id": 2, "title": "Same", "channel": "Show", "year": "2026"},
        {"id": 3, "title": "Same", "channel": "Show", "year": "2026"},
    ]
    preview = media_server.preview_watched_import(
        [
            {"server_key": "plex-1", "source_id": "vod:123", "played": True, "watch_position_secs": 4},
            {"server_key": "plex-2", "title": "Same", "channel": "Show", "year": "2026", "played": True},
            {"server_key": "plex-3", "title": "Unwatched", "played": False},
        ],
        history,
        user_id="user-1",
    )
    assert preview["user_id"] == "user-1"
    assert [(item["history_id"], item["watch_position_secs"]) for item in preview["matches"]] == [(1, 4.0)]
    assert preview["ambiguous"][0]["candidate_history_ids"] == [2, 3]
    assert preview["skipped"][-1]["reason"] == "not watched"
    assert preview["lifecycle_delete_requested"] is False


def test_watched_import_only_updates_previewed_rows_and_rejects_delete_opt_in():
    updates = []
    preview = {"matches": [{"history_id": 9, "watched": True, "watch_position_secs": 8}]}
    assert media_server.apply_watched_import(
        preview, lambda row_id, fields: updates.append((row_id, fields))
    ) == 1
    assert updates == [(9, {"watched": True, "watch_position_secs": 8.0})]
    with pytest.raises(ValueError, match="cannot enable lifecycle deletion"):
        media_server.apply_watched_import(preview, lambda *_args: None, allow_lifecycle_delete=True)


def test_jellyfin_user_and_watched_payloads_are_normalized(monkeypatch):
    responses = [
        b'[{"Id":"u1","Name":"Alex"}]',
        b'{"Items":[{"Id":"item1","Name":"Episode","SeriesName":"Show",'
        b'"ProductionYear":2026,"ProviderIds":{"Tmdb":"123"},'
        b'"UserData":{"Played":true,"PlaybackPositionTicks":10000000}}]}',
    ]

    def fake_request(*_args, **_kwargs):
        return responses.pop(0)

    monkeypatch.setattr(media_server, "_request_bytes", fake_request)
    server_config = {"server_type": "jellyfin", "url": "http://server", "token": "secret"}
    assert media_server.fetch_media_server_users(server_config) == [{"id": "u1", "name": "Alex"}]
    assert media_server.fetch_watched_items(server_config, "u1") == [
        {
            "server_key": "item1",
            "source_id": "123",
            "provider_ids": {"Tmdb": "123"},
            "title": "Episode",
            "channel": "Show",
            "year": "2026",
            "path": "",
            "watched": True,
            "watch_position_secs": 1.0,
            "played": True,
        }
    ]


def test_media_server_config_import_accepts_layout_and_playlist_controls():
    payload = {
        "media_server": {
            "enabled": False,
            "server_type": "kodi",
            "layout_mode": "flat",
            "portable_m3u": True,
            "native_playlist": True,
            "playlist_name": "Archive",
            "watched_user_id": "user-1",
            "watched_user_name": "Alex",
        }
    }
    prepared = config.prepare_config_import(
        __import__("json").dumps({
            "format": config.CONFIG_EXPORT_FORMAT,
            "schema_version": config.CONFIG_EXPORT_SCHEMA_VERSION,
            "exported_by": "test",
            "config": payload,
        }).encode("utf-8"),
        {},
    )
    assert prepared.quarantined_config["media_server"]["layout_mode"] == "flat"


def test_monitor_media_server_layout_override_survives_sqlite_round_trip(tmp_path):
    db_path = tmp_path / "library.db"
    with mock.patch.object(db, "DB_PATH", db_path):
        db.init_db()
        db.save_monitor_channel({
            "url": "https://example.invalid/channel",
            "media_server_layout": "flat",
        })
        rows = db.load_monitor_channels()
    assert rows[0]["media_server_layout"] == "flat"
