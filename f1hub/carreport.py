"""Cars and circuits: phase 5 of docs/roadmap.md, from the per-corner telemetry (f1hub/telemetry.py).

- Car characteristics, per qualifying session: each team's straight-line speed against its speed through slow,
  medium and fast corners, relative to the field (km/h) - the drag / downforce trade-off it ran at that circuit -
  plus traction (speed gained leaving a corner) and energy clipping (speed lost, or not gained, at full throttle).
- The same across the season, with 95% intervals.
- Circuits grouped by layout (k-means on what the reference lap does: share at full throttle, corners per km of
  each type, braking zones, corner and top speed, longest straight), and each team's qualifying pace in each group:
  which cars suit which circuits.

Qualifying only: everyone is flat out on low fuel, so the differences are the cars (and drivers), not strategy.
A team's figure is the mean of its two drivers' typical laps (their three quickest).
"""
import math
from statistics import median

from .cornerreport import _rep_laps, sessions as tel_sessions
from .seasonreport import _Teams, open_dataset, quali_pace

KINDS = ("slow", "medium", "fast")
FEATURES = (("full_throttle", "Full throttle", "% of the lap"), ("slow_km", "Slow corners", "per km"),
            ("medium_km", "Medium corners", "per km"), ("fast_km", "Fast corners", "per km"),
            ("brakes_km", "Braking zones", "per km"), ("corner_speed", "Corner speed", "km/h, average minimum"),
            ("top_speed", "Top speed", "km/h"), ("longest", "Longest straight", "m"))
CLUSTERS = 3


def _r(v, n=1):
    return None if v is None else round(v, n)


def _mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def _mean_se(v):
    v = [x for x in v if x is not None]
    if not v:
        return None
    m = sum(v) / len(v)
    se = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1) / len(v)) if len(v) > 1 else None
    return {"est": _r(m, 2), "se": _r(se, 2), "n": len(v)}


# ------------------------------------------------------------------------------------------------ one session
def _session_cars(db, key, teams):
    """{team: {straight, slow, medium, fast, accel, clip_drop, plateau}} as differences from the field (km/h, m),
    and the straights with every team's top speed and clipping."""
    rep = _rep_laps(db, key, True)
    team_of = {n: teams.of.get((key, n)) for n in rep}
    corners = {r["corner"]: r["kind"] for r in db.execute("SELECT corner, kind FROM tel_corners WHERE session_key = ?", (key,))}
    straights = [dict(r) for r in db.execute("SELECT * FROM tel_straights WHERE session_key = ? AND wraps = 0 ORDER BY straight",
                                             (key,))]
    # each driver's median per corner / straight over their representative laps
    per_c, per_s = {}, {}
    for r in db.execute("SELECT driver_number, lap_number, corner, min_speed FROM tel_corner_laps WHERE session_key = ?", (key,)):
        if r["lap_number"] in rep.get(r["driver_number"], ()):
            per_c.setdefault((r["driver_number"], r["corner"]), []).append(r["min_speed"])
    for r in db.execute("SELECT * FROM tel_straight_laps WHERE session_key = ?", (key,)):
        if r["lap_number"] in rep.get(r["driver_number"], ()):
            per_s.setdefault((r["driver_number"], r["straight"]), []).append(r)
    drv_c = {k: median(v) for k, v in per_c.items()}
    drv_s = {k: {m: median([x[m] for x in v]) for m in ("top_speed", "accel", "clip_drop", "plateau")} for k, v in per_s.items()}

    def team_values(by_driver, item, metric=None):
        out = {}
        for (n, it), v in by_driver.items():
            if it == item and team_of.get(n):
                out.setdefault(team_of[n], []).append(v if metric is None else v[metric])
        return {t: sum(v) / len(v) for t, v in out.items()}

    rel = {}            # team -> metric -> [differences from the field, one per corner / straight]
    for c, kind in corners.items():
        vals = team_values(drv_c, c)
        if len(vals) < 5:
            continue
        field = median(vals.values())
        for t, v in vals.items():
            rel.setdefault(t, {}).setdefault(kind, []).append(v - field)
    st_out = []
    for st in straights:
        tops = team_values(drv_s, st["straight"], "top_speed")
        if len(tops) < 5:
            continue
        for m, name in (("top_speed", "straight"), ("accel", "accel")):
            vals = team_values(drv_s, st["straight"], m)
            field = median(vals.values())
            for t, v in vals.items():
                rel.setdefault(t, {}).setdefault(name, []).append(v - field)
        clip = team_values(drv_s, st["straight"], "clip_drop")
        plat = team_values(drv_s, st["straight"], "plateau")
        st_out.append(dict(st, teams={t: {"top": _r(tops[t]), "clip": _r(clip.get(t)), "plateau": _r(plat.get(t), 0)} for t in tops}))
    # clipping on the session's longest straight (where it shows most)
    longest = max(st_out, key=lambda s: s["length"], default=None)
    cars = {}
    for t, m in rel.items():
        cars[t] = {k: _r(_mean(m.get(k, [])), 1) for k in ("straight", "accel") + KINDS}
        cars[t]["corners"] = _r(_mean([x for k in KINDS for x in m.get(k, [])]), 1)
        if longest and t in longest["teams"]:
            cars[t].update(clip=longest["teams"][t]["clip"], plateau=longest["teams"][t]["plateau"])
    return cars, st_out, longest


