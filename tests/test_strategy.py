"""Strategy simulation (f1hub/strategy.py) on synthetic races with known answers - offline."""
import math
import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

from f1hub import dataset, strategy as st
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)


class NoPass(st.PassModel):
    def p(self, adv, circuit=None):
        return 0.0


class AlwaysPass(st.PassModel):
    def p(self, adv, circuit=None):
        return 1.0


def race(laps=50, pace=(0.0, 0.5, 1.0), pass_model=None, sc=0.0, vsc=0.0, sigma=0.0, pit_loss=22.0, deg=None, **kw):
    cars = [{"number": i + 1, "code": f"D{i + 1}", "team": f"T{i}", "colour": "#888", "pace": p} for i, p in enumerate(pace)]
    comp = deg or {"SOFT": (-0.5, 0.10), "MEDIUM": (0.0, 0.06), "HARD": (0.4, 0.03)}
    return st.Race(laps, 90.0, cars, comp, -0.05, sigma, pit_loss, pit_loss * 0.5, sc, vsc, pass_model or st.PassModel(),
                   "Test", traffic=0.0, **kw)


def grid(r, gaps=None, compound="MEDIUM"):
    return {"lap": 0, "cars": {c["number"]: {"time": (gaps or {}).get(c["number"], i * 0.3), "compound": compound, "age": 0,
                                             "used": {compound}, "stops": 0} for i, c in enumerate(r.cars)}}


class Simulator(unittest.TestCase):
    def test_same_seed_same_answer(self):
        r = race(sigma=0.4, sc=0.02)
        a = st.simulate(r, grid(r), {1: [(25, "HARD")], 2: [(25, "HARD")], 3: [(25, "HARD")]}, runs=20, seed=3)
        b = st.simulate(r, grid(r), {1: [(25, "HARD")], 2: [(25, "HARD")], 3: [(25, "HARD")]}, runs=20, seed=3)
        self.assertEqual(a, b)

    def test_no_passing_holds_the_order(self):
        # the quickest car starts last and can never pass: the order stays as it started
        r = race(pace=(1.0, 0.5, 0.0), pass_model=NoPass())
        pos, _ = st.simulate(r, grid(r), {}, runs=5)
        self.assertEqual([pos[n][0] for n in (1, 2, 3)], [1, 2, 3])

    def test_passing_sorts_by_pace(self):
        r = race(pace=(1.0, 0.5, 0.0), pass_model=AlwaysPass())
        pos, times = st.simulate(r, grid(r), {}, runs=5)
        self.assertEqual([pos[n][0] for n in (1, 2, 3)], [3, 2, 1])
        # in free air the winner's time is exactly the sum of its laps
        expect = sum(90.0 + 0.0 + 0.06 * (a + 1) - 0.05 * lap for a, lap in zip(range(50), range(1, 51))) + 0.6
        self.assertAlmostEqual(times[3][0], expect, places=6)

    def test_pit_stop_costs_the_pit_loss(self):
        r = race(pace=(0.0,), laps=40)
        _, t0 = st.simulate(r, grid(r), {}, runs=1)
        _, t1 = st.simulate(r, grid(r), {1: [(20, "MEDIUM")]}, runs=1)
        # stop + fresh tyres: the pit loss, less the wear saved on the second half
        saved = sum(0.06 * (a + 1) for a in range(20, 40)) - sum(0.06 * (a + 1) for a in range(20))
        self.assertAlmostEqual(t1[1][0] - t0[1][0], 22.0 - saved, places=6)

    def test_safety_car_bunches_the_field_and_cheapens_stops(self):
        r = race(pace=(0.0, 0.0), sc=1.0, laps=12)
        state = grid(r, gaps={1: 0.0, 2: 8.0})
        _, t = st.simulate(r, state, {}, runs=1)
        self.assertAlmostEqual(t[2][0] - t[1][0], st.SC_GAP, places=6)
        # a stop due within SC_WINDOW laps is taken under the first Safety Car lap instead, for less
        pos, _ = st.simulate(r, state, {1: [(1 + st.SC_WINDOW, "HARD")]}, runs=1)
        self.assertEqual(pos[1][0], 2)     # 11 s under the SC drops the leader behind a car 8 s back

    def test_retirements(self):
        r = race(dnf_rate=1.0)
        pos, _ = st.simulate(r, grid(r), {}, runs=3)
        self.assertTrue(all(p is None for v in pos.values() for p in v))


