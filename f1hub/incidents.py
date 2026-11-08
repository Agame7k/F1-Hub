"""Crash / spin / stopped-car detection from OpenF1 location data (every car, ~4 samples a second).

The signature, checked against real crashes (2024 São Paulo qualifying: COL, SAI, ALO, ALB):
  a car at racing speed (> ~140 km/h) is down to walking pace (< ~16 km/h) within 3.5 s and stays there.
Nothing normal on track does that - the slowest corners are ~60 km/h, and a pit stop is preceded by
seconds at the 80 km/h limiter. Location units are ~0.11 m (505 units/s ~ 205 km/h on car_data).

  crash    the impact signature above. If the car drives off again soon it becomes a spin ("rejoined").
  stopped  a slow stop (beached, mechanical): only reported once race control backs it up - a yellow in that
           marshal sector, red flag, SC/VSC, or a message naming the car - because a car parked in the pit
           lane looks the same.
"""
import math
import re
from collections import deque
from datetime import datetime, timezone

KMH = 0.406              # location units/s -> km/h (approximate)
FAST = 345               # ~140 km/h
SLOW = 40                # ~16 km/h
MOVING = 100             # ~40 km/h
REJOIN = 250             # ~100 km/h, held for 2 s = driving off again
DECEL_WINDOW = 6.0       # s from last fast sample to first slow one (a slide through gravel takes a few seconds)
LIMITER = (150, 240)     # ~60-100 km/h: the pit-lane speed limit
LIMITER_HOLD = 2.0       # s steady at limiter speed = in the pit lane, not a crash
SLOW_HOLD = 1.5          # s below SLOW before it counts
STOPPED_HOLD = 8.0       # s below SLOW for a slow stop
SHOW_FOR = 600           # s an incident stays in the list


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


class Car:
    __slots__ = ("pts", "last_fast", "last_moving", "slow_since", "fast_since", "peak", "event", "limiter_since", "pit_like")

    def __init__(self):
        self.pts = deque(maxlen=80)      # (t, x, y)
        self.last_fast = self.last_moving = self.slow_since = self.fast_since = None
        self.peak = deque(maxlen=40)     # (t, smoothed speed) for "how fast was it going"
        self.event = None                # the car's open incident
        self.limiter_since = None        # steady at pit-limiter speed since...
        self.pit_like = None             # ...and when that lasted long enough to look like the pit lane


