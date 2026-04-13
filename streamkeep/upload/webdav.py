"""WebDAV upload adapter — Nextcloud, OwnCloud, etc. (F68).

Uses stdlib urllib for HTTP PUT with Basic auth. No external deps.
Config keys: url, username, password, remote_dir.
"""

import os
import urllib.request

from .base import UploadDestination


class WebDAVDestination(UploadDestination):
    NAME = "WebDAV"

    def upload(self, file_path, metadata=None, progress_cb=None):
        cfg = self.config
        base_url = cfg.get("url", "").rstrip("/")
        user = cfg.get("username", "")
        passwd = cfg.get("password", "")
        remote_dir = cfg.get("remote_dir", "").strip("/")

        if not base_url:
            return False, "WebDAV URL not configured"

        filename = os.path.basename(file_path)
        target = f"{base_url}/{remote_dir}/{filename}" if remote_dir else f"{base_url}/{filename}"

        try:
            with open(file_path, "rb") as f:
                data = f.read()

            req = urllib.request.Request(target, data=data, method="PUT")
            if user:
                import base64
                cred = base64.b64encode(f"{user}:{passwd}".encode()).decode()
                req.add_header("Authorization", f"Basic {cred}")
            req.add_header("Content-Type", "application/octet-stream")

            with urllib.request.urlopen(req, timeout=300) as resp:
                if resp.status in (200, 201, 204):
                    if progress_cb:
                        progress_cb(len(data), len(data))
                    return True, f"Uploaded to {target}"
                return False, f"WebDAV returned status {resp.status}"
        except Exception as e:
            return False, f"WebDAV upload failed: {e}"

    def test_connection(self):
        cfg = self.config
        base_url = cfg.get("url", "").rstrip("/")
        user = cfg.get("username", "")
        passwd = cfg.get("password", "")

        try:
            req = urllib.request.Request(base_url, method="PROPFIND")
            if user:
                import base64
                cred = base64.b64encode(f"{user}:{passwd}".encode()).decode()
                req.add_header("Authorization", f"Basic {cred}")
            req.add_header("Depth", "0")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status in (200, 207):
                    return True, "WebDAV connection OK"
                return False, f"Status {resp.status}"
        except Exception as e:
            return False, f"WebDAV failed: {e}"
