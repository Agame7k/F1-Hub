"""Browser dashboard: python3 -m f1hub gui  ->  http://localhost:8765

One background thread talks to OpenF1 (so the 60 requests/minute limit holds no matter
how many browser tabs are open). With the MQTT stream, a timing message rebuilds the frame within
~0.1 s and /api/events pushes it to the page (Server-Sent Events); the page falls back to polling.

Modes
  live    - latest data, optionally shown N seconds late to match your TV/stream (delay)
  replay  - any session on a virtual clock: seek, play/pause, 0.25-64x (spoiler free)
"""
import errno
import json
import logging
import re
import signal
import zlib
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import api, datafeed, ff1
from .championship import max_remaining, title_status

log = logging.getLogger("f1hub.gui")
PAGE = Path(__file__).with_name("dashboard.html")
REPLAY_TICK = 1.0        # seconds of wall time between replay refreshes
MAX_SPEED = 64
HISTORY_SECONDS = 15 * 60
PUSH_DEBOUNCE = 0.08     # after a streamed timing message, wait this long for the rest of its burst (20 cars)
PUSH_MIN_GAP = 0.25      # ...but never rebuild the frame more than 4 times a second
HISTORY_STEP = 0.9       # the delay buffer keeps about one frame a second (the newest of each second)
MAX_EVENT_STREAMS = 4    # browsers allow ~6 connections per site; leave room for the page's other requests
MAX_FEED_STREAMS = 8     # /api/feed clients (overlays, scripts), counted apart from the dashboard's own
SESSION_OVER_TICK = 300  # s between refreshes once a session is over (late corrections still show up)
# streamed topics that change what's on screen right away; location / car_data ride along with the next frame
URGENT_TOPICS = {"position", "intervals", "laps", "race_control", "pit", "stints", "overtakes", "team_radio", "weather"}


def parse_time(v):
    """datetime.fromisoformat, plus the trailing 'Z' browsers send (toISOString) - Python before 3.11 rejects it."""
    v = v.strip()
    return datetime.fromisoformat(v[:-1] + "+00:00" if v[-1:] in ("Z", "z") else v)


def _fia_published(s):
    """'07.11.26 17:38 CET' (fia.com shows Paris time) -> aware datetime."""
    from zoneinfo import ZoneInfo
    try:
        return datetime.strptime(s.replace(" CET", "").strip(), "%d.%m.%y %H:%M").replace(tzinfo=ZoneInfo("Europe/Paris"))
    except (ValueError, KeyError):
        return None


def same_session(a, b):
    """'Free Practice 2' ~ 'Practice 2', 'Sprint Qualifying' ~ 'Sprint Shootout'..."""
    n = lambda x: re.sub(r"\s+", " ", re.sub(r"free|session|shootout", lambda m: "qualifying" if m.group(0) == "shootout" else "",
                                             (x or "").lower())).strip()
    return n(a) == n(b)


def fia_query(path, q, snap):
    """Stewards' paperwork from fia.com for the session on screen (f1hub/fia.py)."""
    from . import fia
    year = clean_year(q.get("year") or (snap.get("session_start") or "")[:4] or str(datetime.now().year))
    meeting = snap.get("meeting")
    try:
        if path == "/api/fia/article":
            ref = q.get("ref", "")
            if not re.fullmatch(r"(?:[A-F]?\d{1,3}(?:\.\d{1,3}){0,4}(?:\(?[a-z]\)?)?|L-[IVX]{1,4}-\d{1,2}[a-z]?)", ref):
                raise BadRequest("ref must look like B4.1.1, 33.4 or L-IV-2d")
            return fia.article(ref, year)
        if not meeting:
            raise BadRequest("no session on screen")
        cut = None
        before = q.get("before")
        if before:          # spoiler mode while delayed / replaying: nothing published after the moment on screen
            try:
                cut = parse_time(before)
            except ValueError:
                raise BadRequest("before must be an ISO time") from None
        if path == "/api/fia/points":
            return points_query(int(year), meeting, cut)
        if path == "/api/fia/grid":
            return grid_query(int(year), meeting, snap, cut, q.get("target"))
        docs = fia.event_documents(year, meeting)
        if cut:
            docs = [d for d in docs if (_fia_published(d["published"]) or cut) <= cut]
        page = fia.event_page(year, meeting)
        if path == "/api/fia/docs":
            return {"meeting": meeting, "year": year, "page": page, "docs": docs}
        if path == "/api/fia/stewarding":
            car = q.get("car", "")
            cars = [int(c) for c in car.split(",") if c.isdigit() and 1 <= int(c) <= 99][:4]
            text = q.get("text", "")[:400]
            found = fia.match(docs, cars, text)
            out = {"meeting": meeting, "year": year, "page": page, "docs": found[:6], "decision": None, "suggest": []}
            # a decision only counts if it's about the same offence (shared words) and the same session:
            # a car can have several decisions in one weekend
            final = [d for d in found if d["kind"] in ("infringement", "decision", "offence") and d["score"]]
            for cand in final[:3]:
                try:
                    dec = fia.decision(cand["url"])
                except fia.FIAError as e:
                    out["decision_error"] = str(e)
                    break
                if dec.get("session") and snap.get("session") and not same_session(dec["session"], snap["session"]):
                    continue
                out["decision"] = dict(dec, title=cand["title"], published=cand["published"])
                break
            if not out["decision"]:
                try:
                    out["suggest"] = fia.suggest(text, int(year))
                except fia.FIAError:
                    pass
            return out
        raise BadRequest("unknown FIA request")
    except fia.FIAError as e:
        return {"error": str(e)}


STEWARDS = None


def _index(year, meeting):
    global STEWARDS
    from . import stewards
    if STEWARDS is None:
        STEWARDS = stewards.Index()
    STEWARDS.ensure(year, meeting)
    return STEWARDS


def points_query(year, meeting, cut):
    """Penalty points (12-month window) and this season's reprimands, from every decision on fia.com."""
    from . import stewards
    ix = _index(year, meeting)
    now = cut or datetime.now(timezone.utc)
    return {"status": dict(ix.status), "drivers": stewards.points_table(ix.all(cut), now), "ban_at": stewards.BAN_POINTS,
            "as_of": now.isoformat()}


def _quali_order(snap, target):
    """Qualifying classification for the grid: the live order while qualifying runs, else OpenF1's result."""
    from . import quali
    rows = {r["number"]: r for r in snap.get("rows") or []}
    info = lambda n, r: {"number": n, "code": r.get("code"), "name": r.get("name"), "team": r.get("team"),
                         "colour": r.get("colour")}
    qp = snap.get("quali")
    want = "Sprint Qualifying" if target == "Sprint" else "Qualifying"
    here = snap.get("session") or ""
    if qp and quali.is_quali(here) and (("sprint" in here.lower()) == (target == "Sprint")):
        qrows = qp.get("rows") or {}
        order = sorted((r for r in rows.values() if r.get("pos")), key=lambda r: r["pos"])
        out = []
        for i, r in enumerate(order, 1):
            q = qrows.get(str(r["number"])) or {}
            out.append(dict(info(r["number"], r), quali_pos=i, classified=any(q.get(k) for k in ("q1", "q2", "q3")),
                            best=q.get("q3") or q.get("q2") or q.get("q1")))
        done = qp.get("phase") == 3 and qp.get("finished")
        return out, ("qualifying result (unofficial)" if done else f"live {qp.get('label')} order"), not done
    mk = snap.get("meeting_key")
    if not mk:
        return [], None, True
    names = [want] + (["Sprint Shootout"] if target == "Sprint" else [])
    sess = next((s for name in names for s in api.get("sessions", ttl=3600, meeting_key=mk, session_name=name)), None)
    if not sess:
        return [], None, True
    res = sorted((r for r in api.get("session_result", ttl=600, session_key=sess["session_key"])),
                 key=lambda r: r.get("position") or 99)
    out = []
    for i, r in enumerate(res, 1):
        n = r["driver_number"]
        times = [t for t in (r.get("duration") or []) if t] if isinstance(r.get("duration"), list) else \
            ([r["duration"]] if r.get("duration") else [])
        out.append(dict(info(n, rows.get(n) or {}), quali_pos=i, best=times[-1] if times else None,
                        classified=bool(times) and not r.get("dsq")))
    return out, f"{sess['session_name']} classification", False


