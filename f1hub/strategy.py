"""Strategy simulation: phase 6 of docs/roadmap.md.

Inputs measured from the season dataset (f1hub/dataset.py) and the lap-time model (f1hub/lapmodel.py):

- pit loss per circuit: in-lap + out-lap against the driver's normal laps either side, green and under SC / VSC;
- Safety Car and VSC probability per lap and circuit (shrunk toward the season rate: one race says little);
- overtaking probability: a logistic model of "the car within a second behind passes on the next lap", from its
  pace advantage (the lap-time model's prediction for both cars on their tyres) and how hard the circuit is to
  pass at.

The simulator (`simulate`) runs a race lap by lap, many times over: every car's lap is its pace + fuel + compound
offset + degradation x tyre age + noise; a car that catches the one ahead gets past with the pass model's
probability or is held up; Safety Cars and VSCs arrive at random, bunch the field and cheapen stops (a car due to
stop soon takes the cheap stop); cars stop on their plans. On top of it:

- `plan`: every viable one- and two-stop strategy for a race, ranked by expected race time (with Safety Car risk);
- `grade`: every pit stop teams made, against stopping later or on other tyres, from the state of the race at the
  time - simulated finishing position for each option.

Plain Python, seeded random numbers (the same seed gives the same answer), options compared on the same random
races (common random numbers) so small differences are differences, not luck.
"""
import math
import random
from statistics import median

from . import lapmodel
from .racecontrol import neutralisation_counts

VERSION = 4                     # bump when a change alters the results (cached reports are recomputed)
DRY = ("SOFT", "MEDIUM", "HARD")
SC_LAPS, VSC_LAPS = 4, 2        # typical length (laps)
SC_PRIOR_LAPS = 120             # pseudo-laps of season rate added to a circuit's own record (shrinkage)
SC_SLOW, VSC_SLOW = 1.40, 1.30  # lap time under SC / VSC as a multiple of a racing lap
SC_GAP = 0.8                    # s between cars once the Safety Car has bunched them up
HOLD_GAP = 0.35                 # s behind when a pass fails
DIRTY_AIR = 1.5                 # s: following closer than this costs the model's dirty-air figure
SC_WINDOW = 6                   # a car due to stop within this many laps takes a stop under SC / VSC
MIN_STINT = 6
PASS_DEFAULT = (-2.2, 1.6)      # logistic intercept and slope (per s/lap advantage) before any data


def _r(v, n=3):
    return None if v is None else round(v, n)


# ------------------------------------------------------------------------------------------------ inputs
def _races(db, upto=None):
    return db.execute("SELECT * FROM sessions WHERE session_name = 'Race' AND (? IS NULL OR round <= ?) ORDER BY round",
                      (upto, upto)).fetchall()


def race_laps(db, key):
    """Laps the winner completed."""
    r = db.execute("SELECT MAX(laps) FROM results WHERE session_key = ? AND position = 1", (key,)).fetchone()[0]
    return r or db.execute("SELECT MAX(lap_number) FROM laps WHERE session_key = ?", (key,)).fetchone()[0] or 0


def pit_losses(db, key):
    """Time each stop cost: the driver's in-lap + out-lap against what the driver would have done on those two laps
    without stopping (their usual pace + those laps' conditions, from the cars that didn't stop). Comparing with
    the field on the same laps takes out track evolution, a drying track and the slow laps behind a Safety Car.
    Returns ([green losses], [losses under SC / VSC])."""
    laps = {}
    for r in db.execute("SELECT driver_number, lap_number, lap_time, clean, neutral, pit_in, pit_out FROM laps "
                        "WHERE session_key = ?", (key,)):
        laps[(r["driver_number"], r["lap_number"])] = r
    # lap time = the driver's usual pace + that lap's conditions (track, fuel, Safety Car), split by alternating
    # medians, so that the cars dropping out of a lap to stop don't shift its figure
    run = [(n, L, r["lap_time"]) for (n, L), r in laps.items() if r["lap_time"] and not r["pit_in"] and not r["pit_out"]]
    usual = {}
    for n, L, t in run:
        if laps[(n, L)]["clean"]:
            usual.setdefault(n, []).append(t)
    usual = {n: median(v) for n, v in usual.items() if len(v) >= 5}
    field = {}
    for _ in range(4):
        per = {}
        for n, L, t in run:
            if n in usual:
                per.setdefault(L, []).append(t - usual[n])
        field = {L: median(v) for L, v in per.items() if len(v) >= 5}
        per = {}
        for n, L, t in run:
            if L in field and laps[(n, L)]["clean"]:
                per.setdefault(n, []).append(t - field[L])
        usual = {n: median(v) for n, v in per.items() if len(v) >= 5}
    green, neutral = [], []
    for p in db.execute("SELECT driver_number, lap_number FROM pits WHERE session_key = ? AND stop = 1", (key,)):
        n, L = p["driver_number"], p["lap_number"]
        a, b = laps.get((n, L)), laps.get((n, L + 1))
        if L <= 1 or n not in usual or L not in field or L + 1 not in field:
            continue
        if not a or not b or not a["lap_time"] or not b["lap_time"]:
            continue
        loss = a["lap_time"] + b["lap_time"] - field[L] - field[L + 1] - 2 * usual[n]   # field: lap effect
        if not 5 < loss < 45:
            continue
        prev = laps.get((n, L - 1))
        if a["neutral"] and b["neutral"] and prev and prev["neutral"]:      # SC / VSC out for the whole stop
            neutral.append(loss)
        elif not a["neutral"] and not b["neutral"]:
            green.append(loss)
    return green, neutral


def neutral_record(db, key):
    """(Safety Cars, VSCs, race laps) for one race."""
    rc = [dict(r) for r in db.execute("SELECT date, category, flag, message, lap_number FROM race_control WHERE session_key = ?", (key,))]
    n = neutralisation_counts(rc)
    return n["sc"] + n["red"], n["vsc"], race_laps(db, key)


