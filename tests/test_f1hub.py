import unittest
from datetime import datetime, timezone
from unittest import mock

from f1hub import api
from f1hub.championship import (Entry, clinch_scenario, countback_key, has_clinched_over,
                                max_remaining, sort_standings, title_status)
from f1hub.season import Season


def E(code, points, finishes=()):
    return Entry(code, code, "T", points, list(finishes))


class ClinchMaths(unittest.TestCase):
    def test_max_remaining(self):
        self.assertEqual(max_remaining([False, False]), 50)
        self.assertEqual(max_remaining([True, False]), 58)
        self.assertEqual(max_remaining([True], constructor=True), 25 + 18 + 8 + 7)

    def test_clear_clinch(self):
        self.assertTrue(has_clinched_over(E("A", 400), E("B", 349), [False, False]))
        self.assertFalse(has_clinched_over(E("A", 400), E("B", 351), [False, False]))

    def test_tie_goes_to_countback(self):
        # B can at best tie on points by winning both remaining races
        leader = E("A", 100, [1, 1, 1, 1, 1])
        rival = E("B", 50, [2, 2])
        self.assertTrue(has_clinched_over(leader, rival, [False, False]))   # 5 wins beats 2
        leader = E("A", 100, [1])
        self.assertFalse(has_clinched_over(leader, rival, [False, False]))  # 1 win loses to 2

    def test_title_status(self):
        a, b, c = E("A", 300), E("B", 280), E("C", 200)
        champ, contenders = title_status([a, b, c], [False, False, False])
        self.assertIsNone(champ)
        self.assertEqual([e.code for e in contenders], ["A", "B"])
        champ, _ = title_status([a, b, c], [])          # season over
        self.assertIs(champ, a)
        champ, _ = title_status([a, E("B", 240)], [False, False])
        self.assertIs(champ, a)

    def test_countback_order(self):
        x, y = E("X", 100, [1, 3]), E("Y", 100, [2, 2])
        self.assertEqual(sort_standings([y, x])[0], x)
        self.assertGreater(countback_key([1, None, 5]), countback_key([2, 2, 2]))

    def test_clinch_scenario(self):
        msg = clinch_scenario([E("A", 300), E("B", 260)], [False, False, False])
        self.assertIn("by 11+ points", msg)   # lead 40, 50 left after next race -> needs a lead of 51
        self.assertIsNone(clinch_scenario([E("A", 300), E("B", 200)], [False] * 10))


# --------------------------------------------------------------------------- pipeline with fake API
SESS = [
    {"session_key": 1, "meeting_key": 10, "session_name": "Qualifying", "date_start": "2025-03-01T10:00:00+00:00",
     "date_end": "2025-03-01T11:00:00+00:00"},
    {"session_key": 2, "meeting_key": 10, "session_name": "Race", "date_start": "2025-03-02T10:00:00+00:00",
     "date_end": "2025-03-02T12:00:00+00:00"},
    {"session_key": 3, "meeting_key": 20, "session_name": "Sprint", "date_start": "2025-03-08T10:00:00+00:00",
     "date_end": "2025-03-08T11:00:00+00:00"},
    {"session_key": 4, "meeting_key": 20, "session_name": "Race", "date_start": "2025-03-09T10:00:00+00:00",
     "date_end": "2025-03-09T12:00:00+00:00"},
    {"session_key": 5, "meeting_key": 30, "session_name": "Race", "date_start": "2099-03-09T10:00:00+00:00",
     "date_end": "2099-03-09T12:00:00+00:00"},
]
MEET = [{"meeting_key": k, "meeting_name": f"GP {k}", "country_name": f"C{k}", "location": "X",
         "circuit_short_name": "Y", "date_start": d} for k, d in
        ((10, "2025-02-28T00:00:00+00:00"), (20, "2025-03-07T00:00:00+00:00"), (30, "2099-03-07T00:00:00+00:00"))]
DRV = [{"driver_number": 1, "name_acronym": "AAA", "full_name": "Driver A", "team_name": "Red", "team_colour": "FF0000"},
       {"driver_number": 2, "name_acronym": "BBB", "full_name": "Driver B", "team_name": "Red", "team_colour": "FF0000"},
       {"driver_number": 3, "name_acronym": "CCC", "full_name": "Driver C", "team_name": "Blue", "team_colour": "0000FF"}]


def res(order, dnf=()):
    return [{"driver_number": n, "position": i + 1, "dnf": n in dnf, "dns": False, "dsq": False}
            for i, n in enumerate(order)]


RESULTS = {1: res([3, 1, 2]), 2: res([1, 3, 2], dnf=(2,)), 3: res([3, 1, 2]), 4: res([3, 2, 1])}


def fake_get(endpoint, ttl=None, **p):
    if endpoint == "meetings":
        return MEET
    if endpoint == "sessions":
        return SESS
    if endpoint == "session_result":
        return RESULTS.get(p["session_key"], [])
    if endpoint == "drivers":
        return DRV
    if endpoint == "championship_drivers":
        return [{"driver_number": 1, "points_current": 25 + 7 + 15},
                {"driver_number": 3, "points_current": 18 + 8 + 25},
                {"driver_number": 2, "points_current": 15 + 6 + 18}]
    if endpoint == "championship_teams":
        return [{"team_name": "Red", "points_current": 47 + 39}, {"team_name": "Blue", "points_current": 51}]
    return []


