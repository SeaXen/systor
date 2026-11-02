<p align="center">
  <img src="docs/systor-logo.png" alt="systor" width="120">
</p>

<h1 align="center">systor</h1>

<p align="center">
  Lightweight Linux system monitor with a branded web dashboard, sustained-threshold alerts, storage browser, process views, and Telegram/Discord notifications.
</p>

<p align="center">
  <a href="https://github.com/SeaXen/systor/releases/tag/v1.1.0"><img alt="release" src="https://img.shields.io/github/v/release/SeaXen/systor?display_name=tag&sort=semver&color=1f6feb"></a>
  <a href="https://github.com/SeaXen/systor/blob/main/LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-3fb950"></a>
  <img alt="python" src="https://img.shields.io/badge/python-3.9%2B-58a6ff">
  <a href="https://systor.xuup.top/"><img alt="demo" src="https://img.shields.io/badge/demo-live_dashboard-8957e5"></a>
  <a href="https://seaxen.github.io/systor/"><img alt="site" src="https://img.shields.io/badge/site-GitHub_Pages-21262d"></a>
  <img alt="docker" src="https://img.shields.io/badge/docker-multi--arch-2496ed">
  <img alt="platform" src="https://img.shields.io/badge/platform-Linux-8892a0">
</p>

---

## What it is

`systor` is a small, high-performance self-hosted monitoring application for Linux servers where Prometheus + Grafana is too heavy and terminal-only tools are too limited.

It is built for:
- DietPi / Raspberry Pi / small home lab servers
- Low-RAM VPS machines
- Personal infrastructure where you want a **fast dashboard**, **real alerts**, and **simple operations**

Available as both a **zero-overhead bare-metal systemd installation** (<30MB RAM) and an official **multi-arch Docker container** (`ghcr.io/seaxen/systor:latest`).

---

## Highlights

- **Live dashboard** — CPU, temperature, memory, swap, disk I/O, CPU load, network DL/UL, chart history, top processes, recent alerts
- **Drive Health & SMART Insights** — NVMe/SATA/SAS drive telemetry, wear percentage, reallocated sectors, and real-time disk health scoring
- **Service Uptime & Health Monitoring** — Probes external or internal HTTP, HTTPS, and TCP endpoints with latency history
- **Systemd Service Manager & Audit Logging** — Inspect, restart, and monitor host system services with security audit logs
- **SSL / TLS Certificate Tracker** — Live certificate chain validation, issuer verification, and expiration countdowns
- **Security Updates & Kernel Reboot Indicators** — Instant alerts when APT/DNF pending security updates or `/var/run/reboot-required` are detected
- **Prometheus Metric Exporter (`/metrics`)** — Native OpenMetrics format endpoint for optional external Prometheus/Grafana scraping
- **Emergency Web Console** — Authenticated, rate-limited in-browser emergency console for diagnostic and recovery operations
- **Disaster Recovery** — Full JSON configuration and history export with safe WAL-checkpoint database restoration
- **Apps page** — host + Docker processes ranked by CPU, RAM, network, and disk activity
- **Network page** — live traffic + daily / monthly / yearly usage views with interface filtering
- **Speed page** — official Ookla CLI runs, saved server selection, scheduler, run history, packet loss, and alert thresholds
- **Storage page** — browser for allowed roots, uploads/downloads, chunk uploads, checksums, duplicate scan, trash/restore, action-password support
- **Telegram + Discord notifications** — channel-specific test/send flows from the UI
- **Dual Packaging** — 1-line bare-metal curl installer or 1-line Docker container

---

## Public vs private access model

`systor` supports a practical split between **public viewing** and **private admin access**.

### Public / internet
When accessed from a non-private address, `systor` can be exposed as a **read-only dashboard**:
- dashboard visible
- admin pages hidden
- restart/settings/storage/admin APIs blocked
- no sensitive controls on the public UI

### LAN / localhost / Tailscale
Private access keeps the full surface:
- Settings
- Speed runner
- Apps / Network / Storage pages
- Restart controls
- notification configuration
- admin APIs

### Strict Reverse Proxy Security (`trusted_proxies`)
To prevent public clients from forging `X-Forwarded-For` headers to gain unauthorized admin access, `systor` enforces a `trusted_proxies` configuration (default: `127.0.0.1, ::1`). Headers from unverified public connections are rejected, and only requests passing through explicitly trusted reverse proxies (e.g. local Nginx, Caddy, or Cloudflare Tunnel) are evaluated for upstream client IP.

