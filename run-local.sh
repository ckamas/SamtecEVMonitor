#!/usr/bin/env bash
# Run the whole stack locally, then tear it down cleanly on Ctrl-C / SIGTERM:
#   1. Firebase RTDB emulator   (data persists in ./emulator-data, exported on exit)
#   2. ChargePoint login + first poll (proves the session token works; saves it)
#   3. Web server serving index.html patched to read from the emulator
#   4. Poll loop every POLL_INTERVAL seconds (keeps the rotating token alive)
#
# Env overrides: WEB_PORT (8080), POLL_INTERVAL (300), EMULATOR_PORT (9000 — must match firebase.json)
cd "$(dirname "$0")" || exit 1
ROOT="$PWD"

WEB_PORT="${WEB_PORT:-8080}"
POLL_INTERVAL="${POLL_INTERVAL:-300}"
EMULATOR_PORT="${EMULATOR_PORT:-9000}"
PROJECT="demo-samtec-chargers"
LOGDIR="${TMPDIR:-/tmp}/chargepoint-local"
mkdir -p "$LOGDIR"
SERVE_DIR="$(mktemp -d "$LOGDIR/www.XXXXXX")"

EMU_PID="" WEB_PID="" LOOP_PID=""

log() { printf '%s [run-local] %s\n' "$(date +%H:%M:%S)" "$*"; }

stop_pid() { # name pid signal timeout_s
  local name="$1" pid="$2" sig="$3" t="${4:-10}"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 0
  log "stopping $name (pid $pid)"
  kill "-$sig" "$pid" 2>/dev/null
  for _ in $(seq $((t * 5))); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.2
  done
  log "$name did not exit in ${t}s; killing"
  kill -KILL "$pid" 2>/dev/null
}

cleanup() {
  trap - EXIT INT TERM
  echo
  log "shutting down"
  stop_pid "poll loop" "$LOOP_PID" TERM 5
  stop_pid "web server" "$WEB_PID" TERM 5
  # SIGINT lets the firebase CLI run its --export-on-exit before quitting.
  stop_pid "emulator" "$EMU_PID" INT 60
  rm -rf "$SERVE_DIR"
  log "done (logs in $LOGDIR)"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

die() { log "ERROR: $*"; exit 1; }

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# --- preflight --------------------------------------------------------------
[ -f .env ] || die ".env not found"
[ -x ./venv/bin/python ] || die "venv missing (python3 -m venv venv && ./venv/bin/pip install -r requirements.txt)"
[ -x ./tools/firebase/node_modules/.bin/firebase ] || die "firebase CLI missing in tools/firebase"
for p in "$EMULATOR_PORT" 4000 "$WEB_PORT"; do
  port_busy "$p" && die "port $p already in use"
done

set -a; . ./.env; set +a
# Point the poller at the emulator regardless of what .env says.
export FIREBASE_DATABASE_EMULATOR_HOST="127.0.0.1:$EMULATOR_PORT"
export FIREBASE_DATABASE_URL="http://127.0.0.1:$EMULATOR_PORT/?ns=$PROJECT"

# --- 1. emulator ------------------------------------------------------------
log "starting RTDB emulator (UI http://127.0.0.1:4000)"
./tools/firebase/node_modules/.bin/firebase emulators:start \
  --only database --project "$PROJECT" \
  --import ./emulator-data --export-on-exit ./emulator-data \
  >"$LOGDIR/emulator.log" 2>&1 &
EMU_PID=$!

for i in $(seq 120); do
  port_busy "$EMULATOR_PORT" && break
  kill -0 "$EMU_PID" 2>/dev/null || die "emulator exited; see $LOGDIR/emulator.log"
  sleep 0.5
done
port_busy "$EMULATOR_PORT" || die "emulator not listening after 60s; see $LOGDIR/emulator.log"
log "emulator up"

# --- 2. ChargePoint login + first poll --------------------------------------
log "logging in to ChargePoint and polling once (log: $LOGDIR/poller.log)"
if ! ./venv/bin/python poller.py 2>&1 | tee -a "$LOGDIR/poller.log" | grep -E 'Logged in|Writing|Done|ERROR|rejected|blocked'; then
  :
fi
TOKEN_FILE="${CHARGEPOINT_TOKEN_STATE_PATH:-$HOME/.chargepoint_session_token}"
# pipefail not set, so judge success by the saved token being fresh (written this run).
if [ -z "$(find "$TOKEN_FILE" -mmin -2 2>/dev/null)" ]; then
  die "login/poll failed (no fresh session token at $TOKEN_FILE); see $LOGDIR/poller.log"
fi
log "login OK, session token refreshed at $TOKEN_FILE"

# --- 3. web server (patched copy; index.html itself is untouched) ------------
sed -E "s#databaseURL: \"[^\"]*\"#databaseURL: \"http://127.0.0.1:$EMULATOR_PORT/?ns=$PROJECT\"#" \
  index.html >"$SERVE_DIR/index.html"
grep -q "127.0.0.1:$EMULATOR_PORT" "$SERVE_DIR/index.html" || die "failed to patch databaseURL in index.html"
cp -f CNAME "$SERVE_DIR/" 2>/dev/null
./venv/bin/python -m http.server "$WEB_PORT" --bind 127.0.0.1 --directory "$SERVE_DIR" \
  >"$LOGDIR/web.log" 2>&1 &
WEB_PID=$!
sleep 0.5
kill -0 "$WEB_PID" 2>/dev/null || die "web server failed; see $LOGDIR/web.log"
log "web UI:  http://127.0.0.1:$WEB_PORT/"

# --- 4. poll loop -----------------------------------------------------------
(
  trap 'kill $SLEEP_PID 2>/dev/null; exit 0' TERM INT
  while :; do
    sleep "$POLL_INTERVAL" & SLEEP_PID=$!
    wait "$SLEEP_PID"
    ./venv/bin/python poller.py >>"$LOGDIR/poller.log" 2>&1 \
      && echo "$(date +%H:%M:%S) [poll] ok" || echo "$(date +%H:%M:%S) [poll] FAILED (see $LOGDIR/poller.log)"
  done
) &
LOOP_PID=$!
log "polling every ${POLL_INTERVAL}s. Ctrl-C to stop."

wait "$LOOP_PID"
