"""Between sessions: analysis of any finished session, the telemetry lab, circuit previews and season stats.

Data comes from OpenF1 (2023 onwards). FastF1, when it's installed (f1hub/ff1.py), adds seasons from 2018,
smoother lap telemetry, and stands in while OpenF1 is locked during a live session.
"""
import re
from collections import Counter
from datetime import datetime, timezone
from statistics import median

from . import api, ff1
from .racecontrol import neutral_periods, neutralisation_counts
from .report import _slope
from .rules import rules_for


def _ts(s):
    return datetime.fromisoformat(s).timestamp() if s else None


# ------------------------------------------------------------------------------------------------ data source
class Source:
    """Rows for one session, wherever they come from: OpenF1, or FastF1 (older seasons / OpenF1 locked)."""

    def __init__(self, key):
        self.key = int(key)
        self.fastf1 = ff1.is_key(key)
        self.fallback = None              # set when OpenF1 is locked and FastF1 answers instead
        if self.fastf1:
            st = ff1.store(key)
            self.session, self.meeting = st.session, st.meeting
            return
        rows = api.get("sessions", ttl=3600, session_key=self.key)
        if not rows:
            raise api.APIError(f"No session {key}")
        self.session = rows[0]
        self.meeting = (api.get("meetings", ttl=6 * 3600, meeting_key=self.session["meeting_key"]) or [{}])[0]
        end = _ts(self.session.get("date_end")) or 0
        self.ttl = api.FOREVER if datetime.now(timezone.utc).timestamp() - end > 4 * 86400 else 600

    def get(self, endpoint, **params):
        if self.fastf1:
            return ff1.rows(self.key, endpoint, **params)
        if self.fallback:
            return ff1.rows(self.fallback, endpoint, **params)
        try:
            return api.get(endpoint, ttl=self.ttl, session_key=self.key, **params)
        except api.LiveSessionLock:
            if not ff1.available():
                raise
            self.fallback = ff1.key_for_openf1(self.session)       # same session, read through FastF1
            return ff1.rows(self.fallback, endpoint, **params)

    @property
    def via(self):
        return "fastf1" if self.fastf1 or self.fallback else "openf1"


def _drivers(src):
    out = {}
    for d in src.get("drivers"):
        out[d["driver_number"]] = {"number": d["driver_number"], "code": d.get("name_acronym") or str(d["driver_number"]),
                                   "name": d.get("full_name"), "team": d.get("team_name"),
                                   "colour": "#" + (d.get("team_colour") or "888888")}
    return out


# ------------------------------------------------------------------------------------------------ neutralised time
# (when: racecontrol.neutral_periods)
def _overlaps(a, b, spans):
    return any(a < e and b > s for s, e in spans)


# ------------------------------------------------------------------------------------------------ session analysis
WET_COMPOUNDS = ("INTERMEDIATE", "WET")
FUEL_EFFECT = 0.06      # s/lap a race car gains as fuel burns off (a typical estimate): added back to show tyre wear alone


def _box(xs):
    xs = sorted(xs)
    if not xs:
        return None
    q = lambda p: xs[min(len(xs) - 1, max(0, round(p * (len(xs) - 1))))]
    return {"min": xs[0], "q1": q(.25), "med": q(.5), "q3": q(.75), "max": xs[-1], "n": len(xs),
            "mean": round(sum(xs) / len(xs), 3)}


