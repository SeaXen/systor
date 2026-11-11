# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.2.x   | :white_check_mark: |
| 1.1.x   | :white_check_mark: |
| < 1.1.0 | :x:                |

## Reporting a Vulnerability

We take the security of Systor seriously. If you discover a security vulnerability, please **do not report it publicly** on GitHub issues.

Instead, report it via GitHub Private Vulnerability Reporting or email:
**drpelagik@gmail.com**

Please include:
- A description of the issue and potential impact
- Step-by-step reproduction instructions or proof-of-concept
- Any affected components or configurations

You will receive an acknowledgment within 48 hours, followed by an assessment and coordinated disclosure plan.

---

## Threat Model & Security Architecture

Systor employs strict defense-in-depth principles across all components:

### 1. Network Boundary & Reverse Proxy Forwarding
- **Client IP Verification:** Strict validation via `trusted_proxies` ensures untrusted upstream headers (`X-Forwarded-For`, `CF-Connecting-IP`) cannot spoof LAN origin or bypass security gates.
- **Surface Gating:** Internal administration surfaces (`/settings`, `/apps`, `/storage`, `/terminal`) require verified private IP origin (RFC-1918 or CGNAT Tailscale `100.64.0.0/10`) or authenticated admin sessions.

### 2. SSRF (Server-Side Request Forgery) Defense
- **Safe Network Probes:** All outgoing HTTP requests (uptime monitor, webhook testers) route through `systor.notifier.safe_urlopen` which resolves DNS hostnames before socket connection and strictly blocks:
  - Loopback (`127.0.0.0/8`, `::1`)
  - Private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
  - Link-local (`169.254.0.0/16`, `fe80::/10`)
  - Cloud Instance Metadata Services (`169.254.169.254`)

### 3. Subprocess & Command Injection Immunity
- **Zero Shell Execution:** All external system commands (`smartctl`, `systemctl`, `journalctl`, `vcgencmd`, `nvidia-smi`) use `subprocess.run` with discrete argument vectors (`list[str]`) and `shell=False`.
- **Allowed Units Whitelist:** Systemd service control is restricted by glob pattern matchers (`allowed_units: ["systor-*.service"]`).

### 4. Content Security Policy (CSP) & Web Sanitization
- **Strict CSP:** `default-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'; script-src 'self'`.
- **Zero Inline Scripts:** All JavaScript is modularly organized in static files under `/static/js/`. Inline executable scripts are prohibited.
- **Double-Submit CSRF Defense:** Custom header `X-CSRF-Token` paired with HTTP-only cookie validated on all state-changing endpoints.

### 5. Storage & File System Isolation
- **Path Traversal Defense:** Storage manager resolves canonical paths and strictly verifies boundaries within `allowed_roots`.
- **Forbidden Patterns:** Sensitive credentials (`.env`, private keys, SSH keys, `.pem`) are blocked from reading or downloading.
- **Archive Extraction Guard:** Backup restore enforces `tarfile.data_filter` (or equivalent safe extraction validator) to reject paths containing `..` or absolute paths (tar-slip defense).

### 6. Container & Runtime Least Privilege
- **Unprivileged User:** Default container runs as `uid: 10001`, `gid: 10001`.
- **Read-Only Root:** Container root filesystem is mounted `read_only: true` with restricted tmpfs mounts for `/tmp` and `/var/run`.
- **Zero Capabilities:** `cap_drop: [ALL]` with `no-new-privileges: true`.

---

## Automated Security Verification

Our continuous integration pipeline enforces automated security gates on every commit:
- **Bandit SAST:** `bandit -r systor -ll` (zero high or medium severity findings)
- **Dependency Audit:** `pip-audit -r requirements.txt` (zero known CVEs)
- **Security Regression Tests:** `pytest tests/test_security_audit.py`
