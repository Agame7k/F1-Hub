"""Checks F1 Hub's setup and says what to fix.

    python3 -m f1hub doctor

The dashboard's Diagnostics panel (/api/diagnostics) runs the same checks, using what the running dashboard
already knows rather than asking OpenF1 again. Passwords and tokens are never shown, only whether each is set.
"""
import glob
import json
import os
import shutil
import socket
import stat
import sys
import time
import urllib.request
from datetime import datetime

from . import api, ff1, logs

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
ENV_FILE = api.PROJECT_DIR / ".openf1.env"
KNOWN = ("OPENF1_USERNAME", "OPENF1_PASSWORD", "OPENF1_TOKEN", "F1_USERNAME", "F1_PASSWORD", "FASTF1_AUTH_METHOD",
         "F1HUB_CACHE", "F1HUB_RECORD", "F1HUB_RECORD_DIR", "F1HUB_STT_MODEL", "F1HUB_STT_THREADS", "NO_COLOR")
PAIRS = (("OPENF1_USERNAME", "OPENF1_PASSWORD"), ("F1_USERNAME", "F1_PASSWORD"))
LOW_DISK = 2e9          # bytes free before the disk check warns
_disk = {"at": 0, "item": None}


def item(name, status, detail, fix=None):
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def _first_line(e):
    text = str(e).strip().splitlines()
    return text[0][:300] if text else type(e).__name__


# ------------------------------------------------------------------------------------------------ the checks
def check_python():
    v = sys.version_info
    text = f"{v.major}.{v.minor}.{v.micro}"
    if v < (3, 9):
        return item("Python", FAIL, f"{text} - F1 Hub needs Python 3.9 or newer", "Install a newer Python 3")
    return item("Python", OK, text)


def env_file_keys(path=None):
    """The names (never the values) of the settings in .openf1.env, or None without one."""
    try:
        text = (path or ENV_FILE).read_text()
    except OSError:
        return None
    names = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            names.append(line.split("=", 1)[0].strip().removeprefix("export ").strip())
    return names


def check_settings(path=None):
    path = path or ENV_FILE
    out = []
    names = env_file_keys(path)
    if names is None:
        out.append(item("Settings", INFO, "No .openf1.env - free OpenF1 access (2023 on; closed during live sessions)",
                        "For live timing, put OPENF1_USERNAME and OPENF1_PASSWORD in .openf1.env (see the README)"))
    else:
        known = [n for n in names if n in KNOWN]
        out.append(item("Settings", OK, ".openf1.env sets " + (", ".join(known) if known else "nothing F1 Hub uses")))
        unknown = [n for n in names if n not in KNOWN]
        if unknown:
            out.append(item("Settings", WARN, "Not a setting F1 Hub knows: " + ", ".join(unknown),
                            "Check the spelling against the README"))
        try:
            if os.name == "posix" and path.stat().st_mode & (stat.S_IRGRP | stat.S_IROTH):
                out.append(item("Settings", WARN, ".openf1.env (passwords) can be read by other users of this computer",
                                "chmod 600 .openf1.env"))
        except OSError:
            pass
    for a, b in PAIRS:
        if bool(os.environ.get(a)) != bool(os.environ.get(b)):
            have, missing = (a, b) if os.environ.get(a) else (b, a)
            out.append(item("Settings", WARN, f"{have} is set but {missing} isn't", "Set both, or neither"))
    method = os.environ.get("FASTF1_AUTH_METHOD")
    if method and method not in ("password", "browser"):
        out.append(item("Settings", WARN, f"FASTF1_AUTH_METHOD is '{method}'", "Use password or browser"))
    return out


def check_openf1():
    """Asks OpenF1: is it reachable, and does the login work? (For the command line; the dashboard knows.)"""
    if api.offline:
        return [item("OpenF1", INFO, "Offline mode: cached data only")]
    out = []
    if api.authenticated():
        if api._auth_token():
            out.append(item("OpenF1 login", OK, "Logged in: paid access (live timing, streaming)"))
        else:
            out.append(item("OpenF1 login", FAIL, api.login_error() or "The login didn't work",
                            "Check OPENF1_USERNAME / OPENF1_PASSWORD in .openf1.env"))
    else:
        out.append(item("OpenF1 login", INFO, "Not set: free access (2023 on; closed during live sessions)"))
    try:
        s = (api.get("sessions", cache=False, session_key="latest") or [{}])[0]
        out.append(item("OpenF1", OK, f"Reachable - latest session: {s.get('location')} {s.get('session_name')}"))
    except api.LiveSessionLock:
        out.append(item("OpenF1", WARN, "A session is live, so free access is closed until it ends",
                        "Cached data still works; live timing needs a paid OpenF1 account"))
    except Exception as e:
        out.append(item("OpenF1", FAIL, f"Can't reach OpenF1: {_first_line(e)}", "Check the internet connection"))
    return out


def check_streaming():
    """Connects to OpenF1's live stream and waits for it to confirm the subscription (paid accounts only)."""
    if api.offline or not api.authenticated() or not api._auth_token():
        return []
    from .stream import SUBACK_WAIT, Stream
    s = Stream()
    try:
        deadline = time.time() + SUBACK_WAIT + 5
        while time.time() < deadline and not s.subscribed and not s.error:
            time.sleep(0.2)
    finally:
        s.stop()
    return [_stream_item(s.status())]


