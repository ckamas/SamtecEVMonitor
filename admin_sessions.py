"""
Live charging-session details from the ChargePoint admin (Network Management) portal.

The public map API that poller.py uses only reports port *status*. An org admin
login can additionally see, for every port with a car plugged in: start time,
elapsed time, energy, power, voltage, current and the driver. The portal renders
those into the server-side HTML of ``/index.php/station_info_view/<station_id>``
(Sessions section), so this module scrapes that page.

Nothing here writes to ChargePoint (GET only) and nothing here touches poller.py.

Privacy: the page contains the driver's full name. ``parse_station_sessions``
reduces it to initials and never returns the name, username or email.

Usage (prints a summary with initials only):
    python admin_sessions.py 99037 1958701
Seed cookies come from CHARGEPOINT_ADMIN_COOKIE (value) or CHARGEPOINT_ADMIN_COOKIE_FILE
(default ~/.chargepoint_admin_token.txt); either may be a bare ``Cookie:`` header value or
a "Copy as cURL" command. The portal rotates its cookies (2h) on every response, so the
latest values are saved to CHARGEPOINT_ADMIN_STATE_PATH (default
~/.chargepoint_admin_cookie_state) and preferred on the next run.
"""
from __future__ import annotations

import html as html_lib
import logging
import os
import re
import shlex
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://na.chargepoint.com"
DEFAULT_COOKIE_FILE = "~/.chargepoint_admin_token.txt"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/158.0.0.0 Safari/537.36"
)

# The page prints local time with an abbreviation, e.g. "2026-11-20 14:32:43 (PST)".
_TZ_OFFSETS_H = {
    "UTC": 0, "GMT": 0,
    "EST": -5, "EDT": -4, "CST": -6, "CDT": -5,
    "MST": -7, "MDT": -6, "PST": -8, "PDT": -7,
}


class AdminPortalError(Exception):
    """Fetch failed or the page is not the expected station page."""


class AdminSessionExpired(AdminPortalError):
    """Cookies were rejected (login page / captcha instead of the station page)."""


class AdminParseError(AdminPortalError):
    """The page loaded but the Sessions section was not found or changed shape."""


# --------------------------------------------------------------------------- parsing

_SESSIONS_START = re.compile(r'id="sessionsContainer"')
_PORT_HEADING = re.compile(
    r'<h3[^>]*class="childH3"[^>]*>\s*<span>\s*Port\s*(\d+)\s*</span>', re.I
)
_PAIR = re.compile(
    r'<label>\s*([^<]*?)\s*</label>\s*</div>\s*'
    r'<div class="rightColumnItem">\s*(.*?)\s*</div>\s*</div>',
    re.S,
)


def _text(fragment: str) -> str:
    fragment = re.sub(r"<[^>]+>", "", fragment)
    fragment = html_lib.unescape(fragment).replace("\xa0", " ")
    return re.sub(r"\s+", " ", fragment).strip()


def _number(value: str) -> Optional[float]:
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", value or "")
    return float(m.group(1)) if m else None


def initials(full_name: str) -> str:
    """'chuck kamas' -> 'CK'; max 3 letters; empty string if no usable letters."""
    words = re.findall(r"[^\W\d_]+", full_name or "", re.UNICODE)
    return "".join(w[0].upper() for w in words[:3])


def parse_local_time(value: str) -> Optional[datetime]:
    """'2026-11-20 14:32:43 (PST)' -> aware UTC datetime. None if unparseable."""
    m = re.match(r"\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*(?:\((\w+)\))?", value or "")
    if not m:
        return None
    abbr = (m.group(2) or "UTC").upper()
    if abbr not in _TZ_OFFSETS_H:
        return None  # unknown zone: better no time than a wrong time
    naive = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    return (naive - timedelta(hours=_TZ_OFFSETS_H[abbr])).replace(tzinfo=timezone.utc)


def parse_elapsed(value: str) -> Optional[int]:
    """'02:15:11' -> seconds. Also accepts 'MM:SS'."""
    parts = re.findall(r"\d+", value or "")
    if len(parts) == 3:
        h, m, s = map(int, parts)
    elif len(parts) == 2:
        h, (m, s) = 0, map(int, parts)
    else:
        return None
    return h * 3600 + m * 60 + s


