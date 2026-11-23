#!/usr/bin/env bash
# Push the current index.html (and poller.py) to the Pi and reload the kiosk browser.
#   ./RPI/update.sh              # copy files, refresh served page, restart Chromium (lwrespawn relaunches it)
#   ./RPI/update.sh --service    # also restart chargepoint.service (needed after poller.py / run.sh changes; asks for sudo)
# Env: PI_HOST (default samtec@SamtecEVMonitor.local); SSH / SCP (default ssh / scp; e.g. cssh / cscp)
set -euo pipefail
cd "$(dirname "$0")/.."
PI_HOST="${PI_HOST:-samtec@SamtecEVMonitor.local}"
SSH="${SSH:-ssh}"; SCP="${SCP:-scp}"
APP=/opt/chargepoint
DB_PORT=9000 PROJECT=demo-samtec-chargers

$SCP -q index.html poller.py admin_sessions.py requirements.txt RPI/payload/run.sh "$PI_HOST:$APP/"
$SSH "$PI_HOST" "chmod +x $APP/run.sh"
echo "copied index.html, poller.py, admin_sessions.py, requirements.txt, run.sh -> $PI_HOST:$APP"
# Keep the Pi venv in step with requirements.txt (e.g. requests for admin_sessions.py).
$SSH "$PI_HOST" "cd $APP && PIP_CONFIG_FILE=/dev/null ./venv/bin/pip install -q -r requirements.txt"
echo "venv requirements up to date"

if [ "${1:-}" = "--service" ]; then
  # Restarting the service rebuilds the served page itself.
  $SSH -t "$PI_HOST" 'sudo systemctl restart chargepoint.service'
  echo "chargepoint.service restarted"
else
  # Same databaseURL patch run.sh applies, written over the already-served copy.
  $SSH "$PI_HOST" "WWW=/run/user/\$(id -u)/chargepoint-www
    sed -E 's#databaseURL: \"[^\"]*\"#databaseURL: \"http://127.0.0.1:$DB_PORT/?ns=$PROJECT\"#' $APP/index.html > \$WWW/index.html.new &&
    mv \$WWW/index.html.new \$WWW/index.html &&
    grep -q '127.0.0.1:$DB_PORT' \$WWW/index.html"
  echo "served page refreshed"
fi

# Reload the kiosk: kill Chromium; lwrespawn restarts kiosk.sh, which reopens the page.
$SSH "$PI_HOST" 'pkill -x chromium || true'
echo "kiosk reloading — page should be back in a few seconds"