def _stream_item(st):
    if st.get("subscribed") and st.get("refused"):
        return item("Streaming", WARN, "Connected, but OpenF1 turned down: " + ", ".join(st["refused"]),
                    "Those come by polling instead; check what the OpenF1 plan includes")
    if st.get("subscribed"):
        return item("Streaming", OK, "Connected and subscribed: live data arrives within a second")
    if st.get("error"):
        return item("Streaming", FAIL, st["error"], "Live timing falls back to polling (a few seconds behind)")
    return item("Streaming", INFO, "Connecting...")


def check_fastf1():
    if not ff1.VENV_PY.exists():
        return [item("FastF1", INFO, "Not installed: no seasons before 2023, no F1 TV timing data",
                     "python3 -m venv .venv && .venv/bin/pip install fastf1")]
    st = ff1.status()
    if not st["available"]:
        return [item("FastF1", FAIL, f"Installed but not working: {st.get('error')}", ".venv/bin/pip install -U fastf1")]
    out = [item("FastF1", OK, f"FastF1 {st.get('version') or ''}".strip())]
    tv, pw = st.get("auth") or {}, st.get("password") or {}
    if tv.get("signed_in"):
        until = _date(tv.get("expires_at"))
        out.append(item("F1 TV", OK, "Signed in" + (f" until {until}" if until else "") +
                        (f" ({tv['product']})" if tv.get("product") else "")))
    elif pw.get("rejected"):
        r = pw["rejected"]
        out.append(item("F1 TV", FAIL, f"F1 turned down F1_USERNAME / F1_PASSWORD ({_date(r.get('at'))}): {r.get('error')}",
                        "Check the password on formula1.com, then 'Try the password again' in Explore > FastF1 "
                        "(or sign in with the browser there)"))
    elif pw.get("set") and pw.get("method") == "password":
        out.append(item("F1 TV", INFO, "F1_USERNAME / F1_PASSWORD are set: FastF1 signs in when it first needs to"))
    elif tv.get("expired"):
        out.append(item("F1 TV", WARN, "The F1 TV sign-in has expired", "Sign in again from Explore > FastF1"))
    else:
        out.append(item("F1 TV", INFO, "Not signed in: FastF1 uses F1's free feed (no car telemetry or positions)",
                        "Sign in from Explore > FastF1 in the dashboard"))
    return out


def _date(t):
    try:
        return datetime.fromtimestamp(float(t)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def check_tools():
    out = []
    for exe, works, missing, fix in (
            ("ffmpeg", "skips the intro sting on team radio clips", "team radio clips keep their intro sting",
             "Install ffmpeg (sudo apt install ffmpeg)"),
            ("pdftotext", "reads FIA decision documents", "FIA decision documents can't be read",
             "Install poppler-utils (sudo apt install poppler-utils)")):
        if shutil.which(exe):
            out.append(item(exe, OK, f"Found ({works})"))
        else:
            out.append(item(exe, INFO, f"Not found: {missing}", fix))
    whisper = glob.glob(str(api.PROJECT_DIR / ".venv" / "lib" / "python*" / "site-packages" / "faster_whisper"))
    if whisper:
        out.append(item("Speech to text", OK, "faster-whisper is installed (team radio transcripts)"))
    else:
        out.append(item("Speech to text", INFO, "faster-whisper isn't installed: no team radio transcripts",
                        ".venv/bin/pip install faster-whisper"))
    return out


def check_disk(max_age=0):
    """Cache and recordings size, free space, and what cleanup could clear (cached for max_age seconds)."""
    if _disk["item"] and time.time() - _disk["at"] < max_age:
        return _disk["item"]
    from .cleanup import kept, mb, plan
    used = sum(size for _, size in kept())
    base = api.CACHE_DIR if api.CACHE_DIR.exists() else api.PROJECT_DIR
    free = shutil.disk_usage(base).free
    spare = sum(size for _, _, size in plan())
    detail = f"Cache and recordings {mb(used)}; {free / 1e9:,.1f} GB free"
    fix = f"{mb(spare)} can be cleared: python3 -m f1hub cleanup" if spare > 10e6 else None
    _disk.update(at=time.time(), item=item("Disk", WARN if free < LOW_DISK else OK, detail, fix))
    return _disk["item"]


def check_processes():
    found = ff1.leftovers()
    out = []
    for pid in found["sign_in"]:
        out.append(item("Processes", WARN, f"An F1 TV sign-in from an earlier run is still waiting (pid {pid})",
                        f"The dashboard ends it when it next starts, or: kill {pid}"))
    for pid in found["recorder"]:
        out.append(item("Processes", INFO, f"A FastF1 recording from an earlier run is still going (pid {pid})",
                        "The dashboard takes it over when it starts; it stops by itself after the session"))
    return out


def check_port(port):
    try:
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)    # as the server does: TIME_WAIT isn't "in use"
            s.bind(("127.0.0.1", port))
        return item("Dashboard", OK, f"Port {port} is free")
    except OSError:
        pass
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=10) as r:
            json.loads(r.read())
        return item("Dashboard", OK, f"F1 Hub is running at http://localhost:{port}")
    except Exception:
        return item("Dashboard", WARN, f"Port {port} is in use by another program",
                    f"python3 -m f1hub gui --port {port + 1}")


