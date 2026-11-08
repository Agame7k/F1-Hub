"""FastF1, optional: older seasons (2018-2022), smoother lap telemetry, a fallback while OpenF1 is locked,
and recording F1's live timing during a session (with an F1 TV Pro login) to play back afterwards.

FastF1 needs pandas, so it runs in the project's .venv in a worker process (f1hub/ff1_worker.py), like the
speech-to-text. Without it everything here reports "unavailable" and the app carries on with OpenF1 alone.

  .venv/bin/pip install fastf1

Session keys: FastF1 sessions get keys OpenF1 never uses, YYYYRRIIR: year, round, session number (1-5) and a
recording number (0 = F1's archive, 1-9 = a recording made with FastF1), e.g. 202122050 = 2021 R22 Race.
"""
import bisect
import gzip
import json
import os
import re
import signal
import subprocess
import threading
import time
from array import array
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import api

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / ".venv" / "bin" / "python"
WORKER = Path(__file__).resolve().parent / "ff1_worker.py"
RECORD = Path(__file__).resolve().parent / "ff1_record.py"
CACHE = api.CACHE_DIR / "fastf1"
EXPORTS = CACHE / "export"
REC_DIR = ROOT / "recordings" / "fastf1"
KEY_MIN = 200_000_000
FIRST_YEAR = 2018           # F1's live-timing archive (and FastF1's timing data) starts here
SCHEDULE_TTL = 6 * 3600


class FF1Error(Exception):
    pass


# ------------------------------------------------------------------------------------------------ keys
def is_key(key):
    try:
        return int(key) >= KEY_MIN
    except (TypeError, ValueError):
        return False


def make_key(year, rnd, idx, rec=0):
    return int(year) * 100000 + int(rnd) * 1000 + int(idx) * 10 + int(rec)


