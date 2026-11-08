"""Live OpenF1 data as it arrived, kept per session: for replaying a session from it later, and for measuring how
late the live data really was.

While a session runs, every live REST response the dashboard gets and every MQTT message is appended to
recordings/openf1/<session_key>_<place>_<session>/ (api.RECORDINGS; F1HUB_RECORD=0 turns recording off,
and what's there can still be replayed):
  <endpoint>.jsonl.gz   one line per response: {"t": arrival, "endpoint", "params", "url", "data"}
  stream.jsonl.gz       one line per message:  {"rx": arrival, "topic", "row"}
  session.json          the session's details
Recording stops once the session is over (past its end and quiet), so polling afterwards adds nothing.

Earlier versions wrote recordings/<UTC day>/ instead, mixing sessions. Those are still read.

Recorded sessions replay through the usual ReplayTracker (RecordingStore, the same interface as
replay.SessionStore), under keys of their own: REC_KEY_MIN + the OpenF1 session key.
"""
import gzip
import json
import logging
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from . import api
from .stream import normalise_dates

REC_KEY_MIN = 100_000_000          # replay keys for recordings (FastF1's start at 200_000_000)
BACKFILL_GAP = 90                  # s: a request not made in the last 90 s is a fresh start, not a poll (latency())
log = logging.getLogger("f1hub.recordings")

# What makes a row the same row in two responses. A later version replaces an earlier one (a lap gets its time, a
# stint its end...); endpoints not listed compare whole rows.
IDENTITY = {"laps": ("driver_number", "lap_number"), "stints": ("driver_number", "stint_number"),
            "pit": ("driver_number", "lap_number"), "drivers": ("driver_number",),
            "starting_grid": ("driver_number",), "session_result": ("driver_number",),
            "championship_drivers": ("driver_number",), "championship_teams": ("team_name",),
            "location": ("driver_number", "date"), "car_data": ("driver_number", "date"),
            "position": ("driver_number", "date"), "intervals": ("driver_number", "date"),
            "weather": ("date",), "team_radio": ("driver_number", "date"),
            "overtakes": ("date", "overtaking_driver_number", "overtaken_driver_number"),
            "race_control": ("date", "category", "message")}
CURSOR = ("date", "lap_number")      # request parameters that only say from/to where (date>, lap_number>=...)
STATIC = ("sessions", "meetings", "drivers", "starting_grid", "session_result")
TIMING = {"position", "intervals", "laps", "location", "race_control", "car_data", "stream"}   # worth replaying


def is_key(key):
    try:
        return REC_KEY_MIN <= int(key) < 2 * REC_KEY_MIN
    except (TypeError, ValueError):
        return False


def key_for(session_key):
    return REC_KEY_MIN + int(session_key)


def _root(writing=False):
    """recordings/openf1: read from api.RECORDINGS always, written to only while recording is on (api.RECORD_DIR)."""
    base = api.RECORD_DIR if writing else api.RECORDINGS
    return None if base is None else Path(base) / "openf1"


def folder_name(session):
    label = f"{session.get('location') or session.get('country_name') or 'session'}_{session.get('session_name') or ''}"
    label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()      # São Paulo -> Sao Paulo
    return f"{session['session_key']}_" + re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")


# ------------------------------------------------------------------------------------------------ writing
class SessionRecording:
    """Where one live session's data goes. `on` is switched off once the session is over."""

    def __init__(self, session):
        self.session = session
        self.key = session["session_key"]
        root = _root(writing=True)
        self.folder = root / folder_name(session) if root else None
        self.on = True
        self._lock = threading.Lock()
        self._started = False
        _active[self.key] = self

    def rest(self, endpoint, url, params, data):
        """api.get's record callback: one live response."""
        self._write(endpoint, [{"t": time.time(), "endpoint": endpoint, "params": params, "url": url, "data": data}])

    def stream(self, rows):
        """Streamed messages for this session: [(arrival, topic, row)]."""
        self._write("stream", [{"rx": rx, "topic": t, "row": r} for rx, t, r in rows])

    def _write(self, name, lines):
        if not self.on or self.folder is None or not lines:
            return
        try:
            with self._lock:
                if not self._started:
                    self.folder.mkdir(parents=True, exist_ok=True)
                    meta = self.folder / "session.json"
                    if not meta.exists():
                        api.write_atomic(meta, json.dumps({"session": self.session, "recorded_from": time.time()}))
                    self._started = True
                with gzip.open(self.folder / f"{name}.jsonl.gz", "at", encoding="utf-8") as f:
                    f.write("".join(json.dumps(x) + "\n" for x in lines))
        except OSError as e:          # recording must never break live timing
            log.warning("could not record %s: %s", name, e)


