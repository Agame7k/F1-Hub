"""Tiny OpenF1 client (https://openf1.org) with rate limiting and an on-disk cache.

Free (no key) access covers historical data from 2023 onwards. While a live F1
session is running, OpenF1 restricts ALL free access until it ends; cached data
keeps working through that.

Paid access: set OPENF1_USERNAME + OPENF1_PASSWORD (a token is fetched for you),
or OPENF1_TOKEN if you already have an access token.
"""
import collections
import contextlib
import hashlib
import http.client
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api.openf1.org/v1/"
PROJECT_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("F1HUB_CACHE", PROJECT_DIR / ".f1cache"))
log = logging.getLogger("f1hub.api")


def _load_env_file(path=PROJECT_DIR / ".openf1.env"):
    """Read KEY=VALUE lines from .openf1.env; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip().removeprefix("export ").strip(), value.strip().strip("'\""))


_load_env_file()

# Live OpenF1 data is recorded here per session (f1hub/recordings.py); F1HUB_RECORD=0 turns that off.
# FastF1 recordings, which you start yourself, go in RECORDINGS/fastf1 either way.
RECORDINGS = Path(os.environ.get("F1HUB_RECORD_DIR", PROJECT_DIR / "recordings"))
RECORD_DIR = None if os.environ.get("F1HUB_RECORD") == "0" else RECORDINGS


def write_atomic(path, data):
    """Write text or bytes so that nobody ever reads half a file: a temporary file in the same folder, then
    os.replace. (Killed mid-write, a plain write left a truncated cache entry that broke every later run.)"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}-{threading.get_ident()}.part")
    try:
        if isinstance(data, str):
            tmp.write_text(data, encoding="utf-8")
        else:
            tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


RATE_LIMITS = [(6, 1.0), (60, 60.0)]   # OpenF1 REST: 6 requests/second and 60 requests/minute
FOREVER = None


class LiveSessionLock(Exception):
    """OpenF1 blocks free access while a session is live."""


class APIError(Exception):
    pass


_sent = collections.deque()   # timestamps of recent requests


_throttle_lock = threading.Lock()     # feed thread, stream thread and web requests share the budget


def _throttle():
    """Block until another request fits inside every (count, window) rate limit."""
    while True:
        with _throttle_lock:
            wait = _throttle_once()
        if wait is None:
            return
        time.sleep(wait)


def _throttle_once():
    """Record a request and return None if it fits now, else how long to wait. Call with the lock held."""
    now = time.monotonic()
    while _sent and now - _sent[0] > 60.0:
        _sent.popleft()
    waits = []
    for limit, window in RATE_LIMITS:
        recent = [t for t in _sent if now - t < window]
        if len(recent) >= limit:
            waits.append(window - (now - recent[-limit]) + 0.01)
    if not waits:
        _sent.append(now)
        return None
    return max(waits)


_token = {"value": os.environ.get("OPENF1_TOKEN"), "expires": float("inf")}
_login = {"error": None, "at": 0.0}     # the last failed OpenF1 login, if the latest attempt failed
LOGIN_RETRY = 300                       # s before a failed login is tried again (free access meanwhile)
offline = False        # True = never hit the network, cache only


