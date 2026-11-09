"""Strategy report: what f1hub/strategy.py says about a season, for the dashboard (Explore > Strategy) and the
`strategy` command.

The season part (inputs, predictions backtested over every race, title odds after every round) takes half a
minute, and grading one race's pit calls about ten seconds, so both are kept on disk next to the dataset
(DATA_DIR/strategy/) and recomputed only when the dataset or the strategy model changes.
"""
import json
import math

from . import dataset, seasonreport as sr, strategy as st


def _dir():
    d = dataset.DATA_DIR / "strategy"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stamp(db):
    """Changes whenever the season's sessions are compiled again (telemetry doesn't touch anything used here)."""
    return [st.VERSION, db.execute("SELECT MAX(compiled_at) FROM sessions").fetchone()[0]]


def _cached(name, stamp, make):
    path = _dir() / name
    try:
        hit = json.loads(path.read_text())
        if hit.get("stamp") == stamp:
            return hit["data"]
    except (OSError, ValueError):
        pass
    data = make()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"stamp": stamp, "data": data}))
    tmp.replace(path)
    return data


def _cached_only(name, stamp):
    try:
        hit = json.loads((_dir() / name).read_text())
        return hit["data"] if hit.get("stamp") == stamp else None
    except (OSError, ValueError):
        return None


class _Season:
    """The dataset, the lap-time model fits and the strategy inputs, up to a round."""

    def __init__(self, db, upto):
        self.db, self.upto = db, upto
        self.teams = sr._Teams(db, upto)
        self.fits = {f["session_key"]: f for f in sr.model(db, upto, self.teams)["races"]}
        self.inputs = st.Inputs(db, upto, self.fits)
        self.pass_model = st.PassModel.fit(db, self.inputs.races, self.fits)


def _inputs_table(s):
    out = []
    for race in s.inputs.races:
        c = s.inputs.circuit(race["circuit"])
        laps = s.inputs.per[race["circuit"]]["laps"]
        rec = s.inputs.per[race["circuit"]]
        out.append(dict(c, round=race["round"], circuit=race["circuit"], name=race["meeting_name"], laps=laps,
                        sc_seen=rec["sc"], vsc_seen=rec["vsc"],
                        sc_race=st._r(1 - (1 - c["sc_rate"]) ** laps, 3) if laps else None,
                        passing=st._r(s.pass_model.circuits.get(race["circuit"]), 3)))
    return out


def _season(s):
    """Inputs, predictions and title odds for the season so far (heavy: cached)."""
    db = s.db
    bt = st.backtest(db, s.fits, s.teams.of, s.upto, runs=240)
    finishes = [d["finish"] for r in bt["rounds"] for d in r["drivers"]]
    dnf = sum(1 for f in finishes if f is None) / len(finishes) if finishes else 0.08
    title = st.championship(db, s.fits, s.teams.of, bt["score"]["form_sd"] or st.FORM_SD, dnf, s.upto, runs=1500)
    code, colour, team = {}, {}, {}
    for (k, n), t in s.teams.of.items():
        code[n], team[n] = s.teams.code.get((k, n)), t
        colour[n] = s.teams.colour.get(t)
    pm = s.pass_model
    return {"inputs": _inputs_table(s), "pit_loss": st._r(s.inputs.pit_loss_all, 2), "sc_ratio": st._r(s.inputs.sc_ratio, 3),
            "sc_rate": st._r(s.inputs.sc_rate, 4), "vsc_rate": st._r(s.inputs.vsc_rate, 4),
            "pass": dict(pm.to_dict(), curve=[[x / 10, st._r(pm.p(x / 10), 4)] for x in range(-10, 31)]),
            "backtest": bt, "title": title, "dnf": st._r(dnf, 3),
            "drivers": {n: {"code": code[n], "team": team[n], "colour": colour[n]} for n in code},
            "team_colour": dict(s.teams.colour)}


def grade_race(s, session, stamp, progress=lambda m: None):
    f = s.fits.get(session["session_key"])
    if not f or "skipped" in f:
        return {"skipped": (f or {}).get("skipped", "no lap-time model for this race")}
    return _cached(f"{session['year']}-grade-{session['session_key']}.json", stamp,
                   lambda: st.grade(s.db, session, f, s.inputs, s.pass_model, runs=60, progress=progress))


