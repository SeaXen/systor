# Release Notes: Systor v1.1.0

🎉 **Systor v1.1.0** is officially released! 

Systor is a lightweight Linux server and homelab monitoring system featuring a responsive web dashboard, persistent sustained-threshold alerts, SMART disk insights, speedtesting, and one-click disaster recovery.

---

### Highlights in v1.1.0

#### 🎨 WCAG AA Certified Design System & 3-Way Theme Switcher
- **Warm Dark (`warm-dark`)**: Neutral slate background (`#0b0b0b`), elevated panels (`#141414`), crisp borders (`#262626`), and signature teal accents (`#1d9e75`).
- **Paper Light (`light`)**: Warm paper background (`#f9f9f7`), crisp white panels (`#ffffff`), dark high-contrast typography (`#161616`), and theme-adapted accent tokens.
- **Classic Dark (`classic-dark`)**: Preserved deep GitHub navy (`#0f1117`) and blue accents (`#58a6ff`).
- **Zero Contrast Failures**: Fully compliant with WCAG AA across all three themes (0 contrast failures across 2,600+ text nodes).
- **Responsive Layout**: Zero horizontal overflow from 390px mobile viewports up to 1440px ultra-wide screens.
- **Pure Native Canvas Charts**: Hand-rolled, zero-dependency HTML5 canvas charts dynamically responsive to theme token changes.

#### 🛡️ Full-Stack Security Hardening
- **Double-Submit CSRF Protection**: Cryptographically secure double-submit CSRF cookie/header validation (`systor_csrf`) on all non-GET API and mutation requests.
- **Session Hardening**: Cookies enforce `SameSite=Lax` and `HttpOnly=True`.
- **Storage Isolation**: Storage browsing and previewing strictly restricted to configured `allowed_roots`, rejecting unauthorized paths with HTTP 403.
- **Tar-Slip Vulnerability Elimination**: Backup restoration strictly extracts with safety filters, preventing directory traversal via malicious archives.
- **Emergency Console Security**: Removed fallback default credentials. Terminal access requires an explicit `auth.password_hash` with per-IP rate limiting and ephemeral session tokens.
- **Reverse Proxy Protection**: Enforced `trusted_proxies` validation against header spoofing on public reverse-proxy setups.

#### ⚡ Performance & High Concurrency Reliability
- **Multi-threaded Web Server**: Waitress thread pool configured with `threads=16` for responsive multi-client concurrency.
- **Subprocess Limiting**: Process-wide `BoundedSemaphore(2)` prevents external CLI tools (Ookla speedtest, docker stats) from saturating CPU.
- **SSE Stream Concurrency Cap**: Live log streaming capped at 2 concurrent streams to protect host resources.
- **SQLite Concurrency & Data Integrity**: Database operates in WAL mode with connection-level `PRAGMA busy_timeout=5000` and thread-safe `threading.RLock()`.
- **Accurate Metric Sampling**: Dynamic sysconf page-size derivation prevents memory calculation errors on 64 KB page-size architectures (arm64).

#### 🐳 Hardened Containers & Systemd Sandboxing
- **Least-Privilege Docker**: `docker.sock` removed from default compose files (opt-in profile). Added `security_opt: ["no-new-privileges:true"]`.
- **Systemd Sandboxing**: Added `NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=strict`, `ProtectHome=true`, and explicit `ReadWritePaths`.

---

### Verification & Testing Suite

- **107 Automated Pytest Unit & Integration Tests Passed (100%)**
- **51 Playwright E2E Multi-Viewport Checks Passed (100%)**
- **Automated Design & Accessibility Audit Passed**:
  - `0` Contrast Failures in Warm Dark (735 nodes checked)
  - `0` Contrast Failures in Paper Light (726 nodes checked)
  - `0` Contrast Failures in Classic Dark (735 nodes checked)
  - `0` Horizontal Overflow across all 27 page/viewport combinations
  - `0` Console Errors across all 27 page/viewport combinations
  - `51` Semantic `<h2>` Section Headings
  - `100%` Form Control Labeling (`<label for="...">`)
  - Terminal Dialog Focus Trap & Restoration Verified

---

### Installation Quick-Start

#### Option A: Automated Remote Installer (Bare-Metal / VPS / Raspberry Pi)
```bash
curl -sSL https://raw.githubusercontent.com/SeaXen/systor/main/install.sh | sudo bash
```

#### Option B: Docker Compose
```bash
docker compose up -d
```

#### Option C: Python PIP
```bash
pip install .
systor serve all
```
