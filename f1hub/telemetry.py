"""Per-corner telemetry: phase 4 of docs/roadmap.md.

For a compiled session (f1hub/dataset.py), fetch every driver's car data (speed, throttle, brake, gear, RPM, DRS)
and location for the whole session, then:

1. build the circuit's reference line from the session's fastest lap, and find its corners automatically (speed
   minima, named after the official corner numbers where the circuit data has them);
2. put every sample of every lap on that line (distance from the start line, by projecting x/y onto it), so laps
   and drivers compare at the same point of the track;
3. measure every corner of every lap: braking point, peak deceleration, entry, minimum and exit speed, where the
   throttle is fully open again, gear, and the time through the corner;
4. measure every lap: top speed, share of the lap at full throttle and on the brakes, and lift-and-coast.

The raw telemetry (about 0.3 MB a driver, gzipped) is kept in DATA_DIR/telemetry/<session_key>/ so the analysis can
be run again offline; only the measurements go into the dataset.

OpenF1 samples car data and location about four times a second: at 300 km/h that is one sample every ~20 m, so a
braking point is good to about +-10 m. Comparisons over many laps average much of that out.
"""
import bisect
import gzip
import json
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import api

log = logging.getLogger(__name__)

WORKERS = 3              # drivers fetched at once (OpenF1's rate limits still apply to all of them together)
TEL_KINDS = {"quali": ("Qualifying", "Sprint Qualifying"), "race": ("Race", "Sprint")}
MIN_DROP = 12            # km/h a corner's speed must drop from the fastest point before it
MERGE = 140              # m: speed minima closer than this are one corner (a chicane)
LABEL_WITHIN = 160       # m: an official corner this close to a minimum names it
APEX_WINDOW = 120        # m either side of the reference apex to look for a lap's own minimum speed
EXIT_AT = 150            # m after the apex where exit speed is read
FULL_THROTTLE = 95       # % throttle that counts as flat out
SLOW, FAST = 120, 200    # km/h minimum speed: slow / medium / fast corners
LICO_MIN = 0.4           # s with throttle off and no brake, before a braking zone, at speed: lift and coast
LICO_SPEED = 200         # km/h
MAX_GAP = 1.6            # s: a lap with a bigger hole in its car data isn't measured
STRAIGHT_MIN = 300       # m at full throttle on the reference lap: a straight
ACCEL_OVER = 200         # m from the start of a straight over which the speed gained is measured
PLATEAU = 3              # km/h below a straight's top speed: "flat" from there to the braking point
VERSION = 2              # of the measurements: a dataset measured by an older version is measured again (from the raw files)


def _ts(s):
    return datetime.fromisoformat(s).timestamp()


def raw_dir(data_dir, session_key):
    return data_dir / "telemetry" / str(session_key)


# ------------------------------------------------------------------------------------------------ fetching
def _columns(car, loc, t0):
    car = sorted(car, key=lambda r: r["date"])
    loc = sorted(loc, key=lambda r: r["date"])
    ms = lambda r: round((_ts(r["date"]) - t0) * 1000)
    return {"t0": t0,
            "car": {"t": [ms(r) for r in car], "speed": [r.get("speed") or 0 for r in car],
                    "throttle": [min(r.get("throttle") or 0, 100) for r in car],
                    "brake": [1 if (r.get("brake") or 0) > 0 else 0 for r in car],
                    "gear": [r.get("n_gear") or 0 for r in car], "rpm": [r.get("rpm") or 0 for r in car],
                    "drs": [r.get("drs") or 0 for r in car]},
            "loc": {"t": [ms(r) for r in loc], "x": [r.get("x") or 0 for r in loc], "y": [r.get("y") or 0 for r in loc]}}