_active = {}          # session_key -> SessionRecording (the stream files its messages by the session they're for)


def recording_for(session_key):
    return _active.get(session_key)


# ------------------------------------------------------------------------------------------------ reading
def _lines(path):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue            # a line cut short when the app was killed mid-write
    except (OSError, EOFError):        # (a truncated gzip member at the very end)
        return


def _line_session(d):
    if "row" in d:
        return (d.get("row") or {}).get("session_key")
    sk = (d.get("params") or {}).get("session_key")
    if isinstance(sk, int) or (isinstance(sk, str) and sk.isdigit()):
        return int(sk)
    data = d.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0].get("session_key")
    return None


_day_index = {}      # path -> ((size, mtime), {session_key: {"n", "first", "last"}})
_index_lock = threading.Lock()


def _day_files():
    base = Path(api.RECORDINGS)
    if not base.exists():
        return []
    return sorted(p for p in base.glob("20??-??-??/*.jsonl.gz"))


def _index_day_file(path):
    """Which sessions an older (per-day) recording file has lines for. Read once per process and size."""
    st = path.stat()
    stamp = (st.st_size, st.st_mtime)
    with _index_lock:
        hit = _day_index.get(path)
        if hit and hit[0] == stamp:
            return hit[1]
    found = {}
    for d in _lines(path):
        sk = _line_session(d)
        if sk is None:
            continue
        t = d.get("t") or d.get("rx")
        e = found.setdefault(sk, {"n": 0, "first": t, "last": t})
        e["n"] += 1
        if t:
            e["first"] = min(e["first"] or t, t)
            e["last"] = max(e["last"] or t, t)
    with _index_lock:
        _day_index[path] = (stamp, found)
    return found


def _session_folders():
    root = _root()
    if not root or not root.exists():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and re.match(r"\d+_", p.name))


def sessions():
    """Recorded sessions, newest first: {"key" (to replay), "session_key", "session", "bytes", "first", "last"}."""
    found = {}
    for folder in _session_folders():
        sk = int(folder.name.split("_", 1)[0])
        try:
            meta = json.loads((folder / "session.json").read_text())
        except (OSError, ValueError):
            meta = {}
        files = list(folder.glob("*.jsonl.gz"))
        if not files:
            continue
        e = found.setdefault(sk, {"session": meta.get("session"), "bytes": 0, "first": None, "last": None,
                                  "layout": "session", "endpoints": set()})
        e["bytes"] += sum(f.stat().st_size for f in files)
        e["endpoints"] |= {f.name.split(".", 1)[0] for f in files}
        e["first"] = meta.get("recorded_from")
        e["last"] = max(f.stat().st_mtime for f in files)
    for path in _day_files():
        for sk, info in _index_day_file(path).items():
            e = found.setdefault(sk, {"session": None, "bytes": 0, "first": None, "last": None, "layout": "day",
                                      "endpoints": set()})
            e["endpoints"].add(path.name.split(".", 1)[0])
            e["bytes"] += round(path.stat().st_size * info["n"] / max(1, sum(x["n"] for x in _index_day_file(path).values())))
            e["first"] = min(x for x in (e["first"], info["first"]) if x) if info["first"] else e["first"]
            e["last"] = max(x for x in (e["last"], info["last"]) if x) if info["last"] else e["last"]
    out = []
    for sk, e in found.items():
        if not e["endpoints"] & TIMING:
            continue          # only the session's details (it was looked at, not followed): nothing to replay
        if e["session"] is None:
            e["session"] = _recorded_session(sk)
        if not e["session"]:
            continue
        out.append(dict(e, key=key_for(sk), session_key=sk, endpoints=sorted(e["endpoints"])))
    out.sort(key=lambda e: e["session"].get("date_start") or "", reverse=True)
    return out


def _recorded_session(sk):
    """The session's own row: from a recorded "sessions" response, else OpenF1 (cached)."""
    for path in _day_files():
        if path.name == "sessions.jsonl.gz" and sk in _index_day_file(path):
            for d in _lines(path):
                for row in d.get("data") or []:
                    if isinstance(row, dict) and row.get("session_key") == sk and row.get("date_start"):
                        return row
    try:
        rows = api.get("sessions", ttl=api.FOREVER, session_key=sk)
        return rows[0] if rows else None
    except api.APIError:
        return None


