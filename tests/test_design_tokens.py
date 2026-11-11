"""Regression test suite for design tokens, accessibility constraints,
CSP directives, and zero-hex template rules.
"""
import glob
import os
import re
from pathlib import Path


def test_zero_hex_colors_in_templates():
    """D03: Templates must use CSS variables or classes, not hardcoded #hex colors."""
    templates = glob.glob("systor/templates/*.html")
    assert templates, "No template files found to test"
    hex_pattern = re.compile(r"#[0-9a-fA-F]{6}")

    violations = []
    for path in templates:
        content = Path(path).read_text(encoding="utf-8")
        matches = hex_pattern.findall(content)
        if matches:
            violations.append(f"{path}: found hex colors {matches}")

    assert not violations, "Hex colors found in templates:\n" + "\n".join(violations)


def test_no_inline_executable_scripts_in_templates():
    """D05: All JavaScript must be loaded via external scripts for strict CSP."""
    templates = glob.glob("systor/templates/*.html")
    inline_script_pattern = re.compile(
        r"<script(?![^>]*src=)(?![^>]*type=[\"']application/json[\"'])[^>]*>",
        re.IGNORECASE
    )

    violations = []
    for path in templates:
        content = Path(path).read_text(encoding="utf-8")
        matches = inline_script_pattern.findall(content)
        if matches:
            violations.append(f"{path}: found inline script tags {matches}")

    assert not violations, "Inline executable scripts found in templates:\n" + "\n".join(violations)


def test_minimum_font_size_in_css():
    """D02: No font-size below 11px in stylesheets."""
    css_files = glob.glob("systor/static/css/*.css")
    assert css_files, "No CSS files found to test"
    small_font_pattern = re.compile(r"font-size:\s*(8|9|10)(\.[0-9]+)?px", re.IGNORECASE)

    violations = []
    for path in css_files:
        content = Path(path).read_text(encoding="utf-8")
        for line_no, line in enumerate(content.splitlines(), start=1):
            if small_font_pattern.search(line):
                violations.append(f"{path}:{line_no}: {line.strip()}")

    assert not violations, "Font sizes below 11px found:\n" + "\n".join(violations)


def test_no_unsafe_inline_in_web_csp():
    """DoD Gate: CSP must not permit unsafe-inline."""
    web_code = Path("systor/web.py").read_text(encoding="utf-8")
    assert "unsafe-inline" not in web_code, "Found 'unsafe-inline' in systor/web.py"


def test_no_secure_fallback_in_codebase():
    """DoD Gate: secure-fallback banned across all codebase."""
    violations = []
    for root, _, files in os.walk("systor"):
        for file in files:
            if file.endswith((".py", ".html", ".js", ".css")):
                p = Path(root) / file
                content = p.read_text(encoding="utf-8", errors="ignore")
                if "secure-fallback" in content:
                    violations.append(str(p))

    assert not violations, f"Found 'secure-fallback' in: {violations}"