class Inputs:
    """What the simulator needs to know about every circuit, from the races up to a round (none after it, so a
    prediction never sees the future)."""

    def __init__(self, db, upto=None, fits=None):
        self.races = _races(db, upto)
        self.fits = fits if fits is not None else {}
        green_all, sc_all, per = [], [], {}
        tot = {"sc": 0, "vsc": 0, "laps": 0}
        for s in self.races:
            g, ng = pit_losses(db, s["session_key"])
            sc, vsc, laps = neutral_record(db, s["session_key"])
            per[s["circuit"]] = {"green": g, "neutral": ng, "sc": sc, "vsc": vsc, "laps": laps, "key": s["session_key"],
                                 "round": s["round"]}
            green_all += g
            sc_all += ng
            tot["sc"] += sc
            tot["vsc"] += vsc
            tot["laps"] += laps
        self.per = per
        self.pit_loss_all = median(green_all) if green_all else 22.0
        self.sc_ratio = median([x / self.pit_loss_all for x in sc_all]) if len(sc_all) >= 5 else 0.5
        self.sc_rate = (tot["sc"] / tot["laps"]) if tot["laps"] else 0.02
        self.vsc_rate = (tot["vsc"] / tot["laps"]) if tot["laps"] else 0.01

    def circuit(self, circuit):
        """Pit loss (green, under SC), SC and VSC chance per lap at a circuit - shrunk toward the season."""
        c = self.per.get(circuit)
        green = median(c["green"]) if c and len(c["green"]) >= 3 else self.pit_loss_all
        laps = c["laps"] if c else 0
        sc = ((c["sc"] if c else 0) + SC_PRIOR_LAPS * self.sc_rate) / (laps + SC_PRIOR_LAPS)
        vsc = ((c["vsc"] if c else 0) + SC_PRIOR_LAPS * self.vsc_rate) / (laps + SC_PRIOR_LAPS)
        return {"pit_loss": _r(green, 2), "pit_loss_sc": _r(green * self.sc_ratio, 2), "sc_rate": _r(sc, 4),
                "vsc_rate": _r(vsc, 4), "races": 1 if c else 0, "stops": len(c["green"]) if c else 0}


