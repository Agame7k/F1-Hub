"""FastF1 worker. Runs in the project's .venv (FastF1 needs pandas/numpy), so the main app stays plain Python.

Protocol: one JSON object per line.
  stdin:  {"id": 1, "cmd": "schedule", "args": {...}}
  stdout: {"id": 1, "ok": <result>}  or  {"id": 1, "error": "..."}
The first line written is {"ready": true, "version": "..."}.

Everything handed back is shaped like OpenF1's rows, so the dashboard's tracker can replay a FastF1 session
(2018 onwards, or a live-timing recording made with FastF1) with the same code it uses for OpenF1.
Sessions are referred to by a "ref": {"year", "round", "idx"} (idx = FastF1's session number 1-5), plus
"livedata": [files] for a recording.
"""
import ast
import gzip
import json
import math
import os
import re
import signal
import sys
import time
import warnings
from collections import OrderedDict
from datetime import timedelta, timezone

OUT = sys.stdout          # the protocol channel; anything FastF1 prints goes to stderr instead
sys.stdout = sys.stderr
warnings.filterwarnings("ignore")

CACHE = os.environ.get("F1HUB_FF1_CACHE") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                          ".f1cache", "fastf1")
sys.path.append(os.path.dirname(os.path.abspath(__file__)))     # (-I leaves this folder off the path)
import ff1_signin  # noqa: E402
DURATION = {"Race": 2.5, "Sprint": 1.0}     # hours, for the schedule's date_end (FastF1 only knows the start)
LOADED = OrderedDict()                       # ref key -> loaded Session (laps + telemetry), newest last
KEEP = 2


def iso(ts):
    """pandas Timestamp / datetime (naive = UTC) -> ISO string with millisecond precision, always the same format
    so the tracker's string comparisons work."""
    if ts is None or ts != ts:          # None / NaT
        return None
    dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def secs(td):
    if td is None or td != td:
        return None
    return round(td.total_seconds(), 3)


def num(v, cast=float):
    try:
        if v is None or v != v:
            return None
        return cast(v)
    except (TypeError, ValueError):
        return None


def make_key(year, rnd, idx, rec=0):
    return year * 100000 + rnd * 1000 + idx * 10 + rec


# ------------------------------------------------------------------------------------------------ schedule
def schedule(year):
    import fastf1
    ev = fastf1.get_event_schedule(int(year), include_testing=False)
    sessions, meetings = [], []
    for _, e in ev.iterrows():
        rnd = int(e["RoundNumber"])
        if rnd < 1:
            continue
        mk = int(year) * 100 + rnd
        meetings.append({"meeting_key": mk, "meeting_name": e["EventName"], "location": e["Location"],
                         "country_name": e["Country"], "year": int(year), "round": rnd,
                         "date_start": iso(e.get("Session1DateUtc")), "format": e.get("EventFormat")})
        for i in range(1, 6):
            name = e.get(f"Session{i}")
            start = e.get(f"Session{i}DateUtc")
            if not name or name == "None" or start is None or start != start:
                continue
            kind = "Race" if name in ("Race", "Sprint") else "Qualifying" if "Qualifying" in name or "Shootout" in name else "Practice"
            dur = DURATION.get(name, 1.0)
            sessions.append({"session_key": make_key(int(year), rnd, i), "session_name": name, "session_type": kind,
                             "date_start": iso(start), "date_end": iso(start + timedelta(hours=dur)),
                             "meeting_key": mk, "year": int(year), "location": e["Location"],
                             "country_name": e["Country"], "circuit_short_name": e["Location"],
                             "meeting_name": e["EventName"], "round": rnd, "idx": i, "source": "fastf1"})
    return {"sessions": sessions, "meetings": meetings}


def resolve(year, date):
    """OpenF1 session (year + start time) -> FastF1 round and session number."""
    from datetime import datetime
    want = datetime.fromisoformat(date).timestamp()
    best = None
    for s in schedule(year)["sessions"]:
        d = abs(datetime.fromisoformat(s["date_start"]).timestamp() - want)
        if d < 3 * 3600 and (best is None or d < best[0]):
            best = (d, s)
    if not best:
        raise LookupError(f"FastF1 has no session at {date}")
    return {"year": int(year), "round": best[1]["round"], "idx": best[1]["idx"], "name": best[1]["session_name"]}


# ------------------------------------------------------------------------------------------------ loading
def _ref_key(ref):
    return json.dumps(ref, sort_keys=True)


def _is_keyframe(line):
    return line.rstrip().endswith(", '']")