def _race(s, session, stamp, season, grade):
    f = s.fits.get(session["session_key"])
    out = {"round": session["round"], "name": session["meeting_name"], "circuit": session["circuit"],
           "session_key": session["session_key"]}
    out["prediction"] = next((r for r in season["backtest"]["rounds"] if r["round"] == session["round"]), None)
    if not f or "skipped" in f:
        out["skipped"] = (f or {}).get("skipped", "no lap-time model for this race")
        return out
    race = st.race_from_fit(s.db, session, f, s.inputs, s.pass_model)
    out["plan"] = st.plan(race)
    out["compounds"] = {c: [st._r(o, 3), st._r(d, 4)] for c, (o, d) in race.compounds.items()}
    out["max_stint"] = race.max_stint
    seqs = {}
    plans, first = st._actual_plans(s.db, session["session_key"])
    for n, c0 in first.items():
        lab = st._label(c0, plans.get(n, []))
        seqs[lab] = seqs.get(lab, 0) + 1
    out["actual"] = sorted(([k, v] for k, v in seqs.items()), key=lambda x: -x[1])
    name = f"{session['year']}-grade-{session['session_key']}.json"
    out["grade"] = grade_race(s, session, stamp) if grade else _cached_only(name, stamp)
    return out


def _graded_summary(s, stamp):
    """Pit calls across every race graded so far, by team."""
    teams, races = {}, 0
    for race in s.inputs.races:
        g = _cached_only(f"{race['year']}-grade-{race['session_key']}.json", stamp)
        if not g or "stops" not in g:
            continue
        races += 1
        for x in g["stops"]:
            t = teams.setdefault(x["team"] or "?", {"team": x["team"], "colour": x["colour"], "stops": 0, "clear": 0, "lost": 0.0})
            t["stops"] += 1
            if x["cost"]:
                t["clear"] += 1
                t["lost"] += x["cost"]
    rows = sorted(teams.values(), key=lambda t: t["lost"] / t["stops"])
    for t in rows:
        t["lost"] = st._r(t["lost"], 2)
        t["per_stop"] = st._r(t["lost"] / t["stops"], 3)
    gradable = sum(1 for r in s.inputs.races if "skipped" not in s.fits.get(r["session_key"], {"skipped": 1}))
    return {"races": races, "of": len(s.inputs.races), "gradable": gradable, "teams": rows}


def report(year, rnd=None, upto=None, grade=False, progress=lambda m: None):
    """The strategy report for a compiled year: the season part, and one race (rnd, default the latest) in detail.
    grade: also grade that race's pit calls if they aren't cached yet."""
    db = sr.open_dataset(year)
    if db is None:
        return {"year": year, "exists": False}
    try:
        last = db.execute("SELECT MAX(round) FROM sessions WHERE session_name = 'Race'").fetchone()[0] or 0
        upto = last if upto is None else min(upto, last)
        if not upto:
            return {"year": year, "exists": True, "races": []}
        progress("fitting the lap-time model")
        s = _Season(db, upto)
        stamp = _stamp(db)
        progress("predicting every race and the title (first time only)")
        season = _cached(f"{year}-season-{upto}.json", stamp, lambda: _season(s))
        races = [{"round": r["round"], "name": r["meeting_name"], "circuit": r["circuit"]} for r in s.inputs.races]
        pick = next((r for r in s.inputs.races if r["round"] == rnd), s.inputs.races[-1])
        out = {"year": year, "exists": True, "upto": upto, "races": races, "season": season,
               "race": _race(s, pick, stamp, season, grade), "graded": _graded_summary(s, stamp)}
        return out
    finally:
        db.close()


def grade_all(year, progress=lambda m: None):
    """Grade every race's pit calls (the command line's --grade): about ten seconds a race the first time."""
    db = sr.open_dataset(year)
    if db is None:
        return 0
    try:
        last = db.execute("SELECT MAX(round) FROM sessions WHERE session_name = 'Race'").fetchone()[0] or 0
        s = _Season(db, last)
        stamp = _stamp(db)
        n = 0
        for race in s.inputs.races:
            progress(f"R{race['round']} {race['meeting_name']}")
            g = grade_race(s, race, stamp, progress=lambda m, r=race: progress(f"R{r['round']} {r['meeting_name']}: {m}"))
            n += "stops" in g
        return n
    finally:
        db.close()


def title_line(title, drivers, after):
    """[(code, chance)] for the title after a round, most likely first."""
    t = next((x for x in title if x["after"] == after), None)
    if not t:
        return []
    return sorted(((drivers.get(str(n), drivers.get(n, {})).get("code") or str(n), p) for n, p in t["drivers"].items()),
                  key=lambda x: -x[1])


def ci(p, n):
    """Half-width of a 95% interval for a proportion."""
    return 1.96 * math.sqrt(max(p * (1 - p), 1e-9) / n) if n else None
