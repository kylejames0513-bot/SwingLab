#!/usr/bin/env bash
# CaddieInsight server setup for a fresh Ubuntu 24.04 VM.
#
# Run as root (cloud web consoles log you in as root):
#
#   curl -fsSL https://raw.githubusercontent.com/kylejames0513-bot/SwingLab/main/deploy/server-setup.sh | bash
#
# When it finishes it prints the URL to open. CaddieInsight runs as a systemd
# service ("swinglab"), restarts on crash and on reboot.
#
# Accounts are enabled in the shipped config. Set SWINGLAB_SECRET before
# sharing a production instance so signed login sessions survive restarts.

set -euo pipefail

REPO_URL="https://github.com/kylejames0513-bot/SwingLab.git"
INSTALL_DIR=/opt/swinglab
PORT=80

echo "==> Installing system packages (ffmpeg, fonts, GL libraries, python)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ffmpeg fonts-dejavu-core libgles2 libegl1 libgl1 \
    python3-venv python3-pip git

PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
case "$PYVER" in
  3.1[1-9]|3.[2-9]*) ;;
  *) echo "ERROR: Python 3.11+ required, found $PYVER. Use Ubuntu 24.04 or newer."; exit 1 ;;
esac

echo "==> Fetching CaddieInsight..."
if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" pull --ff-only
else
    git clone -q "$REPO_URL" "$INSTALL_DIR"
fi

echo "==> Installing CaddieInsight into a virtualenv..."
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install -q -e "$INSTALL_DIR[web]"

echo "==> Creating the swinglab systemd service..."
cat > /etc/systemd/system/swinglab.service <<EOF
[Unit]
Description=CaddieInsight swing analysis web app
After=network.target

[Service]
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/swinglab serve --host 0.0.0.0 --port $PORT --sessions-dir $INSTALL_DIR/sessions
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now swinglab
sleep 2
systemctl --no-pager --lines=0 status swinglab || true

# -4 forces IPv4: an IPv6 address here would print an unusable URL (needs
# brackets in browsers, and many home networks can't reach IPv6 at all)
PUBLIC_IP=$(curl -4 -fsS --max-time 5 ifconfig.me || hostname -I | awk '{print $1}')
echo
echo "============================================================"
echo " CaddieInsight is running."
echo " Open:  http://$PUBLIC_IP/"
echo
echo " Logs:     journalctl -u swinglab -f"
echo " Restart:  systemctl restart swinglab"
echo " Update:   cd $INSTALL_DIR && git pull && systemctl restart swinglab"
echo "============================================================"
