"""StreamKeep's explicit position on the FFmpeg 8.x / 9.0 split (V173).

Two behaviours differ between the majors StreamKeep can be handed on a user's
PATH: FFmpeg 9.0 hardcodes ``tls_verify`` on where 8.x defaults it off, and 9.0
drops pre-11.1 NVENC SDK support. The position taken is that both majors are
supported, that TLS verification is *stated* rather than inherited so the two
behave identically, and that an ABI outside the tested set is refused by name.
"""

from unittest import mock

import pytest

from streamkeep import capabilities, raw_capture
from streamkeep.paths import (
    FFMPEG_FILTERED_HLS_INPUT_SAFETY,
    FFMPEG_LOCAL_SAFETY,
    FFMPEG_REMOTE_INPUT_SAFETY,
    FFMPEG_REMOTE_SAFETY,
)


def _banner(marketing, libavcodec):
    return (
        f"ffmpeg version {marketing}-full_build Copyright (c) 2000-2026\n"
        "built with gcc 16.1.0\n"
        "configuration: --enable-gpl\n"
        "libavutil      60. 26.102 / 60. 26.102\n"
        f"libavcodec     {libavcodec}. 28.102 / {libavcodec}. 28.102\n"
        "libavformat    62. 12.102 / 62. 12.102\n"
    )


def _pairs(argv):
    """Every ``-flag value`` pair in an FFmpeg argv, for order-free asserts."""
    return list(zip(argv, argv[1:]))


# ── TLS verification is stated, not inherited ────────────────────────

@pytest.mark.parametrize("safety", [
    FFMPEG_REMOTE_INPUT_SAFETY,
    FFMPEG_REMOTE_SAFETY,
    FFMPEG_FILTERED_HLS_INPUT_SAFETY,
])
def test_every_remote_input_states_tls_verify(safety):
    """Left unset, the PATH build silently decides whether certificates are
    checked: 8.1.2 defaults tls_verify to 0, 9.0 hardcodes it to 1."""
    assert ("-tls_verify", "1") in _pairs(list(safety))


def test_local_safety_does_not_claim_a_tls_position():
    # Local concat/filter work reads files, so a TLS flag there would be noise.
    assert "-tls_verify" not in FFMPEG_LOCAL_SAFETY


def test_a_raw_capture_verifies_unless_the_operator_opts_out():
    spec = raw_capture.RawCaptureSpec(
        endpoint="https://radio.example/stream",
        protocol="icy",
        output_path="out.mp3",
        duration_secs=5,
    )
    command = raw_capture.build_ffmpeg_command(spec, executable="ffmpeg")
    assert ("-tls_verify", "1") in _pairs(command)


def test_the_self_signed_opt_in_is_what_turns_verification_off():
    """On 8.x an unset tls_verify already accepted a self-signed origin, so the
    opt-in was a no-op there and a hard requirement on 9.0 - same spec, two
    behaviours."""
    # Only RTSPS and RTMPS accept the opt-in; ICY refuses it outright, so an
    # ICY capture has no way to reach an unverified origin at all.
    spec = raw_capture.RawCaptureSpec(
        endpoint="rtsps://camera.lan:322/stream",
        protocol="rtsp",
        output_path="out.mp4",
        duration_secs=5,
        allow_self_signed=True,
    )
    command = raw_capture.build_ffmpeg_command(spec, executable="ffmpeg")
    pairs = _pairs(command)
    assert ("-tls_verify", "0") in pairs
    assert ("-tls_verify", "1") not in pairs


# ── The ABI major is what the registry branches on ───────────────────

def test_the_libavcodec_major_is_read_from_the_banner():
    assert capabilities.parse_libavcodec_major(_banner("8.1.2", 62)) == 62
    assert capabilities.parse_libavcodec_major(_banner("9.0", 63)) == 63
    assert capabilities.parse_libavcodec_major("") == 0
    assert capabilities.parse_libavcodec_major("libavformat 62. 12.102") == 0


def _probe(marketing, libavcodec):
    output = _banner(marketing, libavcodec) if libavcodec else marketing
    with mock.patch.object(
        capabilities.shutil, "which", return_value=r"C:\Tools\ffmpeg.exe"
    ), mock.patch.object(
        capabilities, "_run_version_command", return_value=(output, 0)
    ), mock.patch.object(
        capabilities, "_path_provenance", return_value="test-fixture"
    ):
        return capabilities._probe_executable(
            "ffmpeg", ["ffmpeg"], ["-version"],
            capabilities.MINIMUM_VERSIONS["ffmpeg"],
            ["media-download"], "Install FFmpeg.",
            annotate=capabilities._annotate_ffmpeg_abi,
        )


@pytest.mark.parametrize("marketing, major", [("8.1.2", 62), ("9.0", 63)])
def test_both_tested_majors_are_supported(marketing, major):
    record = _probe(marketing, major)
    assert record["libavcodec_major"] == major
    assert record["supported"] is True
    assert record["supported_libavcodec_majors"] == [62, 63]


def test_an_untested_abi_is_refused_by_name_not_accepted_silently():
    """The floor was 8.1.2 with no ceiling, so anything newer passed a version
    comparison regardless of what its ABI actually did."""
    record = _probe("10.0", 64)
    assert record["libavcodec_major"] == 64
    assert record["supported"] is False
    assert "libavcodec ABI major 64" in record["detail"]
    assert "62, 63" in record["detail"]


def test_an_unreadable_banner_falls_back_to_the_version_floor():
    # Refusing a build because its banner could not be parsed would be worse
    # than the gap it is reporting.
    record = _probe("ffmpeg version 8.1.2-custom\n", 0)
    assert record["libavcodec_major"] == 0
    assert record["supported"] is True
    assert "ABI major not reported" in record["detail"]


def test_a_missing_executable_still_reports_the_abi_fields_absent():
    with mock.patch.object(capabilities.shutil, "which", return_value=None):
        record = capabilities._probe_executable(
            "ffmpeg", ["ffmpeg"], ["-version"],
            capabilities.MINIMUM_VERSIONS["ffmpeg"],
            ["media-download"], "Install FFmpeg.",
            annotate=capabilities._annotate_ffmpeg_abi,
        )
    assert record["available"] is False
    assert record["supported"] is False


def test_the_real_ffmpeg_on_this_machine_reports_a_supported_abi():
    registry = capabilities.get_runtime_capabilities(refresh=True, config={})
    ffmpeg = registry["ffmpeg"]
    if not ffmpeg.get("available"):
        pytest.skip("no FFmpeg on PATH")
    assert ffmpeg["libavcodec_major"] in capabilities.FFMPEG_SUPPORTED_LIBAVCODEC_MAJORS
    assert ffmpeg["supported"] is True
