"""Stewarding consistency: every stewards' decision of a season, from the FIA's own documents (f1hub/fia.py), sorted
by offence and by outcome, so that similar incidents can be set side by side with the penalties they got and the
rule each decision cites.

Compiling (python3 -m f1hub compile 2025 --stewards) reads each Grand Prix's documents page on fia.com and every
decision PDF on it - a few hundred PDFs a season, one every few seconds so as not to load fia.com, kept on disk for
good - and writes the `stewards` table of the season dataset.
"""
import json
import math
import re
import time
from collections import Counter

from . import fia
from .stewards import SKIP_TITLE

FINAL_KINDS = ("infringement", "decision", "offence")
PDF_SPACING = 3.0          # s between PDFs fetched from fia.com
EVENT_SETTLE = 3 * 86400   # an event's documents are taken as final this long after its race
PAIR_SIM = 0.35           # wording at least this alike (cosine, 0-1) for "similar decisions, different outcomes"

# offence: the first pattern that matches the document title's offence, then the Fact (order matters)
CATEGORIES = [
    ("protest", "Protest or right of review", r"protest|right of review|petition"),
    ("collision", "Causing a collision", r"caus\w* (a )?collision|\bcollision\b|\bcollided\b|incident with car"),
    ("forcing_off", "Forcing another driver off", r"forc\w* .{0,30}\boff\b|pushing .{0,20} off"),
    ("off_advantage", "Leaving the track and gaining an advantage", r"gain\w* (a|an) (lasting )?advantage|leaving the track and gaining"),
    ("track_limits", "Track limits", r"track limits|leaving the track without|left the track .{0,30}(occasions|times)"),
    ("direction", "More than one change of direction", r"change of direction|more than one move"),
    ("impeding", "Impeding", r"imped"),
    ("erratic", "Erratic or unsafe driving", r"erratic|unsafe driving|dangerous driving|unnecessarily slow|potentially dangerous"),
    ("unsafe_release", "Unsafe release", r"unsafe release|unsafe rejoin|unsafe(ly)? re-?join|re-?join\w* .{0,20}unsafe|released .{0,40}unsafe"),
    ("damaged_car", "Driving a damaged car", r"(driving|continued|drove) .{0,30}unsafe condition|obvious damage|damaged car|"
                                             r"mechanical(ly)? unsafe"),
    ("pit_speeding", "Pit lane speeding", r"speeding|pit lane speed"),
    ("pit_lane", "Pit lane conduct", r"fast lane|pit lane incident|in the pit lane|overtook .{0,30}pit"),
    ("pit_lines", "Pit entry or exit line", r"pit (entry|exit)|white line|crossing the (line|white)|pit lane (entry|exit)"),
    ("flags", "Yellow, blue or red flags", r"yellow|blue flag|red flag|double waved"),
    ("safety_car", "Safety Car or VSC procedure", r"safety car|\bvsc\b|virtual safety|\bsc\b|delta time|overtaking under"),
    ("start", "Start procedure", r"false start|jump start|start procedure|grid (box|position)|formation lap|out of position|"
                                 r"starting position|mov\w* (forward )?(prior to|before) (the )?start|start signal"),
    ("technical", "Technical and power unit", r"power unit|\bpu\b|gearbox|\bice\b|\bmgu|energy store|control electronics|"
                                              r"turbocharger|exhaust|parc ferm|technical|plank|skid|fuel|weight|"
                                              r"tyre|tyres|wheel|floor|wing|scrutineer|homologat|component|element"),
    ("procedure", "Procedural", r"weighbridge|weighing|instruction|failed to (follow|comply|report)|late|curfew|cufrew|"
                                r"driver.{0,10}(briefing|parade)|media|anthem|jewel|underwear|helmet|seat belt|clothing|"
                                r"\bdrs\b|fan engagement|attend|107|withdraw|setup sheet|submit|serve .{0,20}penalty|in gear|abandon"),
    ("conduct", "Conduct", r"misconduct|language|swear|abus|behaviour|gesture|12\.2\.1"),
]
CAT_LABEL = {k: lab for k, lab, _ in CATEGORIES} | {"other": "Other"}
CAT_RE = [(k, re.compile(p, re.I)) for k, _, p in CATEGORIES]
DRIVING = ("collision", "forcing_off", "off_advantage", "track_limits", "direction", "impeding", "erratic", "flags",
           "safety_car", "start")