def session_analysis(key, before=None):
    """Pace, tyre degradation, lap chart, team-mate gaps and best laps for one session.
    before (ISO): only what had happened by then (spoiler mode while replaying the same session)."""
    src = Source(key)
    s = src.session
    cut = _ts(before) if before else None
    drivers = _drivers(src)
    is_race = s.get("session_type") == "Race"
    laps = [l for l in src.get("laps") if l.get("lap_number")]
    if cut:
        laps = [l for l in laps if l.get("date_start") and _ts(l["date_start"]) + (l.get("lap_duration") or 0) <= cut]
    rc = [m for m in src.get("race_control") if not cut or (_ts(m.get("date")) or 0) <= cut]
    pits = [p for p in src.get("pit") if not cut or (_ts(p.get("date")) or 0) <= cut]
    stints = src.get("stints")
    spans = neutral_periods(rc)
    pit_in = {(p["driver_number"], p.get("lap_number")) for p in pits}

    by = {}
    for l in laps:
        by.setdefault(l["driver_number"], {})[l["lap_number"]] = l

    # tyre age per lap from the stints
    comp_at = {}
    for st in stints:
        n = st["driver_number"]
        for ln in range(st.get("lap_start") or 1, (st.get("lap_end") or st.get("lap_start") or 1) + 1):
            comp_at[(n, ln)] = (st.get("compound") or "UNKNOWN",
                                (st.get("tyre_age_at_start") or 0) + ln - (st.get("lap_start") or 1), st.get("stint_number"))
    family = lambda n, ln: "wet" if (comp_at.get((n, ln)) or ("",))[0] in WET_COMPOUNDS else "dry"

    clean = {}          # number -> [(lap, time, "dry" / "wet")]
    series = {}
    for n, mine in by.items():
        pts = {}
        for ln, l in sorted(mine.items()):
            t = l.get("lap_duration")
            if not t:
                continue
            start = _ts(l.get("date_start"))
            neutral = bool(start and _overlaps(start, start + t, spans)) or bool(re.search(r"[4567]", l.get("track_status") or ""))
            bad = (is_race and ln == 1) or l.get("is_pit_out_lap") or (n, ln) in pit_in or neutral or l.get("deleted")
            series.setdefault(n, []).append([ln, t, 1 if bad else 0])
            if not bad:
                pts.setdefault(family(n, ln), []).append((ln, t))
        # slow laps are judged against laps on the same kind of tyre: on intermediates a lap is 10 s+ slower, and
        # one median over a wet-to-dry race would throw every wet lap out
        for fam, fpts in pts.items():
            med = median(t for _, t in fpts)
            clean.setdefault(n, []).extend((ln, t, fam) for ln, t in fpts if t <= med * 1.07)
        if n in clean:
            clean[n].sort()
    fam_laps = Counter(f for pts in clean.values() for _, _, f in pts)
    main = "wet" if fam_laps["wet"] > fam_laps["dry"] else "dry"
    mixed = min(fam_laps["wet"], fam_laps["dry"]) >= 0.1 * max(1, sum(fam_laps.values()))

    pace = []
    for n, pts in clean.items():
        b = _box([t for _, t, f in pts if f == main])     # pace on the tyres most of the session was run on
        if b and b["n"] >= 3:
            d = drivers.get(n, {})
            pace.append(dict(d, number=n, **b))
    pace.sort(key=lambda r: r["med"])

    deg = []
    for st in sorted(stints, key=lambda x: (x["driver_number"], x["stint_number"])):
        n = st["driver_number"]
        pts = [(comp_at[(n, ln)][1], t) for ln, t, _ in clean.get(n, []) if comp_at.get((n, ln), (None, None, None))[2] == st["stint_number"]]
        if len(pts) < 5:
            continue
        slope = _slope([a for a, _ in pts], [t for _, t in pts])
        d = drivers.get(n, {})
        deg.append({"number": n, "code": d.get("code"), "colour": d.get("colour"), "stint": st["stint_number"],
                    "compound": st.get("compound"), "laps": len(pts), "deg": round(slope, 3) if slope is not None else None,
                    "deg_fuel": round(slope + FUEL_EFFECT, 3) if slope is not None and is_race else None,
                    "avg": round(sum(t for _, t in pts) / len(pts), 3), "points": [[a, t] for a, t in pts]})

    # best laps, theoretical bests, top speeds
    best = []
    for n, mine in by.items():
        timed = [l for l in mine.values() if l.get("lap_duration") and not l.get("deleted")]
        if not timed:
            continue
        b = min(timed, key=lambda l: l["lap_duration"])
        secs = [min((l.get(f"duration_sector_{i}") for l in mine.values() if l.get(f"duration_sector_{i}")), default=None)
                for i in (1, 2, 3)]
        d = drivers.get(n, {})
        best.append(dict(d, number=n, best=b["lap_duration"], lap=b["lap_number"],
                         compound=(comp_at.get((n, b["lap_number"])) or ("?",))[0],
                         ideal=round(sum(secs), 3) if all(secs) else None, sectors=secs,
                         top_speed=max((l.get("st_speed") or 0 for l in mine.values()), default=None) or None,
                         laps=len(mine)))
    best.sort(key=lambda r: r["best"])
    if best:
        for r in best:
            r["gap"] = round(r["best"] - best[0]["best"], 3)

    # lap chart: position at the end of every lap
    chart = []
    if is_race:
        log = {}
        for p in sorted(src.get("position"), key=lambda p: p["date"]):
            if not cut or _ts(p["date"]) <= cut:
                log.setdefault(p["driver_number"], []).append((_ts(p["date"]), p["position"]))
        for n, mine in by.items():
            pts, hist = [], log.get(n, [])
            for ln, l in sorted(mine.items()):
                end = _ts(l.get("date_start")) and _ts(l["date_start"]) + (l.get("lap_duration") or 0)
                if not end or not l.get("lap_duration"):
                    continue
                pos = None
                for t, p in hist:
                    if t > end:
                        break
                    pos = p
                if pos:
                    pts.append([ln, pos])
            if pts:
                chart.append(dict(drivers.get(n, {"code": str(n), "colour": "#888"}), number=n, points=pts))

    # team-mates: best lap (one-lap pace) and median clean lap (long-run pace)
    teams = {}
    for n, d in drivers.items():
        teams.setdefault(d.get("team") or "?", []).append(n)
    bestmap = {r["number"]: r for r in best}
    pacemap = {r["number"]: r for r in pace}
    mates = []
    for team, ns in teams.items():
        ns = [n for n in ns if n in bestmap]
        if len(ns) < 2:
            continue
        a, b = sorted(ns, key=lambda n: bestmap[n]["best"])[:2]
        row = {"team": team, "colour": drivers[a]["colour"], "a": drivers[a]["code"], "b": drivers[b]["code"],
               "best_gap": round(bestmap[b]["best"] - bestmap[a]["best"], 3)}
        if a in pacemap and b in pacemap:
            row["pace_gap"] = round(pacemap[b]["med"] - pacemap[a]["med"], 3)
        mates.append(row)
    mates.sort(key=lambda r: r["best_gap"])

    strategy = {}
    for st in sorted(stints, key=lambda x: x["stint_number"]):
        if cut and (st.get("lap_start") or 0) > max((l["lap_number"] for l in by.get(st["driver_number"], {}).values()), default=0):
            continue
        strategy.setdefault(st["driver_number"], []).append({"compound": st.get("compound"), "from": st.get("lap_start"),
                                                             "to": st.get("lap_end"), "age": st.get("tyre_age_at_start")})
    order = [r["number"] for r in (pace if is_race else best)]
    order += [n for n in drivers if n not in order]
    return {
        "session": {"key": src.key, "name": s.get("session_name"), "type": s.get("session_type"), "year": s.get("year"),
                    "meeting": src.meeting.get("meeting_name") or s.get("location"), "start": s.get("date_start"),
                    "circuit": s.get("circuit_short_name")},
        "source": src.via, "cut": before, "drivers": drivers, "order": order,
        # wet and dry running both: the track drying (or getting wetter) swamps tyre wear in the degradation
        "conditions": {"mixed": mixed, "pace_on": main, "wet_laps": fam_laps["wet"], "dry_laps": fam_laps["dry"]},
        "pace": pace, "deg": deg, "best": best, "lap_chart": chart, "teammates": mates,
        "laps": {str(n): v for n, v in series.items()},
        "strategy": [dict(drivers.get(n, {"code": str(n), "colour": "#888"}), number=n, stints=v)
                     for n, v in sorted(strategy.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)],
        "neutral": [[s_, None if e == float("inf") else e] for s_, e in spans],
    }


