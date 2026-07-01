import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamkeep import config
from streamkeep.config import install_gui_logging


class ConfigTests(unittest.TestCase):
    def test_load_config_falls_back_to_backup_when_primary_is_corrupt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            config_file = config_dir / "config.json"
            backup_file = config_dir / "config.json.bak"
            config_file.write_text("{not valid json", encoding="utf-8")
            backup_file.write_text('{"theme": "dark"}', encoding="utf-8")

            with mock.patch.object(config, "CONFIG_DIR", config_dir), mock.patch.object(
                config, "CONFIG_FILE", config_file
            ):
                data = config.load_config()

            self.assertEqual(data, {"theme": "dark"})


class GuiLogBridgeTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.handler = install_gui_logging(self.messages.append)
        self.logger = logging.getLogger("streamkeep.test_bridge")

    def tearDown(self):
        root = logging.getLogger("streamkeep")
        root.removeHandler(self.handler)

    def test_warning_propagates_with_level_and_module(self):
        self.logger.warning("disk almost full")
        matching = [m for m in self.messages if "disk almost full" in m]
        self.assertEqual(len(matching), 1)
        self.assertIn("WARN", matching[0])
        self.assertIn("test_bridge", matching[0])

    def test_error_propagates(self):
        self.logger.error("connection lost")
        matching = [m for m in self.messages if "connection lost" in m]
        self.assertEqual(len(matching), 1)
        self.assertIn("ERROR", matching[0])

    def test_duplicate_suppression(self):
        for _ in range(10):
            record = self.logger.makeRecord(
                "streamkeep.test_bridge", logging.WARNING, "", 0,
                "same message", (), None,
            )
            record.created = 1000.0
            self.handler.emit(record)
        forwarded = [m for m in self.messages if "same message" in m and "repeated" not in m]
        self.assertEqual(len(forwarded), 1)


if __name__ == "__main__":
    unittest.main()