# ------------------------------------------------------------------------------------------------ overtaking
def _logistic_fit(rows, groups, ridge=4.0, iters=30):
    """IRLS for logit p = a + a_g + b x with a ridge penalty on the group effects a_g. rows: [(x, group, y)]."""
    gl = sorted(groups)
    gi = {g: i + 2 for i, g in enumerate(gl)}
    p = len(gl) + 2
    beta = [PASS_DEFAULT[0], PASS_DEFAULT[1]] + [0.0] * len(gl)
    for _ in range(iters):
        H = [[0.0] * p for _ in range(p)]
        g = [0.0] * p
        for x, grp, y in rows:
            idx = [(0, 1.0), (1, x), (gi[grp], 1.0)]
            eta = sum(beta[i] * v for i, v in idx)
            mu = 1 / (1 + math.exp(-max(-30, min(30, eta))))
            w = mu * (1 - mu)
            for i, vi in idx:
                g[i] += (y - mu) * vi
                for j, vj in idx:
                    H[i][j] += w * vi * vj
        for k in range(2, p):
            g[k] -= ridge * beta[k]
            H[k][k] += ridge
        H[0][0] += 1e-6
        H[1][1] += 1e-6
        step = _solve(H, g)
        beta = [b + s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-6:
            break
    return beta[0], beta[1], {g: beta[gi[g]] for g in gl}


def _solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


# rough season figures for a compound a race's model couldn't measure: offset (s, against the medium) and
# degradation (s/lap per lap of tyre age)
FALLBACK = {"SOFT": (-0.35, 0.07), "MEDIUM": (0.0, 0.05), "HARD": (0.3, 0.035)}


def compounds_of(fit):
    """{compound: (offset against the race's reference compound, degradation)} from a race's model, every dry
    compound filled in (from FALLBACK, shifted to the same reference) when the model couldn't measure it."""
    ref = fit.get("ref_compound") or "MEDIUM"
    out = {}
    for c in fit["compounds"]:
        off = (c["offset"] or {}).get("est")
        if off is None:
            off = FALLBACK[c["compound"]][0] - FALLBACK.get(ref, (0, 0))[0] if c["compound"] in FALLBACK else 0.3
        out[c["compound"]] = (off, c["deg"]["est"])
    for c in DRY:
        if c not in out:
            out[c] = (FALLBACK[c][0] - FALLBACK.get(ref, (0, 0))[0], FALLBACK[c][1])
    return out


def pace_fn(fit):
    """The lap-time model's pace for (driver, compound, tyre age): seconds against the race's quickest driver."""
    gap = {d["number"]: d["gap"] for d in fit["drivers"]}
    comp = compounds_of(fit)
    worst = max(gap.values()) if gap else 0.0

    def pace(n, compound, age):
        off, deg = comp.get(compound, (0.3, 0.06))
        return gap.get(n, worst) + off + deg * (age or 0)
    return pace


class PassModel:
    """P(the car within a second behind passes on the next lap | its pace advantage in s/lap, the circuit)."""

    def __init__(self, a=PASS_DEFAULT[0], b=PASS_DEFAULT[1], circuits=None, n=0, passes=0):
        self.a, self.b, self.circuits, self.n, self.passes = a, b, circuits or {}, n, passes

    def p(self, adv, circuit=None):
        eta = self.a + self.circuits.get(circuit, 0.0) + self.b * adv
        return 1 / (1 + math.exp(-max(-30, min(30, eta))))

    def to_dict(self):
        return {"a": _r(self.a), "b": _r(self.b), "n": self.n, "passes": self.passes,
                "circuits": {c: _r(v) for c, v in sorted(self.circuits.items(), key=lambda kv: -kv[1])}}

    @classmethod
    def fit(cls, db, races, fits):
        """From every lap where a car ended within a second of the one ahead, and whether it passed it on track on
        the next lap."""
        rows, groups = [], set()
        for s in races:
            f = fits.get(s["session_key"])
            if not f or "skipped" in f:
                continue
            pace = pace_fn(f)
            key = s["session_key"]
            laps = {}
            for r in db.execute("SELECT driver_number, lap_number, position, interval_ahead, compound, tyre_age, neutral, pit_in, "
                                "pit_out FROM laps WHERE session_key = ?", (key,)):
                laps[(r["driver_number"], r["lap_number"])] = r
            by_lap = {}
            for (n, L), r in laps.items():
                if r["position"]:
                    by_lap.setdefault(L, {})[r["position"]] = n
            passes = {(r["overtaking"], r["overtaken"], r["lap_number"]) for r in db.execute(
                "SELECT overtaking, overtaken, lap_number FROM overtakes WHERE session_key = ? AND on_track = 1", (key,))}
            for L, order in by_lap.items():
                if L < 2:
                    continue
                for pos, n in order.items():
                    ahead = order.get(pos - 1)
                    r, ra = laps.get((n, L)), laps.get((ahead, L)) if ahead else None
                    nxt, nxa = laps.get((n, L + 1)), laps.get((ahead, L + 1)) if ahead else None
                    if not (r and ra and nxt and nxa) or r["interval_ahead"] is None or r["interval_ahead"] > 1.0:
                        continue
                    if r["neutral"] or nxt["neutral"] or nxt["pit_in"] or nxa["pit_in"] or nxt["pit_out"] or nxa["pit_out"]:
                        continue
                    adv = pace(ahead, nxa["compound"], nxa["tyre_age"]) - pace(n, nxt["compound"], nxt["tyre_age"])
                    rows.append((max(-3.0, min(3.0, adv)), s["circuit"], 1 if (n, ahead, L + 1) in passes else 0))
                    groups.add(s["circuit"])
        if len(rows) < 200:
            return cls(n=len(rows), passes=sum(r[2] for r in rows))
        a, b, cs = _logistic_fit(rows, groups)
        return cls(a, b, cs, len(rows), sum(r[2] for r in rows))


# ------------------------------------------------------------------------------------------------ the simulator
class Race:
    """One race to simulate. cars: [{number, code, pace (s/lap against the reference), team, colour}].
    compounds: {compound: (offset s, degradation s/lap/lap)}. pass_model: PassModel; circuit: its name."""

    def __init__(self, laps, ref_lap, cars, compounds, fuel, sigma, pit_loss, pit_loss_sc, sc_rate, vsc_rate,
                 pass_model, circuit, traffic=0.25, dnf_rate=0.0, max_stint=None):
        self.laps, self.ref_lap, self.cars = laps, ref_lap, cars
        self.compounds, self.fuel, self.sigma = compounds, fuel, sigma
        self.pit_loss, self.pit_loss_sc = pit_loss, pit_loss_sc
        self.sc_rate, self.vsc_rate = sc_rate, vsc_rate
        self.pass_model, self.circuit, self.traffic = pass_model, circuit, traffic
        self.dnf_rate = dnf_rate               # per car per lap
        self.max_stint = max_stint or {c: 60 for c in compounds}

    def lap_time(self, car, compound, age, lap):
        off, deg = self.compounds.get(compound, (0.5, 0.08))
        return self.ref_lap + car["pace"] + off + deg * age + self.fuel * lap


def simulate(race, state, plans, runs=200, seed=1, form_sd=0.0, sc_events=True):
    """Run the race from `state` `runs` times.

    state: {"lap": laps done, "cars": {number: {"time": cumulative s, "compound", "age", "used": set, "stops": n}}}
           (cars in state only; their order is by time). plans: {number: [(lap, compound), ...]} stops still to make.
    Returns {number: [finishing position per run (None if retired)]} and {number: [race time per run]}."""
    rng = random.Random(seed)
    nums = list(state["cars"])
    car_by = {c["number"]: c for c in race.cars}
    pos_out = {n: [] for n in nums}
    time_out = {n: [] for n in nums}
    exp_pace = {n: race.ref_lap + car_by[n]["pace"] for n in nums}
    for _ in range(runs):
        form = {n: rng.gauss(0, form_sd) for n in nums} if form_sd else {n: 0.0 for n in nums}
        st = {n: dict(state["cars"][n], used=set(state["cars"][n].get("used") or ()), plan=list(plans.get(n, ())),
                      out=False) for n in nums}
        order = sorted(nums, key=lambda n: st[n]["time"])
        sc_left = vsc_left = 0
        for lap in range(state["lap"] + 1, race.laps + 1):
            if sc_events and not sc_left and not vsc_left and lap < race.laps - 1:
                u = rng.random()
                if u < race.sc_rate:
                    sc_left = SC_LAPS
                elif u < race.sc_rate + race.vsc_rate:
                    vsc_left = VSC_LAPS
            neutral = "sc" if sc_left else "vsc" if vsc_left else None
            new = {}
            pitted = set()
            for n in order:
                c = st[n]
                if c["out"]:
                    continue
                if race.dnf_rate and rng.random() < race.dnf_rate:
                    c["out"] = True
                    continue
                stop = None
                if c["plan"]:
                    due = c["plan"][0][0]
                    if due == lap or (neutral and 0 < due - lap <= SC_WINDOW):
                        stop = c["plan"].pop(0)[1]
                # age: laps already done on the set, so this lap is the set's (age + 1)th, as tyre_age counts them
                base = race.lap_time(car_by[n], c["compound"], c["age"] + 1, lap) + form[n]
                if neutral:
                    t = base * (SC_SLOW if neutral == "sc" else VSC_SLOW)
                else:
                    t = base + rng.gauss(0, race.sigma)
                c["age"] += 1
                if stop:                # in at the end of this lap, out on a new set
                    t += race.pit_loss_sc if neutral else race.pit_loss
                    c["compound"], c["age"] = stop, 0
                    c["used"].add(stop)
                    c["stops"] += 1
                    pitted.add(n)
                new[n] = c["time"] + t
            running = [n for n in order if not st[n]["out"]]
            if neutral == "sc":
                # the field queues up behind the Safety Car: order as it stands after the stops, gaps closed
                ranked = sorted(running, key=lambda n: new[n])
                lead = new[ranked[0]]
                for i, n in enumerate(ranked):
                    st[n]["time"] = lead + i * SC_GAP
                order = ranked
            elif neutral == "vsc":
                for n in running:
                    st[n]["time"] = new[n]
                order = sorted(running, key=lambda n: st[n]["time"])
            else:
                # green: walk the field in order; a car that would end the lap ahead of the car in front has to pass it
                placed = []
                for n in order:
                    if st[n]["out"]:
                        continue
                    t = new[n]
                    prev_gap = None
                    if placed:
                        ahead = placed[-1]
                        prev_gap = st[n]["time"] - st[ahead]["time"]
                        if prev_gap is not None and 0 <= prev_gap < DIRTY_AIR and n not in pitted:
                            t += race.traffic
                    i = len(placed)
                    while i > 0:
                        a = placed[i - 1]
                        if t >= new[a] + 0.05:
                            break
                        if a in pitted or n in pitted:            # passing a car in the pit lane is free
                            i -= 1
                            continue
                        adv = (exp_pace[a] + _tyre(race, st[a])) - (exp_pace[n] + _tyre(race, st[n]))
                        if rng.random() < race.pass_model.p(adv, race.circuit):
                            i -= 1
                        else:
                            t = new[a] + HOLD_GAP
                            break
                    new[n] = t
                    placed.insert(i, n)
                for n in placed:
                    st[n]["time"] = new[n]
                order = placed
            sc_left = max(0, sc_left - 1)
            vsc_left = max(0, vsc_left - 1)
        finish = [n for n in order if not st[n]["out"]]
        for n in nums:
            if st[n]["out"]:
                pos_out[n].append(None)
                time_out[n].append(None)
            else:
                pos_out[n].append(finish.index(n) + 1)
                time_out[n].append(st[n]["time"])
    return pos_out, time_out


def _tyre(race, c):
    off, deg = race.compounds.get(c["compound"], (0.5, 0.08))
    return off + deg * (c["age"] + 1)


# ------------------------------------------------------------------------------------------------ building a race
def race_from_fit(db, session, fit, inputs, pass_model):
    """A Race with the pace, tyres and noise the lap-time model measured for this race (hindsight: for grading)."""
    circ = inputs.circuit(session["circuit"])
    drivers = {d["number"]: d for d in fit["drivers"]}
    rows = db.execute("""SELECT r.driver_number, d.code, d.team, d.colour FROM results r LEFT JOIN drivers d
                         ON d.session_key = r.session_key AND d.driver_number = r.driver_number WHERE r.session_key = ?""",
                      (session["session_key"],)).fetchall()
    worst = max((d["gap"] for d in drivers.values()), default=0.0)
    cars = [{"number": r["driver_number"], "code": r["code"], "team": r["team"], "colour": r["colour"],
             "pace": drivers[r["driver_number"]]["gap"] if r["driver_number"] in drivers else worst + 0.5} for r in rows]
    comp = compounds_of(fit)
    return Race(race_laps(db, session["session_key"]), fit["reference_lap"], cars, comp, fit["fuel"]["est"],
                fit["sigma"], circ["pit_loss"], circ["pit_loss_sc"], circ["sc_rate"], circ["vsc_rate"], pass_model,
                session["circuit"], (fit["traffic"] or {}).get("est") or 0.25, max_stint=_max_stints(db, session))


def _max_stints(db, session):
    """Longest stint run on each compound at this race (or the season), plus 10%: plans stay realistic."""
    out = {}
    for r in db.execute("SELECT compound, MAX(lap_end - lap_start + 1) AS n FROM stints WHERE session_key = ? GROUP BY 1",
                        (session["session_key"],)):
        if r["compound"] in DRY and r["n"]:
            out[r["compound"]] = r["n"]
    for r in db.execute("""SELECT st.compound, MAX(st.lap_end - st.lap_start + 1) AS n FROM stints st JOIN sessions s USING (session_key)
                           WHERE s.session_name = 'Race' GROUP BY 1"""):
        if r["compound"] in DRY and r["compound"] not in out:
            out[r["compound"]] = r["n"]
    return {c: int(math.ceil(out.get(c, 40) * 1.1)) for c in DRY}


# ------------------------------------------------------------------------------------------------ planning
def candidates(race, start_lap=0, compound=None, age=0, used=(), step=1):
    """Every viable plan from `start_lap` on (one or two more stops, or none if two compounds are already used),
    as (plan, label). With compound None the race hasn't started: the first stint's tyre is part of the plan."""
    L = race.laps
    out = []
    firsts = [compound] if compound else list(DRY)
    for c0 in firsts:
        a0 = age if compound else 0
        used0 = set(used) | {c0}
        rest = L - start_lap
        if compound and len(used0) >= 2 and a0 + rest <= race.max_stint.get(c0, 60):
            out.append(([], c0, "no stop"))
        for c1 in DRY:
            for p1 in range(start_lap + 1, L - MIN_STINT + 1, step):
                if p1 - start_lap + a0 > race.max_stint.get(c0, 60):
                    break
                if (not compound and p1 < MIN_STINT):
                    continue
                if L - p1 <= race.max_stint.get(c1, 60) and len(used0 | {c1}) >= 2:
                    out.append(([(p1, c1)], c0, None))
                for c2 in DRY:
                    if len(used0 | {c1, c2}) < 2:
                        continue
                    for p2 in range(p1 + MIN_STINT, L - MIN_STINT + 1, step):
                        if p2 - p1 > race.max_stint.get(c1, 60):
                            break
                        if L - p2 <= race.max_stint.get(c2, 60):
                            out.append(([(p1, c1), (p2, c2)], c0, None))
    return out


def _label(c0, plan):
    return "-".join([c0[0]] + [c[0] for _, c in plan])


def expected_time(race, plan, c0, start_lap=0, age0=0, sc_draws=None):
    """A car's race time on a plan in free air (pace, tyres, stops), averaged over Safety Car scenarios: when one
    comes and a stop is due within SC_WINDOW laps, the stop is taken under it, for less."""
    stints, lap, c, age = [], start_lap, c0, age0
    for p, cn in plan:
        stints.append((c, age, p - lap))
        lap, c, age = p, cn, 0
    stints.append((c, age, race.laps - lap))
    base = 0.0
    for comp, a0, n in stints:
        off, deg = race.compounds.get(comp, (0.5, 0.08))
        base += n * off + deg * (n * a0 + n * (n + 1) / 2)
    stops = [p for p, _ in plan]
    if not sc_draws:
        return base + len(stops) * race.pit_loss
    total = 0.0
    saving = race.pit_loss - race.pit_loss_sc
    for sc_laps in sc_draws:
        t = base + len(stops) * race.pit_loss
        for p in stops:
            if any(0 <= p - s <= SC_WINDOW for s in sc_laps if s > start_lap):
                # stopping early under the SC: cheaper stop, a few laps more on the next set (approximated by the
                # next stint's degradation over the laps moved)
                t -= saving
        total += t
    return total / len(sc_draws)


def sc_scenarios(race, n=300, seed=7, start_lap=0):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        laps, lap = [], start_lap + 1
        while lap < race.laps - 1:
            u = rng.random()
            if u < race.sc_rate + race.vsc_rate:
                laps.append(lap)
                lap += SC_LAPS
            lap += 1
        out.append(laps)
    return out


def plan(race, top=12):
    """Every one- and two-stop strategy from the start, ranked by expected race time with Safety Car risk.
    Returns the best few, the best of each tyre sequence, and for the chart the best time for every first-stop lap
    of each sequence."""
    draws = sc_scenarios(race)
    scored = []
    for p, c0, _ in candidates(race, step=1):
        scored.append((expected_time(race, p, c0), p, c0))
    scored.sort(key=lambda x: x[0])
    # Safety Car risk only for the leading few hundred (it never reorders the long tail)
    best = [(expected_time(race, p, c0, sc_draws=draws), p, c0) for _, p, c0 in scored[:400]]
    best.sort(key=lambda x: x[0])
    t0 = best[0][0]
    seq = {}
    for t, p, c0 in best:
        seq.setdefault(_label(c0, p), (t, p, c0))
    curves = {}
    for t, p, c0 in scored:
        lab = _label(c0, p)
        if len(p) > 2 or lab not in seq:
            continue
        cur = curves.setdefault(lab, {})
        first = p[0][0]
        if first not in cur or t < cur[first]:
            cur[first] = t
    sc_free = scored[0][0]
    return {"best": [{"label": _label(c0, p), "stops": [[s, c] for s, c in p], "start": c0, "loss": _r(t - t0, 2)}
                     for t, p, c0 in best[:top]],
            "sequences": sorted(({"label": k, "stops": [[s, c] for s, c in v[1]], "start": v[2], "loss": _r(v[0] - t0, 2)}
                                 for k, v in seq.items()), key=lambda r: r["loss"]),
            "curves": {k: sorted([[lap, _r(t - sc_free, 2)] for lap, t in v.items()]) for k, v in curves.items()
                       if k in [x["label"] for x in sorted(({"label": k2, "l": v2[0]} for k2, v2 in seq.items()),
                                                            key=lambda r: r["l"])[:6]]},
            "laps": race.laps, "pit_loss": race.pit_loss, "pit_loss_sc": race.pit_loss_sc,
            "sc_rate": race.sc_rate, "vsc_rate": race.vsc_rate}


# ------------------------------------------------------------------------------------------------ grading the calls
def _actual_plans(db, key):
    """Every car's stops as they happened: {number: [(in-lap + 1, new compound), ...]} - the lap the new tyres
    start - and its starting compound."""
    plans, first = {}, {}
    for r in db.execute("SELECT driver_number, stint, compound, lap_start FROM stints WHERE session_key = ? ORDER BY stint", (key,)):
        if r["stint"] == 1:
            first[r["driver_number"]] = r["compound"]
        else:
            plans.setdefault(r["driver_number"], []).append((r["lap_start"] - 1, r["compound"]))
    return plans, first


def state_at(db, key, lap, race):
    """The race after `lap` laps, from the dataset: cumulative times from the gaps to the leader, tyres and ages."""
    cars = {}
    rows = db.execute("SELECT driver_number, gap_to_leader, compound, tyre_age, position FROM laps WHERE session_key = ? "
                      "AND lap_number = ?", (key, lap)).fetchall()
    used = {}
    for r in db.execute("SELECT driver_number, compound FROM stints WHERE session_key = ? AND lap_start <= ?", (key, lap)):
        used.setdefault(r["driver_number"], set()).add(r["compound"])
    stops = {r[0]: r[1] for r in db.execute("SELECT driver_number, COUNT(*) FROM stints WHERE session_key = ? AND stint > 1 "
                                            "AND lap_start <= ? GROUP BY 1", (key, lap))}
    for r in rows:
        if r["position"] is None or r["compound"] not in DRY:
            continue
        gap = r["gap_to_leader"] if r["gap_to_leader"] is not None else (0.0 if r["position"] == 1 else None)
        if gap is None:
            continue
        cars[r["driver_number"]] = {"time": gap + race.ref_lap * lap, "compound": r["compound"], "age": r["tyre_age"] or 0,
                                    "used": used.get(r["driver_number"], {r["compound"]}), "stops": stops.get(r["driver_number"], 0)}
    return {"lap": lap, "cars": cars}


def grade(db, session, fit, inputs, pass_model, runs=80, progress=lambda m: None):
    """Every pit stop of a race against the alternatives at the time: stopping 2 or 4 laps later, or now on another
    tyre. Each option: expected finishing position over `runs` simulated races from the lap before the stop, every
    other car on its actual remaining plan. Hindsight caveat: pace and degradation are the whole race's, measured."""
    key = session["session_key"]
    race = race_from_fit(db, session, fit, inputs, pass_model)
    plans, _ = _actual_plans(db, key)
    car = {c["number"]: c for c in race.cars}
    out = []
    stops = sorted((p, n) for n, ps in plans.items() for p, _ in ps)
    for i, (p, n) in enumerate(stops):
        progress(f"stop {i + 1}/{len(stops)}")
        decide = p - 1
        if decide < 1 or p >= race.laps - 1:
            continue
        state = state_at(db, key, decide, race)
        if n not in state["cars"] or len(state["cars"]) < 10:
            continue
        rest = {m: [(lp, c) for lp, c in ps if lp > decide] for m, ps in plans.items()}
        mine = rest[n]
        actual_c = mine[0][1]
        later = mine[1:]
        options = [("as run", mine)]
        for k in (2, 4):
            if p + k < race.laps - 2 and (not later or p + k < later[0][0] - 3):
                options.append((f"{k} laps later", [(p + k, actual_c)] + later))
        for c in DRY:
            if c != actual_c:
                used = set(state["cars"][n]["used"]) | {c} | {x for _, x in later}
                if len(used) >= 2 and race.laps - p <= race.max_stint.get(c, 60) * (2 if later else 1):
                    options.append((f"{c.lower()} instead", [(p, c)] + later))
        if len(later) == 0 and len(state["cars"][n]["used"]) >= 2 and state["cars"][n]["age"] + race.laps - decide <= race.max_stint.get(state["cars"][n]["compound"], 60):
            options.append(("no stop", []))
        sims = []
        for label, pl in options:
            pos, _ = simulate(race, state, {**rest, n: pl}, runs=runs, seed=key + p)
            sims.append((label, pl, [x if x else len(state["cars"]) for x in pos[n]]))   # a retirement counts as last
        base = sims[0][2]
        res = []
        for label, pl, v in sims:
            d = [a - b for a, b in zip(v, base)]          # same random races for every option: paired differences
            m = sum(d) / len(d)
            sd = math.sqrt(sum((x - m) ** 2 for x in d) / (len(d) - 1)) if len(d) > 1 else 0.0
            res.append({"option": label, "plan": [[a, b] for a, b in pl], "position": _r(sum(v) / len(v), 2),
                        "vs_actual": _r(m, 2), "se": _r(sd / math.sqrt(len(d)), 2)})
        best = min(res, key=lambda r: r["position"])
        # a better option only counts when it is clear of the noise (2 standard errors)
        clear = best["option"] != "as run" and -best["vs_actual"] > 2 * (best["se"] or 0)
        prev = state["cars"][n]
        out.append({"number": n, "code": car.get(n, {}).get("code"), "team": car.get(n, {}).get("team"),
                    "colour": car.get(n, {}).get("colour"), "lap": p, "from": prev["compound"], "age": prev["age"],
                    "to": actual_c, "position_before": sorted(state["cars"], key=lambda m: state["cars"][m]["time"]).index(n) + 1,
                    "options": res, "expected": res[0]["position"], "best": best["option"] if clear else "as run",
                    "cost": _r(-best["vs_actual"], 2) if clear else 0.0, "cost_se": best["se"] if clear else None})
    return {"race": session["round"], "circuit": session["circuit"], "laps": race.laps, "runs": runs, "stops": out,
            "pit_loss": race.pit_loss}


# ------------------------------------------------------------------------------------------------ predictions
RACE_POINTS = (25, 18, 15, 12, 10, 8, 6, 4, 2, 1)
SPRINT_POINTS = (8, 7, 6, 5, 4, 3, 2, 1)
HALF_LIFE = 4          # races: how fast a driver's earlier race pace stops counting
W_QUALI = 0.5          # weight of this weekend's qualifying gap against earlier race pace
FORM_SD = 0.30         # % of a lap: race-day form, before the season has measured it
BASE_STRENGTH = 3      # pseudo-races of prior in the grid baseline
PRE_STINT = {"SOFT": 0.40, "MEDIUM": 0.62, "HARD": 0.80}   # longest stint before a race, as a share of its laps


def _gap_pct(fit):
    """{driver: corrected race pace, % off the race's quickest}, from a race's lap-time model."""
    return {d["number"]: d["gap"] / fit["reference_lap"] * 100 for d in fit["drivers"]}


def _ewma(points, at):
    """Weighted mean of [(round, value)], weights halving every HALF_LIFE races back from `at`."""
    w = [(0.5 ** ((at - r) / HALF_LIFE), v) for r, v in points]
    tot = sum(x for x, _ in w)
    return sum(x * v for x, v in w) / tot if tot else None


def quali_gaps(db, session):
    """{driver: best qualifying lap % off pole} for the Qualifying session of a race's meeting."""
    q = db.execute("SELECT session_key FROM sessions WHERE meeting_key = ? AND session_name = 'Qualifying'",
                   (session["meeting_key"],)).fetchone()
    if not q:
        return {}, None
    best = {}
    for r in db.execute("SELECT driver_number, MIN(COALESCE(q3, 1e9), COALESCE(q2, 1e9), COALESCE(q1, 1e9)) AS t "
                        "FROM results WHERE session_key = ?", (q[0],)):
        if r["t"] and r["t"] < 1e9:
            best[r["driver_number"]] = r["t"]
    if not best:
        return {}, None
    pole = min(best.values())
    return {n: (t / pole - 1) * 100 for n, t in best.items()}, pole


class Form:
    """What is known about every driver's race pace before a round: from the lap-time model of earlier races."""

    def __init__(self, fits_before, rnd, team_of, quali=None):
        self.hist, self.team_hist = {}, {}
        self.quali = {}
        for r, gaps in (quali or {}).items():
            if r < rnd:
                for n, g in gaps.items():
                    self.quali.setdefault(n, []).append((r, g))
        for f in fits_before:
            if "skipped" in f:
                continue
            for n, g in _gap_pct(f).items():
                self.hist.setdefault(n, []).append((f["round"], g))
            teams = {}
            for d in f["drivers"]:
                if d["team"]:
                    teams.setdefault(d["team"], []).append(d["gap"] / f["reference_lap"] * 100)
            for t, v in teams.items():
                self.team_hist.setdefault(t, []).append((f["round"], min(v)))
        self.rnd, self.team_of = rnd, team_of

    def prior(self, n):
        """Expected race pace, % off the quickest: the driver's own record; with fewer than two races measured,
        their team's (plus a little), else their qualifying gaps so far (wet races can't be measured)."""
        h = self.hist.get(n, [])
        if len(h) >= 2:
            return _ewma(h, self.rnd)
        t = self.team_hist.get(self.team_of.get(n))
        if t:
            return _ewma(t, self.rnd) + 0.25
        q = self.quali.get(n)
        return _ewma(q, self.rnd) if q else None


    def measured(self, n):
        """How many races (or, failing that, qualifying sessions) the prior is from."""
        h = self.hist.get(n, [])
        if len(h) >= 2:
            return len(h)
        t = self.team_hist.get(self.team_of.get(n))
        return len(t) if t else len(self.quali.get(n, []))


def quali_history(db):
    """{round: {driver: qualifying gap % off pole}} for every round."""
    return {s["round"]: quali_gaps(db, s)[0] for s in _races(db)}


def predict_race(db, session, form, inputs, pass_model, deg, form_sd, dnf_rate, runs=400, seed=11):
    """Win, podium and points chances for every car on the grid, from what was known before the start: earlier race
    pace (form), this weekend's qualifying gap and the grid. deg: {compound: s/lap per lap} from earlier races."""
    key = session["session_key"]
    q, pole = quali_gaps(db, session)
    rows = db.execute("""SELECT r.driver_number, r.grid, r.position, r.status, d.code, d.team, d.colour FROM results r
                         LEFT JOIN drivers d ON d.session_key = r.session_key AND d.driver_number = r.driver_number
                         WHERE r.session_key = ?""", (key,)).fetchall()
    rows = [r for r in rows if r["status"] != "DNS"]
    if len(rows) < 10 or not pole:
        return None
    ref = pole * 1.06
    pace = {}
    for r in rows:
        n = r["driver_number"]
        pr, qq = form.prior(n), q.get(n)
        pace[n] = (W_QUALI * qq + (1 - W_QUALI) * pr if pr is not None and qq is not None
                   else qq + 0.3 if qq is not None else pr + 0.3 if pr is not None else None)
    worst = max(v for v in pace.values() if v is not None)
    pace = {n: (worst + 0.5 if v is None else v) for n, v in pace.items()}
    circ = inputs.circuit(session["circuit"])
    laps = race_laps(db, key)
    cars = [{"number": r["driver_number"], "code": r["code"], "team": r["team"], "colour": r["colour"],
             "pace": pace[r["driver_number"]] / 100 * ref} for r in rows]
    comp = {c: (FALLBACK[c][0], deg.get(c, FALLBACK[c][1])) for c in DRY}
    race = Race(laps, ref, cars, comp, -0.05, 0.45, circ["pit_loss"], circ["pit_loss_sc"], circ["sc_rate"],
                circ["vsc_rate"], pass_model, session["circuit"], dnf_rate=dnf_rate / max(laps, 1),
                max_stint={c: int(PRE_STINT[c] * laps) for c in DRY})
    pl = plan(race, top=3)
    seqs = [(s["start"], [tuple(x) for x in s["stops"]]) for s in pl["sequences"][:3]]
    grid = sorted(rows, key=lambda r: (r["grid"] or 99, pace[r["driver_number"]]))
    rng = random.Random(seed)
    pos = {r["driver_number"]: [] for r in rows}
    chunks = 4
    for k in range(chunks):
        state, plans = {"lap": 0, "cars": {}}, {}
        for i, r in enumerate(grid):
            c0, stops = rng.choice(seqs)
            state["cars"][r["driver_number"]] = {"time": i * 0.25, "compound": c0, "age": 0, "used": {c0}, "stops": 0}
            plans[r["driver_number"]] = stops
        got, _ = simulate(race, state, plans, runs=runs // chunks, seed=seed + k, form_sd=form_sd / 100 * ref)
        for n, v in got.items():
            pos[n] += v
    out = []
    for r in rows:
        n, v = r["driver_number"], pos[r["driver_number"]]
        fin = [x for x in v if x]
        out.append({"number": n, "code": r["code"], "team": r["team"], "colour": r["colour"], "grid": r["grid"],
                    "pace": _r(pace[n], 3), "quali": _r(q.get(n), 3), "prior": _r(form.prior(n), 3),
                    "win": _r(sum(1 for x in v if x == 1) / len(v), 3),
                    "podium": _r(sum(1 for x in v if x and x <= 3) / len(v), 3),
                    "points": _r(sum(1 for x in v if x and x <= 10) / len(v), 3),
                    "expected": _r(sum(fin) / len(fin), 2) if fin else None,
                    "finish": r["position"] if r["status"] == "Finished" else None, "status": r["status"]})
    out.sort(key=lambda x: (-x["win"], -x["podium"], x["expected"] or 99))
    return {"drivers": out, "plan": pl["sequences"][:3], "laps": laps}


def _baseline(history, slot, what):
    """Chance of winning (what=1) or a podium (what=3) from grid slot `slot`, from earlier races this season,
    smoothed toward a smooth prior curve (BASE_STRENGTH pseudo-races of it)."""
    prior = (0.5 ** (slot - 1) * 0.5) if what == 1 else min(0.85, 0.85 * 0.78 ** (slot - 1))
    n = sum(1 for g, _ in history if g == slot)
    k = sum(1 for g, f in history if g == slot and f is not None and f <= what)
    return (k + BASE_STRENGTH * prior) / (n + BASE_STRENGTH)


def _brier(pairs):
    return _r(sum((p - y) ** 2 for p, y in pairs) / len(pairs), 4) if pairs else None


def calibration(pairs, edges=(0, 0.05, 0.15, 0.3, 0.5, 0.7, 1.0001)):
    """Predicted chance against how often it happened, in bins."""
    out = []
    for lo, hi in zip(edges, edges[1:]):
        b = [(p, y) for p, y in pairs if lo <= p < hi]
        if b:
            out.append({"lo": lo, "hi": min(hi, 1.0), "n": len(b), "predicted": _r(sum(p for p, _ in b) / len(b)),
                        "observed": _r(sum(y for _, y in b) / len(b))})
    return out


def backtest(db, fits, team_of, upto=None, runs=400, progress=lambda m: None):
    """Every race of the season predicted from what was known before it, then scored against what happened."""
    races = _races(db, upto)
    by_round = {f["round"]: f for f in fits.values()}
    hist, rounds, resid = [], [], []
    for s in races:
        rnd = s["round"]
        progress(f"predicting round {rnd}")
        before = [by_round[r] for r in sorted(by_round) if r < rnd]
        form = Form(before, rnd, {n: team_of.get((s["session_key"], n)) for n in range(1, 100)})
        inputs = Inputs(db, rnd - 1, fits)
        pm = PassModel.fit(db, inputs.races, fits)
        ok = [f for f in before if "skipped" not in f and not f["fuel"].get("fixed")]
        deg = {}
        for c in DRY:
            v = [(x["deg"]["est"], x["deg"]["se"]) for f in ok for x in f["compounds"] if x["compound"] == c]
            p = lapmodel._pooled(v)
            if p:
                deg[c] = p["est"]
        sd = math.sqrt(sum(e * e for e in resid) / len(resid)) if len(resid) >= 40 else FORM_SD
        dnf = (sum(1 for _, f in hist if f is None) / len(hist)) if len(hist) >= 40 else 0.08
        pr = predict_race(db, s, form, inputs, pm, deg, sd, dnf, runs=runs, seed=11 + rnd)
        if pr:
            base = list(hist)
            for d in pr["drivers"]:
                g = d["grid"] or 20
                d["base_win"] = _r(_baseline(base, g, 1), 3)
                d["base_podium"] = _r(_baseline(base, g, 3), 3)
            rounds.append({"round": rnd, "name": s["meeting_name"], "circuit": s["circuit"], **pr})
            for d in pr["drivers"]:
                hist.append((d["grid"] or 20, d["finish"]))
        # what this race says about race-day form: its measured pace against the prediction's
        f = by_round.get(rnd)
        if pr and f and "skipped" not in f:
            g = _gap_pct(f)
            pace = {d["number"]: d["pace"] for d in pr["drivers"]}
            common = [n for n in g if n in pace]
            if len(common) >= 10:
                mg = median(g[n] for n in common)
                mp = median(pace[n] for n in common)
                resid += [(g[n] - mg) - (pace[n] - mp) for n in common]
    pairs = {k: [(d[k], 1 if d["finish"] is not None and d["finish"] <= (1 if k == "win" else 3) else 0)
                 for r in rounds for d in r["drivers"]] for k in ("win", "podium")}
    basep = {k: [(d["base_" + k], 1 if d["finish"] is not None and d["finish"] <= (1 if k == "win" else 3) else 0)
                 for r in rounds for d in r["drivers"]] for k in ("win", "podium")}
    score = {}
    for k in ("win", "podium"):
        b, bb = _brier(pairs[k]), _brier(basep[k])
        score[k] = {"brier": b, "baseline": bb, "skill": _r(1 - b / bb, 3) if b is not None and bb else None,
                    "calibration": calibration(pairs[k]), "calibration_base": calibration(basep[k])}
    hits = sum(1 for r in rounds if r["drivers"][0]["finish"] == 1)
    score["favourite_won"] = hits
    score["races"] = len(rounds)
    score["form_sd"] = _r(math.sqrt(sum(e * e for e in resid) / len(resid)), 3) if resid else None
    return {"rounds": rounds, "score": score}


TITLE_DRIFT = 0.15     # % of a lap: how far a car's pace may move over ten races of development (one s.d.)


def championship(db, fits, team_of, form_sd, dnf, upto=None, runs=2000, seed=5):
    """Title chances after every round: points so far, plus the remaining Grands Prix and sprints simulated from
    each driver's race pace at the time. Each simulated season draws a development drift per team (pace moves
    over the races left) and an error in each driver's measured pace (form noise over the square root of the races
    it is from), then every event adds race-day form noise and retirements at the season's rate. Ties on points
    go to the first found (countback is not modelled)."""
    events = db.execute("SELECT session_key, round, session_name FROM sessions WHERE session_name IN ('Race', 'Sprint') "
                        "ORDER BY round, session_name DESC").fetchall()
    rounds = sorted({e["round"] for e in events if upto is None or e["round"] <= upto})
    pts, tpts = {}, {}
    for r in db.execute("""SELECT s.round, s.session_key, r.driver_number, r.points FROM results r JOIN sessions s
                           USING (session_key) WHERE s.session_name IN ('Race', 'Sprint')"""):
        d = pts.setdefault(r["round"], {})
        d[r["driver_number"]] = d.get(r["driver_number"], 0) + (r["points"] or 0)
        team = team_of.get((r["session_key"], r["driver_number"]))
        if team:
            t = tpts.setdefault(r["round"], {})
            t[team] = t.get(team, 0) + (r["points"] or 0)
    by_round = {f["round"]: f for f in fits.values()}
    quali = quali_history(db)
    out = []
    for after in rounds:
        have, thave = {}, {}
        for rnd in rounds:
            if rnd <= after:
                for n, p in pts.get(rnd, {}).items():
                    have[n] = have.get(n, 0) + p
                for t, p in tpts.get(rnd, {}).items():
                    thave[t] = thave.get(t, 0) + p
        left = [e for e in events if e["round"] > after]
        # the field: the drivers of the latest event run (what is known at the time)
        nxt = [e for e in events if e["round"] <= after][-1]["session_key"]
        entry = {r[0]: team_of.get((nxt, r[0])) for r in db.execute("SELECT driver_number FROM results WHERE session_key = ?", (nxt,))}
        form = Form([by_round[r] for r in sorted(by_round) if r <= after], after + 1, entry, quali)
        racing = sorted(entry)
        pace = {n: form.prior(n) for n in racing}
        known = [v for v in pace.values() if v is not None]
        worst = max(known) if known else 0.0
        pace = {n: worst + 0.3 if v is None else v for n, v in pace.items()}
        races_left = len({e["round"] for e in left})
        drift = TITLE_DRIFT * math.sqrt(races_left / 10)
        rng = random.Random(seed + after)
        champs, teams_c = {}, {}
        for _ in range(runs):
            tot, ttot = dict(have), dict(thave)
            # how well each driver's pace is known (a standard error shrinking with races measured), and how far
            # each car may develop over the races left
            shift = {t: rng.gauss(0, drift) for t in set(entry.values())}
            base = {n: pace[n] + shift[entry[n]] + rng.gauss(0, form_sd / math.sqrt(max(1, form.measured(n)))) for n in racing}
            for e in left:
                order = sorted(racing, key=lambda n: base[n] + rng.gauss(0, form_sd) + (99 if rng.random() < dnf else 0))
                table = RACE_POINTS if e["session_name"] == "Race" else SPRINT_POINTS
                for i, n in enumerate(order[:len(table)]):
                    tot[n] = tot.get(n, 0) + table[i]
                    if entry[n]:
                        ttot[entry[n]] = ttot.get(entry[n], 0) + table[i]
            w = max(tot, key=lambda n: tot[n])
            champs[w] = champs.get(w, 0) + 1
            if ttot:
                bt = max(ttot, key=lambda t: ttot[t])
                teams_c[bt] = teams_c.get(bt, 0) + 1
        out.append({"after": after, "drivers": {n: _r(v / runs, 3) for n, v in champs.items()},
                    "teams": {t: _r(v / runs, 3) for t, v in teams_c.items()},
                    "points": {n: have.get(n, 0) for n in sorted(set(have) | set(racing))}, "team_points": thave,
                    "events_left": len(left)})
    return out