# ------------------------------------------------------------------------------------------------ telemetry lab
def lab_laps(key):
    """Every driver's laps in a session (for the lap pickers)."""
    src = Source(key)
    drivers = _drivers(src)
    out = {}
    for l in src.get("laps"):
        if l.get("lap_number"):
            out.setdefault(l["driver_number"], []).append({"lap": l["lap_number"], "t": l.get("lap_duration"),
                                                           "pit": bool(l.get("is_pit_out_lap"))})
    comp = {}
    for st in src.get("stints"):
        for ln in range(st.get("lap_start") or 1, (st.get("lap_end") or 0) + 1):
            comp[(st["driver_number"], ln)] = st.get("compound")
    rows = []
    for n, laps in out.items():
        laps.sort(key=lambda x: x["lap"])
        for x in laps:
            x["compound"] = comp.get((n, x["lap"]))
        timed = [x["t"] for x in laps if x["t"]]
        rows.append(dict(drivers.get(n, {"code": str(n), "colour": "#888", "number": n}), number=n,
                         best=min(timed) if timed else None, laps=laps))
    rows.sort(key=lambda r: r["best"] or 1e9)
    s = src.session
    return {"session": {"key": src.key, "name": s.get("session_name"), "year": s.get("year"),
                        "meeting": src.meeting.get("meeting_name") or s.get("location")},
            "source": src.via, "drivers": rows, "fastf1": ff1.available()}


