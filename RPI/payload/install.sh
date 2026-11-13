#!/bin/bash
# First-boot installer. Launched (detached) by cloud-init runcmd from user-data.
# Runs as root on the Pi. Needs internet (apt, pip, npm). Log: /var/log/chargepoint-install.log
set -euxo pipefail
exec > >(tee -a /var/log/chargepoint-install.log) 2>&1

SRC=/boot/firmware/chargepoint
APP=/opt/chargepoint
STATE=/var/lib/chargepoint
U=samtec

[ -f "$APP/.installed" ] && { echo "already installed"; exit 0; }

# wait for network
for i in $(seq 120); do curl -sfI https://deb.debian.org >/dev/null && break; sleep 5; done

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  default-jre-headless nodejs npm python3-venv openssl

mkdir -p "$APP/tools/firebase" "$STATE"
cp "$SRC"/{poller.py,index.html,requirements.txt,database.rules.json,token_server.py,run.sh,kiosk.sh,screen.sh} "$APP/"
cp "$SRC"/firebase.json "$APP/"
cp "$SRC"/package.json "$SRC"/package-lock.json "$APP/tools/firebase/"
cp "$SRC"/env "$APP/.env"
chmod 600 "$APP/.env"
chmod +x "$APP"/run.sh "$APP"/kiosk.sh "$APP"/screen.sh

# Emulator ignores credentials, but firebase_admin must parse a valid service-account
# key. Generate a throwaway one so the real Firebase key never lives on the Pi.
openssl genrsa -out /tmp/dummy.pem 2048
python3 - <<'PY'
import json
pem = open("/tmp/dummy.pem").read()
json.dump({"type": "service_account", "project_id": "demo-samtec-chargers",
           "private_key_id": "dummy", "private_key": pem,
           "client_email": "dummy@demo-samtec-chargers.iam.gserviceaccount.com",
           "client_id": "0", "token_uri": "https://oauth2.googleapis.com/token"},
          open("/opt/chargepoint/firebase-key.json", "w"))
PY
rm -f /tmp/dummy.pem
chmod 600 "$APP/firebase-key.json"

chown -R $U:$U "$APP" "$STATE"
sudo -u $U bash -c "
  set -e
  cd $APP
  python3 -m venv venv
  PIP_CONFIG_FILE=/dev/null ./venv/bin/pip install --retries 10 --timeout 60 --no-cache-dir -r requirements.txt
  cd tools/firebase && npm ci --no-audit --no-fund
  cd $APP && ./tools/firebase/node_modules/.bin/firebase setup:emulators:database
"

# services
cat >/etc/systemd/system/chargepoint.service <<EOF
[Unit]
Description=ChargePoint monitor (RTDB emulator + poller + web)
After=network-online.target
Wants=network-online.target

[Service]
User=$U
ExecStart=$APP/run.sh
Restart=always
RestartSec=10
TimeoutStopSec=90

[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/chargepoint-token.service <<EOF
[Unit]
Description=ChargePoint token paste page (port 8081)
After=network-online.target

[Service]
User=$U
Environment=CHARGEPOINT_STATE_DIR=$STATE
ExecStart=$APP/venv/bin/python -I $APP/token_server.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/chargepoint-screen.service <<EOF
[Unit]
Description=Blank HDMI screen at night

[Service]
Type=oneshot
User=$U
ExecStart=$APP/screen.sh
EOF
cat >/etc/systemd/system/chargepoint-screen.timer <<EOF
[Unit]
Description=Check screen schedule every 5 minutes

[Timer]
OnBootSec=2min
OnCalendar=*:0/5

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable chargepoint.service chargepoint-token.service chargepoint-screen.timer

# kiosk: desktop autologin as $U, labwc autostart runs only the browser (no panel/desktop)
raspi-config nonint do_boot_behaviour B4 || true
install -d -o $U -g $U /home/$U/.config/labwc
cat >/home/$U/.config/labwc/autostart <<EOF
/usr/bin/lwrespawn $APP/kiosk.sh &
EOF
chown $U:$U /home/$U/.config/labwc/autostart

# remove secrets from the (world-readable FAT) boot partition
shred -u "$SRC/env" || rm -f "$SRC/env"

touch "$APP/.installed"
systemctl reboot
