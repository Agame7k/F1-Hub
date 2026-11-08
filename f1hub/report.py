"""Deep-dive analysis of one real session: tyres, pits, positions, incidents, weather, braking."""
from datetime import datetime
from statistics import median

from . import api
from .racecontrol import neutralisation, neutralisation_counts
from .season import _ttl_for


def _dt(s):
    return datetime.fromisoformat(s)


def _slope(xs, ys):
    n = len(xs)
    if n < 4:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else None


class RaceReport:
    def __init__(self, season, rnd, session_name="Race"):
        self.season = season
        self.rnd = rnd
        self.session = rnd.sessions[session_name]
        self.key = self.session["session_key"]
        ttl = _ttl_for(self.session)
        g = lambda ep, **kw: api.get(ep, ttl=ttl, session_key=self.key, **kw)
        self.result = sorted(season.result(self.session), key=lambda r: (r.get("position") or 99))
        self.grid = {r["driver_number"]: r["position"] for r in g("starting_grid")}
        if not self.grid:   # fall back to qualifying order
            q = season.result(rnd.sessions.get("Sprint Qualifying" if session_name == "Sprint" else "Qualifying"))
            self.grid = {r["driver_number"]: r["position"] for r in q if r.get("position")}
        self.laps = g("laps")
        self.stints = g("stints")
        self.pits = g("pit")
        self.overtakes = g("overtakes")
        self.race_control = g("race_control")
        self.weather = g("weather")
        self.leader_pos = g("position", position=1)

    def driver(self, number):
        return self.season.driver(number, self.session)

    # ----------------------------------------------------------------- tyres
    def tyre_strategy(self):
        """number -> list of (compound, lap_start, lap_end, laps, tyre_age_at_start, deg s/lap)."""
        laps_by = {}
        for lap in self.laps:
            if lap.get("lap_duration"):
                laps_by.setdefault(lap["driver_number"], {})[lap["lap_number"]] = lap
        neutral = self.neutralised_laps()
        out = {}
        for st in sorted(self.stints, key=lambda s: (s["driver_number"], s["stint_number"])):
            n = st["driver_number"]
            a, b = st.get("lap_start") or 0, st.get("lap_end") or 0
            mine = laps_by.get(n, {})
            pts = [(l, mine[l]["lap_duration"]) for l in range(a + 1, b + 1)
                   if l in mine and not mine[l].get("is_pit_out_lap") and l not in neutral and l > 1]
            deg = None
            if len(pts) >= 5:
                med = median(t for _, t in pts)
                clean = [(l, t) for l, t in pts if t < med * 1.04]   # drop traffic / pit-in laps
                deg = _slope([l for l, _ in clean], [t for _, t in clean])
            out.setdefault(n, []).append((st.get("compound") or "?", a, b, b - a + 1 if b else 0,
                                          st.get("tyre_age_at_start"), deg))
        return out

    # ----------------------------------------------------------------- incidents
    def neutralised_laps(self):
        """Laps run under a Safety Car or VSC (approximate, from race control)."""
        laps, active = set(), None
        for m in sorted(self.race_control, key=lambda m: m["date"]):
            kind, lap = neutralisation(m), m.get("lap_number")
            if lap is None:
                continue
            if kind in ("sc", "vsc"):
                active = lap
            elif active is not None and kind in ("sc_end", "vsc_end"):
                laps.update(range(active, lap + 1))
                active = None
        return laps

    def incidents(self):
        keep = []
        for m in sorted(self.race_control, key=lambda m: m["date"]):
            msg = m.get("message") or ""
            up = msg.upper()
            neutral = neutralisation(m)
            if neutral in ("sc", "vsc", "sc_end", "vsc_end"):
                kind = "sc"
            elif neutral == "red" and m.get("category") != "SessionStatus":
                kind = "red"
            elif "PENALTY" in up and "NO FURTHER" not in up:
                kind = "penalty"
            elif "UNDER INVESTIGATION" in up or "NOTED" in up:
                kind = "investigation"
            elif m.get("flag") == "CHEQUERED":
                kind = "flag"
            else:
                continue
            keep.append((m.get("lap_number"), kind, msg))
        return keep

    def sc_summary(self):
        n = neutralisation_counts(self.race_control)
        return {"safety_cars": n["sc"], "vscs": n["vsc"], "red_flags": n["red"],
                "track_limits": sum("TRACK LIMITS" in (m.get("message") or "").upper() for m in self.race_control)}

    # ----------------------------------------------------------------- positions
    def lead_changes(self):
        """List of (lap, driver_number) each time a new car took P1."""
        leader_laps = sorted((l for l in self.laps if l.get("date_start")), key=lambda l: l["date_start"])
        out, current = [], None
        for p in sorted(self.leader_pos, key=lambda p: p["date"]):
            if p["driver_number"] == current:
                continue
            current = p["driver_number"]
            when = _dt(p["date"])
            lap = max((l["lap_number"] for l in leader_laps
                       if l["driver_number"] == current and _dt(l["date_start"]) <= when), default=0)
            out.append((lap, current))
        return out

    def overtake_counts(self):
        """Overtakes made / suffered per driver (OpenF1 includes pit-stop position swaps)."""
        made, lost = {}, {}
        for o in self.overtakes:
            made[o["overtaking_driver_number"]] = made.get(o["overtaking_driver_number"], 0) + 1
            lost[o["overtaken_driver_number"]] = lost.get(o["overtaken_driver_number"], 0) + 1
        return made, lost

    # ----------------------------------------------------------------- laps / pits / weather
    def fastest_lap(self):
        valid = [l for l in self.laps if l.get("lap_duration")]
        return min(valid, key=lambda l: l["lap_duration"]) if valid else None

    def pit_summary(self):
        count = {}
        for p in self.pits:
            count[p["driver_number"]] = count.get(p["driver_number"], 0) + 1
        stops = [p for p in self.pits if p.get("stop_duration")]
        fastest = min(stops, key=lambda p: p["stop_duration"]) if stops else None
        return count, fastest

    def weather_summary(self):
        if not self.weather:
            return None
        tt = [w["track_temperature"] for w in self.weather if w.get("track_temperature") is not None]
        at = [w["air_temperature"] for w in self.weather if w.get("air_temperature") is not None]
        return {
            "rain": any(w.get("rainfall") for w in self.weather),
            "track": (min(tt), max(tt)) if tt else None,
            "air": (min(at), max(at)) if at else None,
            "humidity": round(median(w["humidity"] for w in self.weather if w.get("humidity") is not None))
            if any(w.get("humidity") is not None for w in self.weather) else None,
        }