def fetch_session(session, drivers, data_dir, progress=lambda msg: None):
    """Fetch (or reuse) every driver's raw telemetry for a session. Returns {driver_number: columns}."""
    folder = raw_dir(data_dir, session["session_key"])
    folder.mkdir(parents=True, exist_ok=True)
    start = _ts(session["date_start"]) - 300
    end = _ts(session["date_end"]) + 900          # sessions overrun (red flags)
    win = {"date>=": datetime.fromtimestamp(start, timezone.utc).isoformat(),
           "date<": datetime.fromtimestamp(end, timezone.utc).isoformat()}
    out, done, lock = {}, [0], threading.Lock()

    def one(n):
        path = folder / f"{n}.json.gz"
        if path.exists():
            try:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    return n, json.load(f)
            except (OSError, ValueError):
                path.unlink(missing_ok=True)
        car = api.get("car_data", cache=False, session_key=session["session_key"], driver_number=n, **win)
        loc = api.get("location", cache=False, session_key=session["session_key"], driver_number=n, **win)
        cols = _columns(car, loc, start)
        tmp = path.with_suffix(".part")
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
            json.dump(cols, f, separators=(",", ":"))
        tmp.replace(path)
        return n, cols

    with ThreadPoolExecutor(WORKERS) as ex:
        for n, cols in ex.map(one, drivers):
            out[n] = cols
            with lock:
                done[0] += 1
                progress(f"telemetry {done[0]}/{len(drivers)} drivers")
    return out


# ------------------------------------------------------------------------------------------------ one lap's samples
def lap_samples(cols, lap_start, lap_time, strict=True):
    """A lap's car samples with x/y interpolated from the location stream: [(t from lap start, speed, throttle,
    brake, gear, rpm, drs, x, y)]. None if the data has holes. strict=False: don't require samples right up to
    the end (a window running on past the lap, into data that may stop)."""
    t0 = cols["t0"]
    c, l = cols["car"], cols["loc"]
    a_ms, b_ms = round((lap_start - t0) * 1000), round((lap_start + lap_time - t0) * 1000)
    i, j = bisect.bisect_left(c["t"], a_ms), bisect.bisect_right(c["t"], b_ms)
    if j - i < max(10, lap_time * 1.5):
        return None
    lt = l["t"]
    out = []
    for k in range(i, j):
        t = c["t"][k]
        p = bisect.bisect_left(lt, t)
        if p == 0 or p >= len(lt):
            continue
        ta, tb = lt[p - 1], lt[p]
        f = 0 if tb == ta else (t - ta) / (tb - ta)
        x = l["x"][p - 1] + (l["x"][p] - l["x"][p - 1]) * f
        y = l["y"][p - 1] + (l["y"][p] - l["y"][p - 1]) * f
        if x == 0 and y == 0:
            continue
        out.append(((t - a_ms) / 1000, c["speed"][k], c["throttle"][k], c["brake"][k], c["gear"][k], c["rpm"][k],
                    c["drs"][k], x, y))
    if len(out) < 10 or max(b[0] - a[0] for a, b in zip(out, out[1:])) > MAX_GAP:
        return None
    if out[0][0] > MAX_GAP or (strict and lap_time - out[-1][0] > MAX_GAP):
        return None
    return out


# ------------------------------------------------------------------------------------------------ the reference line
class Line:
    """A lap's path as a polyline with distance (metres; location units are 0.1 m). Points project onto it."""

    def __init__(self, pts):
        self.x = [p[0] for p in pts]
        self.y = [p[1] for p in pts]
        self.d = [0.0]
        for k in range(1, len(pts)):
            self.d.append(self.d[-1] + math.hypot(self.x[k] - self.x[k - 1], self.y[k] - self.y[k - 1]) / 10)
        self.length = self.d[-1]

    def _seg(self, k, x, y):
        ax, ay, bx, by = self.x[k], self.y[k], self.x[k + 1], self.y[k + 1]
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        f = 0.0 if ll == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / ll))
        px, py = ax + f * dx, ay + f * dy
        return (x - px) ** 2 + (y - py) ** 2, self.d[k] + f * (self.d[k + 1] - self.d[k])

    def project(self, xs, ys):
        """Distances along the line for a lap's points, searching forward from the last match (laps don't go
        backwards). Returns [(distance, offset from the line in m)]."""
        n = len(self.x) - 1
        out, k = [], None
        for x, y in zip(xs, ys):
            rng = range(n) if k is None else [(k + s) % n for s in range(-3, 12)]
            best = min(((self._seg(s, x, y), s) for s in rng), key=lambda t: t[0][0])
            (dist2, d), k = best
            out.append((d, math.sqrt(dist2) / 10))
        return out

    def at(self, d):
        """x, y at distance d."""
        k = max(0, min(len(self.d) - 2, bisect.bisect_right(self.d, d) - 1))
        f = 0 if self.d[k + 1] == self.d[k] else (d - self.d[k]) / (self.d[k + 1] - self.d[k])
        return self.x[k] + f * (self.x[k + 1] - self.x[k]), self.y[k] + f * (self.y[k + 1] - self.y[k])


