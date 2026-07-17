"""Build StreamKeep distribution icons from the checked-in PNG master.

The canonical artwork is ``icon.png`` at the repository root.  This tool
normalizes an optional replacement source to 1024x1024, then derives the
multi-resolution Windows icons and the Flatpak 512px asset from that master.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "icon.png"
ROOT_ICO = ROOT / "icon.ico"
ASSET_ICO = ROOT / "assets" / "icon.ico"
FLATPAK_ICON = ROOT / "packaging" / "flatpak" / "icon-512.png"
BROWSER_ICON_DIR = ROOT / "browser-extension" / "icons"
MASTER_SIZE = (1024, 1024)
ICO_SIZES = (
    (16, 16),
    (24, 24),
    (32, 32),
    (48, 48),
    (64, 64),
    (128, 128),
    (256, 256),
)
BROWSER_SIZES = (16, 32, 48, 128)


def _load_rgba(path: Path) -> Image.Image:
    with Image.open(path) as source:
        return source.convert("RGBA")


def _normalize_master(source: Path | None) -> Image.Image:
    artwork_path = source or MASTER
    artwork = _load_rgba(artwork_path)
    if artwork.size != MASTER_SIZE:
        artwork = artwork.resize(MASTER_SIZE, Image.Resampling.LANCZOS)
    artwork.save(MASTER, "PNG", optimize=True)
    return artwork


def build_icons(source: Path | None = None) -> None:
    master = _normalize_master(source)
    for destination in (ROOT_ICO, ASSET_ICO):
        master.save(destination, format="ICO", sizes=ICO_SIZES)
    master.resize((512, 512), Image.Resampling.LANCZOS).save(
        FLATPAK_ICON,
        "PNG",
        optimize=True,
    )
    for size in BROWSER_SIZES:
        master.resize((size, size), Image.Resampling.LANCZOS).save(
            BROWSER_ICON_DIR / f"{size}.png",
            "PNG",
            optimize=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        help="Optional replacement artwork; becomes the canonical icon.png master.",
    )
    args = parser.parse_args()
    source = args.source.resolve() if args.source else None
    if source is not None and not source.is_file():
        parser.error(f"source image not found: {source}")
    build_icons(source)
    print(f"Wrote {MASTER.relative_to(ROOT)} ({MASTER_SIZE[0]}x{MASTER_SIZE[1]})")
    print(f"Wrote {ROOT_ICO.relative_to(ROOT)} and {ASSET_ICO.relative_to(ROOT)}")
    print(f"Wrote {FLATPAK_ICON.relative_to(ROOT)} (512x512)")
    print(f"Wrote browser companion icons ({', '.join(map(str, BROWSER_SIZES))}px)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
