import ftplib
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from streamkeep.capabilities import CapabilityUnavailableError
from streamkeep.upload.ftp import FTPDestination
from streamkeep.upload.s3 import S3Destination


class _FakeFTP:
    def __init__(self):
        self.cwd_path = "/"
        self.existing = {"/"}
        self.connected = None
        self.logged_in = None
        self.closed = False

    def connect(self, host, port, timeout=None):
        self.connected = (host, port, timeout)

    def login(self, username, password):
        self.logged_in = (username, password)

    def _normalize(self, part):
        if part == "/":
            return "/"
        if part.startswith("/"):
            return part.rstrip("/") or "/"
        if self.cwd_path == "/":
            return f"/{part}".rstrip("/")
        return f"{self.cwd_path.rstrip('/')}/{part}".rstrip("/")

    def cwd(self, part):
        target = self._normalize(part)
        if target not in self.existing:
            raise ftplib.error_perm("550 missing")
        self.cwd_path = target

    def mkd(self, part):
        target = self._normalize(part)
        self.existing.add(target)

    def storbinary(self, command, file_obj, blocksize=65536, callback=None):
        while True:
            chunk = file_obj.read(blocksize)
            if not chunk:
                break
            if callback is not None:
                callback(chunk)
        self.command = command

    def quit(self):
        self.closed = True

    def close(self):
        self.closed = True


class UploadAdapterTests(unittest.TestCase):
    def test_ftp_upload_normalizes_remote_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "clip.bin"
            file_path.write_bytes(b"streamkeep")
            fake_ftp = _FakeFTP()
            progress = []
            dest = FTPDestination(
                {
                    "host": " ftp.example.com ",
                    "port": "21",
                    "username": "alice",
                    "password": "secret",
                    "allow_insecure_ftp": True,
                    "remote_dir": r"\\nested//clips\\",
                }
            )

            with mock.patch("streamkeep.upload.ftp.ftplib.FTP", return_value=fake_ftp):
                ok, msg = dest.upload(
                    str(file_path),
                    progress_cb=lambda sent, total: progress.append((sent, total)),
                )

            self.assertTrue(ok, msg)
            self.assertEqual(msg, "Uploaded to ftp://ftp.example.com/nested/clips/clip.bin")
            self.assertEqual(fake_ftp.connected, ("ftp.example.com", 21, 15))
            self.assertEqual(fake_ftp.logged_in, ("alice", "secret"))
            self.assertEqual(fake_ftp.cwd_path, "/nested/clips")
            self.assertEqual(fake_ftp.command, "STOR clip.bin")
            self.assertEqual(progress[-1], (file_path.stat().st_size, file_path.stat().st_size))
            self.assertTrue(fake_ftp.closed)

    def test_ftp_upload_rejects_control_char_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # A filename with an embedded CRLF could inject a second FTP
            # control-channel command after STOR.
            file_path = Path(tmpdir) / "clip.bin"
            file_path.write_bytes(b"streamkeep")
            fake_ftp = _FakeFTP()
            dest = FTPDestination({
                "host": "ftp.example.com", "port": "21",
                "username": "a", "password": "b",
                "allow_insecure_ftp": True, "remote_dir": "/",
            })
            with mock.patch(
                "streamkeep.upload.ftp.ftplib.FTP", return_value=fake_ftp
            ), mock.patch(
                "streamkeep.upload.ftp.os.path.basename",
                return_value="clip.bin\r\nDELE important",
            ):
                ok, msg = dest.upload(str(file_path))
            self.assertFalse(ok)
            self.assertIn("control characters", msg)
            self.assertFalse(hasattr(fake_ftp, "command"))

    def test_safe_remote_filename_rules(self):
        assert FTPDestination._safe_remote_filename("/a/b/clip.mp4") == (
            "clip.mp4", ""
        )
        name, err = FTPDestination._safe_remote_filename("bad\r\nname")
        assert name == "" and "control" in err
        name, err = FTPDestination._safe_remote_filename("..")
        assert name == "" and err

    def test_ftp_connection_reports_invalid_port_cleanly(self):
        dest = FTPDestination({"host": "ftp.example.com", "port": "not-a-port"})

        with mock.patch("streamkeep.upload.ftp.ftplib.FTP") as mock_ftp:
            ok, msg = dest.test_connection()

        self.assertFalse(ok)
        self.assertEqual(msg, "FTP port is invalid")
        mock_ftp.assert_not_called()

    def test_s3_validation_runs_before_boto3_import(self):
        dest = S3Destination(
            {
                "access_key": "key",
                "secret_key": "secret",
                "bucket": "archive",
            }
        )

        ok, msg = dest.upload("C:\\definitely-missing-file.bin")

        self.assertFalse(ok)
        self.assertEqual(msg, "File not found")

    def test_s3_connection_reports_missing_bucket_cleanly(self):
        dest = S3Destination({"access_key": "key", "secret_key": "secret"})

        ok, msg = dest.test_connection()

        self.assertFalse(ok)
        self.assertEqual(msg, "S3 bucket not configured")


