"""The registry of external artifacts StreamKeep pins by version.

``stage_advisories`` runs pip-audit over ``requirements.lock``, which by
construction only sees Python wheels. Anything downloaded as a binary and pinned
by version plus hash is invisible to it -- the managed Deno runtime sat 17
published advisories behind without a single stage noticing.

The stage added to close that gap had a hand-written list naming exactly one
binary while its docstring claimed it covered "every version StreamKeep pins",
so the SHA3-pinned SQLite DLL was certified by omission (V187). A gate whose
scope is a hand-written list certifies whatever is not in it, so the list lives
here as one declaration, and ``tests/test_release_gate.py`` derives the set of
modules that *should* be in it and fails when one is missing.

Each entry states how it is verified. OSV can answer for some ecosystems and not
others; an artefact OSV cannot answer for declares a ``minimum_safe`` version
instead, so it is still checked rather than skipped.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class PinnedArtifact:
    """One externally-pinned artefact and how its currency is verified."""

    name: str
    version: str
    #: Dotted module that declares the pin, for the completeness check.
    module: str
    #: OSV ecosystem, or "" when OSV has no queryable package for it.
    ecosystem: str = ""
    #: Required when ``ecosystem`` is "": the lowest version known to be free
    #: of the advisories that matter for this artefact.
    minimum_safe: str = ""
    note: str = ""

    @property
    def osv_queryable(self) -> bool:
        return bool(self.ecosystem)


def version_tuple(value) -> tuple[int, ...]:
    """Return a comparable tuple for a dotted version string."""
    parts = []
    for chunk in str(value or "").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def pinned_artifacts() -> tuple[PinnedArtifact, ...]:
    """Return every externally-pinned artefact, read from its own module."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    from streamkeep.javascript_runtime import (
        DENO_MINIMUM_VERSION,
        DENO_VERSION,
    )
    from streamkeep.sqlite_runtime import FTS5_FIXED_SQLITE_RELEASE

    # ``packaging/`` is a directory of scripts, not a package -- a bare import
    # is the convention here, and ``packaging`` itself is a PyPI distribution
    # whose name would shadow a dotted import.
    from sqlite_runtime import SQLITE_VERSION

    fts5_floor = ".".join(str(part) for part in FTS5_FIXED_SQLITE_RELEASE)
    return (
        PinnedArtifact(
            name="deno",
            version=DENO_VERSION,
            module="streamkeep/javascript_runtime.py",
            ecosystem="crates.io",
            note="executes untrusted remote player JavaScript",
        ),
        PinnedArtifact(
            name="deno",
            version=DENO_MINIMUM_VERSION,
            module="streamkeep/javascript_runtime.py",
            ecosystem="crates.io",
            note="the floor a reused system Deno must clear",
        ),
        PinnedArtifact(
            name="sqlite",
            version=SQLITE_VERSION,
            module="packaging/sqlite_runtime.py",
            # OSV has no package for the SQLite amalgamation/DLL: its advisories
            # are published as plain CVEs against the project, not against an
            # entry in a package ecosystem. Checked against the declared
            # known-fixed release instead of skipped.
            ecosystem="",
            minimum_safe=fts5_floor,
            note=(
                "the FTS5 heap-overflow advisories (CVE-2026-11822/11824) are "
                f"fixed in {fts5_floor}"
            ),
        ),
    )


#: Modules exempt from the completeness check, with the reason. A module that
#: pins a version and a hash but is not an *external download* does not belong
#: in the registry.
COMPLETENESS_EXEMPTIONS = {
    "streamkeep/update_security.py": (
        "verifies StreamKeep's own release manifests; it pins no third-party "
        "artefact of its own"
    ),
    "streamkeep/integrity.py": (
        "hashes the user's archive contents rather than pinning a download"
    ),
    "streamkeep/verify.py": (
        "hashes the user's archive contents rather than pinning a download"
    ),
    "streamkeep/importer.py": (
        "hashes adopted sidecars rather than pinning a download"
    ),
    "packaging/reproducible_build.py": (
        "hashes the artefacts this repo builds, not a pinned download"
    ),
    "packaging/winget_hash.py": (
        "hashes the installer this repo builds, not a pinned download"
    ),
    "packaging/sbom.py": (
        "records hashes of the locked wheels, which stage_advisories covers"
    ),
}