def _files_for(sk):
    files = []
    for folder in _session_folders():
        if folder.name.split("_", 1)[0] == str(sk):
            files += sorted(folder.glob("*.jsonl.gz"))
    files += [p for p in _day_files() if sk in _index_day_file(p)]
    return files


def _ts(s):
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s).timestamp()
    except ValueError:
        return None


def _merge(sk):
    """Every row recorded for a session, per endpoint: REST responses and streamed messages together, each row
    once (the latest version of a lap, a stint...). Also when each row first arrived, and how: {(endpoint, row
    identity): (arrival, "rest" / "stream")} (for latency())."""
    rows, first_seen, version_at, last_asked = {}, {}, {}, {}
    for path in _files_for(sk):
        for d in _lines(path):
            if _line_session(d) not in (sk, None):
                continue
            reach = None
            if "row" in d:
                endpoint, batch, at, via = d.get("topic"), [d.get("row")], d.get("rx"), "stream"
            else:
                endpoint, batch, at, via = d.get("endpoint"), d.get("data"), d.get("t"), "rest"
                # The first answer to a request (F1 Hub just started, a driver just picked, back after a gap) brings
                # everything since some earlier time: that's catching up, not how late the live data is.
                params = d.get("params") or {}
                ask = (endpoint, tuple(sorted((k, str(v)) for k, v in params.items() if not k.startswith(CURSOR))))
                before, last_asked[ask] = last_asked.get(ask), (at, params)
                if at is None or before is None or before[0] is None or at - before[0] > BACKFILL_GAP:
                    via = "catch-up"
                else:
                    reach = before[1]
            if not endpoint or not isinstance(batch, list):
                continue
            table = rows.setdefault(endpoint, {})
            for r in batch:
                if not isinstance(r, dict) or r.get("session_key") not in (sk, None):
                    continue
                r = {k: v for k, v in r.items() if not k.startswith("_")}
                if via == "stream":
                    normalise_dates(r)          # recorded as it came: REST's form, so the two copies match
                ident = IDENTITY.get(endpoint)
                key = tuple(_same(k, r.get(k)) for k in ident) if ident else json.dumps(r, sort_keys=True)
                fk = (endpoint, key)
                if (at or 0) >= version_at.get(fk, 0):          # the latest version to arrive wins
                    table[key], version_at[fk] = r, at or 0
                if endpoint == "laps" and not r.get("lap_duration"):
                    continue                    # a lap only counts as arrived once it has its time
                # The poll before couldn't have returned this row (it asked from a later point - the stream or a
                # late row had moved the cursor on), so when it actually turned up isn't known.
                row_via = via if reach is None or _in_reach(reach, r) else "catch-up"
                if at and (fk not in first_seen or at < first_seen[fk][0]):
                    first_seen[fk] = (at, row_via)
    return rows, first_seen


def _same(field, value):
    """A row-identity value: dates as instants, so the same time written two ways is the same row."""
    if field in ("date", "date_start") and isinstance(value, str):
        t = _ts(value)
        return value if t is None else round(t, 3)
    return value


_CMP = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}


def _in_reach(params, row):
    """Whether a request with these parameters would have returned the row (its date>, lap_number>=... limits)."""
    for k, v in params.items():
        field = next((f for f in CURSOR if k.startswith(f)), None)
        op = _CMP.get(k[len(field):]) if field else None
        if op is None:
            continue
        a, b = (_ts(row.get("date")), _ts(v)) if field == "date" else (row.get(field), v)
        try:
            if a is not None and b is not None and not op(a, float(b) if field == "lap_number" else b):
                return False
        except (TypeError, ValueError):
            pass
    return True


