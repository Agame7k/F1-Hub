"""Reliability across the history of the World Championship (f1hub/history.py has the results): why cars didn't
finish, era by era, per team, and per power-unit maker in the hybrid era (2014 on).

Since 2023 the results only say "Retired", with no cause. For a season compiled into a dataset, F1 Hub tells
retirements after an incident (race control named the car in an incident, collision or crash on its last laps)
from the rest, which are probably mechanical; that split is marked as inferred.
"""
import json
import math
import re
from collections import Counter

from . import history

# cause -> group. Statuses not listed are "other".
GROUPS = {
    "engine": ("Engine", "Power Unit", "Turbo", "ERS", "Supercharger", "Ignition", "Magneto", "Distributor", "Spark plugs",
               "Injection", "Throttle", "Overheating", "Oil leak", "Oil pressure", "Oil pump", "Oil pipe", "Oil line",
               "Water leak", "Water pressure", "Water pump", "Water pipe", "Radiator", "Exhaust", "Heat shield fire",
               "Fire", "Power loss", "Pneumatics", "Cooling system", "Engine fire", "Engine misfire", "Turbocharger"),
    "fuel": ("Fuel pump", "Fuel system", "Fuel leak", "Fuel pressure", "Fuel pipe", "Fuel", "Out of fuel", "Fuel rig"),
    "transmission": ("Gearbox", "Transmission", "Clutch", "Differential", "Halfshaft", "Driveshaft", "Axle", "CV joint"),
    "electrical": ("Electrical", "Electronics", "Alternator", "Battery"),
    "hydraulics": ("Hydraulics",),
    "brakes": ("Brakes", "Brake duct"),
    "suspension": ("Suspension", "Steering", "Wheel bearing", "Handling", "Vibrations", "Track rod"),
    "wheels": ("Wheel", "Wheel nut", "Wheel rim", "Tyre", "Puncture", "Tyre puncture"),
    "bodywork": ("Chassis", "Undertray", "Front wing", "Rear wing", "Broken wing", "Bodywork", "Mirror", "Seat", "Refuelling",
                 "Safety belt", "Safety concerns", "Launch control", "Drivetrain", "Debris"),
    "mechanical": ("Mechanical", "Technical", "Stalled", "Overheating brakes"),
    "incident": ("Accident", "Collision", "Collision damage", "Spun off", "Fatal accident", "Damage"),
    "driver": ("Physical", "Driver unwell", "Injured", "Injury", "Eye injury", "Illness", "Driver Seat"),
    "dsq": ("Disqualified", "Excluded"),
    "dns": ("Did not start", "Withdrew", "Did not qualify", "Did not prequalify", "107% Rule", "Not restarted"),
    "unknown": ("Retired",),
    "not_classified": ("Not classified",),
}
GROUP_OF = {s.lower(): g for g, ss in GROUPS.items() for s in ss}
MECHANICAL = ("engine", "fuel", "transmission", "electrical", "hydraulics", "brakes", "suspension", "wheels", "bodywork",
              "mechanical")
LABEL = {"engine": "Engine / power unit", "fuel": "Fuel system", "transmission": "Gearbox / transmission",
         "electrical": "Electrical", "hydraulics": "Hydraulics", "brakes": "Brakes", "suspension": "Suspension / steering",
         "wheels": "Wheels / tyres", "bodywork": "Chassis / bodywork", "mechanical": "Other mechanical",
         "incident": "Accident / collision", "driver": "Driver unwell / injured", "dsq": "Disqualified",
         "unknown": "Retired (no cause given)", "unknown_mech": "Retired, no incident (probably mechanical)",
         "unknown_incident": "Retired after an incident", "finished": "Finished", "not_classified": "Not classified",
         "other": "Other", "dns": "Did not start"}
POWER_UNIT = ("engine", "fuel", "electrical")      # what a power-unit maker answers for

