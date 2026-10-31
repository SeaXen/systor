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
cp "$SRC_DIR/README.md" "$DST_DIR/" 2>/dev/null || true

echo "==> Setting up isolated Python virtual environment in $DST_DIR/venv"
if ! python3 -m venv "$DST_DIR/venv"; then
  echo "  (info) python3 -m venv failed. Attempting to install python3-venv..."
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq python3-venv python3-pip || true
  fi
  python3 -m venv "$DST_DIR/venv" || {
    echo "ERROR: Failed to create virtual environment. Please install python3-venv."
    exit 1
  }
fi

echo "==> Installing Python dependencies into virtual environment"
"$DST_DIR/venv/bin/pip" install --upgrade pip --quiet 2>/dev/null || true
"$DST_DIR/venv/bin/pip" install --quiet -r "$DST_DIR/requirements.txt"
"$DST_DIR/venv/bin/pip" install --quiet -e "$DST_DIR" 2>/dev/null || "$DST_DIR/venv/bin/pip" install --quiet "$DST_DIR"

echo "==> Creating CLI symlink in /usr/local/bin/systor"
ln -sf "$DST_DIR/venv/bin/systor" /usr/local/bin/systor

echo "==> Writing default config to $CONF_DIR/config.yaml"
if [[ ! -f "$CONF_DIR/config.yaml" ]]; then
  "$DST_DIR/venv/bin/python3" -c "
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
