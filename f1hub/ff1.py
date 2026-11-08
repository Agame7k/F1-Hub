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
REC_DIR = api.RECORDINGS / "fastf1"          # follows F1HUB_RECORD_DIR
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


# ------------------------------------------------------------------------------------------------ logs
LOG_MARK = "=== "
LOG_KEEP = 1_000_000          # bytes; past this a log starts afresh and the old one is kept as <name>.1


def open_log(path):
    """Open a log to append to (rotating it when it has grown past LOG_KEEP), with a line marking this start.
    Each start used to overwrite the log, losing whatever explained the previous failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > LOG_KEEP:
            os.replace(path, path.with_name(path.name + ".1"))
    except FileNotFoundError:
        pass
    f = open(path, "a", encoding="utf-8")
    f.write(f"{LOG_MARK}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} started (pid of F1 Hub {os.getpid()})\n")
    f.flush()
    return f


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
            lines = self.log.read_text(errors="replace")[-2000:].strip().splitlines()
            return next(l for l in reversed(lines) if l.strip() and not l.startswith(LOG_MARK))
        except (OSError, StopIteration, AttributeError):
            return ""

    def _start(self):
        env = dict(os.environ, PYTHONNOUSERSITE="1", F1HUB_FF1_CACHE=str(CACHE))
        CACHE.mkdir(parents=True, exist_ok=True)
        self.log = CACHE / f"worker-{self.name}.log"
        with open_log(self.log) as err:          # the child keeps its own copy of the handle
            self.proc = subprocess.Popen([str(VENV_PY), "-I", str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=err, text=True, bufsize=1, env=env)
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


# ------------------------------------------------------------------------------------------------ schedule / refs
def _disk_cache(name, ttl, fn):
    path = CACHE / name
    if path.exists() and (ttl is None or time.time() - path.stat().st_mtime < ttl):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            pass                          # damaged: fetch it again
    data = fn()
    try:
        api.write_atomic(path, json.dumps(data))
    except OSError:
        pass
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


# ------------------------------------------------------------------------------------------------ F1 TV sign-in
def _credentials():
    return bool(os.environ.get("F1_USERNAME") and os.environ.get("F1_PASSWORD"))


def _method():
    """FastF1's sign-in method: FASTF1_AUTH_METHOD, else FastF1's default (the password)."""
    return os.environ.get("FASTF1_AUTH_METHOD") or "password"


_auth = {"at": 0, "data": None}
_rejected = {"key": None, "data": None}


def auth(force=False):
    """The F1 TV token FastF1 has stored (it's shared by the workers, the recorder and FastF1's own CLI).
    Cached for 30 s; never signs in."""
    if force or not _auth["data"] or time.time() - _auth["at"] > 30:
        try:
            _auth["data"] = QUICK.call("auth", timeout=60)
        except FF1Error as e:
            _auth["data"] = {"signed_in": False, "error": str(e)}
        _auth["at"] = time.time()
    return _auth["data"]


def password_rejected():
    """F1's rejection of F1_USERNAME / F1_PASSWORD ({"error", "at"}), as noted by a FastF1 process, or None.
    FastF1 doesn't send a rejected password again until it changes (f1hub/ff1_signin.py)."""
    from . import ff1_signin
    if not _credentials():
        return None
    try:
        key = Path(ff1_signin.note_path(str(CACHE))).stat().st_mtime
    except OSError:
        return None
    if _rejected["key"] != key:            # (recognising the credentials takes a deliberately slow hash)
        _rejected.update(key=key, data=ff1_signin.rejected(str(CACHE)))
    return _rejected["data"]


def retry_password():
    """Forget F1's rejection so FastF1 tries the password again (after fixing the account, say)."""
    from . import ff1_signin
    ff1_signin.forget_rejection(str(CACHE))
    _rejected["key"] = None
    return status()


