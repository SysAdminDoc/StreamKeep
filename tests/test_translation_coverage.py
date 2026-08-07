"""V171: an incomplete catalog must say so in the app, not only the README.

Spanish sits near 15% translated. Before this, switching to it produced a
mostly-English UI with nothing anywhere to explain why, because the beta
caveat lived only in README.md.
"""

import pytest

from streamkeep import i18n


def test_english_has_no_coverage_figure_to_report():
    """English is the source; a percentage would be meaningless."""
    coverage = i18n.catalog_coverage("en")

    assert coverage["total"] == 0
    assert coverage["beta"] is False
    assert i18n.language_label("en", "English") == "English"


def test_the_pseudo_locale_is_a_layout_test_not_a_language():
    coverage = i18n.catalog_coverage("qps-ploc")

    assert coverage["beta"] is False
    assert i18n.language_label("qps-ploc", "Pseudo") == "Pseudo"


def test_a_real_catalog_reports_what_the_user_will_actually_see():
    """The count must match what the loader accepts, not rows in the file."""
    coverage = i18n.catalog_coverage("es")
    loaded = i18n._load_catalog("es")

    assert coverage["total"] > 1000
    assert 0 < coverage["translated"] < coverage["total"]
    # The loader skips unfinished entries; the report must agree with it or the
    # percentage is describing a different catalog than the one in use.
    assert abs(coverage["translated"] - len(loaded)) <= 1
    assert coverage["ratio"] == pytest.approx(
        coverage["translated"] / coverage["total"]
    )


def test_an_incomplete_catalog_is_marked_beta_in_the_selector_label():
    coverage = i18n.catalog_coverage("es")
    assert coverage["beta"] is True, "Spanish is far below the threshold"

    label = i18n.language_label("es", "Español")

    assert "Español" in label
    assert "%" in label
    assert "beta" in label.lower()


def test_a_missing_catalog_reports_zero_rather_than_crashing():
    coverage = i18n.catalog_coverage("zz")

    assert coverage["total"] == 0
    assert coverage["ratio"] == 0.0
    assert coverage["beta"] is True


def test_the_threshold_is_what_decides_beta(monkeypatch):
    """Bait the threshold: raising it must change the verdict."""
    monkeypatch.setattr(i18n, "BETA_COVERAGE_THRESHOLD", 0.0)
    assert i18n.catalog_coverage("es")["beta"] is False

    monkeypatch.setattr(i18n, "BETA_COVERAGE_THRESHOLD", 1.0)
    assert i18n.catalog_coverage("es")["beta"] is True


def test_the_report_covers_every_shipped_catalog():
    report = i18n.coverage_report()
    languages = {entry["language"] for entry in report}

    assert languages == set(i18n.available_languages())


def test_the_release_gate_reports_coverage_without_gating_on_it():
    """Coverage falls whenever UI strings outpace translation; that is not a
    release failure, but it must be visible."""
    import sys
    from pathlib import Path

    packaging = str(Path(__file__).resolve().parents[1] / "packaging")
    if packaging not in sys.path:
        sys.path.insert(0, packaging)
    from release_gate import stage_translations

    ok, detail = stage_translations()

    assert ok, detail
    assert "es:" in detail
    assert "%" in detail
