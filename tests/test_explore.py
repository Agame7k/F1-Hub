"""Explore tab (analysis, season stats) and the FastF1 bridge - no network, no FastF1 needed."""
import gzip
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from f1hub import analysis, api, ff1, gui


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds")


T0 = datetime(2021, 12, 12, 13, 0, tzinfo=timezone.utc).timestamp()
KEY = ff1.make_key(2021, 22, 5)


def tiny_export(path):
    """Two cars, five laps, a VSC on lap 3, and a few seconds of telemetry - in the worker's export format."""
    laps, pos = [], []
    for n, base in ((33, 90.0), (44, 90.5)):
        t = T0
        for ln in range(1, 6):
            dur = base + (8 if ln == 3 else 0) + ln * 0.05
            laps.append({"driver_number": n, "lap_number": ln, "date_start": iso(t), "lap_duration": dur,
                         "duration_sector_1": 30.0, "duration_sector_2": 30.0, "duration_sector_3": dur - 60,
                         "st_speed": 320, "is_pit_out_lap": False, "track_status": "1", "session_key": KEY})
            t += dur
    pos = [{"date": iso(T0), "driver_number": 33, "position": 1}, {"date": iso(T0), "driver_number": 44, "position": 2}]
    rc = [{"date": iso(T0), "category": "SessionStatus", "message": "SESSION STARTED", "flag": None},
          {"date": iso(T0 + 185), "category": "SafetyCar", "message": "VIRTUAL SAFETY CAR DEPLOYED", "flag": None, "lap_number": 3},
          {"date": iso(T0 + 240), "category": "SafetyCar", "message": "VIRTUAL SAFETY CAR ENDING", "flag": None, "lap_number": 3},
          {"date": iso(T0 + 460), "category": "SessionStatus", "message": "SESSION FINISHED", "flag": None}]
    stints = [{"driver_number": n, "stint_number": 1, "compound": "MEDIUM", "lap_start": 1, "lap_end": 5, "tyre_age_at_start": 0}
              for n in (33, 44)]
    session = {"session_key": KEY, "session_name": "Race", "session_type": "Race", "date_start": iso(T0),
               "date_end": iso(T0 + 470), "meeting_key": 202122, "year": 2021, "location": "Yas Island",
               "circuit_short_name": "Yas Marina Circuit", "gmt_offset": "04:00:00"}
    data = {"session": session, "meeting": {"meeting_key": 202122, "meeting_name": "Abu Dhabi Grand Prix"},
            "drivers": [{"driver_number": 33, "name_acronym": "VER", "team_name": "Red Bull Racing", "team_colour": "0600EF"},
                        {"driver_number": 44, "name_acronym": "HAM", "team_name": "Mercedes", "team_colour": "00D2BE"}],
            "starting_grid": [], "session_result": [], "scheduled_laps": 5,
            "rows": {"laps": laps, "stints": stints, "pit": [], "race_control": rc, "weather": [], "position": pos,
                     "intervals": [], "overtakes": [], "team_radio": [], "championship_drivers": []},
            "telemetry": {"t0": T0, "loc": {"33": {"t": [0, 250, 500, 750], "x": [1, 2, 3, 4], "y": [5, 6, 7, 8], "z": [0, 0, 0, 0]}},
                          "car": {"33": {"t": [0, 250], "speed": [100, 110], "rpm": [9000, 9100], "gear": [3, 3],
                                         "throttle": [99, 100], "brake": [0, 0], "drs": [0, 0]}}}}
    with gzip.open(path, "wt") as f:
        json.dump(data, f)


class FastF1Keys(unittest.TestCase):
    def test_round_trip_and_no_clash_with_openf1(self):
        k = ff1.make_key(2021, 22, 5, 3)
        self.assertEqual(ff1.parse_key(k), {"year": 2021, "round": 22, "idx": 5, "rec": 3})
        self.assertTrue(ff1.is_key(k))
        self.assertFalse(ff1.is_key(9662))          # OpenF1 keys are small
        self.assertFalse(ff1.is_key("latest"))


