#!/usr/bin/env bash
# systor install script (v1.0.0)
# Supports both:
#   1. Local execution: cd systor && sudo ./install.sh
#   2. Headless remote: curl -sSL https://raw.githubusercontent.com/SeaXen/systor/main/install.sh | sudo bash
#
# Actions:
# - Copies / installs app into /opt/systor
# - Sets up isolated Python virtual environment (/opt/systor/venv)
# - Writes default config to /etc/systor/config.yaml
# - Installs smartmontools for Drive SMART health monitoring
# - Installs & enables systemd services for boot autostart
# - Creates CLI symlink /usr/local/bin/systor
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: Please run with sudo or as root: sudo ./install.sh"
  exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}" 2>/dev/null || pwd)" && pwd)"
TEMP_DIR=""

cleanup() {
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf "$TEMP_DIR"
  fi
}
trap cleanup EXIT

# Auto-detect remote execution (e.g. curl ... | bash) vs local repository
if [[ ! -d "$SRC_DIR/systor" || ! -f "$SRC_DIR/pyproject.toml" ]]; then
  echo "==> Remote execution detected. Bootstrapping Systor from GitHub (SeaXen/systor)..."
  TEMP_DIR="$(mktemp -d /tmp/systor-bootstrap-XXXXXX)"

  if command -v git >/dev/null 2>&1; then
    echo "  Cloning repository via git..."
    git clone --depth 1 https://github.com/SeaXen/systor.git "$TEMP_DIR/systor-repo"
    SRC_DIR="$TEMP_DIR/systor-repo"
  elif command -v curl >/dev/null 2>&1 && command -v tar >/dev/null 2>&1; then
    echo "  Downloading source tarball via curl..."
    curl -sSL https://github.com/SeaXen/systor/archive/refs/heads/main.tar.gz | tar -xz -C "$TEMP_DIR"
    SRC_DIR="$TEMP_DIR/systor-main"
  else
    echo "ERROR: Neither 'git' nor 'curl' + 'tar' is available. Please install git or curl."
    exit 1
  fi
fi

DST_DIR="/opt/systor"
CONF_DIR="/etc/systor"
LOG_DIR="/var/log/systor"
DATA_DIR="/var/lib/systor"

echo "==> Creating directories"
mkdir -p "$DST_DIR" "$CONF_DIR" "$LOG_DIR" "$DATA_DIR"

echo "==> Ensuring required system dependencies"
if command -v apt-get >/dev/null 2>&1; then
  MISSING_PKGS=""
  command -v python3 >/dev/null 2>&1 || MISSING_PKGS="$MISSING_PKGS python3"
  python3 -m venv --help >/dev/null 2>&1 || MISSING_PKGS="$MISSING_PKGS python3-venv"
  command -v pip3 >/dev/null 2>&1 || MISSING_PKGS="$MISSING_PKGS python3-pip"
  command -v smartctl >/dev/null 2>&1 || MISSING_PKGS="$MISSING_PKGS smartmontools"
  command -v curl >/dev/null 2>&1 || MISSING_PKGS="$MISSING_PKGS curl"
  if [[ -n "$MISSING_PKGS" ]]; then
    echo "  Installing system packages:$MISSING_PKGS"
    apt-get update -qq && apt-get install -y -qq $MISSING_PKGS || true
  fi
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y -q python3 python3-pip smartmontools curl || true
elif command -v pacman >/dev/null 2>&1; then
  pacman -Sy --noconfirm python python-pip smartmontools curl || true
fi

echo "==> Copying app to $DST_DIR"
cp -r "$SRC_DIR/systor" "$DST_DIR/"
cp "$SRC_DIR/pyproject.toml" "$DST_DIR/" 2>/dev/null || true
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

echo "==> Writing env file $CONF_DIR/systor.env"
if [[ ! -f "$CONF_DIR/systor.env" ]]; then
  cat > "$CONF_DIR/systor.env" <<'EOF'
# systor environment file (v1.0.0)
# Uncomment and fill in to configure notifications & overrides:
# SYSTOR_TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
# SYSTOR_TELEGRAM_CHAT_ID=123456789
# SYSTOR_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
# SYSTOR_WEB_PORT=6677
# SYSTOR_WEB_HOST=0.0.0.0
EOF
  chmod 600 "$CONF_DIR/systor.env"
fi

echo "==> Installing systemd services"
if [[ -d "$SRC_DIR/systemd" ]]; then
  cp "$SRC_DIR/systemd/systor-collector.service" /etc/systemd/system/
  cp "$SRC_DIR/systemd/systor-web.service"      /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now systor-collector systor-web
  echo "  Installed + enabled system services for boot autostart"
else
  echo "  WARNING: systemd unit directory not found at $SRC_DIR/systemd"
fi

echo ""
echo "=========================================================="
echo "  ✓ Systor v1.0.0 installed successfully!"
echo "=========================================================="
echo "  Web dashboard: http://127.0.0.1:6677"
echo "  Configuration: $CONF_DIR/config.yaml"
echo "  Logs:          $LOG_DIR/{collector,web}.log"
echo "  Database:      $DATA_DIR/systor.db"
echo ""
echo "Quick checks:"
echo "  systor status"
echo "  systor serve --help"
echo "  systemctl status systor-collector"
echo "  systemctl status systor-web"
echo "=========================================================="