# hybrid-era power units by season and Ergast/Jolpica constructorId
_PU = {
    2014: {"Mercedes": "mercedes williams force_india mclaren", "Ferrari": "ferrari sauber marussia",
           "Renault": "red_bull toro_rosso lotus_f1 caterham"},
    2015: {"Mercedes": "mercedes williams force_india lotus_f1", "Ferrari": "ferrari sauber manor marussia",
           "Renault": "red_bull toro_rosso", "Honda": "mclaren"},
    2016: {"Mercedes": "mercedes williams force_india manor", "Ferrari": "ferrari sauber haas toro_rosso",
           "Renault": "renault red_bull", "Honda": "mclaren"},
    2017: {"Mercedes": "mercedes williams force_india", "Ferrari": "ferrari sauber haas",
           "Renault": "renault red_bull toro_rosso", "Honda": "mclaren"},
    2018: {"Mercedes": "mercedes williams force_india", "Ferrari": "ferrari sauber haas",
           "Renault": "renault red_bull mclaren", "Honda": "toro_rosso"},
    2019: {"Mercedes": "mercedes williams racing_point", "Ferrari": "ferrari alfa haas",
           "Renault": "renault mclaren", "Honda": "red_bull toro_rosso"},
    2020: {"Mercedes": "mercedes williams racing_point", "Ferrari": "ferrari alfa haas",
           "Renault": "renault mclaren", "Honda": "red_bull alphatauri"},
    2021: {"Mercedes": "mercedes williams aston_martin mclaren", "Ferrari": "ferrari alfa haas",
           "Renault": "alpine", "Honda": "red_bull alphatauri"},
}
for _y in (2022, 2023):
    _PU[_y] = {"Mercedes": "mercedes williams aston_martin mclaren", "Ferrari": "ferrari alfa haas", "Renault": "alpine",
               "Honda RBPT": "red_bull alphatauri"}
for _y in (2024, 2025):
    _PU[_y] = {"Mercedes": "mercedes williams aston_martin mclaren", "Ferrari": "ferrari sauber haas", "Renault": "alpine",
               "Honda RBPT": "red_bull rb"}
PU = {y: {team: maker for maker, teams in m.items() for team in teams.split()} for y, m in _PU.items()}


def group(status):
    s = (status or "").strip()
    if s == "Finished" or s == "Lapped" or re.fullmatch(r"\+\d+ Laps?", s):
        return "finished"
    return GROUP_OF.get(s.lower(), "other")


def _r(v, n=3):
    return None if v is None else round(v, n)


def _rate(k, n):
    """Share with a 95% Wilson interval."""
    if not n:
        return {"est": None, "lo": None, "hi": None, "k": k, "n": n}
    p, z = k / n, 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"est": _r(p, 4), "lo": _r(max(0, c - h), 4), "hi": _r(min(1, c + h), 4), "k": k, "n": n}


def _infer_2023_on(rows, year):
    """For a season whose results only say "Retired": split those into after-an-incident and the rest, from the
    season dataset's race control (if it is compiled)."""
    from . import seasonreport
    db = seasonreport.open_dataset(year)
    if db is None:
        return 0
    try:
        incident = set()
        msgs = db.execute("""SELECT s.round, rc.lap_number, rc.message FROM race_control rc JOIN sessions s USING (session_key)
                             WHERE s.session_name = 'Race'""").fetchall()
        nums = {}
        for r in db.execute("""SELECT s.round, d.driver_number, d.code FROM drivers d JOIN sessions s USING (session_key)
                               WHERE s.session_name = 'Race'"""):
            nums[(r[0], r[2])] = r[1]
        from .racecontrol import cars_in
        for rnd, lap, msg in msgs:
            if re.search(r"INCIDENT|COLLISION|CRASH|SPUN|CONTACT", msg or "", re.I):
                for n in cars_in(msg):
                    incident.add((rnd, n, lap))
        changed = 0
        for r in rows:
            if group(r["status"]) != "unknown":
                continue
            n = nums.get((r["round"], r["code"]))
            hit = any((r["round"], n, L) in incident for L in range(max(0, r["laps"] - 1), r["laps"] + 3)) if n else False
            r["_group"] = "unknown_incident" if hit else "unknown_mech"
            changed += 1
        return changed
    finally:
        db.close()


def load(first=history.FIRST, last=None):
    """Every result kept, with its cause group (the Indianapolis 500 of 1950-60 left out: other cars, other rules)."""
    out = []
    for y in history.have():
        if y < first or (last and y > last):
            continue
        rows = [dict(r, year=y) for r in history.season(y) if not (r["circuit"] == "indianapolis" and y <= 1960)]
        if y >= 2023:
            _infer_2023_on(rows, y)
        for r in rows:
            r["group"] = r.pop("_group", None) or group(r["status"])
        out += rows
    return out


def _started(r):
    return r["group"] != "dns"


def _mech(g):
    return g in MECHANICAL or g == "unknown_mech"


