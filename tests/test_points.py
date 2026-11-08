"""Points by season: the fastest-lap point (2019-2024), and drivers who raced under two numbers."""
import unittest
from datetime import datetime
from unittest import mock

from f1hub import api
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)
from f1hub.analysis import _norm_openf1, season_stats
from f1hub.championship import max_remaining
from f1hub.rules import rules_for
from f1hub.season import Season, fastest_lap_point

GP = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]


def race(key, meeting, day):
    return {"session_key": key, "meeting_key": meeting, "session_name": "Race", "session_type": "Race",
            "date_start": f"2024-03-{day:02d}T15:00:00+00:00", "date_end": f"2024-03-{day:02d}T17:00:00+00:00"}


SESS = [race(2, 10, 2), race(4, 20, 9)]
MEET = [{"meeting_key": k, "meeting_name": f"GP {k}", "country_name": "C", "location": "X", "circuit_short_name": "Y",
         "date_start": f"2024-03-{d:02d}T00:00:00+00:00"} for k, d in ((10, 1), (20, 8))]
DRV = [{"driver_number": 1, "name_acronym": "AAA", "full_name": "Driver A", "team_name": "Red", "team_colour": "FF0000"},
       {"driver_number": 2, "name_acronym": "BBB", "full_name": "Driver B", "team_name": "Red", "team_colour": "FF0000"},
       {"driver_number": 3, "name_acronym": "CCC", "full_name": "Driver C", "team_name": "Blue", "team_colour": "0000FF"}]


def rows(order, points=None, dsq=()):
    return [{"driver_number": n, "position": i + 1, "dnf": False, "dns": False, "dsq": n in dsq,
             "points": (points or {}).get(n, GP[i] if i < 10 and n not in dsq else 0)} for i, n in enumerate(order)]


def fake(results, laps=None, drivers=None):
    def get(endpoint, ttl=None, **p):
        if endpoint == "meetings":
            return MEET
        if endpoint == "sessions":
            return SESS
        if endpoint == "session_result":
            return results.get(p["session_key"], [])
        if endpoint == "drivers":
            return (drivers or {}).get(p["session_key"], DRV)
        if endpoint == "laps":
            return (laps or {}).get(p["session_key"], [])
        return []
    return get


class SeasonRules(unittest.TestCase):
    def test_fastest_lap_years(self):
        self.assertFalse(rules_for(2018)["fastest_lap_point"])
        self.assertTrue(rules_for(2019)["fastest_lap_point"])
        self.assertTrue(rules_for(2024)["fastest_lap_point"])
        self.assertFalse(rules_for(2025)["fastest_lap_point"])
        self.assertEqual(rules_for(2021)["sprint_points"], [3, 2, 1])

    def test_clinch_maths_counts_the_extra_point(self):
        self.assertEqual(max_remaining([False, False], rules=rules_for(2024)), 52)
        self.assertEqual(max_remaining([False, False], rules=rules_for(2025)), 50)


class FastestLapPoint(unittest.TestCase):
    def test_read_from_openf1_points(self):
        r = rows([1, 3, 2], points={2: 16})            # P3 scored 15 + 1
        with mock.patch.object(api, "get", side_effect=AssertionError("no lookup needed")):
            self.assertEqual(fastest_lap_point(r, SESS[0], rules_for(2024)), 2)

    def test_nobody_in_the_top_ten(self):
        with mock.patch.object(api, "get", side_effect=AssertionError("no lookup needed")):
            self.assertIsNone(fastest_lap_point(rows([1, 3, 2]), SESS[0], rules_for(2024)))

    def test_broken_points_fall_back_to_the_laps(self):
        broken = rows([3, 2, 1], points={2: 0, 1: 0})   # like 2023 USA: 0 for P2 onwards
        laps = {2: [{"driver_number": 1, "lap_duration": 90.1}, {"driver_number": 3, "lap_duration": 90.5},
                    {"driver_number": 2, "lap_duration": None}]}
        with mock.patch.object(api, "get", fake({}, laps)):
            self.assertEqual(fastest_lap_point(broken, SESS[0], rules_for(2024)), 1)

    def test_disqualified_driver_scores_nothing(self):
        broken = rows([3, 2, 1], points={2: 0}, dsq=(1,))
        laps = {2: [{"driver_number": 1, "lap_duration": 90.1}, {"driver_number": 3, "lap_duration": 90.5}]}
        with mock.patch.object(api, "get", fake({}, laps)):
            self.assertIsNone(fastest_lap_point(broken, SESS[0], rules_for(2024)))


