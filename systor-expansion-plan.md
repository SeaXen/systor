# Systor 7-Feature Expansion Plan: Architecture & Implementation

> **Goal:** Expand Systor into an all-in-one homelab & server management suite by implementing 7 high-impact, zero-dependency features in a single comprehensive rollout.

---

## Architectural Ground Rules (Invariants)
1. **Zero External Runtime Dependencies:** Pure Python standard library (`sqlite3`, `ssl`, `socket`, `subprocess`, `urllib`, `tarfile`), pure vanilla HTML5/CSS3/ES6.
2. **Graceful Cross-Platform & Privilege Degradation:** If `smartctl` is missing or running non-root on Windows, return clean empty structures without raising uncaught HTTP 500 errors.
3. **Security Invariant:** All write/restart/restore actions strictly require `client_private()` + admin authentication (`auth_logged_in()`).
4. **Resilient SQLite Storage:** New tables added cleanly with `CREATE TABLE IF NOT EXISTS` and automatic schema migrations.

---

## 1. Feature Specifications & Technical Design

### Feature 1: SMART Disk Health & NVMe Wear Inspector
- **File:** `systor/services/smart_service.py`
- **Logic:**
  - Executes `smartctl --scan --json` and `smartctl -a -j <device>` asynchronously with a 5-second timeout.
  - Fallback parser inspects `/sys/block/<dev>/` for rotational state (SSD vs HDD) and device capacity.
  - Extracts device model, serial, overall health (`PASSED`/`FAILED`), temperature, power-on hours, reallocated sectors (SATA), and wear percentage / TBW written (NVMe).
  - Caches results for 60 seconds to prevent unnecessary disk spin-ups.
- **Storage & Alerts:** `Storage.log_alert()` triggered if `smart_status.passed == False` or `reallocated_sectors > 0`.
- **UI:** Embedded into `/storage` as a dedicated **Drive Health & SMART** tab/card with health badges, wear gauges, and drive details table.

---

### Feature 2: Service Uptime & Blackbox Health Pinger
- **File:** `systor/services/uptime_service.py` & `systor/blueprints/network.py`
- **Database Schema:**
  ```sql
  CREATE TABLE IF NOT EXISTS uptime_targets (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      url TEXT NOT NULL,
      method TEXT NOT NULL DEFAULT 'GET',
      expected_status INTEGER NOT NULL DEFAULT 200,
      interval_sec INTEGER NOT NULL DEFAULT 60,
      timeout_sec REAL NOT NULL DEFAULT 5.0,
      enabled INTEGER NOT NULL DEFAULT 1,
      created_at INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS uptime_checks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      target_id INTEGER NOT NULL,
      ts INTEGER NOT NULL,
      status_code INTEGER,
      latency_ms REAL,
      ok INTEGER NOT NULL,
      error TEXT,
      FOREIGN KEY(target_id) REFERENCES uptime_targets(id) ON DELETE CASCADE
  );
  CREATE INDEX IF NOT EXISTS idx_uptime_checks_target_ts ON uptime_checks(target_id, ts);
  ```
- **Engine:** Worker thread pings enabled targets on their interval, records HTTP response codes and latency.
- **API:**
  - `GET /api/uptime`: Returns all targets with real-time status, 24h uptime %, and average latency.
  - `POST /api/uptime/targets`: Add new target.
  - `PUT /api/uptime/targets/<id>`: Update target.
  - `DELETE /api/uptime/targets/<id>`: Remove target.
- **UI:** A **Service Uptime** card on the Network page (`/network`) with latency sparklines, status pills (Online / Degraded / Down), and a modal to manage targets.

---

### Feature 3: Systemd Service Manager & Admin Controls
- **File:** `systor/services/systemd_service.py` & `systor/blueprints/apps.py`
- **Logic:**
  - Queries `systemctl list-units --type=service --all --output=json` (with non-blocking fallback if not running systemd).
  - Filters and categorizes system services (Active, Inactive, Failed).
  - Admin Action: `POST /api/apps/systemd/<unit>/restart`
    - Safe restart with a 10-second timeout.
    - Safety Deny-list: Rejects direct restarts of low-level PID 1 / raw init units.
    - Rate-limited and logged to SQLite `systemd_audit_log`.
- **UI:** Added as a **System Services** tab on the Apps page (`/apps`) with live status chips (`active`, `failed`), search filter, and safe Restart button with confirmation prompt.

---

### Feature 4: SSL / TLS Certificate Expiry Tracker
- **File:** `systor/services/ssl_service.py`
- **Logic:**
  - Probes configured hostnames / domains via Python `ssl` and `socket` to retrieve certificate details.
  - Auto-discovers local Let's Encrypt certificates from `/etc/letsencrypt/live/`.
  - Calculates days remaining until expiration.
- **API:** `GET /api/ssl/certs`
- **UI:** SSL status card with warning badges:
  - 🟢 Green: > 30 days remaining
  - 🟡 Amber: 8–30 days remaining
  - 🔴 Red: <= 7 days remaining (triggers notification alert)

