# Samtec EV Charger Monitor

Live status board for the Samtec EV chargers. A poller logs in to ChargePoint, reads the
status of each port, and writes it to a Firebase Realtime Database (RTDB). A static page
(`index.html`) subscribes to the database and shows each stall as Available / Charging /
Complete, with a co-op charging-time limit (default 120 min) and overdue highlighting.

Live site: <https://samtec-ev.xyz> (see `CNAME`). Add `?kiosk=1` to the URL for TV/wall display.

## How it works

```
ChargePoint API ──► poller.py ──► Firebase RTDB (/stations) ──► index.html (browser)
   (coulomb_sess                    also reads /slot_resets
    session token)                  and /slot_extensions
```

- `poller.py` – one poll per run: fetches each station in `CHARGEPOINT_STATION_IDS`, tracks
  per-port session start times, computes policy-deadline state, writes `/stations`.
- `index.html` – the UI (Tailwind via CDN, Firebase JS SDK). Static; the `CNAME` file suggests GitHub Pages hosting.
- `.github/workflows/poll.yml` – runs the poller every 5 minutes on a **self-hosted macOS ARM64 runner**.
- `RPI/` – an alternative fully offline deployment: a Raspberry Pi kiosk running a local RTDB
  emulator, the poller loop, and Chromium full-screen.
- `run-local.sh`, `run-emulator.sh`, `run-poller.sh` – local development against the Firebase emulator.
- `database.rules.json`, `firebase.json` – RTDB rules and emulator config.

## Authentication: the `coulomb_sess` token

ChargePoint's password login endpoint is protected by Datadome (captcha), so the poller
authenticates with a **`coulomb_sess` session cookie** captured from a logged-in browser.
ChargePoint re-issues the cookie with a fresh ~2 hour lifetime on every authenticated
response; the poller saves the rotated value after each successful run
(`~/.chargepoint_session_token`, or `CHARGEPOINT_TOKEN_STATE_PATH`) so the session stays
alive as long as it polls more often than every ~2 hours.

The token only needs replacing when polling has been interrupted for longer than that
(machine off, network down, runner offline) or ChargePoint invalidates the session. Symptoms:
the poller logs `Saved session token was rejected`, or the Action fails, or the site's
"last updated" time stops advancing.

## Updating the token

### 1. Get a fresh token

1. Log in at <https://driver.chargepoint.com> in a normal browser.
2. Open DevTools → **Application** (Chrome/Edge) or **Storage** (Firefox) → **Cookies** →
   `https://driver.chargepoint.com`.
3. Copy the **Value** of the `coulomb_sess` cookie. (A leading `coulomb_sess=` is fine; the
   Pi tools strip it.)

Treat it like a password – anyone with it can act as your ChargePoint account.

### 2a. GitHub Actions (self-hosted runner)

The workflow reads the `CHARGEPOINT_SESSION_TOKEN` repository secret.

```bash
gh secret set CHARGEPOINT_SESSION_TOKEN --repo ckamas/SamtecEVMonitor
# paste the token when prompted, then Ctrl-D
```

Or: GitHub → Settings → Secrets and variables → Actions → `CHARGEPOINT_SESSION_TOKEN` → Update.

Then trigger a run immediately: Actions → "ChargePoint Poller" → **Run workflow**
(or `gh workflow run poll.yml`).

> Note: `poller.py` prefers `CHARGEPOINT_SESSION_TOKEN` (if non-empty) over the rotated token
> saved on the runner at `~/.chargepoint_session_token`. If you would rather let the runner's
> saved, auto-rotating token take over, clear the secret and write the fresh token to that file
> on the runner instead.

### 2b. Raspberry Pi kiosk

Any of these; the Pi notices the new token within ~5 seconds and polls immediately.

- **Paste page** (easiest): open `http://SamtecEVMonitor.local:8081/`, paste the cookie value, Save.
  The page also shows token age and the last poll status.
- **From your computer over SSH**:
  ```bash
  ./RPI/push-token.sh                 # reads ~/.chargepoint_session_token
  ./RPI/push-token.sh <token>         # token as an argument
  echo <token> | ./RPI/push-token.sh -
  PI_HOST=user@host ./RPI/push-token.sh <token>   # default samtec@SamtecEVMonitor.local
  ```
- **Manually**: write the token to `/var/lib/chargepoint/session_token` on the Pi.

### 2c. Local development

Put the token in `.env` as `CHARGEPOINT_SESSION_TOKEN=...`, or write it to
`~/.chargepoint_session_token` (what `run-local.sh` checks for a fresh login), then re-run
`./run-local.sh`.

## Configuration

Environment variables (see `.env.example`; `.env` and `firebase-key.json` are git-ignored):

| Variable | Purpose |
| --- | --- |
| `CHARGEPOINT_USER` | ChargePoint login email/username (required) |
| `CHARGEPOINT_SESSION_TOKEN` | `coulomb_sess` cookie (preferred over password) |
| `CHARGEPOINT_PASS` | Password login fallback (often blocked by captcha) |
| `CHARGEPOINT_STATION_IDS` | Comma-separated ChargePoint device IDs (required) |
| `CHARGEPOINT_TOKEN_STATE_PATH` | Where the rotated token is persisted (default `~/.chargepoint_session_token`) |
| `FIREBASE_DATABASE_URL` | RTDB URL (required) |
| `FIREBASE_KEY` | Service-account JSON as a string (used on CI) |
| `FIREBASE_CREDENTIALS_PATH` | Service-account file path (default `firebase-key.json`) |
| `CHARGING_LIMIT_MINUTES` | Co-op charging window, default `120` |

GitHub Actions secrets used by the workflow: `CHARGEPOINT_USER`, `CHARGEPOINT_PASS`,
`CHARGEPOINT_SESSION_TOKEN`, `CHARGEPOINT_STATION_IDS`, `FIREBASE_DATABASE_URL`, `FIREBASE_KEY`.

## Running locally

Requires Python 3.11+, Node.js, and Java (for the Firebase emulator).

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
(cd tools/firebase && npm ci)
cp .env.example .env        # fill in values, including the session token
./run-local.sh              # emulator + first poll + web UI at http://127.0.0.1:8080 + poll loop
```

Other helpers: `./run-emulator.sh` (emulator only, UI at <http://127.0.0.1:4000>) and
`./run-poller.sh` (one poll against the emulator). Emulator data persists in `emulator-data/`.

## Raspberry Pi kiosk

Self-contained: the Pi runs the RTDB emulator, the poll loop, a local web server and a
full-screen Chromium, so it needs no Firebase credentials (a throwaway service-account key is generated).

1. Flash Raspberry Pi OS (with cloud-init) to an SD card and leave the boot partition mounted.
2. Make sure `.env` exists in the repo, then run `./RPI/flash-payload.sh`
   (`BOOT=` overrides the mount path, `HDMI_VIDEO=` the HDMI port). It copies the app to the
   boot partition and hooks `RPI/payload/install.sh` into cloud-init.
3. Boot the Pi with internet access. First boot installs everything, removes the copied `.env`
   from the boot partition, and reboots into the kiosk.
4. Push a token (see [Updating the token](#updating-the-token)). Until one arrives the UI is
   up and the Pi reports "waiting for token".

Services: `chargepoint` (emulator + poller + web, port 8080), `chargepoint-token` (paste page,
port 8081), `chargepoint-screen.timer` (HDMI off 18:00–07:00). Logs: `/var/log/chargepoint-install.log`,
`journalctl -u chargepoint`. Status: `/var/lib/chargepoint/status`.