def _keyframe_stamp(lines):
    """When the recorder connected: the keyframe Heartbeat's Utc, else the first timestamped line's time."""
    for line in lines:
        if not _is_keyframe(line):
            m = re.search(r"'(\d{4}-\d\d-\d\dT[^']+)'\]\s*$", line)
            if m:
                return m.group(1)
            continue
        if line.startswith("['Heartbeat'"):
            try:
                utc = json.loads(ast.literal_eval(line.strip())[1]).get("Utc") or ""
            except (ValueError, SyntaxError, AttributeError):
                continue
            if len(utc) >= 19:
                return utc[:23].rstrip("Z") + "Z"
    return None


def _decode_keyframes(lines):
    """FastF1 3.9's client opens a recording with a keyframe: the full state at the moment it connected (session
    info, the session start, driver list, starting tyres, race control so far...) as JSON strings with an empty
    timestamp. FastF1's own reader can't parse those lines and drops them silently, so a recording started after
    the session began loses its session info and start time and won't load. Rewrite them as normal lines, stamped
    with the connection time."""
    stamp = None
    out = []
    for line in lines:
        if _is_keyframe(line):
            stamp = stamp or _keyframe_stamp(lines)
            try:
                cat, payload, _ = ast.literal_eval(line.strip())
                msg = json.loads(payload) if isinstance(payload, str) else payload
            except (ValueError, SyntaxError, TypeError):
                out.append(line)
                continue
            at = stamp
            if cat == "SessionStatus" and isinstance(msg, dict) and msg.get("Status") == "Started":
                at = _started_at(lines) or stamp      # the session began before the recorder joined: say when
            if at:
                # FastF1's reader turns ' into " before decoding: keep apostrophes inside strings out of its way
                line = json.dumps([cat, msg, at]).replace("'", "\\u0027") + "\n"
        out.append(line)
    return out


def _started_at(lines):
    """The first 'Started' in SessionData's StatusSeries (list in a keyframe, dict in an update): its Utc string."""
    for line in lines:
        if "SessionData" not in line[:20] or "Started" not in line:
            continue
        try:
            if _is_keyframe(line):
                msg = json.loads(ast.literal_eval(line.strip())[1])
            else:
                msg = json.loads(line.replace("'", '"').replace("True", "true").replace("False", "false"))[1]
            series = msg.get("StatusSeries") or []
            entries = series.values() if isinstance(series, dict) else series
            for e in entries:
                if isinstance(e, dict) and e.get("SessionStatus") == "Started" and e.get("Utc"):
                    return e["Utc"]
        except (ValueError, SyntaxError, AttributeError, IndexError, TypeError):
            continue
    return None


def _livedata(files):
    from fastf1.livetiming.data import LiveTimingData

    class RecordingData(LiveTimingData):
        def _load_single_file(self, data, *, is_first_file, next_line):
            data = _decode_keyframes(data)
            if next_line is not None:
                next_line = _decode_keyframes([next_line])[0]
            super()._load_single_file(data, is_first_file=is_first_file, next_line=next_line)

        def _try_set_correct_start_date(self, data):
            # FastF1's version takes the first line mentioning SessionStatus and Started, which in a keyframe is
            # SessionInfo (no StatusSeries): look in SessionData instead
            from fastf1.utils import to_datetime
            started = _started_at(data)
            self._start_date = to_datetime(started) if started else None

    return RecordingData(*files)


def _snapshot_only(files):
    """Nothing but the keyframe (and heartbeats): recorded after the session ended."""
    for f in files:
        try:
            with open(f) as fh:
                for line in fh:
                    if not _is_keyframe(line) and not line.startswith("['Heartbeat'"):
                        return False
        except OSError:
            return False
    return True


def _no_parse_cache():
    """FastF1 caches what it parses under the session's archive path, whether it came from F1's archive or from a
    recording: a recording read part-way through (or a second recording of the session) would then be served the
    stale copy, and so would F1's archive after an F1 TV sign-in. Recordings are parsed fresh every time (the
    export they produce is cached by F1 Hub, keyed by the file's mtime); downloads stay cached."""
    import fastf1
    return fastf1.Cache.disabled(disable_http_cache=False)