class FastF1Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(ff1, "EXPORTS", Path(self.tmp.name))
        self.p.start()
        ff1._stores.clear()
        tiny_export(Path(self.tmp.name) / f"{KEY}.json.gz")

    def tearDown(self):
        self.p.stop()
        ff1._stores.clear()
        self.tmp.cleanup()

    def test_query_static_and_telemetry_windows(self):
        st = ff1.store(KEY)
        self.assertEqual(st.query("sessions", session_key=KEY)[0]["session_name"], "Race")
        self.assertEqual({d["name_acronym"] for d in st.query("drivers", session_key=KEY)}, {"VER", "HAM"})
        w = st.query("location", session_key=KEY, **{"date>": iso(T0), "date<": iso(T0 + 0.6)})
        self.assertEqual([r["x"] for r in w], [2, 3])          # strictly after the start, before 0.6 s
        w = st.query("location", session_key=KEY, driver_number=33, **{"date>=": iso(T0), "date<": iso(T0 + 0.3)})
        self.assertEqual([r["x"] for r in w], [1, 2])
        car = st.query("car_data", session_key=KEY, driver_number=33, **{"date<": iso(T0 + 1)})
        self.assertEqual(car[0]["n_gear"], 3)
        self.assertEqual(st.actual_end(), datetime.fromtimestamp(T0 + 470, timezone.utc))

    def test_replay_tracker_runs_on_a_fastf1_store(self):
        from f1hub.replay import ReplayTracker
        tr = ReplayTracker(ff1.store(KEY), datetime.fromtimestamp(T0 + 200, timezone.utc))
        tr.refresh()
        snap = tr.snapshot(None)
        self.assertEqual(snap["meeting"], "Abu Dhabi Grand Prix")
        self.assertEqual(snap["scheduled_laps"], 5)            # from the FastF1 session, not OpenF1
        self.assertEqual([r["code"] for r in snap["rows"]], ["VER", "HAM"])
        self.assertEqual(snap["lap"], 3)                       # nothing from after the replay clock

    def test_analysis_drops_neutralised_and_first_laps(self):
        a = analysis.session_analysis(KEY)
        self.assertEqual(a["source"], "fastf1")
        ver = next(p for p in a["pace"] if p["code"] == "VER")
        self.assertEqual(ver["n"], 3)                          # laps 2, 4, 5: no lap 1, no VSC lap 3
        self.assertLess(ver["max"], 91)
        self.assertEqual(a["teammates"], [])                   # different teams
        self.assertEqual(a["best"][0]["code"], "VER")


class NeutralPeriods(unittest.TestCase):
    def test_openf1_wording_closes_a_vsc(self):
        rc = [{"date": iso(T0), "message": "VIRTUAL SAFETY CAR DEPLOYED"},
              {"date": iso(T0 + 60), "message": "VIRTUAL SAFETY CAR ENDING"},
              {"date": iso(T0 + 100), "message": "SAFETY CAR DEPLOYED"}]
        spans = analysis.neutral_periods(rc)
        self.assertEqual(spans[0], (T0, T0 + 80))
        self.assertEqual(spans[1][1], float("inf"))           # still out


class SeasonStats(unittest.TestCase):
    def test_points_h2h_and_gains(self):
        def r(code, team, pos, grid, status=None):
            return {"code": code, "name": code, "team": team, "colour": "#fff", "pos": pos, "grid": grid,
                    "points": None, "status": status}
        rounds = [
            {"round": 1, "name": "A", "race": [r("AAA", "T1", 1, 3), r("BBB", "T1", None, 1, "DNF"), r("CCC", "T2", 2, 2)],
             "sprint": [], "quali": [r("BBB", "T1", 1, None), r("CCC", "T2", 2, None), r("AAA", "T1", 3, None)]},
            {"round": 2, "name": "B", "race": [r("BBB", "T1", 1, 1), r("AAA", "T1", 2, 2), r("CCC", "T2", 3, 3)],
             "sprint": [], "quali": [r("BBB", "T1", 1, None), r("AAA", "T1", 2, None), r("CCC", "T2", 3, None)]},
        ]
        st = analysis.season_stats(rounds, 2025)
        a = next(d for d in st["drivers"] if d["code"] == "AAA")
        self.assertEqual(a["points"], 43)
        self.assertEqual(a["gained"], 2)                       # P3 -> P1, then P2 -> P2
        self.assertEqual(a["by_round"], [[1, 25], [2, 43]])
        b = next(d for d in st["drivers"] if d["code"] == "BBB")
        self.assertEqual((b["dnfs"], b["poles"], b["form"]), (1, 2, ["DNF", 1]))
        h = next(x for x in st["h2h"] if x["team"] == "T1")
        self.assertEqual((h["a"], h["b"], h["quali"], h["race"]), ("AAA", "BBB", [0, 2], [1, 1]))

    def test_team_colour_fallback(self):
        self.assertEqual(analysis.team_colour("Red Bull Racing", "#888888"), "#3671C6")
        self.assertEqual(analysis.team_colour("Ferrari", "#E8002D"), "#E8002D")