def grid_query(year, meeting, snap, cut, target=None):
    """Provisional starting grid: qualifying order + this weekend's grid penalties (f1hub/stewards.py)."""
    from . import fia, stewards
    here = snap.get("session") or ""
    if target not in ("Race", "Sprint"):
        target = "Sprint" if here in ("Sprint", "Sprint Qualifying", "Sprint Shootout") else "Race"
    order, source, live = _quali_order(snap, target)
    ix = _index(year, meeting)
    recs = ix.all(cut)
    prev = ix.previous_event(year, meeting)
    earlier = [r for r in recs if prev and (r["year"], r["event"]) == prev]
    event = fia.event_name(year, meeting)
    pens = stewards.penalties_for(recs, event, year, target, earlier)
    known = {d["number"] for d in order}
    grid = stewards.provisional_grid(order, pens) if order else []
    return {"target": target, "source": source, "live": live, "grid": grid, "status": dict(ix.status),
            "previous_event": prev[1] if prev else None,
            "unplaced": [dict(p, number=n) for n, p in pens.items() if n not in known]}


class BadRequest(ValueError):
    """A request with a missing or malformed parameter -> HTTP 400."""


def _flag(v):
    if v in ("1", "true", "on", "yes"):
        return "1"
    if v in ("0", "false", "off", "no"):
        return "0"
    raise BadRequest(f"expected 1/0, got {v!r}")


def _number(v, lo, hi, name):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise BadRequest(f"{name} must be a number")
    if not lo <= x <= hi:
        raise BadRequest(f"{name} must be between {lo} and {hi}")
    return x


def _when(v):
    """Replay position: seconds from the session start, or an ISO time."""
    try:
        float(v)
        return str(_number(v, -3600, 6 * 3600, "at"))
    except BadRequest:
        raise
    except ValueError:
        pass
    try:
        t = parse_time(v)
    except ValueError:
        raise BadRequest("at must be seconds or an ISO time") from None
    if t.tzinfo is None:
        raise BadRequest("at needs a timezone")
    return t.isoformat()


def _session_key(v):
    if v.isdigit() or v == "latest":
        return v
    raise BadRequest("session must be a session key")


def _mode(v):
    if v in ("live", "replay"):
        return v
    raise BadRequest("mode must be live or replay")


CONTROL_FIELDS = {     # every setting /api/control accepts, and how to check it
    "delay": lambda v: str(int(_number(v, 0, 600, "delay"))),
    "spoiler": _flag,
    "play": _flag,
    "speed": lambda v: str(_number(v, 0.25, MAX_SPEED, "speed")),
    "mode": _mode,
    "session": _session_key,
    "at": _when,
}


def clean_control(q):
    unknown = set(q) - set(CONTROL_FIELDS)
    if unknown:
        raise BadRequest(f"unknown setting(s): {', '.join(sorted(unknown))}")
    return {k: CONTROL_FIELDS[k](v) for k, v in q.items()}


def clean_lap_query(q, default_session):
    num = q.get("driver", "")
    if not num.isdigit() or not 1 <= int(num) <= 99:
        raise BadRequest("driver must be a car number")
    lap = q.get("lap", "last")
    if lap not in ("best", "last") and not (lap.isdigit() and 1 <= int(lap) <= 200):
        raise BadRequest("lap must be best, last or a lap number")
    key = q.get("session") or default_session
    if not str(key).isdigit():
        raise BadRequest("session must be a session key")
    before = q.get("before")
    if before:
        before = _when(before)
        if not before[0].isdigit() or "T" not in before:
            raise BadRequest("before must be an ISO time")
    return int(key), int(num), lap, before


def clean_year(v, first=2023):
    """A season OpenF1 has (2023 on) - or from 2018 where FastF1 can fill in (first=ff1.FIRST_YEAR)."""
    if v in (None, ""):
        return None
    return int(_number(v, first, datetime.now(timezone.utc).year, "year"))


def clean_key(v):
    if not str(v or "").isdigit():
        raise BadRequest("session must be a session key")
    return int(v)


def clean_before(v):
    if not v:
        return None
    v = _when(v)
    if not v[0].isdigit() or "T" not in v:
        raise BadRequest("before must be an ISO time")
    return v


def _ff1_year(year):
    if year and year < 2023 and not ff1.available():
        raise BadRequest(f"{year} needs FastF1 (OpenF1 starts in 2023): .venv/bin/pip install fastf1")
    return year