def _on_line(line, samples):
    """Lap samples with their distance along the reference line, unwrapped so it rises through the lap."""
    proj = line.project([s[7] for s in samples], [s[8] for s in samples])
    out, prev, lapped = [], None, 0.0
    for s, (d, off) in zip(samples, proj):
        if prev is not None and d + lapped < prev - line.length / 2:
            lapped += line.length          # crossed the start line again (the lap's last sample)
        dd = d + lapped
        if prev is not None and dd < prev:
            dd = prev                       # a little jitter backwards
        out.append((dd,) + s + (off,))
        prev = dd
    # the first samples of a lap can sit just before the line (end of the previous lap)
    if out and out[0][0] > line.length * 0.9:
        out = [((o[0] - line.length) if o[0] > line.length * 0.5 else o[0],) + o[1:] for o in out]
    return out


def find_corners(dist, speed, brake, throttle, official=(), length=None):
    """Corners on the reference lap: local speed minima at least MIN_DROP below the fastest point since the last
    corner, merged when closer than MERGE m. official: [(number, distance)] to name them. Returns dicts."""
    n = len(speed)
    sm = [sum(speed[max(0, k - 1):k + 2]) / len(speed[max(0, k - 1):k + 2]) for k in range(n)]
    minima = [k for k in range(1, n - 1) if sm[k] <= sm[k - 1] and sm[k] < sm[k + 1]]
    picked, last_max_from = [], 0
    for k in minima:
        before = max(sm[last_max_from:k + 1] or [sm[k]])
        slowed = any(brake[j] or throttle[j] < 80 for j in range(max(0, k - 8), k + 1))
        if before - sm[k] >= MIN_DROP and slowed:
            if picked and dist[k] - dist[picked[-1]] < MERGE:
                if speed[k] < speed[picked[-1]]:
                    picked[-1] = k
            else:
                picked.append(k)
            last_max_from = k
    length = length or dist[-1]
    corners = []
    owner = {}       # each official corner names the detected corner nearest to it, if close enough
    for num, d in official:
        near = min(picked, key=lambda k: abs(dist[k] - d), default=None)
        if near is not None and abs(dist[near] - d) <= LABEL_WITHIN:
            owner.setdefault(near, []).append(num)
    for idx, k in enumerate(picked):
        names = owner.get(k, [])
        if not names:
            label = f"C{idx + 1}"
        else:
            lo, hi = min(names), max(names)
            label = f"T{lo}" if lo == hi else f"T{lo}-{hi}"
        corners.append({"corner": idx + 1, "label": label, "apex": dist[k], "min_speed": speed[k],
                        "kind": "slow" if speed[k] < SLOW else "fast" if speed[k] >= FAST else "medium"})
    for i, c in enumerate(corners):      # each corner owns the track from halfway to the previous apex to halfway to the next
        prev_apex = corners[i - 1]["apex"] - (length if i == 0 else 0)
        next_apex = corners[(i + 1) % len(corners)]["apex"] + (length if i == len(corners) - 1 else 0)
        c["start"] = (prev_apex + c["apex"]) / 2
        c["end"] = (c["apex"] + next_apex) / 2
        c["prev_apex"] = prev_apex
    return corners


def find_straights(pts, corners, length):
    """Full-throttle stretches of the reference lap at least STRAIGHT_MIN long (one crossing the line is joined up).
    Named after the corner before them."""
    runs, cur = [], None
    for p in pts:
        flat = p[3] >= FULL_THROTTLE and not p[4]
        if flat and cur is None:
            cur = [p[0], p[0], p[2]]
        elif flat:
            cur[1], cur[2] = p[0], max(cur[2], p[2])
        elif cur is not None:
            runs.append(cur)
            cur = None
    if cur is not None:
        runs.append(cur)
    if len(runs) > 1 and runs[0][0] <= pts[0][0] + 30 and runs[-1][1] >= length - 60:   # the pit straight
        last = runs.pop()
        runs[0] = [last[0], runs[0][1] + length, max(last[2], runs[0][2])]
        runs.append(runs.pop(0))
    out = []
    for a, b, top in runs:
        if b - a < STRAIGHT_MIN:
            continue
        before = [c for c in corners if c["apex"] <= a] or corners[-1:]
        label = f"after {before[-1]['label']}" if before else "straight"
        out.append({"straight": len(out) + 1, "label": label, "start": a, "end": b, "top_speed": top})
    return out


