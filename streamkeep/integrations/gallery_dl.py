"""gallery-dl integration — optional second engine for image galleries and
social-media posts (V10).

gallery-dl (https://github.com/mikf/gallery-dl) downloads image/media
collections from sites the video pipeline handles poorly: Twitter/X media,
Instagram posts, Pixiv, boorus, DeviantArt, Reddit galleries, Tumblr, Flickr,
and more. StreamKeep shells out to it as a separate process (never bundled),
sharing the configured output folder, download-archive, cookies, and proxy.

When gallery-dl is absent, callers get a clear install hint via
``gallery_dl_install_hint()`` instead of an opaque failure.
"""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

_MODULE = "gallery_dl"
_EXECUTABLE = "gallery-dl"

_IMAGE_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif",
})
_PACKAGE_EXTS = frozenset({".cbz", ".zip"})
_INFO_NAMES = frozenset({"info.json"})
_MAX_COVER_BYTES = 16 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_INFO_BYTES = 32 * 1024 * 1024
_MAX_GALLERY_FILES = 100_000

# Hosts gallery-dl covers well that are a poor fit for the streaming/ffmpeg
# pipeline. Matched against the URL host (case-insensitive). This is a routing
# hint, not an allow-list — gallery-dl itself supports far more sites.
_GALLERY_HOST_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"(?:^|\.)twitter\.com$",
    r"(?:^|\.)x\.com$",
    r"(?:^|\.)nitter\.",
    r"(?:^|\.)instagram\.com$",
    r"(?:^|\.)pixiv\.net$",
    r"(?:^|\.)deviantart\.com$",
    r"(?:^|\.)flickr\.com$",
    r"(?:^|\.)tumblr\.com$",
    r"(?:^|\.)redgifs\.com$",
    r"(?:^|\.)imgur\.com$",
    r"(?:^|\.)artstation\.com$",
    r"(?:^|\.)newgrounds\.com$",
    r"(?:^|\.)gelbooru\.com$",
    r"(?:^|\.)safebooru\.org$",
    r"(?:^|\.)danbooru\.donmai\.us$",
    r"(?:^|\.)konachan\.com$",
    r"(?:^|\.)yande\.re$",
))


class GalleryDlUnavailable(RuntimeError):
    """Raised when a gallery-dl operation is requested but it is not installed."""


def gallery_dl_available():
    """Return True when gallery-dl can be invoked (module or executable)."""
    try:
        if importlib.util.find_spec(_MODULE) is not None:
            return True
    except (ImportError, ValueError):
        pass
    return shutil.which(_EXECUTABLE) is not None


def gallery_dl_command_prefix():
    """Return the argv prefix that invokes gallery-dl.

    Prefers ``python -m gallery_dl`` (same interpreter, version we detected)
    and falls back to a PATH executable. Raises ``GalleryDlUnavailable`` when
    neither is present.
    """
    try:
        if importlib.util.find_spec(_MODULE) is not None:
            return [sys.executable, "-m", _MODULE]
    except (ImportError, ValueError):
        pass
    exe = shutil.which(_EXECUTABLE)
    if exe:
        return [exe]
    raise GalleryDlUnavailable(gallery_dl_install_hint())


def gallery_dl_install_hint():
    """Return a one-line install hint for when gallery-dl is missing."""
    return (
        "gallery-dl is not installed. Install it with "
        "'python -m pip install -U gallery-dl' to download image galleries and "
        "social-media posts (Twitter/X, Instagram, Pixiv, boorus, and more)."
    )


def is_gallery_host(url):
    """Return True when *url*'s host is one gallery-dl is a better fit for."""
    host = _url_host(url)
    if not host:
        return False
    return any(pattern.search(host) for pattern in _GALLERY_HOST_PATTERNS)


def _url_host(url):
    from urllib.parse import urlsplit
    try:
        host = urlsplit(str(url or "").strip()).hostname or ""
    except ValueError:
        return ""
    return host.rstrip(".").lower()


