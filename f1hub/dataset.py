"""Season dataset: every finished session of a season compiled into one SQLite file of tidy tables.

    python3 -m f1hub compile 2025              races, sprints and qualifying -> .f1data/season_2025.sqlite
    python3 -m f1hub compile 2025 --practice   practice sessions too
    python3 -m f1hub compile 2025 --csv out/   also write every table as CSV

The tables are described in docs/dataset.md. Sessions already compiled and final are skipped, so re-running after
a race only fetches that race. SQLite is in the standard library, so the core still needs nothing but Python.
"""
import csv
import logging
import os
import re
import sqlite3
import time
from bisect import bisect_right
from collections import Counter
from pathlib import Path
from statistics import median

from . import api
from .analysis import WET_COMPOUNDS, Source, _drivers, _overlaps, _ts
from .quali import _is_deleted, deleted_laps
from .racecontrol import neutral_periods
from .season import Season, _session_over

log = logging.getLogger("f1hub.dataset")
DATA_DIR = Path(os.environ.get("F1HUB_DATA", api.PROJECT_DIR / ".f1data"))
SCHEMA_VERSION = 4
FINAL_AFTER = 4 * 86400     # s after a session ends before its results count as final (as season._ttl_for)
CLEAN_LIMIT = 1.07          # a clean lap is within 7% of the driver's median (races) or best (other sessions)
PIT_BEFORE, PIT_AFTER = 10, 20   # s around a car's time in the pit lane when places it loses or gains aren't passes
SLOW_LAP = 1.15                  # a car this much slower than its usual lap is damaged or stopping: passing it isn't racing
CASCADE = (4, 30)                # a car passed by (or passing) this many cars within this many s: a glitch or a stopped car
PING_PONG = 10                   # s: a swap the same two cars undo within this is timing-loop flicker (or nets to nothing)

KINDS = {"race": ("Race",), "sprint": ("Sprint",),
         "quali": ("Qualifying", "Sprint Qualifying", "Sprint Shootout"),
         "practice": ("Practice 1", "Practice 2", "Practice 3")}
DEFAULT_KINDS = ("race", "sprint", "quali")
GRID_FROM = {"Race": ("Qualifying",), "Sprint": ("Sprint Qualifying", "Sprint Shootout")}

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE sessions (
    session_key INTEGER PRIMARY KEY, year INTEGER, round INTEGER, meeting_key INTEGER, meeting_name TEXT,
    circuit TEXT, location TEXT, country TEXT, session_name TEXT, session_type TEXT,
    date_start TEXT, date_end TEXT, source TEXT, final INTEGER, compiled_at REAL);
CREATE TABLE drivers (
    session_key INTEGER, driver_number INTEGER, code TEXT, name TEXT, team TEXT, colour TEXT,
    PRIMARY KEY (session_key, driver_number));
CREATE TABLE results (
    session_key INTEGER, driver_number INTEGER, position INTEGER, status TEXT, points REAL, laps INTEGER,
    duration REAL, gap_to_leader REAL, laps_down INTEGER, q1 REAL, q2 REAL, q3 REAL,
    grid INTEGER, grid_source TEXT,
    PRIMARY KEY (session_key, driver_number));
CREATE TABLE laps (
    session_key INTEGER, driver_number INTEGER, lap_number INTEGER, date_start TEXT, lap_time REAL,
    s1 REAL, s2 REAL, s3 REAL, i1_speed INTEGER, i2_speed INTEGER, st_speed INTEGER,
    pit_out INTEGER, pit_in INTEGER, stint INTEGER, compound TEXT, tyre_age INTEGER,
    neutral INTEGER, deleted INTEGER, clean INTEGER,
    position INTEGER, interval_ahead REAL, gap_to_leader REAL,
    track_temp REAL, air_temp REAL, rainfall INTEGER,
    PRIMARY KEY (session_key, driver_number, lap_number));