def parse_key(key):
    k = int(key)
    return {"year": k // 100000, "round": k // 1000 % 100, "idx": k // 10 % 100, "rec": k % 10}


# ------------------------------------------------------------------------------------------------ worker
class Worker:
    """One FastF1 process, one request at a time."""

    def __init__(self, name):
        self.name = name
        self.proc = None
        self.lock = threading.Lock()
        self.version = None
        self.error = None
        self.busy = None
        self.n = 0

    def call(self, cmd, timeout=600, **args):
        if not VENV_PY.exists():
            raise FF1Error("FastF1 isn't installed (.venv/bin/pip install fastf1)")
        with self.lock:
            try:
                if self.proc is None or self.proc.poll() is not None:
                    self._start()
                self.n += 1
                self.busy = cmd
                self.proc.stdin.write(json.dumps({"id": self.n, "cmd": cmd, "args": args}) + "\n")
                self.proc.stdin.flush()
                line = self._readline(timeout)
                res = json.loads(line)
                if res.get("id") != self.n:
                    raise FF1Error("FastF1 worker out of step")
            except (OSError, ValueError, FF1Error, TimeoutError) as e:
                self._kill()
                self.error = f"{type(e).__name__}: {e}"
                raise FF1Error(self.error) from None
            finally:
                self.busy = None
        if "error" in res:
            raise FF1Error(res["error"])
        self.error = None
        return res["ok"]

    def _readline(self, timeout):
        box = []
        t = threading.Thread(target=lambda: box.append(self.proc.stdout.readline()), daemon=True)
        t.start()
        t.join(timeout)
        if not box:
            raise TimeoutError(f"FastF1 took longer than {timeout}s")
        if not box[0]:
            raise FF1Error("FastF1 worker stopped (" + (self._stderr_tail() or "no message") + ")")
        return box[0]

    def _stderr_tail(self):
        try:
            return (self.log.read_text(errors="replace")[-300:]).strip().splitlines()[-1]
        except (OSError, IndexError, AttributeError):
            return ""

    def _start(self):
        env = dict(os.environ, PYTHONNOUSERSITE="1", F1HUB_FF1_CACHE=str(CACHE))
        CACHE.mkdir(parents=True, exist_ok=True)
        self.log = CACHE / f"worker-{self.name}.log"
        self.proc = subprocess.Popen([str(VENV_PY), "-I", str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=open(self.log, "w"), text=True, bufsize=1, env=env)
        hello = json.loads(self._readline(120) or "{}")
        if not hello.get("ready"):
            raise FF1Error("FastF1 worker failed to start")
        self.version = hello.get("version")

    def _kill(self):
        if self.proc:
            try:
                self.proc.kill()
            except OSError:
                pass
        self.proc = None


QUICK = Worker("quick")       # schedules, classifications: seconds
HEAVY = Worker("heavy")       # whole sessions with telemetry: up to a couple of minutes the first time
_probe = {"ok": None, "version": None, "at": 0, "error": None}


def available():
    """Is FastF1 usable? (Checked once by starting the quick worker; rechecked every 10 minutes after a failure.)"""
    if not VENV_PY.exists():
        return False
    if _probe["ok"] is None or (not _probe["ok"] and time.time() - _probe["at"] > 600):
        try:
            QUICK.call("ping", timeout=120)
            _probe.update(ok=True, version=QUICK.version, error=None)
        except FF1Error as e:
            _probe.update(ok=False, error=str(e))
        _probe["at"] = time.time()
    return bool(_probe["ok"])


def status():
    if not VENV_PY.exists():
        return {"available": False, "error": "FastF1 isn't installed: .venv/bin/pip install fastf1", "recorder": RECORDER.status()}
    ok = available()
    return {"available": ok, "version": _probe["version"], "error": None if ok else _probe["error"],
            "login": _credentials() or os.environ.get("FASTF1_AUTH_METHOD") == "browser",
            "busy": HEAVY.busy, "loading": dict(LOADING) if LOADING else None, "recorder": RECORDER.status()}


# ------------------------------------------------------------------------------------------------ schedule / refs
def _disk_cache(name, ttl, fn):
    path = CACHE / name
    if path.exists() and (ttl is None or time.time() - path.stat().st_mtime < ttl):
        try:
            return json.loads(path.read_text())
        except ValueError:
            pass
    data = fn()
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def schedule(year):
    """Every session FastF1 knows that season, as OpenF1-shaped session rows (with FastF1 keys)."""
    year = int(year)
    past = year < datetime.now(timezone.utc).year
    return _disk_cache(f"schedule-{year}.json", None if past else SCHEDULE_TTL,
                       lambda: QUICK.call("schedule", timeout=180, year=year))


def ref_for_key(key):
    p = parse_key(key)
    ref = {"year": p["year"], "round": p["round"], "idx": p["idx"]}
    if p["rec"]:
        rec = recording_by_key(key)
        if not rec:
            raise FF1Error(f"No FastF1 recording for session {key}")
        ref.update(livedata=[rec["file"]], rec=p["rec"])
    return ref


def ref_for_openf1(session):
    """An OpenF1 session -> the same session in FastF1 (to read it while OpenF1 is locked, or for its telemetry)."""
    year = int(session.get("year") or session["date_start"][:4])
    key = f"resolve-{session['session_key']}.json"
    return _disk_cache(key, None, lambda: QUICK.call("resolve", timeout=180, year=year, date=session["date_start"]))


def key_for_openf1(session):
    r = ref_for_openf1(session)
    return make_key(r["year"], r["round"], r["idx"])


# ------------------------------------------------------------------------------------------------ session store
LOADING = {}


def export_path(key):
    p = parse_key(key)
    if p["rec"]:
        rec = recording_by_key(key)
        stamp = int(Path(rec["file"]).stat().st_mtime) if rec and Path(rec["file"]).exists() else 0
        return EXPORTS / f"{key}-{stamp}.json.gz"
    return EXPORTS / f"{key}.json.gz"


def _load_export(key, progress=None):
    path = export_path(key)
    if not path.exists():
        ref = ref_for_key(key)
        LOADING.update(key=key, since=time.time(), text="FastF1: loading the session (the first time can take a minute or two)")
        try:
            HEAVY.call("export", timeout=900, ref=ref, out=str(path))
        finally:
            LOADING.clear()
        if parse_key(key)["rec"]:       # older exports of a recording that has grown since
            for old in EXPORTS.glob(f"{key}-*.json.gz"):
                if old != path:
                    old.unlink(missing_ok=True)
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _ts(s):
    return datetime.fromisoformat(s).timestamp() if s else None


class Telemetry:
    """Location / car data per driver as flat arrays (a race is ~1M samples): windows are found by bisection."""
    LOC = ("x", "y", "z")
    CAR = ("speed", "rpm", "gear", "throttle", "brake", "drs")
    CAR_NAMES = {"gear": "n_gear"}

    def __init__(self, tel):
        self.t0 = tel.get("t0") or 0
        self.data = {}
        for kind, cols in (("loc", self.LOC), ("car", self.CAR)):
            per = {}
            for n, d in (tel.get(kind) or {}).items():
                per[int(n)] = {"t": array("q", d["t"]), **{c: array("l", d[c]) for c in cols}}
            self.data[kind] = per

    def window(self, kind, driver=None, lo=None, hi=None, lo_incl=False, key=None):
        cols = self.LOC if kind == "loc" else self.CAR
        out = []
        for n, d in self.data[kind].items():
            if driver is not None and n != driver:
                continue
            t = d["t"]
            a = 0 if lo is None else (bisect.bisect_left if lo_incl else bisect.bisect_right)(t, round((lo - self.t0) * 1000))
            b = len(t) if hi is None else bisect.bisect_left(t, round((hi - self.t0) * 1000))
            if driver is None and hi is not None and lo is None:
                a = max(a, b - 50)          # no lower bound and every car: just the newest few
            for i in range(a, b):
                r = {"date": datetime.fromtimestamp(self.t0 + t[i] / 1000, timezone.utc).isoformat(timespec="milliseconds"),
                     "driver_number": n, "session_key": key}
                for c in cols:
                    r[self.CAR_NAMES.get(c, c)] = d[c][i]
                out.append(r)
        out.sort(key=lambda r: r["date"])
        return out


class FF1Store:
    """Same interface as replay.SessionStore, answered from a FastF1 export."""

    def __init__(self, key):
        data = _load_export(key)
        self.key = int(key)
        self.session = data["session"]
        self.meeting = data["meeting"]
        self.scheduled_laps = data.get("scheduled_laps")
        self.static = {"drivers": data["drivers"], "starting_grid": data["starting_grid"],
                       "session_result": data["session_result"], "sessions": [self.session], "meetings": [self.meeting]}
        self.rows = {}
        for ep, rows in data["rows"].items():
            for r in rows:
                r["_ts"] = _ts(r.get("date") or r.get("date_start"))
            self.rows[ep] = rows
        self.tel = Telemetry(data.get("telemetry") or {})
        self.finished = True
        rc = [r["_ts"] for r in self.rows.get("race_control", []) if r.get("category") == "SessionStatus" and r["_ts"]]
        self._end = max(rc + [_ts(self.session.get("date_end")) or 0])

    def actual_end(self):
        return datetime.fromtimestamp(self._end, timezone.utc)

    def get(self, endpoint, upto_ts):
        return self.rows.get(endpoint, [])

    def query(self, endpoint, **params):
        """Anything the tracker asks for outside the preloaded tables: session info and telemetry windows."""
        if endpoint in self.static:
            rows = self.static[endpoint]
            for k, v in params.items():
                if k in ("session_key", "meeting_key"):
                    continue
                rows = [r for r in rows if r.get(k) == v]
            return rows
        if endpoint in ("location", "car_data"):
            lo = params.get("date>") or params.get("date>=")
            hi = params.get("date<")
            return self.tel.window("loc" if endpoint == "location" else "car", params.get("driver_number"),
                                   _ts(lo), _ts(hi), lo_incl="date>=" in params, key=self.key)
        if endpoint in self.rows:
            return [r for r in self.rows[endpoint] if all(r.get(k) == v for k, v in params.items() if k != "session_key")]
        return []


_stores = {}
_store_lock = threading.Lock()


def store(key):
    """A loaded FastF1 session (kept for the next request: lab and replay often ask for the same one)."""
    key = int(key)
    with _store_lock:            # web requests and the replay can ask at once: load it only once
        st = _stores.get(key)
        if st and parse_key(key)["rec"] and not export_path(key).exists():
            st = None           # the recording has grown: export again
        if st is None:
            _stores.clear()      # one at a time: a race is a few hundred MB in memory
            st = _stores[key] = FF1Store(key)
        return st


def rows(key, endpoint, **params):
    """OpenF1-shaped rows for a FastF1 session (used by analysis and the lab)."""
    return store(key).query(endpoint, **params)


# ------------------------------------------------------------------------------------------------ lap telemetry
def lap_trace(key, driver, lap="best", before=None, openf1_session=None):
    """FastF1's merged telemetry for a lap. key: a FastF1 key, or pass the OpenF1 session to look it up."""
    if openf1_session is not None:
        ref = ref_for_openf1(openf1_session)
        ref = {"year": ref["year"], "round": ref["round"], "idx": ref["idx"]}
    else:
        ref = ref_for_key(key)
    LOADING.update(key=key, since=time.time(), text="FastF1: loading telemetry")
    try:
        return HEAVY.call("lap", timeout=900, ref=ref, driver=int(driver), lap=lap, before=before)
    finally:
        LOADING.clear()


# ------------------------------------------------------------------------------------------------ recorder
def _credentials():
    return bool(os.environ.get("F1_USERNAME") and os.environ.get("F1_PASSWORD"))


class Recorder:
    """Records F1's live-timing feed with FastF1 (python -m fastf1.livetiming save) while a session runs.

    FastF1 can't process live data in real time - a recording is for playing back afterwards, or part-way
    through (whatever has been recorded so far). With an F1 TV Pro login (F1_USERNAME / F1_PASSWORD, or
    FASTF1_AUTH_METHOD=browser) the feed includes everything: car telemetry and positions."""

    def __init__(self):
        self.proc = None
        self.file = None
        self.meta = None
        self.started = None
        self.log = None

    def start(self, session=None):
        if self.running():
            return self.status()
        if not VENV_PY.exists():
            raise FF1Error("FastF1 isn't installed: .venv/bin/pip install fastf1")
        REC_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        meta = {"started": now.isoformat(), "auth": "browser" if os.environ.get("FASTF1_AUTH_METHOD") == "browser"
                else "password" if _credentials() else "none"}
        if session:
            meta.update({k: session.get(k) for k in ("session_key", "session_name", "date_start", "date_end", "year",
                                                     "location", "country_name", "meeting_key")})
            try:
                meta["ref"] = ref_for_openf1(session)
            except FF1Error as e:
                meta["ref_error"] = str(e)
        import unicodedata
        label = f"{(session or {}).get('location') or 'session'}_{(session or {}).get('session_name') or ''}"
        label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()      # São Paulo -> Sao Paulo
        label = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")
        stem = now.strftime("%Y-%m-%d_%H%M") + "_" + label
        self.file = REC_DIR / (stem + ".txt")
        self.log = REC_DIR / (stem + ".log")
        cmd = [str(VENV_PY), "-I", str(RECORD), str(self.file), "--timeout", "1800"]
        if meta["auth"] == "none":
            cmd.append("--no-auth")      # no login: F1's free feed (no car telemetry / positions for recent seasons)
        env = dict(os.environ, PYTHONNOUSERSITE="1")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=open(self.log, "w"), stderr=subprocess.STDOUT,
                                     env=env, cwd=str(ROOT), start_new_session=True)
        self.started = time.time()
        self.meta = meta
        (REC_DIR / (stem + ".json")).write_text(json.dumps(dict(meta, file=str(self.file))))
        return self.status()

    def stop(self):
        if self.running():
            try:
                os.killpg(self.proc.pid, signal.SIGINT)      # let FastF1 close the file cleanly
                self.proc.wait(10)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
        return self.status()

    def running(self):
        return bool(self.proc and self.proc.poll() is None)

    def status(self):
        out = {"running": self.running(), "credentials": _credentials(),
               "auth_method": os.environ.get("FASTF1_AUTH_METHOD") or ("password" if _credentials() else None)}
        if self.file:
            size = self.file.stat().st_size if self.file.exists() else 0
            out.update(file=self.file.name, bytes=size, started=self.started,
                       last_write=self.file.stat().st_mtime if self.file.exists() else None,
                       session=(self.meta or {}).get("session_name"), location=(self.meta or {}).get("location"))
            if self.proc and self.proc.poll() is not None:
                out["exit"] = self.proc.returncode
            try:
                tail = self.log.read_text(errors="replace").strip().splitlines()[-3:]
                out["log"] = [re.sub(r"\s+", " ", t)[:200] for t in tail]
                out["auth_failed"] = any("Failed to authenticate" in t for t in tail)
            except OSError:
                pass
        return out


RECORDER = Recorder()


def recordings():
    """Recordings made with FastF1, newest first, each with a key the replay picker can load."""
    out = []
    if not REC_DIR.exists():
        return out
    per_ref = {}
    for meta_file in sorted(REC_DIR.glob("*.json")):
        try:
            m = json.loads(meta_file.read_text())
        except ValueError:
            continue
        f = Path(m.get("file") or meta_file.with_suffix(".txt"))
        if not f.exists() or f.stat().st_size < 1000:
            continue
        ref = m.get("ref")
        item = {"file": str(f), "name": f.name, "bytes": f.stat().st_size, "started": m.get("started"),
                "session": m.get("session_name"), "location": m.get("location"), "auth": m.get("auth"),
                "recording": RECORDER.running() and RECORDER.file == f, "key": None}
        if ref:
            base = (ref["year"], ref["round"], ref["idx"])
            per_ref[base] = per_ref.get(base, 0) + 1
            if per_ref[base] <= 9:
                item["key"] = make_key(*base, per_ref[base])
        out.append(item)
    return out[::-1]


def recording_by_key(key):
    return next((r for r in recordings() if r["key"] == int(key)), None)