class SeasonPipeline(unittest.TestCase):
    def setUp(self):
        with mock.patch.object(api, "get", fake_get):
            self.s = Season(2025).load()

    def test_rounds(self):
        self.assertEqual(len(self.s.rounds), 3)
        self.assertEqual(len(self.s.completed_rounds()), 2)
        self.assertEqual([r.number for r in self.s.remaining_rounds()], [3])
        self.assertTrue(self.s.rounds[1].has_sprint)

    def test_points_and_official_match(self):
        drivers, teams, _ = self.s.standings()
        pts = {e.code: e.points for e in drivers}
        self.assertEqual(pts, {"CCC": 51, "AAA": 47, "BBB": 39})
        self.assertEqual(self.s.official_check(), [])
        self.assertEqual({t.name: t.points for t in teams}, {"Red": 86, "Blue": 51})
        self.assertEqual(drivers[0].poles, 1)
        self.assertEqual(sum(e.dnfs for e in drivers), 1)

    def test_timeline(self):
        tl = self.s.title_timeline()
        self.assertEqual(len(tl), 2)


class UrlBuilding(unittest.TestCase):
    def test_operators(self):
        url = api._build_url("car_data", {"session_key": 9, "speed>=": 315, "driver_number": [1, 4]})
        self.assertEqual(url, "https://api.openf1.org/v1/car_data?session_key=9&speed>=315&driver_number=1&driver_number=4")




class ClinchWording(unittest.TestCase):
    def test_leader_already_close(self):
        msg = clinch_scenario([E("A", 365), E("B", 242)], [False] * 5)
        self.assertIn("unless B outscores them by more than 22", msg)


class RedFlagTracking(unittest.TestCase):
    def test_suspend_resume(self):
        from f1hub.racecontrol import RaceControl
        rc = RaceControl("-06:00:00")
        M = lambda t, msg, cat="Other", **k: dict({"date": f"2026-11-01T{t}+00:00", "lap_number": 45,
                                                   "category": cat, "message": msg}, **k)
        rc.add(M("21:06:02", "RED FLAG - RACE SUSPENDED"))
        rc.add(M("21:14:01", "RESUMPTION ORDER: 3, 16, 12"))
        rc.add(M("21:20:00", "RACE WILL RESUME AT 15:35"))
        rc.add(M("21:21:00", "STANDING START PROCEDURE"))
        r = rc.red
        self.assertEqual(r["state"], "suspended")
        self.assertEqual(r["order"], [3, 16, 12])
        self.assertEqual(r["resume_at"], "2026-11-01T21:35:00+00:00")
        self.assertEqual(r["start_type"], "Standing start")
        rc.add(M("21:35:10", "SESSION STARTED", cat="SessionStatus"))
        self.assertEqual(r["state"], "resumed")
        self.assertEqual(r["duration_s"], 29 * 60 + 8)

    def test_abandoned(self):
        from f1hub.racecontrol import RaceControl
        rc = RaceControl("-06:00:00")
        rc.add({"date": "2026-11-01T21:06:02+00:00", "message": "RED FLAG", "category": "Other"})
        rc.add({"date": "2026-11-01T21:40:00+00:00", "message": "THE RACE WILL NOT BE RESUMED", "category": "Other"})
        self.assertEqual(rc.red["state"], "abandoned")

    def test_quali_segment_not_resumed(self):
        # São Paulo 2026: red flag ended Q2, then Q3 ran normally - the stoppage belongs to Q2 and is closed by Q3
        from f1hub.racecontrol import RaceControl
        rc = RaceControl("-03:00:00")
        M = lambda t, msg, cat="Other", **k: dict({"date": f"2026-11-07T{t}+00:00", "category": cat, "message": msg}, **k)
        rc.add(M("20:03:17", "SESSION ABORTED", "SessionStatus", qualifying_phase=2))
        rc.add(M("20:03:18", "RED FLAG", "Flag", flag="RED", qualifying_phase=2))
        rc.add(M("20:04:28", "Q2 WILL NOT BE RESUMED"))
        rc.add(M("20:05:47", "SESSION FINISHED", "SessionStatus", qualifying_phase=2))
        self.assertEqual((rc.red["state"], rc.red["phase"]), ("abandoned", 2))
        rc.add(M("20:14:00", "SESSION STARTED", "SessionStatus", qualifying_phase=3))
        self.assertEqual(rc.red["state"], "closed")


class ReplayEnd(unittest.TestCase):
    """São Paulo 2026 qualifying: scheduled to end 19:00, red flags ran it to 20:27."""
    def store(self, msgs):
        from f1hub.replay import SessionStore, _stamp
        st = SessionStore({"session_key": 1, "session_name": "Qualifying",
                           "date_start": "2026-11-07T18:00:00+00:00", "date_end": "2026-11-07T19:00:00+00:00"})
        return st, _stamp([dict(date=f"2026-11-07T{t}+00:00", category="SessionStatus", message=m, qualifying_phase=p)
                           for t, m, p in msgs])

    def test_not_final_until_q3_ends(self):
        st, rc = self.store([("18:00:00", "SESSION STARTED", 1), ("19:41:37", "SESSION FINISHED", 1),
                             ("19:49:00", "SESSION STARTED", 2), ("20:05:47", "SESSION FINISHED", 2)])
        with mock.patch("time.time", return_value=datetime(2026, 11, 7, 20, 10, tzinfo=timezone.utc).timestamp()):
            self.assertIsNone(st._final_end(rc))        # past the schedule, but only Q2 has ended

    def test_final_at_the_real_end(self):
        st, rc = self.store([("18:00:00", "SESSION STARTED", 1), ("20:14:00", "SESSION STARTED", 3),
                             ("20:27:00", "SESSION FINISHED", 3)])
        self.assertEqual(st._final_end(rc), datetime(2026, 11, 7, 20, 27, tzinfo=timezone.utc).timestamp())


class LiveStatus(unittest.TestCase):
    def test_resume_announcement_keeps_red(self):
        from f1hub.live import LiveTracker
        t = LiveTracker.__new__(LiveTracker)
        t.track_status = "GREEN"
        t._track_status({"message": "RED FLAG - RACE SUSPENDED", "category": "Other"})
        t._track_status({"message": "RACE WILL RESUME AT 15:45", "category": "Other"})
        self.assertEqual(t.track_status, "RED")
        t._track_status({"message": "SESSION STARTED", "category": "SessionStatus"})
        self.assertEqual(t.track_status, "GREEN")


