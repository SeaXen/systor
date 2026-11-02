#!/usr/bin/env bash
# systor uninstall — stops services, removes files
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run with sudo: sudo ./uninstall.sh"
  exit 1
fi

echo "==> Stopping and disabling systemd services"
systemctl disable --now systor-collector systor-web 2>/dev/null || true
rm -f /etc/systemd/system/systor-collector.service
rm -f /etc/systemd/system/systor-web.service
systemctl daemon-reload 2>/dev/null || true

echo "==> Removing application files & symlinks"
rm -rf /opt/systor
rm -f /usr/local/bin/systor

# Keep /etc/systor (config) and /var/lib/systor (db) by default to prevent accidental data loss
echo "    /opt/systor                       removed"
echo "    /usr/local/bin/systor             removed"
echo "    /etc/systor                       KEPT (your config)"
echo "    /var/lib/systor                   KEPT (your historical data)"
echo "    /var/log/systor                   KEPT (logs)"
echo ""
echo "To completely purge all configuration and historical data:"
echo "    sudo rm -rf /etc/systor /var/lib/systor /var/log/systor"
echo ""
echo "If you ran Systor using Docker Compose, to remove containers and volumes:"
echo "    docker compose down -v"
echo ""
echo "Done."