# outcomes, least to most severe; a decision's outcome is the most severe one it hands out
OUTCOMES = ["no_action", "warning", "reprimand", "fine", "lap_deleted", "time", "drive_through", "stop_go", "grid",
            "pit_start", "dsq"]
OUT_LABEL = {"no_action": "No further action", "warning": "Warning", "reprimand": "Reprimand", "fine": "Fine",
             "lap_deleted": "Lap time deleted", "time": "Time penalty", "drive_through": "Drive-through",
             "stop_go": "Stop-go", "grid": "Grid drop", "pit_start": "Pit-lane / back of grid", "dsq": "Disqualified"}


def _r(v, n=3):
    return None if v is None else round(v, n)


# ----------------------------------------------------------------------------- reading a decision
def category(offence, fact, infringement=""):
    for text in (offence, fact, infringement):
        for k, rx in CAT_RE:
            if text and rx.search(text):
                return k
    return "other"


def outcome(decision):
    """(outcome, details) from a decision's Decision field."""
    d = re.sub(r"\s+", " ", decision or "")
    low = d.lower()
    found = []
    if re.search(r"no further action|no action|\bnfa\b|no penalty (is )?applied|rejected|dismissed|not admissible|"
                 r"inadmissible|petition .{0,20} admissible|deposit is (forfeited|returned)", low):
        found.append("no_action")
    if re.search(r"\bwarning\b|black and white flag", low):
        found.append("warning")
    if "reprimand" in low:
        found.append("reprimand")
    fine = re.search(r"fine of\s*(?:€|eur|euros?)\s*([\d,. ]+)|(?:€|eur)\s*([\d,. ]+)\s*fine|fine.{0,20}?€\s*([\d,. ]+)", low)
    if fine or re.search(r"\bfine\b", low):
        found.append("fine")
    if re.search(r"\b(lap times?|times?)\b.{0,80}\bdeleted\b|deletion of .{0,20}lap", low):
        found.append("lap_deleted")
    secs = re.search(r"(\d+)\s*(?:-\s*)?second(?:s)?\s*(?:time\s*)?penalty", low)
    if secs and "stop" not in low[max(0, secs.start() - 10):secs.end() + 15]:
        found.append("time")
    if "drive through" in low or "drive-through" in low:
        found.append("drive_through")
    if re.search(r"stop[ -]and[ -]go|stop[ -]go|\d+\s*second stop", low):
        found.append("stop_go")
    grid = re.search(r"(\d+)\s*(?:-\s*)?(?:grid\s*)?(?:place|position)s?\s*(?:grid\s*)?(?:penalty|drop)|drop of (\d+) grid", low)
    if grid:
        found.append("grid")
    if re.search(r"pit ?lane start|start (the \w+ )?from the pit|back of the (starting )?grid|start from the back", low):
        found.append("pit_start")
    if re.search(r"disqualif", low):
        found.append("dsq")
    points = re.search(r"(\d+)\s*penalty points?", low)
    fine_amt = None
    if fine:
        raw = next(g for g in fine.groups() if g)
        try:
            fine_amt = int(re.sub(r"[^\d]", "", raw))
        except ValueError:
            fine_amt = None
    main = max(found, key=OUTCOMES.index) if found else ("no_action" if not d else "other")
    return main, {"all": found, "seconds": int(secs.group(1)) if secs and "time" in found else None,
                  "grid": int(next(g for g in grid.groups() if g)) if grid else None,
                  "points": int(points.group(1)) if points else None, "fine": fine_amt}


def blame(reason):
    """How the stewards apportioned blame for a collision, from the Reason."""
    low = (reason or "").lower()
    for key, rx in (("not_predominantly", r"not (wholly or )?predominantly|no (one )?driver .{0,30}(wholly|predominantly)|"
                                          r"neither driver .{0,30}(wholly|predominantly)"),
                    ("racing_incident", r"racing incident|first[- ]lap incident|lap one incident|close racing"),
                    ("wholly", r"wholly (or predominantly )?(to blame|at fault|responsible)"),
                    ("predominantly", r"predominantly (to blame|at fault|responsible)")):
        if re.search(rx, low):
            return key
    return None