def _session_from_pairs(pairs: Dict[str, str]) -> Dict[str, Any]:
    started = parse_local_time(pairs.get("Start Time", ""))
    return {
        "driver_initials": initials(pairs.get("Driver Name", "")),
        "started_at": started.isoformat() if started else None,
        "elapsed_seconds": parse_elapsed(pairs.get("Elapsed Time", "")),
        "energy_kwh": _number(pairs.get("Total Energy", "")),
        "power_kw": _number(pairs.get("Power", "")),
        "voltage_v": _number(pairs.get("Voltage", "")),
        "current_a": _number(pairs.get("Current", "")),
    }


def parse_station_sessions(page: str) -> Dict[str, Dict[str, Any]]:
    """
    Return {port_number_str: session_dict} for ports with an active session.
    An empty dict means "the portal says there are no active sessions".
    Raises AdminParseError if the Sessions section is missing or unrecognisable, so
    callers never mistake a changed page for an idle station.
    """
    m = _SESSIONS_START.search(page)
    if not m:
        raise AdminParseError("sessionsContainer not found in station page")

    # The section has no clean closing tag; it is followed by inline <script>.
    tail = page[m.end():]
    end = tail.find("<script")
    section = tail[: end if end != -1 else len(tail)]

    headings = list(_PORT_HEADING.finditer(section))
    if not headings:
        if re.search(r"No active sessions", section, re.I):
            return {}
        raise AdminParseError("no 'Port N' blocks and no 'No active sessions' text")

    out: Dict[str, Dict[str, Any]] = {}
    for i, h in enumerate(headings):
        chunk_end = headings[i + 1].start() if i + 1 < len(headings) else len(section)
        pairs = {
            _text(label): _text(value)
            for label, value in _PAIR.findall(section[h.end():chunk_end])
        }
        if "Start Time" not in pairs and "Power" not in pairs:
            raise AdminParseError(f"Port {h.group(1)} block has no session fields")
        out[h.group(1)] = _session_from_pairs(pairs)
    return out


# --------------------------------------------------------------------------- fetching


def parse_cookie_text(raw: str) -> Dict[str, str]:
    """
    Return {'cookie': ..., 'user_agent': ...} from text holding either a bare
    Cookie header value or a pasted 'Copy as cURL' command (uses its -b/-H cookie).
    """
    raw = (raw or "").strip()
    if not raw:
        raise AdminPortalError("admin cookie text is empty")
    if not raw.startswith("curl"):
        return {"cookie": raw.replace("\n", "").strip(), "user_agent": DEFAULT_USER_AGENT}

    args = shlex.split(raw.replace("\\\n", " "))
    cookie, ua = "", DEFAULT_USER_AGENT
    for i, a in enumerate(args[:-1]):
        nxt = args[i + 1]
        if a in ("-b", "--cookie"):
            cookie = nxt
        elif a in ("-H", "--header"):
            name, _, val = nxt.partition(":")
            if name.strip().lower() == "cookie" and not cookie:
                cookie = val.strip()
            elif name.strip().lower() == "user-agent":
                ua = val.strip()
    if not cookie:
        raise AdminPortalError("no cookies found in curl command")
    return {"cookie": cookie, "user_agent": ua}


def load_cookie_header(path: str) -> Dict[str, str]:
    try:
        raw = Path(path).expanduser().read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise AdminPortalError(f"cannot read cookie file {path}: {exc}") from exc
    return parse_cookie_text(raw)


def load_seed_auth_from_env() -> Optional[Dict[str, str]]:
    """
    Seed credentials, or None when the admin integration is not configured.
    CHARGEPOINT_ADMIN_COOKIE (value, for CI secrets) wins over
    CHARGEPOINT_ADMIN_COOKIE_FILE.
    """
    value = (os.environ.get("CHARGEPOINT_ADMIN_COOKIE") or "").strip()
    if value:
        return parse_cookie_text(value)
    path = (os.environ.get("CHARGEPOINT_ADMIN_COOKIE_FILE") or "").strip()
    if path:
        return load_cookie_header(path)
    return None


