<p align="center">
  <img src="docs/systor-logo.png" alt="systor" width="140">
</p>

<h1 align="center">systor</h1>

<p align="center">
  <strong>Lightweight Linux system monitor + branded web dashboard + Telegram/Discord alerts + speedtest.</strong><br>
  Built for resource-constrained machines (DietPi, RPi, home servers, VPS) where Prometheus + Grafana is overkill, and where you still want clean dashboards, real alerts that don't fire on a single spike, and an honest speed test surface.
</p>

<p align="center">
  <img alt="dashboard" src="docs/screen-dashboard.png">
</p>

---

## Why systor?

| Tool | RAM | Setup | Telemetry style | Notifications |
|---|---|---|---|---|
| Prometheus + Grafana | 250 MB+ | hours | industrial | via Alertmanager |
| Netdata | 200 MB+ | one command | charts everywhere | email/webhook |
| Glances (web) | 30 MB | one command | single screen | limited |
| **systor** | **~25 MB** | **one command** | **GitHub-dark, branded** | **Telegram + Discord, first-class** |

systor is one Flask app + one Python collector + one SQLite DB. No SPA, no Electron, no Java, no Prometheus, no Grafana, no agent protocol. Just Python + Waitress + SQLite + a small custom canvas charting layer.

## Highlights

- **Live dashboard** — CPU, temperature, memory, swap, disk I/O, CPU load, network DL/UL. Six charts, six KPI cards, top procs, recent alerts, all on one screen.
- **Sustained-threshold alerts** — won't fire on a single spike. Per-metric threshold + duration in minutes, with recovery events when the condition clears.
- **Telegram + Discord notifications** — channel-specific formatting, test buttons, cooldowns to avoid spam. Both in the same Settings page.
- **Speed page** — real official Ookla CLI runs, history, scheduler, mobile-friendly. Honest per-phase progress parsed from the live stream, not a fake staged animation.
- **Network page** — interface-aware usage tables (daily / monthly / yearly), 90-day bar chart on desktop / 30-day on mobile, per-interface filtering.
- **Apps page** — host + Docker processes ranked by CPU / RAM / network / disk, with pagination and a row-count selector.
- **Lightweight** — ~25 MB total RAM (collector ~10 MB, web ~20 MB peak). `python3.11` only. No build step. Single CSS file, no JS framework.
- **Self-restart** — collector + web both systemd-managed. Restart buttons in the UI. Live SIGHUP config reload.
- **Standalone** — no Hermes, no Docker, no external services. Drop it on any Linux box.

## Quick start

```bash
git clone https://github.com/SeaXen/systor.git
cd systor
sudo ./install.sh
```

Open <http://127.0.0.1:6677> in your browser.

For LAN access, open `http://<host-ip>:6677` (e.g. `http://192.168.1.10:6677`). The web service binds to `0.0.0.0:6677` by default.

The installer:
- Copies the app to `/opt/systor`
- Writes `/etc/systor/config.yaml` with sensible defaults
- Writes `/etc/systor/systor.env` (empty — fill in tokens for notifications)
- Installs `Flask` + `waitress` via `pip install -r requirements.txt`
- Installs + starts `systor-collector` and `systor-web` systemd services
- Creates `/var/log/systor/`, `/var/lib/systor/`
- Reloads systemd daemon and enables services on boot

Uninstall cleanly:
```bash
sudo systemctl disable --now systor-web systor-collector
sudo rm -rf /opt/systor /etc/systor /var/lib/systor /var/log/systor
sudo rm /etc/systemd/system/systor-web.service /etc/systemd/system/systor-collector.service
sudo systemctl daemon-reload
```

## Pages

| Page | URL | What it does |
|---|---|---|
| Dashboard | `/` | KPI strip + 6 charts + top procs + recent alerts |
| Apps | `/apps` | Host + Docker processes ranked by CPU/RAM/Net/Disk |
| Network | `/network` | Live per-interface traffic + daily/monthly/yearly usage tables |
| Speed | `/speed` | Real speedtest runner + scheduler + history |
| Alerts | `/alerts` | Recent alert log with severity filtering |
| Logs | `/logs` | Live log tail + Clear / Download buttons |
| Settings | `/settings` | Thresholds, Telegram, Discord, defaults |

## Speed page

Two real runners:

1. **WAN — official Ookla CLI** — `speedtest` (Speedtest by Ookla). Real-time phase output is parsed from the live stream, so you see actual `ping → jitter → download → upload` progress instead of a fake animation. **Stop** kills the underlying process. **Scheduler** runs it automatically every N minutes and alerts when DL/UL drop below your thresholds.
2. **LAN — local iperf3 helper** — `iperf3 -s` is launched on the host so you can run `iperf3 -c <host-ip> -t 15` from any laptop on the same network and measure real LAN throughput.