class Feed:
    def __init__(self, season, session_key, interval):
        self.season = season
        self.session_key = session_key
        self.interval = max(2, interval)   # back-off after errors; normal pacing is per endpoint
        self.live_tracker = None
        self.live = {"error": "Connecting to OpenF1..."}
        self.history = deque()              # (wall_time, data_time, snapshot) for the delay setting
        self.lock = threading.Lock()        # guards live/history/settings; trackers live on the feed thread
        self.wake = threading.Event()
        self.pending = []                   # (ticket, action) queued for the feed thread
        self.requested = self.applied = 0   # tickets: a /api/control reply waits until its change is on screen
        self.applied_cv = threading.Condition(self.lock)
        self.seek = 0                       # bumps on every jump of the clock (seek, new session, delay): pages
        self.boot = int(time.time())
        self.rollover_at = time.time() + 60        # use it to drop out-of-date frames instead of jumping back to them
        self.settings = {"mode": "live", "delay": 0, "spoiler": True}
        self.replay = None                  # dict(store, tracker, playing, speed, last)
        self.focus = None
        self.view = None                    # dashboard tabs on screen (union over browser tabs) -> fetch priority
        self.views = {}                     # client id -> (tab, last seen)
        self.stream = None                  # real-time MQTT (paid accounts)
        self.control_error = None
        self.feed_error = None              # the live feed's last problem (logged once, until it's fixed)
        self.loading = None                 # "Loading replay..." while a replay session is fetched
        self.push_at = 0.0                  # when an unhandled streamed timing message arrived (0 = none)
        self.frame_no = 0                   # bumps whenever self.live changes; /api/events waits on it
        self.frame_cv = threading.Condition()
        self.event_streams = 0
        self.feed_streams = 0
        self.build_ms = None                # how long the last frame took to build
        self._hist_at = 0.0
        from .transcribe import Transcriber
        self.stt = Transcriber()            # team radio speech-to-text (needs the .venv; optional)
        from .sting import IntroFinder
        self.intro = IntroFinder()          # where F1's radio intro sting ends in each clip (needs ffmpeg)
        if api.authenticated():
            from .stream import Stream
            self.stream = Stream()
            self.stream.on_message = self._on_stream

    def _on_stream(self, topic):
        """Network thread: a streamed timing message - have the feed thread rebuild the frame now."""
        if topic in URGENT_TOPICS and not self.push_at:
            self.push_at = time.time()
            self.wake.set()

    def _new_frame(self):
        with self.frame_cv:
            self.frame_no += 1
            self.frame_cv.notify_all()

    def wait_frame(self, seen, timeout):
        """HTTP threads: block until there's a frame newer than `seen` (or timeout); returns the latest number."""
        with self.frame_cv:
            if self.frame_no == seen:
                self.frame_cv.wait(timeout)
            return self.frame_no

    # ----------------------------------------------------------------- control (from HTTP threads)
    def control(self, **kw):
        with self.lock:
            if kw.get("mode") == "replay" or ("at" in kw and self.replay):
                from .recordings import is_key as recording_key
                via = "FastF1 (the first time can take a minute or two)" if ff1.is_key(kw.get("session")) else \
                    "the recording" if recording_key(kw.get("session")) else "OpenF1"
                self.loading = {"text": f"Loading replay - fetching the session from {via}", "since": time.time()}
            self.requested += 1
            ticket = self.requested
            self.pending.append((ticket, dict(kw, _wall=time.time())))   # when you clicked, not when it's applied
        self.wake.set()
        return ticket

    def wait_applied(self, ticket, timeout=60):     # loading a new session can take a while
        end = time.time() + timeout
        with self.applied_cv:
            while self.applied < ticket and time.time() < end:
                self.applied_cv.wait(end - time.time())

    def _apply(self, kw):
        from .replay import ReplayTracker, SessionStore
        if {"delay", "at", "mode", "session"} & set(kw):
            with self.lock:
                self.seek += 1
        if "delay" in kw:
            self.settings["delay"] = max(0, min(600, int(float(kw["delay"]))))
        if "spoiler" in kw:
            self.settings["spoiler"] = kw["spoiler"] in ("1", "true", True)
        if "focus" in kw:
            self.focus = kw["focus"]
            tracker = self.replay["tracker"] if self.settings["mode"] == "replay" and self.replay else self.live_tracker
            if tracker:
                tracker.set_focus(self.focus)
        if "view" in kw:
            now = time.time()
            self.views[kw.get("cid") or "?"] = (kw["view"], now)
            self.views = {c: v for c, v in self.views.items() if now - v[1] < 90}
            shown = {v[0] for v in self.views.values() if v[0] != "hidden"}
            self.view = shown or ({"hidden"} if self.views else None)
            for tr in (self.live_tracker, self.replay and self.replay["tracker"]):
                if tr:
                    tr.set_view(self.view)
        if kw.get("mode") == "live":
            self.settings["mode"] = "live"
            self.replay = None
        if kw.get("mode") == "replay" or ("at" in kw and self.replay):
            key = kw.get("session") or (self.replay["store"].key if self.replay else None) or "latest"
            same = bool(self.replay) and str(self.replay["store"].key) == str(key)
            if not same:
                self.replay = {"store": self._open_store(key, SessionStore), "tracker": None, "playing": False,
                               "speed": 1, "last": time.time()}
            store = self.replay["store"]
            start = datetime.fromisoformat(store.session["date_start"])
            at = kw.get("at")
            if at is None:
                t = self.replay["tracker"].now() if self.replay["tracker"] else start - timedelta(minutes=2)
            elif str(at).lstrip("-").replace(".", "").isdigit():
                t = start + timedelta(seconds=float(at))           # offset from session start
            else:
                t = datetime.fromisoformat(at)
            t = min(t, datetime.now(timezone.utc) - timedelta(seconds=5))
            self.replay["tracker"] = ReplayTracker(store, t)
            if self.focus:
                self.replay["tracker"].set_focus(self.focus)
            self.replay["tracker"].set_view(self.view)
            self.replay["tracker"].refresh()
            # a seek in the same session counts from the click (the page's clock already moved on from there);
            # a newly loaded session starts exactly where asked, however long loading took
            self.replay["last"] = kw.get("_wall", time.time()) if same else time.time()
            self.settings["mode"] = "replay"
        if self.replay:
            if ("play" in kw or "speed" in kw) and "at" not in kw:
                # pause / change speed exactly where you were when you clicked - the feed thread may get to it
                # a second or two later (busy fetching), and that time must not run at the old speed
                self._replay_catch_up(until=kw.get("_wall"))
            if "play" in kw:
                self.replay["playing"] = kw["play"] in ("1", "true", True)
            if "speed" in kw:
                self.replay["speed"] = max(0.25, min(MAX_SPEED, float(kw["speed"])))

    @staticmethod
    def _open_store(key, SessionStore):
        """A session to replay: OpenF1's, or FastF1's for older seasons and recordings - and FastF1's copy of an
        OpenF1 session while OpenF1 is locked (a live session elsewhere)."""
        from . import recordings
        if ff1.is_key(key):
            return ff1.store(key)
        if recordings.is_key(key):
            return recordings.store(key)
        sess = api.get("sessions", ttl=60, session_key=key)
        if not sess:
            raise api.APIError(f"No session {key}")
        store = SessionStore(sess[0])
        try:
            store.get("race_control", time.time())
        except api.LiveSessionLock:
            if not ff1.available():
                raise
            log.info("OpenF1 is locked - replaying session %s through FastF1", key)
            return ff1.store(ff1.key_for_openf1(sess[0]))
        return store

    # ----------------------------------------------------------------- feed thread
    def run(self):
        from .live import LiveTracker
        next_live = last_build = 0.0
        while True:
            try:
                with self.lock:
                    todo, self.pending = self.pending, []
                try:
                    for _, kw in todo:
                        try:
                            self._apply(kw)
                            self.control_error = None
                        except Exception as e:
                            log.exception("Applying %s failed", {k: v for k, v in kw.items() if k != "_wall"})
                            self.control_error = f"{type(e).__name__}: {e}"
                    if todo and self.settings["mode"] == "replay" and self.replay:
                        self._replay_tick()       # publish the replay frame before clearing "loading"
                finally:
                    with self.applied_cv:
                        if todo:
                            if not self.pending:      # another seek queued meanwhile keeps its "loading"
                                self.loading = None
                            self.applied = max(self.applied, todo[-1][0])
                            self.applied_cv.notify_all()
                # switching tab: fetch what's now on screen straight away (but at most every 3 s)
                if any("view" in kw for _, kw in todo):
                    next_live = 0.0
                if self.settings["mode"] == "replay" and self.replay:
                    self._replay_tick()
                    wait = REPLAY_TICK
                else:
                    self._check_rollover()
                    if self.live_tracker is None:
                        self.live_tracker = LiveTracker(self.session_key)
                        if self.focus:
                            self.live_tracker.set_focus(self.focus)
                        self.live_tracker.set_view(self.view)
                        self.live_tracker.stream = self.stream
                    now = time.time()
                    push_ready = max(self.push_at + PUSH_DEBOUNCE, last_build + PUSH_MIN_GAP) if self.push_at else None
                    pushed = push_ready is not None and now >= push_ready and self.live_tracker.streaming()
                    if now >= next_live or todo or pushed:
                        tick = 1.0 if self.live_tracker.streaming() else 2.0   # REST calls are paced inside refresh()
                        over = self._session_over()
                        if over:
                            tick = SESSION_OVER_TICK    # nothing new comes after a session: save the API budget
                        self._record_switch(over)
                        t0 = time.time()
                        if now >= next_live or pushed:
                            self.push_at = 0.0        # cleared first: messages during refresh() queue the next one
                            self.live_tracker.refresh()
                            next_live = time.time() + tick
                        snap = self.live_tracker.snapshot(self.season)
                        last_build = time.time()
                        self.build_ms = round((last_build - t0) * 1000)
                        snap.update(refresh_seconds=tick, mode="live",
                                    feed={"stream": self.stream.status() if self.stream else None,
                                          "view": sorted(self.view) if self.view else None,
                                          "build_ms": self.build_ms, "push": self.event_streams > 0})
                        packed = zlib.compress(json.dumps(snap).encode(), 3)   # ~6x smaller; 1 frame/s adds up
                        with self.lock:
                            frame = (last_build, snap.get("data_time") or last_build, packed)
                            if self.history and last_build - self._hist_at < HISTORY_STEP:
                                self.history[-1] = frame       # several frames a second: keep the newest
                            else:
                                self.history.append(frame)
                                self._hist_at = last_build
                            while self.history and time.time() - self.history[0][0] > HISTORY_SECONDS:
                                self.history.popleft()
                            self.live = snap
                        self._new_frame()
                    wake_at = next_live
                    if self.push_at and self.live_tracker.streaming():
                        wake_at = min(wake_at, max(self.push_at + PUSH_DEBOUNCE, last_build + PUSH_MIN_GAP))
                    wait = max(0.02, wake_at - time.time())
                    if self.session_key == "latest":
                        wait = min(wait, 60)     # (and look for the next session every minute: _check_rollover)
            except api.LiveSessionLock as e:
                self._feed_problem(str(e))
                with self.lock:
                    self.live = {"error": str(e)}
                wait = self.interval
            except api.APIError as e:  # network blips, offline and not cached: say so once, not every retry
                self._feed_problem(str(e))
                with self.lock:
                    self.live = dict(self.live, error=f"{type(e).__name__}: {e}")
                wait = self.interval
            except Exception as e:     # keep the feed alive whatever happens
                if str(e) != self.feed_error:
                    log.exception("Live timing refresh failed")
                self.feed_error = str(e)
                with self.lock:
                    self.live = dict(self.live, error=f"{type(e).__name__}: {e}")
                wait = self.interval
            else:
                if self.feed_error:
                    log.info("Live timing is working again")
                self.feed_error = None
            self.wake.wait(wait)
            self.wake.clear()

    def _feed_problem(self, text):
        if text != self.feed_error:
            log.warning("Live timing: %s", text.splitlines()[0] if text else text)
        self.feed_error = text

    def _record_switch(self, over):
        """Recording stops once the session is over (nothing more comes) - and resumes if it wasn't, after all."""
        tr = self.live_tracker
        rec = getattr(tr, "recording", None)
        if rec is not None and rec.on == over:
            rec.on = not over
            log.info("%s %s: recording %s", tr.session.get("location"), tr.session.get("session_name"),
                     "stopped (the session is over)" if over else "resumed")

    def _session_over(self, margin=timedelta(minutes=30)):
        """Past the scheduled end *and* gone quiet: red flags can run a session long past its schedule."""
        tr = self.live_tracker
        end = tr and tr.session.get("date_end")
        if not end or datetime.now(timezone.utc) < datetime.fromisoformat(end) + margin:
            return False
        last = getattr(tr, "last_event", None)
        return not last or time.time() - last > 600

    def _check_rollover(self):
        """Following "latest": when the next session starts (FP1 on Friday...), switch to it by itself."""
        if self.session_key != "latest" or self.live_tracker is None or time.time() < self.rollover_at:
            return
        self.rollover_at = time.time() + (60 if self._session_over() else 300)
        try:
            latest = api.get("sessions", ttl=30, session_key="latest")
        except Exception:
            return
        if latest and latest[0]["session_key"] != self.live_tracker.key:
            log.info("New session: %s %s - switching to it", latest[0].get("location"), latest[0].get("session_name"))
            self.live_tracker = None
            with self.lock:
                self.history.clear()        # the delay buffer belongs to the old session
                self.seek += 1

    def _replay_limit(self, r):
        end = r["store"].actual_end() + timedelta(minutes=20)
        return min(end, datetime.now(timezone.utc) - timedelta(seconds=5))

    def _replay_catch_up(self, until=None):
        """Advance the virtual clock by the wall time since the last tick (at the current speed)."""
        r = self.replay
        tr = r and r["tracker"]
        if not tr:
            return
        now = max(r["last"], until or time.time())
        if r["playing"]:
            left = (self._replay_limit(r) - tr.now()).total_seconds()
            if left <= 0:
                r["playing"] = False      # reached the end of the recording (not just "no time passed")
            else:
                tr.advance(min((now - r["last"]) * r["speed"], left))
        r["last"] = now

    def _replay_tick(self):
        r = self.replay
        tr = r["tracker"]
        self._replay_catch_up()
        wall = r["last"]
        tr.refresh()
        snap = tr.snapshot(self.season)
        s = r["store"].session
        snap.update(refresh_seconds=REPLAY_TICK, mode="replay", replay={
            "t": tr.now().isoformat(), "wall": wall, "limit": self._replay_limit(r).isoformat(),
            "playing": r["playing"], "speed": r["speed"],
            "start": s["date_start"], "end": r["store"].actual_end().isoformat(),
            "timeline": tr.timeline(spoiler=self.settings["spoiler"]),
            "name": f"{tr.meeting.get('meeting_name', '')} - {s['session_name']} {s.get('year', '')}"})
        with self.lock:
            self.live = snap
        self._new_frame()

    # ----------------------------------------------------------------- reads (HTTP threads)
    def current(self):
        with self.lock:
            st = dict(self.settings)
            extra = {"settings": st, "control_error": self.control_error, "seek": self.seek, "boot": self.boot,
                     "loading": dict(self.loading, elapsed=round(time.time() - self.loading["since"])) if self.loading else None}
            live_lead = self.live.get("lead_lap_start") if isinstance(self.live, dict) else None
            if st["mode"] == "live" and st["delay"] and self.history:
                # show the newest frame whose events all happened at least `delay` seconds ago (event time,
                # not arrival time), so the picture sits a steady `delay` behind the track, like a TV feed
                target = time.time() - st["delay"]
                pick = None
                for frame in self.history:          # newest event time that's old enough (ties: the later build)
                    if frame[1] <= target and (pick is None or frame[1] >= pick[1]):
                        pick = frame
                if pick is None:      # not enough history yet: hold the oldest frame (never show newer than asked)
                    pick = self.history[0]
                    extra["delay_status"] = {"buffering": True, "ready_in": round(pick[1] - target, 1),
                                             "buffered": round(time.time() - pick[1])}
                wall, data_t, packed = pick
                snap = json.loads(zlib.decompress(packed))
                snap["delayed_by"] = round(time.time() - data_t, 1)
                q = snap.get("quali")
                if q and q.get("running") and q.get("remaining") is not None:
                    # the segment clock in the frame was read when it was built; wind it to the moment shown
                    q["remaining"] = max(0, round(q["remaining"] + (wall - max(target, data_t))))
            else:
                snap = self.live
            if live_lead:
                extra["live_lead_lap_start"] = live_lead     # quick sync measures against live data, not the delayed frame
        snap = dict(snap, **extra)
        r = snap.get("replay")
        if r and r.get("playing") and r.get("wall"):
            # the frame was built up to a second ago: hand out the clock as of *now*, so a page that receives the
            # same frame twice doesn't step backwards
            t = datetime.fromisoformat(r["t"]) + timedelta(seconds=(time.time() - r["wall"]) * r["speed"])
            snap["replay"] = dict(r, t=min(t, datetime.fromisoformat(r["limit"])).isoformat())
        self._add_transcripts(snap)
        return snap

    def _add_transcripts(self, snap):
        """Attach radio transcripts at read time, so delayed frames get texts that finished later too.
        Only clips in the frame being shown are queued - nothing from "the future"."""
        radio = snap.get("radio")
        if not radio:
            snap["stt"] = self.stt.status()
            return
        urls = [r.get("url") for r in reversed(radio)]
        self.stt.request(urls)
        self.intro.request(urls)
        snap["radio"] = [dict(r, stt=self.stt.get(r.get("url")), skip=self.intro.get(r.get("url"))) for r in radio]
        snap["stt"] = self.stt.status()

    def hidden_meeting(self):
        """Meeting whose results must stay hidden (spoiler mode while delayed or replaying)."""
        st = self.settings
        if not st["spoiler"]:
            return None
        if st["mode"] == "replay" and self.replay:
            return self.replay["store"].session["meeting_key"]
        if self.live_tracker and st["delay"]:
            return self.live_tracker.session["meeting_key"]
        return None

    def sessions(self, year=None):
        """Sessions you can replay. With a year: every session OpenF1 has that season (practice, quali, sprint, race)."""
        now = datetime.now(timezone.utc)
        if not year and self.season is None:     # the season is still loading: OpenF1's list for this year
            year = now.year
        if year and int(year) < 2023:     # before OpenF1: F1's archive through FastF1
            out = []
            for x in ff1.schedule(int(year))["sessions"]:
                if datetime.fromisoformat(x["date_start"]) < now:
                    out.append({"key": x["session_key"], "type": x["session_type"], "name": x["session_name"],
                                "meeting": f"R{x['round']} {x['meeting_name']}", "meeting_key": x["meeting_key"],
                                "start": x["date_start"], "source": "fastf1",
                                "label": f"{x['location']} - {x['session_name']}"})
            return out
        if year:
            year = int(year)
            rows = api.get("sessions", ttl=api.FOREVER if year < now.year else 3600, year=year)
            out = []
            for x in sorted(rows, key=lambda x: x["date_start"]):
                if datetime.fromisoformat(x["date_start"]) >= now or x.get("is_cancelled"):
                    continue
                out.append({"key": x["session_key"], "type": x.get("session_type"), "name": x.get("session_name"),
                            "meeting": x.get("country_name") if x.get("location") is None else
                            f"{x.get('country_name')} - {x.get('location')}",
                            "meeting_key": x.get("meeting_key"), "start": x["date_start"],
                            "label": f"{x.get('location') or x.get('country_name')} - {x.get('session_name')}"})
            return out
        out = []
        for r in self.season.rounds:
            for name in ("Sprint", "Race"):
                s = r.sessions.get(name)
                if s and datetime.fromisoformat(s["date_start"]) < now and not s.get("is_cancelled"):
                    out.append({"key": s["session_key"], "label": f"R{r.number} {r.name} - {name}",
                                "start": s["date_start"]})
        return out

    def season_data(self):
        s = self.season
        hide = self.hidden_meeting()
        hide_round = next((r.number for r in s.rounds if r.meeting_key == hide), None)
        upto = hide_round - 1 if hide_round else None
        drivers, teams, by_number = s.standings(upto=upto) if upto is not None else s.standings()
        if hide_round is None:
            drivers, teams = s.apply_official_points(drivers, teams, by_number)
        done = {r.number for r in s.completed_rounds() if not hide_round or r.number < hide_round}
        flags = [r.has_sprint for r in s.rounds if r.number not in done and not r.cancelled]
        dchamp, dcont = title_status(drivers, flags, rules=s.rules) if drivers else (None, [])
        tchamp, tcont = title_status(teams, flags, constructor=True, rules=s.rules) if teams else (None, [])
        cal = []
        for r in s.rounds:
            win = next((x for x in s.result(r.race) if x.get("position") == 1), None) if r.number in done else None
            cal.append({"round": r.number, "name": r.name, "country": r.country, "date": r.race["date_start"][:10],
                        "sprint": r.has_sprint, "done": r.number in done,
                        "hidden": bool(hide_round and r.number >= hide_round),
                        "winner": s.driver(win["driver_number"], r.race).name if win else None})
        # points progression (cumulative, per round) for the top 6
        prog = []
        for e in drivers[:6]:
            pts = []
            for rnd in s.completed_rounds():
                if rnd.number not in done:
                    break
                d, _, by = s.standings(upto=rnd.number)
                num = next((n for n, x in by.items() if x.code == e.code), None)
                pts.append([rnd.number, by[num].points if num else 0])
            prog.append({"code": e.code, "colour": "#" + s.team_colour(e.team), "points": pts})
        return {
            "year": s.year, "completed": len(done), "rounds": len(s.rounds), "spoiler_hidden": bool(hide_round),
            "points_left": max_remaining(flags, rules=s.rules), "rounds_left": len(flags), "remaining": flags,
            "cancelled": [r.name for r in s.cancelled],
            "drivers": [{"code": e.code, "name": e.name, "team": e.team, "colour": "#" + s.team_colour(e.team),
                         "points": e.points, "wins": e.wins, "podiums": e.podiums, "poles": e.poles,
                         "dnfs": e.dnfs, "sprint_wins": e.sprint_wins,
                         "finishes": e.finishes, "alive": e in dcont} for e in drivers],
            "teams": [{"name": e.name, "colour": "#" + s.team_colour(e.name), "points": e.points,
                       "wins": e.wins, "alive": e in tcont} for e in teams],
            "driver_champion": dchamp.name if dchamp else None,
            "team_champion": tchamp.name if tchamp else None,
            "calendar": cal, "progression": prog,
            "remaining_rounds": [{"round": r.number, "name": r.name, "sprint": r.has_sprint}
                                 for r in s.rounds if r.number not in done and not r.cancelled],
        }