def _driver(field):
    m = re.match(r"\s*(\d{1,2})\b", field or "")
    return int(m.group(1)) if m else None


def _session(field):
    low = (field or "").lower()
    for name, key in (("sprint qualifying", "Sprint Qualifying"), ("sprint shootout", "Sprint Qualifying"),
                      ("sprint", "Sprint"), ("qualifying", "Qualifying"), ("race", "Race"),
                      ("practice 1", "Practice 1"), ("practice 2", "Practice 2"), ("practice 3", "Practice 3"),
                      ("practice", "Practice")):
        if name in low:
            return key
    return field or None


# ----------------------------------------------------------------------------- compiling
def compile_season(db, year, rounds=None, refresh=False, progress=lambda m: None):
    """Every stewards' decision of the season's Grands Prix into the `stewards` table. Returns (events, decisions)."""
    meetings = db.execute("""SELECT round, meeting_key, meeting_name, MAX(date_start) AS last FROM sessions
                             GROUP BY round, meeting_key ORDER BY round""").fetchall()
    done = {r[0] for r in db.execute("SELECT meeting_key FROM stew_events WHERE final = 1")}
    events = decisions = 0
    for m in meetings:
        if rounds and m["round"] not in rounds:
            continue
        if m["meeting_key"] in done and not refresh:
            continue
        label = f"R{m['round']} {m['meeting_name']}"
        progress(f"{label}: stewards' documents")
        docs = fia.event_documents(year, m["meeting_name"], ttl=None if _settled(m["last"]) else 600)
        keys = {r["session_name"]: r["session_key"] for r in db.execute(
            "SELECT session_name, session_key FROM sessions WHERE meeting_key = ?", (m["meeting_key"],))}
        rows = []
        todo = [d for d in docs if d["kind"] in FINAL_KINDS and not SKIP_TITLE.search(d["title"])]
        for i, d in enumerate(todo):
            progress(f"{label}: decision {i + 1}/{len(todo)}")
            cached = fia.CACHE.joinpath(fia.hashlib.sha1(d["url"].encode()).hexdigest() + ".txt").exists()
            try:
                p = fia.decision(d["url"])
            except fia.FIAError:
                continue
            if not cached:
                time.sleep(PDF_SPACING)
            cat = category(d["offence"], p.get("fact"), p.get("infringement"))
            out, det = outcome(p.get("decision"))
            sess = _session(p.get("session"))
            rows.append((m["meeting_key"], m["round"], p.get("document") or d["no"], d["url"], d["title"], d["kind"],
                         d["published"], sess, keys.get(sess), _driver(p.get("no")) or (d["cars"][0] if d["cars"] else None),
                         p.get("competitor"), p.get("time"), p.get("fact"), p.get("infringement"), p.get("decision"),
                         p.get("reason"), json.dumps(p.get("articles") or []), cat, out, det["seconds"], det["grid"],
                         det["points"], det["fine"], blame(p.get("reason")) if cat in ("collision", "forcing_off") else None,
                         d["offence"]))
        db.execute("DELETE FROM stewards WHERE meeting_key = ?", (m["meeting_key"],))
        db.executemany(f"INSERT OR REPLACE INTO stewards VALUES ({','.join('?' * 25)})", rows)
        db.execute("INSERT OR REPLACE INTO stew_events VALUES (?, ?, ?, ?)",
                   (m["meeting_key"], m["round"], time.time(), 1 if _settled(m["last"]) else 0))
        db.commit()
        events += 1
        decisions += len(rows)
    return events, decisions


