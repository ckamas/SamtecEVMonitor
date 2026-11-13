#!/usr/bin/env bash
# Pi equivalent of run-local.sh: RTDB emulator + poll loop + local web server.
# Unlike run-local.sh it does NOT die when there is no/expired token; it keeps the
# UI up and polls as soon as a token appears (pushed via scp or the paste page).
cd "$(dirname "$0")" || exit 1
APP="$PWD"
STATE=/var/lib/chargepoint
WWW=/run/user/$(id -u)/chargepoint-www
POLL_INTERVAL="${POLL_INTERVAL:-300}"
PORT_DB=9000 PORT_WEB=8080 PROJECT=demo-samtec-chargers
mkdir -p "$WWW" "$STATE"

set -a; . ./.env; set +a
unset FIREBASE_KEY
export FIREBASE_CREDENTIALS_PATH="$APP/firebase-key.json"
export FIREBASE_DATABASE_EMULATOR_HOST="127.0.0.1:$PORT_DB"
export FIREBASE_DATABASE_URL="http://127.0.0.1:$PORT_DB/?ns=$PROJECT"
export CHARGEPOINT_TOKEN_STATE_PATH="$STATE/session_token"

log() { printf '%s [run] %s\n' "$(date +%H:%M:%S)" "$*"; }
EMU="" WEB=""
cleanup() {
  trap - EXIT INT TERM
  [ -n "$WEB" ] && kill "$WEB" 2>/dev/null
  [ -n "$EMU" ] && { kill -INT "$EMU" 2>/dev/null; wait "$EMU" 2>/dev/null; }  # SIGINT => export-on-exit
}
trap cleanup EXIT
trap 'exit 143' INT TERM

mkdir -p "$STATE/emulator-data"
IMPORT=()
[ -f "$STATE/emulator-data/firebase-export-metadata.json" ] && IMPORT=(--import "$STATE/emulator-data")
./tools/firebase/node_modules/.bin/firebase emulators:start \
  --only database --project "$PROJECT" "${IMPORT[@]}" --export-on-exit "$STATE/emulator-data" &
EMU=$!

for _ in $(seq 120); do
  (exec 3<>"/dev/tcp/127.0.0.1/$PORT_DB") 2>/dev/null && break
  kill -0 "$EMU" 2>/dev/null || { log "emulator exited"; exit 1; }
  sleep 1
done
log "emulator up"

sed -E "s#databaseURL: \"[^\"]*\"#databaseURL: \"http://127.0.0.1:$PORT_DB/?ns=$PROJECT\"#" index.html >"$WWW/index.html"
grep -q "127.0.0.1:$PORT_DB" "$WWW/index.html" || { log "failed to patch databaseURL"; exit 1; }
./venv/bin/python -I -m http.server "$PORT_WEB" --bind 127.0.0.1 --directory "$WWW" >/dev/null 2>&1 &
WEB=$!
log "web UI http://127.0.0.1:$PORT_WEB/"

last=0
while :; do
  now=$(date +%s)
  tok="$CHARGEPOINT_TOKEN_STATE_PATH"
  if [ -s "$tok" ]; then
    tok_mtime=$(stat -c %Y "$tok")
    # poll on schedule, or immediately when a new token was pushed since the last poll
    if [ $((now - last)) -ge "$POLL_INTERVAL" ] || [ "$tok_mtime" -gt "$last" ] && [ $((now - last)) -ge 5 ]; then
      if ./venv/bin/python poller.py; then
        echo "ok $(date -Is)" >"$STATE/status"
      else
        echo "FAILED $(date -Is) (token expired? push a fresh one)" >"$STATE/status"
      fi
      last=$(date +%s)
    fi
  else
    echo "waiting for token $(date -Is)" >"$STATE/status"
  fi
  sleep 5
done