def load(ref, telemetry=True):
    import fastf1
    k = _ref_key(dict(ref, telemetry=telemetry))
    if k in LOADED:
        LOADED.move_to_end(k)
        return LOADED[k]
    s = fastf1.get_session(int(ref["year"]), int(ref["round"]), int(ref["idx"]))
    kw = {"laps": True, "telemetry": telemetry, "weather": True, "messages": True}
    if ref.get("livedata"):
        kw["livedata"] = _livedata(ref["livedata"])
        with _no_parse_cache():
            s.load(**kw)
        s._f1hub_livedata = kw["livedata"]
    else:
        s.load(**kw)
    if ref.get("livedata") and s._t0_date is None and kw["livedata"]._start_date is not None:
        # a recording of F1's free feed has no car telemetry, which is what FastF1 works t0 out from; but a
        # recording's session times all count from its own start date, so that is t0
        import pandas as pd
        s._t0_date = pd.Timestamp(kw["livedata"]._start_date).round("ms")
        if hasattr(s, "_laps"):
            s._laps["LapStartDate"] = s._laps["LapStartTime"] + s._t0_date
    try:
        ok = s.t0_date is not None and not s.laps.empty
    except Exception:
        ok = False
    if not ok:
        msg = ("This recording has no lap timing yet - record while a session is running (it can be played back part-way through)"
               if ref.get("livedata") else "F1's live-timing archive returned no data for this session")
        if ref.get("livedata") and _snapshot_only(ref["livedata"]):
            msg = ("This recording only holds the snapshot F1 sends on connecting - it was started after the session had"
                   " finished, so there are no laps to play back")
        if int(ref["year"]) >= 2023 and not ref.get("livedata") and not auth_status()["signed_in"]:
            msg += (" - from 2023 on F1 only serves it with an F1 TV login: sign in from Explore > FastF1, or put"
                    " F1_USERNAME and F1_PASSWORD in .openf1.env and restart F1 Hub")
        raise RuntimeError(msg)
    LOADED[k] = s
    while len(LOADED) > KEEP:
        LOADED.popitem(last=False)
    return s


def _gap(v):
    """FastF1 gap strings -> OpenF1's: 0.216 / "+1 LAP" / None."""
    if v is None or v != v:
        return None
    v = str(v).strip()
    if not v or v.upper().startswith("LAP"):
        return None
    if v.upper().endswith("L"):          # "1 L", "1L"
        n = "".join(ch for ch in v if ch.isdigit()) or "1"
        return f"+{n} LAP" + ("S" if int(n) > 1 else "")
    try:
        return round(float(v.lstrip("+")), 3)
    except ValueError:
        return None


def _status_rows(s, quali):
    """SessionStatus rows like OpenF1's race_control ('SESSION STARTED' ... with qualifying_phase)."""
    out, phase, ended = [], 0, True
    for _, r in s.session_status.iterrows():
        st = str(r["Status"])
        word = {"Started": "STARTED", "Finished": "FINISHED", "Aborted": "ABORTED", "Ends": None,
                "Finalised": None, "Inactive": None}.get(st)
        if not word:
            continue
        if quali and word == "STARTED" and ended:
            phase += 1
        ended = word == "FINISHED"
        out.append({"date": iso(s.t0_date + r["Time"]), "category": "SessionStatus", "flag": None,
                    "message": f"SESSION {word}", "lap_number": None, "driver_number": None, "scope": None,
                    "sector": None, "qualifying_phase": phase if quali else None})
    return out


def _merged_lines(ld, topic):
    """A live-timing topic's per-driver Lines with every update applied: the state at the end of the recording."""
    def merge(a, b):
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                merge(a[k], v)
            else:
                a[k] = v
    out = {}
    for _, msg in (ld.get(topic) if ld.has(topic) else []):
        lines = msg.get("Lines") if isinstance(msg, dict) else None
        if isinstance(lines, dict):
            for n, v in lines.items():
                if isinstance(v, dict):
                    merge(out.setdefault(n, {}), v)
    return out


