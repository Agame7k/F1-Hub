"""Lap-time model: what makes up a race lap time, fitted race by race on the season dataset (f1hub/dataset.py).

For every clean dry lap of a race:

    lap time = driver pace + fuel x lap number + compound offset + compound degradation x tyre age
               + dirty-air cost (within TRAFFIC s of the car ahead) + noise

fitted by least squares. Plain Python: each lap touches only a handful of the ~30 terms, so the normal equations
are built sparsely and solved directly, in milliseconds per race.

Uncertainty: standard errors are cluster-robust by stint (laps on one set of tyres are not independent), and
every estimate is returned as (value, standard error), so a 0.05 s gap that is really noise reads as noise.

What the terms mean, honestly:
- "fuel" is everything that changes steadily through the race: fuel burned and the track rubbering in together.
  Track temperature mostly changes steadily through a race too (rising in the afternoon, falling at twilight), so
  within a race it can't be told apart from fuel and is not a term; its link with degradation is measured across
  races instead (season()["deg_temp"]).
- degradation is linear in tyre age; a tyre "cliff" is a later, steeper part of that line (a change point).
- the dirty-air cost is the average extra time on laps that end within TRAFFIC s of the car ahead. A faster car
  that has just caught the one ahead is also counted, so it is if anything an underestimate.
"""
import math
from statistics import median

DRY = ("SOFT", "MEDIUM", "HARD")
TRAFFIC = 1.5            # s to the car ahead at the end of the lap: in dirty air
MIN_LAPS = 10            # clean dry laps a driver needs to be in the model
MIN_RACE_LAPS = 200      # clean dry laps a race needs to be modelled at all
MIN_COMPOUND_LAPS = 30   # laps on a compound for it to be in the model (fewer are left out)
MIN_COMPOUND_STINTS = 3  # and stints (errors are measured between stints, so one stint can't give one)
TRIM = 3.5               # robust z beyond which a lap is dropped and the model fitted again
CLIFF_MIN = 0.05         # s per lap per lap extra degradation for a cliff
CLIFF_T = 4.0            # and this many standard errors (strict: every tyre age is tried as the break)
CLIFF_FROM = 8           # a break no earlier than this tyre age (younger tyres are still coming in)
CLIFF_AFTER = 20         # laps beyond the break, from at least
CLIFF_CARS = 3           # this many different stints
STEP_T = 3.0             # t-statistic for a step in a team's pace through the season
STEP_SIDE = 3            # rounds on each side of a step, at least


class Singular(ValueError):
    """The terms can't be separated with these laps (for example a race nobody stopped in)."""


def _r(v, n=3):
    return None if v is None else round(v, n)


def _est(v, se, n=3):
    return {"est": _r(v, n), "se": _r(se, n)}


# ------------------------------------------------------------------------------------------------ least squares
def _invert(a):
    """Gauss-Jordan inverse of a symmetric positive (semi)definite matrix; Singular if it isn't invertible."""
    n = len(a)
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    scale = max(abs(a[i][i]) for i in range(n)) or 1.0
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-10 * scale:
            raise Singular(c)
        m[c], m[p] = m[p], m[c]
        piv = m[c][c]
        m[c] = [v / piv for v in m[c]]
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c]
                mr, mc = m[r], m[c]
                m[r] = [x - f * y for x, y in zip(mr, mc)]
    return [row[n:] for row in m]


