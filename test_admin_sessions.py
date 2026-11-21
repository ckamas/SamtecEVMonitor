import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import admin_sessions as a


def _box(label, value):
    return (
        '<div class="propSheetDisplayBox"><div class="leftColumnItem">'
        f"<label>{label} </label></div>"
        f'<div class="rightColumnItem"><span class="propertyValue">{value}</span>'
        "</div></div>"
    )


def _port(num, name="Jane Q Public", start="2026-11-20 14:32:43 (PST)",
          elapsed="02:15:11", kwh="6.572640", kw="0.000000", v="211.784000",
          amps="0.000000"):
    return (
        f'<h3 class="childH3"><span> Port {num}</span></h3>'
        '<div class="generalPropSheetCategoryContainer" id="chargingDetailsContainer">'
        '<div class="generalLeftContainer">'
        + _box("Driver Name", f"{name} ")
        + _box("EVatar Name", "jqpublic ")
        + "</div><div class=\"generalRightContainer\">"
        + _box("Start Time", start)
        + _box("Elapsed Time", elapsed)
        + _box("Total Energy", f"\n {kwh}&nbsp;kWh ")
        + _box("Power", f"\n {kw}&nbsp;kW ")
        + _box("Voltage", f"\n {v}&nbsp;V ")
        + _box("Current", f"\n {amps}&nbsp;A ")
        + "<!--</div>-->"
    )


def _page(body):
    return (
        "<html><body>"
        '<div id="sessionsContainer" class="stationInfoClosed propSheetTabSection nc">'
        f'<div class="mainPropSheetContainer"><!-- csr actions -->{body}'
        "<script>$('#x').change(function(){ /* No active sessions. is not here */ });"
        "</script></body></html>"
    )


class ParseTests(unittest.TestCase):
    def test_single_active_port(self):
        got = a.parse_station_sessions(_page(_port(2)))
        self.assertEqual(list(got), ["2"])
        s = got["2"]
        self.assertEqual(s["driver_initials"], "JQP")
        self.assertEqual(s["started_at"], "2026-11-20T22:32:43+00:00")
        self.assertEqual(s["elapsed_seconds"], 2 * 3600 + 15 * 60 + 11)
        self.assertEqual(s["energy_kwh"], 6.57264)
        self.assertEqual(s["power_kw"], 0.0)
        self.assertEqual(s["voltage_v"], 211.784)
        self.assertEqual(s["current_a"], 0.0)

    def test_name_username_email_never_returned(self):
        s = a.parse_station_sessions(_page(_port(1)))["1"]
        blob = repr(s)
        for secret in ("Jane", "Public", "jqpublic"):
            self.assertNotIn(secret, blob)

    def test_two_active_ports_are_separated(self):
        page = _page(_port(1, name="Ann Lee", amps="30.5", kw="7.2")
                     + _port(2, name="Bo Chen", amps="0.0"))
        got = a.parse_station_sessions(page)
        self.assertEqual(sorted(got), ["1", "2"])
        self.assertEqual(got["1"]["driver_initials"], "AL")
        self.assertEqual(got["1"]["current_a"], 30.5)
        self.assertEqual(got["1"]["power_kw"], 7.2)
        self.assertEqual(got["2"]["driver_initials"], "BC")
        self.assertEqual(got["2"]["current_a"], 0.0)

    def test_no_active_sessions_is_empty_dict(self):
        page = (
            '<div id="sessionsContainer" class="x"> No active sessions. '
            "<script>var a=1;</script>"
        )
        self.assertEqual(a.parse_station_sessions(page), {})

    def test_changed_page_raises_instead_of_looking_idle(self):
        with self.assertRaises(a.AdminParseError):
            a.parse_station_sessions("<html>login</html>")
        with self.assertRaises(a.AdminParseError):
            a.parse_station_sessions(
                '<div id="sessionsContainer">something new<script></script>'
            )
        with self.assertRaises(a.AdminParseError):
            a.parse_station_sessions(_page(
                '<h3 class="childH3"><span> Port 1</span></h3><p>redesigned</p>'
            ))

    def test_no_active_text_after_script_is_not_trusted(self):
        # "No active sessions" appearing only inside the script must not count.
        page = _page("")  # script contains the phrase; section itself is empty
        with self.assertRaises(a.AdminParseError):
            a.parse_station_sessions(page)

    def test_garbage_values_become_none_not_crash(self):
        page = _page(_port(1, start="n/a", elapsed="--", kwh="?", kw="", v="x", amps="-"))
        s = a.parse_station_sessions(page)["1"]
        self.assertIsNone(s["started_at"])
        self.assertIsNone(s["elapsed_seconds"])
        self.assertIsNone(s["energy_kwh"])
        self.assertIsNone(s["power_kw"])
        self.assertIsNone(s["current_a"])


