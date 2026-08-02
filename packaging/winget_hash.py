"""Fill the WinGet manifest's InstallerSha256 from a built artifact (V35).

Releases are unsigned; the published SHA-256 is what proves a download is the
artifact that was built. This computes it from the local installer and writes
it into the manifest so the value can never be a stale placeholder.

    python packaging/winget_hash.py dist/StreamKeep-4.43.5-setup.exe
    python packaging/winget_hash.py --print-only dist/StreamKeep-...-setup.exe
"""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "packaging" / "winget" / "SysAdminDoc.StreamKeep.yaml"
PLACEHOLDER = "0" * 64
_HASH_RE = re.compile(r"(?m)^(\s*InstallerSha256: )([0-9A-Fa-f]{64})$")


def sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def manifest_hash(manifest=MANIFEST) -> str:
    """Return the hash currently recorded in the manifest, or ''."""
    try:
        match = _HASH_RE.search(Path(manifest).read_text(encoding="utf-8"))
    except OSError:
        return ""
    return match.group(2) if match else ""


def is_placeholder(value) -> bool:
    """Return whether a manifest hash is an unfilled placeholder."""
    text = str(value or "").strip()
    return not text or set(text) <= {"0"}


def write_hash(digest, manifest=MANIFEST) -> bool:
    """Write *digest* into the manifest. Returns whether the file changed."""
    path = Path(manifest)
    source = path.read_text(encoding="utf-8")
    updated, count = _HASH_RE.subn(rf"\g<1>{digest}", source, count=1)
    if count != 1:
        raise ValueError(f"Expected one InstallerSha256 line in {path}")
    if updated == source:
        return False
    path.write_text(updated, encoding="utf-8", newline="\n")
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artifact", type=Path, help="Built installer to hash")
    parser.add_argument(
        "--print-only", action="store_true",
        help="Print the hash without editing the manifest",
    )
    args = parser.parse_args(argv)

    if not args.artifact.is_file():
        print(f"No such artifact: {args.artifact}")
        return 2
    digest = sha256(args.artifact)
    print(digest)
    if args.print_only:
        return 0
    changed = write_hash(digest)
    print(
        f"{'Updated' if changed else 'Already current'}: "
        f"{MANIFEST.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
