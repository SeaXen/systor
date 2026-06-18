#!/usr/bin/env bash
# systor install script
# - Copies app to /opt/systor
# - Writes default config to /etc/systor/config.yaml
# - Installs Python deps system-wide (or in venv)
# - Installs + enables systemd services
# - Creates log + data directories
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run with sudo: sudo ./install.sh"
  exit 1
fi

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DST_DIR="/opt/systor"
CONF_DIR="/etc/systor"
LOG_DIR="/var/log/systor"
DATA_DIR="/var/lib/systor"

echo "==> Creating directories"
mkdir -p "$DST_DIR" "$CONF_DIR" "$LOG_DIR" "$DATA_DIR"

echo "==> Copying app to $DST_DIR"
cp -r "$SRC_DIR/systor" "$DST_DIR/"
cp "$SRC_DIR/setup.py" "$DST_DIR/" 2>/dev/null || true
cp "$SRC_DIR/requirements.txt" "$DST_DIR/" 2>/dev/null || true

echo "==> Writing default config to $CONF_DIR/config.yaml"
if [[ ! -f "$CONF_DIR/config.yaml" ]]; then
  python3 -c "
import sys; sys.path.insert(0, '$DST_DIR')
from systor.config import DEFAULT_CONFIG, save_config
save_config(DEFAULT_CONFIG)
print('  config written')
"
fi

echo "==> Writing env file $CONF_DIR/systor.env (empty — fill in tokens if you want)"
if [[ ! -f "$CONF_DIR/systor.env" ]]; then
  cat > "$CONF_DIR/systor.env" <<'EOF'
# systor environment file
# Uncomment and fill in to enable notifications + custom paths
# SYSTOR_TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
# SYSTOR_TELEGRAM_CHAT_ID=123456789
# SYSTOR_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
# SYSTOR_WEB_PORT=6677
# SYSTOR_WEB_HOST=127.0.0.1
EOF
  chmod 600 "$CONF_DIR/systor.env"
fi

echo "==> Installing Python dependencies"
if command -v pip3 >/dev/null 2>&1; then
  pip3 install --quiet --break-system-packages -r "$DST_DIR/requirements.txt" 2>/dev/null \
    || pip3 install --quiet -r "$DST_DIR/requirements.txt" 2>/dev/null \
    || echo "  (warn) pip install failed — you may need to install Flask + waitress manually"
else
  echo "  pip3 not found — please install Flask + waitress manually"
fi

echo "==> Installing systemd services"
# Always install as system services so boot-autostart works on a fresh device
# with just: sudo ./install.sh
cp "$SRC_DIR/systemd/systor-collector.service" /etc/systemd/system/
cp "$SRC_DIR/systemd/systor-web.service"      /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now systor-collector systor-web
echo "  Installed + enabled system services for boot autostart"

echo ""
echo "==> Done!"
echo "    Web dashboard: http://127.0.0.1:6677"
echo "    Logs:          $LOG_DIR/{collector,web}.log"
echo "    Config:        $CONF_DIR/config.yaml"
echo ""
echo "Quick checks:"
echo "    systor status"
echo "    systemctl status systor-collector"
echo "    systemctl status systor-web"
