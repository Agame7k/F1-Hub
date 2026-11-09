"""Season report: season-wide numbers from a compiled season dataset (f1hub/dataset.py).

Everything here is descriptive - counted or measured straight from the dataset. Pace is the median clean lap,
not yet corrected for fuel or traffic (that is the lap-time model, phase 3 on docs/roadmap.md).
"""
import sqlite3
from statistics import median, pstdev

from . import dataset
from .racecontrol import neutralisation_counts

MIN_CLEAN_LAPS = 10       # a driver needs this many clean dry laps in a race for a race-pace figure
SLOW_STOP = 4.0           # s stationary: a slow stop
DRY = ("SOFT", "MEDIUM", "HARD")
COMPOUNDS = ("SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET")


def _r(v, n=3):
    return None if v is None else round(v, n)


def open_dataset(year):
    """A read-only connection to the year's dataset, or None if it hasn't been compiled."""
    path = dataset.default_path(year)
    if not path.exists():
        return None
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def compiled_years():
    """Years with a compiled dataset, newest first."""
    out = []
    for p in dataset.DATA_DIR.glob("season_*.sqlite"):
        y = p.stem.split("_")[-1]
        if y.isdigit():
            out.append(int(y))
    return sorted(out, reverse=True)


def status(year):
    """What's in the year's dataset: sessions, rounds, laps and when it was last compiled."""
    db = open_dataset(year)
    if db is None:
        return {"year": year, "exists": False}
    try:
        row = db.execute("""SELECT COUNT(*) AS sessions, COUNT(DISTINCT round) AS rounds, MAX(compiled_at) AS at,
                            SUM(final = 0) AS provisional FROM sessions""").fetchone()
        laps = db.execute("SELECT COUNT(*) FROM laps").fetchone()[0]
    except sqlite3.DatabaseError:
        return {"year": year, "exists": False}
    finally:
        db.close()
    return {"year": year, "exists": True, "sessions": row["sessions"], "rounds": row["rounds"], "laps": laps,
            "compiled_at": row["at"], "provisional": row["provisional"] or 0,
            "path": str(dataset.default_path(year))}


def round_before(year, meeting_key):
    """Spoiler mode: the last round to show when meeting_key is hidden - the round before it, if it's in this
    year's dataset, else None (nothing to hide)."""
    db = open_dataset(year)
    if db is None or not meeting_key:
        return None
    try:
        row = db.execute("SELECT MIN(round) FROM sessions WHERE meeting_key = ?", (meeting_key,)).fetchone()
    finally:
        db.close()
    return row[0] - 1 if row and row[0] else None


# ------------------------------------------------------------------------------------------------ helpers
class _Teams:
    """Team names and colours (latest in the season), and each driver's team at each session."""

    def __init__(self, db, upto):
        self.of, self.colour, self.code = {}, {}, {}
        for r in db.execute("""SELECT d.session_key, d.driver_number, d.code, d.team, d.colour FROM drivers d
                               JOIN sessions s USING (session_key) WHERE s.round <= ? ORDER BY s.date_start""", (upto,)):
            self.of[(r["session_key"], r["driver_number"])] = r["team"]
            self.code[(r["session_key"], r["driver_number"])] = r["code"]
            if r["team"]:
                self.colour[r["team"]] = r["colour"]


def _sessions(db, upto, names):
    marks = ",".join("?" * len(names))
    return db.execute(f"""SELECT * FROM sessions WHERE round <= ? AND session_name IN ({marks})
                          ORDER BY round, date_start""", (upto, *names)).fetchall()


def _round_list(sessions):
    seen, out = set(), []
    for s in sessions:
        if s["round"] not in seen:
            seen.add(s["round"])
            out.append({"round": s["round"], "name": s["meeting_name"], "circuit": s["circuit"]})
    return out


