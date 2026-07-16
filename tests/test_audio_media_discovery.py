from streamkeep import gallery, verify
from streamkeep.integrations import media_server
from streamkeep.storage import scan_storage


def test_audio_outputs_are_visible_to_gallery_verify_storage_and_media_server(tmp_path):
    audio = tmp_path / "episode.opus"
    audio.write_bytes(b"not-real-audio")

    assert gallery.find_media_file(str(tmp_path)) == str(audio)
    assert ".opus" in verify.MEDIA_EXTS
    assert media_server._find_media(str(tmp_path)) == str(audio)
    storage = scan_storage(str(tmp_path))
    assert storage.total_files == 1
    assert storage.groups[0].files[0].path == str(audio)


def test_gallery_uses_audio_player_for_audio_share():
    share_id = "audio-test"
    gallery.register_shared(
        share_id, "C:/recording", title="Episode", media="episode.mp3"
    )
    try:
        html = gallery.render_share_html(share_id)
    finally:
        gallery.unregister_shared(share_id)

    assert "<audio controls" in html
    assert "</audio>" in html
    assert "audio/mpeg" in html
