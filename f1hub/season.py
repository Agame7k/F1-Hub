"""Builds a season from real OpenF1 data: calendar, results, standings."""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import api
from .championship import Entry, sort_standings, title_status
from .rules import FASTEST_LAP_TOP, rules_for

HOUR = 3600
log = logging.getLogger("f1hub.season")


def _dt(s):
    return datetime.fromisoformat(s) if s else None


@dataclass
class Round:
    number: int
    meeting_key: int
    name: str
    country: str
    location: str
    circuit: str
    date: datetime
    sessions: dict = field(default_factory=dict)   # session_name -> session dict
    cancelled: bool = False

    @property
    def race(self):
        return self.sessions.get("Race")

    @property
    def sprint(self):
        return self.sessions.get("Sprint")

    @property
    def quali(self):
        return self.sessions.get("Qualifying")

    @property
    def has_sprint(self):
        return "Sprint" in self.sessions


@dataclass
class DriverInfo:
    number: int
    code: str
    name: str
    team: str
    colour: str


def _session_over(session, margin=HOUR):
    end = _dt(session["date_end"])
    return end and (datetime.now(timezone.utc) - end).total_seconds() > margin


def _ttl_for(session):
    # results can be amended by stewards for a while; after a few days treat as final
    end = _dt(session["date_end"])
    age = (datetime.now(timezone.utc) - end).total_seconds() if end else 0
    return api.FOREVER if age > 4 * 24 * HOUR else 15 * 60


def classified(row):
    """A result row's finishing position, or None if it doesn't score (DSQ, DNS, not classified)."""
    pos = row.get("position")
    return pos if isinstance(pos, int) and not row.get("dsq") and not row.get("dns") else None


def fastest_lap_point(rows, session, rules):
    """Number of the driver who scored the fastest-lap point in this race, or None.

    OpenF1's points field includes the bonus, so it's read from there: the top-10 finisher with one point more
    than the table gives. A few races have broken points (2023 Saudi Arabia: 0 for P2; 2023 USA: 0 for P2-P10);
    for those the fastest lap is looked up in the race's laps instead."""
    table = rules["gp_points"]
    holder, sound = None, True
    for r in rows:
        pos = classified(r)
        due = table[pos - 1] if pos and pos <= len(table) else 0
        got = r.get("points")
        if got is None:
            sound = False
        elif pos and pos <= FASTEST_LAP_TOP and got == due + 1:
            holder = r["driver_number"]
        elif got != due:
            sound = False
    if sound:
        return holder
    try:
        laps = [l for l in api.get("laps", ttl=_ttl_for(session), session_key=session["session_key"])
                if l.get("lap_duration") and not l.get("deleted")]
    except (api.APIError, api.LiveSessionLock):
        return None
    if not laps:
        return None
    best = min(laps, key=lambda l: l["lap_duration"])["driver_number"]
    pos = next((classified(r) for r in rows if r["driver_number"] == best), None)
    return best if pos and pos <= FASTEST_LAP_TOP else None