def lab_trace(key, driver, lap="best", src="auto", before=None):
    """One lap's telemetry. src: openf1 / fastf1 / auto (FastF1 for its own sessions, else OpenF1)."""
    from .laptrace import lap_trace
    if ff1.is_key(key):
        return ff1.lap_trace(key, driver, lap, before)
    if src == "fastf1":
        sess = api.get("sessions", ttl=3600, session_key=int(key))
        if not sess:
            raise api.APIError(f"No session {key}")
        return ff1.lap_trace(None, driver, lap, before, openf1_session=sess[0])
    try:
        tr = lap_trace(int(key), int(driver), lap, before)
    except api.LiveSessionLock:
        if not ff1.available():
            raise
        sess = api.get("sessions", ttl=3600, session_key=int(key))
        return ff1.lap_trace(None, driver, lap, before, openf1_session=sess[0])
    if tr:
        total = None
        s = tr["samples"]
        if len(s) > 2:      # add distance in metres: location units are 1/10 m
            dist = 0.0
            prev = None
            for r in s:
                if prev:
                    dist += ((r[6] - prev[6]) ** 2 + (r[7] - prev[7]) ** 2) ** 0.5
                prev = r
            total = dist / 10
            for r in s:
                r.append(round(r[1] * total, 1))
        tr.update(length_m=round(total) if total else None, source="openf1",
                  fields=tr["fields"] + (["dist"] if total else []))
    return tr


# ------------------------------------------------------------------------------------------------ circuit preview
def circuit_info(url):
    """Outline, rotation, corners and pit loss from the circuit data OpenF1 links to (MultiViewer)."""
    if not url:
        return None
    try:
        d = api.get_external(url)
    except api.APIError:
        return None
    pts = list(zip(d.get("x") or [], d.get("y") or []))
    if len(pts) < 50:
        return None
    loss = d.get("pitLoss") or {}
    numf = lambda v: float(v) if v not in (None, "") else None
    return {"outline": [list(p) for p in pts], "rotation": d.get("rotation") or 0,
            "corners": [{"number": c["number"], "x": c["trackPosition"]["x"], "y": c["trackPosition"]["y"],
                         "angle": c.get("angle", 0)} for c in d.get("corners") or []],
            "pit_loss": {"normal": numf(loss.get("normal")), "sc": numf(loss.get("sc")), "vsc": numf(loss.get("vsc"))}}