def _pct_series(per_round, teams):
    """{round: {team: value}} -> per team [[round, % off the best that round]], plus the season average."""
    out = {}
    for rnd, vals in per_round.items():
        if len(vals) < 3:
            continue
        best = min(vals.values())
        for team, v in vals.items():
            out.setdefault(team, []).append([rnd, round((v - best) / best * 100, 3)])
    rows = []
    for team, pts in out.items():
        pts.sort()
        gaps = [p[1] for p in pts]
        rows.append({"team": team, "colour": teams.colour.get(team), "points": pts, "avg": _r(sum(gaps) / len(gaps)),
                     "median": _r(median(gaps)), "best": sum(1 for g in gaps if g == 0), "rounds": len(gaps)})
    return sorted(rows, key=lambda r: r["avg"])


# ------------------------------------------------------------------------------------------------ sections
def race_pace(db, upto, teams):
    """Each team's race pace at every round: its quicker driver's median clean lap on dry tyres, as % off the
    quickest team that day. Not corrected for fuel or traffic."""
    races = _sessions(db, upto, ("Race",))
    per = {}
    for s in races:
        laps = {}
        for r in db.execute(f"""SELECT driver_number, lap_time FROM laps WHERE session_key = ? AND clean = 1
                                AND compound IN ({",".join("?" * len(DRY))})""", (s["session_key"], *DRY)):
            laps.setdefault(r["driver_number"], []).append(r["lap_time"])
        best = {}
        for n, ts in laps.items():
            team = teams.of.get((s["session_key"], n))
            if team and len(ts) >= MIN_CLEAN_LAPS:
                m = median(ts)
                best[team] = min(best.get(team, m), m)
        per[s["round"]] = best
    return {"rounds": _round_list(races), "teams": _pct_series(per, teams)}


def quali_pace(db, upto, teams):
    """Each team's best qualifying lap (Q1, Q2 or Q3) as % off pole, round by round."""
    qs = _sessions(db, upto, ("Qualifying",))
    per = {}
    for s in qs:
        best = {}
        for r in db.execute("SELECT driver_number, MIN(COALESCE(q3, 1e9), COALESCE(q2, 1e9), COALESCE(q1, 1e9)) AS t "
                            "FROM results WHERE session_key = ?", (s["session_key"],)):
            team = teams.of.get((s["session_key"], r["driver_number"]))
            if team and r["t"] and r["t"] < 1e9:
                best[team] = min(best.get(team, r["t"]), r["t"])
        per[s["round"]] = best
    return {"rounds": _round_list(qs), "teams": _pct_series(per, teams)}


def teammates(db, upto, teams):
    """Team-mate qualifying gaps: in the last segment both drivers set a time in, how far apart they were."""
    pairs = {}
    for s in _sessions(db, upto, ("Qualifying",)):
        by_team = {}
        for r in db.execute("SELECT driver_number, q1, q2, q3 FROM results WHERE session_key = ?", (s["session_key"],)):
            team = teams.of.get((s["session_key"], r["driver_number"]))
            if team:
                by_team.setdefault(team, []).append(r)
        for team, rows in by_team.items():
            if len(rows) != 2:
                continue
            a, b = sorted(rows, key=lambda r: teams.code[(s["session_key"], r["driver_number"])])
            seg = next((q for q in ("q3", "q2", "q1") if a[q] and b[q]), None)
            if not seg:
                continue
            ca, cb = teams.code[(s["session_key"], a["driver_number"])], teams.code[(s["session_key"], b["driver_number"])]
            pairs.setdefault((team, ca, cb), []).append({"round": s["round"], "seg": seg.upper(),
                                                         "gap": round(b[seg] - a[seg], 3),
                                                         "pct": round((b[seg] - a[seg]) / a[seg] * 100, 3)})
    out = []
    for (team, ca, cb), rounds in pairs.items():
        if len(rounds) < 3:
            continue
        pct = median(r["pct"] for r in rounds)
        a_ahead = sum(1 for r in rounds if r["gap"] > 0)
        # a positive median: the first driver (alphabetically) is quicker; flip so 'a' is always the quicker one
        if pct < 0:
            ca, cb, pct, a_ahead = cb, ca, -pct, len(rounds) - a_ahead
            rounds = [dict(r, gap=-r["gap"], pct=-r["pct"]) for r in rounds]
        out.append({"team": team, "colour": teams.colour.get(team), "a": ca, "b": cb, "rounds": rounds,
                    "median_pct": round(pct, 3), "median_gap": round(median(r["gap"] for r in rounds), 3),
                    "ahead": [a_ahead, len(rounds) - a_ahead]})
    return sorted(out, key=lambda r: -r["median_pct"])


