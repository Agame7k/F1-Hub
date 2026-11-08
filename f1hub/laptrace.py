"""One driver's lap as a trace - speed, throttle, brake, gear and x/y against distance round the lap.

Powers the head-to-head comparison, braking zones on the map and the ghost car. Completed laps never
change, so everything is cached forever. `before` keeps it spoiler-free: only laps that had finished
by that moment (the time the viewer is watching) are considered.
"""
import math
from datetime import datetime, timedelta

from . import api


def _ts(s):
    return datetime.fromisoformat(s).timestamp()


def pick_lap(session_key, driver, lap="last", before=None):
    laps = [l for l in api.get("laps", ttl=30, session_key=session_key, driver_number=driver)
            if l.get("lap_duration") and l.get("date_start")]
    if before:
        cut = _ts(before)
        laps = [l for l in laps if _ts(l["date_start"]) + l["lap_duration"] <= cut]
    if not laps:
        return None
    if lap == "best":
        clean = [l for l in laps if not l.get("is_pit_out_lap")] or laps
        return min(clean, key=lambda l: l["lap_duration"])
    if lap == "last":
        return max(laps, key=lambda l: l["lap_number"])
    return next((l for l in laps if l["lap_number"] == int(lap)), None)


def lap_trace(session_key, driver, lap="last", before=None):
    l = pick_lap(session_key, driver, lap, before)
    if not l:
        return None
    start = datetime.fromisoformat(l["date_start"])
    end = start + timedelta(seconds=l["lap_duration"])
    win = {"date>=": start.isoformat(), "date<": (end + timedelta(seconds=0.3)).isoformat()}
    car = sorted(api.get("car_data", session_key=session_key, driver_number=driver, **win), key=lambda r: r["date"])
    loc = sorted(api.get("location", session_key=session_key, driver_number=driver, **win), key=lambda r: r["date"])
    t0 = start.timestamp()
    # distance along the lap from the x/y samples
    pts, dist, prev = [], 0.0, None
    for r in loc:
        if prev:
            dist += math.hypot(r["x"] - prev["x"], r["y"] - prev["y"])
        pts.append((_ts(r["date"]) - t0, r["x"], r["y"], dist))
        prev = r
    total = dist or 1.0
    out, j = [], 0
    for r in car:
        t = _ts(r["date"]) - t0
        if not pts:
            break
        while j + 1 < len(pts) and pts[j + 1][0] <= t:
            j += 1
        a = pts[j]
        b = pts[j + 1] if j + 1 < len(pts) else a
        f = 0 if b[0] == a[0] else max(0.0, min(1.0, (t - a[0]) / (b[0] - a[0])))
        x, y, d = (a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, a[3] + (b[3] - a[3]) * f)
        out.append([round(t, 3), round(d / total, 5), r.get("speed"), r.get("throttle"),
                    1 if (r.get("brake") or 0) > 0 else 0, r.get("n_gear"), round(x), round(y)])
    return {"driver": driver, "lap": l["lap_number"], "start": l["date_start"], "duration": l["lap_duration"],
            "sectors": [l.get("duration_sector_1"), l.get("duration_sector_2"), l.get("duration_sector_3")],
            "fields": ["t", "frac", "speed", "throttle", "brake", "gear", "x", "y"], "samples": out}