```yaml
web:
  host: "0.0.0.0"
  port: 6677
  trusted_proxies: "127.0.0.1, ::1"  # Subnets supported, e.g. "10.0.0.0/8, 172.16.0.0/12"
```

---

## Why not just use Grafana?

| Tool | RAM / complexity | Strength | Trade-off |
|---|---:|---|---|
| Prometheus + Grafana | high | powerful ecosystem | heavy for small boxes |
| Netdata | medium-high | instant charts | more surface than many people need |
| Glances web | low | quick install | limited UI / workflow depth |
| **systor** | **low** | focused, branded, practical | intentionally narrower scope |

If you want a small, direct, all-in-one monitor for one machine or a few machines, `systor` is the point.

---

## Quick start

### Option A: Docker Compose (Recommended for Container Stacks)

Run Systor with least-privilege host mounts and zero setup:

```bash
# 1. Download docker-compose.yml
curl -sSL https://raw.githubusercontent.com/SeaXen/systor/main/docker-compose.yml -o docker-compose.yml

# 2. Launch in background
docker compose up -d
```

Or run via single-line `docker run`:
```bash
docker run -d \
  --name systor \
  --restart unless-stopped \
  --pid host \
  -p 6677:6677 \
  -v systor_config:/etc/systor \
  -v systor_data:/var/lib/systor \
  -v systor_log:/var/log/systor \
  -v /sys:/sys:ro \
  -v /dev:/dev:ro \
  -v /var/run/dbus/system_bus_socket:/var/run/dbus/system_bus_socket:ro \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  ghcr.io/seaxen/systor:latest
```

---

### Option B: Bare-Metal Installation (Sub-30MB RAM Native)

Run the automated one-line curl bootstrapper:

```bash
curl -sSL https://raw.githubusercontent.com/SeaXen/systor/main/install.sh | sudo bash
```

Or clone and install manually:
```bash
git clone https://github.com/SeaXen/systor.git
cd systor
sudo ./install.sh
```

Then open:
- Local: <http://127.0.0.1:6677>
- LAN: `http://<host-ip>:6677`

Default bind:
- host: `0.0.0.0`
- port: `6677`

The installer:
- copies the app to `/opt/systor`
- creates an isolated virtualenv in `/opt/systor/venv` (zero system Python pollution, PEP 668 compliant)
- symlinks `/opt/systor/venv/bin/systor` to `/usr/local/bin/systor` for CLI availability
- writes default config to `/etc/systor/config.yaml`
- writes secrets env file to `/etc/systor/systor.env`
- installs and enables `systor-web.service` and `systor-collector.service`
- creates `/var/log/systor/` and `/var/lib/systor/`

---

## Requirements

- Linux
- Python `3.9+` (with `python3-venv`)
- `systemd`

Optional but useful:
- `speedtest` (official Ookla CLI) for WAN tests
- `docker` if you want Docker process visibility
- `tailscale` if you use private overlay access
- `cloudflared` if you want a public tunnel

---

## Services & CLI Control

`systor` provides both an interactive command line interface and systemd background services.

### CLI Commands (`systor`)
```bash
systor status       # View live service statuses, PID, and port
systor snapshot     # Take and display an instantaneous metric snapshot
systor config       # Inspect active configuration
systor serve web    # Run web dashboard in foreground
systor serve collector # Run metric collector in foreground
```

### System Services
| Service | Purpose | Default |
|---|---|---|
| `systor-collector` | metric sampling + alert evaluation | enabled on boot |
| `systor-web` | dashboard and admin UI | enabled on boot |

```bash
systemctl status systor-web
systemctl status systor-collector
systemctl restart systor-web
systemctl restart systor-collector
```

---

## Configuration

Main config:
- `/etc/systor/config.yaml`

Secrets env file:
- `/etc/systor/systor.env`

Example env file:

```bash
SYSTOR_TELEGRAM_BOT_TOKEN=...
SYSTOR_TELEGRAM_CHAT_ID=...
SYSTOR_DISCORD_WEBHOOK=...
```

Config can be adjusted through the **Settings** page for normal operation.

---

## Pages

| Page | URL | Purpose |
|---|---|---|
| Dashboard | `/` | KPI strip, charts, top processes, recent alerts |
| Apps | `/apps` | host + Docker app/process usage |
| Network | `/network` | live traffic and usage rollups |
| Speed | `/speed` | speedtest runner, scheduler, history |
| Storage | `/storage` | file manager for allowed roots |
| Alerts | `/alerts` | recent alert log |
| Logs | `/logs` | log view / clear / download |
| Settings | `/settings` | thresholds, defaults, channels, runtime controls |

