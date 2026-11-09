"""The lap-time model (f1hub/lapmodel.py) on synthetic races with known answers - offline."""
import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

from f1hub import dataset, lapmodel
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)

FUEL, DEG, HARD_OFFSET, TRAFFIC_COST, NOISE = -0.06, {"MEDIUM": 0.08, "HARD": 0.04, "SOFT": 0.12}, 0.5, 0.4, 0.15


def _race(db, key, rnd, plan, seed=1, cliff=None):
    """Write a race: plan(driver index) -> [(compound, first lap, last lap)]. Driver i is i * 0.2 s a lap slower.
    cliff: (compound, age, extra s per lap per lap) beyond that tyre age."""
    rng = random.Random(seed)
    db.execute("INSERT INTO sessions (session_key, year, round, meeting_name, circuit, session_name, session_type, final) "
               "VALUES (?, 2025, ?, ?, ?, 'Race', 'Race', 1)", (key, rnd, f"GP {rnd}", f"Circuit {rnd}"))
    for i in range(10):
        n = i + 1
        db.execute("INSERT INTO drivers (session_key, driver_number, code, name, team, colour) VALUES (?, ?, ?, ?, ?, ?)",
                   (key, n, f"D{n:02}", f"Driver {n}", f"Team {i // 2}", "#888888"))
        for stint, (comp, a, b) in enumerate(plan(i), 1):
            for lap in range(a, b + 1):
                age = lap - a + 1
                t = 90 + i * 0.2 + FUEL * lap + DEG[comp] * age + (HARD_OFFSET if comp == "HARD" else 0)
                if cliff and comp == cliff[0] and age > cliff[1]:
                    t += cliff[2] * (age - cliff[1])
                gap = rng.choice([0.8, 1.2, 2.5, 3.0, 4.0])
                t += (TRAFFIC_COST if gap < lapmodel.TRAFFIC else 0) + rng.gauss(0, NOISE)
                db.execute("INSERT INTO laps (session_key, driver_number, lap_number, lap_time, stint, compound, tyre_age, "
                           "clean, position, interval_ahead, track_temp, rainfall) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 40, 0)",
                           (key, n, lap, round(t, 3), stint, comp, age, n + 1, gap))


def two_stop_pits(i):          # medium then hard, stopping on different laps
    stop = 15 + 2 * i
    return [("MEDIUM", 2, stop), ("HARD", stop + 2, 55)]


class LapModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = dataset.connect(Path(self.tmp.name) / "t.sqlite")
        self.db.row_factory = sqlite3.Row

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def session(self, key):
        return self.db.execute("SELECT * FROM sessions WHERE session_key = ?", (key,)).fetchone()

    def assertWithin(self, est, truth, ses=3.5):
        self.assertIsNotNone(est["se"])
        self.assertLess(abs(est["est"] - truth), ses * est["se"] + 1e-3, f"{est} v {truth}")

    def test_recovers_known_effects(self):
        _race(self.db, 1, 1, two_stop_pits)
        f = lapmodel.fit_race(self.db, self.session(1))
        self.assertNotIn("skipped", f)
        self.assertWithin(f["fuel"], FUEL)
        self.assertWithin(f["traffic"], TRAFFIC_COST)
        c = {x["compound"]: x for x in f["compounds"]}
        self.assertTrue(c["MEDIUM"]["reference"])
        self.assertWithin(c["MEDIUM"]["deg"], DEG["MEDIUM"])
        self.assertWithin(c["HARD"]["deg"], DEG["HARD"])
        self.assertWithin(c["HARD"]["offset"], HARD_OFFSET)
        self.assertLess(abs(f["sigma"] - NOISE), 0.05)
        # driver pace: 0.2 s a lap apart, quickest first
        self.assertEqual([d["number"] for d in f["drivers"]], list(range(1, 11)))
        for d in f["drivers"][1:]:
            self.assertWithin({"est": d["gap"], "se": d["se"]}, (d["number"] - 1) * 0.2)
        self.assertEqual(f["left_out"], [])
        self.assertIsNone(c["HARD"]["cliff"])

    def test_cliff_found(self):
        _race(self.db, 1, 1, lambda i: [("MEDIUM", 2, 10 + i), ("HARD", 12 + i, 60)], cliff=("HARD", 25, 0.15))
        f = lapmodel.fit_race(self.db, self.session(1))
        cliff = next(x for x in f["compounds"] if x["compound"] == "HARD")["cliff"]
        self.assertIsNotNone(cliff)
        self.assertLessEqual(abs(cliff["age"] - 25), 3)
        self.assertLess(abs(cliff["extra"] - 0.15), 0.05)

    def test_thin_compound_left_out(self):
        """One car's five laps on softs: too little to measure (one stint can't give an error), so left out."""
        _race(self.db, 1, 1, lambda i: two_stop_pits(i) if i else [("MEDIUM", 2, 25), ("HARD", 27, 50), ("SOFT", 51, 55)])
        f = lapmodel.fit_race(self.db, self.session(1))
        self.assertEqual(f["left_out"], [{"compound": "SOFT", "laps": 5, "stints": 1}])
        self.assertEqual({c["compound"] for c in f["compounds"]}, {"MEDIUM", "HARD"})

    def test_one_stint_each_fixes_fuel(self):
        """Nobody stops: fuel and tyre age are the same thing. The season fixes fuel at the other races' figure."""
        _race(self.db, 1, 1, two_stop_pits)
        _race(self.db, 2, 2, lambda i: [("MEDIUM" if i % 2 else "HARD", 2, 40)], seed=2)
        races = self.db.execute("SELECT * FROM sessions ORDER BY round").fetchall()
        self.assertTrue(lapmodel.fit_race(self.db, races[1]).get("singular"))
        teams = type("T", (), {"colour": {}})()
        from f1hub.seasonreport import _pct_series
        s = lapmodel.season(self.db, races, {}, teams, _pct_series)
        r2 = s["races"][1]
        self.assertTrue(r2["fuel"]["fixed"])
        self.assertEqual(r2["fuel"]["est"], s["fuel"]["est"])
        hard = next(c for c in r2["compounds"] if c["compound"] == "HARD")
        self.assertIsNone(hard["offset"])           # nobody ran both compounds: left in driver pace
        self.assertIsNone(hard["cliff"])
        self.assertWithin(s["deg"]["MEDIUM"], DEG["MEDIUM"])    # from race 1 only

    def test_wet_race_skipped(self):
        _race(self.db, 1, 1, two_stop_pits)
        self.db.execute("UPDATE laps SET rainfall = 1")
        f = lapmodel.fit_race(self.db, self.session(1))
        self.assertIn("skipped", f)

    def test_steps_and_maths(self):
        flat = [[r, 1.0 + (0.05 if r % 2 else -0.05)] for r in range(1, 11)]
        self.assertIsNone(lapmodel._steps(flat))
        step = lapmodel._steps([[r, v if r < 6 else v - 0.6] for r, v in flat])
        self.assertEqual(step["round"], 6)
        self.assertAlmostEqual(step["change"], -0.6, delta=0.05)
        inv = lapmodel._invert([[4.0, 2.0], [2.0, 3.0]])
        self.assertAlmostEqual(inv[0][0], 0.375)
        self.assertAlmostEqual(inv[0][1], -0.25)
        with self.assertRaises(lapmodel.Singular):
            lapmodel._invert([[1.0, 2.0], [2.0, 4.0]])
        self.assertEqual(lapmodel._pooled([(1.0, 1.0), (3.0, 1.0)]), {"est": 2.0, "se": 0.7071})


if __name__ == "__main__":
    unittest.main()