# --------------------------------------------------------------------------- braking telemetry
def braking_analysis(session, driver_number, lap_number=None):
    """Telemetry for one lap (default: the driver's fastest) from car_data at ~3.7 Hz.

    Returns dict with top speed, time on brakes/full throttle and every braking zone
    (entry speed -> minimum speed, duration, approximate deceleration in g)."""
    ttl = _ttl_for(session)
    laps = api.get("laps", ttl=ttl, session_key=session["session_key"], driver_number=driver_number)
    laps = [l for l in laps if l.get("lap_duration") and l.get("date_start")]
    if not laps:
        return None
    lap = (next((l for l in laps if l["lap_number"] == lap_number), None) if lap_number
           else min((l for l in laps if not l.get("is_pit_out_lap")), key=lambda l: l["lap_duration"]))
    if not lap:
        return None
    start = _dt(lap["date_start"])
    end = start.timestamp() + lap["lap_duration"]
    end_iso = datetime.fromtimestamp(end, tz=start.tzinfo).isoformat()
    samples = api.get("car_data", ttl=ttl, session_key=session["session_key"], driver_number=driver_number,
                      **{"date>=": lap["date_start"], "date<": end_iso})
    samples = sorted(samples, key=lambda s: s["date"])
    if len(samples) < 10:
        return None
    t = [_dt(s["date"]).timestamp() for s in samples]
    total = t[-1] - t[0]
    on_brake = full_throttle = 0.0
    zones, zone = [], None
    for i in range(1, len(samples)):
        dt = t[i] - t[i - 1]
        s = samples[i]
        braking = (s.get("brake") or 0) > 0
        if braking:
            on_brake += dt
        if (s.get("throttle") or 0) >= 98:
            full_throttle += dt
        if braking and zone is None:
            zone = {"start": t[i - 1], "entry": samples[i - 1]["speed"], "min": s["speed"], "end": t[i]}
        elif braking:
            zone["min"] = min(zone["min"], s["speed"])
            zone["end"] = t[i]
        elif zone is not None:
            zone["end"] = t[i]
            zones.append(zone)
            zone = None
    if zone:
        zones.append(zone)
    out_zones = []
    for z in zones:
        dur = max(0.27, z["end"] - z["start"])
        drop = z["entry"] - z["min"]
        if drop < 25:         # ignore lifts / brake dabs
            continue
        out_zones.append({
            "at": round(z["start"] - t[0], 1), "entry": z["entry"], "min": z["min"], "drop": drop,
            "duration": round(dur, 2), "decel_g": round(drop / 3.6 / dur / 9.81, 1),
        })
    return {
        "lap": lap["lap_number"], "lap_time": lap["lap_duration"],
        "top_speed": max(s["speed"] for s in samples),
        "speed_trap": lap.get("st_speed"),
        "brake_pct": round(on_brake / total * 100, 1) if total else 0,
        "full_throttle_pct": round(full_throttle / total * 100, 1) if total else 0,
        "zones": out_zones,
        "max_gear": max(s.get("n_gear") or 0 for s in samples),
        "max_rpm": max(s.get("rpm") or 0 for s in samples),
    }
