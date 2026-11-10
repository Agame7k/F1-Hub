"""Team radio across a season: every clip OpenF1 publishes for the races (the ones the broadcast played, typically
20-30 a race), transcribed with Whisper (f1hub/stt_worker.py), sorted into what it is about, and lined up against
what the timing data shows - a "the tyres are gone" call against the tyre's measured pace, "box" against the stop,
a team order against the team-mates' positions.

Compiling (python3 -m f1hub compile 2025 --radio) downloads and transcribes about 600 clips a season, a few seconds
each on a laptop; clips and transcripts are kept (shared with the live dashboard's radio cache), so it is a one-off.
"""
import json
import re
import subprocess
import time
from collections import Counter
from statistics import median

from . import api, radio, transcribe
from . import progress as prog
from .api import write_atomic

# what a message is about: (key, label, pattern). A message can be several.
CATEGORIES = [
    ("tyres", "Tyre trouble", r"\btyres?\b.{0,40}\b(gone|dead|done|finished|cooked|destroyed|falling|dropping|graining|"
                              r"blister|overheat|no grip|struggl|deg\w*)|\b(no|zero|losing) (rear |front )?grip\b|"
                              r"\b(rears?|fronts?) (are|is) (gone|dead|done|finished|cooked|overheating)|\bgraining\b|"
                              r"\bblister|\bsliding\b|\bdeg is\b|\bdegradation\b|\bstruggl\w* with (the )?(grain|grad|tyre|rear|front|grip)"),
    ("box", "Pit call", r"\bbox[, ]+box\b|\bbox this lap\b|\bbox now\b|\bbox,? (and )?(we|let)|\bpit this lap\b|\bin this lap\b"),
    ("stay_out", "Stay out", r"\bstay(ing)? out\b|\bwe(?:'re| are) staying out\b|\bnot boxing\b|\bno box\b"),
    ("strategy", "Strategy", r"\bplan [a-f]\b|\bundercut\b|\bovercut\b|\bstrategy\b|\btarget lap\b|\bpit window\b|"
                             r"\bpit( stop)? window\b|\bto the end\b|\bfirst stop\b|\bextending\b|\bone[- ]stop\b|\btwo[- ]stop\b|\bextend\b|\bgo(ing)? longer\b|\boffset\b|\bcover\b|\bpit soon\b"),
    ("saving", "Saving", r"\blift and coast\b|\blico\b|\bsav(e|ing) (fuel|energy|the tyres|tyres|battery|brakes)\b|"
                         r"\bmanage (the )?(tyres|temps|brakes|gap)\b|\bharvest|\bmanaging\b|\bbrake temps?\b|\bcooling\b"),
    ("push", "Push", r"\bpush(ing)? (now|hard|flat)\b|\bfull push\b|\bmax(imum)? attack\b|\bquali (lap|mode)\b|\bflat out\b|"
                     r"\bgive it everything\b|\bpush,? push\b|\bpush from here\b|\bhappy for you to push\b"),
    ("team_orders", "Team orders", r"\blet (him|[a-z]+) (through|by|past|go)\b|\bswap (back|positions?)\b|\bhold (your )?position\b|"
                                   r"\bdon'?t (fight|attack|race)\b|\bgive (the|that) position back\b|\bno fighting\b|"
                                   r"\bletting .{0,20} (through|by)\b|\bswap(ping)? (places|cars|now|this)\b"),
    ("car", "Car problem", r"\b(lost|losing|no) (power|drive|gears?|brakes?|drs)\b|\bgearbox\b|\bengine\b|\bpower unit\b|"
                           r"\bdamage\b|\bpuncture\b|\bvibration\b|\bproblem\b|\bissue\b|\bsomething (is )?wrong\b|"
                           r"\bstop the car\b|\bretire\b|\bbrake pedal\b|\bpedal'?s? (going|gone)\b|\btorque\b|\bshifts?\b.{0,20}\bbad\b|"
                           r"\bon fire\b|\boverheating\b"),
    ("other_cars", "Other drivers", r"\b(pushed|forced|ran) me (off|wide)\b|\bblue flags?\b|\bdive[- ]?bomb|\bblock(ed|ing)\b|"
                                    r"\binvestigation\b|\bpenalty\b|\bno further action\b|\bdrove (himself|me)\b|\bhit me\b|\bcrashed into\b|\bstupid\b|\bidiot\b"),
    ("weather", "Weather", r"\brain\b|\bdrops\b|\bspitting\b|\bweather\b|\bwet\b|\bdamp\b|\binters?\b|\bintermediates?\b|\bdrying\b"),
    ("result", "Result and praise", r"\bwell done\b|\bgreat (job|drive|race)\b|\bfantastic (job|drive)\b|\bworld champion\b|"
                                    r"\bpodium\b|\bwe won\b|\byou won\b|\bproud\b|\bincredible\b|\bmega\b"),
    ("gaps", "Gaps and pace", r"\bgap\b|\bdelta\b|\bpace\b|\btenths?\b|\bseconds? (faster|slower|ahead|behind)\b|"
                              r"\b(faster|slower|quicker) than\b|\btime loss\b|\blos(ing|e) (some )?time\b|\bwhere i'?m (slow|losing)\b"),
]
CAT_LABEL = {k: lab for k, lab, _ in CATEGORIES}
CAT_RE = [(k, re.compile(p, re.I)) for k, _, p in CATEGORIES]