def st_wraps(straights, length):
    return any(st["end"] > length for st in straights)


def measure_straights(pts, straights):
    """Each straight on one lap: entry speed, speed gained over the first ACCEL_OVER m, top speed and where, speed
    at the end of full throttle, and clipping - speed lost (or not gained) while still flat out."""
    d = [p[0] for p in pts]
    out = []
    for st in straights:
        idx = [k for k in range(len(d)) if st["start"] - 30 <= d[k] <= st["end"] + 80]
        if len(idx) < 4:
            continue
        # the longest unbroken full-throttle run inside the straight
        runs, cur = [], []
        for k in idx:
            if pts[k][3] >= FULL_THROTTLE and not pts[k][4]:
                cur.append(k)
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        if not runs:
            continue
        run = max(runs, key=lambda r: d[r[-1]] - d[r[0]])
        if d[run[-1]] - d[run[0]] < STRAIGHT_MIN / 2:
            continue
        spd = [pts[k][2] for k in run]
        top_i = max(range(len(run)), key=lambda i: spd[i])
        top = spd[top_i]
        flat_from = next(i for i in range(len(run)) if spd[i] >= top - PLATEAU)
        entry = _interp(d, [p[2] for p in pts], st["start"])
        out.append({"straight": st["straight"], "entry_speed": round(entry),
                    "accel": round(_interp(d, [p[2] for p in pts], st["start"] + ACCEL_OVER) - entry, 1),
                    "top_speed": top, "top_at": round(d[run[top_i]] - st["start"], 1), "end_speed": spd[-1],
                    "clip_drop": top - spd[-1], "clip_dist": round(d[run[-1]] - d[run[top_i]], 1),
                    "plateau": round(d[run[-1]] - d[run[flat_from]], 1),
                    "drs": round(sum(1 for k in run if (pts[k][7] or 0) >= 10) / len(run), 2)})
    return out


# ------------------------------------------------------------------------------------------------ measuring a lap
def _interp(xs, ys, x):
    k = bisect.bisect_left(xs, x)
    if k <= 0:
        return ys[0]
    if k >= len(xs):
        return ys[-1]
    f = 0 if xs[k] == xs[k - 1] else (x - xs[k - 1]) / (xs[k] - xs[k - 1])
    return ys[k - 1] + f * (ys[k] - ys[k - 1])


