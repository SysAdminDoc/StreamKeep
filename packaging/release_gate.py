"""One local, unsigned release gate for StreamKeep (V52).

Reproducible-build smoke alone never looked at the source, the tests, the
translation catalogs, the advisory feed, or whether the product's own claims
still matched reachable behaviour. This runs every one of those as an ordered
list of named stages and reports the exact stage that failed.

    python packaging/release_gate.py              # full gate
    python packaging/release_gate.py --fast       # skip the build/artifact stages
    python packaging/release_gate.py --list       # show the stages and exit
    python packaging/release_gate.py --json       # machine-readable result

Deliberately local-only and unsigned: no signing step, no notarization, and no
CI workflow is introduced or required. Exit code 0 means every selected stage
passed; 1 names the first failure.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_PYTHON = (3, 14)
MIN_RELEASE_PYTHON = (3, 14, 6)

# Stages that need a full build environment; --fast skips them so the gate is
# usable as a pre-commit check without paying for a PyInstaller run.
BUILD_STAGES = frozenset({"reproducible-build", "sbom", "artifact-smoke"})


@dataclass
class StageResult:
    name: str
    ok: bool
    detail: str = ""
    seconds: float = 0.0
    skipped: bool = False


@dataclass
class GateResult:
    stages: list[StageResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(stage.ok for stage in self.stages)

    @property
    def failed_stage(self) -> str:
        for stage in self.stages:
            if not stage.ok:
                return stage.name
        return ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "failed_stage": self.failed_stage,
            "stages": [
                {
                    "name": stage.name,
                    "ok": stage.ok,
                    "skipped": stage.skipped,
                    "seconds": round(stage.seconds, 2),
                    "detail": stage.detail,
                }
                for stage in self.stages
            ],
        }


def _run(command, *, cwd=ROOT, timeout=1800) -> tuple[bool, str]:
    """Run one subprocess and return ``(ok, tail_of_output)``."""
    try:
        result = subprocess.run(
            command, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    if result.returncode == 0:
        return True, ""
    output = (result.stderr or "") + (result.stdout or "")
    tail = [line for line in output.strip().splitlines() if line.strip()][-12:]
    return False, "\n".join(tail) or f"exit code {result.returncode}"


# ── Stages ──────────────────────────────────────────────────────────

def release_python_error(version=None) -> str:
    """Return a release-runtime error, or an empty string when supported."""
    current = tuple((version or sys.version_info)[:3])
    if current[:2] != RELEASE_PYTHON or current < MIN_RELEASE_PYTHON:
        return (
            "release artifacts require Python 3.14.6 or newer within the 3.14 "
            f"line; running Python {'.'.join(str(part) for part in current)}"
        )
    return ""


def stage_release_python() -> tuple[bool, str]:
    error = release_python_error()
    if error:
        return False, error
    return True, f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

def stage_compileall() -> tuple[bool, str]:
    """Every shipped module must at least byte-compile."""
    return _run([
        sys.executable, "-m", "compileall", "-q", "-f",
        "StreamKeep.py", "streamkeep", "packaging",
    ])


def stage_pyflakes() -> tuple[bool, str]:
    return _run([sys.executable, "-m", "pyflakes", "streamkeep", "packaging"])


def stage_ruff() -> tuple[bool, str]:
    """Apply the configured lint contract to every Python product/test lane."""
    return _run([
        sys.executable, "-m", "ruff", "check",
        "StreamKeep.py", "streamkeep", "packaging", "tests",
    ])


def stage_translations() -> tuple[bool, str]:
    """Extraction must be deterministic and the compiled assets must match."""
    ok, detail = _run([
        sys.executable, "-m", "streamkeep.i18n.extract_translations", "--check",
    ])
    if not ok:
        return False, detail or "Translation catalogs are stale"
    ok, detail = _run([
        sys.executable, "-m", "streamkeep.i18n.compile_translations", "--check",
    ])
    if not ok:
        return ok, detail
    # V171: report coverage so a catalog regressing becomes visible here
    # rather than only to the user who switches language. This reports, it
    # does not gate: coverage falls legitimately every time UI strings are
    # added faster than they are translated.
    try:
        from streamkeep.i18n import coverage_report

        lines = [
            f"{entry['language']}: {entry['translated']}/{entry['total']} "
            f"({entry['ratio'] * 100:.1f}%)"
            + (" BETA" if entry["beta"] else "")
            for entry in coverage_report() if entry["total"]
        ]
    except Exception as error:
        return True, f"{detail}\ncoverage unavailable: {error}".strip()
    return True, "\n".join([detail, *lines]).strip()


def stage_dependency_floors() -> tuple[bool, str]:
    """Ensure source dependency floors cannot undercut the hashed lock."""
    from locked_requirements import validate_source_floors

    problems = validate_source_floors(
        ROOT / "requirements.txt", ROOT / "requirements.lock"
    )
    return (not problems), "\n".join(problems)


def stage_tests() -> tuple[bool, str]:
    basetemp = Path(tempfile.mkdtemp(prefix="streamkeep-release-tests-"))
    try:
        return _run([
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "--no-cov", "--basetemp", basetemp,
        ], timeout=3600)
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)


def stage_capability_claims() -> tuple[bool, str]:
    """Every shipped claim must have a reachable, tested path and a doc token."""
    sys.path.insert(0, str(ROOT))
    try:
        from streamkeep.capabilities import validate_product_capability_claims
    except ImportError as error:
        return False, f"capability registry could not be imported: {error}"
    problems = validate_product_capability_claims(ROOT)
    return (not problems), "\n".join(problems)


def stage_release_claims() -> tuple[bool, str]:
    """Docs must match the shipped signing and security boundaries."""
    problems = validate_release_claims(ROOT)
    return (not problems), "\n".join(problems)


def stage_advisories() -> tuple[bool, str]:
    """Dependency advisory scan. Advisory-only when pip-audit is absent."""
    ok, detail = _run(
        [sys.executable, str(ROOT / "packaging" / "sbom.py"), "--audit"],
        timeout=900,
    )
    lowered = detail.casefold()
    if not ok and ("pip-audit" in lowered or "pip_audit" in lowered):
        return True, "pip-audit is not installed; advisory scan skipped"
    return ok, detail


def stage_pinned_binaries() -> tuple[bool, str]:
    """Advisory scan for external binaries StreamKeep pins by version.

    ``stage_advisories`` runs pip-audit over ``requirements.lock``, which by
    construction only sees Python wheels. An artefact downloaded as a binary and
    pinned by version plus hash is invisible to it -- the managed Deno runtime
    sat 17 published advisories behind without a single stage noticing.

    The set is not written here: it comes from ``packaging.pinned_artifacts``,
    and ``tests/test_release_gate.py`` derives which modules must appear in that
    registry. This stage previously named one binary while claiming to cover
    every pin, which certified the SQLite DLL by omission (V187).
    """
    import json
    import urllib.error
    import urllib.request

    sys.path.insert(0, str(ROOT))
    from pinned_artifacts import pinned_artifacts, version_tuple

    findings: list[str] = []
    checked: list[str] = []
    for artifact in pinned_artifacts():
        label = f"{artifact.name} {artifact.version}"
        if not artifact.osv_queryable:
            # OSV cannot answer for this artefact, so the declared known-fixed
            # release is the check. Skipping would authorise it silently.
            if not artifact.minimum_safe:
                return False, (
                    f"{label} is neither OSV-queryable nor given a "
                    "minimum_safe version, so nothing verifies it"
                )
            if version_tuple(artifact.version) < version_tuple(artifact.minimum_safe):
                findings.append(
                    f"{label} is below the known-fixed "
                    f"{artifact.minimum_safe} ({artifact.note})"
                )
            else:
                checked.append(f"{label} >= {artifact.minimum_safe}")
            continue
        payload = json.dumps({
            "version": artifact.version,
            "package": {"name": artifact.name, "ecosystem": artifact.ecosystem},
        }).encode("utf-8")
        request = urllib.request.Request(
            "https://api.osv.dev/v1/query",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
            # Fail closed. A network problem must not read as "no advisories" —
            # a check wired to nothing authorises everything.
            return False, (
                f"could not reach the OSV advisory feed for {label}: "
                f"{error}. Re-run with network access, or pass "
                f"--skip pinned-binaries to acknowledge the gap explicitly."
            )
        vulns = body.get("vulns") or []
        if vulns:
            ids = ", ".join(sorted(entry.get("id", "?") for entry in vulns))
            findings.append(f"{label}: {len(vulns)} advisory/ies ({ids})")
        else:
            checked.append(label)
    if findings:
        return False, "; ".join(findings)
    return True, f"no known advisories for {', '.join(checked)}"


def stage_reproducible_build() -> tuple[bool, str]:
    return _run(
        [sys.executable, str(ROOT / "packaging" / "reproducible_build.py")],
        timeout=3600,
    )


def stage_sbom() -> tuple[bool, str]:
    return _run([sys.executable, str(ROOT / "packaging" / "sbom.py")], timeout=900)


def stage_artifact_smoke() -> tuple[bool, str]:
    """Start the built executable and prove it comes up on a clean profile."""
    artifact = built_artifact()
    if not artifact:
        return False, (
            "No built executable was found under dist/. Run the "
            "reproducible-build stage first, or use --fast to skip the build "
            "stages entirely."
        )
    return _run(
        [
            sys.executable, str(ROOT / "packaging" / "artifact_smoke.py"),
            "--executable", str(artifact),
        ],
        timeout=1800,
    )


def built_artifact() -> Path | None:
    """Return the freshly built application executable, or ``None``.

    Explicitly named rather than globbed: ``dist/`` also holds the installer
    (``StreamKeep-<version>-setup.exe``), and smoking the installer instead of
    the application would be both meaningless and a live installation attempt.
    """
    for candidate in (
        ROOT / "dist" / "StreamKeep" / "StreamKeep.exe",   # onedir (default)
        ROOT / "dist" / "StreamKeep.exe",                  # legacy onefile
    ):
        if candidate.is_file():
            return candidate
    return None


STAGES = (
    ("release-python", stage_release_python),
    ("compileall", stage_compileall),
    ("pyflakes", stage_pyflakes),
    ("ruff", stage_ruff),
    ("translations", stage_translations),
    ("dependency-floors", stage_dependency_floors),
    ("tests", stage_tests),
    ("capability-claims", stage_capability_claims),
    ("release-claims", stage_release_claims),
    ("advisories", stage_advisories),
    ("pinned-binaries", stage_pinned_binaries),
    ("reproducible-build", stage_reproducible_build),
    ("sbom", stage_sbom),
    ("artifact-smoke", stage_artifact_smoke),
)


# ── Release-claim consistency ───────────────────────────────────────

# Phrases that would promise a signed release. This project ships unsigned by
# policy, so documentation must not describe signing as a required step.
_SIGNING_CLAIMS = (
    "requires `STREAMKEEP_SIGN_PFX`",
    "signs each asset by default",
    "notarization is required",
)
# The gate is about honesty, not vocabulary: a line that explains the *absence*
# of signing is exactly what we want to see.
_UNSIGNED_MARKERS = (
    "unsigned",
    "not code signed",
    "no code signing",
)
_PAGE_COUNT_CLAIM = re.compile(r"\b(\d+)-page\s+QStackedWidget\b", re.IGNORECASE)
_LOOPBACK_CLAIM = "local server always binds to `127.0.0.1`"
_CONSTANT_TIME_CLAIM = "validates bearer tokens in constant time"
_MASTER_TOKEN_CLAIM = "never accepted in argv"
_MASTER_TOKEN_CLAIM_PARTS = (
    "placed in urls",
    "written to logs",
)


def _read_tab_registry(root) -> tuple[str, ...] | None:
    """Read the literal tab registry without importing the Qt application."""
    source_path = Path(root) / "streamkeep" / "ui" / "main_window.py"
    try:
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(source_path))
    except (OSError, SyntaxError):
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = (node.target,)
        else:
            continue
        if not any(
            isinstance(target, ast.Attribute) and target.attr == "_tab_names"
            for target in targets
        ):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            return None
        names = []
        for element in node.value.elts:
            if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                return None
            names.append(element.value)
        return tuple(names)
    return None


def _validate_page_count_claim(root) -> list[str]:
    """Ensure CLAUDE's page count still matches the shipped tab registry."""
    root = Path(root)
    try:
        notes = (root / "CLAUDE.md").read_text(encoding="utf-8")
    except OSError as error:
        return [f"CLAUDE.md could not be read for the page-count claim: {error}"]

    claims = [int(match.group(1)) for match in _PAGE_COUNT_CLAIM.finditer(notes)]
    if not claims:
        return [
            "CLAUDE.md must state the current page count as '<N>-page "
            "QStackedWidget'"
        ]
    if len(set(claims)) != 1:
        return [
            "CLAUDE.md contains conflicting QStackedWidget page-count claims: "
            + ", ".join(str(count) for count in claims)
        ]

    registry = _read_tab_registry(root)
    if registry is None:
        return [
            "streamkeep/ui/main_window.py does not expose a literal "
            "self._tab_names registry for release-claim validation"
        ]
    claimed_count = claims[0]
    if claimed_count != len(registry):
        return [
            "CLAUDE.md states a "
            f"{claimed_count}-page QStackedWidget but the shipped tab registry "
            f"contains {len(registry)} pages: {', '.join(registry)}"
        ]
    return []