CREATE TABLE stints (
    session_key INTEGER, driver_number INTEGER, stint INTEGER, compound TEXT, lap_start INTEGER,
    lap_end INTEGER, tyre_age_at_start INTEGER,
    PRIMARY KEY (session_key, driver_number, stint));
CREATE TABLE pits (
    session_key INTEGER, driver_number INTEGER, lap_number INTEGER, date TEXT,
    lane_duration REAL, stop_duration REAL, stop INTEGER);
CREATE TABLE weather (
    session_key INTEGER, date TEXT, air_temp REAL, track_temp REAL, humidity REAL, pressure REAL,
    rainfall INTEGER, wind_speed REAL, wind_direction INTEGER);
CREATE TABLE race_control (
    session_key INTEGER, date TEXT, lap_number INTEGER, category TEXT, flag TEXT, scope TEXT,
    sector INTEGER, driver_number INTEGER, message TEXT);
CREATE TABLE overtakes (
    session_key INTEGER, date TEXT, lap_number INTEGER, overtaking INTEGER, overtaken INTEGER, position INTEGER,
    on_track INTEGER);
CREATE TABLE positions (session_key INTEGER, date TEXT, driver_number INTEGER, position INTEGER);
CREATE TABLE intervals (
    session_key INTEGER, date TEXT, driver_number INTEGER, gap_to_leader REAL, interval REAL, laps_down INTEGER);
CREATE INDEX laps_driver ON laps (driver_number, session_key);
CREATE INDEX positions_key ON positions (session_key, driver_number, date);
CREATE INDEX intervals_key ON intervals (session_key, driver_number, date);
CREATE VIEW laps_full AS
    SELECT s.year, s.round, s.meeting_name, s.circuit, s.session_name, s.session_type,
           d.code, d.name, d.team, l.*
    FROM laps l JOIN sessions s USING (session_key)
    LEFT JOIN drivers d ON d.session_key = l.session_key AND d.driver_number = l.driver_number;
"""
# per-corner telemetry (f1hub/telemetry.py): opt-in, added to any dataset without a rebuild
TEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS tel_sessions (
    session_key INTEGER PRIMARY KEY, ref_driver INTEGER, ref_lap INTEGER, length REAL, outline TEXT, drivers INTEGER,
    compiled_at REAL);
CREATE TABLE IF NOT EXISTS tel_corners (
    session_key INTEGER, corner INTEGER, label TEXT, apex REAL, start REAL, end REAL, x INTEGER, y INTEGER,
    min_speed REAL, kind TEXT, PRIMARY KEY (session_key, corner));
CREATE TABLE IF NOT EXISTS tel_laps (
    session_key INTEGER, driver_number INTEGER, lap_number INTEGER, top_speed REAL, full_throttle REAL, brake_pct REAL,
    lico_time REAL, lico_count INTEGER, gear_changes INTEGER, samples INTEGER, off_line REAL,
    PRIMARY KEY (session_key, driver_number, lap_number));
CREATE TABLE IF NOT EXISTS tel_corner_laps (
    session_key INTEGER, driver_number INTEGER, lap_number INTEGER, corner INTEGER, seg_time REAL, entry_speed REAL,
    min_speed REAL, apex_dist REAL, brake_dist REAL, lift_dist REAL, peak_decel REAL, throttle_dist REAL,
    exit_speed REAL, gear INTEGER, PRIMARY KEY (session_key, driver_number, lap_number, corner));
"""
TEL_TABLES = ("tel_sessions", "tel_corners", "tel_laps", "tel_corner_laps")
TABLES = ("sessions", "drivers", "results", "laps", "stints", "pits", "weather", "race_control", "overtakes",
          "positions", "intervals") + TEL_TABLES


def default_path(year):
    return DATA_DIR / f"season_{year}.sqlite"