def _features(db, key):
    """What the session's reference lap says about the circuit's layout."""
    info = db.execute("SELECT * FROM tel_sessions WHERE session_key = ?", (key,)).fetchone()
    km = info["length"] / 1000
    corners = db.execute("SELECT * FROM tel_corners WHERE session_key = ?", (key,)).fetchall()
    lap = db.execute("SELECT * FROM tel_laps WHERE session_key = ? AND driver_number = ? AND lap_number = ?",
                     (key, info["ref_driver"], info["ref_lap"])).fetchone()
    braked = db.execute("SELECT COUNT(*) FROM tel_corner_laps WHERE session_key = ? AND driver_number = ? AND lap_number = ? "
                        "AND brake_dist IS NOT NULL", (key, info["ref_driver"], info["ref_lap"])).fetchone()[0]
    longest = db.execute("SELECT MAX(length) FROM tel_straights WHERE session_key = ?", (key,)).fetchone()[0]
    if not lap or not corners:
        return None
    count = {k: sum(1 for c in corners if c["kind"] == k) for k in KINDS}
    return {"full_throttle": lap["full_throttle"], "slow_km": _r(count["slow"] / km, 2), "medium_km": _r(count["medium"] / km, 2),
            "fast_km": _r(count["fast"] / km, 2), "brakes_km": _r(braked / km, 2),
            "corner_speed": _r(sum(c["min_speed"] for c in corners) / len(corners), 0), "top_speed": lap["top_speed"],
            "longest": _r(longest or 0, 0), "length": round(info["length"]), "corners": len(corners)}


# ------------------------------------------------------------------------------------------------ circuit groups
def _standardise(rows, keys):
    cols = {k: [r[k] for r in rows] for k in keys}
    stats = {k: (sum(v) / len(v), math.sqrt(sum((x - sum(v) / len(v)) ** 2 for x in v) / len(v)) or 1.0) for k, v in cols.items()}
    return [[(r[k] - stats[k][0]) / stats[k][1] for k in keys] for r in rows]


def kmeans(points, k, iters=50):
    """Deterministic k-means: the first centre is the point farthest from the mean, each next one the point
    farthest from the centres so far. Returns (labels, centres)."""
    if len(points) <= k:
        return list(range(len(points))), [p[:] for p in points]
    dist = lambda a, b: sum((x - y) ** 2 for x, y in zip(a, b))
    mean = [sum(c) / len(points) for c in zip(*points)]
    centres = [max(points, key=lambda p: dist(p, mean))]
    while len(centres) < k:
        centres.append(max(points, key=lambda p: min(dist(p, c) for c in centres)))
    labels = None
    for _ in range(iters):
        new = [min(range(k), key=lambda j: dist(p, centres[j])) for p in points]
        if new == labels:
            break
        labels = new
        for j in range(k):
            members = [p for p, l in zip(points, labels) if l == j]
            if members:
                centres[j] = [sum(c) / len(members) for c in zip(*members)]
    return labels, centres


def pca2(points):
    """The first two principal components (power iteration) and each point's coordinates on them."""
    n, d = len(points), len(points[0])
    mean = [sum(c) / n for c in zip(*points)]
    x = [[v - m for v, m in zip(p, mean)] for p in points]
    cov = [[sum(r[i] * r[j] for r in x) / n for j in range(d)] for i in range(d)]
    comps, share = [], []
    total = sum(cov[i][i] for i in range(d)) or 1.0
    for _ in range(2):
        v = [1.0 / math.sqrt(d) + 0.01 * i for i in range(d)]
        for _ in range(200):
            w = [sum(cov[i][j] * v[j] for j in range(d)) for i in range(d)]
            norm = math.sqrt(sum(a * a for a in w)) or 1.0
            v = [a / norm for a in w]
        lam = sum(v[i] * sum(cov[i][j] * v[j] for j in range(d)) for i in range(d))
        comps.append(v)
        share.append(lam / total)
        cov = [[cov[i][j] - lam * v[i] * v[j] for j in range(d)] for i in range(d)]
    coords = [[sum(a * b for a, b in zip(r, c)) for c in comps] for r in x]
    return coords, comps, share


ARCHETYPES = {          # each group is named after the profile its centre fits best (standardised features)
    "Power": {"full_throttle": 1, "longest": 1, "top_speed": 1},
    "High-speed corners": {"corner_speed": 1, "fast_km": 1, "medium_km": 0.5},
    "Slow and twisty": {"slow_km": 1, "brakes_km": 1, "corner_speed": -1},
}


