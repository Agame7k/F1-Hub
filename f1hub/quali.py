"""Qualifying picture for the live dashboard: Q1/Q2/Q3 clock, per-segment best laps, knockouts,
and live projections for cars on a flying lap. Everything is derived from OpenF1 race_control
(SessionStatus messages carry `qualifying_phase`), laps and position."""
import re
from datetime import datetime

from .racecontrol import CAR_RE

# segment lengths in minutes
DURATIONS = {"Qualifying": (18, 15, 12), "Sprint Qualifying": (12, 10, 8), "Sprint Shootout": (12, 10, 8)}
LAP_RE = re.compile(r"\bLAP (\d+)\b")


def is_quali(session_name):
    return bool(re.search(r"qualifying|shootout", session_name or "", re.I))


def advance_counts(n_cars):
    """How many go through from Q1 and Q2: 20 cars -> 15/10, 22 cars -> 16/10."""
    return n_cars - max(0, (n_cars - 10) // 2), 10


def _ts(s):
    return datetime.fromisoformat(s).timestamp()


def phases(messages, now_ts, session_name="Qualifying"):
    """Segment timeline from SessionStatus messages. Returns {phase: {...}} and the current phase."""
    durs = DURATIONS.get(session_name, DURATIONS["Qualifying"])
    out, cur = {}, None
    for m in sorted((m for m in messages if m.get("date")), key=lambda m: m["date"]):
        p = m.get("qualifying_phase") or cur
        if p:
            cur = max(cur or 0, p)
        if m.get("category") != "SessionStatus" or not p:
            continue
        msg, t = (m.get("message") or "").upper(), _ts(m["date"])
        ph = out.setdefault(p, {"start": None, "end": None, "elapsed": 0.0, "since": None, "suspended": False})
        if "STARTED" in msg or "RESUMED" in msg:
            ph["start"] = ph["start"] or t
            ph["since"], ph["suspended"] = t, False
        elif "ABORTED" in msg or "FINISHED" in msg or "ENDED" in msg:
            if ph["since"]:
                ph["elapsed"] += t - ph["since"]
            ph["since"] = None
            if "ABORTED" in msg:
                ph["suspended"] = True
            else:
                ph["end"] = t
    for p, ph in out.items():
        dur = durs[min(p, 3) - 1] * 60
        run = ph["elapsed"] + (now_ts - ph["since"] if ph["since"] else 0)
        ph["duration"] = dur
        ph["remaining"] = 0 if ph["end"] else max(0.0, dur - run)
        ph["running"] = bool(ph["since"]) and not ph["end"]
    if out:
        cur = max(cur or 0, max(out))
    return out, cur


TIME_DEL_RE = re.compile(r"TIME (\d+):(\d{2}\.\d{3})")


def deleted_laps(messages):
    """Deleted lap times as {(car, "t", seconds)} or {(car, "lap", n)}; reinstated ones are removed again.
    The FIA's lap number doesn't always match OpenF1's, so the lap time is the main key."""
    gone = set()
    for m in sorted((m for m in messages if m.get("date")), key=lambda m: m["date"]):
        msg = (m.get("message") or "").upper()
        if "DELETED" not in msg and "REINSTATED" not in msg:
            continue
        cars = CAR_RE.findall(msg)
        if not cars:
            continue
        car, t, lap = int(cars[0][0]), TIME_DEL_RE.search(msg), LAP_RE.search(msg)
        key = (car, "t", round(int(t.group(1)) * 60 + float(t.group(2)), 3)) if t else \
              (car, "lap", int(lap.group(1))) if lap else None
        if key is None:
            continue
        if "REINSTATED" in msg:
            gone.discard(key)
        else:
            gone.add(key)
    return gone


def _is_deleted(gone, n, no, lap):
    return (n, "lap", no) in gone or (lap.get("t") is not None and (n, "t", round(lap["t"], 3)) in gone)


def phase_of(start_ts, ph):
    """Which segment a lap that started at start_ts belongs to."""
    best = None
    for p in sorted(ph):
        if ph[p]["start"] and start_ts >= ph[p]["start"] - 1:
            best = p
    return best


def picture(tr):
    """tr: a LiveTracker in a qualifying session. Plain JSON-able dict for the GUI."""
    now = tr.now().timestamp()
    ph, cur = phases(tr.messages, now, tr.session.get("session_name"))
    gone = deleted_laps(tr.messages)
    adv1, adv2 = advance_counts(len(tr.drivers) or len(tr.position) or 20)
    cutoff = {1: adv1, 2: adv2}

    best = {}      # number -> {phase: (time, lap_no)}
    valid = {}     # number -> {lap_no: lap} completed, not deleted
    for n, laps in tr.laps_by.items():
        for no, l in laps.items():
            if not l.get("t") or not l.get("start") or l.get("out") or _is_deleted(gone, n, no, l):
                continue
            p = phase_of(_ts(l["start"]), ph) or 1
            valid.setdefault(n, {})[no] = l
            b = best.setdefault(n, {})
            if p not in b or l["t"] < b[p][0]:
                b[p] = (l["t"], no)

    order = sorted(tr.position, key=lambda n: tr.position[n])
    out_in = {}
    for n in order:
        pos = tr.position[n]
        if ph.get(1, {}).get("end") and pos > adv1:
            out_in[n] = 1
        elif ph.get(2, {}).get("end") and pos > adv2:
            out_in[n] = 2
    # before the next segment starts, cars can still be finishing their last lap: knockouts are provisional
    active = [n for n in order if n not in out_in]
    cur = cur or 1

    def seg_best(n, p=None):
        return (best.get(n, {}).get(p or cur) or (None, None))[0]

    ranked = sorted((n for n in active if seg_best(n)), key=seg_best)
    pole = seg_best(ranked[0]) if ranked else None
    cut_n = cutoff.get(cur)
    cut_driver = ranked[cut_n - 1] if cut_n and len(ranked) >= cut_n else None
    cut_time = seg_best(cut_driver) if cut_driver else None

    # session-best sectors in this segment, for colouring the live sectors
    sbest = [None, None, None]
    for n in active:
        for l in valid.get(n, {}).values():
            if phase_of(_ts(l["start"]), ph) != cur:
                continue
            for i, k in enumerate(("s1", "s2", "s3")):
                if l.get(k) and (sbest[i] is None or l[k] < sbest[i]):
                    sbest[i] = l[k]

    on_lap = []
    # a red flag voids laps in progress, and a car that has just crashed or stopped isn't on a lap
    stopped = {e["number"] for e in getattr(getattr(tr, "incidents", None), "events", []) if e["state"] == "stopped"}
    suspended = bool((ph.get(cur) or {}).get("suspended")) or tr.track_status == "RED"
    for n in ([] if suspended else active):
        if n in stopped:
            continue
        no = tr.lap_no.get(n)
        l = (tr.laps_by.get(n) or {}).get(no) or {}
        if l.get("t") or l.get("out") or not l.get("start"):
            continue
        started = _ts(l["start"])
        if now - started > 180 or phase_of(started, ph) != cur:
            continue
        ref_no = (best.get(n, {}).get(cur) or (None, None))[1]
        ref = valid.get(n, {}).get(ref_no) if ref_no else None
        done = [l.get(k) for k in ("s1", "s2", "s3")]
        secs, delta, ref_sum = [], 0.0, 0.0
        for i, s in enumerate(done):
            if s is None:
                break
            r = ref.get(("s1", "s2", "s3")[i]) if ref else sbest[i]
            colour = "purple" if sbest[i] and s <= sbest[i] else "green" if r and s <= r else "yellow"
            secs.append({"t": s, "d": round(s - r, 3) if r else None, "c": colour})
            if r:
                delta += s - r
                ref_sum += r
        if secs and secs[-1]["d"] is not None and secs[-1]["d"] > 2.5:
            continue          # a cool-down or in-lap, not a push lap
        base = seg_best(n) or (sum(sbest) if all(sbest) else None)
        proj = round(base + delta, 3) if base and secs and all(x["d"] is not None for x in secs) else None
        proj_pos = None
        if proj:
            proj_pos = 1 + sum(1 for m in ranked if m != n and seg_best(m) < proj)
        on_lap.append({"number": n, "code": tr.code(n), "colour": "#" + tr.colour(n), "pos": tr.position.get(n),
                       "started": l["start"], "elapsed": round(now - started, 1), "sectors": secs,
                       "ref": seg_best(n), "projected": proj, "projected_pos": proj_pos,
                       "safe": (proj_pos <= cut_n) if (proj_pos and cut_n) else None,
                       "compound": (tr.stints.get(n) or {}).get("compound")})
    on_lap.sort(key=lambda x: -x["elapsed"])

    rows = {}
    for n in order:
        b = best.get(n, {})
        q = {f"q{p}": round(b[p][0], 3) if p in b else None for p in (1, 2, 3)}
        mine = seg_best(n, out_in.get(n, cur))
        rows[n] = dict(q, out_in=out_in.get(n), best=mine,
                       gap=None if n in out_in or not mine or not pole else round(mine - pole, 3),
                       needs=round(mine - cut_time, 3) if (mine and cut_time and n in active and cut_n
                                                          and tr.position.get(n, 99) > cut_n) else None,
                       laps=sum(1 for l in valid.get(n, {}).values() if phase_of(_ts(l["start"]), ph) == cur),
                       on_lap=any(x["number"] == n for x in on_lap),
                       deleted=sum(1 for g in gone if g[0] == n))
    # conditions: the race director's wet/dry track declarations, and what the cars still running are on
    wet = None
    for m in sorted((m for m in tr.messages if m.get("date")), key=lambda m: m["date"]):
        up = (m.get("message") or "").upper()
        if re.search(r"\bWET TRACK\b|TRACK (IS )?(DECLARED )?WET", up):
            wet = True
        elif re.search(r"\bDRY TRACK\b|TRACK (IS )?(DECLARED )?DRY", up):
            wet = False
    tyres = {}
    for n in active:
        comp = (tr.stints.get(n) or {}).get("compound")
        if comp:
            tyres[comp] = tyres.get(comp, 0) + 1
    p = ph.get(cur) or {}
    return {
        "wet": wet, "tyres": dict(sorted(tyres.items(), key=lambda kv: -kv[1])),
        "with_time": sum(1 for n in active if seg_best(n)), "running_cars": len(active),
        "phase": cur, "label": f"Q{cur}" if not re.search(r"sprint|shootout", tr.session.get("session_name") or "", re.I)
        else f"SQ{cur}",
        "remaining": round(p.get("remaining", 0)) if p else None, "running": p.get("running", False),
        "suspended": p.get("suspended", False), "finished": bool(p.get("end")), "duration": p.get("duration"),
        "provisional": bool(p.get("end")) and bool(on_lap),
        "cut": cut_n, "advance": [adv1, adv2], "pole": pole, "pole_driver": tr.code(ranked[0]) if ranked else None,
        "cut_time": cut_time, "cut_driver": tr.code(cut_driver) if cut_driver else None,
        "best_sectors": sbest, "on_lap": on_lap, "rows": {str(k): v for k, v in rows.items()},
        "knocked": {f"q{p}": [tr.code(n) for n in order if out_in.get(n) == p] for p in (1, 2)},
    }
