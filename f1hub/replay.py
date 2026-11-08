"""Replay any session on a virtual clock, using the same LiveTracker logic.

Every request the tracker makes is answered from data cut off at the virtual time T,
so nothing from "the future" (results, flags, points) can leak through - spoiler free.
Full-session endpoints are fetched once and filtered in memory; location and car_data
are too big for that, so they're fetched in small windows that end at T.
"""
import re
import time
from datetime import datetime, timedelta, timezone

from . import api
from .live import LiveTracker
from .racecontrol import is_red_flag
from .rules import RULES

PRELOAD = ("position", "intervals", "race_control", "laps", "stints", "pit", "weather",
           "overtakes", "team_radio", "championship_drivers")
WINDOWED = ("location", "car_data")


def _ts(s):
    return datetime.fromisoformat(s).timestamp() if s else None


SETTLE = 600            # s: OpenF1 is still filling in a session for a few minutes after it ends
NO_END_MESSAGE = 6 * 3600   # s past the schedule: a session whose closing message never came is over anyway


def _stamp(rows):
    for r in rows:
        r["_ts"] = _ts(r.get("date") or r.get("date_start"))
    return rows


class SessionStore:
    """Full-session rows per endpoint, shared between replay trackers of the same session.

    Data is only treated as final - cached for good - once race control says the session is over (in qualifying,
    the end of Q3) and OpenF1 has had SETTLE seconds to catch up. The scheduled date_end is no guide: red flags
    can run a session long past it, and a copy cached then would be missing everything after."""

    def __init__(self, session):
        self.session = session
        self.key = session["session_key"]
        self.rows = {}
        self.loaded_at = {}
        self._final = None       # real end (epoch s) once race control shows the session is over

    def _final_end(self, rc):
        name = self.session.get("session_name") or ""
        sched = _ts(self.session.get("date_end")) or 0
        status = [r for r in rc if r.get("category") == "SessionStatus" and r["_ts"]]
        if status:
            last = max(status, key=lambda r: r["_ts"])
            up = (last.get("message") or "").upper()
            done = any(w in up for w in ("FINISHED", "FINALISED", "ENDS"))
            if done and re.search(r"qualifying|shootout", name, re.I) and last.get("qualifying_phase") not in (None, 3):
                done = False     # only Q1 or Q2 has ended
            if done:
                return max(sched, last["_ts"])
        if time.time() > sched + NO_END_MESSAGE:
            return max([sched] + [r["_ts"] for r in status])
        return None

    @property
    def finished(self):
        return self._final is not None and time.time() > self._final + SETTLE

    def actual_end(self):
        """When the session really finished (or the latest sign of it so far, while it may still be running)."""
        rc = self.get("race_control", time.time())
        if self._final:
            return datetime.fromtimestamp(self._final, timezone.utc)
        done = [r["_ts"] for r in rc if r.get("category") == "SessionStatus" and r["_ts"]]
        return datetime.fromtimestamp(max([_ts(self.session.get("date_end")) or 0] + done), timezone.utc)

    def _load(self, endpoint):
        if endpoint == "race_control":
            # a cached copy (if any) says whether the session is over; only trust it if saved after it settled
            rows = _stamp(api.get(endpoint, ttl=api.FOREVER, session_key=self.key))
            final = self._final_end(rows)
            if final is not None and time.time() > final + SETTLE:
                rows = api.get(endpoint, ttl=api.FOREVER, fetched_after=final + SETTLE, session_key=self.key)
            else:
                rows = api.get(endpoint, ttl=20, session_key=self.key)
            rows = _stamp(rows)
            self._final = self._final_end(rows)
            return rows
        if self._final is None and "race_control" not in self.rows:
            self.get("race_control", time.time())        # learn whether (and when) the session ended first
        if self.finished:
            return _stamp(api.get(endpoint, ttl=api.FOREVER, fetched_after=self._final + SETTLE, session_key=self.key))
        return _stamp(api.get(endpoint, ttl=20, session_key=self.key))

    def get(self, endpoint, upto_ts):
        stale = (not self.finished and endpoint in self.rows
                 and upto_ts > self.loaded_at[endpoint] - 15 and time.time() - self.loaded_at[endpoint] > 20)
        if endpoint not in self.rows or stale:
            self.rows[endpoint] = self._load(endpoint)
            self.loaded_at[endpoint] = time.time()
        return self.rows[endpoint]


