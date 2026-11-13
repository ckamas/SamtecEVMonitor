#!/usr/bin/env bash
# Copy the app + first-boot installer onto the SD card's boot partition and hook it into
# cloud-init (user-data). Backs up user-data first. Does NOT touch the rootfs partition.
set -euo pipefail
cd "$(dirname "$0")/.."
BOOT="${BOOT:-/media/$USER/bootfs}"
[ -f "$BOOT/user-data" ] && [ -f "$BOOT/cmdline.txt" ] || { echo "no Pi boot partition at $BOOT" >&2; exit 1; }
[ -f .env ] || { echo ".env missing" >&2; exit 1; }
[ -d "$BOOT/../rootfs/var/lib/cloud/instance" ] && { echo "Pi has already booted; cloud-init won't re-run" >&2; exit 1; }

D="$BOOT/chargepoint"
mkdir -p "$D"
cp poller.py index.html requirements.txt database.rules.json "$D/"
cp tools/firebase/package.json tools/firebase/package-lock.json "$D/"
cp RPI/payload/{install.sh,run.sh,kiosk.sh,screen.sh,token_server.py,firebase.json} "$D/"
cp .env "$D/env"

cp -n "$BOOT/user-data" "$BOOT/user-data.orig"
if ! grep -q chargepoint/install.sh "$BOOT/user-data"; then
  sed -i '/systemctl, enable, --now, ssh/a\  - [ systemd-run, --unit=chargepoint-install, bash, /boot/firmware/chargepoint/install.sh ]' "$BOOT/user-data"
fi
grep -n -A3 '^runcmd' "$BOOT/user-data"

# --- HDMI: force the output on (the Pi didn't detect the display at boot without this) ---
# Override the port with HDMI_VIDEO=HDMI-A-2 (HDMI-A-1 is the micro-HDMI port nearest USB-C).
# Set HDMI_VIDEO=none to skip.
HDMI_VIDEO="${HDMI_VIDEO:-HDMI-A-1}"
if [ "$HDMI_VIDEO" != none ] && ! grep -q 'video=HDMI' "$BOOT/cmdline.txt"; then
  cp -n "$BOOT/cmdline.txt" "$BOOT/cmdline.txt.orig"
  sed -i "1 s/\$/ video=$HDMI_VIDEO:1920x1080@60D/" "$BOOT/cmdline.txt"
fi
[ "$(wc -l <"$BOOT/cmdline.txt")" -le 1 ] || { echo "cmdline.txt must be a single line" >&2; exit 1; }
echo "cmdline.txt: $(cut -c1-400 "$BOOT/cmdline.txt" | grep -o 'video=[^ ]*' || echo 'no video= param')"
sync
echo "done. Eject: udisksctl unmount -b /dev/mmcblk0p1; udisksctl unmount -b /dev/mmcblk0p2"
