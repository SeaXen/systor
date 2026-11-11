# Systor Product Roadmap & Architecture Plan

This document synthesizes and consolidates Systor's engineering milestones, feature roadmaps, and architectural direction. Historical initial plans and planning artifacts are archived under `docs/history/`.

---

## 1. Vision & Core Philosophy

Systor is the ultra-lightweight, zero-external-dependency, privacy-first Linux system monitor designed for single-board computers (Raspberry Pi), homelabs, VPS instances, and bare-metal production servers.

### Core Guarantees
- **Minimal RAM Footprint:** Operates natively in under 30 MB of RAM (typically 18–25 MB).
- **Zero npm / External CDN Dependencies:** Uses vanilla modern JavaScript and responsive CSS custom properties with zero runtime build step.
- **Python >= 3.9 Native:** Runs across modern Debian, Ubuntu, Alpine, and container environments without C-extension dependencies.
- **Strict Security & Privacy First:** No telemetry, no external callbacks, hardened Content Security Policy (`script-src 'self'`), strict local authentication gates, and zero-privilege containerization.

---

## 2. Completed Milestones (v1.0.0 – v1.2.0)

### Phase 1: Security Hardening (S01 – S08)
- [x] **S01 Session Secret Auto-generation:** Guaranteed 32-byte cryptographically secure session secrets persisted with `0600` permissions.
- [x] **S02 Proxy Forwarding & Anti-Spoofing:** Strict CIDR validation for reverse proxy headers (`CF-Connecting-IP`, `X-Forwarded-For`).
- [x] **S03 Authentication & Rate Limiting:** Brute-force exponential backoff and IP-based lockout.
- [x] **S04 Network Uptime Probes SSRF Defense:** Validation blocks loopback, private RFC-1918, link-local, and cloud metadata (169.254.169.254).
- [x] **S05 Subprocess & Shell Injection Elimination:** Replaced all shell invocations with `shlex`-tokenized argument arrays.
- [x] **S06 Atomic Settings Storage:** Zero-loss atomic writes via temporary files and fsync.
- [x] **S07 Encrypted & Masked Credentials:** Masked token representations in UI with write-only preservation.
- [x] **S08 Container Least-Privilege:** Multi-stage Dockerfile running as unprivileged user `10001:10001` with `cap_drop: ALL` and read-only rootfs.

### Phase 2: Correctness & Observability (C01 – C06)
- [x] **C01 Hardware Sensors:** NVIDIA GPU (`nvidia-smi`), Raspberry Pi throttling (`vcgencmd`), and NUT UPS status.
- [x] **C02 Drive Health & SMART:** SMART attributes, NVMe temperature/wear estimation, and sysfs fallback.
- [x] **C03 SSL Certificate Tracking:** Remote TLS handshake validity auditing and Let's Encrypt renewal monitoring.
- [x] **C04 Disaster Recovery & Backup:** Atomic `.tar.gz` export and rollback-safe configuration restore.
- [x] **C05 Live Audit Log:** Storage operation audit trail (`/var/lib/systor/storage_ops.jsonl`).
- [x] **C06 Prometheus Exporter:** Native OpenMetrics endpoint at `/metrics`.

### Phase 3: Testing & CI Hardening (T01 – T04)
- [x] **T01 Full Test Suite Expansion:** 196 automated tests covering edge cases, network probes, and CLI.
- [x] **T02 80%+ Test Coverage Gate:** Enforced via `--cov-fail-under=80` in continuous integration.
- [x] **T03 Multi-Tool CI Pipeline:** GitHub Actions workflow executing pytest, ruff, mypy, bandit, and pip-audit.
- [x] **T04 End-to-End Headless Playwright Verification:** Browser automation validating layout, mobile bottom bar, and dialog focus.

### Phase 4: Accessible Design & Design Tokens (D01 – D16)
- [x] **D01/D09 Unified Design Tokens:** Complete 3-theme design system (`warm-dark`, `light`, `classic-dark`) in `tokens.css`.
- [x] **D02 Minimum Font Size:** Strict >= 11px floor across all interfaces.
- [x] **D03 Semantic CSS:** Zero hardcoded `#hex` colors in templates.
- [x] **D05 Strict CSP & Script Extraction:** 100% of inline scripts extracted into modular page controllers under `systor/static/js/pages/`.
- [x] **D06 Accessible Custom Dialogs:** Accessible modal replacement for native browser prompts with keyboard focus trapping.
- [x] **D08/D10 Mobile Ergonomics:** Bottom navigation bar and slide-up drawer for mobile screens (390px viewport).
- [x] **D14 Zero Contrast Failures:** 100% WCAG 2.2 AA pass across all viewports and themes via automated pixel auditing.

### Phase 5: Architecture & Code Quality (Q01 – Q05)
- [x] **Q01 Service Decoupling:** Blueprints delegate database and hardware queries to dedicated service modules.
- [x] **Q02 Strict Typing:** 0 mypy errors across all 33 source files.
- [x] **Q03 Worker Thread Safety:** Process-isolated background workers with WSGI reloader guards.
- [x] **Q04/Q05 Schema Validation & CLI:** `systor config check` command validating configuration syntax, ranges, and types.

---

## 3. Future Roadmap (v1.3.0+)

### High-Priority Enhancements
1. **Multi-Node Cluster Aggregation:**
   - Lightweight peer-to-peer gossip or hub-and-spoke node federation.
   - Aggregate dashboard viewing multiple servers in a single unified view.
2. **Custom Alert Routing Rules:**
   - Per-tag notification routing (e.g. storage alerts to Discord `#storage`, critical CPU alerts to Telegram).
   - Maintenance window scheduling (silence alerts during scheduled upgrades).
3. **Advanced SMART Diagnostics:**
   - Automated periodic self-test scheduling (SMART short/long tests).
   - Historical temperature and reallocated sector trend graphs.
4. **Enhanced Mobile PWA Capabilities:**
   - Web Push Notifications for system alerts when web dashboard is saved to home screen.
   - Background periodic sync for server status indicators.

---

## 4. Archive Reference

Historical design documents and planning manifests have been archived:
- `docs/history/systor-ten-on-ten-plan.md`
- `docs/history/SYSTOR-10x10-PLAN.md`
- `docs/history/systor-expansion-plan.md`