def racecraft(db, upto, teams):
    """Per driver over races and sprints: laps led, places on lap 1, overtakes on track, grid to flag."""
    keys = [s["session_key"] for s in _sessions(db, upto, ("Race", "Sprint"))]
    races = {s["session_key"] for s in _sessions(db, upto, ("Race",))}
    if not keys:
        return []
    marks = ",".join("?" * len(keys))
    d = {}

    def row(key, n):
        code = teams.code.get((key, n)) or str(n)
        e = d.setdefault(code, {"code": code, "team": teams.of.get((key, n)), "led": 0, "lap1": [], "made": 0, "lost": 0,
                                "gained": [], "starts": 0})
        e["team"] = teams.of.get((key, n)) or e["team"]
        return e

    for r in db.execute(f"SELECT session_key, driver_number, COUNT(*) AS n FROM laps WHERE position = 1 "
                        f"AND session_key IN ({marks}) GROUP BY 1, 2", keys):
        row(r["session_key"], r["driver_number"])["led"] += r["n"]
    for r in db.execute(f"""SELECT r.session_key, r.driver_number, r.grid, r.position, r.status, l.position AS lap1
                            FROM results r LEFT JOIN laps l ON l.session_key = r.session_key
                            AND l.driver_number = r.driver_number AND l.lap_number = 1
                            WHERE r.session_key IN ({marks})""", keys):
        e = row(r["session_key"], r["driver_number"])
        if r["status"] == "DNS":
            continue
        e["starts"] += 1
        if r["session_key"] in races and r["grid"] and r["lap1"]:
            e["lap1"].append(r["grid"] - r["lap1"])
        if r["session_key"] in races and r["grid"] and r["status"] == "Finished" and r["position"]:
            e["gained"].append(r["grid"] - r["position"])
    for r in db.execute(f"SELECT session_key, overtaking, overtaken FROM overtakes WHERE on_track = 1 "
                        f"AND session_key IN ({marks})", keys):
        row(r["session_key"], r["overtaking"])["made"] += 1
        row(r["session_key"], r["overtaken"])["lost"] += 1
    out = []
    for e in d.values():
        if not e["starts"]:
            continue
        out.append({"code": e["code"], "team": e["team"], "colour": teams.colour.get(e["team"]), "starts": e["starts"],
                    "led": e["led"], "made": e["made"], "lost": e["lost"], "net": e["made"] - e["lost"],
                    "lap1_total": sum(e["lap1"]), "lap1_avg": _r(sum(e["lap1"]) / len(e["lap1"]), 2) if e["lap1"] else None,
                    "gained_total": sum(e["gained"]),
                    "gained_avg": _r(sum(e["gained"]) / len(e["gained"]), 2) if e["gained"] else None})
    return sorted(out, key=lambda e: (-e["made"], e["code"]))


