"""Metadata sidecar export profiles for media servers.

Each profile defines which sidecars to generate alongside recordings:
NFO (Kodi/Jellyfin/Plex), metadata.json, and thumbnail.jpg.

Profiles are per-library: users can have a Jellyfin library that gets
NFO+thumb and a plain archive that gets JSON-only.
"""

import os


BUILTIN_PROFILES = {
    "jellyfin": {
        "label": "Jellyfin / Emby",
        "nfo": True,
        "metadata_json": True,
        "thumbnail": True,
    },
    "plex": {
        "label": "Plex",
        "nfo": True,
        "metadata_json": False,
        "thumbnail": True,
    },
    "archive": {
        "label": "Archive (JSON only)",
        "nfo": False,
        "metadata_json": True,
        "thumbnail": False,
    },
    "full": {
        "label": "Full (all sidecars)",
        "nfo": True,
        "metadata_json": True,
        "thumbnail": True,
    },
    "none": {
        "label": "Disabled",
        "nfo": False,
        "metadata_json": False,
        "thumbnail": False,
    },
}


def generate_sidecars(output_dir, stream_info, vod_info=None, *, profile="full",
                      overwrite=False, log_fn=None):
    """Generate sidecars according to a named profile.

    Returns a dict of ``{sidecar_name: path_or_None}``.
    """
    if not output_dir or not os.path.isdir(output_dir):
        return {}

    cfg = BUILTIN_PROFILES.get(profile)
    if not cfg:
        if log_fn:
            log_fn(f"[SIDECAR] Unknown profile: {profile}")
        return {}

    results = {}

    if cfg.get("metadata_json"):
        p = os.path.join(output_dir, "metadata.json")
        if overwrite or not os.path.isfile(p):
            from ..metadata import MetadataSaver
            MetadataSaver.save(output_dir, stream_info, vod_info)
            results["metadata_json"] = p
        else:
            results["metadata_json"] = None

    if cfg.get("nfo"):
        p = os.path.join(output_dir, "movie.nfo")
        existing_nfo = _find_nfo(output_dir)
        if overwrite or not existing_nfo:
            from ..metadata import MetadataSaver
            MetadataSaver.write_nfo(output_dir, stream_info, vod_info)
            results["nfo"] = existing_nfo or p
        else:
            results["nfo"] = None

    if cfg.get("thumbnail"):
        p = os.path.join(output_dir, "thumbnail.jpg")
        if overwrite or not os.path.isfile(p):
            thumb_url = getattr(stream_info, "thumbnail_url", "") if stream_info else ""
            if thumb_url:
                from ..metadata import MetadataSaver
                MetadataSaver.save(output_dir, stream_info, vod_info)
                results["thumbnail"] = p if os.path.isfile(p) else None
            else:
                results["thumbnail"] = None
        else:
            results["thumbnail"] = None

    return results


def _find_nfo(output_dir):
    """Return the path to an existing .nfo file in the directory, or ''."""
    try:
        for f in os.listdir(output_dir):
            if f.lower().endswith(".nfo"):
                return os.path.join(output_dir, f)
    except OSError:
        pass
    return ""


def refresh_sidecars(output_dir, stream_info, vod_info=None, *, profile="full",
                     log_fn=None):
    """Regenerate sidecars, overwriting existing ones."""
    return generate_sidecars(
        output_dir, stream_info, vod_info,
        profile=profile, overwrite=True, log_fn=log_fn,
    )
