"""Pre-race briefing: what's worth knowing in the last minutes before a race or sprint starts.

Qualifying recap and grid penalties, last year's race at this circuit (winner, where they started, how many
stops), the pit-stop time loss, and the championship going in. Built from cached calls and rebuilt every few
minutes at most (the official grid can be published late), so it costs next to nothing of the API budget.
"""
import logging
import time
from collections import Counter

from . import api
from .championship import max_remaining, sort_standings, title_status

log = logging.getLogger(__name__)
REBUILD = 300       # seconds between rebuilds


def picture(tr, season=None):
    """The briefing for LiveTracker `tr`, or None when it isn't a race that's still to start."""
    if not tr.is_race:
        return None
    cached = getattr(tr, "_prerace", None)
    if cached and time.time() - cached[0] < REBUILD:
        return cached[1]
    out = {"laps": tr.scheduled_laps,
           "pit_loss": ((tr.circuit or {}).get("pit_loss") or None)}
    for name, part in (("quali", _quali), ("last_year", _last_year), ("standings", _standings)):
        try:
            out[name] = part(tr, season)
        except Exception as e:      # any one part failing must never take the timing page down
            log.debug("Pre-race %s unavailable: %s", name, e)
            out[name] = None
    tr._prerace = (time.time(), out)
    return out


def _quali_session(tr):
    name = "Sprint Qualifying" if tr.is_sprint else "Qualifying"
    q = api.get("sessions", ttl=3600, meeting_key=tr.session["meeting_key"], session_name=name)
    if not q and tr.is_sprint:
        q = api.get("sessions", ttl=3600, meeting_key=tr.session["meeting_key"], session_name="Sprint Shootout")
    return q[0] if q else None


def _best(duration):
    """Qualifying duration is [Q1, Q2, Q3]: the time from the furthest segment reached."""
    if isinstance(duration, list):
        return next((t for t in reversed(duration) if t), None)
    return duration


def _quali(tr, season):
    q = _quali_session(tr)
    if not q:
        return None
    rows = [r for r in api.get("session_result", ttl=600, session_key=q["session_key"]) if r.get("position")]
    if not rows:
        return None
    rows.sort(key=lambda r: r["position"])
    times = {r["driver_number"]: {"pos": r["position"], "time": _best(r.get("duration"))} for r in rows}
    pole = rows[0]
    front = sorted(tr.grid, key=tr.grid.get)[:2] if tr.grid else [r["driver_number"] for r in rows[:2]]
    # penalties: drivers starting behind where they qualified (the cars that move up to fill the gap aren't news)
    drops = [{"code": tr.code(n), "colour": "#" + tr.colour(n), "quali": times[n]["pos"], "grid": g}
             for n, g in sorted(tr.grid.items(), key=lambda x: x[1]) if n in times and g > times[n]["pos"]]
    missing = [{"code": tr.code(n), "colour": "#" + tr.colour(n), "quali": times[n]["pos"]}
               for n in times if tr.grid and n not in tr.grid]     # not on the grid: pit-lane start or DNS
    return {"session": q.get("session_name"), "times": {str(n): v for n, v in times.items()},
            "pole": {"code": tr.code(pole["driver_number"]), "colour": "#" + tr.colour(pole["driver_number"]),
                     "time": times[pole["driver_number"]]["time"]},
            "front_row": [{"code": tr.code(n), "colour": "#" + tr.colour(n)} for n in front],
            "penalties": drops, "missing": missing}


def _last_year(tr, season):
    s = tr.session
    prev = api.get("sessions", ttl=24 * 3600, circuit_key=s["circuit_key"], session_name=s["session_name"],
                   year=s["year"] - 1)
    if not prev:
        return None
    key = prev[0]["session_key"]
    res = sorted((r for r in api.get("session_result", ttl=24 * 3600, session_key=key) if r.get("position")),
                 key=lambda r: r["position"])
    if not res:
        return None
    drivers = {d["driver_number"]: d for d in api.get("drivers", ttl=24 * 3600, session_key=key)}
    grid = {g["driver_number"]: g["position"] for g in api.get("starting_grid", ttl=24 * 3600, session_key=key)}
    if not grid:      # older seasons have no starting_grid: the qualifying order is close enough
        q = api.get("sessions", ttl=24 * 3600, meeting_key=prev[0]["meeting_key"],
                    session_name="Sprint Qualifying" if tr.is_sprint else "Qualifying")
        if q:
            grid = {r["driver_number"]: r["position"] for r in api.get("session_result", ttl=24 * 3600,
                                                                       session_key=q[0]["session_key"]) if r.get("position")}
    stints = {}
    for st in sorted(api.get("stints", ttl=24 * 3600, session_key=key), key=lambda x: x["stint_number"]):
        stints.setdefault(st["driver_number"], []).append(st.get("compound") or "UNKNOWN")
    top = [r["driver_number"] for r in res[:10]]
    stops = Counter(len(stints[n]) - 1 for n in top if stints.get(n))
    win = res[0]["driver_number"]
    d = drivers.get(win) or {}
    return {"year": s["year"] - 1,
            "winner": {"code": d.get("name_acronym") or str(win), "name": d.get("full_name"),
                       "team": d.get("team_name"), "colour": "#" + (d.get("team_colour") or "888888"),
                       "grid": grid.get(win), "stints": stints.get(win, [])},
            "laps": res[0].get("number_of_laps"),
            "stops": {str(k): v for k, v in sorted(stops.items())},     # top-10 finishers: number of stops -> drivers
            "dnfs": sum(1 for r in api.get("session_result", ttl=24 * 3600, session_key=key) if r.get("dnf"))}


def _standings(tr, season):
    if season is None or season.year != tr.session.get("year"):
        return None
    rnd = next((r for r in season.rounds if r.meeting_key == tr.session["meeting_key"]), None)
    if rnd is None:
        return None
    table = season.standings(upto=rnd.number - 1)[0]
    if rnd.has_sprint and not tr.is_sprint:          # Saturday's sprint already scored
        sprint = season.session_points(rnd.sprint)
        by_code = {e.code: e for e in table}
        for n, pts in sprint.items():
            e = by_code.get(season.driver(n, rnd.race).code)
            if e:
                e.points += pts
        table = sort_standings(table)
    if not table:
        return None
    later = [r.has_sprint for r in season.rounds if r.number > rnd.number and not r.cancelled]
    remaining = [tr.is_sprint] + later      # today's session (a sprint brings this weekend's GP with it), then the rest
    left = max_remaining(remaining, rules=season.rules)
    champ, contenders = title_status(table, remaining, rules=season.rules)
    lead = table[0].points
    return {"points_left": left, "rounds_left": len(later) + 1,
            "decided": champ.code if champ else None,
            "contenders": [e.code for e in contenders],
            "table": [{"code": e.code, "name": e.name, "team": e.team, "points": e.points,
                       "behind": lead - e.points} for e in table[:6]]}
