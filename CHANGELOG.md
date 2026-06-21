# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on Keep a Changelog.

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