def tyres(db, upto, teams):
    """Race laps by compound at every round, stint lengths per compound, the longest stints and stops per race."""
    races = _sessions(db, upto, ("Race",))
    rounds = []
    for s in races:
        mix = {c: 0 for c in COMPOUNDS}
        for r in db.execute("SELECT compound, COUNT(*) AS n FROM laps WHERE session_key = ? AND compound IS NOT NULL "
                            "GROUP BY 1", (s["session_key"],)):
            if r["compound"] in mix:
                mix[r["compound"]] = r["n"]
        cars = db.execute("SELECT COUNT(DISTINCT driver_number) FROM stints WHERE session_key = ?",
                          (s["session_key"],)).fetchone()[0]
        n_stops = db.execute("SELECT COUNT(*) FROM stints WHERE session_key = ? AND stint > 1",
                             (s["session_key"],)).fetchone()[0]
        rounds.append({"round": s["round"], "name": s["meeting_name"], "circuit": s["circuit"], "laps": mix,
                       "stops_per_car": _r(n_stops / cars, 2) if cars else None})
    keys = [s["session_key"] for s in races]
    by_compound, longest = [], []
    if keys:
        marks = ",".join("?" * len(keys))
        lengths = {}
        for r in db.execute(f"""SELECT st.session_key, st.driver_number, st.compound, st.lap_end - st.lap_start + 1 AS n,
                                s.round, s.meeting_name FROM stints st JOIN sessions s USING (session_key)
                                WHERE st.session_key IN ({marks}) AND st.lap_end IS NOT NULL""", keys):
            if r["compound"] in COMPOUNDS:
                lengths.setdefault(r["compound"], []).append(r["n"])
                longest.append({"code": teams.code.get((r["session_key"], r["driver_number"])), "compound": r["compound"],
                                "laps": r["n"], "round": r["round"], "name": r["meeting_name"],
                                "colour": teams.colour.get(teams.of.get((r["session_key"], r["driver_number"])))})
        by_compound = [{"compound": c, "stints": len(v), "avg": _r(sum(v) / len(v), 1), "median": median(v), "max": max(v)}
                       for c, v in ((c, lengths.get(c)) for c in COMPOUNDS) if v]
        longest = sorted(longest, key=lambda r: -r["laps"])[:10]
    return {"rounds": rounds, "compounds": by_compound, "longest": longest}


def pit_stops(db, upto, teams):
    """Stationary times per team, the fastest stops, and the pit-lane time at each circuit (races and sprints)."""
    keys = [s["session_key"] for s in _sessions(db, upto, ("Race", "Sprint"))]
    if not keys:
        return {"teams": [], "fastest": [], "circuits": []}
    marks = ",".join("?" * len(keys))
    stops, fastest, lanes = {}, [], {}
    for r in db.execute(f"""SELECT p.session_key, p.driver_number, p.lap_number, p.stop_duration, p.lane_duration, p.stop,
                            s.round, s.meeting_name, s.circuit FROM pits p JOIN sessions s USING (session_key)
                            WHERE p.session_key IN ({marks})""", keys):
        team = teams.of.get((r["session_key"], r["driver_number"]))
        t = r["stop_duration"]
        if team and t and 1.5 <= t <= 60:
            stops.setdefault(team, []).append(t)
            fastest.append({"team": team, "colour": teams.colour.get(team), "time": t, "round": r["round"],
                            "name": r["meeting_name"], "lap": r["lap_number"],
                            "code": teams.code.get((r["session_key"], r["driver_number"]))})
        if r["stop"] and r["lane_duration"] and 10 <= r["lane_duration"] <= 60:
            lanes.setdefault((r["round"], r["circuit"], r["meeting_name"]), []).append(r["lane_duration"])
    rows = []
    for team, ts in stops.items():
        rows.append({"team": team, "colour": teams.colour.get(team), "stops": len(ts), "median": _r(median(ts), 2),
                     "best": min(ts), "spread": _r(pstdev(ts), 2) if len(ts) > 1 else None,
                     "slow": sum(1 for t in ts if t >= SLOW_STOP), "slow_pct": _r(100 * sum(1 for t in ts if t >= SLOW_STOP) / len(ts), 1),
                     "times": sorted(ts)})
    circuits = [{"round": k[0], "circuit": k[1], "name": k[2], "median": _r(median(v), 1), "stops": len(v)}
                for k, v in lanes.items() if len(v) >= 5]
    return {"teams": sorted(rows, key=lambda r: r["median"]), "fastest": sorted(fastest, key=lambda r: r["time"])[:10],
            "circuits": sorted(circuits, key=lambda r: r["median"]), "slow_after": SLOW_STOP}