def _ast_callable_name(node) -> str:
    """Return the final name of a call target for small source invariants."""
    target = getattr(node, "func", node)
    return str(getattr(target, "attr", "") or getattr(target, "id", ""))


def _ast_uses_names(node, names) -> bool:
    return any(
        isinstance(child, ast.Name) and child.id in names
        for child in ast.walk(node)
    )


def _validate_companion_token_claim(root) -> list[str]:
    """Check the source facts behind README's master-token security claim."""
    source_path = Path(root) / "streamkeep" / "cli.py"
    try:
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(source_path))
    except (OSError, SyntaxError) as error:
        return [f"streamkeep/cli.py could not be checked for token claims: {error}"]

    problems: list[str] = []
    argv_literals = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == "--token"
    ]
    if argv_literals:
        lines = ", ".join(str(node.lineno) for node in argv_literals)
        problems.append(
            "README says the master bearer token is not accepted in argv, but "
            f"streamkeep/cli.py still contains --token at line(s) {lines}"
        )

    if "_COMPANION_TOKEN_ENV" not in source or (
        "STREAMKEEP_COMPANION_TOKEN" not in source
        or "os.environ.get" not in source
    ):
        problems.append(
            "README documents STREAMKEEP_COMPANION_TOKEN, but the CLI does not "
            "read that environment variable"
        )
    if "sys.stdin.readline" not in source or "master_token_stdin" not in source:
        problems.append(
            "README documents --master-token-stdin, but the CLI does not read "
            "one token line from stdin"
        )

    token_function = next(
        (
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_run_tokens"
        ),
        None,
    )
    if token_function is None:
        return problems + ["streamkeep/cli.py has no _run_tokens implementation"]

    secret_names = {"token", "master_token"}
    request_calls = [
        node for node in ast.walk(token_function)
        if isinstance(node, ast.Call) and _ast_callable_name(node) == "Request"
    ]
    if not request_calls:
        problems.append(
            "The token CLI has no inspectable Request construction for its URL guard"
        )
    for request in request_calls:
        if not request.args:
            problems.append("The token CLI constructs a Request without a URL")
        elif _ast_uses_names(request.args[0], secret_names):
            problems.append(
                "README says the master bearer token is not placed in URLs, but "
                "the token CLI uses it in a Request URL"
            )

    output_calls = {
        "_print_line", "print", "write_log_line", "log", "debug", "info",
        "warning", "error", "critical",
    }
    for call in ast.walk(token_function):
        if isinstance(call, ast.Call) and _ast_callable_name(call) in output_calls:
            if any(_ast_uses_names(argument, secret_names) for argument in call.args):
                problems.append(
                    "README says the master bearer token is not printed or logged, "
                    "but _run_tokens sends it to an output/log call"
                )
                break
    return problems