The runner supports regional quick-picks (Dhaka, Singapore, Mumbai, Delhi, Tokyo, US East/West, EU, LATAM) and any saved custom Ookla server ID. Each completed run is logged to SQLite history with provider, server ID, server name, DL/UL Mbps, ping, jitter, packet loss, and status.

If you want to skip running your own LAN test from a separate laptop, you can also point the Ookla runner at any nearby LAN server IP you control.

## Notifications

Telegram and Discord are first-class in Settings. Each channel has:

- Bot token / webhook URL field (never displayed back, masked after save)
- Per-metric routing (only send what you care about)
- Save + test button
- Channel-specific formatting:
  - **Telegram** — short, emoji-led, bold subject + newline-separated values (HTML parse mode)
  - **Discord** — markdown-flavored, slightly richer block layout
- Test message includes current host + live values, not a static template

Cooldowns are per (channel, metric) so a single bad condition doesn't spam you every minute.

## Configuration

The main config lives at `/etc/systor/config.yaml` (written by the installer on first run). Edit it directly or use the **Settings** page in the UI; both paths hot-reload via SIGHUP to the collector.

```yaml
host: 0.0.0.0
port: 6677
refresh_sec: 5
chart_refresh_sec: 5

alerts:
  cpu_load: { enabled: true, threshold: 4.0, duration_min: 2 }
  cpu_temp: { enabled: true, threshold: 85,  duration_min: 3 }
  memory:   { enabled: true, threshold: 500, duration_min: 2 }
  swap:     { enabled: true, threshold: 4096, duration_min: 2 }
  disk:     { enabled: true, threshold: 90,  duration_min: 5 }

retention_days: 7

speedtest:
  enabled: false
  interval_min: 180
  server_id: ""
  min_dl_mbps: 50
  min_ul_mbps: 20
```

Secrets (Telegram bot token, Discord webhook URL) live in `/etc/systor/systor.env` so they don't end up in the YAML diff:

```bash
SYSTOR_TELEGRAM_BOT_TOKEN=...
SYSTOR_TELEGRAM_CHAT_ID=...
SYSTOR_DISCORD_WEBHOOK_URL=...
```

## CLI

```bash
systor status               # service status, last sample, current thresholds
systor setup telegram       # interactive Telegram setup wizard
systor setup discord        # interactive Discord setup wizard
systor test                 # send a test notification to all configured channels
systor config show          # show effective config
systor config reload        # SIGHUP the collector
systor retention show       # show retention + DB size
systor retention prune      # run retention manually
```

## API

The dashboard is the only consumer of the JSON API, but it's there if you want to integrate with anything else. All endpoints live under `/api/`.

| Endpoint | Returns |
|---|---|
| `GET /api/snapshot` | One-shot live snapshot (CPU, mem, swap, disk, net, db stats) — supports `If-None-Match` for `304` |
| `GET /api/runtime` | Collector + web RSS/CPU, uptime, host storage, cloudflared/tailscaled status |
| `GET /api/series?metric=cpu_pct&hours=6` | Time-bucketed series for any metric |
| `GET /api/network-series?hours=6` | RX + TX time series |
| `GET /api/network-usage?iface=eth0&days=10` | Per-interface daily totals |
| `GET /api/network-interfaces` | List of interfaces with virtual/physical flag |
| `GET /api/apps?scope=all&sort=cpu&limit=24` | Host + Docker apps ranked by metric |
| `GET /api/alerts?limit=50` | Recent alerts |
| `GET /api/notifications` | Recent notification deliveries (success / failure) |
| `GET /api/access` | Per-access log (last N hits) |
| `GET /api/speed/status` | Speedtest runner status (idle / running / stopped / complete) |
| `POST /api/speed/live/start` | Start a live run (optional `server_id`) |
| `POST /api/speed/live/stop` | Stop the active run |
| `GET /api/speed/live/status` | Live status polled by the front-end (phase, ping, jitter, dl, ul, etc.) |
| `GET /api/speedtests?provider=&type=&page=1` | History with provider/type/page filters |
| `GET /api/top-processes?by=cpu&n=10` | Top N processes by CPU or memory |
| `POST /api/restart-collector` | Restart the collector (live UI button) |
| `POST /api/restart-web` | Restart the web service (live UI button) |
| `GET /api/logs?lines=200` | Last N log lines |
| `POST /api/logs/clear` | Truncate the log file |
| `GET /logs/raw` | Download the full log file |
| `GET /health` | `{"ok":true,"ts":...}` |

## Performance budget

Measured on a DietPi VM with the dashboard open in one tab:

