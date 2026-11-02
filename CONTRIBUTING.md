# Contributing to Systor

Thank you for your interest in contributing to Systor!

Systor is designed to be a lightweight, zero-dependency, self-hosted Linux system monitor and administration suite.

## Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/SeaXen/systor.git
   cd systor
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install development dependencies:**
   ```bash
   pip install -r requirements-dev.txt
   pip install -e .
   ```

4. **Install Playwright browsers (for E2E tests and design audits):**
   ```bash
   playwright install chromium
   ```

## Code Guidelines & Principles

- **Zero External Bundling**: Frontend scripts and stylesheets use native ES6, semantic HTML5, and CSS custom properties. Do not introduce bundlers (Webpack, Vite, Tailwind, etc.) or external CDN runtime scripts.
- **Python Quality**:
  - Follow PEP 8 and modern Python 3.9+ type annotations.
  - Run `ruff check systor` to ensure no lint violations.
  - All SQL queries use parameter substitution; SQLite runs in WAL mode with connection locks.
- **Accessibility & Design**:
  - All form inputs require explicit `<label for="...">` associations.
  - Page structure must maintain logical `<h1>` and `<h2>` hierarchy.
  - Colors must achieve WCAG AA contrast (>= 4.5:1 for normal text, >= 3:1 for graphical lines and large text) across all three themes: `warm-dark`, `light`, and `classic-dark`.

## Running Tests

Before submitting a pull request, ensure all test suites pass:

```bash
# 1. Unit and integration tests
pytest tests/ -q

# 2. End-to-end browser tests
python tests/test_webapp_playwright.py

# 3. Comprehensive multi-viewport, multi-theme design audit
python tools/design_audit.py
```

All 3 gates must pass with zero errors.

## Pull Request Process

1. Create a feature branch: `git checkout -b feat/my-improvement`.
2. Commit your changes with conventional commit prefixes: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`.
3. Ensure no temporary or scratch files are committed.
4. Submit your pull request against `main`.
