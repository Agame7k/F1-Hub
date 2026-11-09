"""Per-corner telemetry (f1hub/telemetry.py, f1hub/cornerreport.py) on a synthetic circuit with known corners - offline."""
import math
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from f1hub import cornerreport, dataset, telemetry
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)

R = 318.31                  # m: a round track 2000 m long
APEXES = (500.0, 1500.0)    # m from the line
VMAX = 300 / 3.6
START = datetime(2025, 6, 1, 14, 0, tzinfo=timezone.utc)


def xy(d):
    a = 2 * math.pi * d / (2 * math.pi * R)
    return R * 10 * math.cos(a), R * 10 * math.sin(a)        # location units are 0.1 m


def profile(vmins, decel):
    """Speed (m/s) and brake flag at every metre: brake to each apex at `decel`, accelerate away at 12 m/s2."""
    L = round(2 * math.pi * R)
    out = []
    for m in range(L):
        v, brake = VMAX, False
        for apex, vmin in zip(APEXES, vmins):
            to = (apex - m) % L                # metres until the apex
            since = (m - apex) % L
            vb = math.sqrt(vmin ** 2 + 2 * decel * max(0, to - 15))        # 30 m at the apex at constant speed
            va = math.sqrt(vmin ** 2 + 2 * 12 * max(0, since - 15))
            if vb < v and vb <= va:
                v, brake = vb, True
            elif va < v:
                v, brake = va, False
        out.append((v, brake))
    return out


def driver_columns(prof, laps, t0):
    """Columns as telemetry.fetch_session stores them, for `laps` laps run back to back from t0. Returns
    (columns, [lap times])."""
    L = len(prof)
    t, times, samples = 0.0, [], []
    for _ in range(laps):
        lap_t0 = t
        for m in range(L):
            v, brake = prof[m]
            samples.append((t, m, v, brake))
            t += 1 / v
        times.append(t - lap_t0)
    car = {"t": [], "speed": [], "throttle": [], "brake": [], "gear": [], "rpm": [], "drs": []}
    loc = {"t": [], "x": [], "y": []}
    nxt_c, nxt_l, k = 0.0, 0.1, 0
    for s_t, m, v, brake in samples:            # car data at 4 Hz, location at 4 Hz offset by 0.1 s
        if s_t >= nxt_c:
            car["t"].append(round(s_t * 1000)); car["speed"].append(round(v * 3.6)); car["brake"].append(int(brake))
            car["throttle"].append(0 if brake else 100); car["gear"].append(max(1, min(8, round(v * 3.6 / 40))))
            car["rpm"].append(11000); car["drs"].append(0)
            nxt_c += 0.25
        if s_t >= nxt_l:
            x, y = xy(m)
            loc["t"].append(round(s_t * 1000)); loc["x"].append(round(x)); loc["y"].append(round(y))
            nxt_l += 0.25
    return {"t0": t0, "car": car, "loc": loc}, times