NEGATED = re.compile(r"\bno (sign|signs) of\b[^.?!]*|\bnot (seeing|seen) (any )?[^.?!]*", re.I)


def classify(text):
    """The categories a clip's words fall in. A negated remark ("no sign of graining") doesn't count."""
    if not text:
        return []
    text = NEGATED.sub(" ", text)
    return [k for k, rx in CAT_RE if rx.search(text)]


def _r(v, n=3):
    return None if v is None else round(v, n)


# ----------------------------------------------------------------------------- compiling
class _Worker:
    """The Whisper worker in batch: started on first use, stopped at the end."""

    def __init__(self):
        self.proc = None

    def text(self, url):
        cache = radio.CACHE / (transcribe._key(url) + ".json")
        if cache.exists():
            try:
                return json.loads(cache.read_text()).get("text")
            except (OSError, ValueError):
                pass
        if not transcribe.VENV_PY.exists():
            return None
        radio.fetch_clip(url)
        if self.proc is None or self.proc.poll() is not None:
            self.proc = subprocess.Popen([str(transcribe.VENV_PY), "-I", str(transcribe.WORKER)], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
            if not json.loads(self.proc.stdout.readline() or "{}").get("ready"):
                raise RuntimeError("the speech-to-text worker didn't start")
        path = radio.CACHE / (transcribe._key(url) + ".mp3")
        self.proc.stdin.write(json.dumps({"id": url, "path": str(path)}) + "\n")
        self.proc.stdin.flush()
        res = json.loads(self.proc.stdout.readline() or "{}")
        if "error" in res or res.get("id") != url:
            return None
        res.pop("id", None)
        write_atomic(cache, json.dumps(res))
        return res.get("text")

    def close(self):
        if self.proc:
            self.proc.kill()


def _lap_of(laps, date):
    """The lap a clip was broadcast on: the last lap that started before it."""
    best = None
    for start, lap in laps:
        if start and start <= date:
            best = lap
        else:
            break
    return best


def compile_season(db, rounds=None, refresh=False, progress=lambda m: None):
    """Team radio for every race in the dataset (rounds: only these). Returns (races, clips, transcribed)."""
    races = db.execute("SELECT session_key, round, meeting_name FROM sessions WHERE session_name = 'Race' ORDER BY round").fetchall()
    done = {r[0] for r in db.execute("SELECT session_key FROM radio_sessions WHERE transcribed >= clips")}
    worker = _Worker()
    n_races = n_clips = n_text = 0
    races = [s for s in races if not ((rounds and s["round"] not in rounds) or (s["session_key"] in done and not refresh))]
    prog.begin(progress, "team radio", len(races))
    try:
        for s in races:
            key = s["session_key"]
            label = f"R{s['round']} {s['meeting_name']}"
            progress(f"{label}: team radio")
            clips = api.get("team_radio", session_key=key) or []
            laps = {}
            for r in db.execute("SELECT driver_number, lap_number, date_start FROM laps WHERE session_key = ? ORDER BY lap_number", (key,)):
                laps.setdefault(r["driver_number"], []).append(((r["date_start"] or "")[:19] or None, r["lap_number"]))
            rows, got = [], 0
            for i, c in enumerate(sorted(clips, key=lambda c: c["date"])):
                progress(f"{label}: radio {i + 1}/{len(clips)}")
                url = c.get("recording_url")
                if not url:
                    continue
                try:
                    text = worker.text(url)
                except Exception as e:          # one clip must not stop the season
                    progress(f"{label}: clip failed ({type(e).__name__})")
                    text = None
                got += text is not None
                date = c["date"][:19]
                lap = _lap_of(laps.get(c["driver_number"], []), date)
                rows.append((key, c["driver_number"], c["date"], lap, url, text, json.dumps(classify(text))))
            db.execute("DELETE FROM radio WHERE session_key = ?", (key,))
            db.executemany("INSERT OR REPLACE INTO radio VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            db.execute("INSERT OR REPLACE INTO radio_sessions VALUES (?, ?, ?, ?)", (key, len(rows), got, time.time()))
            db.commit()
            n_races += 1
            n_clips += len(rows)
            n_text += got
            prog.done(progress, label, f"{len(rows)} clips, {got} transcribed")
    finally:
        worker.close()
    return n_races, n_clips, n_text


# ----------------------------------------------------------------------------- what the radio says, against the data
def _mean_se(v):
    if not v:
        return None
    m = sum(v) / len(v)
    if len(v) < 2:
        return {"est": _r(m), "se": None, "n": len(v)}
    sd = (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5
    return {"est": _r(m), "se": _r(sd / len(v) ** 0.5), "n": len(v)}


class _Race:
    """One race's laps, stints, stops and the lap-time model's residuals (how much slower than the model each clean
    lap was, after the driver's own pace, fuel, compound and tyre age)."""

    def __init__(self, db, key, fit):
        self.laps = {(r["driver_number"], r["lap_number"]): dict(r) for r in db.execute(
            "SELECT driver_number, lap_number, lap_time, compound, tyre_age, clean, position, pit_in, pit_out "
            "FROM laps WHERE session_key = ?", (key,))}
        self.pits = {}
        for r in db.execute("SELECT driver_number, lap_number FROM pits WHERE session_key = ?", (key,)):
            self.pits.setdefault(r[0], set()).add(r[1])
        for (n, L), r in self.laps.items():
            if r["pit_in"]:
                self.pits.setdefault(n, set()).add(L)
        self.status = {r[0]: r[1] for r in db.execute("SELECT driver_number, status FROM results WHERE session_key = ?", (key,))}
        self.resid = {}
        if fit and "skipped" not in fit:
            from .strategy import pace_fn
            pace, fuel = pace_fn(fit), fit["fuel"]["est"] or 0.0
            raw = {}
            for (n, L), r in self.laps.items():
                if r["clean"] and r["lap_time"] and r["compound"] in ("SOFT", "MEDIUM", "HARD"):
                    raw[(n, L)] = r["lap_time"] - fuel * L - pace(n, r["compound"], r["tyre_age"])
            by = {}
            for (n, L), v in raw.items():
                by.setdefault(n, []).append(v)
            base = {n: median(v) for n, v in by.items() if len(v) >= 5}
            self.resid = {(n, L): v - base[n] for (n, L), v in raw.items() if n in base and abs(v - base[n]) < 3}

    def window(self, n, a, b):
        v = [self.resid[(n, L)] for L in range(a, b + 1) if (n, L) in self.resid]
        return sum(v) / len(v) if v else None

    def stopped(self, n, a, b):
        return any(a <= L <= b for L in self.pits.get(n, ()))


def _teammate(db, key, n):
    t = db.execute("SELECT team FROM drivers WHERE session_key = ? AND driver_number = ?", (key, n)).fetchone()
    if not t:
        return None
    m = db.execute("SELECT driver_number FROM drivers WHERE session_key = ? AND team = ? AND driver_number != ?",
                   (key, t[0], n)).fetchone()
    return m[0] if m else None


def report(db, fits, upto=None):
    """Every clip, what it is about, and what the data shows around it."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'radio'").fetchone():
        return {"clips": 0}
    rows = [dict(r) for r in db.execute("""SELECT r.*, s.round, s.meeting_name, s.circuit, d.code, d.team, d.colour
        FROM radio r JOIN sessions s USING (session_key)
        LEFT JOIN drivers d ON d.session_key = r.session_key AND d.driver_number = r.driver_number
        WHERE (? IS NULL OR s.round <= ?) ORDER BY r.date""", (upto, upto))]
    races = db.execute("SELECT COUNT(*) FROM radio_sessions rs JOIN sessions s USING (session_key) WHERE (? IS NULL OR s.round <= ?)",
                       (upto, upto)).fetchone()[0]
    if not rows:
        return {"clips": 0, "races": races}
    for r in rows:
        r["cats"] = classify(r["text"])           # classified again on read: improvements apply without re-transcribing
    with_text = [r for r in rows if r["text"]]
    cat_n = Counter(c for r in with_text for c in r["cats"])
    teams = {}
    for r in with_text:
        t = teams.setdefault(r["team"] or "?", {"team": r["team"], "colour": r["colour"], "clips": 0, "cats": Counter()})
        t["clips"] += 1
        t["cats"].update(r["cats"])

    race_cache = {}

    def race(key):
        if key not in race_cache:
            race_cache[key] = _Race(db, key, fits.get(key))
        return race_cache[key]

    # tyre complaints: pace against the model just before the call, against the same driver earlier in the stint,
    # and the same measure at every other lap at a similar tyre age (control)
    tyre_calls, excess, control, to_stop, stop_soon = [], [], [], [], 0
    for r in with_text:
        if "tyres" not in r["cats"] or r["lap_number"] is None:
            continue
        R, n, L = race(r["session_key"]), r["driver_number"], r["lap_number"]
        lap = R.laps.get((n, L)) or {}
        age = lap["tyre_age"] if lap else None
        late = R.window(n, L - 2, L)
        early = R.window(n, L - (age or 0) + 3, L - (age or 0) + 8) if age and age > 10 else None
        nxt = min((p for p in R.pits.get(n, ()) if p >= L), default=None)
        if nxt is not None:
            to_stop.append(nxt - L)
            stop_soon += nxt - L <= 3
        x = late - early if late is not None and early is not None else None
        if x is not None:
            excess.append(x)
        tyre_calls.append(dict(_brief(r), age=age, compound=lap.get("compound") if lap else None,
                               excess=_r(x), laps_to_stop=None if nxt is None else nxt - L,
                               retired=R.status.get(n) not in ("Finished", "Lapped", None)))
    for key in {r["session_key"] for r in tyre_calls} if tyre_calls else ():
        R = race(key)
        for (n, L), lap in R.laps.items():
            age = lap["tyre_age"]
            if age and age > 10 and (n, L) in R.resid:
                late, early = R.window(n, L - 2, L), R.window(n, L - age + 3, L - age + 8)
                if late is not None and early is not None:
                    control.append(late - early)

    # pit calls: did a stop follow within two laps; "stay out": none did. A clip is timed when it was published,
    # which can trail the call by a lap, so the lap before counts too
    box, stay = [], []
    for r in with_text:
        if r["lap_number"] is None:
            continue
        R, n, L = race(r["session_key"]), r["driver_number"], r["lap_number"]
        if "box" in r["cats"]:
            box.append(dict(_brief(r), followed=R.stopped(n, L - 1, L + 2)))
        if "stay_out" in r["cats"]:
            stay.append(dict(_brief(r), followed=not R.stopped(n, L - 1, L + 2)))

    # team orders: the team-mates' order before the call and three laps on
    orders = []
    for r in with_text:
        if "team_orders" not in r["cats"] or r["lap_number"] is None:
            continue
        R, n, L = race(r["session_key"]), r["driver_number"], r["lap_number"]
        m = _teammate(db, r["session_key"], n)
        a0, b0 = (R.laps.get((n, L - 1)) or {}), (R.laps.get((m, L - 1)) or {})
        a1, b1 = (R.laps.get((n, L + 3)) or {}), (R.laps.get((m, L + 3)) or {})
        swapped = None
        if m and all(x and x["position"] for x in (a0, b0, a1, b1)):
            swapped = (a0["position"] < b0["position"]) != (a1["position"] < b1["position"])
        orders.append(dict(_brief(r), swapped=swapped, teammate=m))

    # saving: pace over the next three laps against the three before
    save = []
    for r in with_text:
        if "saving" not in r["cats"] or r["lap_number"] is None:
            continue
        R, n, L = race(r["session_key"]), r["driver_number"], r["lap_number"]
        a, b = R.window(n, L - 3, L - 1), R.window(n, L + 1, L + 3)
        if a is not None and b is not None and not R.stopped(n, L - 3, L + 3):
            save.append(b - a)

    # car problems: how often the car then retired
    car = [r for r in with_text if "car" in r["cats"]]
    car_dnf = sum(1 for r in car if race(r["session_key"]).status.get(r["driver_number"]) not in ("Finished", "Lapped", None))
    all_starts = db.execute("""SELECT COUNT(*), SUM(status NOT IN ('Finished', 'Lapped')) FROM results r JOIN sessions s
        USING (session_key) WHERE s.session_name = 'Race' AND status != 'DNS' AND (? IS NULL OR s.round <= ?)""",
                            (upto, upto)).fetchone()

    return {"clips": len(rows), "transcribed": len(with_text), "races": races, "labels": CAT_LABEL,
            "categories": {k: cat_n[k] for k, _, _ in CATEGORIES},
            "teams": sorted(({"team": t["team"], "colour": t["colour"], "clips": t["clips"], "cats": dict(t["cats"])}
                             for t in teams.values()), key=lambda t: -t["clips"]),
            "tyres": {"calls": tyre_calls, "excess": _mean_se(excess), "control": _mean_se(control),
                      "laps_to_stop": _r(median(to_stop), 1) if to_stop else None,
                      "stopped_within_3": stop_soon, "with_stop": len(to_stop)},
            "box": {"n": len(box), "followed": sum(1 for b in box if b["followed"]), "misses": [b for b in box if not b["followed"]][:8]},
            "stay_out": {"n": len(stay), "followed": sum(1 for b in stay if b["followed"])},
            "team_orders": {"calls": orders, "swapped": sum(1 for o in orders if o["swapped"]),
                            "known": sum(1 for o in orders if o["swapped"] is not None)},
            "saving": {"after_minus_before": _mean_se(save)},
            "car": {"n": len(car), "retired": car_dnf, "base_dnf": _r((all_starts[1] or 0) / all_starts[0], 3) if all_starts[0] else None,
                    "examples": [_brief(r) for r in car][:10]},
            "recent": [_brief(r) for r in with_text if r["cats"]][-40:]}


def _brief(r):
    return {"round": r["round"], "session_key": r["session_key"], "circuit": r["circuit"], "lap": r["lap_number"], "number": r["driver_number"],
            "code": r["code"], "team": r["team"], "colour": r["colour"], "text": r["text"], "cats": r["cats"], "url": r["url"]}