class SeasonWithFastestLaps(unittest.TestCase):
    def setUp(self):
        results = {2: rows([1, 3, 2], points={2: 16}),                 # BBB: P3 and the fastest lap
                   4: rows([3, 2, 1], points={2: 0, 1: 0})}            # broken points: the laps decide
        laps = {4: [{"driver_number": 1, "lap_duration": 80.0}, {"driver_number": 3, "lap_duration": 80.4}]}
        with mock.patch.object(api, "get", fake(results, laps)):
            self.s = Season(2024).load()

    def test_standings(self):
        drivers, teams, _ = self.s.standings()
        self.assertEqual({e.code: e.points for e in drivers}, {"AAA": 25 + 15 + 1, "CCC": 18 + 25, "BBB": 16 + 18})
        self.assertEqual({t.name: t.points for t in teams}, {"Red": 41 + 34, "Blue": 43})

    def test_season_stats_agrees(self):
        with mock.patch.object(api, "get", fake({})):          # (the starting grids)
            st = season_stats(_norm_openf1(self.s), 2024)
        self.assertEqual({d["code"]: d["points"] for d in st["drivers"]}, {"AAA": 41, "CCC": 43, "BBB": 34})
        self.assertEqual({t["team"]: t["points"] for t in st["teams"]}, {"Red": 75, "Blue": 43})
        aaa = next(d for d in st["drivers"] if d["code"] == "AAA")
        self.assertEqual(aaa["by_round"], [[1, 25], [2, 41]])

    def test_2025_ignores_the_extra_point(self):
        with mock.patch.object(api, "get", fake({2: rows([1, 3, 2], points={2: 16})})):
            s = Season(2025).load()
        self.assertEqual({e.code: e.points for e in s.standings()[0]}, {"AAA": 25, "CCC": 18, "BBB": 15})


class DriverUnderTwoNumbers(unittest.TestCase):
    """2024: Bearman raced as #38 for Ferrari, then #50 for Haas - one driver in the standings."""
    def test_one_entry(self):
        stand_in = [dict(DRV[0], driver_number=38)] + DRV[1:]
        results = {2: rows([38, 3, 2]), 4: rows([3, 2, 1])}
        with mock.patch.object(api, "get", fake(results, drivers={2: stand_in})):
            s = Season(2024).load()
        drivers, _, by_number = s.standings()
        self.assertEqual([e.code for e in drivers].count("AAA"), 1)
        self.assertIs(by_number[38], by_number[1])
        self.assertEqual(by_number[1].points, 25 + 15)


class ReplayFastestLap(unittest.TestCase):
    def test_points_follow_the_fastest_lap_so_far(self):
        from f1hub.replay import ReplayTracker
        t = ReplayTracker.__new__(ReplayTracker)
        t.session = {"year": 2024}
        t.is_sprint = False
        T = datetime.fromisoformat("2024-03-02T16:00:00+00:00").timestamp()

        class Store:
            def get(self, endpoint, upto):
                if endpoint == "position":
                    return [{"driver_number": 1, "position": 1, "_ts": T - 100},
                            {"driver_number": 2, "position": 2, "_ts": T - 100}]
                return [{"driver_number": 2, "lap_duration": 80.0, "_ts": T - 300},
                        {"driver_number": 1, "lap_duration": 81.0, "_ts": T - 200},
                        {"driver_number": 1, "lap_duration": 79.0, "_ts": T - 50}]    # still running at T
        t.store = Store()
        champ = [{"driver_number": 1, "points_start": 100}, {"driver_number": 2, "points_start": 90}]
        out = {r["driver_number"]: r["points_current"] for r in t._at_time("championship_drivers", champ, T)}
        self.assertEqual(out, {1: 125, 2: 90 + 18 + 1})


if __name__ == "__main__":
    unittest.main()