class RecordingStore:
    """A recorded session, with replay.SessionStore's interface (like ff1.FF1Store)."""

    def __init__(self, key):
        self.key = int(key)
        sk = self.key - REC_KEY_MIN
        merged = {ep: list(t.values()) for ep, t in _merge(sk)[0].items()}
        if not merged:
            raise api.APIError(f"Nothing recorded for session {sk}")
        session = next((r for r in merged.get("sessions", []) if r.get("session_key") == sk), None) or _recorded_session(sk)
        if not session:
            raise api.APIError(f"No details recorded for session {sk}")
        self.session = session = dict(session)
        self.meeting = next((m for m in merged.get("meetings", []) if m.get("meeting_key") == session.get("meeting_key")),
                            None) or self._meeting(session)
        self.static = {"sessions": [session], "meetings": [self.meeting] if self.meeting else [],
                       **{ep: merged.get(ep, []) for ep in STATIC if ep not in ("sessions", "meetings")}}
        self.rows = {}
        for ep, rs in merged.items():
            if ep in STATIC:
                continue
            for r in rs:
                r["_ts"] = _ts(r.get("date") or r.get("date_start"))
            rs.sort(key=lambda r: r["_ts"] or 0)
            self.rows[ep] = rs
        self.finished = True
        self.scheduled_laps = self._scheduled_laps()
        rc = [r["_ts"] for r in self.rows.get("race_control", []) if r.get("category") == "SessionStatus" and r["_ts"]]
        self._end = max(rc + [_ts(session.get("date_end")) or 0])

    @staticmethod
    def _meeting(session):
        """Recordings from before per-session folders don't have the meeting: OpenF1's (cached) will do."""
        try:
            rows = api.get("meetings", ttl=api.FOREVER, meeting_key=session.get("meeting_key"))
            return rows[0] if rows else {}
        except api.APIError:
            return {}

    def _scheduled_laps(self):
        """The race distance, as the live tracker finds it: last year's winner's laps (cached OpenF1)."""
        if self.session.get("session_type") != "Race" or self.session.get("session_name") == "Sprint":
            return None
        try:
            prev = api.get("sessions", circuit_key=self.session.get("circuit_key"), session_name="Race",
                           year=(self.session.get("year") or 0) - 1)
            res = api.get("session_result", session_key=prev[0]["session_key"], position=1) if prev else []
            return res[0].get("number_of_laps") if res else None
        except api.APIError:
            return None

    def actual_end(self):
        return datetime.fromtimestamp(self._end, timezone.utc)

    def get(self, endpoint, upto_ts):
        return self.rows.get(endpoint, [])

    def query(self, endpoint, **params):
        if endpoint in self.static:
            rows = self.static[endpoint]
        else:
            rows = self.rows.get(endpoint, [])
        lo_incl = "date>=" in params
        lo = _ts(params.get("date>") or params.get("date>="))
        hi = _ts(params.get("date<"))
        out = []
        for r in rows:
            if any(r.get(k) != v for k, v in params.items() if k not in ("session_key", "meeting_key", "date>", "date>=", "date<")):
                continue
            t = r.get("_ts")
            if t is not None and ((lo is not None and (t < lo if lo_incl else t <= lo)) or (hi is not None and t >= hi)):
                continue
            out.append(r)
        return out


_stores = {}
_store_lock = threading.Lock()


def store(key):
    key = int(key)
    with _store_lock:
        if key not in _stores:
            _stores.clear()
            _stores[key] = RecordingStore(key)
        return _stores[key]


# ------------------------------------------------------------------------------------------------ latency
def _pct(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))], 2) if xs else None


def latency(key):
    """How late the live data was: for each endpoint, seconds from a row's own time to when it first reached F1 Hub
    (REST: includes waiting for the next poll; stream: the push itself). Median, 90th percentile and count.
    Rows that came in a first answer (catching up on what happened before F1 Hub asked), or that the poll before
    couldn't have returned (it asked from a later point), aren't counted: when those turned up isn't known."""
    sk = int(key) - REC_KEY_MIN if is_key(key) else int(key)
    rows, first_seen = _merge(sk)
    by = {}
    for (endpoint, ident), (at, via) in first_seen.items():
        if endpoint in STATIC or via == "catch-up":
            continue
        r = rows[endpoint][ident]                # the final version: a lap's time is only known at its end
        t = _ts(r.get("date") or r.get("date_start"))
        if t is None:
            continue
        if endpoint == "laps":
            if not r.get("lap_duration"):
                continue
            t += r["lap_duration"]
        delay = at - t
        if -5 < delay < 600:          # outside that it's a late correction or a clock problem, not delay
            by.setdefault((endpoint, via), []).append(delay)
    out = {}
    for (endpoint, via), xs in sorted(by.items()):
        out.setdefault(endpoint, {})[via] = {"median": _pct(xs, 0.5), "p90": _pct(xs, 0.9), "n": len(xs)}
    return {"session_key": sk, "endpoints": out}