def _recorded_results(s, kind, quali, year, start, t0):
    """The classification and grid from a recording's own timing, for when FastF1 has none: it takes them from
    Ergast, which hasn't got a session that has only just finished. Provisional: decisions after the flag aren't
    in it."""
    import rules
    ld = getattr(s, "_f1hub_livedata", None)
    if ld is None:
        return None, None
    final = _merged_lines(ld, "TimingData")
    app = _merged_lines(ld, "TimingAppData")
    if not final:
        return None, None
    rows = {}
    for n, v in final.items():
        dn = num(n, int)
        if dn is None:
            continue
        best = v.get("BestLapTimes") or []          # qualifying: one per segment (a list, or a dict of updates)
        best = [best[k] for k in sorted(best, key=int)] if isinstance(best, dict) else best
        rows[dn] = {"pos": num(v.get("Position"), int), "laps": num(v.get("NumberOfLaps"), int) or 0,
                    "gap": v.get("GapToLeader"),
                    "best": [secs_of(b.get("Value")) if isinstance(b, dict) else None for b in best]}
    grid = []
    if kind == "Race":
        for n, v in app.items():
            g = num(v.get("GridPos"), int)
            if g and num(n, int) is not None:
                grid.append({"driver_number": int(n), "position": g})
    result = []
    lead = max((r["laps"] for r in rows.values()), default=0)
    sprint = s.name == "Sprint"
    table = rules.rules_for(year)["sprint_points" if sprint else "gp_points"]
    winner_time = None
    if kind == "Race":
        laps = s.laps
        first = sorted(((r["pos"], dn) for dn, r in rows.items() if r["pos"]), key=lambda x: x[0])
        if first:
            won = laps[laps["DriverNumber"] == str(first[0][1])]
            if not won.empty and won["Time"].notna().any():
                winner_time = secs(t0 + won["Time"].max() - start)
    for dn, r in rows.items():
        out = {"driver_number": dn, "number_of_laps": r["laps"], "dns": False, "dsq": False}
        if kind == "Race":
            # classified: covered 90% of the winner's distance (rounded down to whole laps), retired or not
            classified = r["laps"] > 0 and r["laps"] >= int(lead * 0.9)
            pos = r["pos"] if classified else None
            out.update(position=pos, dnf=not classified,
                       points=float(table[pos - 1]) if pos and pos <= len(table) else 0.0,
                       duration=winner_time if pos == 1 else None,
                       gap_to_leader=0 if pos == 1 else _gap(r["gap"]) if pos else None)
        else:
            out.update(position=r["pos"], dnf=False, points=None)
            if quali:
                out["duration"] = (r["best"] + [None] * 3)[:3]
            else:
                out["duration"] = next((b for b in r["best"] if b), None)
        result.append(out)
    return result, grid


def _recorded_radio(s, key, mk):
    """Team radio clips from a recording's TeamRadio topic, as OpenF1's team_radio rows (FastF1 doesn't read them).
    The keyframe repeats the clips so far, so they're de-duplicated."""
    import pandas as pd
    ld = getattr(s, "_f1hub_livedata", None)
    if ld is None or not ld.has("TeamRadio"):
        return []
    base = "https://livetiming.formula1.com" + s.api_path
    out, seen = [], set()
    for _, msg in ld.get("TeamRadio"):
        caps = msg.get("Captures") if isinstance(msg, dict) else None
        caps = caps.values() if isinstance(caps, dict) else caps or []
        for c in caps:
            if not isinstance(c, dict) or not c.get("Path") or not c.get("Utc"):
                continue
            n = num(c.get("RacingNumber"), int)
            if n is None or (n, c["Path"]) in seen:
                continue
            seen.add((n, c["Path"]))
            try:
                date = iso(pd.Timestamp(c["Utc"]))
            except ValueError:
                continue
            out.append({"date": date, "driver_number": n, "recording_url": base + c["Path"],
                        "session_key": key, "meeting_key": mk})
    out.sort(key=lambda r: r["date"])
    return out


def secs_of(v):
    """A live-timing time string ("1:18.737", "48.861") -> seconds."""
    if not v or not isinstance(v, str):
        return None
    try:
        m, _, sec = v.rpartition(":")
        return round(int(m or 0) * 60 + float(sec), 3)
    except ValueError:
        return None


