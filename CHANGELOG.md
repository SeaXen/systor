# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on Keep a Changelog.

## [1.0.0] - 2026-10-31

### Added
- **Drive Health & SMART Insights**: Real-time NVMe/SATA/SAS telemetry, wear levels, reallocated sectors, and disk health indicators.
- **Service Uptime & Blackbox Monitoring**: Configurable background probes for HTTP, HTTPS, TCP, Ping, and DNS endpoints with latency tracking and history.
- **Systemd Service Manager & Audit Logging**: Host system service controls (start/stop/restart) with security audit history.
- **SSL / TLS Certificate Tracker**: Live certificate chain validation, issuer verification, and expiration countdowns.
- **Security Updates & Kernel Reboot Indicators**: Notification and dashboard banner for pending APT/DNF security packages and `/var/run/reboot-required`.
- **Prometheus Metric Exporter (`/metrics`)**: OpenMetrics scraping endpoint for external Prometheus/Grafana collectors.
- **One-Click Disaster Recovery**: Complete JSON configuration export and zero-downtime database restore.
- **Official Multi-Architecture Docker Container**: Automated `linux/amd64` and `linux/arm64` builds on GitHub Container Registry (`ghcr.io/seaxen/systor:latest`).
- **Least-Privilege Docker Compose Setup**: Host PID namespace with read-only `/sys`, `/dev`, D-Bus, and Docker sockets without `--privileged`.
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
