#!/usr/bin/env bash
# Push ChargePoint admin-portal cookies (a "Copy as cURL" paste or a bare cookie string) to the Pi.
#   ./RPI/push-admin-cookie.sh [file]     # default ./admin_curl.txt
#   cat file | ./RPI/push-admin-cookie.sh -
# Also clears the Pi's saved rotated cookies so the new seed is used on the next poll.
# Env: PI_HOST (default samtec@SamtecEVMonitor.local); SSH (default ssh; e.g. cssh)
set -euo pipefail
cd "$(dirname "$0")/.."
PI_HOST="${PI_HOST:-samtec@SamtecEVMonitor.local}"
SSH="${SSH:-ssh}"
src="${1:-admin_curl.txt}"
if [ "$src" = "-" ]; then data="$(cat)"; else data="$(cat "$src")"; fi
[ "${#data}" -ge 50 ] || { echo "cookie data looks empty/too short (${#data} chars)" >&2; exit 1; }
printf '%s\n' "$data" | $SSH "$PI_HOST" 'umask 077; cat > /var/lib/chargepoint/admin_cookie.new && mv /var/lib/chargepoint/admin_cookie.new /var/lib/chargepoint/admin_cookie && rm -f /var/lib/chargepoint/admin_cookie_state'
echo "admin cookies pushed to $PI_HOST (takes effect after the service restarts: ./RPI/update.sh --service)"