class Planner(unittest.TestCase):
    def test_candidates_are_legal(self):
        r = race(laps=40, max_stint={"SOFT": 15, "MEDIUM": 25, "HARD": 35})
        cs = st.candidates(r, step=1)
        self.assertTrue(cs)
        for plan, c0, _ in cs:
            used = {c0} | {c for _, c in plan}
            self.assertGreaterEqual(len(used), 2, plan)
            laps = [0] + [p for p, _ in plan] + [40]
            comps = [c0] + [c for _, c in plan]
            for (a, b), c in zip(zip(laps, laps[1:]), comps):
                self.assertGreaterEqual(b - a, st.MIN_STINT if a else st.MIN_STINT, (plan, c0))
                self.assertLessEqual(b - a, r.max_stint[c], (plan, c0))

    def test_one_stop_when_tyres_last(self):
        r = race(laps=50, deg={"SOFT": (-0.3, 0.05), "MEDIUM": (0.0, 0.02), "HARD": (0.3, 0.01)})
        best = st.plan(r)["best"][0]
        self.assertEqual(len(best["stops"]), 1, best)

    def test_two_stops_when_wear_is_high(self):
        r = race(laps=60, pit_loss=15.0, deg={"SOFT": (-0.6, 0.30), "MEDIUM": (0.0, 0.22), "HARD": (0.5, 0.18)})
        best = st.plan(r)["best"][0]
        self.assertEqual(len(best["stops"]), 2, best)

    def test_free_air_time_matches_the_simulator(self):
        r = race(pace=(0.0,), laps=40)
        plan = [(18, "HARD")]
        _, t = st.simulate(r, grid(r, compound="MEDIUM"), {1: plan}, runs=1)
        _, base = st.simulate(r, grid(r, compound="MEDIUM"), {}, runs=1)
        nostop = st.expected_time(r, [], "MEDIUM")
        self.assertAlmostEqual(t[1][0] - base[1][0], st.expected_time(r, plan, "MEDIUM") - nostop, places=6)


class PassModelFit(unittest.TestCase):
    def test_logistic_recovers_known_coefficients(self):
        rng = random.Random(2)
        a, b, eff = -2.5, 2.0, {"easy": 0.8, "hard": -0.8}
        rows = []
        for _ in range(20000):
            g = rng.choice(list(eff))
            x = rng.uniform(-1, 2)
            p = 1 / (1 + math.exp(-(a + eff[g] + b * x)))
            rows.append((x, g, 1 if rng.random() < p else 0))
        fa, fb, fe = st._logistic_fit(rows, set(eff), ridge=0.1)
        self.assertAlmostEqual(fb, b, delta=0.15)
        self.assertAlmostEqual(fe["easy"] - fe["hard"], 1.6, delta=0.2)


class Inputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = dataset.connect(Path(self.tmp.name) / "t.sqlite")
        self.db.row_factory = sqlite3.Row

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_pit_loss_against_the_field(self):
        """Ten cars lapping at 90 s on a track getting 0.05 s a lap quicker; three stop, each losing 21 s over the
        in-lap and out-lap. The measured loss is 21 s, not thrown by the track evolution."""
        db = self.db
        db.execute("INSERT INTO sessions (session_key, year, round, circuit, session_name, session_type, final) VALUES (1, 2025, 1, 'X', 'Race', 'Race', 1)")
        for n in range(1, 11):
            for lap in range(1, 41):
                t = 90 + n * 0.1 - 0.05 * lap
                stop = n <= 3 and lap in (20 + n, 21 + n)
                if stop:
                    t += 10.5
                db.execute("INSERT INTO laps (session_key, driver_number, lap_number, lap_time, clean, neutral, pit_in, pit_out) "
                           "VALUES (1, ?, ?, ?, ?, 0, ?, ?)", (n, lap, t, 0 if stop else 1, int(n <= 3 and lap == 20 + n), int(n <= 3 and lap == 21 + n)))
            if n <= 3:
                db.execute("INSERT INTO pits (session_key, driver_number, lap_number, stop) VALUES (1, ?, ?, 1)", (n, 20 + n))
        green, neutral = st.pit_losses(db, 1)
        self.assertEqual(len(green), 3)
        for g in green:
            self.assertAlmostEqual(g, 21.0, places=6)
        self.assertEqual(neutral, [])


class Scoring(unittest.TestCase):
    def test_brier_and_calibration(self):
        pairs = [(0.9, 1), (0.1, 0), (0.6, 1), (0.6, 0)]
        self.assertAlmostEqual(st._brier(pairs), (0.01 + 0.01 + 0.16 + 0.36) / 4, places=4)
        cal = st.calibration(pairs)
        mid = next(b for b in cal if b["lo"] == 0.5)
        self.assertEqual((mid["n"], mid["observed"]), (2, 0.5))

    def test_baseline_falls_with_grid_slot(self):
        hist = [(1, 1), (2, 2), (3, 3), (1, 1), (2, 1)]
        win = [st._baseline(hist, g, 1) for g in range(1, 6)]
        self.assertEqual(win, sorted(win, reverse=True))
        self.assertGreater(st._baseline(hist, 2, 3), st._baseline(hist, 2, 1))

    def test_form_prior_weights_recent_races(self):
        fits = [{"round": r, "reference_lap": 100.0, "drivers": [{"number": 1, "team": "A", "gap": g}, {"number": 2, "team": "A", "gap": 0.0}]}
                for r, g in ((1, 1.0), (2, 1.0), (3, 0.0))]
        f = st.Form(fits, 4, {1: "A", 2: "A"})
        self.assertLess(f.prior(1), 2 / 3)       # the latest race counts most
        self.assertGreater(f.prior(1), 0.0)
        self.assertEqual(f.measured(1), 3)
        newcomer = st.Form(fits, 4, {9: "A"})
        self.assertAlmostEqual(newcomer.prior(9), 0.25, places=6)   # the team's pace, plus a little


if __name__ == "__main__":
    unittest.main()