class IncidentDetector:
    def __init__(self):
        self.cars = {}
        self.events = []                 # newest last
        self._next = 1

    # ------------------------------------------------------------------ feeding
    def add(self, n, t, x, y, suppressed=False):
        """One location sample (t in epoch seconds), in time order per car."""
        c = self.cars.setdefault(n, Car())
        if c.pts and t <= c.pts[-1][0]:
            return
        c.pts.append((t, x, y))
        # smoothed speed over ~1 s: single-sample speeds jump about because of timestamp jitter
        ref = next((p for p in c.pts if p[0] >= t - 1.2), None)
        if ref is None or t - ref[0] < 0.6:
            return
        v = math.dist((x, y), ref[1:]) / (t - ref[0])
        c.peak.append((t, v))
        if LIMITER[0] <= v <= LIMITER[1]:
            c.limiter_since = c.limiter_since or t
            if t - c.limiter_since >= LIMITER_HOLD:
                c.pit_like = t
        else:
            c.limiter_since = None
        if v >= FAST:
            c.last_fast = t
        if v >= MOVING:
            c.last_moving = t
        if v <= SLOW:
            c.slow_since = c.slow_since or t
        elif v > 2 * SLOW:
            c.slow_since = None
        c.fast_since = (c.fast_since or t) if v >= REJOIN else None

        e = c.event
        if e and e["state"] == "stopped" and c.fast_since and t - c.fast_since >= 2:
            e["state"], e["rejoined_at"] = "rejoined", _iso(t)
            if e["kind"] == "crash" and t - e["t"] < 180:
                e["kind"] = "spin"          # stopped and drove off: a spin, not a crash
            c.event = None
            return
        if e or suppressed or not c.slow_since or t - c.slow_since < SLOW_HOLD:
            return
        stop_t = c.slow_since
        pitlane = c.pit_like is not None and c.last_fast is not None and c.pit_like > c.last_fast
        if c.last_fast and 0 <= stop_t - c.last_fast <= DECEL_WINDOW and not pitlane:
            top = sorted((s for ts, s in c.peak if stop_t - 6 <= ts <= stop_t), reverse=True)[:5]
            peak = top[len(top) // 2] if top else FAST        # median of the top readings: timestamp jitter makes spikes
            c.event = self._new(n, "crash", stop_t, x, y, peak)
        elif t - stop_t >= STOPPED_HOLD and c.last_moving and stop_t - c.last_moving <= 15 and not pitlane:
            c.event = self._new(n, "stopped", stop_t, x, y, None, needs_confirmation=True)

    def _new(self, n, kind, t, x, y, peak, needs_confirmation=False):
        e = {"id": self._next, "number": n, "kind": kind, "state": "stopped", "t": t, "at": _iso(t), "x": x, "y": y,
             "speed_before": round(peak * KMH / 10) * 10 if peak else None, "needs_confirmation": needs_confirmation,
             "rejoined_at": None}
        self._next += 1
        self.events.append(e)
        del self.events[:-20]
        return e

    # ------------------------------------------------------------------ reading
    def snapshot(self, now, info, place, confirmations):
        """Incidents of the last few minutes, plain data for the GUI.
        info(n) -> {number, code, colour}; place(x, y) -> {corner, sector}; confirmations(e, place) -> [texts]."""
        out = []
        for e in self.events:
            if now - e["t"] > SHOW_FOR or e["t"] > now:
                continue
            where = place(e["x"], e["y"])
            conf = confirmations(e, where)
            # a slow stop only counts with something specific to this car: a yellow in its marshal sector, or a
            # message naming it (a red flag / SC alone could be about anyone)
            if e["needs_confirmation"] and not any(c.startswith(("Yellow", "Double Yellow")) or str(e["number"]) in c for c in conf):
                continue
            out.append(dict(info(e["number"]), id=e["id"], kind=e["kind"], state=e["state"], at=e["at"],
                            x=e["x"], y=e["y"], speed_before=e["speed_before"], rejoined_at=e["rejoined_at"],
                            corner=where.get("corner"), sector=where.get("sector"), confirmed_by=conf[:4]))
        return out[::-1]


def confirmations_from(messages, n, t, sector, window=(-20, 90)):
    """Race-control messages around the incident that back it up."""
    found = []
    for m in messages:
        if not m.get("date"):
            continue
        dt = datetime.fromisoformat(m["date"]).timestamp() - t
        if not window[0] <= dt <= window[1]:
            continue
        msg = (m.get("message") or "").upper()
        flag = m.get("flag")
        if flag in ("YELLOW", "DOUBLE YELLOW") and sector and m.get("sector") in (sector - 1, sector, sector + 1):
            found.append(msg.title() if flag == "DOUBLE YELLOW" else msg.capitalize())
        elif flag == "RED" or re.search(r"\bRED FLAG\b", msg):
            found.append("Red flag")
        elif "VIRTUAL SAFETY CAR DEPLOYED" in msg or "VSC DEPLOYED" in msg:
            found.append("VSC deployed")
        elif "SAFETY CAR DEPLOYED" in msg:
            found.append("Safety car deployed")
        elif "MEDICAL CAR" in msg:
            found.append("Medical car deployed")
        elif re.search(rf"\bCAR {n}\b", msg) and re.search(r"STOPPED|CRASH|BARRIER|SPUN|BEACHED|GRAVEL|IMPACT|RECOVERY", msg):
            found.append(m.get("message"))
    return list(dict.fromkeys(found))