def export(ref, out_path):
    """Load the whole session and write it as OpenF1-shaped rows + compact telemetry (gzip JSON)."""
    s = load(ref)
    info = s.session_info or {}
    meeting = info.get("Meeting") or {}
    circuit = (meeting.get("Circuit") or {}).get("Key")
    year = int(ref["year"])
    rec = int(ref.get("rec") or 0)
    key = make_key(year, int(ref["round"]), int(ref["idx"]), rec)
    mk = year * 100 + int(ref["round"])
    name = s.name
    quali = "Qualifying" in name or "Shootout" in name
    kind = "Race" if name in ("Race", "Sprint") else "Qualifying" if quali else "Practice"
    gmt = info.get("GmtOffset")
    if gmt is not None and gmt == gmt:
        mins = int(gmt.total_seconds() // 60)
        gmt = f"{'-' if mins < 0 else ''}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}:00"     # OpenF1's "-03:00:00"
    else:
        gmt = "00:00:00"
    t0 = s.t0_date
    statuses = s.session_status
    started = [t0 + r["Time"] for _, r in statuses.iterrows() if str(r["Status"]) == "Started"]
    ends = [t0 + r["Time"] for _, r in statuses.iterrows() if str(r["Status"]) in ("Finished", "Finalised", "Ends")]
    start = started[0] if started else s.date
    end = ends[-1] if ends else (start + timedelta(hours=DURATION.get(name, 1.0)))
    ev = s.event
    session = {"session_key": key, "session_name": name, "session_type": kind,
               "date_start": iso(start - timedelta(minutes=0)), "date_end": iso(end),
               "meeting_key": mk, "year": year, "location": ev.get("Location"), "country_name": ev.get("Country"),
               "circuit_short_name": (meeting.get("Circuit") or {}).get("ShortName") or ev.get("Location"),
               "circuit_key": circuit, "gmt_offset": gmt, "is_cancelled": False, "source": "fastf1",
               "recording": bool(ref.get("livedata"))}
    meet = {"meeting_key": mk, "meeting_name": ev.get("EventName"), "location": ev.get("Location"),
            "country_name": ev.get("Country"), "year": year, "circuit_key": circuit,
            "circuit_short_name": session["circuit_short_name"],
            "circuit_info_url": f"https://api.multiviewer.app/api/v1/circuits/{circuit}/{year}" if circuit else None}

    res = s.results
    drivers, grid, result = [], [], []
    for _, r in res.iterrows():
        n = num(r.get("DriverNumber"), int)
        if n is None:
            continue
        colour = str(r.get("TeamColor") or "") or "888888"
        drivers.append({"driver_number": n, "name_acronym": r.get("Abbreviation"), "full_name": r.get("FullName"),
                        "first_name": r.get("FirstName"), "last_name": r.get("LastName"),
                        "broadcast_name": r.get("BroadcastName"), "team_name": r.get("TeamName"),
                        "team_colour": colour.lstrip("#") if colour != "nan" else "888888",
                        "headshot_url": r.get("HeadshotUrl") or None, "session_key": key, "meeting_key": mk})
        g = num(r.get("GridPosition"), int)
        if kind == "Race" and g:
            grid.append({"driver_number": n, "position": g, "session_key": key})
        status = str(r.get("Status") or "")
        cls = str(r.get("ClassifiedPosition") or "")
        pos = num(r.get("Position"), int)
        row = {"driver_number": n, "position": pos if cls.isdigit() or kind != "Race" else None,
               "points": num(r.get("Points")), "number_of_laps": num(r.get("Laps"), int),
               "dnf": cls == "R", "dns": cls == "N" or status == "Did not start", "dsq": cls == "D",
               "session_key": key, "meeting_key": mk}
        if quali:
            row["duration"] = [secs(r.get(q)) for q in ("Q1", "Q2", "Q3")]
        else:
            row["duration"] = secs(r.get("Time")) if pos == 1 else None
            row["gap_to_leader"] = secs(r.get("Time")) if pos and pos > 1 else 0 if pos == 1 else None
        result.append(row)

    if not any(r["position"] for r in result) and ref.get("livedata"):
        rec_result, rec_grid = _recorded_results(s, kind, quali, year, start, t0)
        if rec_result:
            by = {r["driver_number"]: r for r in rec_result}
            for row in result:
                row.update(by.get(row["driver_number"], {}))
        if rec_grid and not grid:
            grid = [dict(g, session_key=key) for g in rec_grid]

    laps, stints, pits = [], [], []
    L = s.laps
    for _, l in L.iterrows():
        n = num(l.get("DriverNumber"), int)
        ln = num(l.get("LapNumber"), int)
        if n is None or ln is None:
            continue
        laps.append({"driver_number": n, "lap_number": ln, "date_start": iso(l.get("LapStartDate")),
                     "lap_duration": secs(l.get("LapTime")),
                     "duration_sector_1": secs(l.get("Sector1Time")), "duration_sector_2": secs(l.get("Sector2Time")),
                     "duration_sector_3": secs(l.get("Sector3Time")),
                     "i1_speed": num(l.get("SpeedI1"), int), "i2_speed": num(l.get("SpeedI2"), int),
                     "st_speed": num(l.get("SpeedST"), int),
                     "is_pit_out_lap": bool(l.get("PitOutTime") == l.get("PitOutTime") and l.get("PitOutTime") is not None),
                     "compound": l.get("Compound") if isinstance(l.get("Compound"), str) else None,
                     "track_status": str(l.get("TrackStatus") or ""), "deleted": bool(l.get("Deleted") is True),
                     "session_key": key})
    for n, grp in L.groupby("DriverNumber"):
        grp = grp.sort_values("LapNumber")
        dn = int(n)
        for st, sg in grp.groupby("Stint"):
            comp = next((c for c in sg["Compound"] if isinstance(c, str) and c), "UNKNOWN")
            life = num(sg["TyreLife"].iloc[0])
            stints.append({"driver_number": dn, "stint_number": int(st), "compound": comp,
                           "lap_start": int(sg["LapNumber"].min()), "lap_end": int(sg["LapNumber"].max()),
                           "tyre_age_at_start": max(0, int(life) - 1) if life is not None else 0, "session_key": key})
        rows = list(grp.itertuples())
        for a, b in zip(rows, rows[1:]):
            if a.PitInTime == a.PitInTime and a.PitInTime is not None and b.PitOutTime == b.PitOutTime and b.PitOutTime is not None:
                lane = secs(b.PitOutTime - a.PitInTime)
                if lane and lane < 120:       # longer = garage time under a red flag, not a pit stop
                    pits.append({"driver_number": dn, "lap_number": int(a.LapNumber), "date": iso(t0 + a.PitInTime),
                                 "pit_duration": lane, "lane_duration": lane, "stop_duration": None, "session_key": key})

    rc = _status_rows(s, quali)
    phase_at = sorted((m["date"], m["qualifying_phase"]) for m in rc)
    for _, m in s.race_control_messages.iterrows():
        d = iso(m.get("Time"))
        ph = None
        if quali:
            ph = max([p for t, p in phase_at if t <= d] or [1])
        rn = m.get("RacingNumber")
        rc.append({"date": d, "category": m.get("Category"), "flag": m.get("Flag") or None,
                   "message": m.get("Message"), "lap_number": num(m.get("Lap"), int),
                   "driver_number": num(rn, int) if rn not in (None, "") else None,
                   "scope": m.get("Scope") or None, "sector": num(m.get("Sector"), int), "qualifying_phase": ph,
                   "session_key": key})
    rc.sort(key=lambda m: m["date"] or "")

    weather = [{"date": iso(t0 + w["Time"]), "air_temperature": num(w["AirTemp"]), "track_temperature": num(w["TrackTemp"]),
                "humidity": num(w["Humidity"]), "pressure": num(w["Pressure"]), "rainfall": int(bool(w["Rainfall"])),
                "wind_speed": num(w["WindSpeed"]), "wind_direction": num(w["WindDirection"], int), "session_key": key}
               for _, w in s.weather_data.iterrows()]

    position, intervals = [], []
    try:
        from fastf1 import _api as fapi
    except ImportError:
        from fastf1 import api as fapi
    try:
        if ref.get("livedata"):
            with _no_parse_cache():
                _, stream = fapi.timing_data(s.api_path, livedata=_livedata(ref["livedata"]))
        else:
            _, stream = fapi.timing_data(s.api_path)
        last_pos, last_iv = {}, {}
        for r in stream.itertuples():
            n = num(r.Driver, int)
            if n is None:
                continue
            d = iso(t0 + r.Time)
            p = num(r.Position, int)
            if p and last_pos.get(n) != p:
                last_pos[n] = p
                position.append({"date": d, "driver_number": n, "position": p, "session_key": key})
            if kind == "Race":
                g, iv = _gap(r.GapToLeader), _gap(r.IntervalToPositionAhead)
                prev = last_iv.get(n)
                if prev is None or (g, iv) != prev[1] and (t0 + r.Time).timestamp() - prev[0] >= 1.0:
                    last_iv[n] = ((t0 + r.Time).timestamp(), (g, iv))
                    intervals.append({"date": d, "driver_number": n, "gap_to_leader": g if p != 1 else 0.0,
                                      "interval": iv if p != 1 else 0.0, "session_key": key})
    except Exception as e:      # no stream timing: fall back to each lap's end-of-lap position
        print(f"timing stream unavailable ({e}); using lap positions", file=sys.stderr)
        for _, l in L.iterrows():
            p, n = num(l.get("Position"), int), num(l.get("DriverNumber"), int)
            if p and n and l.get("Time") == l.get("Time"):
                position.append({"date": iso(t0 + l["Time"]), "driver_number": n, "position": p, "session_key": key})
    position.sort(key=lambda r: r["date"])
    intervals.sort(key=lambda r: r["date"])

    # telemetry, columnar per driver: t = ms since t0 epoch
    base = t0.tz_localize("UTC").timestamp() if t0.tzinfo is None else t0.timestamp()
    tel = {"t0": base, "loc": {}, "car": {}}
    for n, df in (s.pos_data or {}).items():
        ms = ((df["Date"] - t0).dt.total_seconds() * 1000).round().astype("int64").tolist()
        tel["loc"][str(int(n))] = {"t": ms, "x": df["X"].round().astype("int64").tolist(),
                                   "y": df["Y"].round().astype("int64").tolist(),
                                   "z": df["Z"].round().astype("int64").tolist()}
    for n, df in (s.car_data or {}).items():
        ms = ((df["Date"] - t0).dt.total_seconds() * 1000).round().astype("int64").tolist()
        tel["car"][str(int(n))] = {"t": ms, "speed": df["Speed"].fillna(0).astype("int64").tolist(),
                                   "rpm": df["RPM"].fillna(0).astype("int64").tolist(),
                                   "gear": df["nGear"].fillna(0).astype("int64").tolist(),
                                   "throttle": df["Throttle"].fillna(0).clip(0, 104).astype("int64").tolist(),
                                   "brake": (df["Brake"].astype(bool) * 100).astype("int64").tolist(),
                                   "drs": df["DRS"].fillna(0).astype("int64").tolist()}

    data = {"session": session, "meeting": meet, "drivers": drivers, "starting_grid": grid, "session_result": result,
            "scheduled_laps": num(getattr(s, "total_laps", None), int) if kind == "Race" else None,
            "rows": {"laps": laps, "stints": stints, "pit": pits, "race_control": rc, "weather": weather,
                     "position": position, "intervals": intervals, "overtakes": [], "team_radio": _recorded_radio(s, key, mk),
                     "championship_drivers": []},
            "telemetry": tel, "exported": time.time()}
    tmp = out_path + ".part"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=5) as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, out_path)
    return {"path": out_path, "session": session, "laps": len(laps), "drivers": len(drivers)}