def report(first=None, last=None):
    rows = load()
    if not rows:
        return {"seasons": [], "have": history.have()}
    years = sorted({r["year"] for r in rows})
    team_name = {}
    for r in rows:
        team_name[r["team"]] = r["team_name"]
    # season by season: how starts ended
    by_year = []
    for y in years:
        rs = [r for r in rows if r["year"] == y and _started(r)]
        c = Counter(r["group"] for r in rs)
        n = len(rs)
        mech = sum(v for g, v in c.items() if _mech(g))
        by_year.append({"year": y, "starts": n, "races": len({r["round"] for r in rs}),
                        "finished": _r(c["finished"] / n, 4), "mechanical": _r(mech / n, 4),
                        "incident": _r((c["incident"] + c["unknown_incident"]) / n, 4),
                        "unknown": _r(c["unknown"] / n, 4), "other": _r((c["driver"] + c["dsq"] + c["other"] + c["not_classified"]) / n, 4),
                        "inferred": bool(c["unknown_mech"] or c["unknown_incident"])})
    # what failed, decade by decade (share of mechanical retirements)
    decades = []
    for d in range(years[0] // 10 * 10, years[-1] + 1, 10):
        rs = [r for r in rows if d <= r["year"] < d + 10 and _started(r)]
        mech = [r["group"] for r in rs if r["group"] in MECHANICAL]
        c = Counter(mech)
        if rs:
            decades.append({"decade": d, "starts": len(rs), "mechanical": len(mech), "per_100": _r(100 * len(mech) / len(rs), 2),
                            "causes": {g: c[g] for g in MECHANICAL if c[g]}})
    # teams: the last decade-and-a-bit, and all-time
    def team_rows(rs):
        out = {}
        for r in rs:
            if not _started(r):
                continue
            t = out.setdefault(r["team"], {"team": r["team"], "name": team_name[r["team"]], "starts": 0, "mech": 0,
                                           "incident": 0, "finished": 0, "seasons": set()})
            t["starts"] += 1
            t["mech"] += _mech(r["group"])
            t["incident"] += r["group"] in ("incident", "unknown_incident")
            t["finished"] += r["group"] == "finished"
            t["seasons"].add(r["year"])
        res = []
        for t in out.values():
            s = sorted(t.pop("seasons"))
            res.append(dict(t, first=s[0], last=s[-1], mechanical=_rate(t["mech"], t["starts"]),
                            finish=_rate(t["finished"], t["starts"])))
        return res
    lo = first or max(years[0], years[-1] - 11)
    hi = last or years[-1]
    recent = team_rows([r for r in rows if lo <= r["year"] <= hi])
    recent.sort(key=lambda t: t["mechanical"]["est"])
    alltime = [t for t in team_rows(rows) if t["starts"] >= 400]
    alltime.sort(key=lambda t: t["mechanical"]["est"])
    # per team per season in the window (a heat map)
    grid = {}
    for r in rows:
        if lo <= r["year"] <= hi and _started(r):
            g = grid.setdefault((r["team"], r["year"]), [0, 0])
            g[0] += 1
            g[1] += _mech(r["group"])
    heat = [{"team": t, "name": team_name[t], "year": y, "starts": v[0], "mech": v[1]} for (t, y), v in grid.items()]
    # hybrid era: power-unit makers, power-unit failures per start
    pu = []
    for y in sorted(PU):
        if y not in years:
            continue
        rs = [r for r in rows if r["year"] == y and _started(r)]
        makers = {}
        for r in rs:
            m = PU[y].get(r["team"])
            if not m:
                continue
            e = makers.setdefault(m, {"starts": 0, "pu": 0, "mech": 0, "unknown": 0})
            e["starts"] += 1
            e["pu"] += r["group"] in POWER_UNIT
            e["mech"] += _mech(r["group"])
            e["unknown"] += r["group"] in ("unknown", "unknown_mech")
        for m, e in makers.items():
            pu.append({"year": y, "maker": m, "starts": e["starts"], "pu": _rate(e["pu"], e["starts"]),
                       "mechanical": _rate(e["mech"], e["starts"]), "unknown": e["unknown"],
                       "causes_published": e["unknown"] == 0})
    unmapped = sorted({(r["year"], r["team"]) for r in rows if r["year"] in PU and _started(r) and r["team"] not in PU[r["year"]]})
    with_cause = {r["year"] for r in rows if r["group"] in MECHANICAL}
    causes_until = max(with_cause, default=None)
    if causes_until == years[-1]:              # the kept seasons all have causes: nothing to say
        causes_until = None
    return {"have": history.have(), "years": [years[0], years[-1]], "by_year": by_year, "decades": decades,
            "labels": LABEL, "mechanical_groups": list(MECHANICAL), "window": [lo, hi], "teams": recent,
            "alltime": alltime, "heat": heat, "power_units": pu, "unmapped": unmapped,
            "causes_until": causes_until}
