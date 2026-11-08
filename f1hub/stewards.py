"""What the stewards' decisions add up to, from the FIA's own documents (f1hub/fia.py):

  * provisional starting grid - the qualifying order with this weekend's grid penalties applied, in the
    order Article B2.5.4b of the 2026 Sporting Regulations lays down (PU changes, grid drops, pit-lane starts)
  * penalty points - every "N penalty points (total of X for the 12 month period)" over the last 12 months;
    12 points in 12 months is a one-race ban
  * reprimands - counted per championship; the fifth (at least four for driving) brings a 10-place grid drop

Nothing here is official: the FIA publishes the real provisional grid two hours before the start.
"""
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from . import fia

BAN_POINTS = 12
BACK_OF_GRID = 15        # more than 15 places of grid penalties: start behind every other classified car
# titles that never carry points, reprimands or grid drops worth a PDF download
SKIP_TITLE = re.compile(r"deleted lap|permission to start|driver change|scrutineering|curfew|classification|"
                        r"sc2|nonconformity|non-conformity|tyre allocation|garage doors", re.I)
STEWARDING_KINDS = ("infringement", "decision", "offence", "penalty", "reprimand")
# at earlier Grands Prix only on-track offences matter (points, driving reprimands, race grid drops carried over):
# reading just those keeps the download to a few PDFs per event
DRIVING_TITLE = re.compile(r"collision|contact|track limits|leaving the track|left the track|gain|advantage|forc|"
                           r"yellow|red flag|safety car|vsc|impeding|unsafe|overtak|false start|start procedure|"
                           r"position|erratic|dangerous|driving|blue flag|formation|grid|weav|chang(e|ing) of direction|"
                           r"crossing|pit entry|pit exit|instruction|reprimand|warning|tyre|line", re.I)
POINTS_RE = re.compile(r"(\d+)\s+penalty points?(?:\s+imposed)?(?:\s*\((?:total|bringing)[^0-9)]*(\d+))?", re.I)
REPRIMAND_NO_RE = re.compile(r"(\d+)(?:st|nd|rd|th)\s+reprimand", re.I)
DROP_RE = re.compile(r"drop of (\d+) grid (?:positions?|places?)|(\d+)[ -]grid[ -]places?(?: penalty)?|"
                     r"(\d+)[ -]places? grid penalty|grid (?:place )?penalty of (\d+)", re.I)


def published_at(s):
    """'07.11.26 17:38 CET' (fia.com shows Paris time) -> aware datetime."""
    from zoneinfo import ZoneInfo
    try:
        return datetime.strptime(s.replace(" CET", "").strip(), "%d.%m.%y %H:%M").replace(tzinfo=ZoneInfo("Europe/Paris"))
    except (ValueError, KeyError, AttributeError):
        return None


def is_sprint_quali(session):
    return bool(re.search(r"sprint\s+(qualifying|shootout)", session or "", re.I))


# ----------------------------------------------------------------------------- one decision
def record(doc, dec, event, year):
    """The parts of a decision that matter for grids, points and reprimands."""
    who = dec.get("no") or ""
    m = re.match(r"\s*(\d{1,2})\s*[-–]\s*(.+)", who)
    number = int(m.group(1)) if m else (doc["cars"][0] if doc.get("cars") else None)
    name = m.group(2).strip() if m else None
    text = re.sub(r"\s+", " ", dec.get("decision") or "")
    low = text.lower()
    r = {"number": number, "name": name, "competitor": dec.get("competitor"), "event": event, "year": year,
         "session": dec.get("session"), "published": doc["published"], "title": doc["title"], "url": doc["url"],
         "doc": doc.get("no"), "decision": text, "points": None, "points_total": None, "reprimand": None,
         "reprimand_no": None, "grid": None, "articles": dec.get("articles") or []}
    p = POINTS_RE.search(text)
    if p and not re.search(r"\bno penalty points\b", low):
        r["points"] = int(p.group(1))
        r["points_total"] = int(p.group(2)) if p.group(2) else None
    # a driver reprimand ("Driver: Reprimand (Driving)"), not one for the team ("Competitor: Reprimand")
    rep = re.search(r"(?:^|driver\s*:\s*|\.\s*)reprimand(?:\s*\(([^)]*)\))?", low)
    if rep and not re.search(r"competitor\s*:\s*reprimand", low[:rep.end()]):
        kind = (rep.group(1) or "").strip()
        r["reprimand"] = "driving" if kind.startswith("driving") else "other"
        n = REPRIMAND_NO_RE.search(text)
        r["reprimand_no"] = int(n.group(1)) if n else None
    r["grid"] = grid_effect(text, dec.get("session"))
    return r