def build_gallery_dl_command(
    url,
    dest_dir,
    *,
    archive_path="",
    cookies_file="",
    proxy="",
    simulate=False,
    rate_limit="",
    package_format="",
    write_info_json=False,
    extra_options=None,
):
    """Build the gallery-dl argv for *url* into *dest_dir*.

    Shares StreamKeep's output folder, download-archive, cookies, and proxy so
    galleries land alongside video downloads and re-runs skip already-fetched
    files. A URL beginning with ``-`` is rejected so it can't be smuggled as an
    option (gallery-dl has no ``--`` argument terminator).
    """
    text = str(url or "").strip()
    if not text:
        raise ValueError("gallery-dl requires a URL")
    if text.startswith("-"):
        raise ValueError("Download URL cannot begin with a dash")
    package_format = _normalize_package_format(package_format)

    cmd = gallery_dl_command_prefix()
    if dest_dir:
        cmd += ["--destination", str(dest_dir)]
    if archive_path:
        cmd += ["--download-archive", str(archive_path)]
    if cookies_file:
        cmd += ["--cookies", str(cookies_file)]
    if proxy:
        cmd += ["--proxy", str(proxy)]
    if rate_limit:
        cmd += ["--limit-rate", str(rate_limit)]
    if package_format:
        cmd += [f"--{package_format}"]
    if package_format or write_info_json:
        cmd += ["--write-info-json"]
    if simulate:
        cmd += ["--simulate"]
    for key, value in (extra_options or {}).items():
        cmd += ["--option", f"{key}={value}"]
    cmd.append(text)
    return cmd


def _normalize_package_format(value):
    """Return the supported gallery-dl package format or an empty string."""
    normalized = str(value or "").strip().lower()
    if normalized in {"", "none"}:
        return ""
    if normalized not in {"cbz", "zip"}:
        raise ValueError("gallery package format must be cbz, zip, or none")
    return normalized


def _normalized_path(path):
    try:
        return os.path.normcase(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError, ValueError):
        return ""


def _candidate_name(path, package_format=""):
    name = Path(path).name.casefold()
    suffix = Path(name).suffix
    return (
        suffix in _IMAGE_EXTS
        or name in _INFO_NAMES
        or name.endswith(".info.json")
        or suffix == ".cbz"
        or (suffix == ".zip" and package_format == "zip")
    )


def _iter_candidate_files(root, package_format=""):
    """Yield bounded, regular files relevant to a gallery-dl ingest."""
    root = os.fspath(root or "")
    if not root or not os.path.isdir(root):
        return
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames
            if not name.startswith(".")
        )
        for filename in sorted(filenames, key=str.casefold):
            if filename.startswith(".") or not _candidate_name(
                Path(dirpath) / filename, package_format,
            ):
                continue
            path = Path(dirpath) / filename
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            yield path
            count += 1
            if count >= _MAX_GALLERY_FILES:
                return


def snapshot_gallery_output(root, *, package_format=""):
    """Capture candidate file signatures before a gallery-dl process runs."""
    package_format = _normalize_package_format(package_format)
    snapshot = {}
    for path in _iter_candidate_files(root, package_format):
        try:
            stat = path.stat()
        except OSError:
            continue
        key = _normalized_path(path)
        if key:
            snapshot[key] = (
                int(stat.st_size),
                int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9))),
            )
    return snapshot


def _file_changed(path, before):
    key = _normalized_path(path)
    if not key:
        return False
    try:
        stat = Path(path).stat()
        signature = (
            int(stat.st_size),
            int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9))),
        )
    except OSError:
        return False
    if isinstance(before, dict):
        return before.get(key) != signature
    if before:
        return key not in {
            _normalized_path(value) for value in before
        }
    return True


def _bounded_text(value, limit=100_000):
    if isinstance(value, dict):
        for key in ("name", "display_name", "username", "title", "value"):
            if key in value:
                return _bounded_text(value.get(key), limit)
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _bounded_text(item, limit)
            if text:
                return text
        return ""
    try:
        from ..metadata import scrub_public_text
        return scrub_public_text(value).strip()[:limit]
    except (TypeError, ValueError):
        return ""


def _first_value(info, *keys, limit=100_000):
    if not isinstance(info, dict):
        return ""
    for key in keys:
        value = _bounded_text(info.get(key), limit)
        if value:
            return value
    return ""


def _safe_identity_component(value, fallback="item"):
    text = _bounded_text(value, 160)
    text = re.sub(r"[^A-Za-z0-9._:@/-]+", "-", text).strip("-./:")
    return text[:128] or fallback


def _read_gallery_info(path):
    if not path:
        return {}
    try:
        if not path.is_file() or path.stat().st_size > _MAX_INFO_BYTES:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), {})
    return {}