def fit(rows, p):
    """Least squares on sparse rows [(y, [(column, x), ...], cluster)] with p columns.

    Returns (beta, cov, residuals, sigma, r2) with a cluster-robust covariance (CR1)."""
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    for y, xs, _ in rows:
        for i, xi in xs:
            xty[i] += xi * y
            row = xtx[i]
            for j, xj in xs:
                row[j] += xi * xj
    inv = _invert(xtx)
    beta = [sum(inv[i][j] * xty[j] for j in range(p)) for i in range(p)]
    resid = [y - sum(beta[i] * x for i, x in xs) for y, xs, _ in rows]
    n = len(rows)
    sse = sum(e * e for e in resid)
    ys = [r[0] for r in rows]
    my = sum(ys) / n
    sst = sum((y - my) ** 2 for y in ys) or 1.0
    # sandwich: inv . sum_g (X_g' e_g)(X_g' e_g)' . inv
    scores = {}
    for (y, xs, g), e in zip(rows, resid):
        s = scores.setdefault(g, {})
        for i, x in xs:
            s[i] = s.get(i, 0.0) + x * e
    meat = [[0.0] * p for _ in range(p)]
    for s in scores.values():
        items = list(s.items())
        for i, a in items:
            row = meat[i]
            for j, b in items:
                row[j] += a * b
    groups = len(scores)
    k = (groups / max(groups - 1, 1)) * ((n - 1) / max(n - p, 1))
    half = [[sum(inv[i][a] * meat[a][j] for a in range(p) if meat[a][j]) for j in range(p)] for i in range(p)]
    cov = [[k * sum(half[i][a] * inv[a][j] for a in range(p)) for j in range(p)] for i in range(p)]
    return beta, cov, resid, math.sqrt(sse / max(n - p, 1)), 1 - sse / sst


def _se(cov, i):
    return math.sqrt(max(cov[i][i], 0.0))


def _diff_se(cov, i, j):
    return math.sqrt(max(cov[i][i] + cov[j][j] - 2 * cov[i][j], 0.0))


# ------------------------------------------------------------------------------------------------ one race
def _laps(db, session_key):
    marks = ",".join("?" * len(DRY))
    return [dict(r) for r in db.execute(
        f"""SELECT driver_number, lap_number, lap_time, compound, tyre_age, stint, position, interval_ahead, track_temp
            FROM laps WHERE session_key = ? AND clean = 1 AND compound IN ({marks}) AND COALESCE(rainfall, 0) = 0
            AND tyre_age IS NOT NULL AND lap_time IS NOT NULL ORDER BY driver_number, lap_number""",
        (session_key, *DRY))]


def _traffic(lap):
    return 1.0 if (lap["position"] or 0) > 1 and lap["interval_ahead"] is not None and lap["interval_ahead"] < TRAFFIC else 0.0


class _Design:
    """The model's columns for one race's laps."""

    def __init__(self, laps, fuel_fixed=None):
        self.fuel_fixed = fuel_fixed
        per = {}
        for l in laps:
            e = per.setdefault(l["compound"], [0, set()])
            e[0] += 1
            e[1].add((l["driver_number"], l["stint"]))
        self.left_out = [{"compound": c, "laps": n, "stints": len(st)} for c, (n, st) in per.items()
                         if n < MIN_COMPOUND_LAPS or len(st) < MIN_COMPOUND_STINTS]
        out = {e["compound"] for e in self.left_out}
        laps = [l for l in laps if l["compound"] not in out]
        n_by = {}
        for l in laps:
            n_by[l["driver_number"]] = n_by.get(l["driver_number"], 0) + 1
        self.drivers = sorted(d for d, n in n_by.items() if n >= MIN_LAPS)
        keep = set(self.drivers)
        self.laps = [l for l in laps if l["driver_number"] in keep]
        by_c = {}
        for l in self.laps:
            by_c[l["compound"]] = by_c.get(l["compound"], 0) + 1
        self.compounds = [c for c in DRY if by_c.get(c)]
        self.compound_laps = by_c
        self.ref = "MEDIUM" if "MEDIUM" in by_c else max(by_c, key=by_c.get) if by_c else None
        temps = [l["track_temp"] for l in self.laps if l["track_temp"] is not None]
        self.track_temp = median(temps) if temps else None
        cols = [("driver", d) for d in self.drivers] + ([("fuel", None)] if fuel_fixed is None else [])
        # a compound's offset needs a driver who ran it and another compound; without one it can't be told apart
        # from driver pace, and is left in it
        ran = {}
        for l in self.laps:
            ran.setdefault(l["driver_number"], set()).add(l["compound"])
        self.offsets = [c for c in self.compounds if c != self.ref and any(c in r and len(r) > 1 for r in ran.values())]
        cols += [("offset", c) for c in self.offsets]
        cols += [("deg", c) for c in self.compounds]
        self.has_traffic = sum(_traffic(l) for l in self.laps) >= 20
        if self.has_traffic:
            cols.append(("traffic", None))
        self.cols = cols
        self.ix = {c: i for i, c in enumerate(cols)}

    def row(self, l):
        ix = self.ix
        xs = [(ix[("driver", l["driver_number"])], 1.0), (ix[("deg", l["compound"])], float(l["tyre_age"]))]
        y = l["lap_time"]
        if self.fuel_fixed is None:
            xs.append((ix[("fuel", None)], float(l["lap_number"])))
        else:
            y -= self.fuel_fixed * l["lap_number"]
        if l["compound"] in self.offsets:
            xs.append((ix[("offset", l["compound"])], 1.0))
        if self.has_traffic and _traffic(l):
            xs.append((ix[("traffic", None)], 1.0))
        return (y, xs, (l["driver_number"], l["stint"]))


