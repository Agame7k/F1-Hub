"""Season report (f1hub/seasonreport.py) on a dataset compiled from the test season in test_dataset - offline."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from f1hub import dataset, seasonreport
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)
from test_dataset import FakeSeason, FakeSource


class SeasonReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.patches = [mock.patch.object(dataset, "Source", FakeSource), mock.patch.object(dataset, "Season", FakeSeason),
                       mock.patch.object(dataset, "DATA_DIR", Path(cls.tmp.name))]
        for p in cls.patches:
            p.start()
        dataset.compile_season(2025)
        cls.r = seasonreport.report(2025)

    @classmethod
    def tearDownClass(cls):
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def test_status_and_years(self):
        st = seasonreport.status(2025)
        self.assertEqual((st["exists"], st["sessions"], st["rounds"], st["laps"]), (True, 2, 1, 12))
        self.assertEqual(seasonreport.status(2024), {"year": 2024, "exists": False})
        self.assertEqual(seasonreport.compiled_years(), [2025])

    def test_headline(self):
        h = self.r["headline"]
        self.assertEqual((h["rounds"], h["laps"], h["overtakes"], h["stops"], h["winners"], h["polesitters"]), (1, 12, 1, 1, 1, 1))
        self.assertEqual((h["sc"], h["vsc"], h["red"]), (0, 1, 0))

    def test_racecraft(self):
        rc = {e["code"]: e for e in self.r["racecraft"]}
        self.assertEqual((rc["VER"]["made"], rc["VER"]["led"], rc["VER"]["lap1_total"]), (1, 6, 1))   # grid 2 -> P1 on lap 1
        self.assertEqual((rc["HAM"]["lost"], rc["HAM"]["net"]), (1, -1))

    def test_tyres_pits_and_neutralisations(self):
        t = self.r["tyres"]
        self.assertEqual(t["rounds"][0]["stops_per_car"], 0.5)
        self.assertEqual(t["rounds"][0]["laps"]["HARD"], 3)
        self.assertEqual([(p["team"], p["median"], p["stops"]) for p in self.r["pits"]["teams"]], [("Ferrari", 2.3, 1)])
        self.assertEqual(self.r["neutral"][0]["vsc"], 1)

    def test_too_little_for_pace(self):
        """Two cars and six laps: not enough for a pace figure, and not left half-drawn."""
        self.assertEqual(self.r["race_pace"]["teams"], [])
        self.assertEqual(self.r["quali_pace"]["teams"], [])

    def test_spoiler_cut(self):
        self.assertEqual(seasonreport.round_before(2025, 1254), 0)
        self.assertIsNone(seasonreport.round_before(2025, 9999))
        cut = seasonreport.report(2025, upto=0)
        self.assertEqual((cut["upto"], cut["headline"]["rounds"], cut["racecraft"]), (0, 0, []))

    def test_pace_gap_maths(self):
        teams = type("T", (), {"colour": {"A": "#f00", "B": "#0f0", "C": "#00f"}})()
        rows = seasonreport._pct_series({1: {"A": 90.0, "B": 90.9, "C": 91.8}, 2: {"A": 91.0, "B": 91.0, "C": 92.0},
                                         3: {"A": 90.0, "B": 91.0}}, teams)       # round 3: too few teams, left out
        self.assertEqual([(r["team"], r["points"], r["best"]) for r in rows],
                         [("A", [[1, 0.0], [2, 0.0]], 2), ("B", [[1, 1.0], [2, 0.0]], 1), ("C", [[1, 2.0], [2, 1.099]], 0)])


if __name__ == "__main__":
    unittest.main()