def _validate_security_claims(root, readme) -> list[str]:
    """Validate the README's explicit always/never security promises."""
    lowered = readme.casefold()
    problems: list[str] = []

    if _LOOPBACK_CLAIM in lowered:
        server_path = Path(root) / "streamkeep" / "server" / "_legacy.py"
        try:
            server_source = server_path.read_text(encoding="utf-8")
        except OSError as error:
            problems.append(f"loopback binding claim could not be checked: {error}")
        else:
            if '_bind_addr = "127.0.0.1"' not in server_source:
                problems.append(
                    "README says the local server always binds to 127.0.0.1, "
                    "but the server source has no loopback bind invariant"
                )

    if _CONSTANT_TIME_CLAIM in lowered:
        auth_path = Path(root) / "streamkeep" / "server" / "auth.py"
        try:
            auth_source = auth_path.read_text(encoding="utf-8")
        except OSError as error:
            problems.append(f"constant-time token claim could not be checked: {error}")
        else:
            if "def check(" not in auth_source or "secrets.compare_digest" not in auth_source:
                problems.append(
                    "README says bearer tokens are validated in constant time, "
                    "but TokenStore.check has no compare_digest check"
                )

    if _MASTER_TOKEN_CLAIM not in lowered:
        problems.append(
            "README must state that the master bearer token is never accepted "
            "in argv"
        )
    elif any(part not in lowered for part in _MASTER_TOKEN_CLAIM_PARTS):
        problems.append(
            "README's master-token claim must cover argv, URLs, and logs"
        )
    else:
        problems.extend(_validate_companion_token_claim(root))
    return problems