def _cliff(laps, resid, beta_deg):
    """A change point in one compound's degradation: the tyre age after which laps get steeper, if clearly so.

    Works on partial residuals (everything else the model explains taken out, the compound's own line put back)."""
    pts = [(l["tyre_age"], e + beta_deg * l["tyre_age"], (l["driver_number"], l["stint"])) for l, e in zip(laps, resid)]
    ages = sorted({a for a, _, _ in pts})
    best = None
    for k in ages:
        if k < CLIFF_FROM or sum(1 for a in ages if a <= k) < 5:
            continue
        after = [p for p in pts if p[0] > k]
        if len(after) < CLIFF_AFTER or len({p[2] for p in after}) < CLIFF_CARS or len(pts) - len(after) < CLIFF_AFTER:
            continue
        rows = [(y, [(0, 1.0), (1, float(a))] + ([(2, float(a - k))] if a > k else []), g) for a, y, g in pts]
        try:
            b, cov, _, _, _ = fit(rows, 3)
        except Singular:
            continue
        se = _se(cov, 2)
        t = b[2] / se if se else 0
        if best is None or t > best[0]:
            best = (t, k, b[2], se, len(after))
    if best and best[0] >= CLIFF_T and best[2] >= CLIFF_MIN:
        return {"age": best[1], "extra": _r(best[2]), "se": _r(best[3]), "laps_after": best[4]}
    return None