def grid_effect(text, session):
    """Grid places a decision costs: {places, back, pit_lane, target: Race|Sprint, later: served at a later event}."""
    low = text.lower()
    if "suspended" in low:
        return None
    places = next((int(g) for m in DROP_RE.finditer(text) for g in m.groups() if g), 0)
    back = "back of the grid" in low
    pit = bool(re.search(r"(start|starting)[^.]{0,60}pit ?lane|pit ?lane start", low))
    if not (places or back or pit):
        return None
    # B1.9.4b: grid drops from Sprint Qualifying go on the Sprint grid; everything else on the Race
    target = "Sprint" if is_sprint_quali(session) or (re.search(r"\bsprint\b", low) and
                                                       not re.search(r"\brace\b", low)) else "Race"
    # a drop imposed in the race itself (or "for the next Competition") is served at the next Grand Prix;
    # one from the Sprint goes on the same weekend's Race grid
    later = bool(re.search(r"next competition", low)) or (session or "").strip().lower() == "race"
    return {"places": places, "back": back or places > BACK_OF_GRID, "pit_lane": pit, "target": target, "later": later}


# ----------------------------------------------------------------------------- the grid
def provisional_grid(order, penalties):
    """B2.5.4b. order: [{number, ..., quali_pos, classified}] in qualifying order.
    penalties: {number: {places, back, pit_lane, reasons}}. Returns the order with pos/penalty/notes."""
    cls = [d for d in order if d.get("classified")]
    uncls = [d for d in order if not d.get("classified")]
    pen = {n: p for n, p in penalties.items() if p.get("places") or p.get("back") or p.get("pit_lane")}
    pit = [d for d in cls if pen.get(d["number"], {}).get("pit_lane")]
    back = [d for d in cls if d not in pit and pen.get(d["number"], {}).get("back")]
    dropped = [d for d in cls if d not in pit and d not in back and pen.get(d["number"], {}).get("places")]
    clean = [d for d in cls if d not in pit and d not in back and d not in dropped]

    slot = {}                                   # temporary grid position -> number (may run past the grid size)
    # (i) penalised drivers: qualifying position + penalty; on a tie the slowest keeps it, the others go ahead
    for d in sorted(dropped, key=lambda d: -d["quali_pos"]):
        want = d["quali_pos"] + pen[d["number"]]["places"]
        while want in slot and want > 1:
            want -= 1
        while want in slot:
            want += 1
        slot[want] = d["number"]
    # (ii) unpenalised drivers fill the free positions in qualifying order
    free = (i for i in range(1, 1000) if i not in slot)
    for d in clean:
        slot[next(free)] = d["number"]
    # (iii) penalised drivers move up into any gap: just close the order up
    ranked = [slot[k] for k in sorted(slot)]
    # (iv) more than 15 places: behind every other classified driver, in qualifying order; then pit-lane starters
    ranked += [d["number"] for d in back]
    by_no = {d["number"]: d for d in order}
    out = []
    for n in ranked + [d["number"] for d in uncls]:
        d = by_no[n]
        p = pen.get(n, {})
        out.append(dict(d, pos=len(out) + 1, penalty=p.get("places", 0), back=bool(p.get("back")),
                        pit_lane=False, reasons=p.get("reasons", [])))
    for d in pit:      # (B5.3) pit-lane starters leave after the field; listed last
        p = pen[d["number"]]
        out.append(dict(d, pos=None, penalty=p.get("places", 0), back=False, pit_lane=True, reasons=p.get("reasons", [])))
    for d in out:
        d["moved"] = (d["quali_pos"] - d["pos"]) if d.get("pos") and d.get("quali_pos") else None
    return out