def connect(path):
    """Open (and if needed create) a dataset. One built with an older schema is rebuilt from scratch."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        version = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.DatabaseError:
        version = None
    if version and version[0] == str(SCHEMA_VERSION):
        db.executescript(TEL_SCHEMA)
        return db
    db.close()
    if path.exists():
        log.info("rebuilding %s for schema version %s", path.name, SCHEMA_VERSION)
        path.unlink()
    db = sqlite3.connect(path)
    db.executescript(SCHEMA + TEL_SCHEMA)
    db.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    db.commit()
    return db


# ------------------------------------------------------------------------------------------------ value helpers
def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _laps_down(v):
    m = re.match(r"\+?\s*(\d+)\s*LAPS?", str(v or "").upper())
    return int(m.group(1)) if m else None


def _flag(v):
    return None if v is None else int(bool(v))


def _status(r):
    return "DSQ" if r.get("dsq") else "DNS" if r.get("dns") else "DNF" if r.get("dnf") else \
        "Finished" if r.get("position") else None


class _Log:
    """Per-driver time series (e.g. positions) for 'the latest value at or before t' look-ups."""

    def __init__(self, rows, value):
        self.t, self.v = {}, {}
        for r in sorted((r for r in rows if r.get("date")), key=lambda r: r["date"]):
            n = r.get("driver_number")
            self.t.setdefault(n, []).append(_ts(r["date"]))
            self.v.setdefault(n, []).append(value(r))

    def at(self, n, t):
        ts = self.t.get(n)
        if not ts or t is None:
            return None
        i = bisect_right(ts, t) - 1
        return self.v[n][i] if i >= 0 else None


# ------------------------------------------------------------------------------------------------ laps
def merge_stints(stints, pits):
    """OpenF1 sometimes splits one set of tyres into several stints (a wet start: a 'stint' per lap on the same
    intermediates, the field led through the pit lane by the Safety Car). Consecutive stints on the same compound
    are one stint when no pit stop separates them, or when the tyre age just carries on (the same set); the stints
    are numbered again from 1."""
    stopped = {(p.get("driver_number"), p.get("lap_number")) for p in pits}
    out = []
    for n in sorted({s["driver_number"] for s in stints}):
        mine = sorted((s for s in stints if s["driver_number"] == n), key=lambda s: (s.get("stint_number") or 0))
        merged = []
        for st in mine:
            prev = merged[-1] if merged else None
            same_set = prev and prev.get("tyre_age_at_start") is not None and st.get("tyre_age_at_start") is not None \
                and prev.get("lap_start") and prev.get("lap_end") and abs(st["tyre_age_at_start"] - (
                    prev["tyre_age_at_start"] + prev["lap_end"] - prev["lap_start"] + 1)) <= 1
            if prev and prev.get("compound") == st.get("compound") and st.get("lap_start") and prev.get("lap_end") \
                    and (same_set or not any((n, ln) in stopped for ln in range(prev["lap_end"] - 1, st["lap_start"] + 1))):
                prev["lap_end"] = st.get("lap_end") or prev["lap_end"]
                continue
            merged.append(dict(st))
        for i, st in enumerate(merged, 1):
            st["stint_number"] = i
        out.extend(merged)
    return out


def lap_rows(key, session_type, laps, stints, pits, rc, weather, positions, intervals):
    """Every lap as a row of the laps table, with tyre, pit, neutralisation, position, gap, weather and clean flags."""
    is_race = session_type == "Race"
    spans = neutral_periods(rc)
    gone = deleted_laps(rc)
    pit_in = {(p.get("driver_number"), p.get("lap_number")) for p in pits}
    tyre = {}
    for st in stints:
        n, a = st["driver_number"], st.get("lap_start") or 1
        for ln in range(a, (st.get("lap_end") or a) + 1):
            tyre[(n, ln)] = (st.get("stint_number"), st.get("compound"), (st.get("tyre_age_at_start") or 0) + ln - a)
    wx = sorted((_ts(w["date"]), w) for w in weather if w.get("date"))
    wx_t = [t for t, _ in wx]
    pos = _Log(positions, lambda r: r.get("position"))
    gaps = _Log(intervals, lambda r: (_num(r.get("interval")), _num(r.get("gap_to_leader"))))

    rows = []
    for l in sorted((l for l in laps if l.get("lap_number")), key=lambda l: (l["driver_number"], l["lap_number"])):
        n, ln, t = l["driver_number"], l["lap_number"], l.get("lap_duration")
        start = _ts(l.get("date_start"))
        end = start + t if start and t else None
        stint, compound, age = tyre.get((n, ln), (None, None, None))
        neutral = bool(end and _overlaps(start, end, spans)) or bool(re.search(r"[4567]", l.get("track_status") or ""))
        deleted = bool(l.get("deleted")) or (bool(t) and _is_deleted(gone, n, ln, {"t": t}))
        w = wx[max(0, bisect_right(wx_t, start) - 1)][1] if wx and start else {}
        gap = gaps.at(n, end) or (None, None)
        rows.append({
            "session_key": key, "driver_number": n, "lap_number": ln, "date_start": l.get("date_start"),
            "lap_time": t, "s1": l.get("duration_sector_1"), "s2": l.get("duration_sector_2"),
            "s3": l.get("duration_sector_3"), "i1_speed": l.get("i1_speed"), "i2_speed": l.get("i2_speed"),
            "st_speed": l.get("st_speed"), "pit_out": _flag(l.get("is_pit_out_lap")) or 0,
            "pit_in": int((n, ln) in pit_in), "stint": stint, "compound": compound, "tyre_age": age,
            "neutral": int(neutral), "deleted": int(deleted), "clean": 0,
            "position": pos.at(n, end) if is_race else None, "interval_ahead": gap[0], "gap_to_leader": gap[1],
            "track_temp": w.get("track_temperature"), "air_temp": w.get("air_temperature"),
            "rainfall": _flag(w.get("rainfall")),
        })

    # clean: a representative lap. Race laps are judged against the driver's median, other sessions against the
    # driver's best (most laps there are out-laps and cool-downs); wet and dry tyres are judged separately.
    groups = {}
    for r in rows:
        if not r["lap_time"] or r["pit_out"] or r["pit_in"] or r["neutral"] or r["deleted"] or \
                (is_race and r["lap_number"] == 1):
            continue
        fam = "wet" if r["compound"] in WET_COMPOUNDS else "dry"
        groups.setdefault((r["driver_number"], fam), []).append(r)
    for grp in groups.values():
        ref = median(r["lap_time"] for r in grp) if is_race else min(r["lap_time"] for r in grp)
        for r in grp:
            r["clean"] = int(r["lap_time"] <= ref * CLEAN_LIMIT)
    return rows


def overtake_rows(key, overtakes, pits, rc, laps):
    """OpenF1's overtakes are every swap of position, so a car in the pit lane is "overtaken" by everyone who drives
    past it, and so is a car crawling back with damage. on_track = 1 keeps the passes on the track; it is 0 when:
    - either car is in the pit lane or just leaving it (OpenF1's pit date is near the exit, so the window reaches
      back the whole lane time),
    - the overtaken car is slow (damage, a spin, retiring): that lap is more than SLOW_LAP x its usual lap,
      or it never finished the lap,
    - it happens under a Safety Car, VSC or red flag, or after the chequered flag,
    - the overtaking car "passes" several cars at the same instant (a timing correction or classification jump),
    - a cascade: one car passed by CASCADE[0] or more cars within CASCADE[1] s, or (after lap 1) passing that many.
      Racing almost never does that; a timing glitch, or a car that has stopped without a slow lap yet, does,
    - ping-pong: the same two cars swap back within PING_PONG s. Nose to tail, the timing loops flip their order
      back and forth; a pass re-taken that quickly nets to nothing anyway."""
    spans = neutral_periods(rc)
    chequered = min((_ts(m["date"]) for m in rc if m.get("date") and (m.get("flag") or "").upper() == "CHEQUERED"),
                    default=None)
    pitting = {}
    for p in pits:
        t = _ts(p.get("date"))
        if t:
            lane = _num(p.get("lane_duration") or p.get("pit_duration")) or 25
            pitting.setdefault(p.get("driver_number"), []).append((t - lane - PIT_BEFORE, t + PIT_AFTER))
    wet = lambda l: "wet" if l["compound"] in WET_COMPOUNDS else "dry"
    lap_at, clean, timed = {}, {}, {}
    for l in laps:
        start = _ts(l["date_start"])
        if start and l["lap_time"]:
            lap_at.setdefault(l["driver_number"], []).append((start, start + l["lap_time"], l["lap_number"],
                                                              l["lap_time"], wet(l)))
            timed.setdefault(l["driver_number"], []).append(l["lap_time"])
            if l["clean"]:
                clean.setdefault((l["driver_number"], wet(l)), []).append(l["lap_time"])
    usual = {k: median(v) for k, v in clean.items()}
    first = [x[3] for ls in lap_at.values() for x in ls if x[2] == 1]
    usual_first = median(first) if first else None

    def lap_of(n, t):
        return next((x for x in lap_at.get(n, ()) if x[0] <= t < x[1]), None) if t is not None else None

    def slow(n, t):
        lap = lap_of(n, t)
        if lap is None:
            return True
        ref = usual_first if lap[2] == 1 else usual.get((n, lap[4])) or (median(timed[n]) if n in timed else None)
        return bool(ref) and lap[3] > ref * SLOW_LAP

    batch = Counter((o.get("date"), o.get("overtaking_driver_number")) for o in overtakes)
    times = {"overtaking": {}, "overtaken": {}}
    for o in overtakes:
        t = _ts(o.get("date"))
        if t is not None:
            for side in times:
                times[side].setdefault(o.get(side + "_driver_number"), []).append(t)
    for side in times.values():
        for ts in side.values():
            ts.sort()

    def cascade(side, n, t):
        ts = times[side].get(n, [])
        return any(bisect_right(ts, s + CASCADE[1]) - bisect_right(ts, s - 1e-6) >= CASCADE[0]
                   for s in ts if t - CASCADE[1] <= s <= t)

    swaps = {}
    for o in overtakes:
        t = _ts(o.get("date"))
        if t is not None:
            swaps.setdefault((o.get("overtaking_driver_number"), o.get("overtaken_driver_number")), []).append(t)

    def ping_pong(a, b, t):
        return any(abs(s - t) <= PING_PONG for s in swaps.get((b, a), ()))

    out = []
    for o in overtakes:
        a, b, t = o.get("overtaking_driver_number"), o.get("overtaken_driver_number"), _ts(o.get("date"))
        real = (t is not None and not ping_pong(a, b, t) and batch[(o.get("date"), a)] == 1
                and not (chequered and t > chequered)
                and not any(s <= t < e for s, e in spans)
                and not any(s <= t <= e for n in (a, b) for s, e in pitting.get(n, ()))
                and not slow(b, t) and not cascade("overtaken", b, t))
        mine = lap_of(a, t)
        if real and mine and mine[2] > 1 and cascade("overtaking", a, t):
            real = False
        out.append({"session_key": key, "date": o.get("date"), "lap_number": mine[2] if mine else None,
                    "overtaking": a, "overtaken": b, "position": o.get("position"), "on_track": int(real)})
    return out


# ------------------------------------------------------------------------------------------------ sessions
def _insert(db, table, rows):
    rows = list(rows)
    if rows:
        cols = list(rows[0])
        db.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                       [tuple(r[c] for c in cols) for r in rows])


def _grid(src, rnd, session):
    """{number: grid slot} and where it came from: OpenF1's starting grid, or the qualifying order when that's
    missing (it often is): penalties are then not applied."""
    grid = {g["driver_number"]: g.get("position") for g in src.get("starting_grid") if g.get("position")}
    if grid:
        return grid, "official"
    for name in GRID_FROM.get(session["session_name"], ()):
        q = rnd.sessions.get(name)
        if q:
            rows = Source(q["session_key"]).get("session_result")
            return {r["driver_number"]: r["position"] for r in rows if r.get("position")}, "qualifying"
    return {}, None


def compile_session(db, session, rnd, year):
    """Fetch one session and (re)write its rows in every table."""
    key = session["session_key"]
    src = Source(key)
    stype = session.get("session_type")
    is_race = stype == "Race"
    drivers = _drivers(src)
    rc = src.get("race_control")
    weather = src.get("weather")
    pits = src.get("pit")
    stints = merge_stints(src.get("stints"), pits)
    results = src.get("session_result")
    positions = src.get("position") if is_race else []
    intervals = src.get("intervals") if is_race else []
    overtakes = src.get("overtakes") if is_race else []
    laps = lap_rows(key, stype, src.get("laps"), stints, pits, rc, weather, positions, intervals)
    grid, grid_source = _grid(src, rnd, session) if is_race else ({}, None)

    for t in TABLES:
        db.execute(f"DELETE FROM {t} WHERE session_key = ?", (key,))
    end = _ts(session.get("date_end")) or 0
    _insert(db, "sessions", [{
        "session_key": key, "year": year, "round": rnd.number, "meeting_key": session.get("meeting_key"),
        "meeting_name": rnd.name, "circuit": session.get("circuit_short_name") or rnd.circuit,
        "location": session.get("location") or rnd.location, "country": session.get("country_name") or rnd.country,
        "session_name": session.get("session_name"), "session_type": stype, "date_start": session.get("date_start"),
        "date_end": session.get("date_end"), "source": src.via, "final": int(time.time() - end > FINAL_AFTER),
        "compiled_at": time.time()}])
    _insert(db, "drivers", ({"session_key": key, "driver_number": n, "code": d["code"], "name": d["name"],
                             "team": d["team"], "colour": d["colour"]} for n, d in drivers.items()))
    res = []
    for r in results:
        dur, gap = r.get("duration"), r.get("gap_to_leader")
        qs = dur if isinstance(dur, list) else []
        res.append({"session_key": key, "driver_number": r["driver_number"], "position": r.get("position"),
                    "status": _status(r), "points": _num(r.get("points")), "laps": r.get("number_of_laps"),
                    "duration": None if qs else _num(dur), "gap_to_leader": None if isinstance(gap, list) else _num(gap),
                    "laps_down": _laps_down(gap), "q1": _num(qs[0]) if len(qs) > 0 else None,
                    "q2": _num(qs[1]) if len(qs) > 1 else None, "q3": _num(qs[2]) if len(qs) > 2 else None,
                    "grid": grid.get(r["driver_number"]), "grid_source": grid_source if grid else None})
    _insert(db, "results", res)
    _insert(db, "laps", laps)
    _insert(db, "stints", ({"session_key": key, "driver_number": s["driver_number"], "stint": s.get("stint_number"),
                            "compound": s.get("compound"), "lap_start": s.get("lap_start"), "lap_end": s.get("lap_end"),
                            "tyre_age_at_start": s.get("tyre_age_at_start")} for s in stints))
    new_set = {(s["driver_number"], s.get("lap_start")) for s in stints if s["stint_number"] > 1}
    _insert(db, "pits", ({"session_key": key, "driver_number": p.get("driver_number"), "lap_number": p.get("lap_number"),
                          "date": p.get("date"), "lane_duration": _num(p.get("lane_duration") or p.get("pit_duration")),
                          "stop_duration": _num(p.get("stop_duration")),
                          # a tyre change: a new stint starts on the next lap (drive-throughs behind the Safety Car,
                          # penalties and retirements in the pits aren't)
                          "stop": int(any((p.get("driver_number"), (p.get("lap_number") or 0) + d) in new_set
                                          for d in (0, 1, 2)))} for p in pits))
    _insert(db, "weather", ({"session_key": key, "date": w.get("date"), "air_temp": w.get("air_temperature"),
                             "track_temp": w.get("track_temperature"), "humidity": w.get("humidity"),
                             "pressure": w.get("pressure"), "rainfall": _flag(w.get("rainfall")),
                             "wind_speed": w.get("wind_speed"), "wind_direction": w.get("wind_direction")}
                            for w in weather))
    _insert(db, "race_control", ({"session_key": key, "date": m.get("date"), "lap_number": m.get("lap_number"),
                                  "category": m.get("category"), "flag": m.get("flag"), "scope": m.get("scope"),
                                  "sector": m.get("sector"), "driver_number": m.get("driver_number"),
                                  "message": m.get("message")} for m in rc))
    _insert(db, "overtakes", overtake_rows(key, overtakes, pits, rc, laps))
    _insert(db, "positions", ({"session_key": key, "date": p.get("date"), "driver_number": p.get("driver_number"),
                               "position": p.get("position")} for p in positions))
    _insert(db, "intervals", ({"session_key": key, "date": i.get("date"), "driver_number": i.get("driver_number"),
                               "gap_to_leader": _num(i.get("gap_to_leader")), "interval": _num(i.get("interval")),
                               "laps_down": _laps_down(i.get("gap_to_leader"))} for i in intervals))
    return len(laps)


def compile_season(year, kinds=DEFAULT_KINDS, path=None, refresh=False, progress=lambda msg: None, telemetry=(),
                   rounds=None):
    """Compile every finished session of the given kinds. Returns (path, summary) where summary lists the sessions
    compiled, skipped (already final in the dataset), not run yet, and failed (with the reason), and the sessions
    whose telemetry was measured.

    telemetry: kinds of session (telemetry.TEL_KINDS: "quali", "race") to add per-corner telemetry for - slow the
    first time (minutes a session). rounds: only these round numbers."""
    path = Path(path or default_path(year))
    season = Season(year).load(lambda m: progress(f"season: {m}"))
    names = {n for k in kinds for n in KINDS[k]}
    db = connect(path)
    summary = {"compiled": [], "skipped": [], "pending": [], "failed": [], "telemetry": []}
    try:
        final = {k for (k,) in db.execute("SELECT session_key FROM sessions WHERE final = 1")}
        for rnd in season.rounds:
            if rnd.cancelled or (rounds and rnd.number not in rounds):
                continue
            for name, s in sorted(rnd.sessions.items(), key=lambda kv: kv[1].get("date_start") or ""):
                if name not in names:
                    continue
                label = f"R{rnd.number} {rnd.name} - {name}"
                if not _session_over(s):
                    summary["pending"].append(label)
                    continue
                if s["session_key"] in final and not refresh:
                    summary["skipped"].append(label)
                    continue
                progress(label)
                try:
                    n = compile_session(db, s, rnd, year)
                except api.LiveSessionLock:
                    db.rollback()
                    raise
                except (api.APIError, KeyError, TypeError, ValueError) as e:
                    db.rollback()
                    log.warning("could not compile %s: %s", label, e)
                    summary["failed"].append((label, str(e).splitlines()[0] if str(e) else type(e).__name__))
                    continue
                db.commit()
                summary["compiled"].append((label, n))
        if telemetry:
            _compile_telemetry(db, path, telemetry, rounds, refresh, summary, progress)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('year', ?)", (str(year),))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('compiled_at', ?)", (str(time.time()),))
        db.commit()
    finally:
        db.close()
    return path, summary


def _compile_telemetry(db, path, kinds, rounds, refresh, summary, progress):
    """Per-corner telemetry for the dataset's sessions of these kinds that don't have it yet (all with refresh).
    The raw telemetry is kept next to the dataset, so measuring again needs no download."""
    from . import telemetry
    names = [n for k in kinds for n in telemetry.TEL_KINDS[k]]
    marks = ",".join("?" * len(names))
    done = {k for (k,) in db.execute("SELECT session_key FROM tel_sessions")}
    db.row_factory = sqlite3.Row
    try:
        sessions = db.execute(f"SELECT * FROM sessions WHERE session_name IN ({marks}) ORDER BY date_start", names).fetchall()
    finally:
        db.row_factory = None
    for s in sessions:
        if (rounds and s["round"] not in rounds) or (s["session_key"] in done and not refresh):
            continue
        label = f"R{s['round']} {s['meeting_name']} - {s['session_name']}"
        try:
            n = telemetry.compile_session(db, s, path.parent, lambda m: progress(f"{label}: {m}"))
        except api.LiveSessionLock:
            db.rollback()
            raise
        except (api.APIError, KeyError, TypeError, ValueError, OSError) as e:
            db.rollback()
            log.warning("could not measure the telemetry of %s: %s", label, e)
            summary["failed"].append((f"{label} telemetry", str(e).splitlines()[0] if str(e) else type(e).__name__))
            continue
        db.commit()
        summary["telemetry"].append((label, n))


# ------------------------------------------------------------------------------------------------ reading it back
def counts(db):
    return {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def export_csv(db, out_dir):
    """Every table (and the laps_full view) as <name>.csv in out_dir. Returns the paths written."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in TABLES + ("laps_full",):
        cur = db.execute(f"SELECT * FROM {name}")
        p = out / f"{name}.csv"
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([c[0] for c in cur.description])
            w.writerows(cur)
        paths.append(p)
    return paths