def fit_race(db, session, teams=None, fuel_fixed=None):
    """The lap-time model for one race (a sessions row); 'skipped' says why when it can't be fitted.

    teams: {(session_key, driver_number): (code, team, colour)}. fuel_fixed: s per lap to use instead of fitting
    the fuel term (when every driver ran one stint, fuel and tyre age are the same thing)."""
    key = session["session_key"]
    base = {"session_key": key, "round": session["round"], "name": session["meeting_name"], "circuit": session["circuit"]}
    laps = _laps(db, key)
    d = _Design(laps, fuel_fixed)
    if len(d.laps) < MIN_RACE_LAPS or len(d.drivers) < 6:
        return dict(base, skipped="too few clean dry laps (a wet or heavily neutralised race)")
    rows = [d.row(l) for l in d.laps]
    try:
        beta, cov, resid, sigma, r2 = fit(rows, len(d.cols))
        mad = median(abs(e) for e in resid) * 1.4826 or sigma
        keep = [i for i, e in enumerate(resid) if abs(e) <= TRIM * mad]
        trimmed = len(rows) - len(keep)
        if trimmed:
            d.laps = [d.laps[i] for i in keep]
            rows = [rows[i] for i in keep]
            beta, cov, resid, sigma, r2 = fit(rows, len(d.cols))
    except Singular:
        return dict(base, skipped="fuel and tyre age can't be told apart (too few pit stops)", singular=True)
    ix = d.ix
    info = teams or {}

    def who(n):
        return info.get((key, n), (str(n), None, None))

    di = [(n, ix[("driver", n)]) for n in d.drivers]
    fastest_n, fastest_i = min(di, key=lambda t: beta[t[1]])
    n_laps = {}
    for l in d.laps:
        n_laps[l["driver_number"]] = n_laps.get(l["driver_number"], 0) + 1
    drivers = []
    for n, i in di:
        code, team, colour = who(n)
        drivers.append({"number": n, "code": code, "team": team, "colour": colour, "laps": n_laps.get(n, 0),
                        "gap": _r(beta[i] - beta[fastest_i]), "se": _r(_diff_se(cov, i, fastest_i))})
    drivers.sort(key=lambda r: r["gap"])
    team_best = {}
    for r in drivers:
        if r["team"] and r["team"] not in team_best:
            team_best[r["team"]] = r
    reference = median(l["lap_time"] for l in d.laps)

    compounds = []
    for c in d.compounds:
        laps_c = [(l, e) for l, e in zip(d.laps, resid) if l["compound"] == c]
        ages = [l["tyre_age"] for l, _ in laps_c]
        g = ix[("deg", c)]
        entry = {"compound": c, "laps": len(laps_c), "ages": [min(ages), max(ages)],
                 "deg": _est(beta[g], _se(cov, g), 4), "reference": c == d.ref,
                 "offset": _est(0.0, 0.0) if c == d.ref else
                 _est(beta[ix[("offset", c)]], _se(cov, ix[("offset", c)])) if c in d.offsets else None}
        entry["cov"] = _r(cov[ix[("offset", c)]][g], 6) if c in d.offsets else 0.0   # for the curve's error band
        entry["cliff"] = _cliff([l for l, _ in laps_c], [e for _, e in laps_c], beta[g]) if fuel_fixed is None else None
        compounds.append(entry)

    traffic = None
    dirty = {}
    if d.has_traffic:
        t = ix[("traffic", None)]
        traffic = dict(_est(beta[t], _se(cov, t)), laps=sum(1 for l in d.laps if _traffic(l)))
        for l, e in zip(d.laps, resid):
            if _traffic(l):
                team = who(l["driver_number"])[1]
                if team:
                    dirty.setdefault(team, []).append(beta[t] + e)
    fuel = ix.get(("fuel", None))
    return dict(base, laps=len(d.laps), trimmed=trimmed, drivers=drivers, sigma=_r(sigma), r2=_r(r2),
                reference_lap=_r(reference),
                fuel=_est(beta[fuel], _se(cov, fuel), 4) if fuel is not None else {"est": _r(fuel_fixed, 4), "se": None, "fixed": True},
                track_temp=_r(d.track_temp, 1),
                traffic=traffic, compounds=compounds, ref_compound=d.ref, left_out=d.left_out,
                teams={team: {"gap": r["gap"], "se": r["se"], "code": r["code"], "colour": r["colour"]}
                       for team, r in team_best.items()},
                dirty=dirty)


# ------------------------------------------------------------------------------------------------ the season
def _pooled(items):
    """Inverse-variance weighted mean of [(est, se)], with its standard error."""
    w = [(v, 1 / se ** 2) for v, se in items if v is not None and se]
    if not w:
        return None
    tot = sum(x for _, x in w)
    return _est(sum(v * x for v, x in w) / tot, math.sqrt(1 / tot), 4)


def _steps(points):
    """The biggest step in a [[round, %]] series, if clear: where the average gap changes most."""
    if len(points) < 2 * STEP_SIDE:
        return None
    vals = [v for _, v in points]
    best = None
    for k in range(STEP_SIDE, len(vals) - STEP_SIDE + 1):
        a, b = vals[:k], vals[k:]
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        ss = sum((v - ma) ** 2 for v in a) + sum((v - mb) ** 2 for v in b)
        s = math.sqrt(ss / (len(vals) - 2)) if len(vals) > 2 else 0
        t = abs(mb - ma) / (s * math.sqrt(1 / len(a) + 1 / len(b))) if s else 0
        if best is None or t > best[0]:
            best = (t, points[k][0], ma, mb)
    if best and best[0] >= STEP_T:
        return {"round": best[1], "before": _r(best[2]), "after": _r(best[3]), "change": _r(best[3] - best[2]),
                "t": _r(best[0], 1)}
    return None