class ChequeredIsNotRed(unittest.TestCase):
    """'CHEQUERED FLAG' contains the letters 'RED FLAG' - it once flagged every race finish as a red flag."""
    def test_classify(self):
        from f1hub.racecontrol import RaceControl, classify
        self.assertEqual(classify({"message": "CHEQUERED FLAG", "flag": "CHEQUERED"}), "chequered")
        self.assertEqual(classify({"message": "RED FLAG - RACE SUSPENDED", "category": "Other"}), "red")
        rc = RaceControl()
        rc.add({"date": "2026-11-01T22:23:53+00:00", "message": "CHEQUERED FLAG", "flag": "CHEQUERED"})
        self.assertIsNone(rc.red)

    def test_live_status(self):
        from f1hub.live import LiveTracker
        t = LiveTracker.__new__(LiveTracker)
        t.track_status = "GREEN"
        t._track_status({"message": "CHEQUERED FLAG", "flag": "CHEQUERED", "category": "Flag"})
        self.assertEqual(t.track_status, "CHEQUERED")


class Retirements(unittest.TestCase):
    def tracker(self, now, laps, status="GREEN", red_since=None):
        from datetime import datetime
        from f1hub.live import LiveTracker
        from f1hub.racecontrol import RaceControl
        t = LiveTracker.__new__(LiveTracker)
        t.is_race, t.track_status, t.retired = True, status, {}
        t.rc = RaceControl()
        if red_since:
            t.rc.add({"date": red_since, "message": "RED FLAG", "category": "Other", "lap_number": 6})
        t.laps_by = {n: {no: {"t": 80.0, "start": s} for no, s in ls.items()} for n, ls in laps.items()}
        t.lap_no = {n: max(ls) for n, ls in laps.items()}
        t.position = {n: i + 1 for i, n in enumerate(laps)}
        t.now = lambda: datetime.fromisoformat(now)
        return t

    @staticmethod
    def laps(first, upto, start="2026-11-01T20:00:00+00:00"):
        from datetime import datetime, timedelta
        t0 = datetime.fromisoformat(start)
        return {no: (t0 + timedelta(seconds=80 * (no - 1))).isoformat() for no in range(first, upto + 1)}

    def test_silent_car_is_out(self):
        t = self.tracker("2026-11-01T20:20:00+00:00", {1: self.laps(1, 15), 2: self.laps(1, 6)})
        t._detect_retirements()
        self.assertIn(2, t.retired)
        self.assertNotIn(1, t.retired)
        self.assertEqual(t.order(), [1, 2])

    def test_red_flag_doesnt_retire_everyone(self):
        # long stoppage: nobody laps for half an hour, but that's the red flag, not retirements
        t = self.tracker("2026-11-01T20:45:00+00:00", {1: self.laps(1, 8), 2: self.laps(1, 8)},
                         status="RED", red_since="2026-11-01T20:10:30+00:00")
        t._detect_retirements()
        self.assertEqual(t.retired, {})

    def test_race_control_message(self):
        t = self.tracker("2026-11-01T20:05:00+00:00", {1: self.laps(1, 3), 18: self.laps(1, 3)})
        t.rc.add({"date": "2026-11-01T20:04:00+00:00", "message": "CAR 18 (STR) STOPPED ON TRACK", "lap_number": 3})
        t.rc.messages[-1]["cars"] = [18]
        t._detect_retirements()
        self.assertTrue(t.retired[18]["definite"])


class FetchPriority(unittest.TestCase):
    def tracker(self):
        from datetime import datetime, timedelta, timezone
        from f1hub.live import LiveTracker
        t = LiveTracker.__new__(LiveTracker)
        t.refreshes, t.stream, t.view, t._last_fetch = 1, None, None, {}
        t.clock = datetime(2026, 11, 1, 20, tzinfo=timezone.utc)
        t.now = lambda: t.clock
        t.tick = lambda s: setattr(t, "clock", t.clock + timedelta(seconds=s))
        return t

    def count(self, t, endpoint, seconds=60):
        n = 0
        for _ in range(seconds):
            n += t._due(endpoint)
            t.tick(1)
        return n

    def test_on_screen_is_fetched_more(self):
        t = self.tracker()
        t.set_view("map")
        on = self.count(t, "location")
        t2 = self.tracker()
        t2.set_view("champ")
        off = self.count(t2, "location")
        self.assertGreater(on, off * 3)

    def test_union_of_tabs(self):
        t = self.tracker()
        t.set_view({"map", "strat"})
        self.assertGreaterEqual(self.count(t, "stints"), 6)     # strat needs stints every 10 s
        self.assertGreaterEqual(self.count(self.tracker(), "location"), 12)

    def test_hidden_slows_everything(self):
        t = self.tracker()
        t.set_view("hidden")
        self.assertLessEqual(self.count(t, "location"), 2)


class StreamParsing(unittest.TestCase):
    def test_publish_and_drain(self):
        import json
        import struct
        from f1hub.stream import Stream
        s = Stream(connect=False)
        topic = b"v1/location"
        for i, (sk, date) in enumerate([(11404, "2026-11-01T21:57:18.153000"), (99, "2026-11-01T21:57:18Z")]):
            row = json.dumps({"session_key": sk, "driver_number": 1, "date": date, "x": 1, "y": 2, "_id": 10 - i}).encode()
            s._publish(struct.pack("!H", len(topic)) + topic + row)
        rows = s.drain("location", 11404)
        self.assertEqual(len(rows), 1)                                  # other session filtered out
        self.assertEqual(rows[0]["date"], "2026-11-01T21:57:18.153000+00:00")   # UTC offset added
        self.assertEqual(s.drain("location"), [])                       # drained

    def test_remaining_length_roundtrip(self):
        from f1hub.stream import Stream, _varint

        class FakeSocket:
            def __init__(self, data):
                self.data = data

            def recv(self, n):
                chunk, self.data = self.data[:n], self.data[n:]
                return chunk

        for n in (0, 127, 128, 16383, 16384, 2097151):
            ptype, body = Stream(connect=False)._read(FakeSocket(bytes([0x30]) + _varint(n) + b"x" * n))
            self.assertEqual((ptype, len(body)), (3, n))


