"""FTP-family upload adapters with secure defaults and resumable commits.

SFTP uses Paramiko's system/user known-hosts files and rejects unknown host
keys.  FTPS uses the platform CA store (or an explicitly configured CA file).
Plain FTP is retained only for deliberate legacy use and is disabled unless
``allow_insecure_ftp`` is true.
"""

from __future__ import annotations

import ftplib
import os
import ssl

from .base import UploadDestination

_CHUNK_SIZE = 1024 * 1024


def _paramiko_available():
    """Return whether Paramiko can be imported without importing it eagerly."""
    import importlib.util

    try:
        return importlib.util.find_spec("paramiko") is not None
    except (ImportError, ValueError):
        return False


class FTPDestination(UploadDestination):
    NAME = "FTP / SFTP"

    def _transport(self):
        if (self.config or {}).get("use_sftp"):
            return "sftp"
        return str((self.config or {}).get("transport", "ftp") or "ftp").lower()

    def upload(self, file_path, metadata=None, progress_cb=None):
        transport = self._transport()
        if transport == "sftp":
            return self._upload_sftp(file_path, progress_cb)
        if transport in {"ftps", "ftp_tls", "tls"}:
            return self._upload_ftp(file_path, progress_cb, secure=True)
        if transport not in {"ftp", "plain"}:
            return False, f"Unsupported FTP transport: {transport}"
        if not bool((self.config or {}).get("allow_insecure_ftp", False)):
            return False, (
                "Plain FTP is disabled by default; use SFTP or FTPS, or "
                "explicitly enable allow_insecure_ftp."
            )
        return self._upload_ftp(file_path, progress_cb, secure=False)

    def _upload_ftp(self, file_path, progress_cb, *, secure):
        label = "FTPS" if secure else "FTP"
        settings, err = self._resolve_settings(
            default_port=21,
            label=label,
            file_path=file_path,
        )
        if err:
            return False, err

        ftp = None
        try:
            if secure:
                ftp = self._new_ftps_client()
            else:
                ftp = ftplib.FTP()
            ftp.connect(settings["host"], settings["port"], timeout=15)
            if secure and hasattr(ftp, "auth"):
                ftp.auth()
            ftp.login(settings["username"], settings["password"])
            if secure and hasattr(ftp, "prot_p"):
                ftp.prot_p()
            self._ensure_ftp_dir(ftp, settings["remote_dir"])

            filename, name_err = self._safe_remote_filename(file_path)
            if name_err:
                return False, f"{label} upload failed: {name_err}"
            remote_path = self._remote_path(settings["remote_dir"], filename)
            if secure:
                return self._upload_ftp_resumable(
                    ftp, file_path, remote_path, progress_cb, label,
                )

            file_size = os.path.getsize(file_path)
            sent = [0]

            def _callback(block):
                sent[0] += len(block)
                if progress_cb:
                    progress_cb(sent[0], file_size)

            with open(file_path, "rb") as handle:
                ftp.storbinary(
                    f"STOR {filename}", handle,
                    blocksize=65536, callback=_callback,
                )
            return True, f"Uploaded to ftp://{settings['host']}{self._uri_path(remote_path)}"
        except Exception as error:
            return False, self.safe_message(f"{label} upload failed: {error}")
        finally:
            self._close_ftp(ftp)

    def _new_ftps_client(self):
        context = ssl.create_default_context()
        ca_file = str((self.config or {}).get("ftps_ca_file", "") or "").strip()
        if ca_file:
            context.load_verify_locations(cafile=ca_file)
        return ftplib.FTP_TLS(context=context)

    def _upload_ftp_resumable(self, ftp, file_path, remote_path, progress_cb, label):
        file_size = os.path.getsize(file_path)
        partial_path = f"{remote_path}.part"
        offset = self._remote_size(ftp, partial_path)
        if offset > file_size:
            try:
                ftp.delete(partial_path)
            except Exception:
                pass
            offset = 0
        sent = offset
        if progress_cb:
            progress_cb(sent, file_size)
        if offset < file_size:
            def _callback(block):
                nonlocal sent
                sent += len(block)
                if progress_cb:
                    progress_cb(sent, file_size)

            with open(file_path, "rb") as handle:
                handle.seek(offset)
                ftp.storbinary(
                    f"STOR {partial_path}", handle,
                    blocksize=65536, callback=_callback, rest=offset,
                )
        ftp.rename(partial_path, remote_path)
        return True, f"Uploaded to {label.lower()}://{self._resolve_host()}{self._uri_path(remote_path)}"

    @staticmethod
    def _remote_size(ftp, remote_path):
        try:
            value = ftp.size(remote_path)
            return max(0, int(value or 0))
        except Exception:
            return 0

    def _upload_sftp(self, file_path, progress_cb):
        settings, err = self._resolve_settings(
            default_port=22, label="SFTP", file_path=file_path,
        )
        if err:
            return False, err
        if not _paramiko_available():
            return False, "paramiko not installed for SFTP. Run: pip install paramiko"

        client = None
        sftp = None
        try:
            client = self._connect_sftp_client(settings)
            sftp = client.open_sftp()
            self._ensure_sftp_dir(sftp, settings["remote_dir"])

            filename, name_err = self._safe_remote_filename(file_path)
            if name_err:
                return False, f"SFTP upload failed: {name_err}"
            remote_path = self._remote_path(settings["remote_dir"], filename)
            partial_path = f"{remote_path}.part"
            file_size = os.path.getsize(file_path)
            offset = self._sftp_size(sftp, partial_path)
            if offset > file_size:
                try:
                    sftp.remove(partial_path)
                except Exception:
                    pass
                offset = 0
            sent = offset
            if progress_cb:
                progress_cb(sent, file_size)
            if offset < file_size:
                with open(file_path, "rb") as handle:
                    handle.seek(offset)
                    remote = sftp.open(partial_path, "ab")
                    try:
                        while True:
                            block = handle.read(_CHUNK_SIZE)
                            if not block:
                                break
                            remote.write(block)
                            sent += len(block)
                            if progress_cb:
                                progress_cb(sent, file_size)
                    finally:
                        remote.close()
            sftp.rename(partial_path, remote_path)
            return True, f"Uploaded to sftp://{settings['host']}{self._uri_path(remote_path)}"
        except Exception as error:
            return False, self.safe_message(f"SFTP upload failed: {error}")
        finally:
            if sftp is not None:
                try:
                    sftp.close()
                except Exception:
                    pass
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    @staticmethod
    def _sftp_size(sftp, remote_path):
        try:
            return max(0, int(sftp.stat(remote_path).st_size or 0))
        except Exception:
            return 0

    def test_connection(self):
        transport = self._transport()
        if transport == "sftp":
            settings, err = self._resolve_settings(default_port=22, label="SFTP")
            if err:
                return False, err
            if not _paramiko_available():
                return False, "paramiko not installed"
            client = None
            try:
                client = self._connect_sftp_client(settings)
                return True, "SFTP connection OK (host key verified)"
            except Exception as error:
                return False, self.safe_message(f"SFTP failed: {error}")
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
        if transport in {"ftps", "ftp_tls", "tls"}:
            return self._test_ftp_connection(secure=True)
        if transport not in {"ftp", "plain"}:
            return False, f"Unsupported FTP transport: {transport}"
        settings, err = self._resolve_settings(default_port=21, label="FTP")
        if err:
            return False, err
        if not bool((self.config or {}).get("allow_insecure_ftp", False)):
            return False, (
                "Plain FTP is disabled by default; use SFTP or FTPS, or "
                "explicitly enable allow_insecure_ftp."
            )
        return self._test_ftp_connection(secure=False, settings=settings)

    def _test_ftp_connection(self, *, secure, settings=None):
        label = "FTPS" if secure else "FTP"
        if settings is None:
            settings, err = self._resolve_settings(
                default_port=21, label=label,
            )
            if err:
                return False, err
        ftp = None
        try:
            ftp = self._new_ftps_client() if secure else ftplib.FTP()
            ftp.connect(settings["host"], settings["port"], timeout=10)
            if secure and hasattr(ftp, "auth"):
                ftp.auth()
            ftp.login(settings["username"], settings["password"])
            if secure and hasattr(ftp, "prot_p"):
                ftp.prot_p()
            return True, f"{label} connection OK" + (" (certificate verified)" if secure else "")
        except Exception as error:
            return False, self.safe_message(f"{label} failed: {error}")
        finally:
            self._close_ftp(ftp)

    def _connect_sftp_client(self, settings):
        """Open an SSH client that rejects every unknown host key."""
        import paramiko

        client = paramiko.SSHClient()
        client.load_system_host_keys()
        known_hosts = str((self.config or {}).get("sftp_known_hosts", "") or "").strip()
        if known_hosts:
            if not os.path.isfile(known_hosts):
                raise FileNotFoundError("Configured SFTP known-hosts file was not found")
            client.load_host_keys(known_hosts)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            settings["host"],
            port=settings["port"],
            username=settings["username"],
            password=settings["password"],
            timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )
        return client

    def _resolve_settings(self, default_port, label, file_path=None):
        cfg = self.config or {}
        host = str(cfg.get("host", "") or "").strip()
        if not host:
            return None, f"{label} host not configured"
        try:
            port = int(cfg.get("port", default_port) or default_port)
        except (TypeError, ValueError):
            return None, f"{label} port is invalid"
        if port < 1 or port > 65535:
            return None, f"{label} port is invalid"
        if file_path and not os.path.isfile(file_path):
            return None, "File not found"
        return {
            "host": host,
            "port": port,
            "username": str(cfg.get("username", "") or ""),
            "password": str(cfg.get("password", "") or ""),
            "remote_dir": self._normalize_remote_dir(cfg.get("remote_dir", "/")),
        }, None

    def _resolve_host(self):
        return str((self.config or {}).get("host", "") or "").strip()

    @staticmethod
    def _close_ftp(ftp):
        if ftp is not None:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass

    @staticmethod
    def _normalize_remote_dir(remote_dir):
        remote_dir = str(remote_dir or "/").strip().replace("\\", "/")
        if not remote_dir:
            return "/"
        is_absolute = remote_dir.startswith("/")
        parts = [part for part in remote_dir.split("/") if part and part != ".."]
        if not parts:
            return "/"
        normalized = "/".join(parts)
        return f"/{normalized}" if is_absolute else normalized

    @staticmethod
    def _safe_remote_filename(file_path):
        """Return ``(filename, error)`` for a STOR/put-safe leaf name."""
        name = os.path.basename(str(file_path or ""))
        if any(ord(ch) < 0x20 or ch == "\x7f" for ch in name):
            return "", "filename contains control characters"
        name = name.replace("\\", "").replace("/", "").strip()
        if not name or name in (".", ".."):
            return "", "filename is invalid"
        return name, ""

    @staticmethod
    def _remote_path(remote_dir, filename):
        remote_dir = FTPDestination._normalize_remote_dir(remote_dir)
        filename = os.path.basename(str(filename or ""))
        if remote_dir == "/":
            return f"/{filename}"
        return f"{remote_dir.rstrip('/')}/{filename}"

    @staticmethod
    def _uri_path(remote_path):
        return "/" + str(remote_path or "").lstrip("/")

    @staticmethod
    def _ensure_ftp_dir(ftp, remote_dir):
        remote_dir = FTPDestination._normalize_remote_dir(remote_dir)
        if not remote_dir or remote_dir == "/":
            return
        parts = [part for part in remote_dir.split("/") if part]
        if remote_dir.startswith("/"):
            ftp.cwd("/")
        for part in parts:
            try:
                ftp.cwd(part)
            except ftplib.error_perm:
                ftp.mkd(part)
                ftp.cwd(part)

    @staticmethod
    def _ensure_sftp_dir(sftp, remote_dir):
        remote_dir = FTPDestination._normalize_remote_dir(remote_dir)
        if not remote_dir or remote_dir == "/":
            return
        parts = [part for part in remote_dir.split("/") if part]
        current = "/" if remote_dir.startswith("/") else ""
        for part in parts:
            current = f"{current.rstrip('/')}/{part}" if current else part
            try:
                sftp.stat(current)
            except Exception:
                sftp.mkdir(current)
