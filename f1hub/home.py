"""The home page between sessions: what's on now, what's next, the weekend schedule and the latest results.

Everything comes from OpenF1's calendar (sessions) and results, cached, so the page costs a handful of
requests an hour - not the per-second polling of live timing.
"""
from datetime import datetime, timedelta, timezone

from . import api

LIVE_BEFORE = timedelta(minutes=15)     # a session counts as "on" from shortly before the start...
LIVE_AFTER = timedelta(minutes=30)      # ...until a while after the scheduled end (overruns, podium)
NAMES = {}                              # meeting_key -> official meeting name ("Abu Dhabi Grand Prix")


def _dt(s):
    return datetime.fromisoformat(s) if s else None


def _session(x, rounds):
    return {"key": x["session_key"], "name": x.get("session_name"), "type": x.get("session_type"),
            "meeting": NAMES.get(x.get("meeting_key")) or _meeting_name(x), "meeting_key": x.get("meeting_key"), "location": x.get("location"),
            "country": x.get("country_name"), "circuit": x.get("circuit_short_name"),
            "start": x["date_start"], "end": x.get("date_end"), "round": rounds.get(x.get("meeting_key"))}


def _meeting_name(x):     # fallback when the meetings list isn't available
    return f"{x.get('location') or x.get('country_name')} Grand Prix" if x.get("location") else x.get("country_name")


def _lap_time(v):
    if isinstance(v, list):                 # qualifying: [Q1, Q2, Q3] - the furthest part reached
        v = next((t for t in reversed(v) if t), None)
    return v


def _results(sess, rounds, limit=None):
    rows = api.get("session_result", ttl=600, session_key=sess["session_key"])
    if not rows:
        return None
    drivers = {d["driver_number"]: d for d in api.get("drivers", ttl=3600, session_key=sess["session_key"])}
    out = []
    for r in sorted(rows, key=lambda r: (r.get("position") is None, r.get("position") or 99)):
        d = drivers.get(r["driver_number"], {})
        status = "DSQ" if r.get("dsq") else "DNS" if r.get("dns") else "DNF" if r.get("dnf") else None
        gap = r.get("gap_to_leader")
        out.append({"pos": r.get("position"), "number": r["driver_number"],
                    "code": d.get("name_acronym") or str(r["driver_number"]), "name": d.get("full_name"),
                    "first": d.get("first_name"), "last": d.get("last_name"),
                    "team": d.get("team_name"), "colour": "#" + (d.get("team_colour") or "888888"),
                    "headshot": d.get("headshot_url"), "laps": r.get("number_of_laps"),
                    "points": r.get("points"), "status": status,
                    "time": _lap_time(r.get("duration")), "gap": _lap_time(gap) if isinstance(gap, list) else gap})
    return dict(_session(sess, rounds), results=out[:limit] if limit else out)


def _calendar_rounds(sessions, now):
    """meeting_key -> round number, and how many rounds are done, from the calendar alone - numbered the way
    Season numbers them (meetings with a race; cancelled ones aren't counted). For while the season loads."""
    races = sorted((x for x in sessions if x.get("session_name") == "Race"), key=lambda x: x["date_start"])
    rounds = {x["meeting_key"]: i + 1 for i, x in enumerate(races)}
    return rounds, len(races), sum(1 for x in races if x.get("date_end") and _dt(x["date_end"]) < now)


def home(season, now=None, year=None):
    """The home page. `season` may be None while it loads: the calendar is enough for most of the page."""
    now = now or datetime.now(timezone.utc)
    year = season.year if season else (year or now.year)
    try:
        NAMES.update({m["meeting_key"]: m.get("meeting_name") for m in api.get("meetings", ttl=6 * 3600, year=year)})
    except (api.APIError, api.LiveSessionLock):
        pass
    sessions = [x for x in api.get("sessions", ttl=3600, year=year) if not x.get("is_cancelled")]
    sessions.sort(key=lambda x: x["date_start"])
    if season:
        rounds = {r.meeting_key: r.number for r in season.rounds}
        total, done_rounds = len(season.rounds), len(season.completed_rounds())
    else:
        rounds, total, done_rounds = _calendar_rounds(sessions, now)
    if not any(_dt(x["date_start"]) > now for x in sessions):     # season over: look at next year's calendar
        try:
            sessions += [x for x in api.get("sessions", ttl=6 * 3600, year=year + 1) if not x.get("is_cancelled")]
        except (api.APIError, api.LiveSessionLock):
            pass
    live = next((x for x in sessions if _dt(x["date_start"]) - LIVE_BEFORE <= now <= _dt(x["date_end"]) + LIVE_AFTER), None)
    nxt = next((x for x in sessions if _dt(x["date_start"]) > now), None)
    done = [x for x in sessions if _dt(x["date_end"]) < now]

    def weekend(meeting_key):
        return [dict(_session(x, rounds), done=_dt(x["date_end"]) < now) for x in sessions if x["meeting_key"] == meeting_key]

    last_race = next((x for x in reversed(done) if x.get("session_name") == "Race"), None)
    last_any = done[-1] if done else None
    out = {
        "now": now.isoformat(), "year": year,
        "state": "live" if live else "between" if nxt else "offseason",
        "live": _session(live, rounds) if live else None,
        "next": _session(nxt, rounds) if nxt else None,
        "next_weekend": weekend(nxt["meeting_key"]) if nxt else [],
        "this_weekend": weekend(last_any["meeting_key"]) if last_any and (not nxt or nxt["meeting_key"] != last_any["meeting_key"]) else [],
        "last_race": _results(last_race, rounds) if last_race else None,
        "last_session": None,
        "recent": [_session(x, rounds) for x in reversed(done[-8:])],
        "rounds": total, "rounds_done": done_rounds,
    }
    if last_any and last_any is not last_race and last_any.get("session_type") in ("Qualifying", "Race"):
        out["last_session"] = _results(last_any, rounds, limit=10)     # e.g. qualifying on Saturday night
    return out
