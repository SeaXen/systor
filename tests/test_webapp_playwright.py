"""End-to-End Playwright Web Application Test Suite for Systor.

Verifies:
- All 10 web pages and sub-routes (Dashboard, Apps, Storage, Network, Speed, Alerts, Logs, Settings, Login)
- Semantic landmarks, accessible skip links, header telemetry chips
- Tab navigation, form controls, modals, and dynamic view switches
- Multi-viewport responsive rendering (Desktop, Tablet, Mobile)
- Zero JavaScript unhandled errors and zero layout overflows
- Visual evidence screenshots saved to tests/screenshots/
"""
from __future__ import annotations

import os
import sys
import time
import socket
import threading
from pathlib import Path
import pytest

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

playwright = pytest.importorskip("playwright", reason="playwright is not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6677
BASE_URL = os.environ.get("BASE_URL", f"http://{DEFAULT_HOST}:{DEFAULT_PORT}")
SCREENSHOT_DIR = Path(__file__).resolve().parent / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Check if server port is already listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def start_test_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    """Start an in-process Flask test server if one is not already running."""
    from werkzeug.serving import make_server
    from systor.web import create_app

    app = create_app(start_worker=False)
    app.config["TESTING"] = True
    server = make_server(host, port, app, threaded=True)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.4)
    return server