class StreamRecordingAndPush(unittest.TestCase):
    @staticmethod
    def _msg(topic, row):
        import json
        import struct
        t = f"v1/{topic}".encode()
        return struct.pack("!H", len(t)) + t + json.dumps(row).encode()

    def test_records_raw_messages_and_measures_lag(self):
        import gzip
        import json
        import tempfile
        import time
        from datetime import datetime, timezone
        from pathlib import Path
        from unittest import mock
        from f1hub import api
        from f1hub.stream import Stream
        with tempfile.TemporaryDirectory() as d, mock.patch.object(api, "RECORD_DIR", Path(d)):
            s = Stream(connect=False)
            seen = []
            s.on_message = seen.append
            happened = datetime.fromtimestamp(time.time() - 3, timezone.utc).isoformat()
            s._publish(self._msg("position", {"session_key": 1, "driver_number": 4, "position": 1,
                                              "date": happened, "_id": 7, "_key": "a"}))
            s._flush_record()
            lines = [json.loads(l) for l in gzip.open(next(Path(d).glob("*/stream.jsonl.gz")), "rt")]
        self.assertEqual(seen, ["position"])
        self.assertEqual((lines[0]["topic"], lines[0]["row"]["_key"]), ("position", "a"))
        self.assertAlmostEqual(lines[0]["rx"], time.time(), delta=5)
        self.assertAlmostEqual(s.lag()["position"], 3, delta=0.5)

    def test_timing_message_rebuilds_frame_at_once(self):
        import threading
        import time
        from unittest import mock
        from f1hub import gui, live
        from f1hub.stream import Stream

        class Tracker:
            def __init__(self, key):
                self.key, self.session, self.stream, self.builds = key, {}, None, 0
            streaming = lambda self, e=None: True
            set_view = set_focus = lambda self, v: None
            def refresh(self):
                self.stream.drain("position")
                self.builds += 1
            snapshot = lambda self, season: {"rows": [], "data_time": time.time()}

        with mock.patch.object(gui.api, "authenticated", lambda: False), \
                mock.patch("f1hub.transcribe.Transcriber", lambda: None):
            f = gui.Feed(None, 5, 10)
        s = Stream(connect=False)
        s.connected, s.last_msg = True, time.time()
        s.on_message = f._on_stream
        f.stream = s
        with mock.patch.object(live, "LiveTracker", Tracker):
            threading.Thread(target=f.run, daemon=True).start()
            for _ in range(50):
                if f.frame_no:
                    break
                time.sleep(0.02)
            time.sleep(0.3)
            seen, before = f.frame_no, f.live_tracker.builds
            t0 = time.time()
            for n in range(20):                  # one burst: the whole field's positions
                s._publish(self._msg("position", {"session_key": 5, "driver_number": n, "position": n,
                                                  "date": "2026-11-07T14:00:00+00:00"}))
            got = f.wait_frame(seen, 0.8)
            took = time.time() - t0
            time.sleep(0.3)
        self.assertGreater(got, seen)
        self.assertLess(took, 0.5)               # not waiting for the 1 s tick
        self.assertLessEqual(f.live_tracker.builds - before, 2)   # one rebuild for the burst (+ maybe the 1 s tick)
        self.assertLessEqual(len(f.history), 2)  # delay buffer keeps ~1 frame a second


class LapTrace(unittest.TestCase):
    def test_trace_and_spoiler_cutoff(self):
        from f1hub import laptrace
        laps = [{"lap_number": 1, "date_start": "2026-11-01T20:00:00+00:00", "lap_duration": 90.0, "is_pit_out_lap": False},
                {"lap_number": 2, "date_start": "2026-11-01T20:01:30+00:00", "lap_duration": 80.0, "is_pit_out_lap": False},
                {"lap_number": 3, "date_start": "2026-11-01T20:02:50+00:00", "lap_duration": 85.0, "is_pit_out_lap": False}]
        def fake_get(endpoint, ttl=None, **p):
            if endpoint == "laps":
                return laps
            base = "2026-11-01T20:01:3"
            if endpoint == "location":
                return [{"date": f"{base}{i}+00:00", "x": i * 100, "y": 0} for i in range(5)]
            return [{"date": f"{base}{i}+00:00", "speed": 200 + i, "throttle": 100, "brake": 100 if i == 2 else 0, "n_gear": 7}
                    for i in range(5)]
        with mock.patch.object(laptrace.api, "get", fake_get):
            tr = laptrace.lap_trace(1, 1, "best")
            self.assertEqual(tr["lap"], 2)
            self.assertEqual([s[1] for s in tr["samples"]], [0.0, 0.25, 0.5, 0.75, 1.0])
            self.assertEqual([s[4] for s in tr["samples"]], [0, 0, 1, 0, 0])
            # watching at 20:02:00 (delayed / replay): lap 2 hadn't finished yet, so it can't be "best"
            self.assertEqual(laptrace.pick_lap(1, 1, "best", "2026-11-01T20:02:00+00:00")["lap_number"], 1)
            self.assertIsNone(laptrace.pick_lap(1, 1, "best", "2026-11-01T20:01:00+00:00"))


class RadioDownloads(unittest.TestCase):
    def test_only_f1_clips(self):
        from f1hub.radio import _check, clip_name
        ok = "https://livetiming.formula1.com/static/2026/2026-11-01_Mexico_City_Grand_Prix/2026-11-01_Race/TeamRadio/VER_3_20261101_162901.mp3"
        _check(ok)
        self.assertEqual(clip_name(ok, 71), "Mexico_City_Grand_Prix_L71_VER_162901.mp3")
        for bad in ("https://example.com/TeamRadio/a.mp3", "http://livetiming.formula1.com/static/TeamRadio/a.mp3",
                    "https://livetiming.formula1.com/static/2026/x.json", "file:///etc/passwd"):
            with self.assertRaises(ValueError):
                _check(bad)