class TestSFTPHostKeyVerification(unittest.TestCase):
    """Verify SFTP uses SSHClient with host-key verification (not raw Transport)."""

    @mock.patch.dict("sys.modules", {"paramiko": mock.MagicMock()})
    def test_sftp_default_uses_reject_policy(self):
        import sys
        paramiko = sys.modules["paramiko"]
        paramiko.SSHClient.return_value = mock.MagicMock()
        paramiko.RejectPolicy.return_value = "reject"
        paramiko.AutoAddPolicy.return_value = "auto"

        dest = FTPDestination({"use_sftp": True, "host": "h", "username": "u", "password": "p"})
        client = dest._connect_sftp_client({
            "host": "h", "port": 22, "username": "u", "password": "p",
        })
        client.set_missing_host_key_policy.assert_called_once_with("reject")
        client.connect.assert_called_once()

    @mock.patch.dict("sys.modules", {"paramiko": mock.MagicMock()})
    def test_sftp_tofu_flag_cannot_disable_host_key_verification(self):
        import sys
        paramiko = sys.modules["paramiko"]
        paramiko.SSHClient.return_value = mock.MagicMock()
        paramiko.RejectPolicy.return_value = "reject"

        dest = FTPDestination({
            "use_sftp": True, "host": "h", "username": "u", "password": "p",
            "sftp_trust_on_first_use": True,
        })
        client = dest._connect_sftp_client({
            "host": "h", "port": 22, "username": "u", "password": "p",
        })
        client.set_missing_host_key_policy.assert_called_once_with("reject")

    def test_sftp_upload_refuses_unsupported_paramiko_with_repair_guidance(self):
        record = {
            "name": "paramiko",
            "display_name": "Paramiko",
            "available": True,
            "supported": False,
            "version": "4.0.0",
            "minimum": "5.0.0",
            "repair": "Install Paramiko 5.0.0 or newer.",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "clip.bin"
            file_path.write_bytes(b"streamkeep")
            dest = FTPDestination({
                "use_sftp": True, "host": "sftp.example.com", "port": 22,
                "username": "alice", "password": "secret",
            })
            with mock.patch(
                "streamkeep.upload.ftp.require_capability",
                side_effect=CapabilityUnavailableError(record),
            ), mock.patch.object(dest, "_connect_sftp_client") as connect:
                ok, msg = dest.upload(str(file_path))

        self.assertFalse(ok)
        self.assertIn("Paramiko 4.0.0 is below the required minimum 5.0.0", msg)
        self.assertIn("Install Paramiko 5.0.0", msg)
        connect.assert_not_called()

    def test_paramiko_floor_and_modern_sftp_endpoint(self):
        import paramiko

        self.assertEqual(paramiko.__version__, "5.0.0")
        self.assertNotIn("ssh-rsa", paramiko.Transport._preferred_keys)
        self.assertNotIn(
            "diffie-hellman-group1-sha1", paramiko.Transport._preferred_kex
        )
        self.assertFalse(hasattr(paramiko.Transport, "auth_gssapi_with_mic"))

        class _Server(paramiko.ServerInterface):
            def check_auth_password(self, username, password):
                return (
                    paramiko.AUTH_SUCCESSFUL
                    if (username, password) == ("alice", "secret")
                    else paramiko.AUTH_FAILED
                )

            def get_allowed_auths(self, username):
                return "password"

            def check_channel_request(self, kind, chanid):
                return (
                    paramiko.OPEN_SUCCEEDED
                    if kind == "session"
                    else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
                )

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        host_key = paramiko.RSAKey.generate(2048)
        errors = []

        def serve():
            transport = None
            try:
                client_socket, _address = listener.accept()
                transport = paramiko.Transport(client_socket)
                transport.add_server_key(host_key)
                transport.set_subsystem_handler(
                    "sftp", paramiko.SFTPServer,
                    sftp_si=paramiko.SFTPServerInterface,
                )
                transport.start_server(server=_Server())
                channel = None
                while transport.is_active() and channel is None:
                    channel = transport.accept(1)
                while transport.is_active():
                    time.sleep(0.01)
            except Exception as error:  # pragma: no cover - thread handoff
                errors.append(error)
            finally:
                if transport is not None:
                    transport.close()

        server_thread = threading.Thread(target=serve, daemon=True)
        server_thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                known_hosts = Path(tmpdir) / "known_hosts"
                known_hosts.write_text(
                    f"[127.0.0.1]:{port} {host_key.get_name()} "
                    f"{host_key.get_base64()}\n",
                    encoding="utf-8",
                )
                dest = FTPDestination({"sftp_known_hosts": str(known_hosts)})
                client = dest._connect_sftp_client({
                    "host": "127.0.0.1", "port": port,
                    "username": "alice", "password": "secret",
                })
                sftp = client.open_sftp()
                sftp.close()
                client.close()
        finally:
            listener.close()
            server_thread.join(timeout=10)

        self.assertFalse(errors, errors)
        self.assertFalse(server_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