# ------------------------------------------------------------------------------------------------ the season
class SeasonNotReady(Exception):
    """The season is still loading (or couldn't be loaded yet) -> HTTP 503 with what's going on."""


class SeasonLoader:
    """Loads the season in the background, and reloads it every RELOAD seconds (results land after each session;
    the cache makes it cheap). The dashboard used to load every result before it started - and not start at all
    when OpenF1 was locked (a live session, free account) or offline with one result not cached. Now it's up at
    once, and whatever needs the season says it's loading."""
    RELOAD = 30 * 60
    RETRY = 60              # after a failure, or with results still missing
    RETRY_LOCKED = 5 * 60   # OpenF1 locked: it stays that way until the session ends

    def __init__(self, feed, year, on_change=None):
        self.feed, self.year, self.on_change = feed, year, on_change
        self.state = "loading"         # loading -> ready; failed (nothing loaded) / stale (an older copy in use)
        self.error = None
        self.progress = None
        self.loaded_at = None
        self.wake = threading.Event()

    def start(self):
        threading.Thread(target=self.run, daemon=True, name="season").start()
        return self

    def run(self):
        while True:
            self.wake.wait(self.load_once())
            self.wake.clear()

    def load_once(self):
        """Load (or reload) the season. Returns how long to wait before the next go."""
        from .season import Season
        try:
            fresh = Season(self.year).load(progress=self._progress)
        except api.LiveSessionLock as e:
            self._failed(e, "OpenF1 is locked while a session is live - the season loads when it ends")
            return self.RETRY_LOCKED
        except api.APIError as e:
            self._failed(e, f"Couldn't load the {self.year} season")
            return self.RETRY
        except Exception as e:
            log.exception("Loading the %s season failed", self.year)
            self._failed(e)
            return self.RETRY
        finally:
            self.progress = None
        self.feed.season = fresh
        self.state, self.error, self.loaded_at = "ready", None, time.time()
        if self.on_change:
            self.on_change()
        return self.RETRY if fresh.missing else self.RELOAD

    def _progress(self, msg):
        self.progress = msg

    def _failed(self, e, note=None):
        error = str(e).strip().splitlines()[0][:300] if str(e).strip() else type(e).__name__
        if note and error != self.error:          # once, not every retry
            log.warning("%s: %s", note, error)
        self.error = error
        self.state = "stale" if self.feed.season else "failed"

    def status(self):
        s = self.feed.season
        return {"state": self.state, "year": self.year, "error": self.error, "progress": self.progress,
                "loaded_at": self.loaded_at, "missing": list(getattr(s, "missing", []) or [])}

    def season(self):
        """The loaded season, or SeasonNotReady saying why there isn't one yet."""
        if self.feed.season is not None:
            return self.feed.season
        if self.state == "loading":
            raise SeasonNotReady(f"Loading the {self.year} season" + (f" ({self.progress})" if self.progress else "") + "...")
        raise SeasonNotReady(f"Couldn't load the {self.year} season yet ({self.error}) - trying again shortly.")