class Season:
    def __init__(self, year):
        self.year = year
        self.rules = rules_for(year)
        self.fastest_lap = {}   # race session_key -> number of the driver who scored the fastest-lap point
        self.rounds = []
        self.cancelled = []     # cancelled meetings (not numbered, like the official calendar)
        self.results = {}       # session_key -> list of result rows
        self.drivers = {}       # number -> DriverInfo (latest known)
        self.race_drivers = {}  # session_key -> {number: DriverInfo}  (team at the time)
        self.official = None    # official standings from championship_* endpoints, if available
        self.missing = []       # "Mexico City Grand Prix - Race": results that couldn't be fetched this time

    # ----------------------------------------------------------------- loading
    def load(self, progress=lambda msg: None):
        progress("calendar")
        meetings = api.get("meetings", ttl=6 * HOUR, year=self.year)
        sessions = api.get("sessions", ttl=6 * HOUR, year=self.year)
        by_meeting = {}
        for s in sessions:
            by_meeting.setdefault(s["meeting_key"], {})[s["session_name"]] = s
        n = 0
        for m in sorted(meetings, key=lambda m: m["date_start"]):
            ss = by_meeting.get(m["meeting_key"], {})
            if "Race" not in ss:          # skips pre-season testing
                continue
            rnd = Round(0, m["meeting_key"], m["meeting_name"], m["country_name"], m["location"],
                        m["circuit_short_name"], _dt(m["date_start"]), ss,
                        bool(m.get("is_cancelled") or ss["Race"].get("is_cancelled")))
            if rnd.cancelled:
                self.cancelled.append(rnd)
                continue
            n += 1
            rnd.number = n
            self.rounds.append(rnd)

        for rnd in self.rounds:
            for name in ("Sprint", "Race", "Qualifying", "Sprint Qualifying"):
                s = rnd.sessions.get(name)
                if not s or s.get("is_cancelled") or not _session_over(s, margin=0):
                    continue
                progress(f"{rnd.name} - {name}")
                try:
                    self._load_session(name, s)
                except (api.APIError, api.LiveSessionLock) as e:
                    # one result that can't be had right now (offline and not cached, OpenF1 locked for a live
                    # session) used to stop everything; the rest of the season is still worth showing
                    self.results.pop(s["session_key"], None)
                    self.race_drivers.pop(s["session_key"], None)
                    self.missing.append(f"{rnd.name} - {name}")
                    log.warning("No %s %s results yet: %s", rnd.name, name, str(e).splitlines()[0] if str(e) else e)
        self._load_official(progress)
        return self

    def _load_session(self, name, s):
        rows = api.get("session_result", ttl=_ttl_for(s), session_key=s["session_key"])
        if rows:
            self.results[s["session_key"]] = rows
        if name == "Race" and rows and self.rules["fastest_lap_point"]:
            holder = fastest_lap_point(rows, s, self.rules)
            if holder is not None:
                self.fastest_lap[s["session_key"]] = holder
        if name == "Race" and rows:
            drv = api.get("drivers", ttl=_ttl_for(s), session_key=s["session_key"])
            info = {d["driver_number"]: DriverInfo(
                d["driver_number"], d.get("name_acronym") or str(d["driver_number"]),
                d.get("full_name") or d.get("broadcast_name") or "", d.get("team_name") or "?",
                d.get("team_colour") or "FFFFFF") for d in drv}
            self.race_drivers[s["session_key"]] = info
            self.drivers.update(info)

    def _load_official(self, progress):
        last = self.completed_rounds()
        if not last:
            return
        key = last[-1].race["session_key"]
        progress("official standings")
        try:
            drv = api.get("championship_drivers", ttl=_ttl_for(last[-1].race), session_key=key)
            team = api.get("championship_teams", ttl=_ttl_for(last[-1].race), session_key=key)
        except (api.APIError, api.LiveSessionLock):
            return
        if drv:
            self.official = {
                "drivers": {r["driver_number"]: r["points_current"] for r in drv},
                "teams": {r["team_name"]: r["points_current"] for r in team or []},
            }

    # ----------------------------------------------------------------- queries
    def completed_rounds(self):
        return [r for r in self.rounds if r.race and r.race["session_key"] in self.results]

    def remaining_rounds(self):
        done = {r.number for r in self.completed_rounds()}
        return [r for r in self.rounds if r.number not in done and not r.cancelled]

    def result(self, session):
        return self.results.get(session["session_key"], []) if session else []

    def driver(self, number, session=None):
        if session and session["session_key"] in self.race_drivers:
            d = self.race_drivers[session["session_key"]].get(number)
            if d:
                return d
        return self.drivers.get(number) or DriverInfo(number, str(number), f"Car {number}", "?", "FFFFFF")

    def session_points(self, session):
        """{driver_number: points scored} in a race or sprint, with the fastest-lap point in seasons that had one."""
        if not session:
            return {}
        table = self.rules["sprint_points" if session.get("session_name") == "Sprint" else "gp_points"]
        out = {}
        for row in self.result(session):
            pos = classified(row)
            out[row["driver_number"]] = table[pos - 1] if pos and pos <= len(table) else 0
        holder = self.fastest_lap.get(session["session_key"])
        if holder in out:
            out[holder] += 1
        return out

    def team_colour(self, team):
        for d in self.drivers.values():
            if d.team == team:
                return d.colour
        return "FFFFFF"

    # ----------------------------------------------------------------- standings
    def standings(self, upto=None):
        """Computes driver + team standings from race and sprint results.
        upto: include rounds up to this round number (default: all completed).
        Returns (drivers, teams, by_number). A driver who raced under two numbers (a reserve's one-off, then a race
        seat) is one entry, reachable from both numbers."""
        drivers, teams, by_code = {}, {}, {}

        def entry(info):
            e = by_code.get(info.code)
            if e is None:
                e = by_code[info.code] = Entry(info.name, info.code, info.team)
            drivers[info.number] = e
            return e

        for rnd in self.completed_rounds():
            if upto and rnd.number > upto:
                break
            race = rnd.race
            for sess, is_gp in ((rnd.sprint, False), (race, True)):
                scored = self.session_points(sess)
                for row in self.result(sess):
                    info = self.driver(row["driver_number"], race)
                    e = entry(info)
                    e.team = info.team
                    t = teams.setdefault(info.team, Entry(info.team, info.team[:3].upper(), info.team))
                    pos = classified(row)
                    pts = scored.get(row["driver_number"], 0)
                    e.points += pts
                    t.points += pts
                    if is_gp:
                        e.finishes.append(pos)
                        t.finishes.append(pos)
                        e.starts += 0 if row.get("dns") else 1
                        if pos == 1:
                            e.wins += 1
                            t.wins += 1
                        if pos and pos <= 3:
                            e.podiums += 1
                            t.podiums += 1
                        if row.get("dnf"):
                            e.dnfs += 1
                            t.dnfs += 1
                    elif pos == 1:
                        e.sprint_wins += 1
            q = self.result(rnd.quali)
            pole = next((r for r in q if r.get("position") == 1), None)
            if pole:
                entry(self.driver(pole["driver_number"], race)).poles += 1
        return sort_standings(by_code.values()), sort_standings(teams.values()), drivers

    def official_check(self):
        """Compares our points with OpenF1's official championship tables.
        Returns list of mismatch strings (empty = all good), or None if unavailable."""
        if not self.official:
            return None
        _, teams, by_number = self.standings()
        issues = []
        for number, pts in self.official["drivers"].items():
            ours = by_number[number].points if number in by_number else 0
            if abs(ours - pts) > 0.01:
                issues.append(f"{self.driver(number).code}: computed {ours:g}, official {pts:g}")
        tmap = {t.name: t.points for t in teams}
        for team, pts in self.official["teams"].items():
            if team in tmap and abs(tmap[team] - pts) > 0.01:
                issues.append(f"{team}: computed {tmap[team]:g}, official {pts:g}")
        return issues

    def apply_official_points(self, drivers, teams, by_number):
        """Overwrite computed totals with the official ones (they include any penalties)."""
        if not self.official:
            return drivers, teams
        for number, pts in self.official["drivers"].items():
            if number in by_number:
                by_number[number].points = pts
        for t in teams:
            if t.name in self.official["teams"]:
                t.points = self.official["teams"][t.name]
        return sort_standings(drivers), sort_standings(teams)

    def title_timeline(self):
        """Per completed round: who was still mathematically in the title fight."""
        timeline = []
        done = self.completed_rounds()
        for rnd in done:
            drivers, teams, _ = self.standings(upto=rnd.number)
            remaining = [r.has_sprint for r in self.rounds
                         if r.number > rnd.number and not r.cancelled]
            dchamp, dcont = title_status(drivers, remaining, rules=self.rules)
            tchamp, tcont = title_status(teams, remaining, constructor=True, rules=self.rules)
            timeline.append((rnd, drivers, dchamp, dcont, teams, tchamp, tcont, remaining))
        return timeline
