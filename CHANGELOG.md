# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on Keep a Changelog.

## [1.1.0] - 2026-11-02
 
### Added
- **Hardware Telemetry & Sensor Integration**: Native non-blocking telemetry for NVIDIA GPUs (GPU util %, VRAM used/total, temperature, power draw via `nvidia-smi`), Raspberry Pi SoC diagnostics (undervoltage and CPU throttling flags via `vcgencmd`), and Network UPS Tools (NUT) battery/load metrics. Built with dynamic auto-hiding to keep the dashboard clean when hardware is absent.
- **Docker Compose Stacks & Container Telemetry**: Automatic project grouping of running containers by `com.docker.compose.project` label with composite CPU %, aggregate RAM footprint, individual service status badges, and stack restart actions.
- **Security & Network Audit Engine**: Live inspection of open TCP/UDP listening ports, socket binding addresses, UFW firewall rules count/status, and Fail2ban active jails and banned threat counts.
- **Server-Sent Events (SSE) Live Log Streaming**: Real-time log streaming for host Systemd services (`journalctl -u`) and Docker containers (`docker logs -f`) with auto-scroll and terminal output formatting.
- **Two-Way Interactive Telegram Bot Daemon**: Long-polling bot responding directly to mobile commands: `/status` (live host summary), `/speed` (instant network speedtest), `/alerts` (recent incidents), and `/ping`.
- **Secure In-Browser Emergency Terminal**: Slide-up diagnostic console guarded strictly by private LAN/Tailscale verification, admin password re-authentication, and client-bound session tokens.
- **Design Rhythm & Universal Layout Tokens**: Unified card gaps across all pages (`--gap-grid: 10px`, `--gap-page: 10px`), eliminated toolbar overflow scrollbars with clean flex-wrapping, integrated semantic `h2.dash-title` operator headers, and introduced sleek 6px custom scrollbars.

## [1.0.0] - 2026-11-02

> **Corrections applied 2026-11-02 (v1.1.0).** Four statements in this entry
> described intent rather than the shipped code. They are left in place below
> because a changelog is a historical record; the accurate current behaviour is:
>
> - **"real-time Chart.js palette adaptation"** — Chart.js is not a dependency
>   and is not used. Charts are drawn with a small hand-rolled canvas renderer
>   (`systor/static/js/app.js`). There is no CDN dependency by design.
> - **"HTTP, HTTPS, TCP, Ping, and DNS endpoints"** — the uptime monitor
>   implements `http://`, `https://` and `tcp://host:port` only. ICMP ping and
>   DNS resolution are not implemented.
> - **"start/stop/restart"** — `systor/services/systemd_service.py` implements
>   `systemctl restart` only. There is no start or stop action.
> - **"zero-downtime database restore"** — restore is safe but not zero-loss:
>   samples the collector commits during the restore window are discarded.
>   See `systor/services/backup_service.py` for the exact sequence.


### Added
- **ExamForge Design System & 3-Way Theme Switcher**: Added Warm Dark (`#0b0b0b` neutral slate + signature teal `#1d9e75`), Paper Light (`#f9f9f7` warm paper), and Classic Dark (`#0f1117` GitHub dark + blue `#58a6ff`) with zero-flash preload, persistent local storage, and real-time Chart.js palette adaptation.
- **Progressive Web App (PWA) Support**: Native standalone web application capabilities, Web App Manifest (`manifest.json`), high-resolution app icons (192x192, 512x512 maskable), and Service Worker (`sw.js`) with Cache-First static asset caching and Network-Only live telemetry.
- **Mobile Responsive Layout Hardening**: Zero horizontal layout overflow across all viewports (down to 390px mobile) and full compliance with touch target floors (`>=40px`).
- **Web Interface Guidelines & A11y Compliance**: Full accessibility alignment with semantic HTML, keyboard handlers, autocomplete tags, and `prefers-reduced-motion` compliance.
- **Drive Health & SMART Insights**: Real-time NVMe/SATA/SAS telemetry, wear levels, reallocated sectors, and disk health indicators.
- **Service Uptime & Blackbox Monitoring**: Configurable background probes for HTTP, HTTPS, TCP, Ping, and DNS endpoints with latency tracking and history.
- **Systemd Service Manager & Audit Logging**: Host system service controls (start/stop/restart) with security audit history.
- **SSL / TLS Certificate Tracker**: Live certificate chain validation, issuer verification, and expiration countdowns.
- **Security Updates & Kernel Reboot Indicators**: Notification and dashboard banner for pending APT/DNF security packages and `/var/run/reboot-required`.
- **Prometheus Metric Exporter (`/metrics`)**: OpenMetrics scraping endpoint for external Prometheus/Grafana collectors.
- **One-Click Disaster Recovery**: Complete JSON configuration export and zero-downtime database restore.
- **Official Multi-Architecture Docker Container**: Automated `linux/amd64` and `linux/arm64` builds on GitHub Container Registry (`ghcr.io/seaxen/systor:latest`).
- **Host Network & Process Monitoring Docker Setup**: Host PID namespace with host networking, `/proc`, `/sys`, `/dev`, D-Bus, and Docker sockets without `--privileged`.
- **Automated 1-Line Remote Installer**: Headless `curl -sSL ... | sudo bash` auto-bootstrapping installer alongside local git support.
- **Unified Process Supervision**: Built-in `systor serve all` coordinating background metrics collection and Waitress web serving with `tini` signal handling.

### Changed
- Bumped project version to `1.0.0` production release.
- Upgraded installer with automatic dependency detection for `smartmontools`, `python3-venv`, and `curl`.

---

## [0.1.0] - 2026-06-21

### Added
- Branded Flask + Waitress dashboard with live CPU, temperature, memory, swap, disk I/O, CPU load, and network charts
- Apps page for host and Docker process visibility
- Network page with interface-aware usage views and rollups
- Speed page with official Ookla CLI integration, scheduler, and history
- Storage page with allowed-root browsing, uploads/downloads, chunk upload, duplicate scan, trash/restore, and optional action password
- Telegram and Discord notification setup, testing, and recent delivery history
- Public read-only dashboard mode for Cloudflare/public exposure while keeping admin surfaces LAN/Tailscale/local only
- Systemd service units for collector and web

### Changed
- README rewritten for public release quality
- Added real dashboard and speed screenshots to documentation
- Public dashboard optimized with bundled endpoint, ETag support, and short cache window to reduce Cloudflare overhead

### Fixed
- Public tunnel now blocks sensitive/admin pages and APIs
- Storage mount enumeration and browse behavior made safer and faster on problematic mounts
- Web + collector startup path re-verified under systemd

[0.1.0]: https://github.com/SeaXen/systor/releases/tag/v0.1.0
