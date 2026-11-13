#!/usr/bin/env bash
# Push a fresh ChargePoint coulomb_sess token from this computer to the Pi (over ssh).
#   ./push-token.sh                 # uses ~/.chargepoint_session_token
#   ./push-token.sh <token>         # token as argument
#   echo <token> | ./push-token.sh -
# Env: PI_HOST (default samtec@SamtecEVMonitor.local)
set -euo pipefail
PI_HOST="${PI_HOST:-samtec@SamtecEVMonitor.local}"
case "${1:-}" in
  "")  tok="$(cat "$HOME/.chargepoint_session_token")" ;;
  "-") tok="$(cat)" ;;
  *)   tok="$1" ;;
esac
tok="$(printf '%s' "$tok" | sed -E 's/^coulomb_sess=//; s/[[:space:]";]//g')"
[ "${#tok}" -ge 20 ] || { echo "token looks too short" >&2; exit 1; }
printf '%s' "$tok" | ssh "$PI_HOST" 'umask 077; cat > /var/lib/chargepoint/session_token.new && mv /var/lib/chargepoint/session_token.new /var/lib/chargepoint/session_token'
echo "token pushed to $PI_HOST; Pi polls within ~5s. Status: http://${PI_HOST#*@}:8081/"
