"""F1 Hub - real Formula 1 data from OpenF1 in your terminal.

    python3 -m f1hub                      interactive menu (current season)
    python3 -m f1hub standings --year 2025
    python3 -m f1hub title
    python3 -m f1hub calendar
    python3 -m f1hub race 5               race report for round 5 (add --sprint for the sprint)
    python3 -m f1hub braking 5 VER NOR    braking telemetry on each driver's fastest lap
    python3 -m f1hub h2h
    python3 -m f1hub export results.json
    python3 -m f1hub live                 live timing for the session running now (paid OpenF1 login)
    python3 -m f1hub gui                  live dashboard in your browser (http://localhost:8765)
    python3 -m f1hub doctor               check the setup and say what to fix
    python3 -m f1hub cleanup              free disk space F1 Hub no longer needs (--yes to delete)
"""
import argparse
import json
import sys
from datetime import datetime, timezone

from . import api, logs
from .championship import title_status
from .display import (c, dim, print_braking, print_calendar, print_driver_standings, print_h2h,
                      print_race_report, print_team_standings, print_title_fight, set_color)
from .report import RaceReport, braking_analysis
from .season import Season


def load_season(year):
    def progress(msg):
        print(dim(f"  loading {msg}..."), end="\r", file=sys.stderr, flush=True)
    s = Season(year).load(progress)
    print(" " * 70, end="\r", file=sys.stderr)
    if s.missing:
        print(c("  Results not available yet (left out): " + ", ".join(s.missing), "FFB000"), file=sys.stderr)
    return s


def standings_with_titles(season):
    drivers, teams, by_number = season.standings()
    issues = season.official_check()
    drivers, teams = season.apply_official_points(drivers, teams, by_number)
    flags = [r.has_sprint for r in season.remaining_rounds()]
    dchamp, dcont = title_status(drivers, flags, rules=season.rules) if drivers else (None, [])
    tchamp, tcont = title_status(teams, flags, constructor=True, rules=season.rules) if teams else (None, [])
    return drivers, teams, issues, dchamp, dcont, tchamp, tcont


def find_round(season, ident):
    ident = str(ident).strip().lower()
    # most specific first: round number, GP name, circuit/city, then country
    for match in (lambda r: ident == str(r.number), lambda r: ident in r.name.lower(),
                  lambda r: ident in r.circuit.lower() or ident in r.location.lower(),
                  lambda r: ident in r.country.lower()):
        found = next((r for r in season.rounds if match(r)), None)
        if found:
            return found
    return None


def find_driver(season, ident, session):
    ident = str(ident).strip().upper()
    for n in (season.race_drivers.get(session["session_key"]) or season.drivers):
        d = season.driver(n, session)
        if ident in (d.code, str(d.number)) or ident in d.name.upper():
            return d
    return None


# --------------------------------------------------------------------------- commands
def cmd_standings(season, args=None):
    drivers, teams, issues, dchamp, _, tchamp, _ = standings_with_titles(season)
    print_driver_standings(season, drivers, dchamp, issues)
    print_team_standings(season, teams, tchamp)


def cmd_title(season, args=None):
    drivers, teams, _, dchamp, dcont, tchamp, tcont = standings_with_titles(season)
    print_title_fight(season, drivers, teams, dchamp, dcont, tchamp, tcont, season.title_timeline())


def cmd_race(season, ident, sprint=False):
    rnd = find_round(season, ident)
    if not rnd:
        print(f"No round matching '{ident}'.")
        return
    name = "Sprint" if sprint else "Race"
    if name not in rnd.sessions:
        print(f"{rnd.name} has no {name.lower()}.")
        return
    if rnd.sessions[name]["session_key"] not in season.results:
        print(f"No results yet for the {rnd.name} {name.lower()}.")
        return
    print_race_report(RaceReport(season, rnd, name))


def cmd_braking(season, ident, codes, sprint=False):
    rnd = find_round(season, ident)
    if not rnd or not rnd.race:
        print(f"No round matching '{ident}'.")
        return
    session = rnd.sessions["Sprint" if sprint else "Race"]
    infos = []
    for code in codes:
        d = find_driver(season, code, session)
        if not d:
            print(f"No driver '{code}' in that session.")
            return
        infos.append(d)
    print_braking(session, infos, [braking_analysis(session, d.number) for d in infos])