class HelperTests(unittest.TestCase):
    def test_initials(self):
        self.assertEqual(a.initials("chuck kamas"), "CK")
        self.assertEqual(a.initials("  Mary-Jane  O'Neil "), "MJO")
        self.assertEqual(a.initials("a b c d e"), "ABC")
        self.assertEqual(a.initials(""), "")
        self.assertEqual(a.initials("1234"), "")

    def test_time_zones(self):
        self.assertEqual(
            a.parse_local_time("2026-07-01 08:00:00 (PDT)").isoformat(),
            "2026-07-01T15:00:00+00:00",
        )
        self.assertEqual(
            a.parse_local_time("2026-11-20 14:32:43 (EST)").isoformat(),
            "2026-11-20T19:32:43+00:00",
        )
        self.assertIsNone(a.parse_local_time("2026-11-20 14:32:43 (XYZ)"))
        self.assertIsNone(a.parse_local_time(""))

    def test_elapsed(self):
        self.assertEqual(a.parse_elapsed("02:15:11"), 8111)
        self.assertEqual(a.parse_elapsed("15:11"), 911)
        self.assertEqual(a.parse_elapsed("26:00:00"), 93600)
        self.assertIsNone(a.parse_elapsed("soon"))


class CookieFileTests(unittest.TestCase):
    def _write(self, text):
        d = tempfile.mkdtemp()
        p = Path(d) / "c.txt"
        p.write_text(text)
        return str(p)

    def test_bare_cookie_header(self):
        got = a.load_cookie_header(self._write("a=1; b=2\n"))
        self.assertEqual(got["cookie"], "a=1; b=2")

    def test_curl_command_b_flag(self):
        txt = ("curl --url 'https://x/y' \\\n  -H 'user-agent: UA/1' \\\n"
               "  -b 'a=1; b=2' \\\n  -H 'origin: z'")
        got = a.load_cookie_header(self._write(txt))
        self.assertEqual(got["cookie"], "a=1; b=2")
        self.assertEqual(got["user_agent"], "UA/1")

    def test_curl_cookie_header(self):
        txt = "curl --url 'https://x/y' -H 'cookie: a=1; b=2'"
        self.assertEqual(a.load_cookie_header(self._write(txt))["cookie"], "a=1; b=2")

    def test_empty_or_cookieless_raises(self):
        with self.assertRaises(a.AdminPortalError):
            a.load_cookie_header(self._write(""))
        with self.assertRaises(a.AdminPortalError):
            a.load_cookie_header(self._write("curl --url 'https://x/y'"))


class FetchTests(unittest.TestCase):
    AUTH = {"cookie": "a=1", "user_agent": "UA"}

    def _resp(self, status, text=""):
        class R:
            status_code = status
        r = R()
        r.text = text
        return r

    def test_redirect_means_session_expired(self):
        with patch("requests.get", return_value=self._resp(302)):
            with self.assertRaises(a.AdminSessionExpired):
                a.fetch_station_page(1, self.AUTH)

    def test_200_without_station_markup_means_expired(self):
        with patch("requests.get", return_value=self._resp(200, "<html>captcha</html>")):
            with self.assertRaises(a.AdminSessionExpired):
                a.fetch_station_page(1, self.AUTH)

    def test_500_is_generic_error(self):
        with patch("requests.get", return_value=self._resp(500)):
            with self.assertRaises(a.AdminPortalError) as cm:
                a.fetch_station_page(1, self.AUTH)
            self.assertNotIsInstance(cm.exception, a.AdminSessionExpired)

    def test_success_returns_text(self):
        html = '<div id="sessionsContainer">x'
        with patch("requests.get", return_value=self._resp(200, html)) as g:
            self.assertEqual(a.fetch_station_page(7, self.AUTH), html)
            self.assertIn("/index.php/station_info_view/7", g.call_args[0][0])


