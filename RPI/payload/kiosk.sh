#!/usr/bin/env bash
# Full-screen Chromium on the local UI. Respawned by lwrespawn if it exits.
until curl -sf -o /dev/null http://127.0.0.1:8080/; do sleep 2; done
exec chromium --kiosk --noerrdialogs --disable-infobars --no-first-run \
  --ozone-platform=wayland --password-store=basic \
  --disable-session-crashed-bubble --disable-features=Translate \
  --user-data-dir="$HOME/.config/chargepoint-kiosk" \
  'http://127.0.0.1:8080/?kiosk=1'