def _edition(race, now):
    """One past race at this circuit: podium, pole, fastest lap, strategy, safety cars, weather (OpenF1)."""
    key = race["session_key"]
    g = lambda ep, **kw: api.get(ep, ttl=api.FOREVER, session_key=key, **kw)
    drivers = {d["driver_number"]: d for d in g("drivers")}
    info = lambda n: {"code": (drivers.get(n) or {}).get("name_acronym") or str(n),
                      "name": (drivers.get(n) or {}).get("full_name"), "team": (drivers.get(n) or {}).get("team_name"),
                      "colour": "#" + ((drivers.get(n) or {}).get("team_colour") or "888888")}
    res = sorted((r for r in g("session_result") if isinstance(r.get("position"), int)), key=lambda r: r["position"])
    out = {"year": race["year"], "key": key, "podium": [dict(info(r["driver_number"]), pos=r["position"]) for r in res[:3]],
           "laps": res[0].get("number_of_laps") if res else None}
    grid = {r["driver_number"]: r["position"] for r in g("starting_grid")}
    q = api.get("sessions", ttl=api.FOREVER, meeting_key=race["meeting_key"], session_name="Qualifying")
    if q and not grid:      # no starting grid in OpenF1: the qualifying order stands in (penalties not applied)
        grid = {r["driver_number"]: r["position"] for r in api.get("session_result", ttl=api.FOREVER, session_key=q[0]["session_key"])
                if isinstance(r.get("position"), int)}
        out["winner_grid_from"] = "quali"
    if res and grid:
        out["winner_grid"] = grid.get(res[0]["driver_number"])
    if q:
        qr = api.get("session_result", ttl=api.FOREVER, session_key=q[0]["session_key"], position=1)
        if qr:
            dur = qr[0].get("duration")
            t = next((x for x in reversed(dur) if x), None) if isinstance(dur, list) else dur
            qd = {d["driver_number"]: d for d in api.get("drivers", ttl=api.FOREVER, session_key=q[0]["session_key"])}
            d = qd.get(qr[0]["driver_number"]) or {}
            out["pole"] = {"code": d.get("name_acronym"), "colour": "#" + (d.get("team_colour") or "888888"), "time": t}
    laps = [l for l in g("laps") if l.get("lap_duration")]
    if laps:
        fl = min(laps, key=lambda l: l["lap_duration"])
        out["fastest"] = dict(info(fl["driver_number"]), time=fl["lap_duration"], lap=fl["lap_number"])
    stints = g("stints")
    per = {}
    for st in sorted(stints, key=lambda x: x["stint_number"]):
        per.setdefault(st["driver_number"], []).append(st.get("compound") or "?")
    finishers = {r["driver_number"] for r in res if not r.get("dnf")}
    stops = Counter(len(v) - 1 for n, v in per.items() if n in finishers)
    seqs = Counter("-".join(c[0] for c in v) for n, v in per.items() if n in finishers)
    out["stops"] = {str(k): v for k, v in sorted(stops.items())}
    out["sequences"] = seqs.most_common(4)
    if res:
        out["winner_strategy"] = per.get(res[0]["driver_number"])
    out.update(neutralisation_counts(g("race_control")))
    try:
        out["overtakes"] = len(g("overtakes"))
    except api.APIError:
        pass
    w = g("weather")
    if w:
        tt = [x["track_temperature"] for x in w if x.get("track_temperature") is not None]
        out["weather"] = {"rain": any(x.get("rainfall") for x in w), "track": round(sum(tt) / len(tt), 1) if tt else None}
    return out


