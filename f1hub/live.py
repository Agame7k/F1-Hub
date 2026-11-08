"""Live timing for the session happening right now (needs a paid OpenF1 login).

Polls OpenF1 with session_key=latest and only asks for data newer than what it
already has. Requests are tiered to stay inside 60 requests/minute at a 10 s refresh:
  every tick   position, intervals, race_control, laps, location, (focused car_data)  ~6
  every 3rd    stints, pit, weather, overtakes, team_radio                           ~5
  every 6th    championship_drivers                                                  ~1
"""
import os
import re
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from . import api, prerace, quali
from .incidents import IncidentDetector, confirmations_from
from .championship import Entry, max_remaining, sort_standings, title_status
from .display import GOLD, c, dim, fmt_time, header, pad, tyre
from .racecontrol import RaceControl, is_red_flag, neutralisation
from .rules import points_for_distance

def _ts(s):
    """Epoch seconds of an OpenF1 date (with or without its UTC offset), or None."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


FLAG_COLOURS = {"GREEN": "39B54A", "YELLOW": "FFD300", "DOUBLE YELLOW": "FFD300", "RED": "FF0000",
                "SC": "FFB000", "VSC": "FFB000", "CHEQUERED": "FFFFFF"}


def elevation_profile(outline, trace, max_gap=400):
    """Height of each outline point in metres above the lowest point of the lap.

    trace is one lap of location samples, [x, y, z] in OpenF1's units (tenths of a metre), in the same frame as the
    outline. Each outline point takes the height of the nearest sample; samples without a height (z = 0) are skipped.
    None if there's too little to go on, or the outline and the lap don't line up (a different layout)."""
    pts = [p for p in trace if len(p) > 2 and p[2]]
    if len(pts) < 50 or len(outline) < 10:
        return None
    pts = pts[::max(1, len(pts) // 600)]
    heights, far = [], 0
    for p in outline:
        x, y = p[0], p[1]
        d, z = min(((q[0] - x) ** 2 + (q[1] - y) ** 2, q[2]) for q in pts)
        far += d > max_gap ** 2
        heights.append(z)
    if far > len(outline) * 0.2:
        return None
    n, w = len(heights), 3              # location heights are noisy: a small moving average around the loop
    smooth = [sum(heights[(i + k) % n] for k in range(-w, w + 1)) / (2 * w + 1) for i in range(n)]
    low = min(smooth)
    return [round((z - low) / 10, 1) for z in smooth]


class LiveTracker:
    def __init__(self, session_key="latest"):
        sessions = self._fetch("sessions", session_key=session_key)
        if not sessions:
            raise api.APIError("OpenF1 has no current session")
        self.session = sessions[0]
        self.key = self.session["session_key"]
        self.recording = self._recording()
        self.is_race = self.session["session_type"] in ("Race",)
        self.is_sprint = self.session["session_name"] == "Sprint"
        meeting = self._fetch("meetings", meeting_key=self.session["meeting_key"])
        self.meeting = meeting[0] if meeting else {}
        self.drivers = {d["driver_number"]: d for d in self._fetch("drivers", session_key=self.key)}
        self.grid = {}
        if self.is_race:
            self.grid = {g["driver_number"]: g["position"] for g in self._fetch("starting_grid", session_key=self.key)}
            q_name = "Sprint Qualifying" if self.is_sprint else "Qualifying"
            q = None
            if not self.grid or not self.drivers:
                q = self._fetch("sessions", meeting_key=self.session["meeting_key"], session_name=q_name)
            if q and not self.grid:   # not published yet: use the qualifying order
                self.grid = {r["driver_number"]: r["position"]
                             for r in api.get("session_result", ttl=600, session_key=q[0]["session_key"])
                             if r.get("position")}
            if q and not self.drivers:   # before the start OpenF1 may not list the race's drivers yet
                self.drivers = {d["driver_number"]: d for d in self._fetch("drivers", session_key=q[0]["session_key"])}
        self.scheduled_laps = self._scheduled_laps()
        self.circuit = self._circuit_info()
        self.position, self.gap, self.interval = {}, {}, {}
        if self.is_race:
            self.position = dict(self.grid)   # pre-race the tower shows the grid; real positions replace it
        self.last_lap, self.best_lap, self.lap_no = {}, {}, {}
        self.stints, self.pits = {}, {}
        self.messages, self.weather = [], None
        self.track_status = "GREEN"
        self.champ = None
        self._since = {}
        self.refreshes = 0
        self.updated = None
        # richer data for the dashboard
        self.rc = RaceControl(self.session.get("gmt_offset"))
        self.start_ts = datetime.fromisoformat(self.session["date_start"]).timestamp()
        self.laps_by = {}          # number -> {lap: {...}}
        self.stint_hist = {}       # number -> [stints]
        self.pit_list = []
        self.overtake_list = []
        self.radio = []
        self.loc = {}              # number -> (x, y, date)
        self.trail = {}            # number -> deque of [epoch_ms, x, y]: recent samples so the map can animate smoothly
        self.outline = None        # [[x, y], ...] one lap of the circuit
        self.elev_trace = None     # [[x, y, z], ...] the same lap with heights, for the 3D map
        self._elev = (None, None)  # (outline key, heights) - worked out once per outline
        self.gap_hist = {}         # number -> [[epoch, gap_to_leader], ...]
        self.weather_hist = []
        self.retired = {}          # number -> {"lap", "reason"}
        self.focus = None          # driver number whose telemetry is streamed
        self.tel = deque(maxlen=500)
        self.stream = None         # f1hub.stream.Stream when real-time MQTT is available
        self.view = None           # dashboard tab on screen (fetch priority)
        self._last_fetch = {}      # endpoint -> time of last REST fetch
        self._rc_seen = set()
        self._seen = {}            # endpoint -> {row key: time}: rows _new() has returned (see LOOKBACK)
        self._row_at = {}          # (endpoint, driver) -> time of the row applied last, so a late row can't undo it
        self._latency = deque(maxlen=40)   # seconds between a location sample happening and reaching us
        self._newest_loc = 0
        self._data_t = 0.0                 # data_time never runs backwards
        self.incidents = IncidentDetector()   # crash / spin / stopped-car detection from location data

    def _circuit_info(self):
        """Official outline, rotation, corners, marshal sectors and pit loss (MultiViewer, linked by OpenF1)."""
        url = self.meeting.get("circuit_info_url")
        if not url:
            return None
        try:
            d = api.get_external(url)
        except api.APIError:
            return None
        pts = list(zip(d.get("x") or [], d.get("y") or []))
        if len(pts) < 50:
            return None

        def nearest(tp):
            return min(range(len(pts)), key=lambda i: (pts[i][0] - tp["x"]) ** 2 + (pts[i][1] - tp["y"]) ** 2)

        sectors = sorted(({"number": m["number"], "start": nearest(m["trackPosition"])}
                          for m in d.get("marshalSectors") or []), key=lambda m: m["start"])
        loss = d.get("pitLoss") or {}
        num = lambda v: float(v) if v not in (None, "") else None
        return {
            "outline": [list(p) for p in pts], "rotation": d.get("rotation") or 0, "sectors": sectors,
            "corners": [{"number": c["number"], "x": c["trackPosition"]["x"], "y": c["trackPosition"]["y"],
                         "angle": c.get("angle", 0)} for c in d.get("corners") or []],
            "pit_loss": {"normal": num(loss.get("normal")), "sc": num(loss.get("sc")), "vsc": num(loss.get("vsc"))},
        }

    # ----------------------------------------------------------------- data source + clock
    # Everything goes through these two, so a replay can run the same logic on a virtual clock.
    def _fetch(self, endpoint, **params):
        rec = getattr(self, "recording", None)
        return api.get(endpoint, cache=False, record=rec.rest if rec and rec.on else None, **params)

    def _recording(self):
        """Where this session's live data is recorded (recordings/openf1/...), or None with recording off."""
        from .recordings import SessionRecording
        return SessionRecording(self.session) if api.RECORD_DIR is not None else None

    def now(self):
        return datetime.now(timezone.utc)

    def _scheduled_laps(self):
        """Race distance isn't in OpenF1, so use the winner's lap count from last year's race here."""
        if not self.is_race or self.is_sprint:
            return None
        try:
            prev = api.get("sessions", circuit_key=self.session["circuit_key"], session_name="Race",
                           year=self.session["year"] - 1)
            if prev:
                res = api.get("session_result", session_key=prev[0]["session_key"], position=1)
                if res:
                    return res[0].get("number_of_laps")
        except api.APIError:
            pass
        return None

    # ----------------------------------------------------------------- polling
    # A REST fetch asks again from this many seconds before the newest row it has. Rows can turn up late (team radio
    # once its clip is ready), and the stream can miss one (a reconnect): asking only for rows newer than the newest
    # would never find those, because the newest row has already moved past them. Rows already seen are skipped.
    LOOKBACK = {"intervals": 20, "team_radio": 300}
    LOOKBACK_DEFAULT = 90

    def _new(self, endpoint, **params):
        """Rows not seen before, oldest first: pushed by the stream, plus a REST fetch when due. One that came in late
        can be older than rows returned earlier - see _in_order() and _newest()."""
        since = _ts(self._since.get(endpoint))          # (_since keeps the newest row's own date text)
        back = self.LOOKBACK.get(endpoint, self.LOOKBACK_DEFAULT)
        rows = list(self._pushed(endpoint))
        if self._due(endpoint):
            if since is not None:
                params["date>"] = self._iso(datetime.fromtimestamp(since - back, timezone.utc))
            rows += self._fetch(endpoint, session_key=self.key, **params)
        seen, out = self._seen.setdefault(endpoint, {}), []
        for r in rows:
            t = _ts(r.get("date"))
            if t is None:
                continue
            k = (round(t, 3), r.get("driver_number"), r.get("message"), r.get("position"))
            if k not in seen:
                seen[k] = t
                out.append((t, r))
        out.sort(key=lambda x: x[0])
        if out and (since is None or out[-1][0] > since):
            self._since[endpoint], since = out[-1][1]["date"], out[-1][0]
        if len(seen) > 3000:                # forget rows too old to be asked for again
            for k in [k for k, t in seen.items() if t < since - 2 * back]:
                del seen[k]
        return [r for _, r in out]

    def _newest(self, endpoint, row):
        """Whether row is at least as new as the last one applied for its driver (a late row mustn't undo a newer one)."""
        k, t = (endpoint, row.get("driver_number")), _ts(row.get("date")) or 0
        if t < self._row_at.get(k, 0):
            return False
        self._row_at[k] = t
        return True

    @staticmethod
    def _in_order(lst, rows):
        """Add rows (oldest first) to lst, keeping it in time order: one that came in late goes where it belongs."""
        if not rows:
            return
        late = lst and (_ts(rows[0].get("date")) or 0) < (_ts(lst[-1].get("date")) or 0)
        lst.extend(rows)
        if late:
            lst.sort(key=lambda r: _ts(r.get("date")) or 0)

    @staticmethod
    def _iso(dt):
        return dt.astimezone(timezone.utc).isoformat()

    PIT_RETIRE = 90     # s: parked in the pit lane this long (no stop takes it) = provisionally retired
    LAP_STALE = 300     # s: a car whose current lap began longer ago than this has stopped (retired, in the garage)

    def _lap_floor(self):
        """The first lap a laps fetch asks for: the one before the lap the slowest car still running is on. Asking
        only around the leader's lap would never fetch the laps of a car that's been lapped twice. Cars that have
        stopped don't hold it back."""
        now = self.now().timestamp()
        running = [lap for n, lap in self.lap_no.items() if n not in getattr(self, "retired", {})
                   and now - (_ts((self.laps_by.get(n, {}).get(lap) or {}).get("start")) or 0) < self.LAP_STALE]
        return max(1, min(running or [max(self.lap_no.values(), default=1)]) - 1)

    def _store_lap(self, lap):
        n = lap["driver_number"]
        self.lap_no[n] = max(self.lap_no.get(n, 0), lap["lap_number"])
        d = lap.get("lap_duration")
        self.laps_by.setdefault(n, {})[lap["lap_number"]] = {
            "t": d, "s1": lap.get("duration_sector_1"), "s2": lap.get("duration_sector_2"),
            "s3": lap.get("duration_sector_3"), "st": lap.get("st_speed"), "out": lap.get("is_pit_out_lap"),
            "i1": lap.get("i1_speed"), "i2": lap.get("i2_speed"),
            "start": lap.get("date_start")}
        if d:
            if self.lap_no[n] == lap["lap_number"]:
                self.last_lap[n] = d
            if n not in self.best_lap or d < self.best_lap[n]:
                self.best_lap[n] = d

    # Which endpoints each dashboard tab shows. Whatever is on screen is fetched most often;
    # the rest drops to a slower cadence so the 60 requests/minute budget goes where you're looking.
    VIEW_NEEDS = {
        "live": {"location", "stints", "pit"},
        "rc": set(),
        "map": {"location"},
        "strat": {"stints", "pit"},
        "ins": {"stints", "overtakes", "pit"},
        "gaps": set(),
        "drv": {"car_data", "stints"},
        "radio": {"team_radio"},
        "champ": {"championship_drivers"},
        "cal": set(),
    }
    CORE = ("position", "intervals", "race_control", "laps")
    # endpoint -> (seconds between REST fetches when on screen, when off screen)
    CADENCE = {"position": (4, 4), "intervals": (4, 4), "race_control": (5, 5), "laps": (6, 6),
               "location": (5, 30), "car_data": (4, 20), "stints": (10, 30), "pit": (15, 30), "weather": (30, 60),
               "overtakes": (10, 30), "team_radio": (10, 40), "championship_drivers": (20, 60)}
    STREAM_RESYNC = 30     # streamed endpoints still get a REST catch-up this often, in case a message was missed

    def set_view(self, tabs):
        """tabs: what's on screen - one tab name, or a set of them (several browser tabs). "hidden" = nobody looking."""
        if tabs is None:
            self.view = None
            return
        tabs = {tabs} if isinstance(tabs, str) else set(tabs)
        self.view = {t for t in tabs if t in self.VIEW_NEEDS or t == "hidden"} or None

    def streaming(self, endpoint=None):
        st = self.stream
        return bool(st and st.healthy and (endpoint is None or endpoint in st.topics))

    def _due(self, endpoint, tick=None):
        """Time-based, priority-aware schedule for REST fetches."""
        if self.refreshes == 0:
            return True
        if self.streaming(endpoint):
            every = self.STREAM_RESYNC
        else:
            view = self.view
            on = endpoint in self.CORE or (endpoint in ("location", "car_data") if view is None
                                           else any(endpoint in self.VIEW_NEEDS.get(v, ()) for v in view))
            every = self.CADENCE[endpoint][0 if on else 1]
            if view == {"hidden"}:                         # every browser tab in the background: tick over slowly
                every = max(every, 30)
        t = self.now().timestamp()
        if t - self._last_fetch.get(endpoint, 0) >= every - 0.5:
            self._last_fetch[endpoint] = t
            return True
        return False

    def _pushed(self, endpoint):
        """Rows the MQTT stream delivered since last tick (empty if not streaming)."""
        if not self.streaming(endpoint):
            return []
        return self.stream.drain(endpoint, self.key)

    def refresh(self):
        tick = self.refreshes
        # ---------------- fast tier
        for p in self._new("position"):
            if self._newest("position", p):
                self.position[p["driver_number"]] = p["position"]
        if self.is_race:
            for i in self._new("intervals"):
                if not self._newest("intervals", i):
                    continue
                n = i["driver_number"]
                self.gap[n] = i.get("gap_to_leader")
                self.interval[n] = i.get("interval")
                g = i.get("gap_to_leader")
                t = datetime.fromisoformat(i["date"]).timestamp()
                if (isinstance(g, (int, float)) or g is None) and t >= self.start_ts:
                    hist = self.gap_hist.setdefault(n, [])
                    if not hist or t - hist[-1][0] >= 15:
                        hist.append([round(t), g or 0.0])
        self._add_messages(self._new("race_control"))
        for lap in self._pushed("laps"):            # stream sends each lap again as sectors complete
            self._store_lap(lap)
        if tick == 0:
            for lap in self._fetch("laps", session_key=self.key):
                self._store_lap(lap)
        elif self._due("laps"):
            for lap in self._fetch("laps", session_key=self.key, **{"lap_number>=": self._lap_floor()}):
                self._store_lap(lap)
        pushed = self._pushed("location")
        for r in pushed:
            self._add_loc(r)
        if pushed:
            self._since["location"] = max(r["date"] for r in pushed)
        if self._due("location"):
            self._refresh_location()
        tel = self._pushed("car_data")
        if self.focus:
            for r in tel:
                if r.get("driver_number") == self.focus and (not self.tel or r["date"] > self.tel[-1]["date"]
                                                             or self.tel[-1]["n"] != self.focus):
                    self._add_tel(r)
            if self._due("car_data"):
                self._refresh_telemetry()
        # ---------------- priority-driven tier (see VIEW_NEEDS)
        if self._pushed("stints") or self._due("stints"):
            stints = self._fetch("stints", session_key=self.key)
            self.stint_hist = {}
            for st in sorted(stints, key=lambda x: x["stint_number"]):
                self.stint_hist.setdefault(st["driver_number"], []).append(st)
                self.stints[st["driver_number"]] = st
        if self._pushed("pit") or self._due("pit"):
            self.pit_list = sorted(self._fetch("pit", session_key=self.key), key=lambda p: p["date"])
            self.pits = {}
            for p in self.pit_list:
                self.pits[p["driver_number"]] = self.pits.get(p["driver_number"], 0) + 1
        w = self._new("weather")              # these three fetch on their own schedule inside _new
        self._in_order(self.weather_hist, w)
        if w:
            self.weather = self.weather_hist[-1]
        self._in_order(self.overtake_list, self._new("overtakes"))
        self._in_order(self.radio, self._new("team_radio"))
        if self.is_race and self._due("championship_drivers"):
            try:
                self.champ = self._fetch("championship_drivers", session_key=self.key) or None
            except api.APIError:
                self.champ = None
        if self.outline is None and tick % 3 == 1:
            self._build_outline()
        self._detect_retirements()
        self.refreshes += 1
        self.updated = self.now()

    def _detect_retirements(self):
        """Cars that are out: a race-control 'retired/stopped' message, parked in the pit lane for over a
        minute and a half, or no new lap for ~3 lap-times (under a red flag, measured up to the moment the red
        flag came out). The last two are provisional and undone if the car drives on."""
        if not self.is_race:
            return
        for m in self.rc.messages:
            up = m["text"].upper()
            if ("RETIRED" in up or "STOPPED" in up) and m["cars"]:
                for n in m["cars"]:
                    if not (self.retired.get(n) or {}).get("definite"):     # the official word beats our guess
                        self.retired[n] = {"lap": m["lap"], "reason": m["text"], "definite": True}
        # sat in the pit lane / garage long after any pit stop: retired, or a long repair (not a crash)
        det = getattr(self, "incidents", None)
        parked = det.parked_in_pits(self.now().timestamp(), self.PIT_RETIRE) if det else {}
        if self.track_status != "CHEQUERED":         # after the flag the whole field parks in the pit lane
            for n, since in parked.items():
                if not self._incident_suppressed(since):
                    self.retired.setdefault(n, {"lap": self.lap_no.get(n), "reason": "stopped in the pits",
                                                "definite": False, "pits": True})
        for n in [n for n, r in self.retired.items() if r.get("pits") and n not in parked]:
            del self.retired[n]       # drove out again: it was a repair
        if not self.laps_by:
            return
        leader = self.order()[0] if self.order() else None
        lead_laps = sorted((l["t"] for l in self.laps_by.get(leader, {}).values() if l.get("t")))
        if len(lead_laps) < 3:
            return
        typical = lead_laps[len(lead_laps) // 2]
        now = self.now().timestamp()
        if self.track_status == "RED" and self.rc.red:      # everyone stopped: judge against when the red came out
            now = min(now, datetime.fromisoformat(self.rc.red["since"]).timestamp())
        if self.track_status == "CHEQUERED" and getattr(self, "_flag_at", None):
            # after the flag the field stops lapping and parks: only a car already silent at the flag is out
            now = min(now, self._flag_at)
        top = max(self.lap_no.values(), default=0)
        for n, laps in self.laps_by.items():
            starts = [(no, datetime.fromisoformat(l["start"]).timestamp()) for no, l in laps.items() if l.get("start")]
            if not starts:
                continue
            last_no, last_start = max(starts)
            silent = now - last_start
            if silent > max(3 * typical, 240) and top - last_no >= 2:
                self.retired.setdefault(n, {"lap": last_no, "reason": f"no timing since lap {last_no}", "definite": False})
            elif n in self.retired and not self.retired[n]["definite"] and silent < typical * 1.5 and n not in parked:
                del self.retired[n]   # it was only a long pit stop / repair

    def order(self):
        """Running order with retired cars moved to the back."""
        base = sorted(self.position, key=lambda n: self.position[n])
        return [n for n in base if n not in self.retired] + [n for n in base if n in self.retired]

    def _add_loc(self, r):
        n = r["driver_number"]
        self.loc[n] = (r["x"], r["y"], r["date"])
        t = int(datetime.fromisoformat(r["date"]).timestamp() * 1000)
        tr = self.trail.setdefault(n, deque(maxlen=60))
        if not tr or t > tr[-1][0]:
            tr.append([t, r["x"], r["y"]])
            self.incidents.add(n, t / 1000, r["x"], r["y"], suppressed=self._incident_suppressed(t / 1000))

    def _refresh_location(self):
        """Latest x/y of every car. Only ever asks for the last few seconds (it's ~80 rows/s)."""
        since = self._since.get("location")
        floor = self._iso(self.now() - timedelta(seconds=20))
        rows = self._fetch("location", session_key=self.key, **{"date>": max(since, floor) if since else floor})
        if not rows and not self.loc:
            # nothing in the last 20 s (session over / delayed feed): take the newest few seconds we know of
            anchors = [v for k, v in self._since.items() if k in ("position", "intervals", "race_control") and v]
            if anchors:
                end = datetime.fromisoformat(max(anchors))
                rows = self._fetch("location", session_key=self.key,
                            **{"date>": self._iso(end - timedelta(seconds=4)), "date<": self._iso(end)})
        for r in sorted(rows, key=lambda r: r["date"]):
            self._add_loc(r)
        if rows:
            self._since["location"] = max(r["date"] for r in rows)

    def _incident_suppressed(self, t):
        """Times when cars stopping is normal: under a red flag, before a race starts, standing restarts."""
        for start, end in self._red_periods():
            if start <= t and (end is None or t < end + (300 if self.is_race else 0)):
                return True
        if self.is_race:
            race_start = getattr(self, "_race_start", None)     # lights out = the first lap-1 start (cached once known)
            if race_start is None:
                starts = [datetime.fromisoformat(l["start"]).timestamp() for laps in self.laps_by.values()
                          for no, l in laps.items() if no == 1 and l.get("start")]
                if len(starts) >= 3:
                    race_start = self._race_start = min(starts)
            if race_start is None or t < race_start + 5:
                return True
        return False

    def _red_periods(self):
        """(start, end) of every stoppage: red flag / session aborted, until the session starts, resumes or ends
        (in qualifying a red flag can end a segment outright). Cached until a new message arrives."""
        if getattr(self, "_red_cache", (None,))[0] == len(self.messages):
            return self._red_cache[1]
        periods, cur = [], None
        for m in sorted((m for m in self.messages if m.get("date")), key=lambda m: m["date"]):
            up, t = (m.get("message") or "").upper(), datetime.fromisoformat(m["date"]).timestamp()
            if cur is None and (is_red_flag(m) or (m.get("category") == "SessionStatus" and "ABORTED" in up)):
                cur = t
            elif cur is not None and m.get("category") == "SessionStatus" and re.search(r"STARTED|RESUMED|FINISHED|ENDED|FINALISED", up):
                periods.append((cur, t))
                cur = None
        if cur is not None:
            periods.append((cur, None))
        self._red_cache = (len(self.messages), periods)
        return periods

    def _incident_place(self, x, y):
        """Marshal sector and nearest corner for a spot on track (official circuit data)."""
        c = self.circuit or {}
        out = {"corner": None, "sector": None}
        outline = c.get("outline") or self.outline
        if outline:
            i = min(range(len(outline)), key=lambda k: (outline[k][0] - x) ** 2 + (outline[k][1] - y) ** 2)
            secs = [m for m in c.get("sectors") or [] if m["start"] <= i]
            if secs:
                out["sector"] = secs[-1]["number"]
            elif c.get("sectors"):
                out["sector"] = c["sectors"][-1]["number"]       # before the first marker: the last sector wraps round
        corners = c.get("corners") or []
        if corners:
            best = min(corners, key=lambda k: (k["x"] - x) ** 2 + (k["y"] - y) ** 2)
            if (best["x"] - x) ** 2 + (best["y"] - y) ** 2 < 3000 ** 2:     # within ~330 m
                out["corner"] = best["number"]
        return out

    def _impact_speed(self, e):
        """Real speed before the impact, from the car's own telemetry (one small request per incident, cached).
        Location-based speeds are only a rough guide: timestamp jitter inflates them."""
        if "car_speed" not in e:
            e["car_speed"] = None
            try:
                rows = self._fetch("car_data", session_key=self.key, driver_number=e["number"],
                                   **{"date>": self._iso(datetime.fromtimestamp(e["t"] - 6, timezone.utc)),
                                      "date<": self._iso(datetime.fromtimestamp(e["t"] + 1, timezone.utc))})
                speeds = [r["speed"] for r in rows if r.get("speed") is not None]
                if speeds:
                    e["car_speed"] = max(speeds)
            except api.APIError:
                pass
        return e["car_speed"]

    def incident_list(self):
        for e in self.incidents.events:
            if e["kind"] in ("crash", "spin") and "car_speed" not in e:
                if self._impact_speed(e):
                    e["speed_before"] = e["car_speed"]
        info = lambda n: {"number": n, "code": self.code(n), "colour": "#" + self.colour(n),
                          "name": (self.drivers.get(n) or {}).get("full_name"),
                          "team": (self.drivers.get(n) or {}).get("team_name")}
        return self.incidents.snapshot(self.now().timestamp(), info, self._incident_place,
                                       lambda e, where: confirmations_from(self.messages, e["number"], e["t"], where.get("sector")))

    def _elevation(self, outline):
        """Heights for the outline the map is drawn from (cached: the outline rarely changes)."""
        if not outline or not self.elev_trace:
            return None
        key = (len(outline), tuple(outline[0]), len(self.elev_trace))
        if self._elev[0] != key:
            self._elev = (key, elevation_profile(outline, self.elev_trace))
        return self._elev[1]

    def _build_outline(self):
        """Trace the circuit from one clean lap of location data."""
        best = None
        for n, laps in self.laps_by.items():
            for no, l in laps.items():
                if l["t"] and l["start"] and not l["out"] and no > 1 and (best is None or l["t"] < best[1]["t"]):
                    best = (n, l)
        if not best:
            return
        n, l = best
        start = datetime.fromisoformat(l["start"])
        rows = self._fetch("location", session_key=self.key, driver_number=n,
                    **{"date>=": self._iso(start), "date<": self._iso(start + timedelta(seconds=l["t"]))})
        rows = [r for r in sorted(rows, key=lambda r: r["date"]) if r["x"] or r["y"]]
        pts = [[r["x"], r["y"]] for r in rows]
        if len(pts) > 50:
            self.outline = pts
            self.elev_trace = [[r["x"], r["y"], r.get("z") or 0] for r in rows]

    def _refresh_telemetry(self):
        since = self.tel[-1]["date"] if self.tel and self.tel[-1]["n"] == self.focus else \
            self._iso(self.now() - timedelta(seconds=40))
        rows = self._fetch("car_data", session_key=self.key, driver_number=self.focus, **{"date>": since})
        if not rows and not self.tel:   # nothing recent: take the last 30 s before the newest timing data
            anchors = [v for k, v in self._since.items() if k in ("position", "intervals") and v]
            if anchors:
                end = datetime.fromisoformat(max(anchors))
                rows = self._fetch("car_data", session_key=self.key, driver_number=self.focus,
                            **{"date>": self._iso(end - timedelta(seconds=30)), "date<": self._iso(end)})
        for r in sorted(rows, key=lambda r: r["date"]):
            if not self.tel or self.tel[-1]["n"] != self.focus or r["date"] > self.tel[-1]["date"]:
                self._add_tel(r)

    def _add_tel(self, r):
        self.tel.append({"n": self.focus, "date": r["date"], "speed": r.get("speed"), "throttle": r.get("throttle"),
                         "brake": r.get("brake"), "gear": r.get("n_gear"), "rpm": r.get("rpm"), "drs": r.get("drs")})

    def set_focus(self, number):
        if number != self.focus:
            self.focus = number
            self.tel.clear()
            if number:
                self._refresh_telemetry()

    def _add_messages(self, rows):
        late = False
        for m in rows:
            k = (m["date"], m.get("message"))
            if k in self._rc_seen:
                continue
            self._rc_seen.add(k)
            late = late or bool(self.messages) and (_ts(m["date"]) or 0) < (_ts(self.messages[-1]["date"]) or 0)
            self.messages.append(m)
            if not late:
                self.rc.add(m)
                self._track_status(m)
        if late:
            self._reread_messages()

    def _reread_messages(self):
        """A race-control message came in late, older than ones already read: read them all again in time order (the
        flags, neutralisations and track status depend on the order)."""
        self.messages.sort(key=lambda m: _ts(m.get("date")) or 0)
        self.rc = RaceControl(self.session.get("gmt_offset"))
        self.track_status, self._flag_at = "GREEN", None
        for m in self.messages:
            self.rc.add(m)
            self._track_status(m)

    def _chequered(self, m):
        """The flag is out: note when (the first time), so the retirement check can judge the field as it was then."""
        self.track_status = "CHEQUERED"
        if getattr(self, "_flag_at", None) is None and m.get("date"):
            self._flag_at = datetime.fromisoformat(m["date"]).timestamp()

    def _track_status(self, m):
        msg = (m.get("message") or "").upper()
        flag, kind = m.get("flag"), neutralisation(m)
        track_wide = m.get("scope") in (None, "Track")
        if m.get("category") == "SessionStatus":
            if kind == "red":               # SESSION ABORTED: F1's status while a session is suspended
                self.track_status = "RED"
            elif "FINISHED" in msg or "ENDED" in msg or "FINALISED" in msg:
                self._chequered(m)
            elif kind == "started":
                self.track_status = "GREEN"
            return
        if kind == "red":
            self.track_status = "RED"
        elif kind in ("sc", "vsc"):
            self.track_status = kind.upper()
        elif flag == "CHEQUERED":
            self._chequered(m)
        elif self.track_status == "RED":
            # announcements like "RACE WILL RESUME AT 15:45" are not the restart itself;
            # only the session status (handled above) or a track-wide green flag ends a red flag
            if flag == "GREEN" and track_wide:
                self.track_status = "GREEN"
        elif kind in ("sc_end", "vsc_end") or ((flag == "GREEN" or "TRACK CLEAR" in msg) and track_wide):
            self.track_status = "GREEN"

    # ----------------------------------------------------------------- views
    def code(self, n):
        return (self.drivers.get(n) or {}).get("name_acronym") or str(n)

    def colour(self, n):
        return (self.drivers.get(n) or {}).get("team_colour") or "FFFFFF"

    def live_title_picture(self, season, reduced=False):
        """Championship if the session finished in the current order.
        reduced=True: points for a red-flagged race that is not resumed (distance-based table)."""
        if not self.champ or season is None or season.year != self.session.get("year"):
            return None     # (a replay from another season: today's season data doesn't apply)
        this_round = next((r.number for r in season.rounds if r.meeting_key == self.session["meeting_key"]), None)
        if this_round is None:
            return None
        table_pts = None
        if reduced:
            if not self.scheduled_laps:
                return None
            laps_done = max(0, max(self.lap_no.values(), default=1) - 1)
            table_pts = points_for_distance(laps_done, self.scheduled_laps)
        past = season.standings(upto=this_round - 1)[2]      # stats as they stood before this weekend
        entries = {}
        for row in self.champ:
            n = row["driver_number"]
            e = Entry((self.drivers.get(n) or {}).get("full_name") or self.code(n), self.code(n))
            pos = self.position.get(n)
            out = self.retired.get(n)
            lead = max(self.lap_no.values(), default=0)
            if out and (out.get("lap") or 0) < 0.9 * (self.scheduled_laps or lead or 1):
                e.points = row["points_start"]          # retired and not classified: no points
            elif table_pts is not None:
                e.points = row["points_start"] + (table_pts[pos - 1] if pos and pos <= len(table_pts) else 0)
            else:   # OpenF1's points_current moves with the live order
                e.points = row["points_current"] if row.get("points_current") is not None else row["points_start"]
            e.start = row["points_start"]
            e.number = n
            e.past_finishes = list(past[n].finishes) if n in past else []
            e.finishes = list(e.past_finishes)
            if not self.is_sprint and pos:
                e.finishes.append(pos)
            if n in past:
                e.wins, e.podiums, e.poles, e.starts = past[n].wins, past[n].podiums, past[n].poles, past[n].starts
            if not self.is_sprint and pos and not out:
                e.wins += pos == 1
                e.podiums += pos <= 3
                e.starts += 1
            entries[n] = e
        table = sort_standings(entries.values())
        remaining = [r.has_sprint for r in season.rounds         # rounds after this one (right for replays too)
                     if r.number > this_round and not r.cancelled]
        if self.is_sprint:   # this weekend's GP is still to come
            remaining.insert(0, False)
        champ, contenders = title_status(table, remaining, rules=season.rules)
        # was it already settled before this session? (then today isn't the day it was won)
        before = [Entry(e.name, e.code, points=e.start, finishes=e.past_finishes) for e in table]
        rem_before = ([True] + remaining[1:]) if self.is_sprint else [False] + remaining
        champ_before, _ = title_status(before, rem_before, rules=season.rules)
        self.title_decided_before = champ_before.code if champ_before else None
        self._team_pic = self._team_title(season, this_round, entries, remaining)
        return table, champ, contenders, remaining, table_pts

    def _team_title(self, season, this_round, drivers, remaining):
        """The constructors' championship if the session finished like this: the teams' totals before this weekend
        plus what each driver is scoring now, credited to the team they're driving for today."""
        teams = {}
        for t in season.standings(upto=this_round - 1)[1]:
            teams[t.name] = Entry(t.name, t.code, t.name, points=t.points, finishes=list(t.finishes), wins=t.wins)
            teams[t.name].start = t.points
        for n, e in drivers.items():
            name = (self.drivers.get(n) or {}).get("team_name")
            if not name:
                continue
            t = teams.get(name)
            if t is None:
                t = teams[name] = Entry(name, name[:3].upper(), name)
                t.start = 0
            t.points += e.points - e.start
            pos = self.position.get(n)
            if not self.is_sprint and pos and not self.retired.get(n):
                t.finishes.append(pos)
                t.wins += pos == 1
        if not teams:
            return None
        table = sort_standings(teams.values())
        champ, contenders = title_status(table, remaining, constructor=True, rules=season.rules)
        rem_before = ([True] + remaining[1:]) if self.is_sprint else [False] + remaining
        before = [Entry(t.name, t.code, points=t.start) for t in table]
        champ_before, _ = title_status(before, rem_before, constructor=True, rules=season.rules)
        colour = lambda name: next(("#" + self.colour(n) for n, d in self.drivers.items() if d.get("team_name") == name), "#ffffff")
        info = lambda t: {"name": t.name, "colour": colour(t.name), "points": t.points, "wins": t.wins,
                          "drivers": [{"code": self.code(n), "name": d.get("full_name"), "headshot": d.get("headshot_url")}
                                      for n, d in self.drivers.items() if d.get("team_name") == t.name][:2]}
        return {"champion": champ.name if champ else None,
                "champion_info": dict(info(champ), margin=champ.points - table[1].points if len(table) > 1 else None) if champ else None,
                "decided_before": champ_before.name if champ_before else None,
                "year": self.session.get("year"), "rounds_left": len(remaining),
                "contenders": [t.name for t in contenders],
                "table": [{"name": t.name, "points": t.points, "colour": colour(t.name)} for t in table[:6]]}

    def snapshot(self, season=None):
        """Everything the GUI needs, as plain JSON-able data."""
        lead_lap = max(self.lap_no.values(), default=0)
        fastest = min(self.best_lap.values(), default=None)
        sector_info = self._sector_colours()
        live_pts = {}
        for row in self.champ or []:
            if row.get("points_current") is not None:
                live_pts[row["driver_number"]] = row["points_current"] - row["points_start"]
        rows = []
        for n in self.order():
            st = self.stints.get(n) or {}
            age = None
            if st:
                age = (st.get("tyre_age_at_start") or 0) + max(0, self.lap_no.get(n, 0) - (st.get("lap_start") or 0))
            d = self.drivers.get(n) or {}
            out = self.retired.get(n)
            rows.append({
                "pos": len(rows) + 1 if not out else None, "out": out, "number": n, "code": self.code(n), "name": d.get("full_name"),
                "team": d.get("team_name"), "colour": "#" + self.colour(n), "headshot": d.get("headshot_url"),
                "gap": self.gap.get(n), "interval": self.interval.get(n),
                "compound": st.get("compound"), "tyre_age": age, "stint": st.get("stint_number"),
                "pits": self.pits.get(n, 0), "last_lap": self.last_lap.get(n), "best_lap": self.best_lap.get(n),
                "fastest": fastest is not None and self.best_lap.get(n) == fastest,
                "lap": self.lap_no.get(n), "grid": self.grid.get(n),
                "sectors": sector_info.get(n), "live_points": None if out else live_pts.get(n),
                "lap_start": (self.laps_by.get(n, {}).get(self.lap_no.get(n)) or {}).get("start"),
                "stint_laps": self._stint_laps(n, st),
            })

        def title(pic, label):
            if not pic:
                return None
            table, champ, contenders, remaining, pts = pic
            info_of = lambda e: {"number": e.number, "colour": "#" + self.colour(e.number),
                                 "team": (self.drivers.get(e.number) or {}).get("team_name"),
                                 "headshot": (self.drivers.get(e.number) or {}).get("headshot_url"),
                                 "wins": e.wins, "podiums": e.podiums, "poles": e.poles, "starts": e.starts}
            return {"label": label, "champion": champ.name if champ else None,
                    "champion_code": champ.code if champ else None,
                    "champion_info": dict(info_of(champ), points=champ.points,
                                          margin=champ.points - table[1].points if len(table) > 1 else None) if champ else None,
                    "decided_before": getattr(self, "title_decided_before", None),
                    "year": self.session.get("year"),
                    "contenders": [e.code for e in contenders], "rounds_left": len(remaining),
                    "points_left": max_remaining(remaining, rules=season.rules),
                    "table": [{"code": e.code, "name": e.name, "points": e.points, "delta": e.points - e.start}
                              for e in table[:8]]}

        self._team_pic = None
        scenarios = [title(self.live_title_picture(season), "If it finished like this")]
        team_title = self._team_pic
        if self.track_status == "RED" and self.is_race and not self.is_sprint:
            laps_done = max(0, lead_lap - 1)
            pct = round(laps_done / self.scheduled_laps * 100) if self.scheduled_laps else None
            scenarios.append(title(self.live_title_picture(season, reduced=True),
                                   f"If the race is NOT resumed - reduced points ({laps_done}/"
                                   f"{self.scheduled_laps} laps, {pct}%)" if pct is not None else "If not resumed"))
        w = self.weather or {}
        pre = lead_lap == 0 and self.now().timestamp() < self.start_ts
        return {
            "meeting": self.meeting.get("meeting_name") or self.session.get("location"),
            "circuit": self.session.get("circuit_short_name"), "session": self.session["session_name"],
            "status": self.track_status, "lap": lead_lap, "scheduled_laps": self.scheduled_laps,
            "now": self.now().isoformat(), "session_key": self.key, "data_time": self.data_time(), "last_event": getattr(self, "last_event", None),
            "meeting_key": self.session.get("meeting_key"),
            "lead_lap_start": self._lead_lap_start(),
            "session_start": self.session.get("date_start"), "session_end": self.session.get("date_end"),
            "pre_session": pre,
            "prerace": prerace.picture(self, season) if pre else None,
            "updated": self.updated.isoformat() if self.updated else None,
            "weather": {"track": w.get("track_temperature"), "air": w.get("air_temperature"),
                        "rain": bool(w.get("rainfall")), "humidity": w.get("humidity"),
                        "wind": w.get("wind_speed"),
                        # F1's rain sensor can lag a shower by many minutes; race control declaring low grip doesn't
                        "low_grip": self.rc.low_grip} if w else None,
            "rows": rows,
            "messages": [{"lap": m.get("lap_number"), "time": m["date"][11:19], "category": m.get("category"),
                          "flag": m.get("flag"), "text": m.get("message")} for m in self.messages[-40:]][::-1],
            "title": [t for t in scenarios if t],
            "team_title": team_title,
            "quali": quali.picture(self) if quali.is_quali(self.session.get("session_name")) else None,
            **self._extras(),
        }

    def data_time(self):
        """When the newest thing in this frame actually happened on track (epoch seconds).
        The delay buffer runs on this, not on when the frame was built: OpenF1 data reaches us 3-6 s
        after the event and that lag wobbles, so timing by arrival made a delayed view drift against the TV.
        Never earlier than the newest event seen, so a delayed frame can't show something early."""
        now = self.now().timestamp()
        newest_loc = max((tr[-1][0] for tr in self.trail.values() if tr), default=0) / 1000
        if newest_loc > self._newest_loc:
            self._newest_loc = newest_loc
            if now - newest_loc < 30:
                self._latency.append(now - newest_loc)
        lat = sorted(self._latency)[len(self._latency) // 2] if self._latency else 4.0
        newest = newest_loc
        for k, v in self._since.items():
            if v and k != "car_data":
                try:
                    newest = max(newest, datetime.fromisoformat(v).timestamp())
                except ValueError:
                    pass
        self.last_event = newest or None      # unclamped: how long the session has actually been quiet
        self._data_t = max(self._data_t, min(now, max(newest, now - min(max(lat, 1.0), 10.0))))
        return round(self._data_t, 3)

    def _lead_lap_start(self):
        order = self.order()
        laps = self.laps_by.get(order[0], {}) if order else {}
        starts = [l["start"] for l in laps.values() if l.get("start")]
        return max(starts) if starts else None

    def _stint_laps(self, n, st):
        """[[tyre age, lap time], ...] for the current stint's clean laps - for the tyre degradation view."""
        if not st or not st.get("lap_start"):
            return []
        start, age0 = st["lap_start"], st.get("tyre_age_at_start") or 0
        out = []
        for lap, v in sorted(self.laps_by.get(n, {}).items()):
            if lap > start and v.get("t") and not v.get("out"):       # skip the out-lap
                out.append([age0 + lap - start, round(v["t"], 3)])
        return out

    def _sector_colours(self):
        """Last completed lap's sectors, coloured like F1 timing: purple = session best,
        green = personal best, yellow = slower."""
        overall, personal = {}, {}
        for n, laps in self.laps_by.items():
            for l in laps.values():
                for k in ("s1", "s2", "s3"):
                    v = l.get(k)
                    if v:
                        overall[k] = min(overall.get(k, v), v)
                        personal[(n, k)] = min(personal.get((n, k), v), v)
        out = {}
        for n, laps in self.laps_by.items():
            done = [l for no, l in sorted(laps.items()) if l.get("t")]
            if not done:
                continue
            last = done[-1]
            out[n] = [{"t": last.get(k), "c": None if not last.get(k) else
                       "purple" if last[k] <= overall[k] else "green" if last[k] <= personal[(n, k)] else "yellow"}
                      for k in ("s1", "s2", "s3")]
        return out

    def _insights(self):
        info = lambda n: {"number": n, "code": self.code(n), "colour": "#" + self.colour(n),
                          "name": (self.drivers.get(n) or {}).get("full_name")}
        order = [n for n in self.order() if n not in self.retired]
        num = lambda v: v if isinstance(v, (int, float)) else None

        # battles: cars within 1 s of the car ahead
        battles = []
        for i in range(1, len(order)):
            iv = num(self.interval.get(order[i]))
            if iv is not None and iv < 1.0:
                battles.append({"pos": i + 1, "ahead": info(order[i - 1]), "behind": info(order[i]), "gap": iv})

        # pit-now predictor using the circuit's published pit loss
        loss_tbl = (self.circuit or {}).get("pit_loss") or {}
        mode = {"SC": "sc", "VSC": "vsc"}.get(self.track_status, "normal")
        loss = loss_tbl.get(mode) or loss_tbl.get("normal")
        if not loss and self.pit_list:
            lanes = sorted(p.get("lane_duration") or 0 for p in self.pit_list if p.get("lane_duration"))
            loss = lanes[len(lanes) // 2] if lanes else None
        pit_now = []
        if loss and self.track_status != "RED":
            gaps = {n: (0.0 if self.position.get(n) == 1 else num(self.gap.get(n))) for n in order}
            for n in order:
                g = gaps.get(n)
                if g is None:
                    continue
                proj = g + loss
                ahead = [m for m in order if m != n and gaps.get(m) is not None and gaps[m] < proj]
                rejoin = len(ahead) + 1
                behind_of = max(ahead, key=lambda m: gaps[m]) if ahead else None
                pit_now.append(dict(info(n), now=self.position.get(n), rejoin=rejoin,
                                    lost=rejoin - (self.position.get(n) or rejoin),
                                    behind=self.code(behind_of) if behind_of else None,
                                    margin=round(proj - gaps[behind_of], 1) if behind_of else None))

        # speed traps, theoretical best, race pace
        traps, theo, pace = [], [], []
        for n in order:
            laps = [l for l in self.laps_by.get(n, {}).values()]
            st = max((l["st"] for l in laps if l.get("st")), default=None)
            i1 = max((l["i1"] for l in laps if l.get("i1")), default=None)
            i2 = max((l["i2"] for l in laps if l.get("i2")), default=None)
            if st or i1 or i2:
                traps.append(dict(info(n), st=st, i1=i1, i2=i2))
            bs = [min((l[k] for l in laps if l.get(k)), default=None) for k in ("s1", "s2", "s3")]
            if all(bs):
                theo.append(dict(info(n), theoretical=round(sum(bs), 3), best=self.best_lap.get(n),
                                 sectors=[round(b, 3) for b in bs]))
            timed = [l for no, l in sorted(self.laps_by.get(n, {}).items()) if l.get("t") and not l.get("out") and no > 1]
            if len(timed) >= 3:
                ts = sorted(l["t"] for l in timed)
                med = ts[len(ts) // 2]
                clean = [l["t"] for l in timed if l["t"] < med * 1.03][-5:]
                if len(clean) >= 3:
                    pace.append(dict(info(n), avg=round(sum(clean) / len(clean), 3), laps=len(clean)))
        theo.sort(key=lambda x: x["theoretical"])
        pace.sort(key=lambda x: x["avg"])

        # lap chart: order in which cars started each lap (= order at the end of the previous lap)
        chart = {}
        top = max(self.lap_no.values(), default=0)
        for lap in range(2, top + 1):
            crossed = sorted((l["start"], n) for n, laps in self.laps_by.items()
                             for no, l in laps.items() if no == lap and l.get("start"))
            for p, (_, n) in enumerate(crossed, 1):
                chart.setdefault(n, []).append([lap - 1, p])
        if self.grid:
            for n, g in self.grid.items():
                chart.setdefault(n, []).insert(0, [0, g])
        lap_chart = [dict(info(n), points=chart.get(n, [])) for n in order if chart.get(n)]

        start = datetime.fromisoformat(self.session["date_start"])
        return {"battles": battles, "pit_now": pit_now, "pit_loss": loss, "pit_mode": mode,
                "traps": traps, "theoretical": theo, "pace": pace, "lap_chart": lap_chart,
                "session_start": start.isoformat()}

    def _lap_at(self, date):
        """Leader's lap number at a given time (OpenF1 overtakes/pits/radio carry only a timestamp)."""
        order = self.order()
        laps = self.laps_by.get(order[0], {}) if order else {}
        best = 0
        for no, l in laps.items():
            if l["start"] and l["start"] <= date and no > best:
                best = no
        return best or None

    def _extras(self):
        info = lambda n: {"number": n, "code": self.code(n), "colour": "#" + self.colour(n)}
        order = self.order()
        outline = self.outline
        if outline and len(outline) > 400:
            step = len(outline) / 400
            outline = [outline[int(i * step)] for i in range(400)]
        cut = (self.now().timestamp() - 8) * 1000
        cars = [dict(info(n), x=self.loc[n][0], y=self.loc[n][1], pos=self.position.get(n),
                     trail=[p for p in self.trail.get(n, ()) if p[0] >= cut])
                for n in order if n in self.loc and n not in self.retired]
        strategy = []
        for n in order:
            strategy.append(dict(info(n), stints=[
                {"compound": st.get("compound") or "UNKNOWN", "from": st.get("lap_start"),
                 "to": st.get("lap_end") or self.lap_no.get(n), "age": st.get("tyre_age_at_start")}
                for st in self.stint_hist.get(n, [])]))
        gaps = [dict(info(n), points=self.gap_hist.get(n, [])) for n in order[:10]]
        focus = None
        if self.focus:
            n = self.focus
            laps = sorted(self.laps_by.get(n, {}).items())
            focus = dict(info(n), sector_colours=self._sector_colours().get(n), name=(self.drivers.get(n) or {}).get("full_name"),
                         team=(self.drivers.get(n) or {}).get("team_name"),
                         headshot=(self.drivers.get(n) or {}).get("headshot_url"),
                         tel=list(self.tel)[-160:],
                         laps=[{"lap": no, **{k: l[k] for k in ("t", "s1", "s2", "s3", "st", "out")}} for no, l in laps],
                         stints=strategy[order.index(n)]["stints"] if n in order else [],
                         stewarding=(self.rc.per_driver.get(n) or {}).get("items", []),
                         pos=self.position.get(n), best=self.best_lap.get(n))
        wh = self.weather_hist[-120:]
        return {
            "overtake_mode": self.rc.overtake_mode,
            "incidents": self.incident_list(),
            "rc": self.rc.snapshot(self.code),
            "map": {"outline": (self.circuit or {}).get("outline") or outline, "cars": cars,
                    "elevation": self._elevation((self.circuit or {}).get("outline") or outline),
                    "rotation": (self.circuit or {}).get("rotation", 0),
                    "sectors": (self.circuit or {}).get("sectors", []),
                    "corners": (self.circuit or {}).get("corners", [])},
            "insights": self._insights(),
            "overtakes": [{"time": o["date"][11:19], "lap": self._lap_at(o["date"]), "pos": o.get("position"),
                           "by": info(o["overtaking_driver_number"]), "on": info(o["overtaken_driver_number"])}
                          for o in reversed(self.overtake_list[-60:])],
            "overtake_counts": {self.code(n): sum(1 for o in self.overtake_list if o["overtaking_driver_number"] == n)
                                for n in order},
            "pits": [dict(info(p["driver_number"]), lap=p.get("lap_number"), time=p["date"][11:19],
                          lane=p.get("lane_duration") or p.get("pit_duration"), stop=p.get("stop_duration"))
                     for p in reversed(self.pit_list)],
            "radio": [dict(info(r["driver_number"]), time=r["date"][11:19], lap=self._lap_at(r["date"]),
                           date=r["date"], url=r.get("recording_url")) for r in reversed(self.radio)],
            "strategy": strategy,
            "gaps": gaps,
            "weather_hist": [{"t": w["date"][11:16], "track": w.get("track_temperature"), "air": w.get("air_temperature"),
                              "rain": bool(w.get("rainfall"))} for w in wh],
            "focus": focus,
            "max_lap": max(self.lap_no.values(), default=0),
        }

    def render(self, season=None):
        out = []
        s, m = self.session, self.meeting
        lead_lap = max(self.lap_no.values(), default=0)
        status = self.track_status
        out.append(header(f"LIVE - {m.get('meeting_name', s['location'])} - {s['session_name'].upper()}"))
        out.append(f"  {c(' ' + status + ' ', FLAG_COLOURS.get(status), bold=True)}   "
                   f"Lap {c(lead_lap, bold=True)}   "
                   + (dim(f"track {self.weather['track_temperature']}C  air {self.weather['air_temperature']}C  "
                          f"{'RAIN' if self.weather.get('rainfall') else 'dry'}") if self.weather else "")
                   + dim(f"   updated {self.updated:%H:%M:%S} UTC" if self.updated else ""))
        out.append(dim(f"  {'Pos':>3}  {'Driver':<6}{'Gap':>10}{'Int':>9}  {'Tyre':<9}{'Pits':>4}"
                       f"  {'Last lap':>9}  {'Best':>9}  {'Grid':>4} {'+/-':>4}"))
        fastest = min(self.best_lap.values(), default=None)
        for n in self.order():
            pos = self.position[n]
            st = self.stints.get(n)
            tyre_s = "-"
            if st:
                age = (st.get("tyre_age_at_start") or 0) + max(0, self.lap_no.get(n, 0) - (st.get("lap_start") or 0))
                tyre_s = f"{tyre(st.get('compound'))} {age:>2} laps"
            gap, inter = self.gap.get(n), self.interval.get(n)
            fmt = lambda v: "-" if v is None else (f"+{v:.3f}" if isinstance(v, (int, float)) else str(v))
            best = self.best_lap.get(n)
            best_s = fmt_time(best) if best else "-"
            if best and best == fastest:
                best_s = c(best_s, "B138DD", bold=True)
            grid = self.grid.get(n)
            delta = ""
            if grid:
                g = grid - pos
                delta = c(f"+{g}", "39B54A") if g > 0 else c(str(g), "FF5050") if g < 0 else " 0"
            out.append(f"  {pos:>3}  {pad(c(self.code(n), self.colour(n), bold=True), 6)}"
                       f"{('LEADER' if pos == 1 else fmt(gap)):>10}{('' if pos == 1 else fmt(inter)):>9}  "
                       f"{pad(tyre_s, 9)}{self.pits.get(n, 0):>4}  {fmt_time(self.last_lap.get(n)):>9}  "
                       f"{pad(best_s, 9, right=True)}  {grid or '-':>4} {pad(delta, 4, right=True)}")
        if self.messages:
            out.append(c("\n  Race control", bold=True))
            for msg in self.messages[-6:]:
                lap = msg.get("lap_number")
                out.append(f"    {('L' + str(lap)) if lap else '':<5}{msg.get('message')}")
        rf = self.rc.red
        if rf:
            since = datetime.fromisoformat(rf["since"])
            if rf["state"] == "suspended":
                mins = int((self.now() - since).total_seconds() // 60)
                line = f"\n  RED FLAG since lap {rf['lap']} ({mins} min)"
                if rf["resume_at"]:
                    left = int((datetime.fromisoformat(rf["resume_at"]) - self.now()).total_seconds())
                    line += f" - resumes at {rf['resume_local']} local" + (f" (in {left // 60}:{left % 60:02d})" if left > 0 else "")
                if rf["start_type"]:
                    line += f" - {rf['start_type']}"
                out.append(c(line, "FF5050", bold=True))
                if rf["order"]:
                    out.append("  Restart order: " + " ".join(self.code(n) for n in rf["order"]))
            elif rf["state"] == "resumed":
                out.append(c(f"\n  RACE RESUMED on lap {rf['resumed_lap']} after {rf['duration_s'] // 60} min "
                             f"{rf['duration_s'] % 60} s stoppage", "39B54A", bold=True))
            elif rf["state"] == "abandoned":
                out.append(c("\n  RACE WILL NOT BE RESUMED - result stands from before the red flag", "FF5050", bold=True))
        if self.track_status == "RED" and self.is_race and not self.is_sprint and self.scheduled_laps:
            red = self.live_title_picture(season, reduced=True)
            if red:
                laps_done = max(0, lead_lap - 1)
                out.append(c(f"\n  RED FLAG: if the race is not resumed, reduced points apply "
                             f"({laps_done}/{self.scheduled_laps} laps)", "FF5050", bold=True))
                out.append("    " + (c(f"{red[1].name.upper()} would still be WORLD CHAMPION", GOLD, bold=True)
                                     if red[1] else "Title still open: " + ", ".join(e.code for e in red[2])))
        pic = self.live_title_picture(season)
        if pic:
            table, champ, contenders, remaining, _ = pic
            out.append(c("\n  LIVE CHAMPIONSHIP (if it finished like this)", GOLD, bold=True))
            for i, e in enumerate(table[:5]):
                delta = e.points - e.start
                out.append(f"    {i+1}. {e.code:<4}{e.points:>6g}" + (c(f"  (+{delta:g})", "39B54A") if delta else ""))
            if champ:
                out.append(c(f"  >> {champ.name.upper()} WOULD BE CROWNED WORLD CHAMPION AT THE FLAG!", GOLD, bold=True))
            else:
                out.append(f"  Still in the fight: {', '.join(e.code for e in contenders)}"
                           + dim(f"  ({len(remaining)} rounds left after this)"))
        return "\n".join(out)


def watch(season=None, session_key="latest", interval=10, once=False):
    tracker = LiveTracker(session_key)
    interval = max(8, interval)   # ~8 requests per refresh -> stays under 60/minute
    while True:
        tracker.refresh()
        screen = tracker.render(season)
        if once:
            print(screen)
            return tracker
        if sys.stdout.isatty():
            os.system("clear" if os.name != "nt" else "cls")
        print(screen)
        print(dim(f"\n  Refreshing every {interval}s - Ctrl+C to stop"))
        if tracker.track_status == "CHEQUERED" and tracker.refreshes > 1:
            print(c("  Chequered flag! Final classification will appear in the race report shortly.", GOLD))
        time.sleep(interval)