def measure_lap(pts, corners, length, lap_time):
    """pts: _on_line output for one lap. Returns (lap metrics, [corner metrics]).

    The first and last corners straddle the start line: their time is put together from the end and the start of
    this lap, so the corners add up to the lap time."""
    d = [p[0] for p in pts]
    t = [p[1] for p in pts]
    spd = [p[2] for p in pts]
    thr = [p[3] for p in pts]
    brk = [p[4] for p in pts]
    gear = [p[5] for p in pts]
    out = []
    for c in corners:
        lo, hi = max(c["apex"] - APEX_WINDOW, c["start"]), min(c["apex"] + APEX_WINDOW, c["end"])
        idx = [k for k in range(len(d)) if lo <= d[k] <= hi]
        if not idx:
            continue
        # this lap's apex: the speed dip nearest the reference apex (not the end of the previous corner's exit)
        dips = [k for k in idx if 0 < k < len(d) - 1 and spd[k] <= spd[k - 1] and spd[k] <= spd[k + 1]]
        a = min(dips, key=lambda k: abs(d[k] - c["apex"])) if dips else min(idx, key=lambda k: spd[k])
        # braking: the last brake-off -> brake-on change since the previous corner's apex
        zone = [k for k in range(len(d)) if c["prev_apex"] + 20 < d[k] <= d[a]]
        brake_d = lift_d = None
        peak = None
        for k in zone:
            if k > 0 and brk[k] and not brk[k - 1]:
                brake_d = (d[k - 1] + d[k]) / 2
            if k > 0 and thr[k] < 50 <= thr[k - 1]:
                lift_d = (d[k - 1] + d[k]) / 2
        if brake_d is not None:
            for k in zone:
                if d[k] >= brake_d and k > 0 and t[k] > t[k - 1]:
                    g = (spd[k - 1] - spd[k]) / 3.6 / (t[k] - t[k - 1]) / 9.81
                    peak = g if peak is None or g > peak else peak
        entry = max((spd[k] for k in zone), default=None)
        thr_d = next(((d[k - 1] + d[k]) / 2 for k in range(a + 1, len(d)) if thr[k] >= FULL_THROTTLE and d[k] <= c["end"] + 200), None)
        t_in = _interp(d, t, c["start"]) if c["start"] >= 0 else _interp(d, t, c["start"] + length) - lap_time
        t_out = _interp(d, t, c["end"]) if c["end"] <= length else lap_time + _interp(d, t, c["end"] - length)
        out.append({"corner": c["corner"], "seg_time": round(t_out - t_in, 3) if t_in is not None and t_out is not None else None,
                    "entry_speed": entry, "min_speed": spd[a], "apex_dist": round(d[a] - c["apex"], 1),
                    "brake_dist": round(c["apex"] - brake_d, 1) if brake_d is not None else None,
                    "lift_dist": round(c["apex"] - lift_d, 1) if lift_d is not None else None,
                    "peak_decel": round(peak, 2) if peak is not None else None,
                    "throttle_dist": round(thr_d - c["apex"], 1) if thr_d is not None else None,
                    "exit_speed": round(_interp(d, spd, c["apex"] + EXIT_AT)), "gear": gear[a]})
    # lap: full throttle and brake by distance, lift-and-coast stretches that end on the brakes
    full = on_brake = 0.0
    for k in range(1, len(d)):
        step = d[k] - d[k - 1]
        full += step if thr[k] >= FULL_THROTTLE else 0
        on_brake += step if brk[k] else 0
    lico_t, lico_n, run = 0.0, 0, None
    for k in range(len(d)):
        coasting = thr[k] < 10 and not brk[k] and spd[k] >= LICO_SPEED
        if coasting and run is None:
            run = k
        elif not coasting and run is not None:
            dur = t[k] - t[run]
            if brk[k] and dur >= LICO_MIN:
                lico_t += dur
                lico_n += 1
            run = None
    span = (d[-1] - d[0]) or 1
    return ({"top_speed": max(spd), "full_throttle": round(100 * full / span, 1), "brake_pct": round(100 * on_brake / span, 1),
             "lico_time": round(lico_t, 2), "lico_count": lico_n, "gear_changes": sum(1 for a, b in zip(gear, gear[1:]) if a != b),
             "samples": len(pts), "off_line": round(sum(p[-1] for p in pts) / len(pts), 1)}, out)


# ------------------------------------------------------------------------------------------------ one session
def _official_corners(session_key, db):
    """The circuit's numbered corners (x, y) from the circuit data OpenF1 links to, if it can be had."""
    from .analysis import circuit_info
    row = db.execute("SELECT meeting_key, year FROM sessions WHERE session_key = ?", (session_key,)).fetchone()
    if not row or not row[0]:
        return []
    try:
        m = (api.get("meetings", meeting_key=row[0]) or [{}])[0]
    except api.APIError:
        return []
    info = circuit_info(m.get("circuit_info_url"))
    return [(c["number"], c["x"], c["y"]) for c in (info or {}).get("corners") or []]