class ExploreRequests(unittest.TestCase):
    def test_years_before_2023_need_fastf1(self):
        self.assertEqual(gui.clean_year("2019", ff1.FIRST_YEAR), 2019)
        with self.assertRaises(gui.BadRequest):
            gui.clean_year("2019")                             # OpenF1-only endpoints still start in 2023
        with mock.patch.object(ff1, "available", return_value=False):
            with self.assertRaises(gui.BadRequest):
                gui._ff1_year(2019)
        self.assertEqual(gui._ff1_year(2024), 2024)

    def test_keys_and_times(self):
        self.assertEqual(gui.clean_key("202122050"), 202122050)
        for bad in ("", "abc", None):
            with self.assertRaises(gui.BadRequest):
                gui.clean_key(bad)
        self.assertIsNone(gui.clean_before(""))
        with self.assertRaises(gui.BadRequest):
            gui.clean_before("120")


class LockedOpenF1FallsBackToFastF1(unittest.TestCase):
    def test_replay_store_switches_when_openf1_is_locked(self):
        from f1hub.replay import SessionStore
        sess = {"session_key": 9662, "session_name": "Race", "date_start": "2024-12-08T13:00:00+00:00",
                "date_end": "2024-12-08T15:00:00+00:00", "year": 2024, "meeting_key": 1252}

        def fake_get(endpoint, **kw):
            if endpoint == "sessions":
                return [sess]
            raise api.LiveSessionLock("locked")
        sentinel = object()
        with mock.patch.object(api, "get", side_effect=fake_get), \
                mock.patch.object(ff1, "available", return_value=True), \
                mock.patch.object(ff1, "key_for_openf1", return_value=202424050) as k, \
                mock.patch.object(ff1, "store", return_value=sentinel) as st:
            self.assertIs(gui.Feed._open_store(9662, SessionStore), sentinel)
        k.assert_called_once_with(sess)
        st.assert_called_once_with(202424050)

    def test_without_fastf1_the_lock_is_reported(self):
        from f1hub.replay import SessionStore
        sess = {"session_key": 9662, "session_name": "Race", "date_start": "2024-12-08T13:00:00+00:00",
                "date_end": "2024-12-08T15:00:00+00:00", "year": 2024, "meeting_key": 1252}

        def fake_get(endpoint, **kw):
            if endpoint == "sessions":
                return [sess]
            raise api.LiveSessionLock("locked")
        with mock.patch.object(api, "get", side_effect=fake_get), mock.patch.object(ff1, "available", return_value=False):
            with self.assertRaises(api.LiveSessionLock):
                gui.Feed._open_store(9662, SessionStore)


class WorkerGapParsing(unittest.TestCase):
    def test_fastf1_gap_strings(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("ff1_worker", Path(ff1.__file__).with_name("ff1_worker.py"))
        w = importlib.util.module_from_spec(spec)
        import sys
        out = sys.stdout
        try:
            spec.loader.exec_module(w)       # (the module points stdout at stderr; put it back)
        finally:
            sys.stdout = out
        self.assertEqual(w._gap("+0.216"), 0.216)
        self.assertEqual(w._gap("1 L"), "+1 LAP")
        self.assertEqual(w._gap("2L"), "+2 LAPS")
        self.assertIsNone(w._gap("LAP 1"))
        self.assertIsNone(w._gap(""))
        self.assertEqual(w.make_key(2021, 22, 5), KEY)


if __name__ == "__main__":
    unittest.main()