class ReplayTracker(LiveTracker):
    CADENCE = dict(LiveTracker.CADENCE, location=(2, 30), car_data=(2, 20))

    def __init__(self, store, at):
        self.store = store
        self._t = at if isinstance(at, datetime) else datetime.fromisoformat(at)
        super().__init__(store.key)

    # ----------------------------------------------------------------- clock
    def now(self):
        return self._t

    def advance(self, seconds):
        self._t += timedelta(seconds=seconds)

    # ----------------------------------------------------------------- data
    def _fetch(self, endpoint, **params):
        T = self._t.timestamp()
        if endpoint in WINDOWED:
            cap = self._t.astimezone(timezone.utc).isoformat()
            if not params.get("date<") or _ts(params["date<"]) > T:
                params["date<"] = cap
            lo = params.get("date>") or params.get("date>=")
            if lo and _ts(lo) >= T:
                return []
            return api.get(endpoint, ttl=api.FOREVER, **params)   # past windows never change
        if endpoint not in PRELOAD:
            return api.get(endpoint, ttl=3600, **params)
        rows = self.store.get(endpoint, T)
        out = self._at_time(endpoint, rows, T)
        return [r for r in out if self._match(r, params)]

    def _driver_laps(self, T):
        laps = {}
        for l in self.store.get("laps", T):
            if l["_ts"] is not None and l["_ts"] <= T:
                laps[l["driver_number"]] = max(laps.get(l["driver_number"], 0), l["lap_number"])
        return laps

    def _at_time(self, endpoint, rows, T):
        if endpoint == "laps":
            out = []
            for l in rows:
                if l["_ts"] is None or l["_ts"] > T:
                    continue
                l2 = dict(l)
                if l.get("lap_duration") and l["_ts"] + l["lap_duration"] > T:
                    l2["lap_duration"] = None           # lap still in progress at T
                    acc = l["_ts"]
                    for k in ("duration_sector_1", "duration_sector_2", "duration_sector_3"):
                        if l2.get(k) is not None:
                            acc += l2[k]
                            if acc > T:
                                l2[k] = None
                    l2["st_speed"] = l2["i2_speed"] = None if acc > T else l2.get("i2_speed")
                out.append(l2)
            return out
        if endpoint == "stints":
            cur = self._driver_laps(T)
            out = []
            for st in rows:
                lap = cur.get(st["driver_number"], 0)
                if (st.get("lap_start") or 0) <= max(lap, 1) and lap:
                    st2 = dict(st)
                    st2["lap_end"] = min(st.get("lap_end") or lap, lap)
                    out.append(st2)
            return out
        if endpoint == "championship_drivers":
            # points_current at the end would be a spoiler: rebuild it from the order at T
            pos = {}
            for p in sorted((r for r in self.store.get("position", T) if r["_ts"] <= T), key=lambda r: r["_ts"]):
                pos[p["driver_number"]] = p["position"]
            table = RULES["sprint_points"] if self.is_sprint else RULES["gp_points"]
            out = []
            for r in rows:
                p = pos.get(r["driver_number"])
                r2 = dict(r)
                r2["points_current"] = r["points_start"] + (table[p - 1] if p and p <= len(table) else 0)
                out.append(r2)
            return out
        return [r for r in rows if r["_ts"] is None or r["_ts"] <= T]

    @staticmethod
    def _match(r, params):
        for key, v in params.items():
            if key == "session_key":
                continue
            if key in ("date>", "date>=", "date<"):
                rt = r.get("_ts")
                vt = _ts(v)
                if rt is None:
                    continue
                if key == "date>" and not rt > vt or key == "date>=" and not rt >= vt or key == "date<" and not rt < vt:
                    return False
            elif key == "lap_number>=":
                if (r.get("lap_number") or 0) < v:
                    return False
            elif r.get(key) != v:
                return False
        return True

    # ----------------------------------------------------------------- the scrub bar's timeline
    def timeline(self, spoiler=True):
        """Chapters, flags and shaded spans over the whole session, for finding your way around a replay.
        Chapter starts (Q1/Q2/Q3, the race start) are always given - that's how you jump to Q3. Everything else past
        the replay clock (red flags, safety cars, restarts, chequered flags, how long a segment ran) is left out while
        spoiler mode is on: it would give away what's coming."""
        T = self._t.timestamp()
        name = self.session.get("session_name") or ""
        quali = bool(re.search(r"qualifying|shootout", name, re.I))
        seg = "SQ" if quali and re.search(r"sprint", name, re.I) else "Q"
        events, bands = [], []
        started, seg_open, red_open = set(), None, None

        def ev(m, kind, label="", lead=20):
            events.append({"t": m["date"], "kind": kind, "label": label, "lap": m.get("lap_number"), "lead": lead,
                           "text": m.get("message")})

        for m in sorted(self.store.get("race_control", T), key=lambda m: m["_ts"] or 0):
            up = (m.get("message") or "").upper()
            status = m.get("category") == "SessionStatus"
            phase = m.get("qualifying_phase")
            if status and "STARTED" in up:
                if red_open:
                    bands.append({"kind": "red", "from": red_open, "to": m["date"]})
                    red_open = None
                if phase not in started:
                    started.add(phase)
                    label = f"{seg}{phase}" if quali and phase else "Start"
                    ev(m, "chapter", label, lead=15)
                    seg_open = (label, m["date"])
                else:
                    ev(m, "restart", "Restart", lead=15)
            elif is_red_flag(m) and not status:
                ev(m, "red", lead=20)
                red_open = red_open or m["date"]
            elif "VIRTUAL SAFETY CAR DEPLOYED" in up or "VSC DEPLOYED" in up:
                ev(m, "vsc", lead=30)
            elif "SAFETY CAR DEPLOYED" in up:
                ev(m, "sc", lead=30)
            elif status and ("FINISHED" in up or "ENDS" in up):
                if seg_open:
                    bands.append({"kind": "seg", "label": seg_open[0], "from": seg_open[1], "to": m["date"]})
                    seg_open = None
                if red_open:          # stopped and never restarted ("Q2 will not be resumed")
                    bands.append({"kind": "red", "from": red_open, "to": m["date"]})
                    red_open = None
                ev(m, "chequered", lead=90)
        now = datetime.fromtimestamp(T, timezone.utc).isoformat()
        if seg_open:
            bands.append({"kind": "seg", "label": seg_open[0], "from": seg_open[1], "to": None})
        if red_open:
            bands.append({"kind": "red", "from": red_open, "to": None})
        if spoiler:
            events = [e for e in events if e["kind"] == "chapter" or _ts(e["t"]) <= T]
            cut = []
            for b in bands:
                if _ts(b["from"]) > T:
                    continue
                if b["to"] is None or _ts(b["to"]) > T:
                    b = dict(b, to=now)       # still running as far as you know
                cut.append(b)
            bands = cut
        return {"events": events, "bands": bands}
