"""Tests for CLI serve commands, versioning, and container packaging assets."""
import os
import argparse
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

import systor
from systor import cli


class TestCliAndPackaging(unittest.TestCase):
    def test_version_bump_to_1_0_0(self):
        """Verify that systor version is 1.0.0 across app and init."""
        self.assertEqual(systor.__version__, "1.0.0")

    def test_cli_serve_all_spawns_threads(self):
        """Verify that _serve_all launches collector in a thread and invokes web.run."""
        with patch("threading.Thread") as mock_thread, \
             patch("systor.web.run") as mock_web_run:
            mock_inst = MagicMock()
            mock_thread.return_value = mock_inst

            cli._serve_all()

            mock_thread.assert_called_once()
            mock_inst.start.assert_called_once()
            mock_web_run.assert_called_once()

    def test_cmd_serve_routing(self):
        """Test cmd_serve routing to collector, web, and all."""
        with patch("systor.collector.run") as mock_col_run:
            args = argparse.Namespace(component="collector")
            cli.cmd_serve(args)
            mock_col_run.assert_called_once()

        with patch("systor.web.run") as mock_web_run:
            args = argparse.Namespace(component="web")
            cli.cmd_serve(args)
            mock_web_run.assert_called_once()

        with patch("systor.cli._serve_all") as mock_all:
            args = argparse.Namespace(component="all")
            cli.cmd_serve(args)
            mock_all.assert_called_once()

    def test_cmd_serve_default_is_all(self):
        """Test cmd_serve defaults to 'all' if no component or None is passed."""
        with patch("systor.cli._serve_all") as mock_all:
            args = argparse.Namespace(component=None)
            cli.cmd_serve(args)
            mock_all.assert_called_once()

    def test_dockerfile_and_compose_exist(self):
        """Ensure Dockerfile, docker-compose.yml, and .dockerignore exist and are valid."""
        repo_root = Path(__file__).resolve().parent.parent
        dockerfile = repo_root / "Dockerfile"
        compose = repo_root / "docker-compose.yml"
        dockerignore = repo_root / ".dockerignore"

        self.assertTrue(dockerfile.exists(), "Dockerfile must exist")
        self.assertTrue(compose.exists(), "docker-compose.yml must exist")
        self.assertTrue(dockerignore.exists(), ".dockerignore must exist")

        df_content = dockerfile.read_text(encoding="utf-8")
        self.assertIn("python:3.11-slim", df_content)
        self.assertIn("smartmontools", df_content)
        self.assertIn("tini", df_content)
        self.assertIn("HEALTHCHECK", df_content)

        compose_content = compose.read_text(encoding="utf-8")
        self.assertIn("systor_config", compose_content)
        self.assertIn("systor_data", compose_content)
        self.assertIn("pid: host", compose_content)

    def test_install_script_contains_bootstrap(self):
        """Ensure install.sh contains remote bootstrap logic and smartmontools install."""
        repo_root = Path(__file__).resolve().parent.parent
        install_sh = repo_root / "install.sh"
        self.assertTrue(install_sh.exists(), "install.sh must exist")

        content = install_sh.read_text(encoding="utf-8")
        self.assertIn("SeaXen/systor", content)
        self.assertIn("smartmontools", content)
        self.assertIn("systor.env", content)

    def test_theme_tokens_and_switcher_assets(self):
        """Verify 3-way theme tokens (classic-dark, warm-dark, light) and template hooks."""
        repo_root = Path(__file__).resolve().parent.parent
        css_file = repo_root / "systor" / "static" / "css" / "style.min.css"
        base_html = repo_root / "systor" / "templates" / "base.html"
        settings_html = repo_root / "systor" / "templates" / "settings.html"
        app_js = repo_root / "systor" / "static" / "js" / "app.js"

        css_text = css_file.read_text(encoding="utf-8")
        self.assertIn('data-theme="warm-dark"', css_text)
        self.assertIn('data-theme="light"', css_text)
        self.assertIn('data-theme="classic-dark"', css_text)
        self.assertIn('--teal:            #1d9e75', css_text)
        self.assertIn('theme-picker-grid', css_text)

        base_text = base_html.read_text(encoding="utf-8")
        self.assertIn('theme-toggle-btn', base_text)
        self.assertIn('setSystorTheme', base_text)
        self.assertIn('localStorage.getItem(\'systor_theme\')', base_text)

        settings_text = settings_html.read_text(encoding="utf-8")
        self.assertIn('Appearance &amp; Theme', settings_text)
        self.assertIn('data-theme-choice="warm-dark"', settings_text)
        self.assertIn('data-theme-choice="light"', settings_text)
        self.assertIn('data-theme-choice="classic-dark"', settings_text)

        app_js_text = app_js.read_text(encoding="utf-8")
        self.assertIn('systor:theme-change', app_js_text)
        self.assertIn('updateChartColors', app_js_text)

    def test_pwa_manifest_and_service_worker(self):
        """Verify PWA manifest, service worker, and web app icons."""
        repo_root = Path(__file__).resolve().parent.parent
        manifest = repo_root / "systor" / "static" / "manifest.json"
        sw = repo_root / "systor" / "static" / "sw.js"
        icon192 = repo_root / "systor" / "static" / "img" / "icon-192.png"
        icon512 = repo_root / "systor" / "static" / "img" / "icon-512.png"
        base_html = repo_root / "systor" / "templates" / "base.html"

        self.assertTrue(manifest.exists(), "manifest.json must exist")
        self.assertTrue(sw.exists(), "sw.js must exist")
        self.assertTrue(icon192.exists(), "icon-192.png must exist")
        self.assertTrue(icon512.exists(), "icon-512.png must exist")

        base_text = base_html.read_text(encoding="utf-8")
        self.assertIn('rel="manifest"', base_text)
        self.assertIn('serviceWorker.register', base_text)

        sw_text = sw.read_text(encoding="utf-8")
        self.assertIn("systor-shell-v1.0.0", sw_text)
        self.assertIn("/api/", sw_text)


if __name__ == "__main__":
    unittest.main()