def _settled(last):
    try:
        t = time.mktime(time.strptime((last or "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return False
    return time.time() - t > EVENT_SETTLE


# ----------------------------------------------------------------------------- consistency
_STOP = set("""the a an of and or to in on at by for with from was were is be been as that this it its car cars driver
drivers stewards reviewed video evidence in-car positional telemetry team representative heard turn lap laps
which had has have not no into after before during their they them his her he she during while than then
also any all would could should did does do there where when only more most other one two three""".split())


def _tokens(text):
    words = re.findall(r"[a-z]+", (text or "").lower())
    return [w for w in words if w not in _STOP and len(w) > 2]


def _vectors(rows):
    docs = [Counter(_tokens(" ".join(filter(None, (r["offence"], r["fact"], r["reason"]))))) for r in rows]
    df = Counter(w for d in docs for w in d)
    n = len(docs)
    out = []
    for d in docs:
        v = {w: (1 + math.log(c)) * math.log((1 + n) / (1 + df[w])) for w, c in d.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({w: x / norm for w, x in v.items()})
    return out


def _cos(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def _repeat(r):
    """A second (or later) offence of the kind, which the guidelines penalise harder."""
    return bool(re.search(r"\((2nd|3rd|\dth)\)|second (offence|infringement|breach)|repeat(ed)? (offence|infringement|breach)",
                          f"{r['offence']} {r['reason']}", re.I))


def severity(r):
    """A comparable number for an outcome: the time it costs in a race where that applies."""
    o = r["outcome"]
    return {"no_action": 0, "warning": 0.5, "reprimand": 1, "fine": 1, "lap_deleted": 2,
            "time": 2 + (r["seconds"] or 5) / 5, "drive_through": 6, "stop_go": 8, "grid": 5 + (r["grid"] or 0) / 3,
            "pit_start": 9, "dsq": 12}.get(o, 0)


def report(db, upto=None):
    """Decisions by offence and outcome, how consistent each offence's outcomes were, similar decisions that ended
    differently, the rules cited, and penalties per driver and team."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'stewards'").fetchone():
        return {"decisions": 0}
    rows = [dict(r) for r in db.execute("SELECT * FROM stewards WHERE (? IS NULL OR round <= ?) ORDER BY round, doc",
                                        (upto, upto))]
    events = db.execute("SELECT COUNT(*) FROM stew_events WHERE (? IS NULL OR round <= ?)", (upto, upto)).fetchone()[0]
    if not rows:
        return {"decisions": 0, "events": events}
    names = {}
    for r in db.execute("SELECT d.driver_number, d.code, d.team, d.colour FROM drivers d JOIN sessions s USING (session_key) "
                        "WHERE (? IS NULL OR s.round <= ?) ORDER BY s.date_start", (upto, upto)):
        names[r["driver_number"]] = (r["code"], r["team"], r["colour"])
    for r in rows:
        # read again from the stored text, so improvements to the reading apply without fetching anything
        r["category"] = category(r["offence"], r["fact"], r["infringement"])
        r["outcome"], det = outcome(r["decision"])
        r.update(seconds=det["seconds"], grid=det["grid"], points=det["points"], fine=det["fine"],
                 blame=blame(r["reason"]) if r["category"] in ("collision", "forcing_off") else None)
        r["code"], r["team"], r["colour"] = names.get(r["driver_number"], (None, None, None))
        r["articles"] = json.loads(r["articles"] or "[]")
        r["severity"] = severity(r)

    cats = []
    for k in [c for c, _, _ in CATEGORIES] + ["other"]:
        rs = [r for r in rows if r["category"] == k]
        if not rs:
            continue
        mix = Counter(r["outcome"] for r in rs)
        pen = [r for r in rs if r["outcome"] not in ("no_action", "warning")]
        modal, modal_n = mix.most_common(1)[0]
        secs = Counter(r["seconds"] for r in rs if r["outcome"] == "time")
        arts = Counter(a["label"] for r in rs for a in r["articles"])
        by_session = Counter(r["session"] or "?" for r in rs)
        cats.append({"category": k, "label": CAT_LABEL[k], "driving": k in DRIVING, "n": len(rs), "penalised": len(pen),
                     "outcomes": {o: mix[o] for o in OUTCOMES + ["other"] if mix.get(o)},
                     "modal": modal, "consistency": _r(modal_n / len(rs), 3), "seconds": dict(sorted(secs.items())),
                     "articles": arts.most_common(4), "sessions": dict(by_session)})
    cats.sort(key=lambda c: -c["n"])

    # collisions: penalty against how the stewards apportioned blame
    coll = {}
    for r in rows:
        if r["category"] in ("collision", "forcing_off") and r["session"] in ("Race", "Sprint"):
            b = r["blame"] or "unstated"
            coll.setdefault(b, Counter())[r["outcome"]] += 1
    blame_table = [{"blame": b, "outcomes": dict(c), "n": sum(c.values())} for b, c in coll.items()]

    # similar decisions that ended differently (same offence category, wording alike, outcomes apart)
    vec = _vectors(rows)
    pairs, nearest = [], {}
    for i in range(len(rows)):
        best = []
        for j in range(len(rows)):
            if i == j or rows[i]["category"] != rows[j]["category"]:
                continue
            s = _cos(vec[i], vec[j])
            best.append((s, j))
            if j > i and s >= PAIR_SIM and abs(rows[i]["severity"] - rows[j]["severity"]) >= 2 \
                    and rows[i]["category"] not in ("technical", "protest", "procedure", "other") \
                    and _repeat(rows[i]) == _repeat(rows[j]) \
                    and (rows[i]["session"] in ("Race", "Sprint")) == (rows[j]["session"] in ("Race", "Sprint")):
                pairs.append((s, i, j))
        best.sort(reverse=True)
        nearest[i] = [(round(s, 3), j) for s, j in best[:3] if s > 0.2]
    pairs.sort(reverse=True)

    def brief(r):
        return {k: r[k] for k in ("round", "doc", "url", "title", "session", "driver_number", "code", "team", "colour",
                                  "category", "outcome", "seconds", "grid", "points", "fine", "blame", "fact", "decision",
                                  "reason", "severity")} | {"articles": [a["label"] for a in r["articles"]]}

    per_driver = {}
    for r in rows:
        if not r["driver_number"]:
            continue
        d = per_driver.setdefault(r["driver_number"], {"number": r["driver_number"], "code": r["code"], "team": r["team"],
                                                       "colour": r["colour"], "decisions": 0, "penalties": 0, "seconds": 0,
                                                       "points": 0, "grid": 0, "reprimands": 0, "driving": 0})
        d["decisions"] += 1
        d["penalties"] += r["outcome"] not in ("no_action", "warning", "reprimand")
        d["seconds"] += r["seconds"] or 0
        d["points"] += r["points"] or 0
        d["grid"] += r["grid"] or 0
        d["reprimands"] += r["outcome"] == "reprimand"
        d["driving"] += r["category"] in DRIVING and r["outcome"] not in ("no_action",)
    teams = {}
    for d in per_driver.values():
        t = teams.setdefault(d["team"] or "?", {"team": d["team"], "colour": d["colour"], "decisions": 0, "penalties": 0,
                                                "seconds": 0, "points": 0, "grid": 0})
        for k in ("decisions", "penalties", "seconds", "points", "grid"):
            t[k] += d[k]
    by_round = Counter(r["round"] for r in rows)
    pen_round = Counter(r["round"] for r in rows if r["outcome"] not in ("no_action", "warning"))
    meet = {r["round"]: r["meeting_name"] for r in db.execute("SELECT DISTINCT round, meeting_name FROM sessions")}
    return {"decisions": len(rows), "events": events,
            "outcomes": dict(Counter(r["outcome"] for r in rows)), "outcome_labels": OUT_LABEL, "order": OUTCOMES,
            "categories": cats, "blame": blame_table,
            "pairs": [{"similarity": round(s, 3), "a": brief(rows[i]), "b": brief(rows[j])} for s, i, j in pairs[:12]],
            "decisions_list": [dict(brief(r), similar=[{"similarity": s, "doc": rows[j]["doc"], "round": rows[j]["round"],
                                                        "outcome": rows[j]["outcome"], "seconds": rows[j]["seconds"],
                                                        "code": rows[j]["code"]} for s, j in nearest[i]])
                               for i, r in enumerate(rows)],
            "drivers": sorted(per_driver.values(), key=lambda d: (-d["penalties"], -d["seconds"])),
            "teams": sorted(teams.values(), key=lambda t: (-t["penalties"], -t["seconds"])),
            "rounds": [{"round": k, "name": meet.get(k), "decisions": by_round[k], "penalties": pen_round[k]}
                       for k in sorted(by_round)]}
