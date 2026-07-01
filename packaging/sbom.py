"""SBOM and dependency-advisory release check.

Generates a CycloneDX SBOM from the current Python environment and
optionally runs a pip-audit compatible advisory scan.

Usage:
    python packaging/sbom.py [--output sbom.json] [--audit]
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _installed_packages():
    """Return installed packages as a list of (name, version) tuples."""
    result = subprocess.run(
        [sys.executable, "-m", "pip", "list", "--format=json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return []
    try:
        return [(p["name"], p["version"]) for p in json.loads(result.stdout)]
    except (json.JSONDecodeError, KeyError):
        return []


def generate_sbom(output_path=None):
    """Generate a CycloneDX 1.5 SBOM JSON. Returns (ok, path_or_error)."""
    packages = _installed_packages()
    if not packages:
        return False, "Could not list installed packages"

    components = []
    for name, version in sorted(packages):
        components.append({
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:pypi/{name.lower()}@{version}",
        })

    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tools": [{"name": "streamkeep-sbom", "version": "1.0.0"}],
            "component": {
                "type": "application",
                "name": "StreamKeep",
            },
        },
        "components": components,
    }

    if not output_path:
        output_path = ROOT / "dist" / "sbom.cdx.json"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sbom, f, indent=2, ensure_ascii=False)

    return True, str(output_path)


def run_advisory_audit():
    """Run pip-audit and return (ok, findings_text).

    Returns (True, "") if no vulnerabilities found.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip_audit", "--format=json", "--progress-spinner=off"],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError:
        return False, "pip-audit not installed (pip install pip-audit)"
    except subprocess.TimeoutExpired:
        return False, "pip-audit timed out"

    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            deps = data.get("dependencies", [])
            vulns = [d for d in deps if d.get("vulns")]
            if not vulns:
                return True, ""
            lines = []
            for d in vulns:
                for v in d["vulns"]:
                    lines.append(
                        f"{d['name']}=={d['version']}: {v['id']} "
                        f"(fix: {v.get('fix_versions', ['?'])})"
                    )
            return False, "\n".join(lines)
        except (json.JSONDecodeError, KeyError):
            return True, ""
    else:
        return False, result.stderr.strip() or result.stdout.strip() or "pip-audit failed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SBOM and advisory check")
    parser.add_argument("-o", "--output", default="", help="SBOM output path")
    parser.add_argument("--audit", action="store_true", help="Run pip-audit advisory scan")
    args = parser.parse_args()

    ok, result = generate_sbom(args.output or None)
    if ok:
        print(f"SBOM: {result}")
    else:
        print(f"SBOM failed: {result}", file=sys.stderr)
        sys.exit(1)

    if args.audit:
        ok, findings = run_advisory_audit()
        if ok:
            print("Advisory: no known vulnerabilities")
        else:
            print(f"Advisory findings:\n{findings}", file=sys.stderr)
            sys.exit(1)
