"""Terminal output: colours, tables, banners."""
import os
import re
import sys

from .championship import clinch_scenario, max_remaining
from .rules import RULES

USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
TYRE_COLOURS = {"SOFT": "E10600", "MEDIUM": "FFD300", "HARD": "F0F0F0", "INTERMEDIATE": "39B54A", "WET": "0067FF"}
GOLD = "FFC906"


def set_color(enabled):
    global USE_COLOR
    USE_COLOR = enabled


def c(text, hex_colour=None, bold=False):
    if not USE_COLOR:
        return str(text)
    codes = ["1"] if bold else []
    if hex_colour:
        h = hex_colour.lstrip("#")
        codes.append("38;2;{};{};{}".format(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))
    return f"\033[{';'.join(codes)}m{text}\033[0m" if codes else str(text)


def dim(text):
    return c(text, "8A8A8A")


def pad(s, width, right=False):
    """Pad a possibly-coloured string to a visible width."""
    n = max(0, width - len(re.sub(r"\033\[[0-9;]*m", "", str(s))))
    return " " * n + str(s) if right else str(s) + " " * n


def header(text, width=80, colour="E10600"):
    line = "=" * width
    return f"\n{c(line, colour)}\n{c(text.center(width), bold=True)}\n{c(line, colour)}"