---

### Feature 5: System Security Patches & Kernel Reboot Indicator
- **File:** `systor/metrics.py` & `systor/templates/base.html`
- **Logic:**
  - Detects `/var/run/reboot-required` (indicating new kernel installed).
  - Parses `/var/lib/update-notifier/updates-available` or checks Debian/Ubuntu package notifier.
  - Injects values into `collect_snapshot()["system_updates"]`:
    `{"reboot_required": bool, "updates_total": int, "updates_security": int}`
- **UI:** Header chip in `base.html`:
  - `📦 4 Updates (2 Security)`
  - `🔄 Reboot Required`

---

### Feature 6: Prometheus Scraping Endpoint (`/metrics`)
- **File:** `systor/blueprints/dashboard.py`
- **Route:** `GET /metrics`
- **Output:** Native Prometheus OpenMetrics exposition format:
  ```text
  # HELP systor_cpu_percent Current CPU usage percent
  # TYPE systor_cpu_percent gauge
  systor_cpu_percent 14.2
  # HELP systor_memory_used_bytes Memory used in bytes
  # TYPE systor_memory_used_bytes gauge
  systor_memory_used_bytes 4294967296
  # HELP systor_disk_used_percent Disk usage percentage per mount
  # TYPE systor_disk_used_percent gauge
  systor_disk_used_percent{mount="/"} 42.5
  # HELP systor_uptime_target_up Uptime status of monitored target (1=up, 0=down)
  # TYPE systor_uptime_target_up gauge
  systor_uptime_target_up{name="Plex",url="http://192.168.1.50:32400"} 1
  ```
- **Security:** Accessible to private LAN clients or authenticated via `Authorization: Bearer <token>`.

---

### Feature 7: One-Click Disaster Recovery (Backup & Restore Engine)
- **File:** `systor/services/backup_service.py` & `systor/blueprints/settings.py`
- **Logic:**
  - `GET /api/backup/export`: Safely checkpoints SQLite WAL journal, packages `systor.db` + active `config.yaml` + metadata into a gzip `.tar.gz` archive and streams download.
  - `POST /api/backup/restore`: Accepts `.tar.gz` upload, verifies archive integrity, checks SQLite database integrity (`PRAGMA integrity_check`), replaces database and configuration, and safely restarts worker caches.
- **UI:** A dedicated **Disaster Recovery & Backup** section in `/settings` with one-click Download Backup and Drag-and-Drop Restore.

---

## 2. Implementation Work Breakdown & File Changes

```
systor/
├── services/
│   ├── smart_service.py        # [NEW] SMART & NVMe wear reader with caching
│   ├── uptime_service.py       # [NEW] Asynchronous HTTP/TCP health checker
│   ├── systemd_service.py      # [NEW] Systemd unit inspector and safe restart controller
│   ├── ssl_service.py          # [NEW] TLS certificate expiry prober
│   └── backup_service.py       # [NEW] Database checkpoint and tar.gz backup/restore
├── storage.py                  # [UPDATE] Add tables for uptime_targets, uptime_checks, audit_log
├── metrics.py                  # [UPDATE] Add reboot-required & updates-available readers
├── blueprints/
│   ├── dashboard.py            # [UPDATE] Add /metrics Prometheus exporter route
│   ├── apps.py                 # [UPDATE] Add systemd listing and safe restart API
│   ├── storage.py              # [UPDATE] Add /api/storage/smart and drive health payload
│   ├── network.py              # [UPDATE] Add /api/uptime endpoints
│   └── settings.py             # [UPDATE] Add /api/backup/export and /api/backup/restore
├── templates/
│   ├── base.html               # [UPDATE] Header chip for updates/reboot required
│   ├── storage.html            # [UPDATE] Drive Health & SMART tab/card
│   ├── apps.html               # [UPDATE] Systemd Units tab with safe restart modal
│   ├── network.html            # [UPDATE] Service Uptime monitor with target manager
│   └── settings.html           # [UPDATE] Backup & Restore cards
└── tests/
    ├── test_smart_service.py   # [NEW] Unit tests for SMART parsing
    ├── test_uptime_service.py  # [NEW] Unit tests for uptime checker
    ├── test_systemd_service.py # [NEW] Unit tests for systemd restart controller
    ├── test_backup_service.py  # [NEW] Unit tests for tar.gz backup & restore
    └── test_prometheus.py      # [NEW] OpenMetrics format validation
```

---

## 3. Verification & Quality Gates
1. **Unit & Integration Tests:** 100% pass across all new services.
2. **Playwright E2E Tests:** Run headless Chromium verifying:
   - Drive Health tab renders in Storage.
   - Systemd units render in Apps.
   - Uptime monitor renders in Network.
   - Backup download returns valid tar.gz.
   - `/metrics` returns valid OpenMetrics plaintext.
3. **Security Audit:** Verify all write endpoints reject unauthorized/public requests.
4. **Git Push:** Clean atomic commit pushed to `origin main`.
