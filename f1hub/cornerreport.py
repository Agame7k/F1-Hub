"""Corners: what the per-corner telemetry in a season dataset says (f1hub/telemetry.py measures it).

- one session: the circuit's corners, every driver's typical corner (braking point, minimum speed, throttle,
  time through it), who is quickest where, time lost in slow, medium and fast corners, and two drivers side by
  side; in races also consistency, big moments and lift-and-coast;
- the season: team-mates' driving styles compared, corner type by corner type.

A driver's typical corner is the median over their representative laps: in qualifying their three quickest
measured laps, in a race their clean laps (dataset `clean` flag).
"""
import json
import math
import sqlite3
from statistics import median, pstdev

from .seasonreport import _Teams, open_dataset

QUALI = ("Qualifying", "Sprint Qualifying")
TOP_QUALI_LAPS = 3
MOMENT = 0.5             # s slower than the driver's own median through a corner: a big moment (race laps)
KINDS = ("slow", "medium", "fast")
METRICS = ("seg_time", "entry_speed", "min_speed", "apex_dist", "brake_dist", "lift_dist", "peak_decel",
           "throttle_dist", "exit_speed", "gear")


def _r(v, n=2):
    return None if v is None else round(v, n)


def _med(vals):
    vals = [v for v in vals if v is not None]
    return median(vals) if vals else None