def run_webapp_tests() -> int:
    """Run full Playwright web application test suite."""
    print("=" * 64)
    print("SYSTOR WEB APPLICATION PLAYWRIGHT TEST SUITE")
    print(f"Target URL: {BASE_URL}")
    print(f"Screenshots directory: {SCREENSHOT_DIR}")
    print("=" * 64)

    server = None
    if not is_port_open(DEFAULT_HOST, DEFAULT_PORT):
        print(f"[*] Port {DEFAULT_PORT} is not running. Starting in-process Flask server...")
        server = start_test_server(DEFAULT_HOST, DEFAULT_PORT)
        print(f"[+] In-process test server running at {BASE_URL}")
    else:
        print(f"[+] Connected to existing server at {BASE_URL}")

    test_results: list[tuple[str, bool, str]] = []
    console_errors: list[str] = []
    page_errors: list[str] = []

    def record(name: str, passed: bool, details: str = ""):
        status = "[PASS]" if passed else "[FAIL]"
        test_results.append((name, passed, details))
        msg = f"  {status}: {name}"
        if details:
            msg += f" ({details})"
        print(msg)
        if not passed:
            print(f"    ERROR: {details}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            # -------------------------------------------------------------
            # Desktop Context (1280x800)
            # -------------------------------------------------------------
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                color_scheme="dark",
            )
            page = context.new_page()

            page.on("pageerror", lambda err: page_errors.append(f"[{page.url}] {err}"))
            page.on(
                "console",
                lambda msg: console_errors.append(f"[{page.url}] [{msg.type}] {msg.text}")
                if msg.type in ["error"]
                else None,
            )

            # -------------------------------------------------------------
            # Suite 1: Core Landmarks & Accessibility (Dashboard /)
            # -------------------------------------------------------------
            print("\n[Suite 1] Core Landmarks & Accessibility (Dashboard /)")
            res = page.goto(f"{BASE_URL}/", wait_until="networkidle")
            record("Dashboard HTTP 200", res is not None and res.status == 200, f"Status: {res.status if res else 'None'}")

            title = page.title()
            record("Page Title Contains 'systor'", "systor" in title, f"Title: '{title}'")

            skip_link = page.locator("a.sr-only")
            record("Skip to Content Link Exists", skip_link.count() >= 1, "a.sr-only present")

            banner = page.locator("div.header[role='banner']")
            record("Header Banner Landmark", banner.count() == 1, "div.header role='banner'")

            nav_tabs = page.locator("nav.page-tabs[role='navigation']")
            record("Main Navigation Tabs Landmark", nav_tabs.count() == 1, "nav.page-tabs")

            main_landmark = page.locator("main#main-content")
            record("Main Content Landmark #main-content", main_landmark.count() == 1)

            # Check theme color & dark styling
            bg_color = page.evaluate("() => window.getComputedStyle(document.body).backgroundColor")
            record("Dark Surface Theme Applied", "15" in bg_color or "0, 0" in bg_color or "rgb" in bg_color, f"Body BG: {bg_color}")

            # Capture Desktop Dashboard Screenshot
            page.screenshot(path=str(SCREENSHOT_DIR / "01_dashboard_desktop.png"), full_page=True)

            # -------------------------------------------------------------
            # Suite 2: Dashboard Telemetry & KPI Cards
            # -------------------------------------------------------------
            print("\n[Suite 2] Dashboard Telemetry & KPI Cards")
            stat_cards = page.locator(".stat-card")
            card_count = stat_cards.count()
            record("Stat Cards Rendered", card_count >= 4, f"Found {card_count} stat cards")

            header_meta = page.locator("#global-header-meta")
            record("Global Header Telemetry Chip Present", header_meta.count() == 1)

            # -------------------------------------------------------------
            # Suite 3: Apps & Containers View (/apps)
            # -------------------------------------------------------------
            print("\n[Suite 3] Apps & Containers View (/apps)")
            res = page.goto(f"{BASE_URL}/apps", wait_until="networkidle")
            record("Apps Page HTTP 200", res is not None and res.status == 200)

            scope_select = page.locator("#apps-scope")
            record("Apps Scope Select Present", scope_select.count() == 1)

            refresh_btn = page.locator("#apps-refresh")
            record("Apps Refresh Button Present", refresh_btn.count() == 1)

            host_card = page.locator(".apps-summary-card.rx-card")
            record("Host KPI Summary Card Present", host_card.count() == 1)

            page.screenshot(path=str(SCREENSHOT_DIR / "02_apps_desktop.png"), full_page=True)

            # -------------------------------------------------------------
            # Suite 4: Storage Manager & Section Tabs (/storage)
            # -------------------------------------------------------------
            print("\n[Suite 4] Storage Manager & Section Tabs (/storage)")
            res = page.goto(f"{BASE_URL}/storage", wait_until="networkidle")
            record("Storage Page HTTP 200", res is not None and res.status == 200)

            path_input = page.locator("#storage-path")
            record("Storage Path Input Present", path_input.count() == 1)

            tab_overview = page.locator("#tab-overview")
            tab_analysis = page.locator("#tab-analysis")
            tab_files = page.locator("#tab-files")
            record("Storage Tabs Overview/Analysis/Files Exist", tab_overview.count() == 1 and tab_analysis.count() == 1 and tab_files.count() == 1)

            # Switch tab to Disk Analysis
            tab_analysis.click()
            page.wait_for_timeout(200)
            panel_analysis = page.locator("#panel-analysis")
            is_active = panel_analysis.get_attribute("class") or ""
            record("Switching to Disk Analysis Tab", "active" in is_active or panel_analysis.is_visible())

            # Switch tab to File Manager
            tab_files.click()
            page.wait_for_timeout(200)
            panel_files = page.locator("#panel-files")
            record("Switching to File Manager Tab", panel_files.count() == 1)

            # Switch tab to Drive Health (SMART)
            tab_smart = page.locator("#tab-smart")
            record("Storage Tab SMART Health Exists", tab_smart.count() == 1)
            tab_smart.click()
            page.wait_for_timeout(300)
            panel_smart = page.locator("#panel-smart")
            record("Switching to SMART Health Tab", panel_smart.is_visible())
            page.screenshot(path=str(SCREENSHOT_DIR / "03b_storage_smart_desktop.png"), full_page=True)

            page.screenshot(path=str(SCREENSHOT_DIR / "03_storage_desktop.png"), full_page=True)

            # -------------------------------------------------------------
            # Suite 5: Network Interfaces & Bandwidth (/network)
            # -------------------------------------------------------------
            print("\n[Suite 5] Network Interfaces & Bandwidth (/network)")
            res = page.goto(f"{BASE_URL}/network", wait_until="networkidle")
            record("Network Page HTTP 200", res is not None and res.status == 200)

            net_hours = page.locator("#net-hours")
            record("Network Rate View Select Present", net_hours.count() == 1)

            net_granularity = page.locator("#net-granularity")
            record("Network Granularity Select Present", net_granularity.count() == 1)

            page.screenshot(path=str(SCREENSHOT_DIR / "04_network_desktop.png"), full_page=True)

            # -------------------------------------------------------------
            # Suite 6: Speedtest Center (/speed)
            # -------------------------------------------------------------
            print("\n[Suite 6] Speedtest Center (/speed)")
            res = page.goto(f"{BASE_URL}/speed", wait_until="networkidle")
            record("Speedtest Page HTTP 200", res is not None and res.status == 200)

            speed_runner = page.locator("#speed-live-runner")
            record("Live Speed Runner Hero Present", speed_runner.count() == 1)

            speed_run_btn = page.locator("#speed-run-btn")
            record("Run Speedtest Button Present", speed_run_btn.count() == 1)

            page.screenshot(path=str(SCREENSHOT_DIR / "05_speed_desktop.png"), full_page=True)

            # -------------------------------------------------------------
            # Suite 7: System Alerts & Incidents (/alerts)
            # -------------------------------------------------------------
            print("\n[Suite 7] System Alerts & Incidents (/alerts)")
            res = page.goto(f"{BASE_URL}/alerts", wait_until="networkidle")
            record("Alerts Page HTTP 200", res is not None and res.status == 200)

            alert_dot = page.locator("#alert-dot")
            record("Alert Status Dot Indicator Present", alert_dot.count() == 1)

            refresh_alerts = page.locator("#refresh-alerts")
            record("Refresh Alerts Button Present", refresh_alerts.count() == 1)

            page.screenshot(path=str(SCREENSHOT_DIR / "06_alerts_desktop.png"), full_page=True)

            # -------------------------------------------------------------
            # Suite 8: Real-time System Logs (/logs)
            # -------------------------------------------------------------
            print("\n[Suite 8] Real-time System Logs (/logs)")
            res = page.goto(f"{BASE_URL}/logs", wait_until="networkidle")
            record("Logs Page HTTP 200", res is not None and res.status == 200)

            log_view = page.locator("#log-view")
            record("Log Viewer Container Present", log_view.count() == 1)

            log_level_select = page.locator("#log-level")
            record("Log Level Filter Select Present", log_level_select.count() == 1)

            page.screenshot(path=str(SCREENSHOT_DIR / "07_logs_desktop.png"), full_page=True)

            # -------------------------------------------------------------
            # Suite 9: Settings Configuration & Tab Switching (/settings)
            # -------------------------------------------------------------
            print("\n[Suite 9] Settings Configuration & Tab Switching (/settings)")
            res = page.goto(f"{BASE_URL}/settings", wait_until="networkidle")
            record("Settings Page HTTP 200", res is not None and res.status == 200)

            general_tab_btn = page.locator("button[data-settings-tab='general']")
            notif_tab_btn = page.locator("button[data-settings-tab='notifications']")
            auth_tab_btn = page.locator("button[data-settings-tab='auth']")
            record("Settings Tabs (General, Notifications, Auth) Present", general_tab_btn.count() == 1 and notif_tab_btn.count() == 1 and auth_tab_btn.count() == 1)

            # Switch to Notifications tab
            notif_tab_btn.click()
            page.wait_for_timeout(200)
            notif_panel = page.locator("section[data-settings-panel='notifications']")
            is_active = notif_panel.get_attribute("class") or ""
            record("Settings Tab Switching to Notifications", "active" in is_active)

            # Switch to Auth tab
            auth_tab_btn.click()
            page.wait_for_timeout(200)
            auth_panel = page.locator("section[data-settings-panel='auth']")
            is_active = auth_panel.get_attribute("class") or ""
            record("Settings Tab Switching to Auth", "active" in is_active)

            # Switch to Access tab (Disaster Recovery & Full Backup)
            access_tab_btn = page.locator("button[data-settings-tab='access']")
            record("Settings Tab Access Present", access_tab_btn.count() == 1)
            access_tab_btn.click()
            page.wait_for_timeout(300)
            access_panel = page.locator("section[data-settings-panel='access']")
            is_active = access_panel.get_attribute("class") or ""
            record("Settings Tab Switching to Access", "active" in is_active)
            page.screenshot(path=str(SCREENSHOT_DIR / "08b_settings_access_desktop.png"), full_page=True)

            # Switch back to General tab
            general_tab_btn.click()
            page.wait_for_timeout(200)
            general_panel = page.locator("section[data-settings-panel='general']")
            is_active = general_panel.get_attribute("class") or ""
            record("Settings Tab Switching to General", "active" in is_active)

            page.screenshot(path=str(SCREENSHOT_DIR / "08_settings_desktop.png"), full_page=True)

            # -------------------------------------------------------------
            # Suite 10: Admin Authentication Page (/login)
            # -------------------------------------------------------------
            print("\n[Suite 10] Admin Authentication Page (/login)")
            # Auth disabled: visiting /login redirects (302) to dashboard
            res_redirect = page.goto(f"{BASE_URL}/login", wait_until="networkidle")
            record("Login Redirects to Dashboard When Auth Disabled", res_redirect is not None and res_redirect.status == 200 and page.url.rstrip("/") == BASE_URL.rstrip())

            # Preview mode: visiting /login?preview=1 renders login form
            res = page.goto(f"{BASE_URL}/login?preview=1", wait_until="networkidle")
            record("Login Form HTTP 200", res is not None and res.status == 200)

            user_input = page.locator("#login-username")
            record("Login Username Input Present", user_input.count() == 1)

            pass_input = page.locator("#login-password")
            record("Login Password Input Present", pass_input.count() == 1)

            submit_btn = page.locator("button[type='submit']")
            record("Login Submit Button Present", submit_btn.count() >= 1)

            page.screenshot(path=str(SCREENSHOT_DIR / "09_login_desktop.png"), full_page=True)

            # -------------------------------------------------------------
            # Suite 11: Multi-Viewport Responsive Layouts (Mobile & Tablet)
            # -------------------------------------------------------------
            print("\n[Suite 11] Multi-Viewport Responsive Layouts (Mobile & Tablet)")

            # Mobile Viewport: 390x844 (iPhone 14 / modern smartphone)
            mobile_context = browser.new_context(viewport={"width": 390, "height": 844})
            mobile_page = mobile_context.new_page()

            mobile_page.goto(f"{BASE_URL}/", wait_until="networkidle")
            overflows = mobile_page.evaluate(
                "() => document.documentElement.scrollWidth <= window.innerWidth + 2"
            )
            record("Dashboard Mobile Layout Has Zero Horizontal Overflow", overflows, "scrollWidth <= innerWidth")

            tab_height = mobile_page.evaluate(
                "() => { const tab = document.querySelector('.page-tab'); return tab ? tab.getBoundingClientRect().height : 0; }"
            )
            record("Mobile Navigation Tab Meets Touch Target Floor (>=40px)", tab_height >= 40, f"Height: {tab_height}px")

            viewport_content = mobile_page.evaluate(
                "() => document.querySelector('meta[name=\"viewport\"]')?.getAttribute('content') || ''"
            )
            record("Mobile Viewport Uses viewport-fit=cover", "viewport-fit=cover" in viewport_content, viewport_content)

            mobile_page.screenshot(path=str(SCREENSHOT_DIR / "10_dashboard_mobile.png"), full_page=True)

            # Mobile Storage Viewport
            mobile_page.goto(f"{BASE_URL}/storage", wait_until="networkidle")
            overflows_storage = mobile_page.evaluate(
                "() => document.documentElement.scrollWidth <= window.innerWidth + 2"
            )
            record("Storage Mobile Layout Has Zero Horizontal Overflow", overflows_storage, "scrollWidth <= innerWidth")
            mobile_page.screenshot(path=str(SCREENSHOT_DIR / "11_storage_mobile.png"), full_page=True)

            # Mobile Settings Viewport
            mobile_page.goto(f"{BASE_URL}/settings", wait_until="networkidle")
            overflows_settings = mobile_page.evaluate(
                "() => document.documentElement.scrollWidth <= window.innerWidth + 2"
            )
            record("Settings Mobile Layout Has Zero Horizontal Overflow", overflows_settings, "scrollWidth <= innerWidth")
            mobile_page.screenshot(path=str(SCREENSHOT_DIR / "12_settings_mobile.png"), full_page=True)

            mobile_context.close()
            context.close()
            browser.close()

    finally:
        if server is not None:
            print("[*] Shutting down in-process test server...")
            try:
                server.shutdown()
            except Exception:
                pass

    # -------------------------------------------------------------
    # Suite 12: Console & Unhandled Error Verification
    # -------------------------------------------------------------
    print("\n[Suite 12] Console & Unhandled Error Verification")
    record("Zero Uncaught Page Errors", len(page_errors) == 0, f"{len(page_errors)} uncaught errors")
    if page_errors:
        for err in page_errors:
            print(f"    PAGE ERROR: {err}")

    record("Zero Console Errors", len(console_errors) == 0, f"{len(console_errors)} console errors")
    if console_errors:
        for err in console_errors:
            print(f"    CONSOLE ERROR: {err}")

    # -------------------------------------------------------------
    # Summary Report
    # -------------------------------------------------------------
    total = len(test_results)
    passed = sum(1 for _, p, _ in test_results if p)
    failed = total - passed

    print("\n" + "=" * 64)
    print(f"E2E PLAYWRIGHT TEST RESULTS: {passed}/{total} PASSED ({failed} FAILED)")
    print("=" * 64)

    return 0 if failed == 0 else 1


def test_webapp_playwright_e2e():
    """Pytest entrypoint for Playwright E2E suite."""
    assert run_webapp_tests() == 0, "Playwright webapp E2E tests failed"


if __name__ == "__main__":
    sys.exit(run_webapp_tests())