def circuit_preview(meeting_key=None, year=None):
    """What to expect at a circuit: the map, past winners, poles, fastest laps, typical strategy, safety cars."""
    now = datetime.now(timezone.utc)
    year = year or now.year
    meetings = api.get("meetings", ttl=6 * 3600, year=year)
    if meeting_key:
        m = next((x for x in meetings if x["meeting_key"] == int(meeting_key)), None)
    else:      # the next meeting that hasn't finished
        upcoming = [x for x in meetings if not x.get("is_cancelled") and "test" not in (x.get("meeting_name") or "").lower()
                    and _ts(x.get("date_end") or x["date_start"]) > now.timestamp()]
        m = min(upcoming, key=lambda x: x["date_start"]) if upcoming else None
        if m is None and year == now.year:
            return circuit_preview(None, year + 1) if year + 1 <= now.year + 1 else None
    if not m:
        return None
    races = []
    for y in range(2023, now.year + 1):
        try:
            rs = api.get("sessions", ttl=api.FOREVER if y < now.year else 6 * 3600, year=y,
                         circuit_key=m["circuit_key"], session_name="Race")
        except api.APIError:
            continue
        races += [r for r in rs if _ts(r["date_end"]) < now.timestamp() - 3600 and not r.get("is_cancelled")]
    editions = []
    for r in sorted(races, key=lambda r: r["date_start"], reverse=True)[:4]:
        try:
            editions.append(_edition(r, now))
        except api.APIError as e:
            editions.append({"year": r["year"], "error": str(e)})
    agg_stops = Counter()
    for e in editions:
        for k, v in (e.get("stops") or {}).items():
            agg_stops[k] += v
    sessions = api.get("sessions", ttl=3600, meeting_key=m["meeting_key"])
    return {
        "meeting": {"key": m["meeting_key"], "name": m.get("meeting_name"), "official": m.get("meeting_official_name"),
                    "location": m.get("location"), "country": m.get("country_name"), "circuit": m.get("circuit_short_name"),
                    "circuit_key": m.get("circuit_key"), "year": m.get("year"), "start": m.get("date_start"),
                    "circuit_type": m.get("circuit_type")},
        "schedule": [{"key": s["session_key"], "name": s["session_name"], "start": s["date_start"], "end": s.get("date_end")}
                     for s in sorted(sessions, key=lambda s: s["date_start"])],
        "map": circuit_info(m.get("circuit_info_url")),
        "editions": editions, "stops": dict(agg_stops),
        "fastf1": ff1.available(),
    }


def circuit_history(name, location, upto_year):
    """Older winners at the circuit (FastF1, 2018-2022): matched by Grand Prix name, then by location."""
    out = []
    norm = lambda x: re.sub(r"[^a-z]", "", (x or "").lower())
    for y in range(ff1.FIRST_YEAR, min(upto_year, 2023)):
        try:
            sch = ff1.schedule(y)
        except ff1.FF1Error:
            continue
        race = next((s for s in sch["sessions"] if s["session_name"] == "Race" and norm(s.get("meeting_name")) == norm(name)), None) \
            or next((s for s in sch["sessions"] if s["session_name"] == "Race" and norm(s.get("location")) == norm(location)), None)
        if not race:
            continue
        try:
            res = ff1._disk_cache(f"results-{race['session_key']}.json", None,
                                  lambda: ff1.QUICK.call("results", timeout=300, ref={"year": y, "round": race["round"], "idx": race["idx"]}))
        except ff1.FF1Error as e:
            out.append({"year": y, "error": str(e)})
            continue
        rows = sorted((r for r in res["results"] if r.get("pos")), key=lambda r: r["pos"])
        for r in res["results"]:
            r["colour"] = team_colour(r.get("team"), r.get("colour"))
        pole = next((r for r in res["results"] if r.get("grid") == 1), None)
        out.append({"year": y, "key": race["session_key"], "podium": rows[:3], "laps": rows[0].get("laps") if rows else None,
                    "pole": {"code": pole["code"], "colour": pole["colour"]} if pole else None,
                    "winner_grid": rows[0].get("grid") if rows else None})
    return out[::-1]


# ------------------------------------------------------------------------------------------------ season stats
TEAM_COLOURS = {"red_bull": "3671C6", "mercedes": "27F4D2", "ferrari": "E8002D", "mclaren": "FF8000",
                "alpine": "FF87BC", "renault": "FFF500", "aston_martin": "229971", "racing_point": "F596C8",
                "force_india": "F596C8", "alphatauri": "6692FF", "toro_rosso": "469BFF", "rb": "6692FF",
                "alfa": "C92D4B", "kick": "52E252", "sauber": "52E252", "haas": "B6BABD", "williams": "64C4FF"}