# ------------------------------------------------------------------------------------------------ lap telemetry
def _pick(laps, lap, before):
    import pandas as pd
    laps = laps[laps["LapTime"].notna()]
    if before:
        cut = pd.Timestamp(before).tz_convert("UTC").tz_localize(None)
        laps = laps[(laps["LapStartDate"] + laps["LapTime"]) <= cut]
    if laps.empty:
        return None
    if lap == "best":
        clean = laps[laps["PitOutTime"].isna() & laps["PitInTime"].isna()]
        clean = clean if not clean.empty else laps
        return clean.loc[clean["LapTime"].idxmin()]
    if lap == "last":
        return laps.loc[laps["LapNumber"].idxmax()]
    hit = laps[laps["LapNumber"] == int(lap)]
    return None if hit.empty else hit.iloc[0]


def lap_trace(ref, driver, lap="best", before=None):
    """FastF1's merged car + position telemetry for one lap, with distance: the same shape as f1hub.laptrace
    plus distance in metres, RPM and DRS on the end of each sample."""
    s = load(ref)
    laps = s.laps.pick_drivers(str(driver))
    l = _pick(laps, lap, before)
    if l is None:
        return None
    tel = l.get_telemetry()
    tel = tel[tel["Time"].notna()]
    total = float(tel["Distance"].max() or 1.0)
    samples = []
    for r in tel.itertuples():
        t = r.Time.total_seconds()
        dist = float(r.Distance) if r.Distance == r.Distance else 0.0
        samples.append([round(t, 3), round(max(0.0, min(1.0, dist / total)), 5), num(r.Speed, int),
                        num(r.Throttle, int), 1 if bool(r.Brake) else 0, num(r.nGear, int),
                        num(r.X, int), num(r.Y, int), round(dist, 1), num(r.RPM, int), num(r.DRS, int)])
    return {"driver": int(driver), "lap": int(l["LapNumber"]), "start": iso(l["LapStartDate"]),
            "duration": secs(l["LapTime"]),
            "sectors": [secs(l["Sector1Time"]), secs(l["Sector2Time"]), secs(l["Sector3Time"])],
            "compound": l.get("Compound") if isinstance(l.get("Compound"), str) else None,
            "tyre_life": num(l.get("TyreLife"), int), "length_m": round(total),
            "fields": ["t", "frac", "speed", "throttle", "brake", "gear", "x", "y", "dist", "rpm", "drs"],
            "samples": samples, "source": "fastf1"}