# ------------------------------------------------------------------------------------------------ the API
class NotFound(Exception):
    pass


class Reply:
    """A response that isn't JSON: the page, a script, a download."""

    def __init__(self, body, ctype, headers=()):
        self.body, self.ctype, self.headers = body, ctype, list(headers)


def allowed_hosts(port):
    return {f"{h}:{port}" for h in ("localhost", "127.0.0.1", "[::1]")} | {"localhost", "127.0.0.1"}


def trusted(headers, allowed):
    """Only this machine's own dashboard may use the API: blocks other websites (cross-site requests) and
    DNS-rebinding tricks that point a foreign hostname at localhost."""
    if headers.get("Host", "") not in allowed:
        return False
    if headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
        return False
    origin = headers.get("Origin")
    return not origin or origin.split("//", 1)[-1] in allowed


WEB = Path(__file__).with_name("web")
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


def env_stamp():
    """When .openf1.env last changed (None if there's none): settings are read once, at startup."""
    try:
        return (api.PROJECT_DIR / ".openf1.env").stat().st_mtime
    except OSError:
        return None


class App:
    """Everything the dashboard's API answers, by path (ROUTES). The HTTP handler only checks where a request comes
    from and turns results into responses, so each route can be called directly (tests)."""

    def __init__(self, feed, port=8765, year=None, open_feed=False):
        self.feed = feed
        self.port = port
        self.allowed = allowed_hosts(port)
        self.open_feed = open_feed          # --open-feed: web pages from anywhere (an OBS overlay file) may read /api/feed
        self.loader = SeasonLoader(feed, year or datetime.now(timezone.utc).year, on_change=self.season_changed)
        self.season_cache = {"key": None, "data": None, "at": 0}
        self.home_cache = {"data": None, "at": 0}
        self.explore_cache = {}
        self.seasons = {}
        self.started = time.time()
        self.env_at_start = env_stamp()
        self.upstream_errors = {}

    def upstream_problem(self, path, e):
        """A request that failed because a data source did: logged once per path and message, not per request."""
        text = str(e).splitlines()[0] if str(e) else type(e).__name__
        if self.upstream_errors.get(path) != text:
            self.upstream_errors[path] = text
            log.warning("%s: %s", path, text)

    def season_changed(self):
        self.season_cache["at"] = self.home_cache["at"] = 0
        self.explore_cache = {k: v for k, v in self.explore_cache.items() if k[:2] != ("stats", self.loader.year)}

    def route(self, path, q):
        fn = ROUTES.get(path)
        if fn is not None:
            return fn(self, q)
        for prefix, fn in PREFIX_ROUTES:
            if path.startswith(prefix):
                return fn(self, q, path)
        raise NotFound(path)

    def tidy(self):
        """At startup, off the request path: processes an earlier run left behind, and the recordings index."""
        try:
            for note in ff1.tidy_leftovers():
                log.info("FastF1: %s", note)
        except Exception:
            log.exception("Checking for leftover FastF1 processes failed")
        try:
            from . import recordings
            recordings.sessions()          # reading older recordings takes a few seconds the first time
        except Exception:
            log.exception("Reading the recordings failed")

    # ---------------------------------------------------------------- the page
    def page(self, q):
        return Reply(PAGE.read_text(encoding="utf-8"), "text/html; charset=utf-8")

    def static(self, q, path):
        name = path[len("/static/"):]
        ext = Path(name).suffix
        if not re.fullmatch(r"[a-z0-9_-]+\.[a-z]+", name) or ext not in STATIC_TYPES or not (WEB / name).is_file():
            raise NotFound(path)
        return Reply((WEB / name).read_bytes(), STATIC_TYPES[ext])

    # ---------------------------------------------------------------- live timing and replays
    def live(self, q):
        return self.feed.current()

    def focus(self, q):
        num = q.get("driver", "")
        if num and not (num.isdigit() and 1 <= int(num) <= 99):
            raise BadRequest("driver must be a car number")
        want = int(num) if num else None
        self.feed.control(focus=want)
        for _ in range(40):       # give the feed thread up to ~4 s to fetch the telemetry
            time.sleep(0.1)
            f = self.feed.current().get("focus")
            if (f or {}).get("number") == want or (want is None and not f):
                break
        return self.feed.current()

    def control(self, q):
        self.feed.wait_applied(self.feed.control(**clean_control(q)))   # reply with the frame *after* the change
        return self.feed.current()

    def view(self, q):
        tab = q.get("tab", "")
        cid = q.get("cid", "")[:16]
        if not tab.isalpha() or len(tab) > 12:
            raise BadRequest("bad tab")
        self.feed.control(view=tab, cid=cid)
        return {"ok": True}

    def sessions(self, q):
        return self.feed.sessions(_ff1_year(clean_year(q.get("year"), ff1.FIRST_YEAR)))

    def radio(self, q, path):
        """Team radio downloads. F1 hosts the clips on another site, so the browser can't save them directly
        (cross-origin) - the server fetches them and hands them over as a file."""
        from .radio import clip_name, fetch_clip, session_zip
        if path == "/api/radio_file":
            url = q.get("url", "")
            try:
                data = fetch_clip(url)      # refuses anything that isn't an F1 team-radio clip
            except ValueError as e:
                raise BadRequest(str(e))
            name, ctype = clip_name(url, q.get("lap")), "audio/mpeg"
        else:
            data, name = session_zip(self.feed.current(), q.get("driver"))
            ctype = "application/zip"
        safe = "".join(c for c in name if c.isalnum() or c in "._-") or "radio"
        return Reply(data, ctype, [("Content-Disposition", f'attachment; filename="{safe}"')])

    # ---------------------------------------------------------------- explore
    def lab_trace(self, q):
        from .analysis import lab_trace
        key, num, lap, before = clean_lap_query(q, self.feed.current().get("session_key"))
        src = q.get("src", "auto")
        if src not in ("auto", "openf1", "fastf1"):
            raise BadRequest("src must be auto, openf1 or fastf1")
        try:
            tr = lab_trace(key, num, lap, src, before)
        except ff1.FF1Error as e:
            tr = {"error": f"FastF1: {e}"}
        return tr or {"error": "no completed lap yet"}

    def lab_laps(self, q):
        from .analysis import lab_laps
        return lab_laps(clean_key(q.get("session")))

    def analysis(self, q):
        from .analysis import session_analysis
        return session_analysis(clean_key(q.get("session")), clean_before(q.get("before")))

    def circuit(self, q):
        from .analysis import circuit_preview
        mk = q.get("meeting")
        if mk and not mk.isdigit():
            raise BadRequest("meeting must be a meeting key")
        key = ("circuit", mk)
        hit = self.explore_cache.get(key)
        if not hit or time.time() - hit[0] > 1800:
            hit = self.explore_cache[key] = (time.time(), circuit_preview(int(mk) if mk else None, clean_year(q.get("year"))))
        return hit[1] or {"error": "no upcoming meeting found"}

    def circuit_history(self, q):
        from .analysis import circuit_history
        if not ff1.available():
            return {"history": [], "error": "FastF1 isn't installed"}
        year = clean_year(q.get("year")) or datetime.now().year
        return {"history": circuit_history(q.get("name", "")[:80], q.get("location", "")[:80], year)}

    def stats(self, q):
        """Season stats (Explore tab): OpenF1 seasons from their results, 2018-2022 from the Ergast archive via FastF1.
        This season respects spoiler mode: nothing from the meeting you're replaying or watching delayed."""
        from .analysis import _norm_ergast, _norm_openf1, season_stats
        year = _ff1_year(clean_year(q.get("year"), ff1.FIRST_YEAR)) or self.loader.year
        hide = self.feed.hidden_meeting()
        key = ("stats", year, hide)
        hit = self.explore_cache.get(key)
        if hit and time.time() - hit[0] < (1800 if year == self.loader.year else 86400):
            return hit[1]
        if year < 2023:
            data = ff1._disk_cache(f"season-{year}.json", None, lambda: ff1.QUICK.call("season_results", timeout=600, year=year))
            out = season_stats(_norm_ergast(data), year)
        else:
            if year == self.loader.year:
                season = self.loader.season()
            else:
                if year not in self.seasons:
                    from .season import Season
                    self.seasons[year] = Season(year).load()
                season = self.seasons[year]
            upto = None
            if hide:
                r = next((r.number for r in season.rounds if r.meeting_key == hide), None)
                upto = r - 1 if r else None
            official = None
            if season.official and upto is None:      # official totals: they include any penalties
                official = {season.driver(n).code: p for n, p in season.official["drivers"].items()}
            out = season_stats(_norm_openf1(season, upto), year, official)
            out["spoiler_hidden"] = bool(upto is not None)
        self.explore_cache[key] = (time.time(), out)
        return out

    # ---------------------------------------------------------------- home and season
    def home(self, q):
        hc = self.home_cache
        if time.time() - hc["at"] > 60 or not hc["data"]:
            from .home import home
            hc.update(data=home(self.feed.season, year=self.loader.year), at=time.time())
        data = dict(hc["data"], season_status=self.loader.status())
        hide = self.feed.hidden_meeting()
        if hide and data.get("last_race") and data["last_race"].get("meeting_key") == hide:
            data = dict(data, last_race=dict(data["last_race"], results=None, hidden=True), last_session=None)
        return data

    def season(self, q):
        self.loader.season()
        key = self.feed.hidden_meeting()
        sc = self.season_cache
        if sc["key"] != key or time.time() - sc["at"] > 60 or not sc["data"]:
            sc.update(key=key, data=self.feed.season_data(), at=time.time())
        return dict(sc["data"], season_status=self.loader.status())

    def fia(self, q, path):
        return fia_query(path, q, self.feed.current())

    # ---------------------------------------------------------------- FastF1 and recordings
    def ff1_status(self, q):
        return ff1.status()

    def ff1_login(self, q):
        action = q.get("action")
        if action == "start":
            return ff1.LOGIN.start()
        if action == "cancel":
            return ff1.LOGIN.cancel()
        if action == "signout":
            return ff1.sign_out()
        if action == "retry":
            return ff1.retry_password()
        if action == "status":
            return {"sign_in": ff1.LOGIN.status(), "auth": ff1.auth()}
        raise BadRequest("action must be start, cancel, signout, retry or status")

    def ff1_recordings(self, q):
        return {"recordings": ff1.recordings(), "recorder": ff1.RECORDER.status()}

    def ff1_record(self, q):
        action = q.get("action")
        if action == "start":
            tr = self.feed.live_tracker
            sess = tr.session if tr else None
            if sess is None:
                try:
                    sess = (api.get("sessions", ttl=60, session_key="latest") or [None])[0]
                except (api.APIError, api.LiveSessionLock):
                    sess = None
            return ff1.RECORDER.start(sess)
        if action == "stop":
            return ff1.RECORDER.stop()
        raise BadRequest("action must be start or stop")

    def recordings(self, q):
        """Live OpenF1 data recorded per session (f1hub/recordings.py), to replay."""
        from . import recordings
        out = []
        for e in recordings.sessions():
            s = e["session"] or {}
            out.append({"key": e["key"], "session_key": e["session_key"], "name": s.get("session_name"),
                        "location": s.get("location"), "year": s.get("year"), "start": s.get("date_start"),
                        "bytes": e["bytes"], "first": e["first"], "last": e["last"], "layout": e["layout"],
                        "endpoints": e["endpoints"], "replaying": bool(self.feed.replay) and
                        str(self.feed.replay["store"].key) == str(e["key"])})
        return {"recordings": out, "recording": api.RECORD_DIR is not None, "folder": str(Path(api.RECORDINGS) / "openf1")}

    def latency(self, q):
        from . import recordings
        key = clean_key(q.get("session"))
        if not recordings.is_key(key):
            key = recordings.key_for(key)
        return recordings.latency(key)

    # ---------------------------------------------------------------- health
    def health(self, q):
        """A quick "is it up, and is it working" for scripts and `doctor` (the page uses /api/diagnostics)."""
        st = self.feed.current()
        return {"ok": "rows" in st, "error": st.get("error"), "mode": st.get("mode"),
                "stream": (st.get("feed") or {}).get("stream"), "season": self.loader.status(),
                "restart_needed": env_stamp() != self.env_at_start, "uptime": round(time.time() - self.started)}

    def diagnostics(self, q):
        from .doctor import dashboard_checks
        return dashboard_checks(self)