def _auth_token():
    """The OpenF1 access token, or None for free access. A failed login (wrong password, network down) used to
    fail every request - even ones a cached copy could answer - and asked OpenF1 for a token again each time.
    Now it's free access, and the cache, until the login is tried again LOGIN_RETRY seconds later."""
    user, pw = os.environ.get("OPENF1_USERNAME"), os.environ.get("OPENF1_PASSWORD")
    if user and pw and (not _token["value"] or time.time() > _token["expires"]):
        if _login["error"] and time.time() - _login["at"] < LOGIN_RETRY:
            return None
        body = urllib.parse.urlencode({"username": user, "password": pw}).encode()
        req = urllib.request.Request("https://api.openf1.org/token", data=body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                tok = json.loads(r.read().decode())
            value, expires = tok["access_token"], int(tok.get("expires_in", 3600))
        except urllib.error.HTTPError as e:
            with contextlib.closing(e):
                pass
            why = ("OpenF1 turned down OPENF1_USERNAME / OPENF1_PASSWORD" if e.code in (400, 401, 403)
                   else "OpenF1's login answered") + f" (HTTP {e.code})"
            return _login_failed(why)
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:
            return _login_failed(f"couldn't log in to OpenF1 ({getattr(e, 'reason', None) or e})")
        _login.update(error=None, at=time.time())
        _token["value"] = value
        _token["expires"] = time.time() + expires - 120
    return _token["value"]


def _login_failed(why):
    if _login["error"] != why:
        log.warning("%s - using free access, trying again in %d minutes", why, LOGIN_RETRY // 60)
    _login.update(error=why, at=time.time())
    _token["value"] = None
    return None


def login_error():
    """Why the paid OpenF1 login isn't in use right now, or None."""
    return _login["error"]


def authenticated():
    return bool(os.environ.get("OPENF1_TOKEN") or
                (os.environ.get("OPENF1_USERNAME") and os.environ.get("OPENF1_PASSWORD")))


def _cache_path(url):
    return CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".json")


def _read_cached(path):
    """The cached {"url", "fetched", "data"} at path, or None if there is none. A damaged entry counts as missing:
    it's fetched again and replaced."""
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        cached = None
    if isinstance(cached, dict) and "data" in cached and isinstance(cached.get("fetched"), (int, float)):
        return cached
    log.warning("damaged cache entry %s - it will be fetched again", path.name)
    return None


def _write_cached(path, url, data):
    try:
        write_atomic(path, json.dumps({"url": url, "fetched": time.time(), "data": data}))
    except OSError as e:          # disk full, read-only folder...: the data is still good, just not kept
        log.warning("could not cache %s: %s", url, e)


def _build_url(endpoint, params):
    # OpenF1 uses operators inside the key, e.g. {"session_key>=": 9000}
    parts = []
    for key, value in params.items():
        values = value if isinstance(value, (list, tuple)) else [value]
        op = ""
        for symbol in (">=", "<=", ">", "<"):
            if key.endswith(symbol):
                key, op = key[: -len(symbol)], symbol
                break
        for v in values:
            if isinstance(v, bool):
                v = str(v).lower()
            parts.append(urllib.parse.quote(key) + (op or "=") + urllib.parse.quote(str(v)))
    return BASE + endpoint + ("?" + "&".join(parts) if parts else "")


def get(endpoint, ttl=FOREVER, cache=True, fetched_after=None, record=None, **params):
    """GET an endpoint. ttl: seconds a cached copy stays fresh (None = forever).
    fetched_after: epoch seconds - a cached copy saved before then is stale whatever the ttl
    (e.g. one saved while the session was still running).
    cache=False: always hit the network and cache nothing (live timing); record(endpoint, url, params, data)
    then gets each response (recordings.SessionRecording.rest)."""
    url = _build_url(endpoint, params)
    path = _cache_path(url)
    stale = None
    cached = _read_cached(path) if cache else None
    if cached is not None:
        fresh = ttl is FOREVER or time.time() - cached["fetched"] < ttl
        if offline or (fresh and (fetched_after is None or cached["fetched"] >= fetched_after)):
            return cached["data"]
        stale = cached["data"]
    if offline:
        raise APIError(f"Not cached (offline mode): {url}")

    headers = {"Accept": "application/json", "User-Agent": "f1hub/1.0"}
    token = _auth_token()
    if token:
        headers["Authorization"] = "Bearer " + token

    for attempt in range(5):
        _throttle()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                data = json.loads(r.read().decode())
            break
        except urllib.error.HTTPError as e:
            with contextlib.closing(e):
                body = e.read().decode(errors="replace")
            if e.code == 404:          # OpenF1 answers 404 for "no results"
                data = []
                break
            if e.code == 429 or e.code >= 500:
                retry_after = e.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt * 2
                time.sleep(min(delay, 60))
                continue
            if e.code == 401 and "live" in body.lower():
                if stale is not None:
                    return stale
                hint = (f"The paid login isn't working: {login_error()}." if login_error() else
                        "Paid access (OPENF1_USERNAME / OPENF1_PASSWORD in .openf1.env) works during sessions.")
                raise LiveSessionLock(
                    "A live F1 session is in progress, so OpenF1 has locked free access until it ends.\n"
                    f"Cached data still works - try again after the session. {hint}") from None
            raise APIError(f"HTTP {e.code} for {url}: {body[:200]}") from None
        except urllib.error.URLError as e:
            if stale is not None:
                return stale
            raise APIError(f"Network error for {url}: {e.reason}") from None
        except (http.client.HTTPException, ConnectionError, TimeoutError) as e:
            # a response cut off part-way (large telemetry downloads), a reset or a read timeout: try again
            log.warning("retrying %s after %s", url, type(e).__name__)
            time.sleep(2 ** attempt)
            continue
    else:
        if stale is not None:
            return stale
        raise APIError(f"Gave up after repeated rate-limit/server errors: {url}")

    if isinstance(data, dict) and "detail" in data:
        raise APIError(f"{url}: {data['detail']}")
    if not cache:
        if record is not None:
            record(endpoint, url, dict(params), data)
        return data
    _write_cached(path, url, data)
    return data


def get_external(url, ttl=24 * 3600):
    """Fetch a JSON URL OpenF1 links to (e.g. meetings.circuit_info_url), cached like everything else."""
    path = _cache_path(url)
    cached = _read_cached(path)
    if cached is not None:
        if offline or time.time() - cached["fetched"] < ttl:
            return cached["data"]
    if offline:
        raise APIError(f"Not cached (offline mode): {url}")
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "f1hub/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, ValueError) as e:
        raise APIError(f"Could not fetch {url}: {e}") from None
    _write_cached(path, url, data)
    return data