def _cookie_dict(header: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in header.split(";"):
        name, sep, val = part.strip().partition("=")
        if sep and name:
            out[name] = val
    return out


def _cookie_header(cookies: Dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def default_state_path() -> Path:
    return Path(
        os.environ.get("CHARGEPOINT_ADMIN_STATE_PATH") or "~/.chargepoint_admin_cookie_state"
    ).expanduser()


def _load_state(path: Path) -> Optional[Dict[str, str]]:
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    return {"cookie": text, "user_agent": DEFAULT_USER_AGENT} if text else None


def _save_state(path: Path, cookie: str) -> None:
    """Atomically persist the rotated cookies (0600). Never raises."""
    tmp = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as fh:
            fh.write(cookie)
            tmp = fh.name
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        tmp = ""
    except OSError as exc:
        logger.warning("Could not persist refreshed admin cookies: %s", exc)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass


def fetch_station_page(station_id: int, auth: Dict[str, str], timeout: int = 60) -> str:
    import requests  # local import: parsing/tests need no network library

    url = f"{BASE_URL}/index.php/station_info_view/{int(station_id)}"
    headers = {
        "Cookie": auth["cookie"],
        "User-Agent": auth["user_agent"],
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": BASE_URL + "/",
    }
    resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=False)
    if resp.status_code in (301, 302, 303, 307, 308, 401, 403):
        raise AdminSessionExpired(
            f"station {station_id}: HTTP {resp.status_code} "
            "(cookies expired or blocked by bot protection)"
        )
    if resp.status_code != 200:
        raise AdminPortalError(f"station {station_id}: HTTP {resp.status_code}")
    if "sessionsContainer" not in resp.text:
        # 200 but not the station page: login form or captcha interstitial.
        raise AdminSessionExpired(f"station {station_id}: page is not the station view")
    # Portal re-issues auth-session / ci_ui_session (2h) on every response. Merge
    # them so the session stays alive as long as we keep polling.
    rotated = getattr(resp, "cookies", None)
    if rotated:
        jar = _cookie_dict(auth["cookie"])
        jar.update({c.name: c.value for c in rotated})
        auth["cookie"] = _cookie_header(jar)
    return resp.text


def fetch_sessions_for_stations(
    station_ids: Iterable[int],
    seed_auth: Dict[str, str],
    state_path: Optional[Path] = None,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """
    {station_id_str: {port: session}} for every station that could be read.

    Credentials: the rotated cookies saved by the previous run are tried first,
    then the seed cookies. Raises AdminSessionExpired if none is accepted.
    A station that fails for another reason is logged and left out of the result
    (so callers keep the data they already have for it).
    """
    state_path = state_path or default_state_path()
    ids = [int(i) for i in station_ids]
    if not ids:
        return {}

    saved = _load_state(state_path)
    if saved:
        saved["user_agent"] = seed_auth["user_agent"]  # bot protection ties cookie to UA
    candidates, seen = [], set()
    for auth in (saved, seed_auth):
        if auth and auth["cookie"] not in seen:
            seen.add(auth["cookie"])
            candidates.append(dict(auth))

    result: Dict[str, Dict[str, Dict[str, Any]]] = {}
    last_expired: Optional[AdminSessionExpired] = None
    for auth in candidates:
        try:
            first = parse_station_sessions(fetch_station_page(ids[0], auth))
        except AdminSessionExpired as exc:
            logger.warning("Admin cookies rejected (%s).", exc)
            last_expired = exc
            continue
        except AdminPortalError as exc:
            logger.warning("Admin station %s unreadable: %s", ids[0], exc)
            first = None
        if first is not None:
            result[str(ids[0])] = first
        for sid in ids[1:]:
            try:
                result[str(sid)] = parse_station_sessions(fetch_station_page(sid, auth))
            except AdminSessionExpired:
                raise
            except AdminPortalError as exc:
                logger.warning("Admin station %s unreadable: %s", sid, exc)
        _save_state(state_path, auth["cookie"])
        return result

    raise last_expired or AdminSessionExpired("no admin cookies available")


def _main(argv: list) -> int:
    if not argv:
        print(__doc__)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    seed = load_seed_auth_from_env() or load_cookie_header(DEFAULT_COOKIE_FILE)
    try:
        result = fetch_sessions_for_stations([int(x) for x in argv], seed)
    except AdminPortalError as exc:
        print(f"ERROR {type(exc).__name__}: {exc}")
        return 1
    for sid in argv:
        sessions = result.get(str(int(sid)))
        if sessions is None:
            print(f"station {sid}: unreadable (see warnings)")
        elif not sessions:
            print(f"station {sid}: no active sessions")
        for port, sess in sorted((sessions or {}).items()):
            print(f"station {sid} port {port}: {sess}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