def status():
    rec = RECORDER.status()
    if not VENV_PY.exists():
        return {"available": False, "error": "FastF1 isn't installed: .venv/bin/pip install fastf1", "recorder": rec}
    ok = available()
    tv = auth() if ok else {"signed_in": False}
    rejected = password_rejected()
    password_ok = _credentials() and _method() == "password" and not rejected
    return {"available": ok, "version": _probe["version"], "error": None if ok else _probe["error"],
            # signed in, or expected to be: a stored F1 TV token, or a password F1 hasn't turned down
            "login": bool(tv.get("signed_in")) or bool(password_ok),
            "password": {"set": _credentials(), "method": _method(), "rejected": rejected},
            "auth": tv, "sign_in": LOGIN.status(),
            "busy": HEAVY.busy, "loading": dict(LOADING) if LOADING else None, "recorder": rec}


def _kill_group(pid, sig):
    """Signal a process started with start_new_session (it leads its own process group) and its children."""
    try:
        os.killpg(pid, sig)
        return True
    except OSError:
        try:
            os.kill(pid, sig)
            return True
        except OSError:
            return False


class Login:
    """FastF1's browser sign-in (python -m fastf1 auth f1tv --authenticate --method browser), run for the dashboard.

    FastF1 starts a small server on 127.0.0.1, prints a link to f1login.fastf1.dev and waits until that page
    (with FastF1's browser add-on) hands back the F1 TV token, which FastF1 then stores for every process to use.
    Here the link is picked out of its output and shown on the page. Gives up after TIMEOUT, whether or not the
    page is still open, and ends with the dashboard (shutdown())."""
    URL_RE = re.compile(r"https://f1login\.fastf1\.dev\S*")
    TIMEOUT = 600

    def __init__(self):
        self.proc = None
        self.url = None
        self.started = None
        self.lines = []
        self.error = None

    def start(self):
        if self.running():
            return self.status()
        if not VENV_PY.exists():
            raise FF1Error("FastF1 isn't installed: .venv/bin/pip install fastf1")
        self.url, self.error, self.lines = None, None, []
        cmd = [str(VENV_PY), "-I", "-u", "-m", "fastf1", "auth", "f1tv", "--authenticate", "--method", "browser"]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, env=dict(os.environ, PYTHONNOUSERSITE="1"), start_new_session=True)
        self.started = time.time()
        threading.Thread(target=self._read, args=(self.proc,), daemon=True, name="f1tv-sign-in").start()
        threading.Thread(target=self._watchdog, args=(self.proc,), daemon=True, name="f1tv-sign-in-timeout").start()
        deadline = time.time() + 15            # the link normally appears within a second
        while time.time() < deadline and not self.url and self.running():
            time.sleep(0.1)
        return self.status()

    def _read(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            self.lines = (self.lines + [line[:300]])[-5:]
            m = self.URL_RE.search(line)
            if m:
                self.url = m.group(0)
        proc.wait()
        _auth["at"] = 0                       # re-read the stored token on the next status

    def _watchdog(self, proc):
        try:
            proc.wait(self.TIMEOUT)
        except subprocess.TimeoutExpired:
            if self.proc is proc:
                self.cancel()
                self.error = f"Sign-in timed out after {self.TIMEOUT // 60} minutes"

    def cancel(self):
        if self.running():
            _kill_group(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                _kill_group(self.proc.pid, signal.SIGKILL)
            self.error = "Sign-in cancelled"
        return self.status()

    def running(self):
        return bool(self.proc and self.proc.poll() is None)

    def status(self):
        if self.proc is None:
            return {"state": "idle"}
        if self.running():
            return {"state": "waiting", "url": self.url, "started": self.started, "until": self.started + self.TIMEOUT}
        if self.proc.returncode == 0 and not self.error:
            return {"state": "done"}
        msg = self.error or next((l for l in reversed(self.lines) if "failed" in l.lower() or "error" in l.lower()),
                                 self.lines[-1] if self.lines else f"exit {self.proc.returncode}")
        return {"state": "failed", "error": msg}


LOGIN = Login()


def sign_out():
    """Remove FastF1's stored F1 TV token."""
    if LOGIN.running():
        LOGIN.cancel()
    subprocess.run([str(VENV_PY), "-I", "-m", "fastf1", "auth", "f1tv", "--clear"], capture_output=True, timeout=60,
                   env=dict(os.environ, PYTHONNOUSERSITE="1"))
    LOGIN.proc = None
    return {"auth": auth(force=True), "sign_in": LOGIN.status()}


# ------------------------------------------------------------------------------------------------ processes
def _processes():
    """(pid, parent pid, command line) of every process, from ps (POSIX); empty if ps isn't there."""
    try:
        out = subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def _args_of(pid):
    """A running process's command line, or None if it has gone."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode(errors="replace").strip() or None
    except FileNotFoundError:
        if os.path.isdir("/proc/self"):
            return None
    except OSError:
        return None
    try:
        out = subprocess.run(["ps", "-p", str(int(pid)), "-o", "args="], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def leftovers(procs=None):
    """FastF1 processes an earlier F1 Hub left running (it was killed, or crashed) - their parent is no F1 Hub."""
    procs = _processes() if procs is None else procs
    args = {pid: a for pid, _, a in procs}
    out = {"sign_in": [], "recorder": []}
    for pid, ppid, a in procs:
        if pid == os.getpid() or "f1hub" in args.get(ppid, ""):
            continue
        if a.startswith(str(VENV_PY)) and "-m fastf1 auth f1tv --authenticate" in a:
            out["sign_in"].append(pid)
        elif a.startswith(str(VENV_PY)) and str(RECORD) in a:
            out["recorder"].append(pid)
    return out


def tidy_leftovers():
    """At startup: end a browser sign-in left over from an earlier run (nobody can finish it any more - its link
    was on that run's page), and take over a recording still running, so it shows and can be stopped here.
    Returns what was done, for the log."""
    done = []
    found = leftovers()
    for pid in found["sign_in"]:
        if _kill_group(pid, signal.SIGTERM):
            done.append(f"ended a browser sign-in left over from an earlier run (pid {pid})")
    for pid in found["recorder"]:
        meta = RECORDER.adopt(pid)
        if meta:
            done.append(f"took over the recording still running from an earlier run (pid {pid}, {Path(meta['file']).name})")
    return done


def shutdown():
    """When the dashboard stops: end a sign-in in progress (nobody is left to finish it). A recording carries on: it
    stops by itself 30 minutes after the feed goes quiet, and the next F1 Hub takes it over. Returns notes to print."""
    notes = []
    if LOGIN.running():
        LOGIN.cancel()
        notes.append("Stopped the F1 TV sign-in that was waiting.")
    if RECORDER.running():
        notes.append(f"The FastF1 recording carries on ({RECORDER.file.name}): it stops by itself 30 minutes after "
                     "the session, and F1 Hub picks it up again when you restart it.")
    return notes


# ------------------------------------------------------------------------------------------------ recorder
AUTH_FAILED = "Failed to authenticate with Formula 1 account"


def auth_failure(lines):
    """Why a recording's sign-in failed (it then records F1's free feed), read from its log - or None."""
    for line in lines:
        if AUTH_FAILED in line:
            return line.split("continuing without authentication:", 1)[-1].strip()[:300]
    return None


def _record_auth():
    """How a recording will sign in, in the order FastF1 tries: "browser" (a stored F1 TV token, from either
    method), "password" (F1_USERNAME / F1_PASSWORD, unless F1 has rejected them) or "none" (F1's free feed)."""
    if auth().get("signed_in"):
        return "browser"
    if _method() == "password" and _credentials() and not password_rejected():
        return "password"
    return "none"


class Recorder:
    """Records F1's live-timing feed with FastF1 (python -m fastf1.livetiming save) while a session runs.

    FastF1 can't process live data in real time - a recording is for playing back afterwards, or part-way
    through (whatever has been recorded so far). With an F1 TV Pro login (F1_USERNAME / F1_PASSWORD, or
    FASTF1_AUTH_METHOD=browser) the feed includes everything: car telemetry and positions.

    The recording runs in its own process group, so it outlives a dashboard restart; the next dashboard takes it
    over (adopt) from the pid kept in its .json."""

    def __init__(self):
        self.proc = None
        self.pid = None            # an adopted recording (started by an earlier dashboard)
        self.file = None
        self.meta = None
        self.meta_file = None
        self.started = None
        self.log = None

    def start(self, session=None):
        if self.running():
            return self.status()
        if not VENV_PY.exists():
            raise FF1Error("FastF1 isn't installed: .venv/bin/pip install fastf1")
        REC_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        meta = {"started": now.isoformat(), "auth": _record_auth()}
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
        env = dict(os.environ, PYTHONNOUSERSITE="1", F1HUB_FF1_CACHE=str(CACHE))
        with open(self.log, "a", encoding="utf-8") as out:      # (the child keeps its own copy of the handle)
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                         env=env, cwd=str(ROOT), start_new_session=True)
        self.pid = None
        self.started = time.time()
        self.meta = dict(meta, file=str(self.file), pid=self.proc.pid)
        self.meta_file = REC_DIR / (stem + ".json")
        self._save_meta()
        return self.status()

    def _save_meta(self):
        try:
            api.write_atomic(self.meta_file, json.dumps(self.meta))
        except OSError:
            pass

    def adopt(self, pid):
        """Take over a recording an earlier dashboard started: the one whose .json names this pid."""
        if self.running():
            return None
        for meta_file in sorted(REC_DIR.glob("*.json"), reverse=True)[:30]:
            try:
                m = json.loads(meta_file.read_text())
            except (OSError, ValueError):
                continue
            if m.get("pid") == pid and m.get("file") and m["file"] in (_args_of(pid) or ""):
                self.proc, self.pid, self.meta, self.meta_file = None, pid, m, meta_file
                self.file = Path(m["file"])
                self.log = self.file.with_suffix(".log")
                try:
                    self.started = datetime.fromisoformat(m["started"]).timestamp()
                except (KeyError, ValueError):
                    self.started = None
                return m
        return None

    def stop(self):
        if self.running():
            pid = self.proc.pid if self.proc else self.pid
            _kill_group(pid, signal.SIGINT)      # let FastF1 close the file cleanly
            if not self._wait(10):
                _kill_group(pid, signal.SIGKILL)
                self._wait(5)
        return self.status()

    def _wait(self, timeout):
        if self.proc:
            try:
                self.proc.wait(timeout)
                return True
            except subprocess.TimeoutExpired:
                return False
        end = time.time() + timeout
        while time.time() < end:
            if not self.running():
                return True
            time.sleep(0.2)
        return False

    def running(self):
        if self.proc is not None:
            return self.proc.poll() is None
        if self.pid:
            return str(RECORD) in (_args_of(self.pid) or "")
        return False

    def status(self):
        out = {"running": self.running(), "credentials": _credentials(),
               "auth_method": os.environ.get("FASTF1_AUTH_METHOD") or ("password" if _credentials() else None)}
        if self.meta:
            out["auth"] = self.meta.get("auth")
        if self.file:
            size = self.file.stat().st_size if self.file.exists() else 0
            out.update(file=self.file.name, bytes=size, started=self.started,
                       last_write=self.file.stat().st_mtime if self.file.exists() else None,
                       session=(self.meta or {}).get("session_name"), location=(self.meta or {}).get("location"),
                       adopted=bool(self.pid))
            if self.proc and self.proc.poll() is not None:
                out["exit"] = self.proc.returncode
            try:
                lines = self.log.read_text(errors="replace").strip().splitlines()
            except (OSError, AttributeError):
                lines = []
            out["log"] = [re.sub(r"\s+", " ", t)[:200] for t in lines[-3:]]
            # the sign-in is near the top of the log: long gone from the last lines once the feed is running
            failure = auth_failure(lines)
            if failure:
                out.update(auth_failed=True, auth_error=failure, auth="none")
                if self.meta and self.meta.get("auth") != "none":
                    self.meta.update(auth="none", auth_wanted=self.meta.get("auth"), auth_error=failure)
                    self._save_meta()       # so the recordings list says "free feed" too
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
        except (OSError, ValueError):
            continue
        f = Path(m.get("file") or meta_file.with_suffix(".txt"))
        if not f.exists() or f.stat().st_size < 1000:
            continue
        auth_used = m.get("auth")
        if auth_used != "none":         # recordings from before the sign-in result was saved: check the log
            try:
                if auth_failure(f.with_suffix(".log").read_text(errors="replace").splitlines()):
                    auth_used = "none"
            except OSError:
                pass
        ref = m.get("ref")
        item = {"file": str(f), "name": f.name, "bytes": f.stat().st_size, "started": m.get("started"),
                "session": m.get("session_name"), "location": m.get("location"), "auth": auth_used,
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