GLANCE = [
    ("Laps led", "most laps at the front at the end of a lap, races and sprints",
     """SELECT d.code, COUNT(*) FROM laps l JOIN drivers d USING (session_key, driver_number)
        WHERE l.position = 1 GROUP BY d.code ORDER BY 2 DESC LIMIT 5"""),
    ("Positions gained", "grid to finish, summed over the races (grid = qualifying order when OpenF1 has none)",
     """SELECT d.code, SUM(r.grid - r.position) FROM results r JOIN drivers d USING (session_key, driver_number)
        JOIN sessions s USING (session_key)
        WHERE s.session_name = 'Race' AND r.status = 'Finished' AND r.grid > 0
        GROUP BY d.code ORDER BY 2 DESC LIMIT 5"""),
    ("Overtakes made", "passes on track (not pit stops, not under a Safety Car or VSC)",
     """SELECT d.code, COUNT(*) FROM overtakes o JOIN drivers d
        ON d.session_key = o.session_key AND d.driver_number = o.overtaking
        WHERE o.on_track = 1 GROUP BY d.code ORDER BY 2 DESC LIMIT 5"""),
    ("Longest stints", "laps on one set of tyres in a race",
     """SELECT d.code || ' ' || s.meeting_name || ' (' || st.compound || ')', st.lap_end - st.lap_start + 1
        FROM stints st JOIN drivers d USING (session_key, driver_number) JOIN sessions s USING (session_key)
        WHERE s.session_type = 'Race' AND st.lap_end IS NOT NULL ORDER BY 2 DESC LIMIT 5"""),
    ("Pit lane time", "median time from pit entry to exit, by circuit (shortest first)",
     """SELECT s.circuit, p.lane_duration FROM pits p JOIN sessions s USING (session_key)
        WHERE s.session_name = 'Race' AND p.stop = 1 AND p.lane_duration BETWEEN 10 AND 60"""),
    ("Neutralised racing", "share of race laps under a Safety Car, VSC or red flag, by circuit",
     """SELECT s.circuit, ROUND(100.0 * SUM(l.neutral) / COUNT(*), 1) || '%' FROM laps l JOIN sessions s
        USING (session_key) WHERE s.session_name = 'Race' GROUP BY s.circuit HAVING SUM(l.neutral) > 0
        ORDER BY SUM(l.neutral) * 1.0 / COUNT(*) DESC LIMIT 5"""),
]


def glance(db):
    """A few season-wide numbers straight from the dataset: [(title, note, [(label, value), ...])]."""
    out = []
    for title, note, sql in GLANCE:
        rows = [tuple(r) for r in db.execute(sql)]
        if title == "Pit lane time":          # SQLite has no median: done here
            by = {}
            for circuit, t in rows:
                by.setdefault(circuit, []).append(t)
            rows = sorted(((c, round(median(ts), 1)) for c, ts in by.items() if len(ts) >= 5), key=lambda r: r[1])[:5]
        out.append((title, note, rows))
    return out
