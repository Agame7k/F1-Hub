"""Builds a season from real OpenF1 data: calendar, results, standings."""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import api
from .championship import Entry, sort_standings, title_status
from .rules import RULES

HOUR = 3600


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


class Season:
    def __init__(self, year):
        self.year = year
        self.rounds = []
        self.cancelled = []     # cancelled meetings (not numbered, like the official calendar)
        self.results = {}       # session_key -> list of result rows
        self.drivers = {}       # number -> DriverInfo (latest known)
        self.race_drivers = {}  # session_key -> {number: DriverInfo}  (team at the time)
        self.official = None    # official standings from championship_* endpoints, if available

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
                rows = api.get("session_result", ttl=_ttl_for(s), session_key=s["session_key"])
                if rows:
                    self.results[s["session_key"]] = rows
                if name == "Race" and rows:
                    drv = api.get("drivers", ttl=_ttl_for(s), session_key=s["session_key"])
                    info = {d["driver_number"]: DriverInfo(
                        d["driver_number"], d.get("name_acronym") or str(d["driver_number"]),
                        d.get("full_name") or d.get("broadcast_name") or "", d.get("team_name") or "?",
                        d.get("team_colour") or "FFFFFF") for d in drv}
                    self.race_drivers[s["session_key"]] = info
                    self.drivers.update(info)
        self._load_official(progress)
        return self

    def _load_official(self, progress):
        last = self.completed_rounds()
        if not last:
            return
        key = last[-1].race["session_key"]
        progress("official standings")
        try:
            drv = api.get("championship_drivers", ttl=_ttl_for(last[-1].race), session_key=key)
            team = api.get("championship_teams", ttl=_ttl_for(last[-1].race), session_key=key)
        except api.APIError:
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

    def team_colour(self, team):
        for d in self.drivers.values():
            if d.team == team:
                return d.colour
        return "FFFFFF"

    # ----------------------------------------------------------------- standings
    def standings(self, upto=None):
        """Computes driver + team standings from race and sprint results.
        upto: include rounds up to this round number (default: all completed)."""
        drivers, teams = {}, {}
        for rnd in self.completed_rounds():
            if upto and rnd.number > upto:
                break
            race = rnd.race
            for sess, table, is_gp in ((rnd.sprint, RULES["sprint_points"], False),
                                       (race, RULES["gp_points"], True)):
                rows = self.result(sess)
                for row in rows:
                    info = self.driver(row["driver_number"], race)
                    e = drivers.setdefault(info.number, Entry(info.name, info.code, info.team))
                    e.team = info.team
                    t = teams.setdefault(info.team, Entry(info.team, info.team[:3].upper(), info.team))
                    pos = None if row.get("dsq") or row.get("dns") else row.get("position")
                    pts = table[pos - 1] if pos and pos <= len(table) else 0
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
                info = self.driver(pole["driver_number"], race)
                drivers.setdefault(info.number, Entry(info.name, info.code, info.team)).poles += 1
        return sort_standings(drivers.values()), sort_standings(teams.values()), drivers

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
            dchamp, dcont = title_status(drivers, remaining)
            tchamp, tcont = title_status(teams, remaining, constructor=True)
            timeline.append((rnd, drivers, dchamp, dcont, teams, tchamp, tcont, remaining))
        return timeline
