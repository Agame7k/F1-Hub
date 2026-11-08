"""Live polling (f1hub/live.py): rows that turn up late, or that the stream missed, still get in - and don't undo
what's newer. Asking only for rows newer than the newest one seen never finds those: the newest row has already moved
past them (a lap deleted after the stewards' check, team radio once its clip is ready, a stream reconnect)."""
import unittest
from datetime import datetime, timedelta, timezone

from f1hub.live import LiveTracker
from f1hub.racecontrol import RaceControl
from f1hub.stream import normalise_dates
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)

T0 = datetime(2026, 11, 8, 17, 0, tzinfo=timezone.utc)


def iso(s):
    return (T0 + timedelta(seconds=s)).isoformat()


def tracker(published=None, pushed=None):
    """A tracker whose REST answers come from `published` (endpoint -> rows OpenF1 has so far) and whose stream
    delivers `pushed` (endpoint -> rows, taken once)."""
    t = LiveTracker.__new__(LiveTracker)
    t.key, t._since, t._seen, t._row_at, t._rc_seen = 9999, {}, {}, {}, set()
    t.messages, t.rc, t.track_status, t.session = [], RaceControl(), "GREEN", {}
    t.published, t.pushed, t.asked = published or {}, pushed or {}, []

    def fetch(endpoint, **params):
        t.asked.append(params)
        since = params.get("date>")
        return [dict(r) for r in t.published.get(endpoint, [])
                if since is None or datetime.fromisoformat(r["date"]) > datetime.fromisoformat(since)]
    t._fetch = fetch
    t._due = lambda endpoint, tick=None: True
    t._pushed = lambda endpoint: t.pushed.pop(endpoint, [])
    return t


def pos(s, n, p):
    return {"date": iso(s), "driver_number": n, "position": p}


class LateRows(unittest.TestCase):
    def test_a_late_row_is_fetched(self):
        t = tracker({"position": [pos(10, 1, 1), pos(20, 2, 2)]})
        self.assertEqual(len(t._new("position")), 2)
        t.published["position"].append(pos(15, 3, 3))        # published after the row from 20 s
        self.assertEqual(t._new("position"), [pos(15, 3, 3)])
        self.assertEqual(t.asked[-1]["date>"], iso(20 - LiveTracker.LOOKBACK_DEFAULT))
        self.assertEqual(t._new("position"), [])              # and only once

    def test_a_streamed_row_older_than_the_newest_is_kept(self):
        t = tracker(pushed={"position": [pos(30, 1, 1)]})
        t._due = lambda endpoint, tick=None: False
        t._new("position")
        t.pushed["position"] = [pos(25, 2, 2)]
        self.assertEqual(t._new("position"), [pos(25, 2, 2)])

    def test_the_rest_copy_of_a_streamed_row_isnt_new(self):
        streamed = dict(pos(30, 1, 1), date=(T0 + timedelta(seconds=30)).isoformat(timespec="milliseconds"))
        t = tracker({"position": [pos(30, 1, 1)]}, pushed={"position": [streamed]})
        self.assertEqual(len(t._new("position")), 1)

    def test_a_late_row_doesnt_undo_a_newer_one(self):
        t = tracker()
        self.assertTrue(t._newest("position", pos(20, 1, 3)))
        self.assertFalse(t._newest("position", pos(15, 1, 5)))
        self.assertTrue(t._newest("position", pos(15, 2, 5)))          # (another driver)

    def test_lists_stay_in_time_order(self):
        radio = [{"date": iso(10)}, {"date": iso(30)}]
        LiveTracker._in_order(radio, [{"date": iso(20)}])
        self.assertEqual([r["date"] for r in radio], [iso(10), iso(20), iso(30)])


class LateMessages(unittest.TestCase):
    def msg(self, s, text, cat="SafetyCar"):
        return {"date": iso(s), "category": cat, "flag": None, "scope": None, "message": text, "lap_number": 1}

    def test_read_again_in_order(self):
        t = tracker()
        t._add_messages([self.msg(100, "SAFETY CAR DEPLOYED"), self.msg(200, "SAFETY CAR IN THIS LAP")])
        self.assertEqual(t.track_status, "GREEN")
        t._add_messages([self.msg(50, "VIRTUAL SAFETY CAR DEPLOYED")])     # came in late
        self.assertEqual(t.track_status, "GREEN")                           # not back under a VSC
        self.assertEqual([m["date"] for m in t.messages], [iso(50), iso(100), iso(200)])
        self.assertEqual([p["kind"] for p in t.rc.periods], ["vsc", "sc"])

    def test_in_order_isnt_read_again(self):
        t = tracker()
        t._add_messages([self.msg(100, "SAFETY CAR DEPLOYED")])
        rc = t.rc
        t._add_messages([self.msg(200, "SAFETY CAR IN THIS LAP")])
        self.assertIs(t.rc, rc)


class LapsOfLappedCars(unittest.TestCase):
    def tracker(self, laps):
        """laps: number -> (lap it's on, seconds ago that lap began)"""
        t = tracker()
        now = T0 + timedelta(hours=1)
        t.now = lambda: now
        t.lap_no = {n: lap for n, (lap, _) in laps.items()}
        t.laps_by = {n: {lap: {"start": (now - timedelta(seconds=ago)).isoformat()}} for n, (lap, ago) in laps.items()}
        t.retired = {}
        return t

    def test_from_the_slowest_car_running(self):
        t = self.tracker({1: (50, 30), 2: (48, 60), 3: (10, 3000)})      # 3 stopped long ago
        self.assertEqual(t._lap_floor(), 47)

    def test_retired_cars_dont_hold_it_back(self):
        t = self.tracker({1: (50, 30), 2: (12, 20)})
        t.retired = {2: {"lap": 12}}
        self.assertEqual(t._lap_floor(), 49)

    def test_everyone_stopped(self):                      # a red flag: as before, around the leader
        self.assertEqual(self.tracker({1: (50, 900), 2: (49, 900)})._lap_floor(), 49)


class StreamDates(unittest.TestCase):
    def test_rest_form(self):
        for given, want in [("2026-11-08T17:00:30", "2026-11-08T17:00:30+00:00"),
                            ("2026-11-08T17:00:30.123000", "2026-11-08T17:00:30.123000+00:00"),
                            ("2026-11-08T17:00:30Z", "2026-11-08T17:00:30+00:00"),
                            ("2026-11-08T17:00:30+00:00", "2026-11-08T17:00:30+00:00"),
                            ("2026-11-08T14:00:30-03:00", "2026-11-08T14:00:30-03:00")]:
            with self.subTest(given):
                self.assertEqual(normalise_dates({"date": given})["date"], want)


if __name__ == "__main__":
    unittest.main()
