import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import poller


class NormalizePortStatusTests(unittest.TestCase):
    def test_unavailable_is_not_misclassified_as_available(self):
        for status, status_v2 in (
            ("UNAVAILABLE", ""),
            ("unavailable", ""),
            ("", "UNAVAILABLE"),
            ("FAULT", ""),
            ("OFFLINE", ""),
        ):
            with self.subTest(status=status, status_v2=status_v2):
                self.assertEqual(
                    poller._normalize_port_status(status, status_v2),
                    "Unavailable",
                )

    def test_available_and_charging_still_map_correctly(self):
        self.assertEqual(poller._normalize_port_status("AVAILABLE", ""), "Available")
        self.assertEqual(poller._normalize_port_status("CHARGING", ""), "Charging")
        self.assertEqual(poller._normalize_port_status("IN_USE", ""), "Charging")
        self.assertEqual(poller._normalize_port_status("fully_charged", ""), "Complete")

    def test_not_charging_and_inactive_avoid_substring_false_positives(self):
        self.assertEqual(poller._normalize_port_status("NOT_CHARGING", ""), "Available")
        self.assertEqual(poller._normalize_port_status("INACTIVE", ""), "Inactive")


class HomeChargerStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_not_charging_while_plugged_in_is_complete(self):
        client = SimpleNamespace(
            get_home_charger_status=AsyncMock(
                return_value=SimpleNamespace(
                    charging_status="NOT_CHARGING",
                    is_plugged_in=True,
                    brand="ChargePoint",
                    model="Home Flex",
                )
            )
        )

        payload = await poller.fetch_home_charger_status(client, 99)

        self.assertEqual(payload["ports"]["1"], "Complete")

    async def test_not_charging_while_unplugged_is_available(self):
        client = SimpleNamespace(
            get_home_charger_status=AsyncMock(
                return_value=SimpleNamespace(
                    charging_status="NOT_CHARGING",
                    is_plugged_in=False,
                    brand="ChargePoint",
                    model="Home Flex",
                )
            )
        )

        payload = await poller.fetch_home_charger_status(client, 99)

        self.assertEqual(payload["ports"]["1"], "Available")


class OccupancyClearingTests(unittest.TestCase):
    def test_unavailable_port_does_not_clear_like_available(self):
        """Unavailable must stay occupied-bucket-adjacent: not 'available'."""
        self.assertEqual(
            poller._session_occupancy_bucket(
                poller._normalize_port_status("UNAVAILABLE", "")
            ),
            "occupied",
        )
        self.assertEqual(
            poller._session_occupancy_bucket(
                poller._normalize_port_status("AVAILABLE", "")
            ),
            "available",
        )

    def test_unavailable_status_preserves_session(self):
        prev_root = {
            "123": {
                "ports": {"1": "Charging"},
                "port_sessions": {"1": {"started_at": "2026-07-25T10:00:00+00:00"}},
            }
        }
        stations = {
            "123": {
                "ports": {
                    "1": poller._normalize_port_status("UNAVAILABLE", ""),
                }
            }
        }

        poller.enrich_stations_with_port_sessions(prev_root, stations)

        self.assertEqual(
            stations["123"]["port_sessions"]["1"]["started_at"],
            "2026-07-25T10:00:00+00:00",
        )


