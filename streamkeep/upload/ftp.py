"""FTP / SFTP upload adapter (F68).

FTP via stdlib ftplib, SFTP via paramiko (optional dep).
Config keys: host, port, username, password, remote_dir, use_sftp.
"""

import ftplib
import os

from .base import UploadDestination


class FTPDestination(UploadDestination):
    NAME = "FTP / SFTP"

    def upload(self, file_path, metadata=None, progress_cb=None):
        cfg = self.config
        if cfg.get("use_sftp"):
            return self._upload_sftp(file_path, progress_cb)
        return self._upload_ftp(file_path, progress_cb)

    def _upload_ftp(self, file_path, progress_cb):
        cfg = self.config
        host = cfg.get("host", "")
        port = int(cfg.get("port", 21) or 21)
        user = cfg.get("username", "")
        passwd = cfg.get("password", "")
        remote_dir = cfg.get("remote_dir", "/")

        try:
            ftp = ftplib.FTP()
            ftp.connect(host, port, timeout=15)
            ftp.login(user, passwd)
            if remote_dir:
                try:
                    ftp.cwd(remote_dir)
                except ftplib.error_perm:
                    ftp.mkd(remote_dir)
                    ftp.cwd(remote_dir)

            filename = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            sent = [0]

            def _callback(block):
                sent[0] += len(block)
                if progress_cb:
                    progress_cb(sent[0], file_size)

            with open(file_path, "rb") as f:
                ftp.storbinary(f"STOR {filename}", f, blocksize=65536, callback=_callback)
            ftp.quit()
            return True, f"Uploaded to ftp://{host}{remote_dir}/{filename}"
        except Exception as e:
            return False, f"FTP upload failed: {e}"

    def _upload_sftp(self, file_path, progress_cb):
        try:
            import paramiko
        except ImportError:
            return False, "paramiko not installed for SFTP. Run: pip install paramiko"

        cfg = self.config
        host = cfg.get("host", "")
        port = int(cfg.get("port", 22) or 22)
        user = cfg.get("username", "")
        passwd = cfg.get("password", "")
        remote_dir = cfg.get("remote_dir", "/")

        try:
            transport = paramiko.Transport((host, port))
            transport.connect(username=user, password=passwd)
            sftp = paramiko.SFTPClient.from_transport(transport)

            try:
                sftp.stat(remote_dir)
            except FileNotFoundError:
                sftp.mkdir(remote_dir)

            filename = os.path.basename(file_path)
            remote_path = f"{remote_dir.rstrip('/')}/{filename}"

            def _cb(sent, total):
                if progress_cb:
                    progress_cb(sent, total)

            sftp.put(file_path, remote_path, callback=_cb)
            sftp.close()
            transport.close()
            return True, f"Uploaded to sftp://{host}{remote_path}"
        except Exception as e:
            return False, f"SFTP upload failed: {e}"

    def test_connection(self):
        cfg = self.config
        if cfg.get("use_sftp"):
            try:
                import paramiko
                transport = paramiko.Transport((cfg.get("host", ""), int(cfg.get("port", 22) or 22)))
                transport.connect(username=cfg.get("username", ""), password=cfg.get("password", ""))
                transport.close()
                return True, "SFTP connection OK"
            except ImportError:
                return False, "paramiko not installed"
            except Exception as e:
                return False, f"SFTP failed: {e}"
        try:
            ftp = ftplib.FTP()
            ftp.connect(cfg.get("host", ""), int(cfg.get("port", 21) or 21), timeout=10)
            ftp.login(cfg.get("username", ""), cfg.get("password", ""))
            ftp.quit()
            return True, "FTP connection OK"
        except Exception as e:
            return False, f"FTP failed: {e}"