def validate_release_claims(root) -> list[str]:
    """Return documentation claims that contradict the shipped product."""
    root = Path(root)
    problems: list[str] = _validate_page_count_claim(root)

    try:
        readme = (root / "README.md").read_text(encoding="utf-8")
    except OSError as error:
        return [f"README.md could not be read: {error}"]

    for claim in _SIGNING_CLAIMS:
        if claim in readme:
            problems.append(
                f"README promises a signing step this project never performs: {claim!r}"
            )
    if not any(marker in readme.casefold() for marker in _UNSIGNED_MARKERS):
        problems.append(
            "README does not state that releases are unsigned and updated "
            "manually or by a package manager"
        )

    problems.extend(_validate_security_claims(root, readme))

    # The Spanish catalog is partial; it must be labelled as such wherever the
    # product advertises language support.
    translated, total = spanish_coverage(root)
    if total and translated < total:
        if "beta" not in readme.casefold():
            problems.append(
                f"Spanish is {translated}/{total} translated but README does "
                "not label it beta"
            )

    metainfo = root / "packaging" / "flatpak" / (
        "com.github.SysAdminDoc.StreamKeep.metainfo.xml"
    )
    try:
        metainfo_text = metainfo.read_text(encoding="utf-8")
    except OSError:
        metainfo_text = ""
    if metainfo_text:
        problems.extend(_validate_metainfo_claims(metainfo_text, root))
    return problems


