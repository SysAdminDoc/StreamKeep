import sys
from unittest import mock

from streamkeep import plugins, youtube_backend


class _Backend:
    def health(self, request, context=None):
        if context is not None:
            context.require("network")
        return {
            "reachable": True,
            "detail": "helper https://helper.example.invalid/api is ready",
            "capabilities": ["cipher", "po-token"],
        }

    def solve(self, request, context=None):
        if context is not None:
            context.require("network")
        return {
            "extractor_args": [
                "--extractor-args",
                "youtube:po_token=test-token",
            ],
        }


def _handle(target=_Backend):
    spec = plugins.PluginAdapterSpec(
        "test-remote", "youtube_backend", "Backend", 1,
        ("network",), (), 2.0, 2,
    )
    return plugins.PluginAdapterHandle(spec, sys.modules[__name__], target, {})


def test_remote_backend_contract_returns_bounded_args_and_redacted_health():
    youtube_backend.reset_backend_state()
    handle = _handle()
    config = {
        youtube_backend.REMOTE_BACKEND_MODE_KEY: "auto",
        youtube_backend.REMOTE_BACKEND_URL_KEY:
            "https://helper.example.invalid/api?token=secret",
    }
    with mock.patch.object(
        youtube_backend, "registered_adapters", return_value=[handle],
    ):
        args = youtube_backend.resolve_extractor_args(
            "https://www.youtube.com/watch?v=abc",
            config=config,
        )
        status = youtube_backend.backend_status(config)

    assert args == ["--extractor-args", "youtube:po_token=test-token"]
    assert status["reachable"] is True
    assert status["plugin_id"] == "test-remote"
    assert status["backend_url"] == "https://helper.example.invalid"
    assert "secret" not in status["detail"]
    assert status["capabilities"] == ["cipher", "po-token"]


def test_remote_backend_absence_fails_open_and_does_not_call_non_youtube():
    youtube_backend.reset_backend_state()
    config = {
        youtube_backend.REMOTE_BACKEND_MODE_KEY: "required",
        youtube_backend.REMOTE_BACKEND_URL_KEY: "https://helper.example.invalid",
    }
    with mock.patch.object(
        youtube_backend, "registered_adapters", return_value=[],
    ), mock.patch.object(
        youtube_backend, "load_all_plugins", return_value=(0, 0),
    ) as load_all:
        assert youtube_backend.resolve_extractor_args(
            "https://example.invalid/video", config=config,
        ) == []
        assert youtube_backend.resolve_extractor_args(
            "https://www.youtube.com/watch?v=abc", config=config,
        ) == []
        status = youtube_backend.backend_status(config)

    load_all.assert_called_once()
    assert status["available"] is False
    assert status["reachable"] is False
    assert "trusted youtube_backend" in status["detail"]


def test_remote_backend_rejects_unsafe_or_unbounded_plugin_results():
    class UnsafeBackend:
        def health(self, request, context=None):
            return {"reachable": True}

        def solve(self, request, context=None):
            return {"extractor_args": ["--cookies", "C:\\secret.txt"]}

    youtube_backend.reset_backend_state()
    config = {
        youtube_backend.REMOTE_BACKEND_MODE_KEY: "auto",
        youtube_backend.REMOTE_BACKEND_URL_KEY: "https://helper.example.invalid",
    }
    with mock.patch.object(
        youtube_backend, "registered_adapters", return_value=[_handle(UnsafeBackend)],
    ):
        assert youtube_backend.resolve_extractor_args(
            "https://www.youtube.com/watch?v=abc", config=config,
        ) == []


def test_ytdlp_command_includes_remote_backend_args_only_at_youtube_boundary():
    from streamkeep.extractors import ytdlp

    extractor = ytdlp.YtDlpExtractor()
    extractor._auth_args = lambda url: []
    extractor._request_header_args = lambda: []
    with mock.patch.object(ytdlp, "ytdlp_command", return_value=["yt-dlp"]), \
            mock.patch.object(ytdlp, "youtube_pot_args", return_value=[]), \
            mock.patch.object(
                ytdlp,
                "youtube_remote_backend_args",
                side_effect=lambda url, **kwargs: (
                    ["--extractor-args", "youtube:po_token=test-token"]
                    if "youtube.com" in url else []
                ),
            ):
        command = extractor._build_cmd(
            "https://www.youtube.com/watch?v=abc",
        )
        non_youtube = extractor._build_cmd("https://example.invalid/video")

    extractor_arg_index = command.index("--extractor-args")
    assert command[extractor_arg_index:extractor_arg_index + 2] == [
        "--extractor-args", "youtube:po_token=test-token",
    ]
    assert "--extractor-args" not in non_youtube


def test_health_report_surfaces_remote_backend_reachability():
    from streamkeep.extractors import ytdlp

    fake_runtime = {
        "state": "ready", "summary": "Ready", "yt_dlp_version": "2026.07.04",
        "js_runtime": {"name": "deno"}, "ejs_available": True, "problems": [],
    }
    remote = {
        "configured": True, "mode": "auto", "backend_url": "https://helper.example.invalid",
        "plugin_id": "test-remote", "available": True, "reachable": True,
        "usable": True, "capabilities": ["cipher"], "detail": "Ready",
    }
    with mock.patch.object(ytdlp, "ytdlp_runtime_status", return_value=fake_runtime), \
            mock.patch.object(
                ytdlp, "youtube_pot_provider_status",
                return_value={"available": False, "detail": "No PO-token provider"},
            ), mock.patch(
                "streamkeep.pot_provider.cached_status", return_value={}
            ), mock.patch.object(
                youtube_backend, "backend_status", return_value=remote,
            ):
        report = ytdlp.youtube_health_report(config={})

    assert report["remote_backend"]["reachable"] is True
    assert report["remote_backend"]["plugin_id"] == "test-remote"