---

## Speed page

The Speed page is a real runner, not a fake animation.

It supports:
- **official Ookla CLI** runs
- saved server selection
- local quick-pick buttons
- scheduler / auto-run
- run history with provider / target / DL / UL / ping / jitter / loss / status
- alerting on bad speed results

If `speedtest` is missing:

```bash
curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash
sudo apt-get install speedtest
```

---

## Storage page

The Storage page is designed for practical server-side file work rather than full desktop-style browsing.

Current capabilities include:
- allowed-root browsing
- uploads and chunk uploads
- downloads
- copy / move / rename / delete / trash / restore
- checksum and duplicate scan
- optional action password for destructive operations
- LAN/Tailscale-only write protection

This is intentionally conservative for safety.

---

## Notifications

Telegram and Discord are first-class features.

You can:
- save credentials from the UI
- send live test notifications
- route alerts through one or both channels
- review recent delivery results

Message formatting is different per channel so alerts stay readable.

---

## Releases and changelog

- Current release: **v1.1.0**
- First public release: **v1.0.0**
- Human-readable change history: [`CHANGELOG.md`](CHANGELOG.md)
- Release details: [`RELEASE_NOTES.md`](RELEASE_NOTES.md)
- GitHub releases: use the Releases tab for tagged snapshots and release notes

---

## Emergency Terminal (Web Console)

Systor includes a secure in-browser emergency console for diagnostics and service triage.

### Security Controls:
1. **Private Network Only**: Only accessible from private LAN / Tailscale addresses (`is_request_private`).
2. **Mandatory Admin Hash**: Requires `auth.password_hash` to be configured. Default credentials are unconditionally rejected.
3. **Per-IP Rate Limiting**: Exponential cooldown on failed attempts prevents brute-force attacks.
4. **Ephemerality**: Single-use cryptographically random tokens bound strictly to client IP with a 15-minute TTL.
5. **Accessibility**: Screen-reader friendly modal dialog (`role="dialog"`, `aria-modal="true"`) with keyboard Tab trapping and focus restoration on close.

To open the terminal, click the terminal icon `>_` in the header or invoke the drawer.

---

## Configuration Reference

Configuration is loaded from `/etc/systor/config.yaml`, `~/.config/systor/config.yaml`, or `./systor.yaml`. Any setting can be overridden via `SYSTOR_*` environment variables.

| YAML Key | Type | Default | Environment Variable | Description |
|---|---|---|---|---|
| `collector.poll_interval_sec` | int | `5` | `SYSTOR_POLL_INTERVAL` | Seconds between telemetry polling cycles |
| `collector.retention_days` | int | `7` | `SYSTOR_RETENTION_DAYS` | Days to keep raw 5-second metrics |
| `collector.rollup_after_hours` | int | `24` | - | Age in hours before averaging into 5-minute buckets |
| `collector.rollup_retention_days` | int | `90` | - | Retention for rolled-up metrics |
| `thresholds.cpu_load_1m.value` | float | `4.0` | `SYSTOR_CPU_LOAD` | Sustained 1-minute CPU load alert threshold |
| `thresholds.cpu_temp_c.value` | float | `85.0` | `SYSTOR_CPU_TEMP` | Sustained CPU temperature threshold (°C) |
| `thresholds.mem_free_mb.value` | int | `500` | `SYSTOR_MEM_FREE_MB` | Low memory alert threshold in MB |
| `thresholds.swap_used_mb.value` | int | `4096` | `SYSTOR_SWAP_USED_MB` | High swap usage alert threshold in MB |
| `thresholds.disk_used_pct.value` | int | `90` | `SYSTOR_DISK_PCT` | Disk space usage threshold percentage |
| `thresholds.cooldown_sec` | int | `600` | `SYSTOR_COOLDOWN` | Cooldown between repeated alerts for the same metric |
| `telegram.enabled` | bool | `false` | - | Enable Telegram notifications |
| `telegram.bot_token` | str | `""` | `SYSTOR_TELEGRAM_BOT_TOKEN` | Telegram Bot API token |
| `telegram.chat_id` | str | `""` | `SYSTOR_TELEGRAM_CHAT_ID` | Telegram chat ID for alert delivery |
| `discord.enabled` | bool | `false` | - | Enable Discord notifications |
| `discord.webhook_url` | str | `""` | `SYSTOR_DISCORD_WEBHOOK` | Discord incoming webhook URL |
| `auth.enabled` | bool | `false` | - | Require authentication for private dashboard |
| `auth.username` | str | `"admin"` | - | Administrator username |
| `auth.password_hash` | str | `""` | - | Werkzeug password hash for admin / terminal login |
| `apps.auto_refresh_sec` | int | `15` | `SYSTOR_APPS_REFRESH_SEC` | Process list auto-refresh frequency in seconds |
| `apps.default_limit` | int | `10` | `SYSTOR_APPS_LIMIT` | Top processes to display by default |
| `apps.docker_control` | bool | `false` | - | Allow compose project restart actions from UI |
| `web.host` | str | `"0.0.0.0"` | `SYSTOR_WEB_HOST` | Bind address (`0.0.0.0` for LAN, `127.0.0.1` for local) |
| `web.port` | int | `6677` | `SYSTOR_WEB_PORT` | HTTP listening port |
| `web.trusted_proxies` | str | `"127.0.0.1, ::1"` | - | Comma-separated trusted upstream proxy IPs/CIDRs |
| `storage_page.allowed_roots` | str | `"/mnt/tb"` | - | Colon or comma-separated permitted storage roots |
| `logging.level` | str | `"INFO"` | - | Minimum logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `logging.file` | str | `"/var/log/systor/systor.log"` | - | Log file destination |