def penalties_for(records, event, year, target, earlier=()):
    """{number: {places, back, pit_lane, reasons}} for this event's `target` grid (Race or Sprint).
    earlier: records from the previous Grand Prix, whose race-incident drops are served here."""
    out = {}

    def add(r, carried=False):
        g = r["grid"]
        p = out.setdefault(r["number"], {"places": 0, "back": False, "pit_lane": False, "reasons": []})
        p["places"] += g["places"]
        p["back"] = p["back"] or g["back"]
        p["pit_lane"] = p["pit_lane"] or g["pit_lane"]
        p["reasons"].append({"title": r["title"], "url": r["url"], "decision": r["decision"], "places": g["places"],
                             "back": g["back"], "pit_lane": g["pit_lane"], "carried": carried,
                             "event": r["event"], "session": r["session"]})
        if p["places"] > BACK_OF_GRID:
            p["back"] = True

    for r in records:
        if r.get("grid") and r["number"] and r["event"] == event and r["year"] == year \
                and not r["grid"]["later"] and r["grid"]["target"] == target:
            add(r)
    if target == "Race":
        for r in earlier:
            if r.get("grid") and r["number"] and r["grid"]["later"]:
                add(r, carried=True)
    return out


# ----------------------------------------------------------------------------- points + reprimands
def driver_key(r):
    return fia._norm(r.get("name") or "") or f"car {r.get('number')}"


def points_table(records, now=None):
    """Penalty points still live (imposed in the last 12 months) and this season's reprimands, per driver."""
    now = now or datetime.now(timezone.utc)
    drivers = {}
    for r in sorted(records, key=lambda r: published_at(r["published"]) or now):
        when = published_at(r["published"])
        if not when or when > now:
            continue
        d = drivers.setdefault(driver_key(r), {"name": r.get("name"), "number": r.get("number"), "points": 0,
                                               "items": [], "reprimands": [], "stated_total": None})
        d["number"] = r.get("number") or d["number"]
        d["name"] = r.get("name") or d["name"]
        if r.get("points") and when > now - timedelta(days=365):
            expires = when + timedelta(days=365)
            d["points"] += r["points"]
            d["items"].append({"points": r["points"], "event": r["event"], "year": r["year"], "session": r["session"],
                               "title": r["title"], "url": r["url"], "date": when.date().isoformat(),
                               "expires": expires.date().isoformat()})
            if r.get("points_total") is not None:
                d["stated_total"] = r["points_total"]
        if r.get("reprimand") and r["year"] == now.year:
            d["reprimands"].append({"kind": r["reprimand"], "no": r.get("reprimand_no"), "event": r["event"],
                                    "session": r["session"], "title": r["title"], "url": r["url"],
                                    "date": when.date().isoformat()})
    out = []
    for d in drivers.values():
        if not d["points"] and not d["reprimands"]:
            continue
        rep = len(d["reprimands"])
        driving = sum(1 for x in d["reprimands"] if x["kind"] == "driving")
        d["items"].sort(key=lambda x: x["expires"])
        d.update(reprimand_count=rep, driving_reprimands=driving,
                 next_expiry=d["items"][0] if d["items"] else None,
                 to_ban=max(0, BAN_POINTS - d["points"]),
                 # the fifth reprimand (with four for driving) is a 10-place grid drop: one more would do it
                 reprimand_risk=rep == 4 and driving >= 3)
        out.append(d)
    out.sort(key=lambda d: (-d["points"], -d["reprimand_count"], d["name"] or ""))
    return out


