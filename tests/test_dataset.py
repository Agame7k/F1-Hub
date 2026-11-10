"""Season dataset (f1hub/dataset.py): lap enrichment, results, incremental compiles and CSV export - offline."""
import csv
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from f1hub import dataset
from f1hub.season import Round
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)

T0 = datetime(2025, 3, 16, 4, 0, tzinfo=timezone.utc).timestamp()
RACE, QUALI = 9693, 9692


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds")


def race_rows():
    """Two cars, six laps. VER leads; HAM pits on lap 3 (MEDIUM -> HARD); a VSC covers lap 4; HAM's lap 6 is slow."""
    laps, pos, ints = [], [], []
    for n, base in ((1, 90.0), (44, 90.6)):
        t = T0
        for ln in range(1, 7):
            dur = base + (6 if ln == 4 else 0) + (5 if (n, ln) == (44, 6) else 0) + (20 if (n, ln) == (44, 3) else 0)
            laps.append({"driver_number": n, "lap_number": ln, "date_start": iso(t), "lap_duration": dur,
                         "duration_sector_1": 30.0, "duration_sector_2": 30.0, "duration_sector_3": dur - 60,
                         "st_speed": 320, "is_pit_out_lap": (n, ln) == (44, 4)})
            ints.append({"date": iso(t + dur - 1), "driver_number": n,
                         "gap_to_leader": 0.0 if n == 1 else 0.8 * ln, "interval": 0.0 if n == 1 else 0.8 * ln})
            t += dur
        pos.append({"date": iso(T0 + 1), "driver_number": n, "position": 1 if n == 1 else 2})
    vsc_start = T0 + 3 * 90.0 + 10
    rc = [{"date": iso(T0), "category": "Flag", "flag": "GREEN", "message": "GREEN LIGHT - PIT EXIT OPEN"},
          {"date": iso(vsc_start), "category": "SafetyCar", "message": "VIRTUAL SAFETY CAR DEPLOYED", "lap_number": 4},
          {"date": iso(vsc_start + 60), "category": "SafetyCar", "message": "VIRTUAL SAFETY CAR ENDING", "lap_number": 4}]
    return {
        "drivers": [{"driver_number": 1, "name_acronym": "VER", "full_name": "Max Verstappen", "team_name": "Red Bull Racing",
                     "team_colour": "3671C6"},
                    {"driver_number": 44, "name_acronym": "HAM", "full_name": "Lewis Hamilton", "team_name": "Ferrari",
                     "team_colour": "E80020"}],
        "laps": laps, "position": pos, "intervals": ints, "race_control": rc,
        "stints": [{"driver_number": 1, "stint_number": 1, "compound": "MEDIUM", "lap_start": 1, "lap_end": 6, "tyre_age_at_start": 0},
                   {"driver_number": 44, "stint_number": 1, "compound": "MEDIUM", "lap_start": 1, "lap_end": 3, "tyre_age_at_start": 2},
                   {"driver_number": 44, "stint_number": 2, "compound": "HARD", "lap_start": 4, "lap_end": 6, "tyre_age_at_start": 0}],
        "pit": [{"driver_number": 44, "lap_number": 3, "date": iso(T0 + 270), "lane_duration": None, "pit_duration": 21.4,
                 "stop_duration": 2.3}],
        "weather": [{"date": iso(T0 - 60), "air_temperature": 18.0, "track_temperature": 30.0, "rainfall": 0},
                    {"date": iso(T0 + 300), "air_temperature": 18.5, "track_temperature": 33.0, "rainfall": 0}],
        "session_result": [{"driver_number": 1, "position": 1, "points": 25.0, "number_of_laps": 6, "duration": 560.0,
                            "gap_to_leader": 0, "dnf": False, "dns": False, "dsq": False},
                           {"driver_number": 44, "position": 2, "points": 18.0, "number_of_laps": 6, "duration": 590.0,
                            "gap_to_leader": "+1 LAP", "dnf": False, "dns": False, "dsq": False}],
        "starting_grid": [],
        "overtakes": [{"date": iso(T0 + 30), "overtaking_driver_number": 1, "overtaken_driver_number": 44, "position": 1},
                      # HAM in the pit lane (in at ~T0+250, out at ~T0+270), then a swap under the VSC
                      {"date": iso(T0 + 262), "overtaking_driver_number": 1, "overtaken_driver_number": 44, "position": 1},
                      {"date": iso(T0 + 320), "overtaking_driver_number": 44, "overtaken_driver_number": 1, "position": 1}],
    }