# ------------------------------------------------------------------------------------------------ light queries
def results(ref):
    """Classification only (quick: no laps or telemetry) - for circuit history."""
    import fastf1
    s = fastf1.get_session(int(ref["year"]), int(ref["round"]), int(ref["idx"]))
    s.load(laps=False, telemetry=False, weather=False, messages=False)
    out = []
    for _, r in s.results.iterrows():
        out.append({"pos": num(r.get("Position"), int), "code": r.get("Abbreviation"), "name": r.get("FullName"),
                    "team": r.get("TeamName"), "colour": "#" + (str(r.get("TeamColor") or "888888").lstrip("#") or "888888"),
                    "grid": num(r.get("GridPosition"), int), "status": r.get("Status"), "laps": num(r.get("Laps"), int),
                    "points": num(r.get("Points")), "time": secs(r.get("Time")),
                    "q": [secs(r.get(q)) for q in ("Q1", "Q2", "Q3")]})
    return {"name": s.name, "event": s.event.get("EventName"), "year": int(ref["year"]), "results": out}


def season_results(year):
    """A whole season's race (and sprint / qualifying) classifications from the Jolpica-Ergast archive."""
    from fastf1.ergast import Ergast
    erg = Ergast(result_type="pandas", auto_cast=True)
    rounds = {}

    def grab(fn, kind):
        offset = 0
        while True:
            resp = fn(season=int(year), limit=100, offset=offset)
            desc = resp.description
            for i, row in desc.iterrows():
                rnd = int(row["round"])
                r = rounds.setdefault(rnd, {"round": rnd, "name": row.get("raceName"), "date": str(row.get("raceDate"))[:10],
                                            "location": row.get("locality"), "race": [], "sprint": [], "quali": []})
                for _, x in resp.content[i].iterrows():
                    r[kind].append({"code": x.get("driverCode") or str(x.get("driverId"))[:3].upper(),
                                    "name": f"{x.get('givenName')} {x.get('familyName')}",
                                    "number": num(x.get("number"), int), "team": x.get("constructorName"),
                                    "team_id": x.get("constructorId"), "pos": num(x.get("position"), int),
                                    "grid": num(x.get("grid"), int), "points": num(x.get("points")) or 0,
                                    "status": x.get("status"), "pos_text": str(x.get("positionText") or "")})
            offset += 100
            if offset >= resp.total_results:
                break

    grab(erg.get_race_results, "race")
    try:
        grab(erg.get_sprint_results, "sprint")
    except Exception:
        pass
    try:
        grab(erg.get_qualifying_results, "quali")
    except Exception:
        pass
    return {"year": int(year), "rounds": [rounds[k] for k in sorted(rounds)]}