# ----------------------------------------------------------------------------- the season index
class Index:
    """Every stewarding decision of the last 12 months, read once from fia.com in the background
    (each PDF is cached for good, so later runs only fetch what's new)."""
    REFRESH = 600        # s between looks for new documents at the current event
    PACE = 6             # s between new PDF downloads: fia.com sits behind a bot check that trips on bursts

    def __init__(self):
        self.lock = threading.Lock()
        self.records = {}          # url -> record
        self.events = {}           # (year, name) -> {"first": dt, "last": dt}
        self.status = {"state": "idle", "done": 0, "total": 0, "error": None, "built": None}
        self._thread = None
        self._wanted = None

    def ensure(self, year, current_event=None):
        """Start (or refresh) the background build; returns immediately."""
        with self.lock:
            stale = not self.status["built"] or time.time() - self.status["built"] > self.REFRESH
            if self._thread and self._thread.is_alive():
                return
            if not stale and self._wanted == (year, current_event):
                return
            self._wanted = (year, current_event)
            self._thread = threading.Thread(target=self._build, args=(year, current_event), daemon=True)
            self._thread.start()

    def _build(self, year, current_event):
        now = datetime.now(timezone.utc)
        self.status.update(state="building", error=None)
        try:
            todo = []
            for y in (year - 1, year):
                try:
                    events = fia.season_events(y)
                except fia.FIAError:
                    continue
                for path, name in events:
                    live = y == year and current_event and fia._norm(name) == fia._norm(current_event)
                    ttl = 120 if live else None if y < now.year else 12 * 3600
                    try:
                        docs = fia.event_documents(y, name, ttl=ttl, url=fia.event_url(path))
                    except fia.FIAError:
                        continue
                    if fia.blocked() and not docs:
                        continue
                    dates = [t for t in (published_at(d["published"]) for d in docs) if t]
                    if dates:
                        self.events[(y, name)] = {"first": min(dates), "last": max(dates)}
                    if dates and max(dates) < now - timedelta(days=370):
                        continue
                    for d in docs:
                        if d["kind"] in STEWARDING_KINDS and d["cars"] and not SKIP_TITLE.search(d["title"]) \
                                and (live or DRIVING_TITLE.search(d["offence"] or d["title"])) and d["url"] not in self.records:
                            todo.append((bool(live), published_at(d["published"]) or now, d, name, y))
            # this weekend first, then newest first: what's on screen fills in before last year's paperwork
            todo.sort(key=lambda x: (x[0], x[1]), reverse=True)
            self.status.update(total=len(todo) + len(self.records), done=len(self.records))
            paused = None
            for _, _, d, name, y in todo:
                cached = (fia.CACHE / (fia.hashlib.sha1(d["url"].encode()).hexdigest() + ".txt")).exists()
                try:
                    dec = fia.decision(d["url"])
                except fia.FIABlocked as e:
                    # skip what isn't cached yet (the cached ones still load); the next ensure() after the
                    # pause carries on where this left off
                    paused = str(e)
                    continue
                except fia.FIAError as e:
                    self.status["error"] = str(e)
                    if "pdftotext" in str(e):
                        break
                    continue
                self.status["error"] = None
                with self.lock:
                    self.records[d["url"]] = record(d, dec, name, y)
                self.status["done"] += 1
                if not cached:
                    time.sleep(self.PACE)
            if paused:
                self.status.update(state="paused", error=paused, built=time.time() - self.REFRESH + fia.blocked())
            else:
                self.status.update(state="ready", built=time.time())
        except Exception as e:          # never take the server down over paperwork
            self.status.update(state="error", error=str(e), built=time.time())

    def all(self, before=None):
        with self.lock:
            recs = list(self.records.values())
        if before:
            recs = [r for r in recs if (published_at(r["published"]) or before) <= before]
        return recs

    def previous_event(self, year, event):
        """(year, name) of the Grand Prix before this one, by document dates."""
        me = self.events.get((year, event)) or next((v for (y, n), v in self.events.items()
                                                     if y == year and fia._norm(n) == fia._norm(event)), None)
        if not me:
            return None
        older = [(v["last"], k) for k, v in self.events.items() if v["last"] < me["first"]]
        return max(older)[1] if older else None