QUALI_ROWS = {"drivers": race_rows()["drivers"], "laps": [], "position": [], "race_control": [], "stints": [], "pit": [],
              "weather": [], "starting_grid": [],
              "session_result": [{"driver_number": 44, "position": 1, "duration": [80.1, 79.5, 79.0], "gap_to_leader": [0, 0, 0]},
                                 {"driver_number": 1, "position": 2, "duration": [80.3, 79.6, 79.2], "gap_to_leader": [0.2, 0.1, 0.2]}]}


class FakeSource:
    fetched = []

    def __init__(self, key):
        self.key = int(key)
        self.via = "openf1"
        self.rows = race_rows() if self.key == RACE else QUALI_ROWS

    def get(self, endpoint, **params):
        FakeSource.fetched.append((self.key, endpoint))
        return self.rows.get(endpoint, [])


def session(key, name, stype, start):
    return {"session_key": key, "session_name": name, "session_type": stype, "meeting_key": 1254,
            "date_start": iso(start), "date_end": iso(start + 7200), "circuit_short_name": "Melbourne",
            "location": "Melbourne", "country_name": "Australia"}


class FakeSeason:
    def __init__(self, year):
        self.rounds = [Round(1, 1254, "Australian Grand Prix", "Australia", "Melbourne", "Albert Park",
                             datetime.fromtimestamp(T0, timezone.utc),
                             {"Qualifying": session(QUALI, "Qualifying", "Qualifying", T0 - 86400),
                              "Race": session(RACE, "Race", "Race", T0)})]

    def load(self, progress=None):
        return self