class Telemetry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.patch = mock.patch.object(dataset, "DATA_DIR", Path(cls.tmp.name))
        cls.patch.start()
        db = dataset.connect(dataset.default_path(2025))
        db.execute("INSERT INTO sessions (session_key, year, round, meeting_name, circuit, session_name, session_type, "
                   "date_start, final) VALUES (1, 2025, 1, 'Test GP', 'Roundel', 'Qualifying', 'Qualifying', ?, 1)",
                   (START.isoformat(),))
        t0 = START.timestamp()
        cls.raw = {}
        # driver 2 brakes later (harder) and carries 5 km/h more through the first corner
        for n, vmins, decel in ((1, (100 / 3.6, 160 / 3.6), 40.0), (2, (105 / 3.6, 160 / 3.6), 48.0)):
            db.execute("INSERT INTO drivers VALUES (1, ?, ?, ?, 'Team', '#ff0000')", (n, f"D{n}", f"Driver {n}"))
            cols, times = driver_columns(profile(vmins, decel), 3, t0)
            cls.raw[n] = cols
            at = t0
            for lap, lt in enumerate(times, 1):
                db.execute("INSERT INTO laps (session_key, driver_number, lap_number, date_start, lap_time, pit_out, "
                           "pit_in, deleted, clean) VALUES (1, ?, ?, ?, ?, 0, 0, 0, 1)",
                           (n, lap, datetime.fromtimestamp(at, timezone.utc).isoformat(), round(lt, 3)))
                at += lt
        official = [(1, *xy(APEXES[0] - 30)), (2, *xy(APEXES[1] + 20)), (3, *xy(1000))]    # T3: a flat-out kink
        cls.measured = telemetry.analyse_session(db, 1, cls.raw, official)
        db.commit()
        db.row_factory = sqlite3.Row
        cls.db = db

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.patch.stop()
        cls.tmp.cleanup()

    def corner(self, n, lap, c):
        return self.db.execute("SELECT * FROM tel_corner_laps WHERE driver_number = ? AND lap_number = ? AND corner = ?",
                               (n, lap, c)).fetchone()

    def test_corners_found_and_named(self):
        cs = self.db.execute("SELECT * FROM tel_corners ORDER BY corner").fetchall()
        self.assertEqual([c["label"] for c in cs], ["T1", "T2"])          # the flat-out T3 is no corner
        self.assertEqual([c["kind"] for c in cs], ["slow", "medium"])
        for c, apex in zip(cs, APEXES):    # distance runs from the reference lap's first sample (up to ~20 m past the
            self.assertLess(abs(c["apex"] - apex), 40)       # line), and the apex is anywhere on a 30 m arc
        self.assertEqual(self.measured, 6)
        self.assertLess(abs(self.db.execute("SELECT length FROM tel_sessions").fetchone()[0] - 2000), 10)

    def test_corner_times_add_up_to_the_lap(self):
        for n in (1, 2):
            for lap in (1, 2, 3):
                segs = [r[0] for r in self.db.execute("SELECT seg_time FROM tel_corner_laps WHERE driver_number = ? "
                                                      "AND lap_number = ?", (n, lap))]
                lt = self.db.execute("SELECT lap_time FROM laps WHERE driver_number = ? AND lap_number = ?", (n, lap)).fetchone()[0]
                self.assertAlmostEqual(sum(segs), lt, delta=0.15)

    def test_braking_point_and_apex_speed(self):
        a, b = self.corner(1, 2, 1), self.corner(2, 2, 1)
        # braking from 300 to 100 km/h at 40 m/s2 takes 85.7 m, + 15 m of constant speed to the apex;
        # at 48 m/s2 to 105 km/h: 69.6 + 15 m
        self.assertLess(abs(a["brake_dist"] - 100.7), 15)
        self.assertLess(abs(b["brake_dist"] - 84.6), 15)
        self.assertLess(abs((b["min_speed"] - a["min_speed"]) - 5), 3)
        self.assertGreater(a["peak_decel"], 3)
        self.assertLess(self.corner(2, 2, 1)["seg_time"], a["seg_time"])
        lap = self.db.execute("SELECT * FROM tel_laps WHERE driver_number = 1 AND lap_number = 2").fetchone()
        self.assertEqual(lap["top_speed"], 300)
        self.assertEqual(lap["lico_time"], 0)
        self.assertGreater(lap["brake_pct"], 3)

    def test_report(self):
        r = cornerreport.report(2025)
        self.assertEqual((r["a"], r["b"]), (2, 1))       # the quicker driver first
        self.assertEqual(r["kinds"], {"slow": 1, "medium": 1, "fast": 0})
        self.assertEqual(r["corners"][0]["quickest_code"], "D2")
        self.assertEqual(r["by_kind"][0]["code"], "D2")
        p1, p2 = r["profiles"]["1"]["1"], r["profiles"]["2"]["1"]
        self.assertGreater(p1["brake_dist"] - p2["brake_dist"], 8)
        self.assertEqual(r["laps"], None)              # qualifying: no race stats
        self.assertEqual(r["styles"]["pairs"], [])     # one round: too few for a style

    def test_car_report(self):
        """One team: too few to compare against a field, or to group circuits - empty, not broken."""
        from f1hub import carreport
        r = carreport.report(2025)
        self.assertEqual([x["session_key"] for x in r["sessions"]], [1])
        self.assertEqual((r["cars"], r["circuits"]["groups"]), ([], []))
        self.assertEqual(r["circuits"]["circuits"][0]["corners"], 2)

    def test_no_telemetry(self):
        r = cornerreport.report(2024)
        self.assertFalse(r["exists"])

    def test_straights(self):
        sts = self.db.execute("SELECT * FROM tel_straights ORDER BY straight").fetchall()
        self.assertEqual(len(sts), 2)
        self.assertEqual([st["label"] for st in sts], ["after T1", "after T2"])
        self.assertEqual([st["wraps"] for st in sts], [0, 1])            # T2 to T1 crosses the line
        lap = self.db.execute("SELECT * FROM tel_straight_laps WHERE driver_number = 1 AND lap_number = 2 AND straight = 1").fetchone()
        self.assertEqual((lap["top_speed"], lap["clip_drop"]), (300, 0))
        self.assertGreater(lap["accel"], 30)
        # the wrapped straight is measured on into the next lap, so it isn't cut at the line
        wrap = self.db.execute("SELECT * FROM tel_straight_laps WHERE driver_number = 1 AND lap_number = 1 AND straight = 2").fetchone()
        self.assertEqual(wrap["top_speed"], 300)

    def test_clipping(self):
        """Flat out, speed peaks then falls 8 km/h before the braking point: clipping."""
        pts, d = [], 0.0
        speeds = [200, 230, 255, 275, 290, 300, 304, 305, 303, 300, 298, 297]
        for k, v in enumerate(speeds):
            pts.append((d, k * 0.25, v, 100, 0, 7, 11000, 12, 0, 0, 0.0))
            d += v / 3.6 * 0.25
        pts.append((d, len(speeds) * 0.25, 280, 0, 1, 7, 11000, 0, 0, 0, 0.0))
        m = telemetry.measure_straights(pts, [{"straight": 1, "start": 0, "end": d}])[0]
        self.assertEqual((m["top_speed"], m["end_speed"], m["clip_drop"]), (305, 297, 8))
        self.assertGreater(m["clip_dist"], 80)
        self.assertEqual(m["drs"], 1.0)

    def test_lift_and_coast(self):
        """Throttle off with no brake for a second at speed, ending on the brakes: one lift-and-coast."""
        pts = []
        for k in range(40):                    # 0.25 s apart, 20 m apart
            t, d = k * 0.25, k * 20.0
            thr, brk, spd = (0, 0, 290) if 10 <= k < 15 else (0, 1, 200) if 15 <= k < 18 else (100, 0, 300)
            pts.append((d, t, spd, thr, brk, 7, 11000, 0, 0, 0, 0.0))
        lap, _ = telemetry.measure_lap(pts, [], 800, 10.0)
        self.assertEqual((lap["lico_count"], lap["lico_time"]), (1, 1.25))


if __name__ == "__main__":
    unittest.main()