class ReplayNoSpoilers(unittest.TestCase):
    def test_lap_in_progress_is_blanked(self):
        from datetime import datetime
        from f1hub.replay import ReplayTracker
        t = ReplayTracker.__new__(ReplayTracker)
        T = datetime.fromisoformat("2026-11-01T20:01:00+00:00").timestamp()
        lap = {"driver_number": 1, "lap_number": 2, "date_start": "2026-11-01T20:00:30+00:00", "lap_duration": 80.0,
               "duration_sector_1": 25.0, "duration_sector_2": 30.0, "duration_sector_3": 25.0, "i2_speed": 300,
               "_ts": datetime.fromisoformat("2026-11-01T20:00:30+00:00").timestamp()}
        future = dict(lap, lap_number=3, _ts=T + 50)
        out = t._at_time("laps", [lap, future], T)
        self.assertEqual(len(out), 1)                    # lap 3 hasn't started
        self.assertIsNone(out[0]["lap_duration"])        # lap 2 still running at T
        self.assertEqual(out[0]["duration_sector_1"], 25.0)
        self.assertIsNone(out[0]["duration_sector_2"])


class RadioTranscripts(unittest.TestCase):
    def test_without_venv_it_just_reports_unavailable(self):
        from pathlib import Path
        from f1hub import transcribe
        with mock.patch.object(transcribe, "VENV_PY", Path("/nonexistent/python")):
            t = transcribe.Transcriber()
        t.request(["https://livetiming.formula1.com/static/x/TeamRadio/VER_3_20261101_162901.mp3"])
        self.assertEqual(t.status()["state"], "unavailable")
        self.assertEqual(len(t.queue), 0)

    def test_newest_clip_goes_first(self):
        from pathlib import Path
        from f1hub import transcribe
        with mock.patch.object(transcribe, "VENV_PY", Path("/nonexistent/python")):
            t = transcribe.Transcriber()
        t.state = "idle"                                  # pretend it's installed, but don't start the worker
        t.get = lambda url: None
        t.request(["old.mp3", "new.mp3"])                 # callers pass oldest first
        self.assertEqual(t.queue.pop(), "new.mp3")


class RequestValidation(unittest.TestCase):
    def test_control_accepts_good_settings(self):
        from f1hub.gui import clean_control
        self.assertEqual(clean_control({"delay": "30", "spoiler": "1", "speed": "2"}),
                         {"delay": "30", "spoiler": "1", "speed": "2.0"})
        self.assertEqual(clean_control({"mode": "replay", "session": "9999", "at": "-120"})["at"], "-120.0")
        self.assertTrue(clean_control({"at": "2026-11-01T20:00:00+00:00"})["at"].startswith("2026-11-01T20"))

    def test_control_rejects_bad_settings(self):
        from f1hub.gui import clean_control, BadRequest
        for bad in ({"delay": "abc"}, {"delay": "9999"}, {"mode": "hack"}, {"session": "1;drop"},
                    {"speed": "100"}, {"at": "yesterday"}, {"at": "2026-11-01T20:00:00"}, {"evil": "1"}):
            with self.assertRaises(BadRequest, msg=bad):
                clean_control(bad)

    def test_lap_and_year(self):
        from f1hub.gui import clean_lap_query, clean_year, BadRequest
        self.assertEqual(clean_lap_query({"driver": "1", "lap": "best"}, 9999), (9999, 1, "best", None))
        for bad in ({"driver": "x"}, {"driver": "1", "lap": "-3"}, {"driver": "1", "before": "soon"},
                    {"driver": "1", "session": "abc"}):
            with self.assertRaises(BadRequest, msg=bad):
                clean_lap_query(bad, 9999)
        self.assertIsNone(clean_year(""))
        with self.assertRaises(BadRequest):
            clean_year("abc")
        with self.assertRaises(BadRequest):
            clean_year("1950")


class ReplayClock(unittest.TestCase):
    """Pausing / changing speed must land where the viewer clicked, not where the feed thread got to it."""
    def _feed(self, playing=True, speed=10):
        from datetime import datetime, timedelta, timezone
        from f1hub.gui import Feed

        class Tracker:
            def __init__(self, t): self.t = t
            def now(self): return self.t
            def advance(self, s): self.t += timedelta(seconds=s)

        class Store:
            session = {"date_start": "2026-11-01T20:00:00+00:00", "date_end": "2026-11-01T22:00:00+00:00"}

            def actual_end(self):
                return datetime.fromisoformat(self.session["date_end"])

        f = Feed.__new__(Feed)
        self.start = datetime(2026, 11, 1, 20, 30, tzinfo=timezone.utc)
        f.replay = {"store": Store(), "tracker": Tracker(self.start), "playing": playing, "speed": speed, "last": 1000.0}
        return f

    def test_slow_down_counts_from_the_click(self):
        import time as _t
        from unittest import mock
        f = self._feed(speed=10)
        with mock.patch("f1hub.gui.time.time", return_value=1003.0):     # applied 3 s after the last tick...
            f._replay_catch_up(until=1001.0)                             # ...but clicked 1 s after it
        self.assertEqual((f.replay["tracker"].now() - self.start).total_seconds(), 10.0)   # 1 s at 10x, not 3 s
        self.assertEqual(f.replay["last"], 1001.0)                       # the 2 s since the click run at the new speed

    def test_paused_clock_stands_still(self):
        from unittest import mock
        f = self._feed(playing=False)
        with mock.patch("f1hub.gui.time.time", return_value=1005.0):
            f._replay_catch_up()
        self.assertEqual(f.replay["tracker"].now(), self.start)

    def test_two_changes_at_once_keep_playing(self):
        from unittest import mock
        f = self._feed(speed=2)
        with mock.patch("f1hub.gui.time.time", return_value=1000.0):
            f._replay_catch_up(until=1000.0)        # zero time since the last tick
        self.assertTrue(f.replay["playing"])


