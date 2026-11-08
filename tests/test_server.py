"""The dashboard server (f1hub/gui.py): who may use the API, the routes, loading the season in the background,
and the pieces around it - the home page before the season is in, the OpenF1 login, replay telemetry by the
minute, logging, `doctor` and `cleanup`."""
import io
import json
import logging
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from f1hub import api, cleanup, doctor, ff1, gui, logs, replay
from f1hub.season import Season
import isolation
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)

UTC = timezone.utc


class TempDir(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def patch(self, *patches):
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


# ------------------------------------------------------------------------------------------------ who may ask
class Trusted(unittest.TestCase):
    allowed = gui.allowed_hosts(8765)

    def test_own_page(self):
        self.assertTrue(gui.trusted({"Host": "localhost:8765"}, self.allowed))
        self.assertTrue(gui.trusted({"Host": "127.0.0.1:8765", "Sec-Fetch-Site": "same-origin",
                                     "Origin": "http://127.0.0.1:8765"}, self.allowed))
        self.assertTrue(gui.trusted({"Host": "localhost:8765", "Sec-Fetch-Site": "none"}, self.allowed))   # typed in

    def test_other_sites(self):
        self.assertFalse(gui.trusted({"Host": "localhost:8765", "Sec-Fetch-Site": "cross-site"}, self.allowed))
        self.assertFalse(gui.trusted({"Host": "localhost:8765", "Origin": "https://evil.example"}, self.allowed))
        self.assertFalse(gui.trusted({"Host": "evil.example:8765"}, self.allowed))        # DNS rebinding
        self.assertFalse(gui.trusted({}, self.allowed))


# ------------------------------------------------------------------------------------------------ the server
class StubFeed:
    def __init__(self):
        self.season = None
        self.stream = None
        self.replay = None
        self.controls = []

    def current(self):
        return {"error": "Connecting to OpenF1..."}

    def hidden_meeting(self):
        return None

    def control(self, **kw):
        self.controls.append(kw)
        return len(self.controls)


class Server(unittest.TestCase):
    """A real server on a spare port, with the feed stood in for."""

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        self.port = self.server.server_address[1]
        self.feed = StubFeed()
        self.app = gui.App(self.feed, self.port, 2026)
        self.server.RequestHandlerClass = gui.make_handler(self.app)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers.get("Content-Type"), e.read()

    def test_routes(self):
        code, ctype, body = self.get("/")
        self.assertEqual((code, ctype), (200, "text/html; charset=utf-8"))
        self.assertEqual(self.get("/api/nothing")[0], 404)
        code, _, body = self.get("/api/view?tab=live&cid=a")
        self.assertEqual((code, json.loads(body)), (200, {"ok": True}))
        self.assertEqual(self.feed.controls, [{"view": "live", "cid": "a"}])
        self.assertEqual(self.get("/api/view?tab=x1")[0], 400)
        self.assertEqual(self.get("/static/..%2Fgui.py")[0], 404)
        self.assertEqual(self.get("/static/nothing.js")[0], 404)

    def test_season_loading_is_a_503_that_says_so(self):
        code, _, body = self.get("/api/season")
        self.assertEqual(code, 503)
        body = json.loads(body)
        self.assertTrue(body["loading"])
        self.assertIn("Loading the 2026 season", body["error"])
        self.assertEqual(body["season_status"]["state"], "loading")

    def test_other_sites_are_turned_away(self):
        self.assertEqual(self.get("/api/live", {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.get("/api/live", {"Host": f"evil.example:{self.port}"})[0], 403)
        self.assertEqual(self.get("/api/live", {"Sec-Fetch-Site": "cross-site"})[0], 403)

    def test_health(self):
        code, _, body = self.get("/api/health")
        body = json.loads(body)
        self.assertEqual(code, 200)
        self.assertFalse(body["restart_needed"])
        self.assertEqual(body["season"]["state"], "loading")

    def test_every_route_is_an_app_method(self):
        for path, fn in gui.ROUTES.items():
            self.assertIs(getattr(gui.App, fn.__name__), fn, path)
        with self.assertRaises(gui.NotFound):
            self.app.route("/api/nothing", {})


# ------------------------------------------------------------------------------------------------ the season
class FakeSeason:
    outcome = None          # a Season-like object to return, or an exception to raise

    def __init__(self, year):
        self.year = year

    def load(self, progress=None):
        progress("calendar")
        if isinstance(FakeSeason.outcome, Exception):
            raise FakeSeason.outcome
        return FakeSeason.outcome


class SeasonLoading(unittest.TestCase):
    def setUp(self):
        self.feed = StubFeed()
        self.changed = []
        self.loader = gui.SeasonLoader(self.feed, 2026, on_change=lambda: self.changed.append(1))
        p = mock.patch("f1hub.season.Season", FakeSeason)
        p.start()
        self.addCleanup(p.stop)

    def test_not_ready_while_loading(self):
        with self.assertRaisesRegex(gui.SeasonNotReady, "Loading the 2026 season"):
            self.loader.season()

    def test_ready(self):
        FakeSeason.outcome = SimpleNamespace(missing=[])
        self.assertEqual(self.loader.load_once(), gui.SeasonLoader.RELOAD)
        self.assertIs(self.loader.season(), FakeSeason.outcome)
        self.assertEqual(self.loader.status()["state"], "ready")
        self.assertEqual(self.changed, [1])

    def test_missing_results_are_tried_again_soon(self):
        FakeSeason.outcome = SimpleNamespace(missing=["GP - Race"])
        self.assertEqual(self.loader.load_once(), gui.SeasonLoader.RETRY)
        self.assertEqual(self.loader.status()["missing"], ["GP - Race"])

    def test_locked_then_failed_then_stale(self):
        FakeSeason.outcome = api.LiveSessionLock("A live F1 session is in progress")
        with self.assertLogs("f1hub.gui", "WARNING"):
            self.assertEqual(self.loader.load_once(), gui.SeasonLoader.RETRY_LOCKED)
        self.assertEqual(self.loader.status()["state"], "failed")
        with self.assertRaisesRegex(gui.SeasonNotReady, "live F1 session"):
            self.loader.season()
        FakeSeason.outcome = SimpleNamespace(missing=[])
        self.loader.load_once()
        loaded = self.feed.season
        FakeSeason.outcome = api.APIError("Network error")
        with self.assertLogs("f1hub.gui", "WARNING"):
            self.assertEqual(self.loader.load_once(), gui.SeasonLoader.RETRY)
        self.assertEqual(self.loader.status()["state"], "stale")
        self.assertIs(self.loader.season(), loaded)          # the older copy is still shown


MEET = [{"meeting_key": k, "meeting_name": f"GP {k}", "country_name": "C", "location": "X", "circuit_short_name": "Y",
         "date_start": f"2025-03-{k // 10:02d}T00:00:00+00:00"} for k in (10, 20)]
SESS = [{"session_key": k // 10, "meeting_key": k, "session_name": "Race", "date_start": f"2025-03-{k // 10:02d}T10:00:00+00:00",
         "date_end": f"2025-03-{k // 10:02d}T12:00:00+00:00"} for k in (10, 20)]


class MissingResults(unittest.TestCase):
    def test_one_result_that_cant_be_had_doesnt_stop_the_season(self):
        def get(endpoint, ttl=None, **p):
            if endpoint == "session_result" and p["session_key"] == 2:
                raise api.LiveSessionLock("A live F1 session is in progress")
            return {"meetings": MEET, "sessions": SESS,
                    "session_result": [{"driver_number": 1, "position": 1, "points": 25}],
                    "drivers": [{"driver_number": 1, "name_acronym": "AAA"}]}.get(endpoint, [])
        with mock.patch.object(api, "get", get), self.assertLogs("f1hub.season", "WARNING"):
            s = Season(2025).load()
        self.assertEqual(s.missing, ["GP 20 - Race"])
        self.assertEqual([r.number for r in s.completed_rounds()], [1])


class HomeBeforeTheSeason(unittest.TestCase):
    def test_rounds_from_the_calendar(self):
        from f1hub import home as homemod

        def sess(key, name, mk, day):
            return {"session_key": key, "session_name": name, "session_type": name, "meeting_key": mk, "location": "X",
                    "country_name": "C", "date_start": f"2026-11-{day:02d}T10:00:00+00:00",
                    "date_end": f"2026-11-{day:02d}T12:00:00+00:00"}
        rows = {"sessions": [sess(1, "Race", 1, 1), sess(2, "Practice 1", 2, 6), sess(3, "Race", 2, 8)],
                "meetings": [], "session_result": [], "drivers": []}
        with mock.patch.object(homemod.api, "get", side_effect=lambda ep, **kw: rows[ep]):
            h = homemod.home(None, now=datetime(2026, 11, 7, tzinfo=UTC), year=2026)
        self.assertEqual((h["year"], h["rounds"], h["rounds_done"]), (2026, 2, 1))
        self.assertEqual((h["next"]["name"], h["next"]["round"]), ("Race", 2))


# ------------------------------------------------------------------------------------------------ the live feed
class RecordingSwitch(unittest.TestCase):
    def test_off_when_the_session_is_over_and_back_on(self):
        f = gui.Feed.__new__(gui.Feed)
        rec = SimpleNamespace(on=True)
        f.live_tracker = SimpleNamespace(recording=rec, session={"location": "Monza", "session_name": "Race"})
        f._record_switch(False)
        self.assertTrue(rec.on)
        with self.assertLogs("f1hub.gui", "INFO"):
            f._record_switch(True)
        self.assertFalse(rec.on)
        with self.assertLogs("f1hub.gui", "INFO"):
            f._record_switch(False)
        self.assertTrue(rec.on)
        f.live_tracker = SimpleNamespace(session={})       # a replay records nothing
        f._record_switch(True)


# ------------------------------------------------------------------------------------------------ OpenF1 login
class OpenF1Login(TempDir):
    def setUp(self):
        super().setUp()
        self.calls = []

        def urlopen(req, timeout=None):
            self.calls.append(req.full_url)
            if req.full_url.endswith("/token"):
                raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"bad credentials"))
            raise urllib.error.URLError("down")
        self.patch(mock.patch.dict(os.environ, {"OPENF1_USERNAME": "me", "OPENF1_PASSWORD": "wrong"}),
                   mock.patch.object(api, "_auth_token", isolation.REAL_AUTH_TOKEN),
                   mock.patch.object(api.urllib.request, "urlopen", urlopen),
                   mock.patch.dict(api._login, {"error": None, "at": 0.0}),
                   mock.patch.dict(api._token, {"value": None, "expires": float("inf")}),
                   mock.patch.object(api, "CACHE_DIR", self.dir), mock.patch.object(api, "offline", False),
                   mock.patch.object(api, "_throttle", lambda: None), mock.patch("time.sleep", lambda s: None))

    def test_turned_down_means_free_access_for_a_while(self):
        with self.assertLogs("f1hub.api", "WARNING") as logged:
            self.assertIsNone(api._auth_token())
            self.assertIsNone(api._auth_token())
        self.assertEqual(len(self.calls), 1)                # not asked again straight away
        self.assertIn("turned down", api.login_error())
        self.assertNotIn("wrong", " ".join(logged.output))
        with mock.patch("time.time", return_value=time.time() + api.LOGIN_RETRY + 1):
            api._auth_token()
        self.assertEqual(len(self.calls), 2)

    def test_a_cached_copy_still_answers(self):
        url = api._build_url("laps", {"session_key": 1})
        api.write_atomic(api._cache_path(url), json.dumps({"url": url, "fetched": time.time() - 3600, "data": ["old"]}))
        with self.assertLogs("f1hub.api", "WARNING"):
            self.assertEqual(api.get("laps", ttl=60, session_key=1), ["old"])


# ------------------------------------------------------------------------------------------------ replays
def iso(t):
    return datetime.fromtimestamp(t, UTC).isoformat()


class ReplayByTheMinute(unittest.TestCase):
    T0 = datetime(2026, 11, 1, 20, 0, tzinfo=UTC).timestamp()

    def tracker(self, at, finished=True):
        t = replay.ReplayTracker.__new__(replay.ReplayTracker)
        t.store = SimpleNamespace(finished=finished)
        t._t = datetime.fromtimestamp(at, UTC)
        return t

    def fetch(self, tracker, **params):
        asked = []

        def get(endpoint, ttl=None, **p):
            asked.append((p["date>="], p["date<"], p.get("driver_number"), ttl))
            start = datetime.fromisoformat(p["date>="]).timestamp()
            return [{"date": iso(start + s), "x": s} for s in range(0, 60, 5)]
        with mock.patch.object(replay.api, "get", get):
            rows = tracker._fetch("location", session_key=1, **params)
        return rows, asked

    def test_whole_minutes_cut_to_the_window(self):
        T0 = self.T0
        rows, asked = self.fetch(self.tracker(T0 + 90), **{"date>": iso(T0 + 50)})
        self.assertEqual([r["date"] for r in rows], [iso(T0 + s) for s in (55, 60, 65, 70, 75, 80, 85)])
        self.assertEqual(asked, [(iso(T0), iso(T0 + 60), None, None), (iso(T0 + 60), iso(T0 + 120), None, None)])
        _, again = self.fetch(self.tracker(T0 + 93), **{"date>": iso(T0 + 52)})
        self.assertEqual(again, asked)                     # the next tick asks for the same (cached) minutes

    def test_a_minute_still_filling_in_isnt_kept(self):
        T0 = self.T0
        with mock.patch.object(replay.time, "time", return_value=T0 + 100):
            _, asked = self.fetch(self.tracker(T0 + 90, finished=False), driver_number=4, **{"date>=": iso(T0 + 70)})
        self.assertEqual(asked, [(iso(T0 + 60), iso(T0 + 120), 4, 10)])


# ------------------------------------------------------------------------------------------------ logging
class Logging(TempDir):
    def record(self, level, msg, exc=None):
        return logging.LogRecord("f1hub.x", level, __file__, 1, msg, None,
                                 (type(exc), exc, None) if exc else None)

    def test_terminal_lines(self):
        fmt = logs._Terminal()
        self.assertEqual(fmt.format(self.record(logging.INFO, "Dashboard running")), "Dashboard running")
        self.assertEqual(fmt.format(self.record(logging.WARNING, "Careful")), "Warning: Careful")
        line = fmt.format(self.record(logging.ERROR, "Refresh failed", ValueError("bad data")))
        self.assertTrue(line.startswith("Error: Refresh failed: ValueError: bad data  (details: "))

    def test_recent_from_the_file(self):
        with mock.patch.object(api, "CACHE_DIR", self.dir):
            logs.log_file().parent.mkdir(parents=True)
            logs.log_file().write_text("2026-11-08 10:00:00,123 INFO f1hub.gui: started\n"
                                       "2026-11-08 10:00:05,000 WARNING f1hub.api: damaged cache entry x.json\n"
                                       "2026-11-08 10:00:09,000 ERROR f1hub.gui: /api/stats failed\n"
                                       "Traceback (most recent call last):\n"
                                       '  File "x.py", line 1, in <module>\n'
                                       "KeyError: 'points'\n"
                                       "2026-11-08 10:00:10,000 INFO f1hub.gui: still going\n")
            self.assertEqual(logs.recent_from_file(), [
                {"at": "2026-11-08 10:00:05", "level": "WARNING", "where": "f1hub.api", "message": "damaged cache entry x.json"},
                {"at": "2026-11-08 10:00:09", "level": "ERROR", "where": "f1hub.gui",
                 "message": "/api/stats failed -> KeyError: 'points'"}])


# ------------------------------------------------------------------------------------------------ doctor
class Doctor(TempDir):
    def test_settings_are_named_never_shown(self):
        env = self.dir / ".openf1.env"
        env.write_text("OPENF1_USERNAME=me@example.com\nOPENF1_PASSWORD='hunter2'\nexport F1_PASWORD=typo\n# note\n")
        env.chmod(0o644)
        with mock.patch.dict(os.environ, {"OPENF1_USERNAME": "me@example.com", "OPENF1_PASSWORD": "hunter2",
                                          "F1_USERNAME": "x"}):
            os.environ.pop("F1_PASSWORD", None)
            out = doctor.check_settings(env)
        text = json.dumps(out)
        for secret in ("hunter2", "me@example.com", "typo"):
            self.assertNotIn(secret, text)
        self.assertIn("OPENF1_USERNAME, OPENF1_PASSWORD", out[0]["detail"])
        self.assertIn("F1_PASWORD", text)                       # a misspelt name is pointed out
        self.assertIn("chmod 600", text)                        # readable by others
        self.assertIn("F1_USERNAME is set but F1_PASSWORD isn't", text)
        env.chmod(0o600)
        self.assertNotIn("chmod", json.dumps(doctor.check_settings(env)))

    def test_dashboard_checks(self):
        app = gui.App(StubFeed(), 8765, 2026)
        with mock.patch.object(ff1, "VENV_PY", self.dir / "no-python"), \
                mock.patch.object(ff1, "leftovers", lambda: {"sign_in": [66891], "recorder": []}), \
                mock.patch.object(api, "CACHE_DIR", self.dir), mock.patch.object(api, "RECORDINGS", self.dir / "rec"):
            out = doctor.dashboard_checks(app)
        by = {c["name"]: c for c in out["checks"]}
        self.assertEqual(by["Season"]["status"], "info")             # loading
        self.assertIn("kill 66891", by["Processes"]["fix"])
        self.assertEqual(by["FastF1"]["status"], "info")             # not installed
        self.assertFalse(out["restart_needed"])


# ------------------------------------------------------------------------------------------------ cleanup
class Cleanup(TempDir):
    def setUp(self):
        super().setUp()
        self.cache, self.rec = self.dir / "cache", self.dir / "recordings"
        self.cache.mkdir()
        self.patch(mock.patch.object(api, "CACHE_DIR", self.cache), mock.patch.object(api, "RECORDINGS", self.rec))

    def entry(self, endpoint, **params):
        url = api._build_url(endpoint, dict(session_key=1, **params))
        path = api._cache_path(url)
        api.write_atomic(path, json.dumps({"url": url, "fetched": 1, "data": [{"x": 1}] * 50}))
        return path

    def test_what_goes_and_what_stays(self):
        old = self.entry("location", **{"date>": "2026-11-01T20:00:03.2+00:00", "date<": "2026-11-01T20:00:05+00:00"})
        old_tel = self.entry("car_data", driver_number=4, **{"date>": "2026-11-01T20:00:03.2+00:00"})
        self.entry("location", **{"date>=": "2026-11-01T20:00:00+00:00", "date<": "2026-11-01T20:01:00+00:00"})
        self.entry("car_data", driver_number=4, **{"date>=": "2026-11-01T20:00:00+00:00", "date<": "2026-11-01T20:01:30+00:00"})
        self.entry("laps")
        stuck, fresh = self.cache / ".a.json.1-2.part", self.cache / ".b.json.1-2.part"
        stuck.write_text("x")
        fresh.write_text("x")
        os.utime(stuck, (time.time() - 2 * cleanup.PART_AGE,) * 2)
        groups = {what: paths for what, paths, _ in cleanup.plan()}
        self.assertEqual(sorted(groups["Replay windows in the cache (never reused)"]), sorted([old, old_tel]))
        self.assertEqual(groups["Unfinished writes (.part files)"], [stuck])

    def test_old_recordings_only_when_asked(self):
        now = time.time()
        for name, days in (("1_Old_Race", 100), ("2_New_Race", 5)):
            folder = self.rec / "openf1" / name
            folder.mkdir(parents=True)
            (folder / "session.json").write_text(json.dumps({"session": {"date_start": iso(now - days * 86400)}}))
            (folder / "laps.jsonl.gz").write_bytes(b"x")
        (self.rec / "2020-01-01").mkdir()
        ff = self.rec / "fastf1"
        ff.mkdir()
        for ext in (".txt", ".log", ".json"):
            (ff / f"old{ext}").write_text("x")
        os.utime(ff / "old.txt", (now - 100 * 86400,) * 2)
        self.assertEqual([what for what, _, _ in cleanup.plan()], ["Replay windows in the cache (never reused)",
                                                                    "Unfinished writes (.part files)"])
        found = cleanup.old_recordings(30, now)
        self.assertEqual([p.name for p in found], ["1_Old_Race", "2020-01-01", "old.txt", "old.log", "old.json"])
        with mock.patch("builtins.print"):
            cleanup.run(yes=False, recordings_days=30)
        self.assertTrue((self.rec / "openf1" / "1_Old_Race").exists())        # showing only: nothing deleted
        with mock.patch("builtins.print"):
            cleanup.run(yes=True, recordings_days=30)
        self.assertEqual(sorted(p.name for p in (self.rec / "openf1").iterdir()), ["2_New_Race"])
        self.assertEqual(list(ff.iterdir()), [])


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------------------------------------------ the data feed
from f1hub import datafeed  # noqa: E402


def feed_frame(**over):
    """A dashboard frame with just what the feed reads."""
    f = {"mode": "replay", "session_key": 9999, "seek": 1, "meeting": "Test GP", "session": "Race", "status": "GREEN",
         "lap": 12, "scheduled_laps": 57, "replay": {"playing": True, "speed": 4, "timeline": []},
         "rows": [{"pos": 1, "number": 1, "code": "VER", "gap": None, "interval": None, "compound": "MEDIUM", "out": None},
                  {"pos": None, "number": 4, "code": "NOR", "gap": None, "out": {"reason": "retired", "lap": 9}}],
         "map": {"cars": [{"number": 1, "code": "VER", "pos": 1, "x": 100, "y": -50, "trail": [[1000, 90, -40], [1250, 100, -50]]}]},
         "messages": [{"time": "14:02:00", "text": "GREEN LIGHT - PIT EXIT OPEN"}, {"time": "14:00:00", "text": "RISK OF RAIN 20%"}],
         "weather": {"track": 41.2, "air": 27.0, "rain": False}, "rc": {"sector_flags": {"7": "YELLOW"}}}
    f.update(over)
    return f


class DataFeed(unittest.TestCase):
    def test_channels(self):
        self.assertEqual(datafeed.parse_channels(""), set(datafeed.CHANNELS))
        self.assertEqual(datafeed.parse_channels("leaderboard, positions.ver,positions.44"),
                         {"leaderboard", "positions.VER", "positions.44"})
        for bad in ("nope", "leaderboard.VER", "positions.VERS", "positions.1;x", "positions."):
            with self.assertRaises(datafeed.BadChannels, msg=bad):
                datafeed.parse_channels(bad)

    def test_only_what_changed(self):
        sub = datafeed.Subscriber(datafeed.parse_channels("session,leaderboard,positions.VER,race_control"))
        first = sub.events(feed_frame())
        names = [c for c, _ in first]
        self.assertEqual(names[:3], ["session", "leaderboard", "positions.VER"])
        rc = [json.loads(t)["text"] for c, t in first if c == "race_control"]
        self.assertEqual(rc, ["RISK OF RAIN 20%", "GREEN LIGHT - PIT EXIT OPEN"])        # oldest first
        board = json.loads(dict(first)["leaderboard"])
        self.assertEqual(board[1]["out"], "retired")
        self.assertNotIn("sectors", board[0])
        car = json.loads(dict(first)["positions.VER"])
        self.assertEqual((car["x"], car["y"], car["t"]), (100, -50, 1250))
        self.assertEqual(sub.events(feed_frame()), [])                 # nothing new: nothing sent
        later = sub.events(feed_frame(lap=13, messages=[{"time": "14:05:00", "text": "SAFETY CAR DEPLOYED"}]
                                      + feed_frame()["messages"]))
        self.assertEqual([c for c, _ in later], ["session", "race_control"])
        self.assertEqual(json.loads(later[1][1])["text"], "SAFETY CAR DEPLOYED")

    def test_a_seek_starts_race_control_over(self):
        sub = datafeed.Subscriber({"race_control"})
        self.assertEqual(len(sub.events(feed_frame())), 2)
        self.assertEqual(len(sub.events(feed_frame(seek=2))), 2)

    def test_nothing_before_the_first_frame(self):
        self.assertEqual(datafeed.Subscriber(set(datafeed.CHANNELS)).events({"error": "Connecting to OpenF1..."}), [])


class FrameFeed(StubFeed):
    def __init__(self):
        super().__init__()
        self.frame_cv = threading.Condition()
        self.feed_streams = 0
        self.frame = feed_frame()

    def current(self):
        return self.frame

    def wait_frame(self, seen, timeout):
        time.sleep(0.05)
        return seen + 1


class FeedServer(Server):
    def setUp(self):
        super().setUp()
        self.feed = self.app.feed = FrameFeed()
        self.server.RequestHandlerClass = gui.make_handler(self.app)

    def first_events(self, path, headers=None, n=2):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.headers.get("Content-Type"), "text/event-stream")
            cors = r.headers.get("Access-Control-Allow-Origin")
            events = []
            while len(events) < n:
                line = r.readline().decode().strip()
                if line.startswith("event: "):
                    events.append(line[7:])
            return events, cors

    def test_streams_the_channels_asked_for(self):
        events, cors = self.first_events("/api/feed?channels=weather,track")
        self.assertEqual(events, ["weather", "track"])
        self.assertIsNone(cors)

    def test_bad_channel_is_a_400(self):
        code, _, body = self.get("/api/feed?channels=nope")
        self.assertEqual(code, 400)
        self.assertIn("unknown channel", json.loads(body)["error"])

    def test_other_sites_only_with_open_feed(self):
        evil = {"Origin": "https://overlay.example", "Sec-Fetch-Site": "cross-site"}
        self.assertEqual(self.get("/api/feed", evil)[0], 403)
        self.app.open_feed = True
        events, cors = self.first_events("/api/feed?channels=session", evil, n=1)
        self.assertEqual((events, cors), (["session"], "*"))
        self.assertEqual(self.get("/api/live", evil)[0], 403)            # only the feed opens up
        self.assertEqual(self.get("/api/feed", {"Host": f"evil.example:{self.port}"})[0], 403)    # DNS rebinding