ROUTES = {
    "/": App.page, "/index.html": App.page,
    "/api/live": App.live, "/api/focus": App.focus, "/api/control": App.control, "/api/view": App.view,
    "/api/sessions": App.sessions,
    "/api/lap": App.lab_trace, "/api/lab/trace": App.lab_trace, "/api/lab/laps": App.lab_laps,
    "/api/analysis": App.analysis, "/api/circuit": App.circuit, "/api/circuit/history": App.circuit_history,
    "/api/stats": App.stats, "/api/home": App.home, "/api/season": App.season,
    "/api/ff1/status": App.ff1_status, "/api/ff1/login": App.ff1_login,
    "/api/ff1/recordings": App.ff1_recordings, "/api/ff1/record": App.ff1_record,
    "/api/recordings": App.recordings, "/api/recordings/latency": App.latency,
    "/api/health": App.health, "/api/diagnostics": App.diagnostics,
}
PREFIX_ROUTES = [("/api/fia/", App.fia), ("/static/", App.static), ("/api/radio_", App.radio)]


def make_handler(app):
    feed = app.feed

    class Handler(BaseHTTPRequestHandler):
        def _headers(self, code, ctype, length, extra=()):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")              # no embedding the controls in another page
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in extra:
                self.send_header(k, v)
            self.send_header("Content-Length", str(length))
            self.end_headers()

        def _send(self, body, ctype="application/json", code=200, extra=()):
            data = body.encode() if isinstance(body, str) else body
            try:
                self._headers(code, ctype, len(data), extra)
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass        # the browser went away mid-response (tab closed / reloaded) - nothing to do

        def _json(self, obj, code=200):
            self._send(json.dumps(obj), code=code)

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/api/feed" and app.open_feed and self.headers.get("Host", "") in app.allowed:
                return self._feed(q)          # read-only, so other sites may read it - but only when you said so
            if not trusted(self.headers, app.allowed):
                return self._json({"error": "requests from other sites are not allowed"}, 403)
            if url.path == "/api/events":
                return self._events()
            if url.path == "/api/feed":
                return self._feed(q)
            try:
                out = app.route(url.path, q)
                if isinstance(out, Reply):
                    self._send(out.body, out.ctype, extra=out.headers)
                else:
                    self._json(out)
            except NotFound:
                self._json({"error": "not found"}, 404)
            except BadRequest as e:
                self._json({"error": str(e)}, 400)
            except SeasonNotReady as e:
                self._json({"error": str(e), "loading": True, "season_status": app.loader.status()}, 503)
            except api.LiveSessionLock as e:
                self._json({"error": str(e), "locked": True}, 503)
            except api.APIError as e:       # OpenF1 (or F1, the FIA) unreachable, or offline and not cached
                app.upstream_problem(url.path, e)
                self._json({"error": str(e), "upstream": True}, 502)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                log.exception("%s failed", url.path)
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)

        def log_message(self, *args):
            pass

        def _events(self):
            """Server-Sent Events: each new frame as soon as it's built (several a second while streaming), and
            at least one a second otherwise, since a delayed or replay view moves on even without new data."""
            with feed.frame_cv:
                if feed.event_streams >= MAX_EVENT_STREAMS:
                    return self._json({"error": "too many live connections - polling instead"}, 503)
                feed.event_streams += 1
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.end_headers()
                seen = -1
                self.wfile.write(b"retry: 2000\n\n")
                sent = 0.0
                while True:
                    seen = feed.wait_frame(seen, 1.0)
                    st = feed.settings
                    if st["mode"] != "live" or st["delay"]:
                        # delayed / replay views step about once a second: don't resend the same picture
                        gap = 0.9 - (time.time() - sent)
                        if gap > 0:
                            time.sleep(gap)
                    sent = time.time()
                    data = json.dumps(feed.current(), separators=(",", ":"))
                    self.wfile.write(b"data: " + data.encode() + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass            # page closed or reloaded
            finally:
                with feed.frame_cv:
                    feed.event_streams -= 1

        def _feed(self, q):
            """The data feed for other programs (datafeed.py, docs/feed.md): the channels asked for, each sent when it
            changes, from the same frame the dashboard shows - so a delay or a replay applies to it too."""
            cors = (("Access-Control-Allow-Origin", "*"),) if app.open_feed else ()
            try:
                sub = datafeed.Subscriber(datafeed.parse_channels(q.get("channels")))
            except datafeed.BadChannels as e:
                return self._send(json.dumps({"error": str(e)}), code=400, extra=cors)
            with feed.frame_cv:
                if feed.feed_streams >= MAX_FEED_STREAMS:
                    return self._send(json.dumps({"error": f"at most {MAX_FEED_STREAMS} feed clients at once"}), code=503, extra=cors)
                feed.feed_streams += 1
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                for k, v in cors:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(b"retry: 2000\n\n")
                seen, idle = -1, time.time()
                while True:
                    seen = feed.wait_frame(seen, 1.0)
                    out = sub.events(feed.current())
                    for channel, text in out:
                        self.wfile.write(datafeed.encode(channel, text))
                    if out:
                        idle = time.time()
                    elif time.time() - idle > 15:
                        self.wfile.write(b": keep-alive\n\n")        # a comment line: notices a client that went away
                        idle = time.time()
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with feed.frame_cv:
                    feed.feed_streams -= 1

    return Handler


def serve(year=None, session_key="latest", interval=10, port=8765, open_browser=True, open_feed=False):
    """Run the dashboard. Up at once: the season loads in the background (SeasonLoader)."""
    from . import logs
    logs.setup()
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler, bind_and_activate=False)
    try:                    # the port first: a second copy shouldn't get as far as connecting to OpenF1
        server.server_bind()
        server.server_activate()
    except OSError as e:
        server.server_close()
        if e.errno == errno.EADDRINUSE:
            raise SystemExit(f"Port {port} is already in use - is F1 Hub running already? Open http://localhost:{port}"
                             f" (or start this one with --port {port + 1}).") from None
        raise
    feed = Feed(None, session_key, interval)
    app = App(feed, port, year, open_feed=open_feed)
    server.RequestHandlerClass = make_handler(app)
    app.loader.start()
    threading.Thread(target=feed.run, daemon=True, name="feed").start()
    threading.Thread(target=app.tidy, daemon=True, name="tidy").start()
    url = f"http://localhost:{port}"
    log.info("F1 Hub dashboard running at %s  (Ctrl+C to stop)", url)
    log.info("Log: %s", logs.log_file())
    log.info("Data feed for other programs: %s/api/feed%s", url, "  (open to other sites: --open-feed)" if open_feed else "")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    def stop(*_):
        raise KeyboardInterrupt
    for name in ("SIGTERM", "SIGHUP"):       # a closed terminal or `kill`: shut down the same way as Ctrl+C
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for note in ff1.shutdown():
            log.info(note)
        if feed.stream:
            feed.stream.stop()          # (writes out the last streamed messages to the recording)
        log.info("F1 Hub stopped.")