class HomePage(unittest.TestCase):
    """Between sessions: what's on, what's next, the weekend and the latest results."""
    def _home(self, now):
        from datetime import datetime
        from types import SimpleNamespace
        from unittest import mock
        from f1hub import home as homemod

        def sess(key, name, mk, start, end, typ=None):
            return {"session_key": key, "session_name": name, "session_type": typ or name, "meeting_key": mk,
                    "location": "Town" if mk == 1 else "Ville", "country_name": "X", "circuit_short_name": "C",
                    "date_start": start, "date_end": end}
        rows = {
            "sessions": [sess(10, "Qualifying", 1, "2026-11-07T18:00:00+00:00", "2026-11-07T19:00:00+00:00"),
                         sess(11, "Race", 1, "2026-11-08T17:00:00+00:00", "2026-11-08T19:00:00+00:00"),
                         sess(20, "Practice 1", 2, "2026-11-20T00:30:00+00:00", "2026-11-20T01:30:00+00:00", "Practice"),
                         sess(21, "Race", 2, "2026-11-22T04:00:00+00:00", "2026-11-22T06:00:00+00:00")],
            "meetings": [{"meeting_key": 1, "meeting_name": "Town Grand Prix"}, {"meeting_key": 2, "meeting_name": "Ville Grand Prix"}],
            "session_result": [{"position": 2, "driver_number": 44, "gap_to_leader": 0.8, "points": 18},
                               {"position": 1, "driver_number": 1, "gap_to_leader": 0, "points": 25},
                               {"position": None, "driver_number": 16, "dnf": True}],
            "drivers": [{"driver_number": 1, "name_acronym": "VER", "team_colour": "3671C6"}],
        }
        season = SimpleNamespace(year=2026, rounds=[SimpleNamespace(meeting_key=1, number=20), SimpleNamespace(meeting_key=2, number=21)],
                                 completed_rounds=lambda: [])
        with mock.patch.object(homemod.api, "get", side_effect=lambda ep, **kw: rows[ep]):
            return homemod.home(season, now=datetime.fromisoformat(now))

    def test_between_weekends(self):
        h = self._home("2026-11-09T12:00:00+00:00")
        self.assertEqual(h["state"], "between")
        self.assertEqual((h["next"]["name"], h["next"]["meeting"], h["next"]["round"]), ("Practice 1", "Ville Grand Prix", 21))
        self.assertEqual([x["name"] for x in h["next_weekend"]], ["Practice 1", "Race"])
        res = h["last_race"]["results"]
        self.assertEqual([(r["pos"], r["code"]) for r in res], [(1, "VER"), (2, "44"), (None, "16")])
        self.assertEqual(res[2]["status"], "DNF")

    def test_live_session_and_saturday_night(self):
        self.assertEqual(self._home("2026-11-08T19:20:00+00:00")["state"], "live")      # podium time still counts
        h = self._home("2026-11-07T21:00:00+00:00")                                      # after qualifying
        self.assertEqual(h["state"], "between")
        self.assertEqual(h["last_session"]["name"], "Qualifying")
        self.assertEqual(h["next"]["name"], "Race")


class DelayBuffer(unittest.TestCase):
    """A delayed view runs on event time: OpenF1's wobbling arrival lag must not move the picture."""
    def _feed(self, frames, delay):
        import json, threading, zlib
        from collections import deque
        from f1hub.gui import Feed
        f = Feed.__new__(Feed)
        f.lock = threading.Lock()
        f.settings = {"mode": "live", "delay": delay, "spoiler": False}
        f.control_error, f.seek, f.boot, f.loading = None, 0, 1, None
        f.history = deque((wall, dt, zlib.compress(json.dumps(dict(s, data_time=dt)).encode()))
                          for wall, dt, s in frames)
        f.live = dict(frames[-1][2], lead_lap_start="LIVE")
        f._add_transcripts = lambda snap: None
        return f

    def test_picks_by_event_time_not_arrival(self):
        from unittest import mock
        # built every second, but the data inside arrived 3 s, 6 s, 3 s... after it happened
        frames = [(100 + i, 100 + i - (6 if i % 2 else 3), {"id": i}) for i in range(10)]
        f = self._feed(frames, delay=8)
        with mock.patch("f1hub.gui.time.time", return_value=110.0):
            snap = f.current()
        self.assertLessEqual(snap["data_time"], 102.0)              # nothing newer than 110 - 8 on screen
        self.assertEqual(snap["id"], 7)                              # newest event time that qualifies (101, built
        self.assertEqual(snap["delayed_by"], 9.0)                    # last) - not frame 9 just because it's newer
        self.assertEqual(snap["live_lead_lap_start"], "LIVE")       # quick sync measures against live data

    def test_holds_while_buffering(self):
        from unittest import mock
        f = self._feed([(100, 97, {"id": 0}), (101, 98, {"id": 1})], delay=30)
        with mock.patch("f1hub.gui.time.time", return_value=101.0):
            snap = f.current()
        self.assertEqual(snap["id"], 0)
        self.assertTrue(snap["delay_status"]["buffering"])
        self.assertEqual(snap["delay_status"]["ready_in"], 26.0)    # data 97 + 30 s delay = 127, 26 s from now

    def test_quali_clock_wound_to_the_moment_shown(self):
        from unittest import mock
        q = {"running": True, "remaining": 300}
        f = self._feed([(100, 96, {"quali": q}), (101, 97, {"quali": q})], delay=10)
        with mock.patch("f1hub.gui.time.time", return_value=107.0):
            snap = f.current()                                      # shows data 97, built at 101, for moment 97
        self.assertEqual(snap["quali"]["remaining"], 304)