if __name__ == "__main__":
    unittest.main()


class RotationTests(unittest.TestCase):
    """Cookie rotation, persisted state, and seed fallback."""

    PAGE = '<div id="sessionsContainer"> No active sessions. <script></script>'

    @staticmethod
    def _resp(status, text="", cookies=None):
        class C:
            def __init__(self, n, v):
                self.name, self.value = n, v

        class R:
            pass

        r = R()
        r.status_code, r.text = status, text
        r.cookies = [C(n, v) for n, v in (cookies or {}).items()]
        return r

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.state = self.dir / "state"
        self.seed = {"cookie": "auth-session=seed; keep=1", "user_agent": "UA/seed"}

    def test_rotated_cookies_are_merged_and_saved(self):
        seen = []

        def fake_get(url, headers, **kw):
            seen.append(headers["Cookie"])
            return self._resp(200, self.PAGE, {"auth-session": "rot1", "new": "x"})

        with patch("requests.get", side_effect=fake_get):
            out = a.fetch_sessions_for_stations([1, 2], self.seed, self.state)
        self.assertEqual(out, {"1": {}, "2": {}})
        # 2nd station already uses the rotated cookie from the 1st response
        self.assertEqual(seen[0], "auth-session=seed; keep=1")
        self.assertIn("auth-session=rot1", seen[1])
        saved = self.state.read_text()
        self.assertIn("auth-session=rot1", saved)
        self.assertIn("keep=1", saved)
        self.assertIn("new=x", saved)
        self.assertEqual(oct(self.state.stat().st_mode & 0o777), "0o600")

    def test_saved_state_preferred_then_seed_fallback(self):
        self.state.write_text("auth-session=old")
        calls = []

        def fake_get(url, headers, **kw):
            calls.append((headers["Cookie"], headers["User-Agent"]))
            if headers["Cookie"] == "auth-session=old":
                return self._resp(302)  # saved cookies died
            return self._resp(200, self.PAGE, {"auth-session": "fresh"})

        with patch("requests.get", side_effect=fake_get):
            out = a.fetch_sessions_for_stations([1], self.seed, self.state)
        self.assertEqual(out, {"1": {}})
        self.assertEqual(calls[0], ("auth-session=old", "UA/seed"))  # seed's UA reused
        self.assertEqual(calls[1][0], self.seed["cookie"])
        self.assertIn("auth-session=fresh", self.state.read_text())

    def test_all_credentials_rejected_raises_and_keeps_state_untouched(self):
        self.state.write_text("auth-session=old")
        with patch("requests.get", return_value=self._resp(403)):
            with self.assertRaises(a.AdminSessionExpired):
                a.fetch_sessions_for_stations([1], self.seed, self.state)
        self.assertEqual(self.state.read_text(), "auth-session=old")

    def test_one_bad_station_does_not_lose_the_others(self):
        def fake_get(url, headers, **kw):
            if url.endswith("/2"):
                return self._resp(500)
            return self._resp(200, self.PAGE)

        with patch("requests.get", side_effect=fake_get):
            out = a.fetch_sessions_for_stations([1, 2, 3], self.seed, self.state)
        self.assertEqual(sorted(out), ["1", "3"])  # 2 left out, not reported idle

    def test_no_stations_makes_no_requests(self):
        with patch("requests.get") as g:
            self.assertEqual(a.fetch_sessions_for_stations([], self.seed, self.state), {})
        g.assert_not_called()

    def test_env_seed_precedence_and_disabled_default(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(a.load_seed_auth_from_env())
        f = self.dir / "c.txt"
        f.write_text("file=1")
        with patch.dict("os.environ", {"CHARGEPOINT_ADMIN_COOKIE_FILE": str(f)}, clear=True):
            self.assertEqual(a.load_seed_auth_from_env()["cookie"], "file=1")
        with patch.dict("os.environ", {"CHARGEPOINT_ADMIN_COOKIE": "env=1",
                                       "CHARGEPOINT_ADMIN_COOKIE_FILE": str(f)}, clear=True):
            self.assertEqual(a.load_seed_auth_from_env()["cookie"], "env=1")
