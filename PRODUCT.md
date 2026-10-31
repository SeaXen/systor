# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Self-hosters, homelab enthusiasts, DevOps engineers, and Linux system administrators running single-node servers, mini-PCs, homelabs, or cloud VPS instances who need immediate, low-overhead visibility into host performance, Docker containers, network throughput, internet speed stability, and disk storage without deploying heavy infrastructure like Prometheus/Grafana or Netdata.

## Product Purpose

`systor` is a lightweight, zero-external-dependency host and container telemetry monitor, network throughput tracker, scheduled Ookla speedtest runner, and authenticated LAN storage manager. It exists to provide instant operational clarity and actionable server health metrics with near-zero resource consumption (<30 MB RAM), persistent historical telemetry, and single-admin security.

## Positioning

Unlike bloated monitoring platforms that require NodeJS runtimes, Redis, Elasticsearch, or multiple Docker containers, `systor` runs entirely on native Python 3 with a lightweight SQLite database and a vanilla HTML5/CSS3/ES6 web interface. It uniquely integrates host metrics, container stats, historical network accounting, official Ookla CLI speedtesting with threshold alerts, and a sandboxed LAN/Tailscale file manager in a single self-contained daemon.

## Operating Context

- Deployed as dual systemd services (`systor-collector.service` and `systor-web.service`) or run directly via Python.
- Typical environments: Ubuntu, Debian, Proxmox VE, Arch Linux, Alpine, Raspberry Pi OS, macOS, and Windows.
- Accessed via local LAN (`http://<ip>:8009`), Tailscale/WireGuard private mesh, or behind reverse proxies and Cloudflare Tunnels.
- Dual-role access model: Public read-only view for status pages and dashboards, with protected single-admin login for process actions, logs, settings, and file management.

## Capabilities and Constraints

- **Telemetry & Monitoring**: Sub-second snapshot collection of CPU, per-core load, temperature sensors, RAM, swap, per-device disk I/O, and per-interface network throughput.
- **Docker Integration**: Auto-detects Docker engine socket; surfaces container-level CPU, memory, disk, and network utilization alongside host processes.
- **Network Accounting**: Long-term SQLite rollups of bandwidth usage (daily, weekly, monthly, yearly) and interface-level counters.
- **Speedtest Engine**: Native integration with official Ookla Speedtest CLI; supports manual runs, configurable background cron scheduling, failure alerts, and server presets.
- **Alerting & Notifications**: Telegram Bot and Discord Webhook notifications for CPU/temperature/disk/memory threshold breaches and degraded speedtest results.
- **Storage Management**: Sandboxed multi-root file browser, disk usage tree analysis, large/old file detection, checksum duplicate identification, and soft-delete trash recovery.
- **Security**: Single-admin session authentication, rate-limiting lockout against brute-force attacks, and automatic write-action disabling when accessed over public tunnels.
- **Technical Invariant**: Zero external JavaScript or CSS dependencies. No npm build step. Pure standard CSS tokens, canvas charting, and vanilla ES6 fetch.

## Brand Commitments

- **Tone**: Engineering-focused, authoritative, precise, and utilitarian.
- **Palette**: GitHub-dark inspired palette with high-contrast functional color semantics (Accent Blue `#58a6ff`, Cyan `#22d3ee`, Emerald Green `#3fb950`, Amber `#d29922`, Crimson `#f85149`, Amethyst `#bc8cff`, Background Dark `#0f1117`).
- **Typography**: Native system sans font stack with strict `tabular-nums` monospace for all timestamps, telemetry readings, and metrics.

## Evidence on Hand

- Project codebase at `d:/Repo/systor`.
- Full test suite: 52 automated pytest test cases (`tests/test_auth.py`, `tests/test_blueprints.py`, `tests/test_collector.py`, `tests/test_metrics.py`, `tests/test_security.py`, `tests/test_storage.py`).
- Production templates in `systor/templates/` (`base.html`, `dashboard.html`, `apps.html`, `network.html`, `speed.html`, `storage.html`, `settings.html`, `logs.html`, `alerts.html`, `login.html`).
- Zero-dependency assets in `systor/static/css/style.css` and `systor/static/js/app.js`.

## Product Principles

1. **Zero External Runtime Dependencies**: The UI and backend must operate 100% air-gapped without external CDNs, fonts, or npm dependencies.
2. **Deterministic Information Density**: Pack high-signal operational telemetry into clean, scannable layouts without decorative fluff or layout shifts.
3. **Public-Safe by Default**: Exposing the dashboard on a tunnel or status screen must never expose destructive control, logs, credentials, or file operations.
4. **Resilient Local Telemetry**: Collector and web layers run independently; transient network or sensor failures must gracefully degrade without UI stalls.

## Accessibility & Inclusion

- Compliance with WCAG 2.2 AA / AAA contrast standards on dark surfaces.
- Full keyboard operability with prominent `:focus-visible` outline rings.
- Semantic HTML landmarks (`<main>`, `<nav role="navigation">`, `<header role="banner">`) and skip links.
- ARIA image roles and descriptive labels on all telemetry charts and data visualizations.
- Live announcements (`role="alert"`, `aria-live="polite"`) for status messages and notifications.
- Respect for user motion preferences via `@media (prefers-reduced-motion: reduce)`.