class FIADocuments(unittest.TestCase):
    """Reading the stewards' paperwork from fia.com (f1hub/fia.py) - offline, on real document text."""
    DECISION = """                       2026 SÃO PAULO GRAND PRIX
From          The Stewards                                             Document      25
The Stewards, having received a report from the Race Director, have considered the following matter:

No / Driver    51 - Rafael Camara
Competitor TGR Haas F1 Team
Time           16:31
Session        Free Practice 2
Fact           Car 51 unnecessarily impeded Car 16 in turn 15.
Infringement Breach of Article B4.1.1 of the FIA F1 Regulations.
Decision       Driver: Reprimand (Driving).
               This is the driver's 1st reprimand of the season.
Reason         The Stewards heard from the driver of Car 51 and reviewed video
               evidence.

               Competitors are reminded that they have the right to appeal certain decisions of the
               Stewards, in accordance with Article 15 of the FIA International Sporting Code.
"""

    def test_document_titles(self):
        from f1hub.fia import parse_title
        t = parse_title("Doc 25 - Infringement - Car 51 - Unnecessarily impeding of Car 16")
        self.assertEqual((t["no"], t["kind"], t["cars"][0], t["offence"], t["stewarding"]),
                         (25, "infringement", 51, "Unnecessarily impeding of Car 16", True))
        self.assertFalse(parse_title("Doc 27 - Curfew")["stewarding"])
        self.assertEqual(parse_title("Doc 9 - Summons - Cars 4 and 81 - Alleged collision")["cars"], [4, 81])

    def test_decision_fields(self):
        from f1hub.fia import parse_decision
        d = parse_decision(self.DECISION)
        self.assertEqual(d["no"], "51 - Rafael Camara")
        self.assertEqual(d["session"], "Free Practice 2")
        self.assertEqual(d["articles"], [{"ref": "B4.1.1", "source": "f1", "label": "Article B4.1.1"}])
        self.assertTrue(d["decision"].startswith("Driver: Reprimand"))
        self.assertNotIn("appeal", d["reason"])          # the boilerplate isn't part of the reasons
        self.assertEqual(d["document"], 25)

    def test_cited_rules(self):
        from f1hub.fia import cited_articles
        self.assertEqual(cited_articles("Breach of Appendix L, Chapter IV, Article 2 d) of the FIA International Sporting Code.")[0]["ref"], "L-IV-2d")
        self.assertEqual(cited_articles("Breach of Article B1.6.2a of the FIA F1 Regulations.")[0]["ref"], "B1.6.2a")
        old = cited_articles("Breach of Article 33.4 of the FIA Formula One Sporting Regulations and Article 12.2.1.l of the FIA International Sporting Code")
        self.assertEqual([(a["ref"], a["source"]) for a in old], [("33.4", "f1"), ("12.2.1", "isc")])

    def test_matching_needs_the_same_offence(self):
        from f1hub.fia import match, parse_title
        docs = [dict(parse_title(t), title=t, url="u", published="") for t in
                ("Doc 42 - Infringement - Car 12 - Erratic Driving", "Doc 20 - Summons - Car 6 - Alleged Failure to follow Race Director's Instructions")]
        m = match(docs, [12], "Q1 INCIDENT INVOLVING CAR 12 (ANT) NOTED - FAILING TO FOLLOW RACE DIRECTORS INSTRUCTIONS")
        self.assertEqual([x["score"] for x in m], [0])     # car 12's only decision is about something else
        self.assertEqual(match(docs, [6], "CAR 6 (HAD) FAILING TO FOLLOW RACE DIRECTOR INSTRUCTIONS")[0]["score"], 3)

    def test_same_session_names(self):
        from f1hub.gui import same_session
        self.assertTrue(same_session("Free Practice 2", "Practice 2"))
        self.assertTrue(same_session("Sprint Qualifying", "Sprint Shootout"))
        self.assertFalse(same_session("Race", "Qualifying"))

    def test_only_fia_urls(self):
        from f1hub import fia
        self.assertTrue(fia.is_fia_url("https://www.fia.com/system/files/decision-document/x.pdf"))
        for bad in ("http://www.fia.com/x.pdf", "https://fia.com.evil.example/x.pdf", "file:///etc/passwd"):
            self.assertFalse(fia.is_fia_url(bad))
        with self.assertRaises(fia.FIAError):
            fia.decision("https://evil.example/x.pdf")


class CrashDetection(unittest.TestCase):
    """f1hub/incidents.py on synthetic position traces (units/s: 505 ~ 205 km/h, 4 samples a second)."""
    def _drive(self, det, profile, n=1, start=1000.0, suppressed=False):
        """profile: [(seconds, speed in units/s), ...] driven along a straight line."""
        t, x = start, 0.0
        for dur, v in profile:
            for _ in range(int(dur * 4)):
                t += 0.25
                x += v * 0.25
                det.add(n, t, x, 0.0, suppressed=suppressed)
        return t

    def test_high_speed_to_standstill_is_a_crash(self):
        from f1hub.incidents import IncidentDetector
        det = IncidentDetector()
        self._drive(det, [(10, 700), (1.5, 300), (10, 0)])
        self.assertEqual([(e["kind"], e["state"]) for e in det.events], [("crash", "stopped")])

    def test_pit_stop_is_not_a_crash(self):
        from f1hub.incidents import IncidentDetector
        det = IncidentDetector()
        self._drive(det, [(10, 700), (1, 400), (6, 197), (1, 80), (25, 0)])     # brake to the limiter, then the box
        self.assertEqual(det.events, [])

    def test_spin_and_drive_off(self):
        from f1hub.incidents import IncidentDetector
        det = IncidentDetector()
        self._drive(det, [(10, 600), (1, 200), (4, 0), (2, 150), (6, 500)])
        self.assertEqual([(e["kind"], e["state"]) for e in det.events], [("spin", "rejoined")])

    def test_slow_corner_and_red_flag_are_quiet(self):
        from f1hub.incidents import IncidentDetector
        det = IncidentDetector()
        self._drive(det, [(10, 700), (2, 130), (10, 600)])                       # a hairpin at ~50 km/h
        self._drive(det, [(10, 700), (1.5, 300), (10, 0)], n=2, suppressed=True)  # stopping under a red flag
        self.assertEqual(det.events, [])

    def test_slow_stop_needs_race_control(self):
        from f1hub.incidents import IncidentDetector
        det = IncidentDetector()
        end = self._drive(det, [(10, 300), (4, 120), (20, 0)])                  # coasting to a halt
        self.assertEqual([e["kind"] for e in det.events], ["stopped"])
        info = lambda n: {"number": n, "code": "ALB", "colour": "#fff"}
        place = lambda x, y: {"corner": 3, "sector": 2}
        self.assertEqual(det.snapshot(end, info, place, lambda e, w: ["Red flag"]), [])        # not specific enough
        shown = det.snapshot(end, info, place, lambda e, w: ["Double Yellow In Track Sector 2"])
        self.assertEqual((shown[0]["kind"], shown[0]["corner"]), ("stopped", 3))


