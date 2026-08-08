"""The local unsigned release gate (V52).

These tests exercise the gate's own logic — stage ordering, failure reporting,
and the release-claim checks — without paying for a PyInstaller build.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "packaging") not in sys.path:
    sys.path.insert(0, str(ROOT / "packaging"))

import release_gate as gate  # noqa: E402


class StageDriverTests(unittest.TestCase):
    def test_every_stage_is_named_and_ordered_cheapest_first(self):
        names = [name for name, _func in gate.STAGES]
        self.assertEqual(names[0], "release-python")
        self.assertEqual(names[1], "compileall")
        # The expensive build stages must come after the cheap checks so a
        # trivial failure never costs a full PyInstaller run.
        for build_stage in gate.BUILD_STAGES:
            self.assertIn(build_stage, names)
            self.assertGreater(names.index(build_stage), names.index("tests"))

    def test_the_gate_stops_at_and_names_the_first_failure(self):
        with mock.patch.object(gate, "STAGES", (
            ("first", lambda: (True, "")),
            ("second", lambda: (False, "exploded")),
            ("third", lambda: (True, "")),
        )):
            result = gate.run_gate(echo=None)
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_stage, "second")
        self.assertEqual([s.name for s in result.stages], ["first", "second"])
        self.assertEqual(result.stages[-1].detail, "exploded")

    def test_a_clean_run_reports_ok_with_no_failed_stage(self):
        with mock.patch.object(gate, "STAGES", (
            ("first", lambda: (True, "")),
            ("second", lambda: (True, "")),
        )):
            result = gate.run_gate(echo=None)
        self.assertTrue(result.ok)
        self.assertEqual(result.failed_stage, "")

    def test_fast_mode_skips_only_the_build_stages(self):
        ran = []

        def track(name):
            def stage():
                ran.append(name)
                return True, ""
            return stage

        with mock.patch.object(gate, "STAGES", (
            ("tests", track("tests")),
            ("reproducible-build", track("reproducible-build")),
            ("artifact-smoke", track("artifact-smoke")),
        )):
            result = gate.run_gate(fast=True, echo=None)
        self.assertEqual(ran, ["tests"])
        self.assertTrue(result.ok)
        skipped = {s.name for s in result.stages if s.skipped}
        self.assertEqual(skipped, {"reproducible-build", "artifact-smoke"})

    def test_only_selects_a_single_stage(self):
        with mock.patch.object(gate, "STAGES", (
            ("first", lambda: (True, "")),
            ("second", lambda: (True, "")),
        )):
            result = gate.run_gate(only=["second"], echo=None)
        self.assertEqual([s.name for s in result.stages], ["second"])

    def test_the_json_shape_names_the_failed_stage(self):
        with mock.patch.object(gate, "STAGES", (
            ("first", lambda: (False, "bad")),
        )):
            payload = gate.run_gate(echo=None).to_dict()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["failed_stage"], "first")
        self.assertEqual(payload["stages"][0]["detail"], "bad")

    def test_an_unknown_stage_name_is_rejected(self):
        self.assertEqual(gate.main(["--only", "not-a-stage"]), 2)

    def test_listing_stages_exits_cleanly(self):
        self.assertEqual(gate.main(["--list"]), 0)

    def test_dependency_floor_stage_passes_for_the_checked_in_lock(self):
        ok, detail = gate.stage_dependency_floors()
        self.assertTrue(ok, detail)

    @mock.patch.object(gate, "_run", return_value=(True, ""))
    def test_test_stage_uses_a_dedicated_basetemp(self, run):
        self.assertEqual(gate.stage_tests(), (True, ""))
        command = [str(part) for part in run.call_args.args[0]]
        self.assertIn("--basetemp", command)
        base = Path(command[command.index("--basetemp") + 1])
        self.assertFalse(base.exists())

    @mock.patch.object(gate, "_run", return_value=(False, "No module named pip_audit"))
    def test_advisory_stage_skips_when_pip_audit_is_missing(self, run):
        ok, detail = gate.stage_advisories()
        self.assertTrue(ok)
        self.assertIn("skipped", detail)

    def test_release_python_policy_accepts_target_and_rejects_older_runtime(self):
        # Derived from the gate's own floor so a bump does not need this test
        # edited -- and so a bump cannot pass while the policy still admits the
        # superseded patch (V193).
        floor = gate.MIN_RELEASE_PYTHON
        expected = ".".join(str(part) for part in floor)
        self.assertEqual(gate.release_python_error(floor), "")
        below = (floor[0], floor[1], max(0, floor[2] - 1))
        self.assertIn(expected, gate.release_python_error(below))
        self.assertIn(expected, gate.release_python_error((3, 13, 14)))

    def test_no_stage_introduces_signing_or_ci(self):
        # The gate is local and unsigned by policy; nothing in it may shell out
        # to a signing tool or a CI runner.
        source = (ROOT / "packaging" / "release_gate.py").read_text(encoding="utf-8")
        for forbidden in ("signtool", "codesign", "notarytool", "gh workflow"):
            self.assertNotIn(forbidden, source.casefold())
        self.assertFalse((ROOT / ".github" / "workflows").exists())


class ReleaseClaimTests(unittest.TestCase):
    def test_the_shipped_tree_passes_its_own_claim_check(self):
        self.assertEqual(gate.validate_release_claims(ROOT), [])

    def test_page_claim_drift_against_the_tab_registry_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "streamkeep" / "ui").mkdir(parents=True)
            (root / "README.md").write_text(
                "Releases are unsigned. Spanish is beta.\n", encoding="utf-8",
            )
            (root / "CLAUDE.md").write_text(
                "The GUI uses a 4-page QStackedWidget.\n", encoding="utf-8",
            )
            (root / "streamkeep" / "ui" / "main_window.py").write_text(
                "self._tab_names = ['Download', 'Monitor', 'History', 'Operations', 'Storage']\n",
                encoding="utf-8",
            )
            problems = gate.validate_release_claims(root)
        self.assertTrue(any("states a 4-page" in problem for problem in problems))

    def test_a_signing_promise_is_reported(self):
        with mock.patch.object(Path, "read_text", autospec=True) as read_text:
            def fake(self, *a, **kw):
                if self.name == "README.md":
                    return (
                        "Releases are unsigned. Spanish is beta.\n"
                        "The command requires `STREAMKEEP_SIGN_PFX`, so sign it.\n"
                    )
                raise OSError("not needed")
            read_text.side_effect = fake
            problems = gate.validate_release_claims(ROOT)
        self.assertTrue(
            any("signing step" in problem for problem in problems), problems,
        )

    def test_omitting_the_unsigned_statement_is_reported(self):
        with mock.patch.object(Path, "read_text", autospec=True) as read_text:
            def fake(self, *a, **kw):
                if self.name == "README.md":
                    return "StreamKeep downloads streams. Spanish is beta.\n"
                raise OSError("not needed")
            read_text.side_effect = fake
            problems = gate.validate_release_claims(ROOT)
        self.assertTrue(
            any("unsigned" in problem for problem in problems), problems,
        )

    def test_a_partial_spanish_catalog_must_be_labelled_beta(self):
        with mock.patch.object(gate, "spanish_coverage", return_value=(195, 1427)):
            with mock.patch.object(Path, "read_text", autospec=True) as read_text:
                def fake(self, *a, **kw):
                    if self.name == "README.md":
                        return "Releases are unsigned. Full Spanish support.\n"
                    raise OSError("not needed")
                read_text.side_effect = fake
                problems = gate.validate_release_claims(ROOT)
        self.assertTrue(
            any("beta" in problem for problem in problems), problems,
        )

    def test_a_fully_translated_catalog_needs_no_beta_label(self):
        with mock.patch.object(gate, "spanish_coverage", return_value=(1427, 1427)):
            with mock.patch.object(Path, "read_text", autospec=True) as read_text:
                def fake(self, *a, **kw):
                    if self.name == "README.md":
                        return "Releases are unsigned. Spanish is complete.\n"
                    raise OSError("not needed")
                read_text.side_effect = fake
                problems = gate.validate_release_claims(ROOT)
        self.assertFalse(
            any("beta" in problem for problem in problems), problems,
        )

    def test_spanish_coverage_reads_the_real_catalog(self):
        translated, total = gate.spanish_coverage(ROOT)
        self.assertGreater(total, 1000)
        self.assertGreaterEqual(translated, 0)
        self.assertLessEqual(translated, total)


class CapabilityClaimStageTests(unittest.TestCase):
    def test_native_notifications_is_a_shipped_claim_with_a_tested_path(self):
        from streamkeep.capabilities import get_product_capability_claims

        shipped = {
            claim.id: claim
            for claim in get_product_capability_claims(status="shipped")
        }
        self.assertIn("native-notifications", shipped)
        paths = shipped["native-notifications"].paths
        self.assertTrue(paths)
        self.assertTrue(paths[0].test_nodeid.startswith("tests/"))

    def test_reachable_capabilities_are_shipped_and_unreachable_stay_experimental(self):
        from streamkeep.capabilities import get_product_capability_claims

        shipped = {
            claim.id: claim
            for claim in get_product_capability_claims(status="shipped")
        }
        self.assertIn("upload-delivery", shipped)
        self.assertTrue(shipped["upload-delivery"].paths)
        self.assertIn("plugin-adapters", shipped)
        self.assertTrue(shipped["plugin-adapters"].paths)


class PinnedArtifactRegistryTests(unittest.TestCase):
    """The pinned-binaries scope must be derived, not hand-written.

    ``stage_pinned_binaries`` exists because pip-audit cannot see a downloaded
    binary. Its scope was a hand-written tuple naming one artefact while the
    docstring claimed it covered every pin, so the SHA3-pinned SQLite DLL was
    certified by omission (V187).
    """

    def test_every_module_that_pins_a_download_is_in_the_registry(self):
        import ast

        from pinned_artifacts import COMPLETENESS_EXEMPTIONS, pinned_artifacts

        registered = {artifact.module for artifact in pinned_artifacts()}
        candidates = []
        for directory in ("streamkeep", "packaging"):
            for path in sorted((ROOT / directory).rglob("*.py")):
                if "__pycache__" in str(path):
                    continue
                text = path.read_text(encoding="utf-8")
                tree = ast.parse(text)
                names = {
                    target.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Assign)
                    for target in node.targets
                    if isinstance(target, ast.Name)
                }
                # A pinned external download declares both a version and a
                # digest of the thing it downloads.
                pins_version = any(name.endswith("_VERSION") for name in names)
                pins_digest = any(
                    "SHA" in name or name.endswith("_DIGEST") for name in names
                )
                downloads = "urllib.request" in text or "_URL" in names
                if pins_version and pins_digest and downloads:
                    candidates.append(
                        str(path.relative_to(ROOT)).replace("\\", "/")
                    )

        missing = [
            candidate for candidate in candidates
            if candidate not in registered
            and candidate not in COMPLETENESS_EXEMPTIONS
        ]
        self.assertFalse(missing, (
            "these modules pin an external download but are absent from "
            "packaging/pinned_artifacts.py, so the gate certifies them by "
            f"omission: {missing}"
        ))

    def test_an_artifact_osv_cannot_answer_for_still_declares_a_floor(self):
        from pinned_artifacts import pinned_artifacts

        for artifact in pinned_artifacts():
            if artifact.osv_queryable:
                continue
            self.assertTrue(artifact.minimum_safe, (
                f"{artifact.name} {artifact.version} is not OSV-queryable and "
                "declares no minimum_safe, so nothing verifies it"
            ))

    def test_the_stage_fails_when_a_pin_is_below_its_known_fixed_release(self):
        from unittest import mock as _mock

        import pinned_artifacts as registry

        stale = registry.PinnedArtifact(
            name="sqlite", version="3.53.1",
            module="packaging/sqlite_runtime.py",
            ecosystem="", minimum_safe="3.53.2", note="FTS5 overflow",
        )
        with _mock.patch.object(
            registry, "pinned_artifacts", lambda: (stale,)
        ):
            ok, detail = gate.stage_pinned_binaries()
        self.assertFalse(ok)
        self.assertIn("below the known-fixed", detail)

    def test_the_stage_reports_a_non_queryable_pin_it_verified(self):
        from unittest import mock as _mock

        import pinned_artifacts as registry

        current = registry.PinnedArtifact(
            name="sqlite", version="3.53.3",
            module="packaging/sqlite_runtime.py",
            ecosystem="", minimum_safe="3.53.2", note="FTS5 overflow",
        )
        with _mock.patch.object(
            registry, "pinned_artifacts", lambda: (current,)
        ):
            ok, detail = gate.stage_pinned_binaries()
        self.assertTrue(ok, detail)
        self.assertIn("sqlite 3.53.3 >= 3.53.2", detail)


class ReleasePythonFloorTests(unittest.TestCase):
    """The release Python floor is declared in four places and must agree.

    A half-landed bump would let one lane build on an interpreter another lane
    refuses. 3.14.7 carries security content the previous floor admitted an
    interpreter without: tarfile.data_filter path-traversal fixes, libexpat
    2.8.1 (CVE-2026-45186) and pip 26.1 (CVE-2026-3219) (V193).
    """

    _DECLARING_FILES = (
        "packaging/build.py",
        "packaging/release_gate.py",
        "packaging/reproducible_build.py",
        "StreamKeep.spec",
    )

    def _declared_floors(self):
        import re

        found = {}
        for relative in self._DECLARING_FILES:
            text = (ROOT / relative).read_text(encoding="utf-8")
            match = re.search(
                r"^MIN_RELEASE_PYTHON = \((\d+),\s*(\d+),\s*(\d+)\)",
                text, re.MULTILINE,
            )
            self.assertIsNotNone(
                match, f"{relative} no longer declares MIN_RELEASE_PYTHON"
            )
            found[relative] = tuple(int(part) for part in match.groups())
        return found

    def test_every_lane_declares_the_same_floor(self):
        floors = self._declared_floors()
        self.assertEqual(
            len(set(floors.values())), 1,
            f"the release Python floor disagrees between lanes: {floors}",
        )

    def test_no_lane_still_names_a_superseded_floor_in_its_message(self):
        floors = self._declared_floors()
        expected = ".".join(str(part) for part in next(iter(floors.values())))
        for relative in self._DECLARING_FILES:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(expected, text, (
                f"{relative} declares {expected} but its operator-facing "
                "message names a different version"
            ))

    def test_the_readme_states_the_same_release_floor(self):
        floors = self._declared_floors()
        expected = ".".join(str(part) for part in next(iter(floors.values())))
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"Python {expected}", readme, (
            "README.md must state the release floor the lanes enforce"
        ))
