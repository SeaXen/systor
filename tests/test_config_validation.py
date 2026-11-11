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

    def test_invalid_subsections(self):
        # collector invalid types and not dict
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["collector"]["retention_days"] = 0
        cfg["collector"]["rollup_after_hours"] = 0
        cfg["collector"]["rollup_retention_days"] = 0
        is_valid, errors, _ = validate_config(cfg)
        self.assertFalse(is_valid)

        cfg["collector"] = "invalid"
        is_valid, errors, _ = validate_config(cfg)
        self.assertFalse(is_valid)

        # web invalid host and not dict
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["web"]["host"] = "   "
        is_valid, errors, _ = validate_config(cfg)
        self.assertFalse(is_valid)

        cfg["web"] = None
        is_valid, errors, _ = validate_config(cfg)
        self.assertFalse(is_valid)

        # auth invalid branches
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["auth"]["enabled"] = "yes"
        cfg["auth"]["max_fails"] = 0
        cfg["auth"]["cooldown_sec"] = -1
        is_valid, errors, _ = validate_config(cfg)
        self.assertFalse(is_valid)

        cfg["auth"] = 123
        is_valid, errors, _ = validate_config(cfg)
        self.assertFalse(is_valid)

        # thresholds invalid branches
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["thresholds"]["cooldown_sec"] = -5
        cfg["thresholds"]["cpu_load_1m"] = {"value": "high", "duration_min": -1}
        is_valid, errors, _ = validate_config(cfg)
        self.assertFalse(is_valid)

        cfg["thresholds"] = "not-a-dict"
        is_valid, errors, _ = validate_config(cfg)
        self.assertFalse(is_valid)

        # speed & logging branches
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["speed"]["auto_interval_min"] = 2
        cfg["logging"]["level"] = "UNKNOWN_LEVEL"
        is_valid, errors, warnings = validate_config(cfg)
        self.assertTrue(is_valid)
        self.assertTrue(any("speed.auto_interval_min" in w for w in warnings))
        self.assertTrue(any("logging.level" in w for w in warnings))

        cfg["speed"] = []
        is_valid, errors, _ = validate_config(cfg)
        self.assertFalse(is_valid)

    def test_cli_config_check_cmd(self):
        res = subprocess.run([sys.executable, "-m", "systor", "config", "check"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("valid", res.stdout.lower())

    def test_cli_config_check_cmd_failure(self):
        from systor.cli import cmd_config_check
        from unittest.mock import patch
        with patch("systor.cli.load_config") as mock_load:
            bad_cfg = copy.deepcopy(DEFAULT_CONFIG)
            bad_cfg["web"]["port"] = -1
            mock_load.return_value = bad_cfg
            with self.assertRaises(SystemExit) as ctx:
                cmd_config_check(None)
            self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