def cmd_export(season, path):
    drivers, teams, issues, dchamp, _, tchamp, _ = standings_with_titles(season)
    data = {
        "year": season.year, "source": "OpenF1 (api.openf1.org)",
        "generated": datetime.now(timezone.utc).isoformat(),
        "drivers_champion": dchamp.name if dchamp else None,
        "constructors_champion": tchamp.name if tchamp else None,
        "drivers": [{"pos": i + 1, "name": e.name, "code": e.code, "team": e.team, "points": e.points,
                     "wins": e.wins, "podiums": e.podiums, "poles": e.poles, "dnfs": e.dnfs}
                    for i, e in enumerate(drivers)],
        "constructors": [{"pos": i + 1, "team": e.name, "points": e.points, "wins": e.wins}
                         for i, e in enumerate(teams)],
        "rounds": [{
            "round": r.number, "name": r.name, "circuit": r.circuit, "date": r.race["date_start"],
            "sprint": r.has_sprint, "cancelled": r.cancelled,
            "race": [{"pos": x.get("position"), "driver": season.driver(x["driver_number"], r.race).code,
                      "dnf": x.get("dnf"), "dsq": x.get("dsq")} for x in season.result(r.race)],
        } for r in season.rounds],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {path}")


# --------------------------------------------------------------------------- interactive
def menu(season):
    while True:
        done, total = len(season.completed_rounds()), len(season.rounds)
        print(c(f"\n  F1 HUB - {season.year} season ({done}/{total} rounds complete) - live data from OpenF1",
                "E10600", bold=True))
        print("   1. Driver & constructor standings\n   2. Title fight / champion\n   3. Calendar & winners\n"
              "   4. Race report\n   5. Sprint report\n   6. Braking telemetry\n   7. Teammate head-to-head\n"
              "   8. Change season\n   9. Export to JSON\n   L. LIVE timing (session running now)\n   0. Quit")
        choice = input("  > ").strip()
        try:
            if choice == "1":
                cmd_standings(season)
            elif choice == "2":
                cmd_title(season)
            elif choice == "3":
                print_calendar(season)
            elif choice in ("4", "5"):
                print_calendar(season)
                cmd_race(season, input("  Round number or name: "), sprint=choice == "5")
            elif choice == "6":
                print_calendar(season)
                rnd = input("  Round number or name: ")
                codes = input("  Driver codes (e.g. VER NOR): ").split()
                if codes:
                    cmd_braking(season, rnd, codes)
            elif choice == "7":
                print_h2h(season)
            elif choice == "8":
                season = load_season(int(input("  Year (2023+): ")))
            elif choice == "9":
                cmd_export(season, input("  File name [f1_export.json]: ").strip() or "f1_export.json")
            elif choice.lower() == "l":
                from .live import watch
                try:
                    watch(season)
                except KeyboardInterrupt:
                    pass
            elif choice in ("0", "q", ""):
                return
        except api.LiveSessionLock as e:
            print(c("\n  " + str(e), "FFB000"))
        except (api.APIError, ValueError) as e:
            print(c(f"\n  Error: {e}", "FF5050"))


def main(argv=None):
    p = argparse.ArgumentParser(prog="f1hub", description="Real F1 data from OpenF1")
    p.add_argument("--year", type=int, default=datetime.now().year)
    p.add_argument("--offline", action="store_true", help="use cached data only")
    p.add_argument("--no-color", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("standings")
    sub.add_parser("title")
    sub.add_parser("calendar")
    sub.add_parser("h2h")
    r = sub.add_parser("race")
    r.add_argument("round")
    r.add_argument("--sprint", action="store_true")
    b = sub.add_parser("braking")
    b.add_argument("round")
    b.add_argument("drivers", nargs="+")
    b.add_argument("--sprint", action="store_true")
    lv = sub.add_parser("live", help="live timing for the current session")
    lv.add_argument("--interval", type=int, default=10, help="seconds between refreshes (min 8)")
    lv.add_argument("--once", action="store_true", help="print one snapshot and exit")
    lv.add_argument("--session", default="latest", help="OpenF1 session_key (default: latest)")
    gu = sub.add_parser("gui", help="live dashboard in your browser")
    gu.add_argument("--port", type=int, default=8765)
    gu.add_argument("--interval", type=int, default=10)
    gu.add_argument("--session", default="latest")
    gu.add_argument("--no-browser", action="store_true")
    gu.add_argument("--open-feed", action="store_true",
                    help="let web pages from anywhere (e.g. an OBS overlay) read the data feed at /api/feed")
    e = sub.add_parser("export")
    e.add_argument("path", nargs="?", default="f1_export.json")
    dr = sub.add_parser("doctor", help="check the setup and say what to fix")
    dr.add_argument("--port", type=int, default=8765, help="the dashboard's port")
    dr.add_argument("--no-network", action="store_true", help="skip the checks that contact OpenF1")
    cl = sub.add_parser("cleanup", help="free disk space F1 Hub no longer needs")
    cl.add_argument("--yes", action="store_true", help="delete (without it, only shows what would go)")
    cl.add_argument("--recordings-older-than", type=int, metavar="DAYS",
                    help="also delete recordings of sessions more than DAYS days old")
    args = p.parse_args(argv)
    if args.no_color:
        set_color(False)
    api.offline = args.offline
    logs.setup()

    try:
        if args.cmd == "doctor":
            from .doctor import run
            return run(args.port, network=not args.no_network)
        if args.cmd == "cleanup":
            from .cleanup import run
            return run(args.yes, args.recordings_older_than)
        if args.cmd == "gui":
            from .gui import serve
            serve(args.year, args.session, args.interval, args.port, not args.no_browser,   # loads the season itself
                  open_feed=args.open_feed)
            return 0
        if args.cmd == "live":
            from .live import watch
            try:
                season = load_season(args.year)
            except (api.APIError, api.LiveSessionLock) as err:   # live timing doesn't need it, only the title picture
                print(c(f"Couldn't load the season ({str(err).splitlines()[0]}): no title picture.", "FFB000"),
                      file=sys.stderr)
                season = None
            watch(season, args.session, args.interval, args.once)
            return 0
        season = load_season(args.year)
        if not args.cmd:
            menu(season)
        elif args.cmd == "standings":
            cmd_standings(season)
        elif args.cmd == "title":
            cmd_title(season)
        elif args.cmd == "calendar":
            print_calendar(season)
        elif args.cmd == "h2h":
            print_h2h(season)
        elif args.cmd == "race":
            cmd_race(season, args.round, args.sprint)
        elif args.cmd == "braking":
            cmd_braking(season, args.round, args.drivers, args.sprint)
        elif args.cmd == "export":
            cmd_export(season, args.path)
    except api.LiveSessionLock as err:
        print(c(str(err), "FFB000"), file=sys.stderr)
        return 2
    except api.APIError as err:
        print(c(f"Error: {err}", "FF5050"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
