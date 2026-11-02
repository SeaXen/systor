# Release Notes: Systor v1.0.0

🎉 **Systor v1.0.0** is officially released! 

Systor is a lightweight Linux server and homelab monitoring system featuring a responsive web dashboard, persistent sustained-threshold alerts, SMART disk insights, speedtesting, and one-click disaster recovery.

---

### Highlights in v1.0.0

#### 🎨 ExamForge Design System & 3-Way Theme Switcher
- **Warm Dark (`warm-dark`)**: Deep `#0b0b0b` neutral slate background, `#151515` elevated panels, and ExamForge signature teal accents (`#1d9e75`).
- **Paper Light (`light`)**: Clean `#f9f9f7` warm paper background with high-contrast text and crisp card borders.
- **Classic Dark (`classic-dark`)**: Preserved original deep GitHub navy (`#0f1117`) and blue accents (`#58a6ff`).
- **Instant Switching & Zero-Flash**: Seamless header toggle (`🌙`/`☀`/`🌌`) with zero-flash `<head>` preloading and live Chart.js canvas color adaptation.

#### 📱 Native Progressive Web App (PWA)
- Installable directly to Home Screen on iOS, Android, macOS, and Windows.
- Full `manifest.json` and high-resolution icons (192x192 and 512x512 maskable).
- Service Worker (`sw.js`) with Cache-First static shell loading and Network-Only real-time telemetry.

#### 🐳 Production Docker & Docker Compose
- Multi-architecture container (`linux/amd64` and `linux/arm64`) on GitHub Container Registry (`ghcr.io/seaxen/systor:latest`).
- Optimized `docker-compose.yml` with host networking, host PID namespace, and read-only host telemetry mounts (`/proc`, `/sys`, `/dev`, `/var/run/docker.sock`).
- Multi-stage slim image with `smartmontools`, `curl`, and `tini` init supervision.

#### 🛡️ Hardware Health, Telemetry & Management
- **SMART Drive Health**: NVMe wear levels, temperature, and reallocated sector tracking.
- **Systemd & Docker Process Control**: Service manager with start/stop/restart and audit logging.
- **Network Uptime Probes & SSL Tracker**: Live HTTP/TCP endpoint uptime and certificate expiry countdowns.
- **Speedtest Runner**: Scheduled Ookla tests with bandwidth alerting.
- **One-Click Disaster Recovery**: Full configuration archive export and restore.

---

### Installation Quick-Start

#### Option A: Automated 1-Line Remote Installer (Bare-Metal / VPS / Raspberry Pi)
```bash
curl -sSL https://raw.githubusercontent.com/SeaXen/systor/main/install.sh | sudo bash
```

#### Option B: Docker Compose
```yaml
services:
  systor:
    image: ghcr.io/seaxen/systor:latest
    container_name: systor
    restart: unless-stopped
    pid: host
    network_mode: host
    volumes:
      - systor_config:/etc/systor
      - systor_data:/var/lib/systor
      - systor_log:/var/log/systor
      - /proc:/proc:ro
      - /sys:/sys:ro
      - /dev:/dev:ro
      - /etc/os-release:/etc/os-release:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro

volumes:
  systor_config:
  systor_data:
  systor_log:
```
Run with:
```bash
docker compose up -d
```

#### Option C: Python PIP
```bash
git clone https://github.com/SeaXen/systor.git
cd systor
pip install .
systor serve all
```

---

### Verification
- **74 Automated Tests Passed** (Unit & Integration)
- **51 Playwright E2E Multi-Viewport Checks Passed** (Zero layout overflow down to 390px mobile)
- **Web Interface Guidelines & WCAG Accessibility Compliant**