def _find_info_sidecar(directory):
    try:
        paths = sorted(
            (path for path in Path(directory).iterdir() if path.is_file()),
            key=lambda path: path.name.casefold(),
        )
    except OSError:
        return None
    for path in paths:
        lowered = path.name.casefold()
        if lowered == "info.json":
            return path
    for path in paths:
        if path.name.casefold().endswith(".info.json"):
            return path
    return None


def _safe_zip_member(info):
    name = str(getattr(info, "filename", "") or "").replace("\\", "/")
    if not name or "\x00" in name or name.startswith("/"):
        return False
    parts = PurePosixPath(name).parts
    if ".." in parts or getattr(info, "is_dir", lambda: False)():
        return False
    mode = (int(getattr(info, "external_attr", 0) or 0) >> 16) & 0o170000
    return mode != 0o120000


def _atomic_bytes(path, data):
    temporary = path.with_name(path.name + ".tmp")
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


def _extract_package_cover(directory, package_paths):
    """Extract only the first bounded image from a CBZ/ZIP as a cover."""
    cover_path = None
    for archive in sorted(package_paths, key=lambda path: path.name.casefold()):
        try:
            if archive.stat().st_size > _MAX_ARCHIVE_BYTES:
                continue
            with zipfile.ZipFile(archive) as bundle:
                members = sorted(bundle.infolist(), key=lambda item: item.filename.casefold())
                for member in members[:_MAX_GALLERY_FILES]:
                    if not _safe_zip_member(member):
                        continue
                    suffix = Path(member.filename).suffix.casefold()
                    if suffix not in _IMAGE_EXTS:
                        continue
                    if int(getattr(member, "file_size", 0) or 0) > _MAX_COVER_BYTES:
                        continue
                    with bundle.open(member) as handle:
                        data = handle.read(_MAX_COVER_BYTES + 1)
                    if not data or len(data) > _MAX_COVER_BYTES:
                        continue
                    cover_path = Path(directory) / f"streamkeep-cover{suffix}"
                    if cover_path.exists():
                        return cover_path
                    return _atomic_bytes(cover_path, data)
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
            continue
    return ""


def _direct_media_files(directory, package_format=""):
    allowed_packages = {".cbz"}
    if package_format == "zip":
        allowed_packages.add(".zip")
    try:
        files = []
        for path in Path(directory).iterdir():
            if not path.is_file() or path.name.startswith("."):
                continue
            suffix = path.suffix.casefold()
            if suffix in _IMAGE_EXTS or suffix in allowed_packages:
                files.append(path)
        return sorted(files, key=lambda path: path.name.casefold())
    except OSError:
        return []


def _format_size(size):
    if size < 1024:
        return f"{size} B"
    if size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size / 1024 ** 2:.1f} MB"
    return f"{size / 1024 ** 3:.2f} GB"


def _set_identity(info, directory, root, source_url):
    from ..metadata import build_archival_provenance
    from ..models import StreamInfo

    category = _safe_identity_component(
        _first_value(info, "category", "extractor", "extractor_key"),
        fallback="gallery",
    )
    item_url = _first_value(
        info, "webpage_url", "original_url", "post_url", "url", limit=4096,
    ) or _bounded_text(source_url, 4096)
    explicit = _first_value(
        info, "id", "post_id", "gallery_id", "display_id", limit=160,
    )
    try:
        relative = os.path.relpath(directory, root)
    except (TypeError, ValueError):
        relative = os.path.basename(directory)
    if explicit:
        source_id = f"gallery:{category}:{_safe_identity_component(explicit)}"
    else:
        digest = hashlib.sha256(
            f"{item_url}\x00{relative}".encode("utf-8", errors="replace")
        ).hexdigest()[:32]
        source_id = f"gallery:{category}:{digest}"
    title = _first_value(info, "title", "name", "caption", limit=100_000)
    channel = _first_value(
        info, "author", "uploader", "user", "username", "artist", limit=256,
    )
    description = _first_value(info, "description", "caption", limit=100_000)
    provenance = build_archival_provenance(
        StreamInfo(
            platform="gallery-dl",
            channel=channel,
            title=title,
            description=description,
            source_id=source_id,
            webpage_url=item_url,
        ),
        source_url=item_url or source_url,
    )
    return provenance, title, channel, description, info