def _validate_metainfo_claims(text, root) -> list[str]:
    """Flag store metadata that advertises experimental capabilities as shipped."""
    sys.path.insert(0, str(root))
    try:
        from streamkeep.capabilities import get_product_capability_claims
    except ImportError:
        return []
    problems = []
    lowered = text.casefold()
    # Only phrases specific enough to be a genuine promise are checked; the
    # goal is catching "ships X" for an X that is not reachable.
    experimental_tokens = {
        "upload-delivery": "upload destinations",
        "plugin-adapters": "plugin sdk",
    }
    for claim in get_product_capability_claims(status="experimental"):
        token = experimental_tokens.get(claim.id)
        if not token or token not in lowered:
            continue
        window_start = max(0, lowered.index(token) - 200)
        window = lowered[window_start:lowered.index(token) + 200]
        if "experimental" not in window:
            problems.append(
                f"Flatpak metainfo advertises {token!r} without marking the "
                f"{claim.id} capability experimental"
            )
    return problems


def spanish_coverage(root) -> tuple[int, int]:
    """Return ``(translated, total)`` message counts for the Spanish catalog."""
    catalog = Path(root) / "streamkeep" / "i18n" / "streamkeep_es.ts"
    try:
        text = catalog.read_text(encoding="utf-8")
    except OSError:
        return (0, 0)
    total = text.count("<message")
    unfinished = text.count('type="unfinished"')
    return (max(0, total - unfinished), total)