class AdminSessionMergeTests(unittest.TestCase):
    NOW = poller.datetime(2026, 11, 20, 23, 0, 0, tzinfo=poller.timezone.utc)
    ADMIN = {
        "99037": {
            "2": {
                "driver_initials": "CK",
                "started_at": "2026-11-20T22:32:43+00:00",
                "elapsed_seconds": 1637,
                "energy_kwh": 6.57,
                "power_kw": 7.2,
                "voltage_v": 211.8,
                "current_a": 30.5,
            }
        }
    }

    def _stations(self, port2="Charging"):
        return {
            "99037": {
                "source": "public",
                "ports": {"1": "Available", "2": port2},
                # as produced by enrich_stations_with_port_sessions (first-seen guess)
                "port_sessions": {"2": {"started_at": "2026-11-20T22:50:00+00:00"}}
                if port2 != "Available"
                else {},
            }
        }

    def test_admin_start_time_and_details_replace_first_seen_guess(self):
        st = self._stations()
        poller.enrich_stations_with_admin_sessions(st, self.ADMIN, self.NOW)
        sub = st["99037"]["port_sessions"]["2"]
        self.assertEqual(sub["started_at"], "2026-11-20T22:32:43+00:00")
        self.assertEqual(sub["started_source"], "admin")
        self.assertEqual(sub["driver_initials"], "CK")
        self.assertEqual(sub["current_a"], 30.5)
        self.assertEqual(sub["power_kw"], 7.2)
        self.assertEqual(sub["admin_observed_at"], self.NOW.isoformat())

    def test_never_exposes_name_like_fields(self):
        st = self._stations()
        poller.enrich_stations_with_admin_sessions(st, self.ADMIN, self.NOW)
        self.assertEqual(
            set(st["99037"]["port_sessions"]["2"]),
            {"started_at", "started_source", "driver_initials", "energy_kwh",
             "power_kw", "voltage_v", "current_a", "admin_observed_at"},
        )

    def test_available_port_ignores_stale_admin_row(self):
        st = self._stations(port2="Available")
        poller.enrich_stations_with_admin_sessions(st, self.ADMIN, self.NOW)
        self.assertEqual(st["99037"]["port_sessions"], {})

    def test_future_or_bad_start_time_keeps_first_seen(self):
        for bad in ("2026-11-21T23:00:00+00:00", "not-a-date", None):
            with self.subTest(bad=bad):
                st = self._stations()
                admin = {"99037": {"2": {**self.ADMIN["99037"]["2"], "started_at": bad}}}
                poller.enrich_stations_with_admin_sessions(st, admin, self.NOW)
                sub = st["99037"]["port_sessions"]["2"]
                self.assertEqual(sub["started_at"], "2026-11-20T22:50:00+00:00")
                self.assertNotIn("started_source", sub)
                self.assertEqual(sub["current_a"], 30.5)  # details still merged

    def test_none_values_are_omitted(self):
        st = self._stations()
        admin = {"99037": {"2": {"started_at": None, "current_a": None,
                                 "driver_initials": "", "power_kw": 0.0}}}
        poller.enrich_stations_with_admin_sessions(st, admin, self.NOW)
        sub = st["99037"]["port_sessions"]["2"]
        self.assertNotIn("current_a", sub)
        self.assertNotIn("driver_initials", sub)
        self.assertEqual(sub["power_kw"], 0.0)  # zero is a real value

    def test_errored_unknown_and_empty_inputs_are_safe(self):
        st = {"1": {"error": "boom"}, "2": {"ports": {"1": "Charging"}}}
        poller.enrich_stations_with_admin_sessions(
            st, {"1": {"1": {"current_a": 1}}, "2": {"1": {"current_a": 1}}, "9": {}}, self.NOW
        )
        self.assertNotIn("port_sessions", st["1"])
        self.assertNotIn("port_sessions", st["2"])
        poller.enrich_stations_with_admin_sessions(st, {}, self.NOW)
        poller.enrich_stations_with_admin_sessions(st, None, self.NOW)

    def test_real_start_survives_policy_and_next_poll(self):
        # Admin outage on the next poll: prior real start is carried by first-seen logic.
        st = self._stations()
        poller.enrich_stations_with_admin_sessions(st, self.ADMIN, self.NOW)
        prev_root = {"99037": dict(st["99037"])}
        nxt = {"99037": {"source": "public", "ports": {"1": "Available", "2": "Charging"}}}
        poller.enrich_stations_with_port_sessions(prev_root, nxt)
        self.assertEqual(
            nxt["99037"]["port_sessions"]["2"]["started_at"], "2026-11-20T22:32:43+00:00"
        )