COMMANDS = {
    "schedule": lambda a: schedule(a["year"]),
    "resolve": lambda a: resolve(a["year"], a["date"]),
    "export": lambda a: export(a["ref"], a["out"]),
    "lap": lambda a: lap_trace(a["ref"], a["driver"], a.get("lap", "best"), a.get("before")),
    "laps": lambda a: lap_list(a["ref"]),
    "results": lambda a: results(a["ref"]),
    "season_results": lambda a: season_results(a["year"]),
    "auth": lambda a: auth_status(),
    "ping": lambda a: {"pong": True},
}


# ------------------------------------------------------------------------------------------------ F1 TV sign-in
def auth_status():
    """The F1 TV token FastF1 has stored on disk, without signing in."""
    from fastf1.internals.f1account.tokens import TokenManager
    tok = TokenManager(auth_func=None).get_stored()
    if tok is None:
        return {"signed_in": False, "expired": False}
    try:
        claims = tok.claims
    except Exception:
        claims = {}
    return {"signed_in": tok.is_valid(), "expired": not tok.is_valid(), "expires_at": tok.expires_at,
            "subscription": claims.get("SubscriptionStatus"), "product": claims.get("SubscribedProduct")}


def lap_list(ref):
    s = load(ref)
    out = {}
    for _, l in s.laps.iterrows():
        n = num(l.get("DriverNumber"), int)
        if n is None:
            continue
        out.setdefault(str(n), []).append({"lap": num(l.get("LapNumber"), int), "t": secs(l.get("LapTime")),
                                           "compound": l.get("Compound") if isinstance(l.get("Compound"), str) else None,
                                           "pit": bool(l.get("PitOutTime") == l.get("PitOutTime") or l.get("PitInTime") == l.get("PitInTime"))})
    return out


def main():
    # Ctrl+C in the dashboard's terminal reaches this process too: leave it to the dashboard, which closes our
    # stdin when it stops (the loop below then ends) - no KeyboardInterrupt traceback in the log
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    import fastf1
    os.makedirs(CACHE, exist_ok=True)
    try:
        fastf1.Cache.enable_cache(CACHE)
    except Exception:
        pass
    fastf1.set_log_level("WARNING")
    ff1_signin.install(CACHE)        # no browser sign-in here; a rejected password isn't sent again
    print(json.dumps({"ready": True, "version": fastf1.__version__}), file=OUT, flush=True)
    for line in sys.stdin:
        try:
            job = json.loads(line)
        except ValueError:
            continue
        try:
            res = COMMANDS[job["cmd"]](job.get("args") or {})
            out = {"id": job.get("id"), "ok": res}
        except Exception as e:      # one bad session must not take the worker down
            out = {"id": job.get("id"), "error": f"{type(e).__name__}: {e}"}
        try:
            print(json.dumps(out, default=str, allow_nan=False) if _finite(out) else json.dumps(_clean(out), default=str),
                  file=OUT, flush=True)
        except BrokenPipeError:     # the dashboard went away mid-job
            return


def _finite(o):
    try:
        json.dumps(o, default=str, allow_nan=False)
        return True
    except ValueError:
        return False


def _clean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


if __name__ == "__main__":
    main()