# ── Driver ──────────────────────────────────────────────────────────

def run_gate(*, fast=False, only=(), skip=(), echo=print) -> GateResult:
    """Run the gate and return every stage result, stopping at the first failure."""
    result = GateResult()
    selected = set(only or ())
    skipped = set(skip or ())
    for name, func in STAGES:
        if selected and name not in selected:
            continue
        if name in skipped:
            result.stages.append(
                StageResult(name, True, "skipped (--skip)", skipped=True)
            )
            if echo:
                echo(f"  SKIP  {name} (--skip)")
            continue
        if fast and name in BUILD_STAGES:
            result.stages.append(
                StageResult(name, True, "skipped (--fast)", skipped=True)
            )
            if echo:
                echo(f"  SKIP  {name}")
            continue
        started = time.monotonic()
        ok, detail = func()
        elapsed = time.monotonic() - started
        result.stages.append(StageResult(name, ok, detail, elapsed))
        if echo:
            echo(f"  {'PASS' if ok else 'FAIL'}  {name} ({elapsed:.1f}s)")
            if detail and not ok:
                for line in detail.splitlines():
                    echo(f"        {line}")
        if not ok:
            break
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fast", action="store_true",
        help="Skip the build, SBOM, and artifact stages",
    )
    parser.add_argument(
        "--only", action="append", default=[],
        help="Run only the named stage (repeatable)",
    )
    parser.add_argument(
        "--skip", action="append", default=[],
        help=(
            "Skip the named stage (repeatable). Reported as SKIP so the gap is "
            "visible rather than silently passing."
        ),
    )
    parser.add_argument("--list", action="store_true", help="List stages and exit")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    if args.list:
        for name, _func in STAGES:
            marker = " (build)" if name in BUILD_STAGES else ""
            print(f"{name}{marker}")
        return 0

    unknown = (set(args.only) | set(args.skip)) - {name for name, _ in STAGES}
    if unknown:
        print(f"Unknown stage(s): {', '.join(sorted(unknown))}")
        return 2

    echo = None if args.json else print
    if echo:
        echo("StreamKeep release gate (local, unsigned)")
    result = run_gate(fast=args.fast, only=args.only, skip=args.skip, echo=echo)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    elif result.ok:
        print("Release gate passed.")
    else:
        print(f"Release gate FAILED at stage: {result.failed_stage}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