---

## Project layout

```text
systor/
├── .github/
│   └── workflows/ci.yml
├── docs/
│   ├── index.html
│   └── landing.css
├── systemd/
│   ├── systor-collector.service
│   └── systor-web.service
├── systor/
│   ├── blueprints/
│   │   ├── apps.py
│   │   ├── auth.py
│   │   ├── common.py
│   │   ├── dashboard.py
│   │   ├── network.py
│   │   ├── settings.py
│   │   ├── speed.py
│   │   └── storage.py
│   ├── services/
│   │   ├── backup_service.py
│   │   ├── runtime_service.py
│   │   ├── security_audit_service.py
│   │   ├── smart_service.py
│   │   ├── storage_service.py
│   │   ├── systemd_service.py
│   │   └── uptime_service.py
│   ├── static/
│   │   ├── css/ (tokens.css, style.css)
│   │   ├── js/ (app.js, dialog.js)
│   │   └── img/
│   ├── templates/
│   ├── bot.py
│   ├── cli.py
│   ├── collector.py
│   ├── config.py
│   ├── metrics.py
│   ├── notifier.py
│   ├── security.py
│   ├── sensors.py
│   ├── speed.py
│   ├── storage.py
│   ├── terminal.py
│   └── web.py
├── tests/
├── tools/
│   └── design_audit.py
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── install.sh
├── uninstall.sh
├── README.md
├── RELEASE_NOTES.md
├── SECURITY.md
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
└── LICENSE
```

---

## Troubleshooting

### Dashboard not reachable on LAN

```bash
ss -ltnp | grep 6677
systemctl status systor-web
```

### Notifications not arriving
- verify credentials in Settings
- use **Save + test** from the UI
- inspect `/var/log/systor/`

### `speedtest` not found
Install the official Ookla CLI and ensure it is on `PATH`.

### Docker app data missing
The collector runs as root by default. If you run custom service users, Docker access may need adjustment.

### I changed host or port
Update config, then restart the web service:

```bash
systemctl restart systor-web
```

---

## Security notes

- keep secrets in `/etc/systor/systor.env`, not in git
- do not expose admin pages directly to the public internet
- prefer LAN / Tailscale for admin access
- if using Cloudflare tunnel, expose the read-only dashboard surface only
- limit Storage roots to the minimum you actually need

---

## GitHub Pages landing

A static marketing page lives in [`docs/`](docs/) (`index.html` + `landing.css`). Enable it once in the repo:

**Settings → Pages → Build from branch `main` → folder `/docs`**

Public URL: **https://seaxen.github.io/systor/** (after GitHub finishes the first deploy).

---

## Community & Contributing

- **Contributing Guide**: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- **Security Policy & Vulnerability Reporting**: [`SECURITY.md`](SECURITY.md)
- **Code of Conduct**: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)

---

## License

MIT.

---

## Author

**Dr. Sagar**  
GitHub: [SeaXen](https://github.com/SeaXen)  
Email: [drpelagik@gmail.com](mailto:drpelagik@gmail.com)
