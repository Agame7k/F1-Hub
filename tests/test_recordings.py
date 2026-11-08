"""Recordings of live OpenF1 data (f1hub/recordings.py) and the MQTT subscription check (f1hub/stream.py)."""
import gzip
import json
import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from f1hub import api, recordings
from f1hub.stream import Stream
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)

SESSION = {"session_key": 11404, "meeting_key": 1300, "session_name": "Race", "session_type": "Race",
           "location": "Mexico City", "year": 2026, "date_start": "2026-11-01T20:00:00+00:00",
           "date_end": "2026-11-01T22:00:00+00:00"}


def iso(h, m, s=0):
    return datetime(2026, 11, 1, h, m, s, tzinfo=timezone.utc).isoformat()


def ts(h, m, s=0):
    return datetime(2026, 11, 1, h, m, s, tzinfo=timezone.utc).timestamp()


class WithRecordDir(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for p in (mock.patch.object(api, "RECORD_DIR", self.dir), mock.patch.object(api, "RECORDINGS", self.dir)):
            p.start()
            self.addCleanup(p.stop)
        recordings._day_index.clear()
        recordings._stores.clear()


class Writing(WithRecordDir):
    def test_one_folder_per_session(self):
        rec = recordings.SessionRecording(SESSION)
        rec.rest("position", "u", {"session_key": 11404}, [{"session_key": 11404, "position": 1}])
        rec.stream([(1.0, "laps", {"session_key": 11404, "lap_number": 3})])
        folder = self.dir / "openf1" / "11404_Mexico_City_Race"
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["position.jsonl.gz", "session.json", "stream.jsonl.gz"])
        self.assertEqual(json.loads((folder / "session.json").read_text())["session"]["session_key"], 11404)
        self.assertIs(recordings.recording_for(11404), rec)

    def test_nothing_once_the_session_is_over(self):
        rec = recordings.SessionRecording(SESSION)
        rec.on = False
        rec.rest("position", "u", {}, [{"position": 1}])
        self.assertFalse((self.dir / "openf1").exists())

    def test_recording_off_still_replays_what_is_there(self):
        recordings.SessionRecording(SESSION).rest("laps", "u", {}, [{"session_key": 11404, "lap_number": 1}])
        with mock.patch.object(api, "RECORD_DIR", None):           # F1HUB_RECORD=0
            self.assertIsNone(recordings.SessionRecording(SESSION).folder)
            self.assertEqual([e["session_key"] for e in recordings.sessions()], [11404])


class Reading(WithRecordDir):
    def setUp(self):
        super().setUp()
        rec = recordings.SessionRecording(SESSION)
        rec.rest("sessions", "u", {"session_key": "latest"}, [SESSION])
        lap = {"session_key": 11404, "driver_number": 1, "lap_number": 5, "date_start": iso(20, 30), "lap_duration": None}
        with mock.patch("time.time", return_value=ts(20, 30, 50)):     # (polled within the last 90 s: not catching up)
            rec.rest("laps", "u", {"session_key": 11404}, [lap])
        with mock.patch("time.time", return_value=ts(20, 31, 40)):
            rec.rest("laps", "u", {"session_key": 11404}, [dict(lap, lap_duration=90.0)])     # the same lap, finished
        rec.stream([(ts(20, 30, 2), "position", {"session_key": 11404, "driver_number": 1, "position": 1,
                                                 "date": iso(20, 30), "_id": 1}),
                    (ts(20, 30, 3), "position", {"session_key": 11404, "driver_number": 4, "position": 2,
                                                 "date": iso(20, 30, 1), "_id": 2})])
        with mock.patch("time.time", return_value=ts(20, 30, 9)):     # the same row again, by REST
            rec.rest("position", "u", {"session_key": 11404},
                     [{"session_key": 11404, "driver_number": 1, "position": 1, "date": iso(20, 30)}])
        rec.rest("location", "u", {"session_key": 11404}, [
            {"session_key": 11404, "driver_number": d, "date": iso(20, 30, s), "x": s, "y": d} for d in (1, 4) for s in range(10)])

    def test_listed(self):
        found = recordings.sessions()
        self.assertEqual([(e["key"], e["session_key"], e["layout"]) for e in found], [(100011404, 11404, "session")])

    def test_store_merges_and_keeps_the_latest_version(self):
        st = recordings.RecordingStore(recordings.key_for(11404))
        self.assertEqual(st.session["location"], "Mexico City")
        laps = st.get("laps", 0)
        self.assertEqual([(l["lap_number"], l["lap_duration"]) for l in laps], [(5, 90.0)])
        pos = st.get("position", 0)
        self.assertEqual(len(pos), 2)                                  # REST and stream copies counted once
        self.assertNotIn("_id", pos[0])

    def test_location_windows(self):
        st = recordings.store(recordings.key_for(11404))
        rows = st.query("location", session_key=11404, **{"date>": iso(20, 30, 2), "date<": iso(20, 30, 5)})
        self.assertEqual(sorted((r["driver_number"], r["x"]) for r in rows), [(1, 3), (1, 4), (4, 3), (4, 4)])
        self.assertEqual(len(st.query("location", driver_number=4, **{"date>=": iso(20, 30, 8)})), 2)
        self.assertEqual(st.query("sessions", session_key=11404), [SESSION])

    def test_latency(self):
        lat = recordings.latency(recordings.key_for(11404))["endpoints"]
        self.assertEqual(lat["position"]["stream"], {"median": 2.0, "p90": 2.0, "n": 2})
        self.assertNotIn("rest", lat["position"])                     # it came by stream first
        self.assertEqual(lat["laps"]["rest"]["median"], 10.0)           # from the end of the lap to its time arriving

    def test_catching_up_isnt_latency(self):
        rec = recordings.SessionRecording(dict(SESSION, session_key=11405))

        def pos(n, h, m, s):
            return {"session_key": 11405, "driver_number": n, "position": n, "date": iso(h, m, s)}
        with mock.patch("time.time", return_value=ts(20, 40)):       # started late: the first answer has the last 10 min
            rec.rest("position", "u", {"session_key": 11405}, [pos(1, 20, 30, 0), pos(2, 20, 35, 0)])
        with mock.patch("time.time", return_value=ts(20, 40, 4)):
            rec.rest("position", "u", {"session_key": 11405, "date>": iso(20, 35)}, [pos(3, 20, 40, 1)])
        with mock.patch("time.time", return_value=ts(20, 50)):       # after a 10-minute gap: catching up again
            rec.rest("position", "u", {"session_key": 11405, "date>": iso(20, 40, 1)}, [pos(4, 20, 45, 0)])
        lat = recordings.latency(recordings.key_for(11405))["endpoints"]
        self.assertEqual(lat["position"]["rest"], {"median": 3.0, "p90": 3.0, "n": 1})

    def test_older_per_day_files(self):
        day = self.dir / "2026-11-01"
        day.mkdir()
        with gzip.open(day / "position.jsonl.gz", "at", encoding="utf-8") as f:
            for i in range(80):
                f.write(json.dumps({"t": ts(21, 0, i % 60), "endpoint": "position", "params": {"session_key": 999},
                                    "url": "u", "data": [{"session_key": 999, "driver_number": i, "position": 1,
                                                          "date": iso(21, 0, i % 60), "pad": "x" * 100}]}) + "\n")
        with gzip.open(day / "sessions.jsonl.gz", "at", encoding="utf-8") as f:
            f.write(json.dumps({"t": 1, "endpoint": "sessions", "params": {"session_key": "latest"}, "url": "u",
                                "data": [dict(SESSION, session_key=999, date_start="2026-10-31T20:00:00+00:00")]}) + "\n")
        keys = [e["session_key"] for e in recordings.sessions()]
        self.assertEqual(keys, [11404, 999])
        st = recordings.RecordingStore(recordings.key_for(999))
        self.assertEqual(len(st.get("position", 0)), 80)


