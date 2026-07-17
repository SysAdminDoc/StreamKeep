from pathlib import Path
from unittest import mock

from streamkeep import cli
from streamkeep.capabilities import (
    ProductCapabilityClaim,
    get_product_capability_claims,
    validate_product_capability_claims,
)

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_capability_claims_have_reachable_tested_paths():
    assert validate_product_capability_claims(ROOT) == []
    assert get_product_capability_claims(status="shipped")
    assert get_product_capability_claims(status="experimental")


def test_release_gate_rejects_orphaned_shipped_claim():
    orphan = ProductCapabilityClaim(
        "orphan", "Unreachable release claim", "shipped", "StreamKeep",
    )
    problems = validate_product_capability_claims(ROOT, claims=(orphan,))
    assert problems == ["orphan: shipped capability has no reachable path"]


def test_download_cli_reaches_worker_dispatch():
    with mock.patch("streamkeep.crash_log.setup_crash_logging"), mock.patch.object(
        cli, "_run_download"
    ) as run_download:
        cli.run_cli(["download", "https://example.com/video", "--quality", "720p"])

    run_download.assert_called_once()
    args = run_download.call_args.args[0]
    assert args.url == "https://example.com/video"
    assert args.quality == "720p"


def test_extractor_cli_reaches_listing_dispatch():
    with mock.patch("streamkeep.crash_log.setup_crash_logging"), mock.patch.object(
        cli, "_list_extractors"
    ) as list_extractors:
        cli.run_cli(["extractors"])

    list_extractors.assert_called_once_with()
