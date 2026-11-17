#!/usr/bin/env bash
# Screen off 18:00-07:00 local time, on otherwise. Idempotent; run every few minutes.
# The TV is also put in standby / woken over HDMI-CEC (needs cec-utils and AQUOS LINK
# enabled in the TV menu). Order matters: standby before the output is disabled (the
# CEC physical address needs a live HDMI link), wake after it is re-enabled.
export XDG_RUNTIME_DIR=/run/user/$(id -u)
sock=$(ls "$XDG_RUNTIME_DIR"/wayland-[0-9] 2>/dev/null | head -1)
[ -n "$sock" ] || exit 0
export WAYLAND_DISPLAY=$(basename "$sock")
h=$(date +%-H)
if [ "$h" -ge 18 ] || [ "$h" -lt 7 ]; then want=no; else want=yes; fi

cec() { echo "$1" | timeout 15 cec-client -s -d 1 2>&1; }

cec_standby() {
  command -v cec-client >/dev/null || return 0
  cec standby\ 0 >/dev/null
  logger -t chargepoint-screen "CEC standby sent"
}

cec_wake() {
  command -v cec-client >/dev/null || return 0
  local i
  for i in 1 2 3; do
    cec on\ 0 >/dev/null
    sleep 5
    if cec "pow 0" | grep -q 'power status: \(on\|in transition from standby to on\)'; then
      logger -t chargepoint-screen "CEC wake ok (attempt $i)"
      return 0
    fi
  done
  logger -t chargepoint-screen "CEC wake FAILED after 3 attempts"
}

wlr-randr | awk '/^[^ ]/{n=$1} /Enabled:/{print n, $2}' | while read -r o e; do
  case "$o" in HDMI-A-*) ;; *) continue ;; esac
  [ "$e" = "$want" ] && continue
  if [ "$want" = yes ]; then
    wlr-randr --output "$o" --on
    sleep 2
    cec_wake
  else
    cec_standby
    wlr-randr --output "$o" --off
  fi
done