def _save_gallery_metadata(
    directory,
    provenance,
    title,
    channel,
    description,
    *,
    package_format="",
):
    from ..metadata import MetadataSaver, load_metadata_sidecar
    from ..models import StreamInfo

    metadata_path = Path(directory) / "metadata.json"
    if metadata_path.is_file():
        return load_metadata_sidecar(metadata_path)
    info = StreamInfo(
        platform=provenance.platform,
        channel=channel,
        title=title or Path(directory).name,
        description=description,
        source_id=provenance.source_id,
        webpage_url=provenance.webpage_url,
    )
    info.tags = [
        "gallery-dl",
        *([f"package:{package_format}"] if package_format else []),
    ]
    MetadataSaver.save(
        str(directory), info, source_url=provenance.webpage_url,
    )
    return load_metadata_sidecar(metadata_path)


def _history_is_duplicate(rows, path, provenance):
    target = _normalized_path(path)
    identity = (
        str(provenance.platform or ""),
        str(provenance.source_id or ""),
    )
    for row in rows:
        if not isinstance(row, dict):
            continue
        if identity[0] and identity[1] and (
            str(row.get("platform", "")), str(row.get("source_id", ""))
        ) == identity:
            return row
        if target and _normalized_path(row.get("path", "")) == target:
            return row
    return None


def ingest_gallery_output(
    root,
    source_url="",
    *,
    before_paths=None,
    package_format="",
    db_module=None,
):
    """Register newly downloaded gallery-dl image sets in StreamKeep.

    The filesystem remains the source of truth: each set directory receives
    one normal ``metadata.json`` sidecar and one history row. Original
    gallery-dl ``info.json`` files are never rewritten. Package-only sets get
    a bounded cover extracted from the archive so the authenticated local
    gallery can render them without unpacking the full archive.
    """
    package_format = _normalize_package_format(package_format)
    root_path = Path(root or "").expanduser()
    if not root_path.is_dir():
        return {
            "ingested": 0, "skipped": 0,
            "errors": [f"Gallery output directory not found: {root_path}"],
            "entries": [],
        }
    before = before_paths if before_paths is not None else {}
    try:
        from .. import db as default_db
        db_module = db_module or default_db
        history_rows = list(db_module.load_history() or [])
    except Exception as error:
        return {
            "ingested": 0, "skipped": 0,
            "errors": [f"Could not read StreamKeep history: {error}"],
            "entries": [],
        }

    directories = {}
    for path in _iter_candidate_files(root_path, package_format):
        if _file_changed(path, before):
            directories.setdefault(path.parent, True)

    result = {"ingested": 0, "skipped": 0, "errors": [], "entries": []}
    for directory in sorted(directories, key=lambda path: str(path).casefold()):
        files = _direct_media_files(directory, package_format)
        image_files = [path for path in files if path.suffix.casefold() in _IMAGE_EXTS]
        allowed_packages = {".cbz"}
        if package_format == "zip":
            allowed_packages.add(".zip")
        package_files = [
            path for path in files
            if path.suffix.casefold() in allowed_packages
        ]
        if not image_files and package_files:
            cover = _extract_package_cover(directory, package_files)
            if cover:
                image_files = [cover]
        if not image_files:
            result["errors"].append(
                f"{directory}: no supported image or readable package cover found"
            )
            continue

        info_path = _find_info_sidecar(directory)
        info = _read_gallery_info(info_path)
        provenance, title, channel, description, _ = _set_identity(
            info, str(directory), str(root_path), source_url,
        )
        if _history_is_duplicate(history_rows, str(directory), provenance):
            result["skipped"] += 1
            continue
        try:
            _save_gallery_metadata(
                directory,
                provenance,
                title or directory.name,
                channel,
                description,
                package_format=package_format,
            )
            total_size = sum(
                path.stat().st_size
                for path in _direct_media_files(directory, package_format)
                if path.is_file()
            )
            if total_size <= 0:
                total_size = sum(path.stat().st_size for path in image_files if path.is_file())
            date = _first_value(info, "date", "upload_date", "created_at", limit=64)
            if not date:
                date = datetime.now(timezone.utc).isoformat(timespec="seconds")
            entry = {
                "date": date,
                "platform": provenance.platform or "gallery-dl",
                "source_id": provenance.source_id,
                "webpage_url": provenance.webpage_url,
                "url": provenance.webpage_url,
                "title": title or directory.name,
                "channel": channel,
                "quality": "image set",
                "size": _format_size(total_size),
                "path": str(directory),
            }
            history_id = db_module.save_history_entry(entry)
            row = dict(entry)
            row["id"] = history_id or 0
            result["entries"].append(row)
            result["ingested"] += 1
            history_rows.append(row)
        except Exception as error:
            result["errors"].append(f"{directory}: {error}")
    return result