def _name_groups(centres, keys):
    """Name the groups Power / High-speed corners / Slow and twisty: the one-to-one assignment whose centres fit
    those profiles best overall."""
    from itertools import permutations
    ix = {k: i for i, k in enumerate(keys)}
    names = list(ARCHETYPES)
    fit = lambda j, name: sum(w * centres[j][ix[k]] for k, w in ARCHETYPES[name].items())
    best = max(permutations(names, len(centres)), key=lambda p: sum(fit(j, n) for j, n in enumerate(p)))
    return dict(enumerate(best))


def circuits(db, quali, teams, upto):
    rows = []
    for s in quali:
        f = _features(db, s["session_key"])
        if f:
            rows.append(dict(f, round=s["round"], circuit=s["circuit"], name=s["name"]))
    if len(rows) < CLUSTERS + 1:
        return {"circuits": rows, "groups": [], "suits": [], "features": FEATURES}
    keys = [k for k, _, _ in FEATURES]
    z = _standardise(rows, keys)
    labels, centres = kmeans(z, CLUSTERS)
    names = _name_groups(centres, keys)
    coords, comps, share = pca2(z)
    for r, l, c in zip(rows, labels, coords):
        r.update(group=names[l], x=_r(c[0], 3), y=_r(c[1], 3))
    groups = [{"name": names[j], "circuits": [r["circuit"] for r in rows if r["group"] == names[j]],
               "profile": {k: _r(centres[j][i], 2) for i, k in enumerate(keys)}} for j in range(len(centres))]
    loadings = [{k: _r(c[i], 2) for i, k in enumerate(keys)} for c in comps]
    # which cars suit which circuits: each team's qualifying gap to pole, by group
    qp = quali_pace(db, upto, teams)
    group_of = {r["round"]: r["group"] for r in rows}
    suits = []
    for t in qp["teams"]:
        by = {}
        for rnd, pct in t["points"]:
            if rnd in group_of:
                by.setdefault(group_of[rnd], []).append(pct)
        if not by:
            continue
        ft = [(next(r["full_throttle"] for r in rows if r["round"] == rnd), pct) for rnd, pct in t["points"] if rnd in group_of]
        suits.append({"team": t["team"], "colour": t["colour"], "groups": {g: _mean_se(v) for g, v in by.items()},
                      "r_full_throttle": _corr(ft)})
    return {"circuits": rows, "groups": groups, "suits": suits, "features": FEATURES, "pca": {"loadings": loadings,
            "share": [_r(x, 3) for x in share]}}


def _corr(xy):
    if len(xy) < 5:
        return None
    mx, my = sum(x for x, _ in xy) / len(xy), sum(y for _, y in xy) / len(xy)
    sxy = sum((x - mx) * (y - my) for x, y in xy)
    sxx, syy = sum((x - mx) ** 2 for x, _ in xy), sum((y - my) ** 2 for _, y in xy)
    return _r(sxy / math.sqrt(sxx * syy), 2) if sxx and syy else None


# ------------------------------------------------------------------------------------------------ the report
def report(year, key=None, upto=None):
    """Car characteristics for one qualifying session (default the latest measured), across the season, and the
    circuits grouped by layout."""
    db = open_dataset(year)
    if db is None:
        return {"year": year, "exists": False}
    try:
        quali = [s for s in tel_sessions(db) if s["session_name"] == "Qualifying" and (upto is None or s["round"] <= upto)]
        base = {"year": year, "exists": True, "sessions": quali}
        if not quali:
            return base
        last = max(s["round"] for s in quali)
        teams = _Teams(db, last)
        s = next((x for x in quali if x["session_key"] == key), quali[-1])
        season, cars_now, straights, longest = {}, None, None, None
        for x in quali:
            cars, sts, lg = _session_cars(db, x["session_key"], teams)
            for t, v in cars.items():
                season.setdefault(t, []).append(dict(v, round=x["round"], circuit=x["circuit"]))
            if x is s:
                cars_now, straights, longest = cars, sts, lg
        colour = teams.colour
        session_cars = sorted(({"team": t, "colour": colour.get(t), **v} for t, v in cars_now.items()),
                              key=lambda r: -(r["straight"] or 0))
        season_cars = []
        for t, rounds in season.items():
            season_cars.append({"team": t, "colour": colour.get(t), "rounds": rounds,
                                **{k: _mean_se([r.get(k) for r in rounds]) for k in ("straight", "corners", "accel", "clip", "plateau") + KINDS}})
        season_cars.sort(key=lambda r: -((r["straight"] or {}).get("est") or 0))
        return dict(base, session=s, cars=session_cars, straights=straights, longest=longest and longest["straight"],
                    season=season_cars, circuits=circuits(db, quali, teams, upto if upto is not None else last),
                    team_colours=colour)
    finally:
        db.close()