def analyse_session(db, session_key, raw, official=None, progress=lambda msg: None):
    """Measure every lap of a session from its raw telemetry ({driver: columns}) and write the tel_ tables.
    official: [(number, x, y)] corner positions (None: look them up). Returns the number of laps measured."""
    laps = db.execute("""SELECT driver_number, lap_number, date_start, lap_time, pit_out, pit_in FROM laps
                         WHERE session_key = ? AND lap_time IS NOT NULL AND date_start IS NOT NULL
                         ORDER BY lap_time""", (session_key,)).fetchall()
    # the reference: the fastest lap with clean telemetry
    ref = None
    for n, lap, start, lt, po, pi in laps:
        if po or pi or n not in raw:
            continue
        s = lap_samples(raw[n], _ts(start), lt)
        if s:
            ref = (n, lap, lt, s)
            break
    for tbl in ("tel_sessions", "tel_corners", "tel_laps", "tel_corner_laps", "tel_straights", "tel_straight_laps"):
        db.execute(f"DELETE FROM {tbl} WHERE session_key = ?", (session_key,))
    if not ref:
        return 0
    n_ref, lap_ref, lt_ref, s_ref = ref
    path = [(s[7], s[8]) for s in s_ref]
    if math.hypot(path[0][0] - path[-1][0], path[0][1] - path[-1][1]) < 1500:      # close the loop (< 150 m)
        path.append(path[0])
    line = Line(path)
    pts = _on_line(line, s_ref)
    if official is None:
        official = _official_corners(session_key, db)
    named = []
    for num, x, y in official:
        d, off = line.project([x], [y])[0]
        if off < 60:
            named.append((num, d))
    corners = find_corners([p[0] for p in pts], [p[2] for p in pts], [p[4] for p in pts], [p[3] for p in pts], named, line.length)
    step = max(1, len(line.x) // 300)
    outline = [[round(line.x[k]), round(line.y[k])] for k in range(0, len(line.x), step)]
    db.execute("INSERT INTO tel_sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
               (session_key, n_ref, lap_ref, round(line.length, 1), json.dumps(outline), len(raw), time.time()))
    for c in corners:
        x, y = line.at(c["apex"])
        db.execute("INSERT INTO tel_corners VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (session_key, c["corner"], c["label"], round(c["apex"], 1), round(c["start"], 1), round(c["end"], 1),
                    round(x), round(y), c["min_speed"], c["kind"]))
    straights = find_straights(pts, corners, line.length)
    for st in straights:
        db.execute("INSERT INTO tel_straights VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (session_key, st["straight"], st["label"], round(st["start"], 1), round(st["end"], 1),
                    round(st["end"] - st["start"], 1), st["top_speed"], int(st["end"] > line.length)))
    measured = 0
    for i, (n, lap, start, lt, po, pi) in enumerate(laps):
        if i % 10 == 0:
            progress(f"measuring corners, lap {i + 1}/{len(laps)}")
        if n not in raw:
            continue
        s = lap_samples(raw[n], _ts(start), lt)
        if not s:
            continue
        on = _on_line(line, s)
        # the pit straight crosses the line: read on into the next few seconds so it is one continuous stretch
        ext = lap_samples(raw[n], _ts(start), lt + 12, strict=False) if st_wraps(straights, line.length) else None
        on_ext = _on_line(line, ext) if ext else on
        sts = measure_straights(on_ext, straights) if not (po or pi) else []
        db.executemany("INSERT INTO tel_straight_laps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       [(session_key, n, lap, m["straight"], m["entry_speed"], m["accel"], m["top_speed"], m["top_at"],
                         m["end_speed"], m["clip_drop"], m["clip_dist"], m["plateau"], m["drs"]) for m in sts])
        if on[-1][0] - on[0][0] < line.length * 0.9:     # not a full lap on the line (pit lane, a cut)
            continue
        lm, cms = measure_lap(on, corners, line.length, lt)
        db.execute("INSERT INTO tel_laps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (session_key, n, lap, lm["top_speed"], lm["full_throttle"], lm["brake_pct"], lm["lico_time"],
                    lm["lico_count"], lm["gear_changes"], lm["samples"], lm["off_line"]))
        if not (po or pi):
            db.executemany("INSERT INTO tel_corner_laps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           [(session_key, n, lap, c["corner"], c["seg_time"], c["entry_speed"], c["min_speed"], c["apex_dist"],
                             c["brake_dist"], c["lift_dist"], c["peak_decel"], c["throttle_dist"], c["exit_speed"], c["gear"])
                            for c in cms])
        measured += 1
    return measured


def compile_session(db, session, data_dir, progress=lambda msg: None):
    """Fetch (or reuse) and analyse one session's telemetry. session: a dataset sessions row (dict-like)."""
    drivers = [r[0] for r in db.execute("SELECT driver_number FROM drivers WHERE session_key = ?", (session["session_key"],))]
    raw = fetch_session(session, drivers, data_dir, progress)
    progress("measuring corners")
    return analyse_session(db, session["session_key"], {n: c for n, c in raw.items() if c["car"]["t"]}, progress=progress)