| Component | Idle | Active |
|---|---|---|
| Collector RSS | ~10 MB | ~12 MB |
| Web RSS | ~20 MB | ~25 MB |
| SQLite DB (7 days raw) | ~14 MB | grows ~2 MB / day |
| First-paint | < 500 ms | < 500 ms |
| `/api/snapshot` | 8–15 ms | 8–15 ms (304 on identical) |
| `/api/apps` (warm cache) | 70 ms | 70 ms |
| `/api/apps` (cold cache, first call) | 1.6 s | first call only |
| Static assets (CSS, logo) | 5 ms (cache) | 5 ms (cache) |

The collector samples every 5 seconds by default and writes one row per metric per cycle. Default retention is 7 days of raw samples.

## Resource & accessibility

- **Accessibility** — every page has a skip link, focus-visible outlines on every button/link, ARIA roles on the main navigation, and a one-line `role="note"` help string at the top of each page.
- **Caching** — `/api/snapshot` returns an ETag and supports `If-None-Match` for instant 304s. Static assets are served with `Cache-Control: public, max-age=300`.
- **Throttling** — every fetch on the front-end is guarded by an `inflight` flag so a slow tick never stacks a parallel duplicate request.
- **No external JS** — no jQuery, no Chart.js, no framework. Single small canvas drawer inlined per page.

## Architecture

```
systor/
├── systor/
│   ├── __init__.py            # package init, version
│   ├── __main__.py            # python -m systor entry
│   ├── cli.py                 # `systor status`, `setup telegram`, etc.
│   ├── collector.py           # background poller → SQLite
│   ├── config.py              # config + secrets loader
│   ├── metrics.py             # psutil / disk / network / docker collectors
│   ├── notifier.py            # Telegram + Discord senders
│   ├── speed.py               # Ookla CLI wrapper + iperf3 helper
│   ├── storage.py             # SQLite schema + retention
│   ├── web.py                 # Flask app, all routes
│   ├── data/                  # SQLite DB lives here by default
│   ├── static/
│   │   ├── css/style.css
│   │   ├── css/style.min.css
│   │   └── img/
│   │       ├── systor-logo.png
│   │       ├── systor-logo-header.png
│   │       └── favicon.png
│   └── templates/
│       ├── base.html
│       ├── dashboard.html
│       ├── apps.html
│       ├── network.html
│       ├── speed.html
│       ├── alerts.html
│       ├── logs.html
│       └── settings.html
├── install.sh                 # installer (systemd + dirs + pip)
├── requirements.txt           # Flask, waitress
├── setup.py
└── README.md
```

Two long-running processes:

| Service | Role | Restart policy |
|---|---|---|
| `systor-collector` | Samples metrics every 5s → SQLite | `Restart=always` |
| `systor-web` | Waitress-served Flask on `:6677` | `Restart=always` |

Both reload config on SIGHUP without dropping samples.

## Troubleshooting

**Dashboard unreachable on the LAN**
The web service binds to `0.0.0.0` by default. If you can't reach it from another device, check:
```bash
sudo ss -ltnp | grep 6677
sudo ufw status                # or `sudo iptables -L` if you use nftables
```

**No notifications arriving**
1. Open Settings → Telegram / Discord → **Save + test** with your bot token / webhook URL. If the test fails, the credentials are wrong.
2. The token is masked after save. Re-enter the real token if you want to retest.
3. Check `/var/log/systor/systor.log` for delivery errors and cooldowns.

**`speedtest` not found**
The Ookla CLI is not bundled. Install it once:
```bash
# Debian/Ubuntu/DietPi
curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash
sudo apt-get install speedtest
```
systor auto-detects `speedtest` on PATH.

**`docker stats` permission denied**
The collector runs as root, so this should just work. If you run as a non-root user, add them to the `docker` group and re-login.

**Charts frozen**
The chart refresh interval is independent from the data sampling interval. If the chart timer is 5s but the collector writes every 30s, the chart redraws correctly on each tick but values won't change between collector samples. Set both to 5s in Settings.

**Disk usage never dropping**
The retention pass deletes old raw rows + rollups based on `retention_days` in config. It runs once per hour by default. To force it now:
```bash
systor retention prune
```

**I want to back up the DB**
```bash
sudo systemctl stop systor-web systor-collector
sudo cp /var/lib/systor/systor.db /var/lib/systor/systor.db.bak
sudo systemctl start systor-collector systor-web
```

**I changed the port**
Edit `host` and `port` in `/etc/systor/config.yaml`, then:
```bash
sudo systemctl restart systor-web
```

## License

MIT. Use it, fork it, ship it.

## Author

Dr. Sagar — [drpelagik@gmail.com](mailto:drpelagik@gmail.com) — [github.com/SeaXen](https://github.com/SeaXen)