def _corr(xy):
    if len(xy) < 5:
        return None
    mx = sum(x for x, _ in xy) / len(xy)
    my = sum(y for _, y in xy) / len(xy)
    sxy = sum((x - mx) * (y - my) for x, y in xy)
    sxx = sum((x - mx) ** 2 for x, _ in xy)
    syy = sum((y - my) ** 2 for _, y in xy)
    return _r(sxy / math.sqrt(sxx * syy), 2) if sxx and syy else None


def season(db, races, teams_info, team_colour, pct_series):
    """The model for every race in `races` (sessions rows), and what it says across the season.

    pct_series: seasonreport._pct_series (passed in to keep one definition of '% off the best')."""
    fits = [fit_race(db, s, teams_info) for s in races]
    fuel = _pooled([(f["fuel"]["est"], f["fuel"]["se"]) for f in fits if "skipped" not in f])
    if fuel:      # races where fuel and tyre age can't be separated: fixed at the season's figure instead
        fits = [fit_race(db, s, teams_info, fuel["est"]) if f.get("singular") else f for s, f in zip(races, fits)]
    ok = [f for f in fits if "skipped" not in f]
    per_round = {f["round"]: {team: f["reference_lap"] + t["gap"] for team, t in f["teams"].items()} for f in ok}
    pace = pct_series(per_round, team_colour)
    for t in pace:
        t["step"] = _steps(t["points"])
    # error bars for the trend: the gap's standard error as % of that race's reference lap
    se_pct = {(f["round"], team): round(t["se"] / f["reference_lap"] * 100, 3) for f in ok for team, t in f["teams"].items()}
    for t in pace:
        t["se"] = [se_pct.get((rnd, t["team"])) for rnd, _ in t["points"]]

    dirty = {}
    for f in ok:
        for team, costs in f.pop("dirty").items():
            dirty.setdefault(team, []).extend(costs)
    dirty_teams = []
    for team, costs in dirty.items():
        if len(costs) >= 30:
            m = sum(costs) / len(costs)
            sd = math.sqrt(sum((c - m) ** 2 for c in costs) / (len(costs) - 1))
            dirty_teams.append({"team": team, "colour": team_colour.colour.get(team), "laps": len(costs),
                                **_est(m, sd / math.sqrt(len(costs)))})
    dirty_teams.sort(key=lambda r: r["est"])

    # degradation across the season: only races where fuel was fitted (with fuel fixed, a drying or rubbering-in
    # track beyond the season's usual rate lands in the tyre term)
    deg = {c: [] for c in DRY}
    fitted = [f for f in ok if not f["fuel"].get("fixed")]
    for f in fitted:
        for c in f["compounds"]:
            deg[c["compound"]].append((c["deg"]["est"], c["deg"]["se"]))
    deg_temp = {c: [[f["track_temp"], x["deg"]["est"], f["round"]] for f in fitted if f.get("track_temp")
                    for x in f["compounds"] if x["compound"] == c] for c in DRY}
    for f in fits:
        f.pop("dirty", None)
    return {
        "races": fits,
        "pace": {"rounds": [{"round": f["round"], "name": f["name"], "circuit": f["circuit"]} for f in ok], "teams": pace},
        "fuel": fuel,
        "traffic": _pooled([(f["traffic"]["est"], f["traffic"]["se"]) for f in ok if f["traffic"]]),
        "deg": {c: _pooled(v) for c, v in deg.items() if v},
        "deg_temp": {c: {"points": v, "r": _corr([(x, y) for x, y, _ in v])} for c, v in deg_temp.items() if v},
        "dirty_teams": dirty_teams,
        "cliffs": [dict(c["cliff"], compound=c["compound"], round=f["round"], circuit=f["circuit"])
                   for f in ok for c in f["compounds"] if c.get("cliff")],
        "settings": {"traffic": TRAFFIC, "min_laps": MIN_LAPS},
    }