class Dataset(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "s.sqlite"
        FakeSource.fetched = []
        self.patches = [mock.patch.object(dataset, "Source", FakeSource), mock.patch.object(dataset, "Season", FakeSeason)]
        for p in self.patches:
            p.start()
        self.out, self.summary = dataset.compile_season(2025, path=self.path)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row

    def tearDown(self):
        self.db.close()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def lap(self, n, ln):
        return self.db.execute("SELECT * FROM laps WHERE driver_number = ? AND lap_number = ?", (n, ln)).fetchone()

    def test_compiles_both_sessions(self):
        self.assertEqual([label for label, _ in self.summary["compiled"]],
                         ["R1 Australian Grand Prix - Qualifying", "R1 Australian Grand Prix - Race"])
        self.assertEqual(self.summary["failed"], [])
        self.assertEqual(dataset.counts(self.db)["laps"], 12)

    def test_tyres_and_pits(self):
        self.assertEqual((self.lap(44, 3)["compound"], self.lap(44, 3)["tyre_age"], self.lap(44, 3)["pit_in"]), ("MEDIUM", 4, 1))
        self.assertEqual((self.lap(44, 4)["compound"], self.lap(44, 4)["tyre_age"], self.lap(44, 4)["pit_out"]), ("HARD", 0, 1))
        pit = self.db.execute("SELECT lane_duration, stop_duration, stop FROM pits").fetchone()
        self.assertEqual(tuple(pit), (21.4, 2.3, 1))    # pit_duration stands in when lane_duration is missing

    def test_clean_laps(self):
        self.assertEqual(self.lap(1, 1)["clean"], 0)    # race start
        self.assertEqual(self.lap(1, 4)["neutral"], 1)  # under the VSC
        self.assertEqual(self.lap(1, 4)["clean"], 0)
        self.assertEqual(self.lap(1, 2)["clean"], 1)
        self.assertEqual(self.lap(44, 3)["clean"], 0)   # in-lap
        self.assertEqual(self.lap(44, 6)["clean"], 1)   # +5 s is inside 7% of the median

    def test_position_gap_and_weather_at_each_lap(self):
        self.assertEqual(self.lap(1, 2)["position"], 1)
        self.assertEqual(self.lap(44, 5)["position"], 2)
        self.assertAlmostEqual(self.lap(44, 5)["interval_ahead"], 4.0)
        self.assertEqual(self.lap(1, 1)["track_temp"], 30.0)
        self.assertEqual(self.lap(1, 5)["track_temp"], 33.0)

    def test_overtakes_on_track(self):
        rows = self.db.execute("SELECT lap_number, overtaking, on_track FROM overtakes ORDER BY date").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(1, 1, 1), (3, 1, 0), (4, 44, 0)])

    def test_overtake_filters(self):
        """OpenF1 records every swap of position; only racing passes are on_track."""
        laps = [{"driver_number": n, "lap_number": ln, "date_start": iso(T0 + (ln - 1) * 90), "lap_time": 90.0,
                 "compound": "MEDIUM", "clean": 1} for n in range(1, 8) for ln in range(1, 6)]
        laps[4]["lap_time"] = 120.0       # car 1, lap 5: crawling back with damage
        ot = lambda t, a, b: {"date": iso(T0 + t), "overtaking_driver_number": a, "overtaken_driver_number": b}
        rc = [{"date": iso(T0 + 440), "category": "Flag", "flag": "CHEQUERED", "message": "CHEQUERED FLAG"}]
        rows = dataset.overtake_rows(1, [
            ot(100, 2, 3),                                                  # a pass
            ot(190, 4, 5), ot(195, 5, 4),                                   # ping-pong
            ot(200, 6, 2), ot(200, 6, 3),                                   # two at the same instant
            ot(280, 2, 7), ot(285, 3, 7), ot(290, 4, 7), ot(295, 5, 7),     # a cascade
            ot(370, 2, 1),                                                  # passing a damaged car
            ot(450, 3, 2),                                                  # after the flag
        ], [], rc, laps)
        self.assertEqual([r["on_track"] for r in rows], [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(rows[0]["lap_number"], 2)

    def test_safety_car_through_the_pit_lane(self):
        """Melbourne 2025: the Safety Car led the field through the pit lane on laps 2-4. OpenF1 has a pit row and a
        new 'stint' each lap, but the tyre age carries on: one set, and the drive-throughs aren't stops."""
        st = lambda i, c, a, b, age: {"driver_number": 1, "stint_number": i, "compound": c, "lap_start": a, "lap_end": b,
                                      "tyre_age_at_start": age}
        pits = [{"driver_number": 1, "lap_number": ln} for ln in (2, 3, 4, 33)]
        out = dataset.merge_stints([st(1, "INTERMEDIATE", 1, 1, 0), st(2, "INTERMEDIATE", 2, 2, 2), st(3, "INTERMEDIATE", 3, 3, 3),
                                    st(4, "INTERMEDIATE", 4, 33, 4), st(5, "INTERMEDIATE", 34, 57, 0)], pits)
        self.assertEqual([(s["lap_start"], s["lap_end"]) for s in out], [(1, 33), (34, 57)])   # a fresh set after lap 33

    def test_split_stints_are_merged(self):
        """OpenF1 split Melbourne 2025's wet start into a 'stint' per lap on the same intermediates."""
        st = lambda n, i, c, a, b: {"driver_number": n, "stint_number": i, "compound": c, "lap_start": a, "lap_end": b,
                                    "tyre_age_at_start": 0}
        out = dataset.merge_stints([st(1, 1, "INTERMEDIATE", 1, 1), st(1, 2, "INTERMEDIATE", 2, 2), st(1, 3, "INTERMEDIATE", 3, 33),
                                    st(1, 4, "HARD", 34, 57),
                                    st(4, 1, "MEDIUM", 1, 20), st(4, 2, "MEDIUM", 21, 57)],     # a real stop: new mediums
                                   [{"driver_number": 1, "lap_number": 33}, {"driver_number": 4, "lap_number": 20}])
        self.assertEqual([(s["driver_number"], s["stint_number"], s["compound"], s["lap_start"], s["lap_end"]) for s in out],
                         [(1, 1, "INTERMEDIATE", 1, 33), (1, 2, "HARD", 34, 57), (4, 1, "MEDIUM", 1, 20), (4, 2, "MEDIUM", 21, 57)])

    def test_results_with_grid_from_qualifying(self):
        rows = {r["driver_number"]: r for r in self.db.execute(f"SELECT * FROM results WHERE session_key = {RACE}")}
        self.assertEqual((rows[44]["grid"], rows[44]["grid_source"], rows[44]["laps_down"]), (1, "qualifying", 1))
        q = self.db.execute(f"SELECT q1, q2, q3 FROM results WHERE session_key = {QUALI} AND driver_number = 44").fetchone()
        self.assertEqual(tuple(q), (80.1, 79.5, 79.0))

    def test_position_code_instead_of_number(self):
        # OpenF1 sends 'RT' as the position of a car that retired in qualifying without a time
        rt = {"driver_number": 1, "position": "RT", "duration": [None, None, None], "gap_to_leader": [None, None, None],
              "dnf": False, "dns": False, "dsq": False}
        rows = dict(QUALI_ROWS, session_result=[QUALI_ROWS["session_result"][0], rt])
        class Retired(FakeSource):
            def __init__(self, key):
                super().__init__(key)
                if self.key == QUALI:
                    self.rows = rows

        with mock.patch.object(dataset, "Source", Retired):
            _, summary = dataset.compile_season(2025, path=self.path, refresh=True)
        self.assertEqual(summary["failed"], [])
        q = self.db.execute(f"SELECT position, status FROM results WHERE session_key = {QUALI} AND driver_number = 1").fetchone()
        self.assertEqual(tuple(q), (None, "DNF"))
        grid = self.db.execute(f"SELECT grid FROM results WHERE session_key = {RACE} AND driver_number = 1").fetchone()[0]
        self.assertIsNone(grid)

    def test_position_codes_already_stored_are_repaired(self):
        self.db.execute(f"UPDATE results SET position = 'RT' WHERE session_key = {QUALI} AND driver_number = 1")
        self.db.execute(f"UPDATE results SET grid = 'RT' WHERE session_key = {RACE} AND driver_number = 1")
        self.db.execute(f"UPDATE results SET grid = '1' WHERE session_key = {RACE} AND driver_number = 44")
        self.db.commit()
        dataset.connect(self.path).close()
        rows = {(r["session_key"], r["driver_number"]): r for r in self.db.execute("SELECT * FROM results")}
        self.assertEqual((rows[QUALI, 1]["position"], rows[QUALI, 1]["status"]), (None, "DNF"))
        self.assertEqual((rows[RACE, 1]["grid"], rows[RACE, 1]["grid_source"]), (None, None))
        self.assertEqual(rows[RACE, 44]["grid"], 1)

    def test_final_sessions_are_skipped_next_time(self):
        FakeSource.fetched = []
        _, summary = dataset.compile_season(2025, path=self.path)
        self.assertEqual(len(summary["skipped"]), 2)
        self.assertEqual(FakeSource.fetched, [])
        _, summary = dataset.compile_season(2025, path=self.path, refresh=True)
        self.assertEqual(len(summary["compiled"]), 2)
        self.assertEqual(dataset.counts(self.db)["laps"], 12)      # replaced, not duplicated

    def test_unfinished_sessions_wait(self):
        later = datetime.now(timezone.utc).timestamp() + 86400
        with mock.patch.object(FakeSeason, "__init__", lambda s, y: setattr(s, "rounds", [Round(
                2, 1255, "Chinese Grand Prix", "China", "Shanghai", "Shanghai", datetime.now(timezone.utc),
                {"Race": session(9999, "Race", "Race", later)})])):
            _, summary = dataset.compile_season(2025, path=self.path)
        self.assertEqual(summary["pending"], ["R2 Chinese Grand Prix - Race"])

    def test_glance_and_csv(self):
        g = {title: rows for title, _, rows in dataset.glance(self.db)}
        self.assertEqual(g["Laps led"][0], ("VER", 6))
        self.assertEqual(g["Positions gained"][0], ("VER", 1))
        self.assertEqual(g["Overtakes made"], [("VER", 1)])
        paths = dataset.export_csv(self.db, Path(self.tmp.name) / "csv")
        with open(Path(self.tmp.name) / "csv" / "laps_full.csv", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(paths), len(dataset.TABLES) + 1)
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[0]["code"], "VER")

    def test_old_schema_is_rebuilt(self):
        self.db.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
        self.db.commit()
        dataset.connect(self.path).close()
        self.assertEqual(sqlite3.connect(self.path).execute("SELECT COUNT(*) FROM laps").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
