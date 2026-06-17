# systor

**Lightweight Linux system monitor with a web dashboard, sustained-threshold alerts, and Telegram / Discord notifications.**

Built for resource-constrained machines where Prometheus + Grafana is overkill, and where you still want clean dashboards and real alerts that don't fire on a single spike.

```

systor — system monitor

~15 MB RAM total. No SPA. No Electron. No Java. Just Python + Flask + SQLite.
```

## Features

- **Live metrics** — CPU load (1/5/15m), CPU temperature, memory, swap, disk, network
- **Sustained-threshold alerts** — won't fire on a single spike; set per-metric threshold + duration in **minutes**
- **Telegram + Discord notifications** — with cooldowns to avoid alert spam
- **Web dashboard on port 6677** — GitHub-dark style, live charts, no external JS deps, LAN-accessible
- **Hot-reload config** — change thresholds in the UI, collector picks them up within one poll cycle
- **SQLite storage** — 7 days raw + 90 days aggregated by default
- **~15 MB RAM** — collector ~10 MB, web server ~20 MB peak
- **No cron** — runs as a systemd user service, auto-restarts on crash
- **Standalone** — no Hermes, no Docker, no external services
- **CLI** — `systor status`, `systor setup telegram`, `systor test`

## Quick start

```bash
git clone https://github.com/SeaXen/systor
cd systor
sudo ./install.sh
```

Open <http://127.0.0.1:6677> in your browser. For LAN access, use the host's IP, e.g.
<http://192.168.1.10:6677>.

The install script:
- Copies the app to `/opt/systor`
- Writes `/etc/systor/config.yaml` with sensible defaults
- Creates `/etc/systor/systor.env` (empty — fill in tokens for notifications)
- Installs `Flask` + `waitress` via pip
- Installs + starts the two systemd services (`systor-collector`, `systor-web`)

## Alert thresholds

Each metric has three knobs: **enable** (checkbox), **threshold value**, and **duration in minutes**.
The alert only fires when the value stays above (or below) the threshold for the configured number of minutes.

| Metric              | Default      | When it fires                              |
|---------------------|--------------|--------------------------------------------|
| CPU load (1m avg)   | > 4.0 for 2m | 1-minute load average stays above 4.0      |
| CPU temperature     | > 85°C for 3m| CPU temp stays above 85 °C                 |
| Memory free         | < 500 MB for 2m | available memory drops below 500 MB     |
| Swap used           | > 4096 MB for 2m | swap usage grows above 4 GB            |
| Disk used           | > 90% for 5m | any mount fills above 90%                  |

Example: "alert me if temperature > 85 °C for 3 minutes" → set `CPU temperature = 85 °C, 3 min` in
the **Settings** page and click **Save & apply**. The collector picks up the change within
`poll_interval_sec` (default 30 s) — no restart needed for threshold changes.

## Configure notifications

Edit `/etc/systor/systor.env` (mode 600):

```bash
# Telegram
SYSTOR_TELEGRAM_BOT_TOKEN=123456789:ABC-DEF...
SYSTOR_TELEGRAM_CHAT_ID=123456789

# Discord (webhook URL)
SYSTOR_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
```

Or use the CLI:

```bash
sudo -u $USER systor setup telegram --token "..." --chat-id "..." --test
sudo -u $USER systor setup discord --webhook "https://..." --test
```

Or use the web UI: <http://127.0.0.1:6677/settings> — the **Send test** button fires a
notification immediately so you can verify the channel.

## CLI

```bash
systor status                  # one-shot snapshot
systor setup telegram          # interactive / scripted config
systor setup discord
systor test                    # send a test notification
systor test --channel telegram # just one channel
systor serve collector         # run collector in foreground (debug)
systor serve web               # run web in foreground
systor config show             # dump current config as JSON
```

## Architecture

```
   /etc/systor/
   ├── config.yaml          ← main config (thresholds, ports, channels)
   └── systor.env           ← secrets (bot tokens, webhooks)

   /opt/systor/
   └── systor/              ← python package

   /var/lib/systor/
   └── systor.db            ← SQLite (samples, rollups, alerts, notifications)

   /var/log/systor/
   ├── systor.log
   ├── collector.log
   └── web.log

   systemd user services:
   ├── systor-collector.service   ← polls every 30s
   └── systor-web.service         ← Flask on port 6677
```