def team_colour(team, given=None):
    """F1's colour when the archive has one, else the team's usual colour (older seasons have none)."""
    if given and given.lstrip("#").lower() not in ("888888", "nan", "none", ""):
        return given
    key = re.sub(r"[^a-z]+", "_", (team or "").lower()).strip("_")
    hit = next((v for k, v in TEAM_COLOURS.items() if key.startswith(k) or k in key), None)
    return "#" + (hit or "888888")


def _norm_openf1(season, upto=None):
    """Season (OpenF1) -> the plain per-round structure season_stats works on."""
    rounds = []
    for rnd in season.completed_rounds():
        if upto and rnd.number > upto:
            break

        def rows(sess, grid=None, scores=False):
            # points come from the season's own tally (the table, plus the fastest-lap point where it applied):
            # OpenF1's points field has errors (2023 Saudi Arabia: 0 points for P2)
            scored = season.session_points(sess) if scores else {}
            out = []
            for r in season.result(sess):
                d = season.driver(r["driver_number"], rnd.race)
                st = "DSQ" if r.get("dsq") else "DNS" if r.get("dns") else "DNF" if r.get("dnf") else None
                pos = r.get("position") if isinstance(r.get("position"), int) else None     # ("RT" etc. come through as text)
                if pos is None and not st:
                    st = "DNF"
                out.append({"code": d.code, "name": d.name, "team": d.team, "colour": "#" + d.colour,
                            "pos": pos if not st else None, "grid": (grid or {}).get(r["driver_number"]),
                            "points": scored.get(r["driver_number"]) if scores else None, "status": st})
            return out
        try:
            grid = {g["driver_number"]: g["position"] for g in
                    api.get("starting_grid", ttl=api.FOREVER, session_key=rnd.race["session_key"])
                    if isinstance(g.get("position"), int)}          # (pit-lane starts come through as text)
        except api.APIError:
            grid = {}
        if not grid:        # OpenF1 often has no starting grid: the qualifying order is the next best thing
            grid = {r["driver_number"]: r["position"] for r in season.result(rnd.quali) if isinstance(r.get("position"), int)}
        rounds.append({"round": rnd.number, "name": rnd.name, "race": rows(rnd.race, grid, scores=True),
                       "sprint": rows(rnd.sprint, scores=True), "quali": rows(rnd.quali)})
    return rounds


def _norm_ergast(data):
    rounds = []
    for r in data["rounds"]:
        def rows(lst):
            out = []
            for x in lst:
                txt = x.get("pos_text") or ""
                st = None if txt.isdigit() or not txt else "DSQ" if txt == "D" else "DNS" if txt in ("W", "F") else "DNF"
                colour = team_colour(x.get("team_id") or x.get("team"))
                out.append({"code": x["code"], "name": x["name"], "team": x["team"], "colour": colour,
                            "pos": x["pos"] if not st else None, "grid": x.get("grid") or None, "points": x.get("points"),
                            "status": st})
            return out
        rounds.append({"round": r["round"], "name": r["name"], "race": rows(r["race"]), "sprint": rows(r["sprint"]),
                       "quali": rows(r["quali"])})
    return rounds


