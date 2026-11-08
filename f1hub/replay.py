"""Replay any session on a virtual clock, using the same LiveTracker logic.

Every request the tracker makes is answered from data cut off at the virtual time T,
so nothing from "the future" (results, flags, points) can leak through - spoiler free.
Full-session endpoints are fetched once and filtered in memory; location and car_data
are too big for that, so they're fetched in small windows that end at T.
"""
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


class SessionStore:
    """Full-session rows per endpoint, shared between replay trackers of the same session."""

    def __init__(self, session):
        self.session = session
        self.key = session["session_key"]
        self.rows = {}
        self.loaded_at = {}

    @property
    def finished(self):
        end = _ts(self.session.get("date_end"))
        return end is not None and time.time() > end + 3600

    def get(self, endpoint, upto_ts):
        stale = (not self.finished and endpoint in self.rows
                 and upto_ts > self.loaded_at[endpoint] - 15 and time.time() - self.loaded_at[endpoint] > 20)
        if endpoint not in self.rows or stale:
            ttl = api.FOREVER if self.finished else 20
            rows = api.get(endpoint, ttl=ttl, session_key=self.key)
            for r in rows:
                r["_ts"] = _ts(r.get("date") or r.get("date_start"))
            self.rows[endpoint] = rows
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

    # ----------------------------------------------------------------- markers for the scrubber (only up to T)
    def markers(self):
        T = self._t.timestamp()
        out = []
        for m in self.store.get("race_control", T):
            if m["_ts"] > T:
                continue
            up = (m.get("message") or "").upper()
            kind = None
            if is_red_flag(m):
                kind = "red"
            elif "VIRTUAL SAFETY CAR DEPLOYED" in up or "VSC DEPLOYED" in up:
                kind = "vsc"
            elif "SAFETY CAR DEPLOYED" in up:
                kind = "sc"
            elif m.get("flag") == "CHEQUERED":
                kind = "chequered"
            if kind:
                out.append({"t": m["date"], "kind": kind, "lap": m.get("lap_number")})
        return out