```
   ┌─────────────────────┐
   │ systor-collector     │  poll every 30s
   │  (Python daemon)     │  read /proc, /sys, df
   │                      │  evaluate sustained thresholds
   │                      │  insert into SQLite
   │                      │  send via Telegram / Discord
   └──────┬───────────────┘
          │ writes
          ▼
   ┌─────────────────────┐         ┌─────────────────────┐
   │ systor.db (SQLite)  │  ◄──── │ systor-web           │
   └─────────────────────┘  reads  │  (Flask + waitress)  │
                                  │  port 6677           │
                                  │  JSON API + dashboard │
                                  └─────────────────────┘
```

## Configuration

Edit `/etc/systor/config.yaml` (or use the web UI):

```yaml
collector:
  poll_interval_sec: 30
  retention_days: 7
  rollup_after_hours: 24
  rollup_retention_days: 90

thresholds:
  cpu_load_1m: 4.0          # alert if load avg > 4
  cpu_temp_c: 80.0          # alert if CPU temp > 80°C
  mem_free_mb: 500          # alert if available memory < 500 MB
  swap_used_mb: 4096        # alert if swap used > 4 GB
  disk_used_pct: 90         # alert if any mount > 90% full
  sustained_samples_cpu: 4  # 4 samples × 30s = 2 min
  sustained_samples_temp: 4
  sustained_samples_mem: 4
  sustained_samples_swap: 4
  sustained_samples_disk: 1
  cooldown_sec: 600         # 10 min between repeated alerts

telegram:
  enabled: false
  bot_token: ""             # or env SYSTOR_TELEGRAM_BOT_TOKEN
  chat_id: ""               # or env SYSTOR_TELEGRAM_CHAT_ID

discord:
  enabled: false
  webhook_url: ""

web:
  host: 127.0.0.1
  port: 6677

logging:
  level: INFO
  file: /var/log/systor/systor.log
```

All thresholds can also be overridden with environment variables (`SYSTOR_POLL_INTERVAL`, `SYSTOR_CPU_LOAD`, etc.).

## Resource cost

| Component | RSS at idle | CPU at idle |
|---|---|---|
| systor-collector | ~10 MB | < 0.5% |
| systor-web (Flask + waitress) | ~20 MB | < 0.5% |
| SQLite database (7 days data) | ~5-20 MB | — |
| **Total** | **~30-50 MB** | **< 1%** |

No JavaScript framework, no bundler, no Webpack. The web dashboard is vanilla HTML + CSS + JS, ~15 KB total. The charts are drawn in a `<canvas>` directly, no Chart.js dependency.

## Project structure

```
systor/
├── systor/                  ← python package
│   ├── __init__.py
│   ├── __main__.py           ← python -m systor
│   ├── config.py             ← YAML + env config
│   ├── metrics.py            ← /proc, /sys, df readers
│   ├── storage.py            ← SQLite layer
│   ├── notifier.py           ← Telegram + Discord
│   ├── collector.py          ← the polling daemon
│   ├── web.py                ← Flask dashboard
│   ├── cli.py                ← systor command
│   ├── templates/            ← Jinja2 HTML
│   └── static/               ← CSS + JS
├── systemd/                  ← service unit files
├── install.sh
├── uninstall.sh
├── setup.py                  ← pip install .
├── requirements.txt
├── LICENSE
├── README.md
└── .gitignore
```

## Tested on

- Debian 12, Debian 13
- Ubuntu 22.04, 24.04
- DietPi
- Raspberry Pi OS (Bookworm)

Requires:
- Linux kernel ≥ 3.10 (for `/sys/class/thermal`)
- Python ≥ 3.9
- systemd (any modern distro)
- `df` (from coreutils)
- `bc` (optional, for some metrics)

## Privacy

`/var/lib/systor/systor.db` stores:
- System metrics (CPU, memory, disk, network) — no PII
- Timestamps
- Alert messages

It does NOT store:
- Process names (yet)
- Network destinations
- User data
- File contents

## License

MIT — see [LICENSE](LICENSE).

## Author

Dr. Sagar (GitHub: [@SeaXen](https://github.com/SeaXen))
