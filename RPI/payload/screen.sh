#!/usr/bin/env bash
# Screen off 18:00-07:00 local time, on otherwise. Idempotent; run every few minutes.
export XDG_RUNTIME_DIR=/run/user/$(id -u)
sock=$(ls "$XDG_RUNTIME_DIR"/wayland-[0-9] 2>/dev/null | head -1)
[ -n "$sock" ] || exit 0
export WAYLAND_DISPLAY=$(basename "$sock")
h=$(date +%-H)
if [ "$h" -ge 18 ] || [ "$h" -lt 7 ]; then want=no; else want=yes; fi
wlr-randr | awk '/^[^ ]/{n=$1} /Enabled:/{print n, $2}' | while read -r o e; do
  case "$o" in HDMI-A-*) ;; *) continue ;; esac
  [ "$e" = "$want" ] && continue
  if [ "$want" = yes ]; then wlr-randr --output "$o" --on; else wlr-randr --output "$o" --off; fi
done