class StewardsGridAndPoints(unittest.TestCase):
    """f1hub/stewards.py: B2.5.4b grid order, and what a decision document's wording means."""
    def order(self, n, unclassified=()):
        return [{"number": i, "code": f"D{i}", "quali_pos": i, "classified": i not in unclassified} for i in range(1, n + 1)]

    def grid(self, order, pens):
        from f1hub.stewards import provisional_grid
        return [g["number"] for g in provisional_grid(order, pens)]

    def test_no_penalties_is_the_quali_order(self):
        self.assertEqual(self.grid(self.order(5), {}), [1, 2, 3, 4, 5])

    def test_drop_and_tie(self):
        # P1 +3 -> slot 4; P2 +2 -> slot 4 too: the slower one (P2) keeps 4, P1 goes ahead to 3
        g = self.grid(self.order(6), {1: {"places": 3}, 2: {"places": 2}})
        self.assertEqual(g, [3, 4, 1, 2, 5, 6])

    def test_drop_past_the_end_closes_up(self):
        g = self.grid(self.order(5), {4: {"places": 10}})
        self.assertEqual(g, [1, 2, 3, 5, 4])

    def test_back_of_grid_and_pit_lane(self):
        from f1hub.stewards import provisional_grid
        out = provisional_grid(self.order(6, unclassified=(6,)), {1: {"places": 30, "back": True}, 2: {"back": True},
                                                                   3: {"pit_lane": True}})
        self.assertEqual([g["number"] for g in out], [4, 5, 1, 2, 6, 3])
        self.assertTrue(out[-1]["pit_lane"])
        self.assertIsNone(out[-1]["pos"])
        self.assertEqual(out[0]["moved"], 3)

    def test_decision_wording(self):
        from f1hub.stewards import record
        doc = {"title": "Doc 39 - Infringement - Car 11 - Changes to PU elements", "url": "u", "published": "07.11.26 17:31 CET",
               "cars": [11], "no": 39}
        r = record(doc, {"no": "11 - Sergio Perez", "session": "Free Practice 3",
                         "decision": "Drop of 30 grid positions for the next Race in which the driver participates."},
                   "São Paulo Grand Prix", 2026)
        self.assertEqual((r["number"], r["name"]), (11, "Sergio Perez"))
        self.assertEqual(r["grid"], {"places": 30, "back": True, "pit_lane": False, "target": "Race", "later": False})
        r = record(doc, {"no": "43 - Franco Colapinto", "session": "Race",
                         "decision": "10 second time penalty imposed after the Race.\n1 penalty points (total of 5 for the 12 month period)."},
                   "Mexico City Grand Prix", 2026)
        self.assertEqual((r["points"], r["points_total"], r["grid"]), (1, 5, None))
        r = record(doc, {"no": "87 - Oliver Bearman", "session": "Qualifying",
                         "decision": "Driver: Reprimand (Driving).\nThis is the driver’s 2nd reprimand of the season."}, "x", 2026)
        self.assertEqual((r["reprimand"], r["reprimand_no"]), ("driving", 2))
        r = record(doc, {"no": "11 - X", "session": "Free Practice 3", "decision": "Competitor: Reprimand."}, "x", 2026)
        self.assertIsNone(r["reprimand"])
        r = record(doc, {"no": "22 - Y", "session": "Qualifying",
                         "decision": "The driver is required to start the race from the pit lane."}, "x", 2026)
        self.assertTrue(r["grid"]["pit_lane"])
        r = record(doc, {"no": "18 - Z", "session": "Sprint", "decision": "Drop of 5 grid positions at the driver's next race."}, "x", 2026)
        self.assertFalse(r["grid"]["later"])          # a Sprint incident is served on that weekend's Race grid
        r = record(doc, {"no": "5 - W", "session": "Race", "decision": "Drop of 5 grid positions at the driver's next race."}, "x", 2026)
        self.assertTrue(r["grid"]["later"])

    def test_points_expire_after_12_months(self):
        from datetime import datetime, timezone
        from f1hub.stewards import points_table
        base = {"name": "A B", "number": 1, "event": "E", "year": 2025, "session": "Race", "title": "t", "url": "u",
                "reprimand": None, "points_total": None}
        recs = [dict(base, points=2, published="01.10.25 12:00 CET"), dict(base, points=3, published="01.12.25 12:00 CET"),
                dict(base, points=1, published="01.06.26 12:00 CET", year=2026)]
        t = points_table(recs, datetime(2026, 11, 7, tzinfo=timezone.utc))[0]
        self.assertEqual(t["points"], 4)
        self.assertEqual(t["next_expiry"]["expires"], "2026-12-01")


if __name__ == "__main__":
    unittest.main()
