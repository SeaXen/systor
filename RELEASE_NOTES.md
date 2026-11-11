# Release Notes: Systor v1.2.0

🎉 **Systor v1.2.0** is officially released!

Systor is the ultra-lightweight, zero-external-dependency, privacy-first Linux system monitor designed for single-board computers (Raspberry Pi), homelabs, VPS instances, and bare-metal production servers.

---

### Highlights in v1.2.0

#### 🛡️ Complete Security Hardening & Zero-Privilege Containerization
- **SSRF Defense in Probes**: All outgoing HTTP requests (uptime monitor, webhook testers) route through `systor.notifier.safe_urlopen` which resolves DNS hostnames before socket connection and strictly blocks loopback (`127.0.0.0/8`, `::1`), private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local, and cloud instance metadata services (`169.254.169.254`).
- **Cryptographically Secure Session Secrets**: Guaranteed 32-byte session secrets auto-generated with `0600` permissions on disk.
- **Strict Content Security Policy**: `default-src 'self'; script-src 'self'; object-src 'none'`. 100% of inline scripts extracted into modular controllers under `/static/js/pages/`.
- **Zero-Privilege Docker Runtime**: Runs as dedicated non-root user `10001:10001`, `cap_drop: ALL`, `no-new-privileges: true`, and `read_only: true` root filesystem.
- **Subprocess Shell Injection Elimination**: Replaced all shell invocations with discrete tokenized argument vectors (`shell=False`).
- **Atomic File Writes**: Configuration saved atomically via temporary file replacement and fsync.

#### 🎨 100% WCAG 2.2 AA Contrast & Accessible UI Polish
- **Zero-Failure WCAG 2.2 AA Contrast**: 0 contrast failures across all 3 themes (warm-dark, paper light, classic-dark) verified via pixel-accurate headless Chromium testing.
- **Accessible Custom Dialogs (`window.SystorDialog`)**: Replaced native browser `alert()`, `confirm()`, and `prompt()` with keyboard-accessible, focus-trapped modal dialogs.
- **Mobile Ergonomics**: Mobile bottom navigation bar and slide-up page drawer for viewports under 700px.
- **Minimum Font Size Floor**: Enforced >= 11px font size floor across all styles.
- **Zero Hardcoded Hex in Templates**: Semantic CSS tokens used exclusively across all HTML templates.

#### 🏗️ Architecture Decoupling & Strict Typing
- **Decoupled Blueprint Architecture**: Blueprints delegate data access and hardware queries to dedicated service modules.
- **Strict Typing**: 0 mypy errors across all 33 source files.
- **Config Schema Validation & CLI**: Hand-rolled schema validator and `systor config check` CLI subcommand.
- **Background Worker Safety**: Thread-safe worker initialization preventing duplicate processes under multi-process WSGI reloaders.

#### 🧪 Testing & Verification
- **Test Suite Expansion**: 204 automated unit and integration tests passing.
- **Enforced Coverage Gate**: Code coverage elevated to **80.29%** enforced with `--cov-fail-under=80`.
- **Database Schema Upgrade Safe**: Automated tests verifying zero data loss during migrations from earlier database versions.
- **Multi-Tool CI Pipeline**: GitHub Actions workflow executing pytest, ruff, mypy, bandit, and pip-audit.