def fmt_time(t):
    if t is None:
        return "-"
    if isinstance(t, list):
        t = next((x for x in reversed(t) if x), None)
        if t is None:
            return "-"
    m, s = divmod(float(t), 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{s:06.3f}" if h else f"{m}:{s:06.3f}"


def tyre(compound, short=True):
    name = (compound or "?").upper()
    if name == "UNKNOWN":
        name = "?"
    return c(name[0] if short else name.title(), TYRE_COLOURS.get(name), bold=True)


def champion_banner(title, name, team, colour, detail):
    width = 80
    trophy = [
        r"      ___________      ", r"     '._==_==_=_.'     ", r"     .-\:      /-.     ",
        r"    | (|:.     |) |    ", r"     '-|:.     |-'     ", r"       \::.    /       ",
        r"        '::. .'        ", r"          ) (          ", r"        _.' '._        ",
        r"       '-------'       ",
    ]
    out = ["", c("*" * width, GOLD, bold=True)]
    out += [c(line.center(width), GOLD, bold=True) for line in trophy]
    out += ["", c(title.center(width), GOLD, bold=True), c(name.upper().center(width), colour, bold=True)]
    if team:
        out.append(c(team.center(width), bold=True))
    out += [detail.center(width), c("*" * width, GOLD, bold=True)]
    return "\n".join(out)


# --------------------------------------------------------------------------- standings
def print_driver_standings(season, drivers, champ=None, verified=None):
    done = len(season.completed_rounds())
    print(header(f"{season.year} DRIVERS' CHAMPIONSHIP - after round {done}/{len(season.rounds)}"))
    if verified is not None:
        print("  " + (c("Points verified against OpenF1 official standings", "39B54A") if not verified
                      else c("Using official totals (differences: " + "; ".join(verified) + ")", "FFB000")))
    leader = drivers[0].points if drivers else 0
    print(dim(f"  {'Pos':>3}  {'Driver':<24}{'Team':<18}{'Pts':>6} {'Gap':>5}  "
              f"{'Win':>3} {'Pod':>3} {'Pole':>4} {'SprW':>4} {'DNF':>3}"))
    for i, e in enumerate(drivers):
        crown = c("  WORLD CHAMPION", GOLD, bold=True) if champ is e else ""
        gap = "" if i == 0 else f"-{leader - e.points:g}"
        print(f"  {i+1:>3}  {pad(c(e.name, season.team_colour(e.team), bold=True), 24)}{e.team[:17]:<18}"
              f"{e.points:>6g} {gap:>5}  {e.wins:>3} {e.podiums:>3} {e.poles:>4} {e.sprint_wins:>4} "
              f"{e.dnfs:>3}{crown}")


def print_team_standings(season, teams, champ=None):
    done = len(season.completed_rounds())
    print(header(f"{season.year} CONSTRUCTORS' CHAMPIONSHIP - after round {done}/{len(season.rounds)}"))
    leader = teams[0].points if teams else 0
    for i, e in enumerate(teams):
        colour = season.team_colour(e.name)
        crown = c("  CHAMPIONS", GOLD, bold=True) if champ is e else ""
        gap = "" if i == 0 else f"-{leader - e.points:g}"
        bar = "#" * int(e.points / max(1, leader) * 30)
        print(f"  {i+1:>3}  {pad(c(e.name, colour, bold=True), 20)}{e.points:>6g} {gap:>6}  "
              f"W{e.wins:<3}{c(bar, colour)}{crown}")


# --------------------------------------------------------------------------- title fight
def print_title_fight(season, drivers, teams, dchamp, dcont, tchamp, tcont, timeline):
    remaining = season.remaining_rounds()
    flags = [r.has_sprint for r in remaining]
    print(header(f"{season.year} TITLE FIGHT", colour=GOLD))
    print(f"  Rounds left: {len(remaining)} ({sum(flags)} sprint weekends)   "
          f"Max points still available: drivers {max_remaining(flags):g}, "
          f"constructors {max_remaining(flags, constructor=True):g}")

    for label, table, champ, cont, constructor in (
            ("DRIVERS", drivers, dchamp, dcont, False), ("CONSTRUCTORS", teams, tchamp, tcont, True)):
        print(c(f"\n  {label}", bold=True))
        if not table:
            print("  No results yet.")
            continue
        if champ:
            decided = next((t for t in timeline if (t[5] if constructor else t[2])), None)
            rnd = decided[0] if decided else None
            spare = len(decided[7]) if decided else 0
            detail = (f"Clinched at the {rnd.name} (round {rnd.number})" if rnd else "") + \
                     (f" with {spare} round{'s' * (spare != 1)} to spare" if spare else " at the final round")
            title = f"{season.year} FORMULA 1 {'CONSTRUCTORS' if constructor else 'DRIVERS'}' WORLD CHAMPION" + \
                    ("S" if constructor else "")
            print(champion_banner(title, champ.name, None if constructor else champ.team,
                                  season.team_colour(champ.team), detail))
            continue
        best = max_remaining(flags, constructor)
        for e in table[:6]:
            alive = e in cont
            gap = table[0].points - e.points
            state = c("LEADER", GOLD, bold=True) if e is table[0] else \
                (c(f"in contention (needs {gap:g} of {best:g} available)", "39B54A") if alive
                 else dim("eliminated"))
            print(f"    {pad(c(e.name, season.team_colour(e.team), bold=True), 24)}{e.points:>6g}  {state}")
        sc = clinch_scenario(table, flags, constructor)
        if sc:
            nxt = remaining[0]
            print(c(f"  >> CLINCH WATCH ({nxt.name}): ", "FFB000", bold=True) + sc)

    # elimination timeline
    print(c("\n  Title timeline (drivers still mathematically alive after each round)", bold=True))
    for rnd, d, dc, dcont_, *_ in timeline:
        codes = [e.code for e in dcont_]
        names = (", ".join(codes[:8]) + (dim(f" +{len(codes) - 8} more") if len(codes) > 8 else "")
                 if not dc else c(f"{dc.name} CHAMPION", GOLD, bold=True))
        print(f"    R{rnd.number:<3}{rnd.country[:14]:<15} {names}")
        if dc:
            break


# --------------------------------------------------------------------------- calendar
def print_calendar(season):
    print(header(f"{season.year} CALENDAR"))
    done = {r.number for r in season.completed_rounds()}
    nxt = season.remaining_rounds()[0].number if season.remaining_rounds() else None
    for r in season.rounds:
        tag = c(" [SPRINT]", "FFB000") if r.has_sprint else ""
        if r.cancelled:
            line = dim("CANCELLED")
        elif r.number in done:
            win = next((x for x in season.result(r.race) if x.get("position") == 1), None)
            pole = next((x for x in season.result(r.quali) if x.get("position") == 1), None)
            if win:
                d = season.driver(win["driver_number"], r.race)
                line = f"winner {c(d.name, d.colour, bold=True)}"
                if pole:
                    line += dim(f"   pole {season.driver(pole['driver_number'], r.race).code}")
            else:
                line = "results pending"
        elif r.number == nxt:
            line = c("<- NEXT  " + r.race["date_start"][:10], "39B54A", bold=True)
        else:
            line = dim(r.race["date_start"][:10])
        print(f"  R{r.number:<3}{r.country[:14]:<15}{r.circuit[:22]:<23}{line}{tag}")
    if season.cancelled:
        print(dim("  Cancelled: " + ", ".join(r.name for r in season.cancelled)))


# --------------------------------------------------------------------------- race report
def print_race_report(rep):
    s, rnd = rep.season, rep.rnd
    name = rep.session["session_name"]
    print(header(f"ROUND {rnd.number} - {rnd.name.upper()} - {name.upper()}"))
    print(dim(f"  {rnd.circuit}, {rnd.location}   {rep.session['date_start'][:10]}"))
    w = rep.weather_summary()
    if w:
        print(f"  Weather: {'RAIN during session' if w['rain'] else 'dry'}"
              + (f"   track {w['track'][0]:.0f}-{w['track'][1]:.0f}C" if w["track"] else "")
              + (f"   air {w['air'][0]:.0f}-{w['air'][1]:.0f}C" if w["air"] else "")
              + (f"   humidity {w['humidity']}%" if w["humidity"] is not None else ""))
    sc = rep.sc_summary()
    print(f"  Safety cars: {sc['safety_cars']}   VSCs: {sc['vscs']}   Red flags: {sc['red_flags']}   "
          f"Track-limits messages: {sc['track_limits']}")

    pts_table = RULES["sprint_points"] if name == "Sprint" else RULES["gp_points"]
    strategy = rep.tyre_strategy()
    stops, fastest_stop = rep.pit_summary()
    made, lost = rep.overtake_counts()
    fl = rep.fastest_lap()
    print(dim(f"\n  {'Pos':>3}  {'Driver':<22}{'Team':<16}{'Grid':>4} {'+/-':>4}  {'Time/Status':<15}"
              f"{'Pits':>4} {'Ovt':>4}  Tyres"))
    for r in rep.result:
        d = rep.driver(r["driver_number"])
        pos = r.get("position")
        grid = rep.grid.get(r["driver_number"])
        if r.get("dsq"):
            status = "DSQ"
        elif r.get("dns"):
            status = "DNS"
        elif r.get("dnf"):
            status = f"DNF (L{r.get('number_of_laps') or 0})"
        elif pos == 1:
            status = fmt_time(r.get("duration"))
        else:
            gap = r.get("gap_to_leader")
            status = f"+{gap:.3f}s" if isinstance(gap, (int, float)) else str(gap)
        delta = ""
        if grid and pos and not (r.get("dnf") or r.get("dsq") or r.get("dns")):
            g = grid - pos
            delta = c(f"+{g}", "39B54A") if g > 0 else c(str(g), "FF5050") if g < 0 else " 0"
        tyres = "-".join(tyre(st[0]) for st in strategy.get(r["driver_number"], []))
        pts = pts_table[pos - 1] if pos and pos <= len(pts_table) and not r.get("dsq") else 0
        pos_s = f"{pos:>3}" if pos else " NC"
        print(f"  {pos_s}  {pad(c(d.name, d.colour, bold=True), 22)}{d.team[:15]:<16}{grid or '-':>4} "
              f"{pad(delta, 4, right=True)}  {status:<15}{stops.get(r['driver_number'], 0):>4} "
              f"{made.get(r['driver_number'], 0):>4}  {pad(tyres, 12)}"
              + (c(f" {pts} pts", GOLD) if pts else ""))
    if fl:
        d = rep.driver(fl["driver_number"])
        print(f"\n  Fastest lap: {c(d.name, d.colour, bold=True)} {fmt_time(fl['lap_duration'])} (lap {fl['lap_number']})"
              + (f", speed trap {fl['st_speed']} km/h" if fl.get("st_speed") else ""))
    if fastest_stop:
        d = rep.driver(fastest_stop["driver_number"])
        print(f"  Fastest pit stop: {c(d.code, d.colour, bold=True)} {fastest_stop['stop_duration']:.1f}s stationary "
              f"(lap {fastest_stop['lap_number']})")
    if made:
        top = max(made, key=made.get)
        print(f"  Most positions gained on track/pits: {rep.driver(top).code} ({made[top]})"
              + dim("  [OpenF1 overtakes include pit-stop swaps]"))

    lc = rep.lead_changes()
    if lc:
        print(c("\n  Lead changes", bold=True))
        print("    " + "  ->  ".join(f"{rep.driver(n).code} ({'start' if not lap else 'L' + str(lap)})" for lap, n in lc))

    print(c("\n  Tyre stints (trend = lap-time change per lap on clean laps; fuel burn-off can make it negative)", bold=True))
    for r in rep.result[:10]:
        st = strategy.get(r["driver_number"], [])
        parts = []
        for comp, a, b, n, age, deg in st:
            used = f" used{age}" if age else ""
            parts.append(f"{tyre(comp, short=False)} L{a}-{b} ({n} laps{used}"
                         + (f", {deg:+.3f}s/lap" if deg is not None else "") + ")")
        print(f"    {rep.driver(r['driver_number']).code}: " + "  |  ".join(parts))

    inc = rep.incidents()
    if inc:
        print(c("\n  Race control", bold=True))
        for lap, kind, msg in inc[:25]:
            col = {"sc": "FFB000", "red": "FF0000", "penalty": "FF5050"}.get(kind)
            print(f"    {('L' + str(lap)) if lap else '':<5}{c(msg, col)}")
        if len(inc) > 25:
            print(dim(f"    ... {len(inc) - 25} more"))


def print_braking(session, drivers_info, analyses):
    print(header(f"BRAKING & TELEMETRY - {session['session_name']} ({session['location']})"))
    for info, a in zip(drivers_info, analyses):
        if not a:
            print(f"  {info.code}: no telemetry available")
            continue
        print(c(f"\n  {info.name} ({info.team}) - lap {a['lap']} {fmt_time(a['lap_time'])}", info.colour, bold=True))
        print(f"    Top speed {a['top_speed']} km/h   speed trap {a['speed_trap'] or '-'} km/h   "
              f"max RPM {a['max_rpm']}   top gear {a['max_gear']}")
        print(f"    On the brakes {a['brake_pct']}% of the lap   full throttle {a['full_throttle_pct']}%   "
              f"braking zones {len(a['zones'])}")
        print(dim(f"    {'Zone':>4} {'at(s)':>6}  {'entry':>5} -> {'min':>3} km/h  {'drop':>4}  {'time':>5}  decel"))
        for i, z in enumerate(a["zones"], 1):
            bar = "#" * min(30, int(z["decel_g"] * 4))
            print(f"    {i:>4} {z['at']:>6}  {z['entry']:>5} -> {z['min']:>3}       {z['drop']:>4}  "
                  f"{z['duration']:>5}  {z['decel_g']:>4}g {c(bar, 'FF5050')}")
        if a["zones"]:
            hardest = max(a["zones"], key=lambda z: z["drop"])
            print(f"    Biggest stop: {hardest['entry']} -> {hardest['min']} km/h in {hardest['duration']}s")
    print(dim("\n  Telemetry is sampled at ~3.7 Hz, so deceleration figures are approximate."))


def print_h2h(season):
    """Teammate qualifying and race head-to-heads from real results."""
    print(header(f"{season.year} TEAMMATE HEAD-TO-HEAD"))
    pairs = {}
    for rnd in season.completed_rounds():
        q = {r["driver_number"]: r.get("position") or 99 for r in season.result(rnd.quali)}
        ra = {r["driver_number"]: (r.get("position") or 99) for r in season.result(rnd.race)}
        teams = {}
        for n in ra:
            teams.setdefault(season.driver(n, rnd.race).team, []).append(n)
        for team, nums in teams.items():
            if len(nums) != 2:
                continue
            a, b = sorted(nums)
            rec = pairs.setdefault((team, a, b), {"q": [0, 0], "r": [0, 0]})
            if a in q and b in q and q[a] != q[b]:
                rec["q"][0 if q[a] < q[b] else 1] += 1
            if ra[a] != ra[b]:
                rec["r"][0 if ra[a] < ra[b] else 1] += 1
    for (team, a, b), rec in sorted(pairs.items()):
        da, db = season.driver(a), season.driver(b)
        col = season.team_colour(team)
        print(f"  {pad(c(team, col, bold=True), 18)} {da.code} vs {db.code}   "
              f"quali {rec['q'][0]:>2}-{rec['q'][1]:<2}   race {rec['r'][0]:>2}-{rec['r'][1]:<2}")