def season_stats(rounds, year, official=None):
    """Form, averages, team-mate head-to-heads and points progression from per-round classifications.
    official: {code: points} - the official totals (with fastest-lap points and penalties) replace the computed ones."""
    rules = rules_for(year)
    gp, sp = rules["gp_points"], rules["sprint_points"]
    drv, teams = {}, {}
    for rnd in rounds:
        qpos = {r["code"]: r["pos"] for r in rnd["quali"] if r["pos"]}
        weekend = {}
        for kind in ("sprint", "race"):
            for r in rnd[kind]:
                table = gp if kind == "race" else sp
                pts = r["points"] if r.get("points") is not None else \
                    (table[r["pos"] - 1] if r["pos"] and r["pos"] <= len(table) else 0)
                weekend[r["code"]] = weekend.get(r["code"], 0) + (pts or 0)
        for r in rnd["race"]:
            e = drv.setdefault(r["code"], {"code": r["code"], "name": r["name"], "team": r["team"], "colour": r["colour"],
                                           "points": 0, "wins": 0, "podiums": 0, "poles": 0, "dnfs": 0, "starts": 0,
                                           "finishes": [], "grids": [], "qualis": [], "gained": [], "q3": 0,
                                           "points_finishes": 0, "by_round": [], "form": []})
            e["team"], e["colour"] = r["team"], r["colour"]
            e["starts"] += 0 if r["status"] == "DNS" else 1
            e["points"] += weekend.get(r["code"], 0)
            e["by_round"].append([rnd["round"], round(e["points"], 1)])
            e["form"].append(r["pos"] or r["status"])
            if r["pos"]:
                e["finishes"].append(r["pos"])
                e["wins"] += r["pos"] == 1
                e["podiums"] += r["pos"] <= 3
                e["points_finishes"] += r["pos"] <= 10
                if isinstance(r.get("grid"), int) and r["grid"] > 0:
                    e["gained"].append(r["grid"] - r["pos"])
            elif r["status"] == "DNF":
                e["dnfs"] += 1
            if isinstance(r.get("grid"), int) and r["grid"] > 0:
                e["grids"].append(r["grid"])
            q = qpos.get(r["code"])
            if q:
                e["qualis"].append(q)
                e["poles"] += q == 1
                e["q3"] += q <= 10
            t = teams.setdefault(r["team"], {"team": r["team"], "colour": r["colour"], "points": 0, "by_round": [], "_r": {}})
            t["_r"][rnd["round"]] = t["_r"].get(rnd["round"], 0) + weekend.get(r["code"], 0)
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
    out = []
    for e in drv.values():
        out.append({**{k: v for k, v in e.items() if k not in ("finishes", "grids", "qualis", "gained", "form")},
                    "avg_finish": avg(e["finishes"]), "avg_grid": avg(e["grids"]), "avg_quali": avg(e["qualis"]),
                    "gained": sum(e["gained"]), "avg_gained": avg(e["gained"]),
                    "best": min(e["finishes"]) if e["finishes"] else None, "form": e["form"][-5:],
                    "ppr": round(e["points"] / max(1, len(e["by_round"])), 2), "points": round(e["points"], 1)})
    if official:
        for e in out:
            if e["code"] in official:
                e["points"] = official[e["code"]]
    out.sort(key=lambda e: (-e["points"], -e["wins"]))
    for t in teams.values():
        acc = 0
        for rn in sorted(t["_r"]):
            acc += t["_r"][rn]
            t["by_round"].append([rn, round(acc, 1)])
        t["points"] = round(acc, 1)
        del t["_r"]
    # team-mate head-to-heads (the two drivers with most starts for each team)
    h2h = []
    for team in teams:
        ds = sorted((e for e in drv.values() if e["team"] == team), key=lambda e: -e["starts"])[:2]
        if len(ds) < 2:
            continue
        a, b = ds[0]["code"], ds[1]["code"]
        q = [0, 0]
        r_ = [0, 0]
        for rnd in rounds:
            qp = {x["code"]: x["pos"] for x in rnd["quali"] if x["pos"]}
            rp = {x["code"]: x["pos"] or 99 for x in rnd["race"] if x["status"] != "DNS"}
            if a in qp and b in qp:
                q[0 if qp[a] < qp[b] else 1] += 1
            if a in rp and b in rp and (rp[a] < 99 or rp[b] < 99):
                r_[0 if rp[a] < rp[b] else 1] += 1
        h2h.append({"team": team, "colour": teams[team]["colour"], "a": a, "b": b, "quali": q, "race": r_,
                    "points": [round(drv[a]["points"], 1), round(drv[b]["points"], 1)]})
    h2h.sort(key=lambda x: -sum(x["points"]))
    return {"year": year, "rounds": [{"round": r["round"], "name": r["name"]} for r in rounds],
            "drivers": out, "teams": sorted(teams.values(), key=lambda t: -t["points"]), "h2h": h2h}