def has_telemetry(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name = 'tel_sessions'").fetchone() is not None


def sessions(db):
    """Sessions with telemetry, in order."""
    if not has_telemetry(db):
        return []
    return [dict(r) for r in db.execute("""SELECT s.session_key, s.round, s.meeting_name AS name, s.circuit, s.session_name,
                                           s.session_type FROM tel_sessions t JOIN sessions s USING (session_key)
                                           ORDER BY s.date_start""")]


def _rep_laps(db, key, quali):
    """{driver: [lap numbers]} - the laps a driver's typical corner is taken from."""
    out = {}
    if quali:
        for r in db.execute("""SELECT t.driver_number, t.lap_number, l.lap_time FROM tel_laps t JOIN laps l
                               USING (session_key, driver_number, lap_number)
                               WHERE t.session_key = ? AND l.pit_in = 0 AND l.pit_out = 0 AND l.deleted = 0
                               AND EXISTS (SELECT 1 FROM tel_corner_laps c WHERE c.session_key = t.session_key
                                           AND c.driver_number = t.driver_number AND c.lap_number = t.lap_number)
                               ORDER BY l.lap_time""", (key,)):
            laps = out.setdefault(r["driver_number"], [])
            if len(laps) < TOP_QUALI_LAPS and (not laps or r["lap_time"] <= 1.03 * out[r["driver_number"]][0][1]):
                laps.append((r["lap_number"], r["lap_time"]))
        return {n: [l for l, _ in v] for n, v in out.items()}
    for r in db.execute("""SELECT t.driver_number, t.lap_number FROM tel_laps t JOIN laps l
                           USING (session_key, driver_number, lap_number) WHERE t.session_key = ? AND l.clean = 1""", (key,)):
        out.setdefault(r["driver_number"], []).append(r["lap_number"])
    return out


def _profiles(db, key, rep):
    """{driver: {corner: {metric: median, 'sd_seg': ..., 'sd_min': ..., 'n': laps, 'best_seg': ...}}}"""
    rows = {}
    for r in db.execute("SELECT * FROM tel_corner_laps WHERE session_key = ?", (key,)):
        if r["lap_number"] in rep.get(r["driver_number"], ()):
            rows.setdefault(r["driver_number"], {}).setdefault(r["corner"], []).append(r)
    out = {}
    for n, by_c in rows.items():
        out[n] = {}
        for c, rs in by_c.items():
            seg = [x["seg_time"] for x in rs if x["seg_time"] is not None]
            mins = [x["min_speed"] for x in rs]
            p = {m: _med([x[m] for x in rs]) for m in METRICS}
            p.update(n=len(rs), best_seg=min(seg) if seg else None,
                     sd_seg=pstdev(seg) if len(seg) > 2 else None, sd_min=pstdev(mins) if len(mins) > 2 else None,
                     braked=sum(1 for x in rs if x["brake_dist"] is not None) / len(rs))
            out[n][c] = p
    return out


def session_report(year, key=None, a=None, b=None, upto=None):
    """One session's corners. key: a session with telemetry (default the latest). a, b: driver numbers to compare
    (default: the two quickest). upto: only rounds up to this one (spoiler mode)."""
    db = open_dataset(year)
    if db is None:
        return {"year": year, "exists": False}
    try:
        tel = [x for x in sessions(db) if upto is None or x["round"] <= upto]
        base = {"year": year, "exists": True, "sessions": tel}
        if not tel:
            return base
        s = next((x for x in tel if x["session_key"] == key), tel[-1])
        key = s["session_key"]
        quali = s["session_name"] in QUALI
        info = db.execute("SELECT * FROM tel_sessions WHERE session_key = ?", (key,)).fetchone()
        corners = [dict(r) for r in db.execute("SELECT * FROM tel_corners WHERE session_key = ? ORDER BY corner", (key,))]
        teams = _Teams(db, s["round"])
        rep = _rep_laps(db, key, quali)
        prof = _profiles(db, key, rep)
        best_lap = {r["driver_number"]: r["t"] for r in db.execute(
            "SELECT driver_number, MIN(lap_time) AS t FROM laps WHERE session_key = ? GROUP BY 1", (key,))}
        drivers = []
        for n in prof:
            team = teams.of.get((key, n))
            drivers.append({"number": n, "code": teams.code.get((key, n)) or str(n), "team": team,
                            "colour": teams.colour.get(team), "laps": len(rep.get(n, ())), "best": best_lap.get(n)})
        drivers.sort(key=lambda d: d["best"] or 1e9)
        nums = [d["number"] for d in drivers]
        a = a if a in prof else (nums[0] if nums else None)
        b = b if b in prof and b != a else next((x for x in nums if x != a), None)

        # quickest through each corner, and time lost by corner type
        wins, lost = {}, {n: {k: 0.0 for k in KINDS} for n in prof}
        complete = {n for n in prof if all(prof[n].get(c["corner"], {}).get("seg_time") is not None for c in corners)}
        for c in corners:
            segs = {n: prof[n][c["corner"]]["seg_time"] for n in complete}
            if not segs:
                continue
            fast = min(segs.values())
            c["quickest"] = min(segs, key=segs.get)
            c["spread"] = _r(max(segs.values()) - fast, 3)
            wins[c["quickest"]] = wins.get(c["quickest"], 0) + 1
            for n, v in segs.items():
                lost[n][c["kind"]] += v - fast
        code = {d["number"]: d for d in drivers}
        for c in corners:
            if c.get("quickest") is not None:
                c["quickest_code"] = code[c["quickest"]]["code"]
                c["quickest_colour"] = code[c["quickest"]]["colour"]
        by_kind = sorted(({"number": n, "code": code[n]["code"], "team": code[n]["team"], "colour": code[n]["colour"],
                           "wins": wins.get(n, 0), **{k: _r(lost[n][k], 3) for k in KINDS},
                           "total": _r(sum(lost[n].values()), 3)} for n in complete), key=lambda r: r["total"])
        kinds = {k: sum(1 for c in corners if c["kind"] == k) for k in KINDS}

        def side(n):
            return {str(c["corner"]): {m: _r(v, 3 if m == "seg_time" else 1) for m, v in prof[n].get(c["corner"], {}).items()}
                    for c in corners} if n in prof else {}

        out = dict(base, session=s, quali=quali, length=info["length"], outline=json.loads(info["outline"]),
                   corners=corners, kinds=kinds, drivers=drivers, by_kind=by_kind, a=a, b=b,
                   profiles={str(n): side(n) for n in (a, b) if n is not None})
        out["laps"] = _lap_stats(db, key, rep, code, prof, corners) if not quali else None
        out["top_speed"] = [dict(code[r["driver_number"]], top=r["top"]) for r in db.execute(
            "SELECT driver_number, MAX(top_speed) AS top FROM tel_laps WHERE session_key = ? GROUP BY 1 ORDER BY 2 DESC",
            (key,)) if r["driver_number"] in code]
        return out
    finally:
        db.close()


def _lap_stats(db, key, rep, code, prof, corners):
    """Races: per driver, lift-and-coast, consistency and big moments over clean laps."""
    rows = {}
    for r in db.execute("SELECT * FROM tel_laps WHERE session_key = ?", (key,)):
        if r["lap_number"] in rep.get(r["driver_number"], ()):
            rows.setdefault(r["driver_number"], []).append(r)
    moments = {}
    for r in db.execute("SELECT driver_number, lap_number, corner, seg_time FROM tel_corner_laps WHERE session_key = ?", (key,)):
        n = r["driver_number"]
        p = prof.get(n, {}).get(r["corner"])
        if p and r["lap_number"] in rep.get(n, ()) and r["seg_time"] and p["seg_time"] and r["seg_time"] - p["seg_time"] > MOMENT:
            moments[n] = moments.get(n, 0) + 1
    out = []
    for n, rs in rows.items():
        if n not in code or len(rs) < 5:
            continue
        sds = [p["sd_seg"] for p in prof.get(n, {}).values() if p.get("sd_seg") is not None]
        out.append(dict(code[n], clean_laps=len(rs), lico=_r(sum(r["lico_time"] for r in rs) / len(rs), 2),
                        lico_laps=sum(1 for r in rs if r["lico_count"]),
                        full_throttle=_r(sum(r["full_throttle"] for r in rs) / len(rs), 1),
                        consistency=_r(sum(sds) / len(sds), 3) if sds else None, moments=moments.get(n, 0),
                        lico_trend=[[r["lap_number"], r["lico_time"]] for r in sorted(rs, key=lambda r: r["lap_number"])]))
    return sorted(out, key=lambda r: -r["lico"])


# ------------------------------------------------------------------------------------------------ the season
def _mean_se(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    m = sum(vals) / len(vals)
    se = math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) / math.sqrt(len(vals)) if len(vals) > 1 else None
    return {"est": _r(m, 2), "se": _r(se, 2), "n": len(vals)}


def styles(year, upto=None):
    """Team-mates in qualifying across the season: per corner type, how much later one brakes, the minimum speed
    each carries and the time each gains, from their typical corners at every round they both have telemetry.

    a is the team-mate who gains time overall; every figure is in a's favour when positive: brake = metres later,
    min = km/h more at the apex, throttle = metres sooner back to full throttle, time = s quicker per corner."""
    db = open_dataset(year)
    if db is None:
        return {"year": year, "exists": False, "pairs": []}
    try:
        tel = [s for s in sessions(db) if s["session_name"] == "Qualifying" and (upto is None or s["round"] <= upto)]
        last = max((s["round"] for s in tel), default=0)
        teams = _Teams(db, last)
        pairs = {}
        for s in tel:
            key = s["session_key"]
            corners = {r["corner"]: r["kind"] for r in db.execute("SELECT corner, kind FROM tel_corners WHERE session_key = ?", (key,))}
            prof = _profiles(db, key, _rep_laps(db, key, True))
            by_team = {}
            for n in prof:
                team = teams.of.get((key, n))
                if team:
                    by_team.setdefault(team, []).append(n)
            for team, ns in by_team.items():
                if len(ns) != 2:
                    continue
                x, y = sorted(ns, key=lambda n: teams.code.get((key, n)) or "")
                cx, cy = teams.code.get((key, x)), teams.code.get((key, y))
                e = pairs.setdefault((team, cx, cy), {"rounds": 0, **{k: {"brake": [], "min": [], "throttle": [], "time": []} for k in KINDS}})
                e["rounds"] += 1
                for c, kind in corners.items():
                    px, py = prof[x].get(c), prof[y].get(c)
                    if not px or not py:
                        continue
                    d = e[kind]
                    if px["brake_dist"] is not None and py["brake_dist"] is not None:
                        d["brake"].append(px["brake_dist"] - py["brake_dist"])      # + : y brakes later
                    if px["min_speed"] is not None and py["min_speed"] is not None:
                        d["min"].append(py["min_speed"] - px["min_speed"])           # + : y carries more speed
                    if px["throttle_dist"] is not None and py["throttle_dist"] is not None:
                        d["throttle"].append(px["throttle_dist"] - py["throttle_dist"])   # + : y back on throttle sooner
                    if px["seg_time"] is not None and py["seg_time"] is not None:
                        d["time"].append(px["seg_time"] - py["seg_time"])            # + : y quicker
        out = []
        for (team, cx, cy), e in pairs.items():
            if e["rounds"] < 2:
                continue
            # every difference is "+ = cy better"; put the driver who gains time overall first, reading in their favour
            gain = sum(sum(e[k]["time"]) for k in KINDS)
            sign, first, second = (1, cy, cx) if gain >= 0 else (-1, cx, cy)
            out.append({"team": team, "colour": teams.colour.get(team), "a": first, "b": second, "rounds": e["rounds"],
                        "kinds": {k: {m: _mean_se([sign * x for x in v]) for m, v in e[k].items()} for k in KINDS}})
        return {"year": year, "exists": True, "pairs": sorted(out, key=lambda p: p["team"]), "rounds": len(tel)}
    finally:
        db.close()


def report(year, key=None, a=None, b=None, upto=None):
    out = session_report(year, key, a, b, upto)
    if out.get("exists") and out.get("sessions"):
        out["styles"] = styles(year, upto)
    return out


def available(year):
    db = open_dataset(year)
    if db is None:
        return False
    try:
        return bool(sessions(db))
    except sqlite3.DatabaseError:
        return False
    finally:
        db.close()