class StreamAndPolls(WithRecordDir):
    """A streamed row and its REST copy are the same row (replays show it once), and the delays only time a row when
    the poll before it could have returned it."""
    MSG = {"session_key": 11404, "date": iso(20, 31), "category": "Flag", "flag": "YELLOW", "scope": "Sector",
           "sector": 3, "message": "YELLOW IN TRACK SECTOR 3", "lap_number": 4}

    def poll(self, rec, at, params, rows):
        with mock.patch("time.time", return_value=at):
            rec.rest("race_control", "u", dict(params, session_key=11404), rows)

    def test_streamed_and_polled_copies_are_one_row(self):
        rec = recordings.SessionRecording(SESSION)
        rec.stream([(ts(20, 31, 2), "race_control", dict(self.MSG, date=iso(20, 31)[:19], _id=7))])  # no UTC offset
        self.poll(rec, ts(20, 31, 20), {}, [self.MSG])
        rows, first = recordings._merge(11404)
        self.assertEqual(len(rows["race_control"]), 1)
        self.assertEqual([v for (ep, _), v in first.items() if ep == "race_control"], [(ts(20, 31, 2), "stream")])

    def test_a_row_the_poll_before_couldnt_return_isnt_timed(self):
        rec = recordings.SessionRecording(SESSION)
        early, late = dict(self.MSG, date=iso(20, 30, 20)), dict(self.MSG, date=iso(20, 31, 10), sector=4)
        self.poll(rec, ts(20, 30), {}, [])                              # F1 Hub starts: catching up
        self.poll(rec, ts(20, 31), {"date>": iso(20, 30, 50)}, [])      # (the stream had moved it on to 20:30:50)
        self.poll(rec, ts(20, 31, 30), {}, [early, late])               # restarted: everything again
        lat = recordings.latency(recordings.key_for(11404))["endpoints"]["race_control"]
        self.assertEqual(lat, {"rest": {"median": 20.0, "p90": 20.0, "n": 1}})       # only the 20:31:10 one

    def test_reach(self):
        self.assertFalse(recordings._in_reach({"date>": iso(20, 31)}, {"date": iso(20, 31)}))
        self.assertTrue(recordings._in_reach({"date>=": iso(20, 31)}, {"date": iso(20, 31)}))
        self.assertFalse(recordings._in_reach({"lap_number>=": 5}, {"lap_number": 3}))
        self.assertTrue(recordings._in_reach({"session_key": 1, "lap_number>=": "5"}, {"lap_number": 6}))


class Subscription(unittest.TestCase):
    def test_refused_topics(self):
        s = Stream(topics=("position", "location"), connect=False)
        s._suback(struct.pack("!H", 1) + bytes([0, 0x80]))
        self.assertTrue(s.subscribed)
        self.assertEqual(s.refused, ["location"])
        self.assertIn("location", s.error)

    def test_everything_refused(self):
        s = Stream(topics=("position", "location"), connect=False)
        with self.assertRaisesRegex(RuntimeError, "refused"):
            s._suback(struct.pack("!H", 1) + bytes([0x80, 0x80]))
        self.assertFalse(s.subscribed)

    def test_not_healthy_until_confirmed(self):
        import time
        s = Stream(connect=False)
        s.connected, s.last_msg = True, time.time()
        self.assertFalse(s.healthy)
        s._suback(struct.pack("!H", 1) + bytes(len(s.topics)))
        self.assertTrue(s.healthy)


if __name__ == "__main__":
    unittest.main()