def check_log(recent):
    if not recent:
        return item("Log", OK, "No warnings or errors logged recently")
    errors = sum(1 for r in recent if r["level"] in ("ERROR", "CRITICAL"))
    return item("Log", INFO, f"{len(recent)} recent warnings" + (f" ({errors} errors)" if errors else "") +
                f"; the latest: {recent[-1]['message']}", f"Full log: {logs.log_file()}")


# ------------------------------------------------------------------------------------------------ the dashboard's
def dashboard_checks(app):
    """The checks for the dashboard's Diagnostics panel, from what the running dashboard knows."""
    from .gui import env_stamp
    checks = [check_python()] + check_settings()
    restart = env_stamp() != app.env_at_start
    if restart:
        checks.append(item("Settings", WARN, ".openf1.env has changed since the dashboard started",
                           "Restart the dashboard to use the new settings (Ctrl+C, then python3 -m f1hub gui)"))
    if api.offline:
        checks.append(item("OpenF1", INFO, "Offline mode: cached data only"))
    elif api.login_error():
        checks.append(item("OpenF1 login", FAIL, api.login_error() + " - using free access",
                           "Check OPENF1_USERNAME / OPENF1_PASSWORD in .openf1.env, then restart the dashboard"))
    elif api.authenticated():
        checks.append(item("OpenF1 login", OK, "Paid access (live timing, streaming)"))
    else:
        checks.append(item("OpenF1 login", INFO, "Free access (2023 on; closed during live sessions)"))
    checks.append(_season_item(app.loader.status()))
    cur = app.feed.current()
    if cur.get("error"):
        checks.append(item("Live timing", WARN, _first_line(cur["error"])))
    stream = getattr(app.feed, "stream", None)
    if stream is not None:
        checks.append(_stream_item(stream.status()))
    if api.RECORD_DIR is None:
        checks.append(item("Recording", INFO, "Recording live OpenF1 data is off (F1HUB_RECORD=0)"))
    else:
        checks.append(item("Recording", OK, f"Live sessions are recorded to {api.RECORD_DIR / 'openf1'}"))
    checks += check_fastf1() + check_tools() + [check_disk(max_age=600)] + check_processes()
    recent = list(logs.RECENT)[-10:]
    checks.append(check_log(recent))
    return {"checks": checks, "recent": recent, "restart_needed": restart, "log_file": str(logs.log_file()),
            "started": app.started, "problems": sum(1 for c in checks if c["status"] in (WARN, FAIL))}


def _season_item(st):
    year = st.get("year")
    if st["state"] == "loading":
        return item("Season", INFO, f"Loading the {year} season" + (f" ({st['progress']})" if st.get("progress") else ""))
    if st["state"] == "failed":
        return item("Season", FAIL, f"Couldn't load the {year} season: {st.get('error')}", "It's tried again every minute")
    if st["state"] == "stale":
        return item("Season", WARN, f"Showing an older copy of the {year} season: {st.get('error')}",
                    "It's tried again shortly")
    if st.get("missing"):
        return item("Season", WARN, "Results not available yet: " + ", ".join(st["missing"]),
                    "They're fetched again every minute")
    return item("Season", OK, f"{year} season loaded")


# ------------------------------------------------------------------------------------------------ the command
TAGS = {OK: ("ok", "3CB371"), WARN: ("warn", "FFB000"), FAIL: ("fail", "FF5050"), INFO: ("info", None)}


def run(port=8765, network=True):
    from .display import c, dim
    print("\n  F1 Hub doctor\n")
    checks = [check_python()] + check_settings()
    if network:
        checks += check_openf1() + check_streaming()
    checks += check_fastf1() + check_tools() + [check_disk()] + check_processes() + [check_port(port)]
    recent = logs.recent_from_file()
    checks.append(check_log(recent))
    for x in checks:
        word, colour = TAGS[x["status"]]
        tag = c(f"{word:5s}", colour) if colour else dim(f"{word:5s}")
        print(f"  {tag} {x['name']:15s} {x['detail']}")
        if x["fix"]:
            print(dim(f"  {'':5s} {'':15s} -> {x['fix']}"))
    if recent:
        print(dim(f"\n  Latest warnings and errors ({logs.log_file()}):"))
        for r in recent[-5:]:
            print(dim(f"    {r['at']}  {r['level'].lower():7s} {r['message'][:160]}"))
    fails = sum(1 for x in checks if x["status"] == FAIL)
    warns = sum(1 for x in checks if x["status"] == WARN)
    print()
    if fails or warns:
        print(f"  {fails} problem{'s' if fails != 1 else ''}, {warns} warning{'s' if warns != 1 else ''}.\n")
    else:
        print("  Everything looks fine.\n")
    return 1 if fails else 0
