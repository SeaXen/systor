#!/usr/bin/env bash
# systor uninstall — stops services, removes files
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run with sudo: sudo ./uninstall.sh"
  exit 1
fi

USER_NAME="${SUDO_USER:-root}"

echo "==> Stopping services"
if [[ "$USER_NAME" != "root" ]]; then
  sudo -u "$USER_NAME" systemctl --user disable --now systor-collector systor-web 2>/dev/null || true
  rm -f "/home/$USER_NAME/.config/systemd/user/systor-collector.service"
  rm -f "/home/$USER_NAME/.config/systemd/user/systor-web.service"
  sudo -u "$USER_NAME" systemctl --user daemon-reload
else
  systemctl disable --now systor-collector systor-web 2>/dev/null || true
  rm -f /etc/systemd/system/systor-collector.service
  rm -f /etc/systemd/system/systor-web.service
  systemctl daemon-reload
fi

echo "==> Removing files"
rm -rf /opt/systor
# Keep /etc/systor (config) and /var/lib/systor (db) — back up manually if not wanted
echo "    /opt/systor                       removed"
echo "    /etc/systor                       KEPT (your config)"
echo "    /var/lib/systor                   KEPT (your historical data)"
echo "    /var/log/systor                   KEPT (logs)"
echo ""
echo "To remove all data: sudo rm -rf /etc/systor /var/lib/systor /var/log/systor"
echo "Done."