def neutralised(db, upto):
    """Safety Cars, VSCs and red flags at every race, and the share of laps run under them."""
    out = []
    for s in _sessions(db, upto, ("Race", "Sprint")):
        rc = [dict(r) for r in db.execute("SELECT date, category, flag, message, lap_number FROM race_control "
                                          "WHERE session_key = ?", (s["session_key"],))]
        n = neutralisation_counts(rc)
        laps = db.execute("SELECT COUNT(*), SUM(neutral) FROM laps WHERE session_key = ?", (s["session_key"],)).fetchone()
        out.append({"round": s["round"], "name": s["meeting_name"], "circuit": s["circuit"], "session": s["session_name"],
                    "sc": n["sc"], "vsc": n["vsc"], "red": n["red"],
                    "pct": _r(100 * (laps[1] or 0) / laps[0], 1) if laps[0] else None})
    return out


def weather(db, upto):
    """Track and air temperature range and rain at every race."""
    out = []
    for s in _sessions(db, upto, ("Race",)):
        rows = db.execute("SELECT track_temp, air_temp, rainfall, wind_speed FROM weather WHERE session_key = ? "
                          "AND date BETWEEN ? AND ?", (s["session_key"], s["date_start"], s["date_end"])).fetchall()
        track = [r["track_temp"] for r in rows if r["track_temp"] is not None]
        air = [r["air_temp"] for r in rows if r["air_temp"] is not None]
        if not track:
            continue
        out.append({"round": s["round"], "name": s["meeting_name"], "circuit": s["circuit"],
                    "track_min": min(track), "track_max": max(track), "track_median": _r(median(track), 1),
                    "air_median": _r(median(air), 1) if air else None,
                    "rain_pct": _r(100 * sum(1 for r in rows if r["rainfall"]) / len(rows), 0),
                    "wind_median": _r(median(r["wind_speed"] for r in rows if r["wind_speed"] is not None), 1)
                    if any(r["wind_speed"] is not None for r in rows) else None})
    return out


def headline(db, upto):
    one = lambda sql, *a: db.execute(sql, (upto, *a)).fetchone()[0]
    return {
        "rounds": one("SELECT COUNT(DISTINCT round) FROM sessions WHERE round <= ? AND session_name = 'Race'"),
        "sprints": one("SELECT COUNT(*) FROM sessions WHERE round <= ? AND session_name = 'Sprint'"),
        "laps": one("""SELECT COUNT(*) FROM laps JOIN sessions USING (session_key) WHERE round <= ?
                       AND session_type = 'Race'"""),
        "overtakes": one("""SELECT COUNT(*) FROM overtakes JOIN sessions USING (session_key) WHERE round <= ?
                            AND on_track = 1"""),
        "stops": one("""SELECT COUNT(*) FROM pits JOIN sessions USING (session_key) WHERE round <= ?
                        AND session_name = 'Race' AND stop = 1"""),
        "winners": one("""SELECT COUNT(DISTINCT r.driver_number) FROM results r JOIN sessions s USING (session_key)
                          WHERE s.round <= ? AND s.session_name = 'Race' AND r.position = 1"""),
        "polesitters": one("""SELECT COUNT(DISTINCT r.driver_number) FROM results r JOIN sessions s USING (session_key)
                              WHERE s.round <= ? AND s.session_name = 'Qualifying' AND r.position = 1"""),
    }


def report(year, upto=None):
    """The whole season report for a compiled year. upto: only rounds up to this one (spoiler mode)."""
    db = open_dataset(year)
    if db is None:
        return {"year": year, "exists": False}
    try:
        last = db.execute("SELECT MAX(round) FROM sessions").fetchone()[0] or 0
        upto = last if upto is None else min(upto, last)
        teams = _Teams(db, upto)
        neutral = neutralised(db, upto)
        h = headline(db, upto)
        h.update(sc=sum(r["sc"] for r in neutral), vsc=sum(r["vsc"] for r in neutral), red=sum(r["red"] for r in neutral))
        return {"year": year, "exists": True, "upto": upto, "last_round": last, "status": status(year), "headline": h,
                "race_pace": race_pace(db, upto, teams), "quali_pace": quali_pace(db, upto, teams),
                "teammates": teammates(db, upto, teams), "racecraft": racecraft(db, upto, teams),
                "tyres": tyres(db, upto, teams), "pits": pit_stops(db, upto, teams), "neutral": neutral,
                "weather": weather(db, upto)}
    finally:
        db.close()
