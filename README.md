<p align="center">
  <img src="docs/systor-logo.png" alt="systor" width="120">
</p>

<h1 align="center">systor</h1>

<p align="center">
  Lightweight Linux system monitor with a branded web dashboard, sustained-threshold alerts, storage browser, process views, and Telegram/Discord notifications.
</p>

<p align="center">
  <a href="https://github.com/SeaXen/systor/releases/tag/v1.0.0"><img alt="release" src="https://img.shields.io/github/v/release/SeaXen/systor?display_name=tag&sort=semver&color=1f6feb"></a>
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
- **Service Uptime & Blackbox Monitoring** — Probes external or internal HTTP, HTTPS, TCP, Ping, and DNS endpoints with latency history
- **Systemd Service Manager & Audit Logging** — Inspect, restart, and monitor host system services with security audit logs
- **SSL / TLS Certificate Tracker** — Live certificate chain validation, issuer verification, and expiration countdowns
- **Security Updates & Kernel Reboot Indicators** — Instant alerts when APT/DNF pending security updates or `/var/run/reboot-required` are detected
- **Prometheus Metric Exporter (`/metrics`)** — Native OpenMetrics format endpoint for optional external Prometheus/Grafana scraping
- **One-Click Disaster Recovery** — Full JSON backup export and zero-downtime restore for configurations, uptime targets, and audit histories
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

- First public release: **v0.1.0**
- Human-readable change history: [`CHANGELOG.md`](CHANGELOG.md)
- GitHub releases: use the Releases tab for tagged snapshots and release notes

---

## API

The front-end consumes JSON endpoints under `/api/`.

Common endpoints:
- `GET /api/snapshot`
- `GET /api/runtime`
- `GET /api/series?metric=cpu_pct&hours=6`
- `GET /api/network-series?hours=6`
- `GET /api/apps?scope=all&sort=cpu&limit=24`
- `GET /api/top-processes?by=cpu&n=10`
- `GET /api/public-dashboard?hours=6`
- `GET /api/alerts?limit=50`
- `POST /api/restart-collector`
- `POST /api/restart-web`
- `GET /health`

Public mode intentionally exposes only a small read-only subset.

---

## Project layout

```text
systor/
├── systor/
│   ├── cli.py
│   ├── collector.py
│   ├── config.py
│   ├── metrics.py
│   ├── notifier.py
│   ├── speed.py
│   ├── storage.py
│   ├── web.py
│   ├── static/
│   └── templates/
├── systemd/
├── docs/
├── install.sh
├── requirements.txt
├── setup.py
└── README.md
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

## License

MIT.

---

## Author

**Dr. Sagar**  
GitHub: [SeaXen](https://github.com/SeaXen)  
Email: [drpelagik@gmail.com](mailto:drpelagik@gmail.com)
