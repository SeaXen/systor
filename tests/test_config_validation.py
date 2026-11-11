import unittest
from systor.config import validate_config, DEFAULT_CONFIG
import copy
import subprocess
import sys


class TestConfigValidation(unittest.TestCase):
    def test_default_config_valid(self):
        is_valid, errors, warnings = validate_config(DEFAULT_CONFIG)
        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)

    def test_invalid_port(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["web"]["port"] = 999999
        is_valid, errors, warnings = validate_config(cfg)
        self.assertFalse(is_valid)
        self.assertTrue(any("web.port" in e for e in errors))

    def test_invalid_collector_poll(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["collector"]["poll_interval_sec"] = 0
        is_valid, errors, warnings = validate_config(cfg)
        self.assertFalse(is_valid)
        self.assertTrue(any("poll_interval_sec" in e for e in errors))

    def test_unknown_section_warning(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["my_custom_plugin"] = {"foo": "bar"}
        is_valid, errors, warnings = validate_config(cfg)
        self.assertTrue(is_valid)
        self.assertTrue(any("my_custom_plugin" in w for w in warnings))

    def test_cli_config_check_cmd(self):
        res = subprocess.run([sys.executable, "-m", "systor", "config", "check"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("valid", res.stdout.lower())


if __name__ == "__main__":
    unittest.main()
