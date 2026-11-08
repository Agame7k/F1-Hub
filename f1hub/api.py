"""Tiny OpenF1 client (https://openf1.org) with rate limiting and an on-disk cache.

Free (no key) access covers historical data from 2023 onwards. While a live F1
session is running, OpenF1 restricts ALL free access until it ends; cached data
keeps working through that.

Paid access: set OPENF1_USERNAME + OPENF1_PASSWORD (a token is fetched for you),
or OPENF1_TOKEN if you already have an access token.
"""
import collections
import gzip
import hashlib
import json
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

# Every live (uncached) response is also appended here, so sessions can be replayed and studied later.
# Set F1HUB_RECORD=0 to turn it off.
RECORD_DIR = None if os.environ.get("F1HUB_RECORD") == "0" else \
    Path(os.environ.get("F1HUB_RECORD_DIR", PROJECT_DIR / "recordings"))


def _record(endpoint, url, params, data):
    if RECORD_DIR is None:
        return
    try:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        folder = RECORD_DIR / day
        folder.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"t": time.time(), "endpoint": endpoint, "params": params, "url": url, "data": data})
        with gzip.open(folder / f"{endpoint}.jsonl.gz", "at", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass   # recording must never break live timing
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
offline = False        # True = never hit the network, cache only


def _auth_token():
    user, pw = os.environ.get("OPENF1_USERNAME"), os.environ.get("OPENF1_PASSWORD")
    if user and pw and (not _token["value"] or time.time() > _token["expires"]):
        body = urllib.parse.urlencode({"username": user, "password": pw}).encode()
        req = urllib.request.Request("https://api.openf1.org/token", data=body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                tok = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise APIError(f"OpenF1 login failed (HTTP {e.code}) - check OPENF1_USERNAME/PASSWORD") from None
        _token["value"] = tok["access_token"]
        _token["expires"] = time.time() + int(tok.get("expires_in", 3600)) - 120
    return _token["value"]


def authenticated():
    return bool(os.environ.get("OPENF1_TOKEN") or
                (os.environ.get("OPENF1_USERNAME") and os.environ.get("OPENF1_PASSWORD")))


def _cache_path(url):
    return CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".json")


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


def get(endpoint, ttl=FOREVER, cache=True, **params):
    """GET an endpoint. ttl: seconds a cached copy stays fresh (None = forever).
    cache=False: always hit the network and store nothing (live timing)."""
    url = _build_url(endpoint, params)
    path = _cache_path(url)
    stale = None
    if cache and path.exists():
        cached = json.loads(path.read_text())
        if offline or ttl is FOREVER or time.time() - cached["fetched"] < ttl:
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
                raise LiveSessionLock(
                    "A live F1 session is in progress, so OpenF1 has locked free access until it ends.\n"
                    "Cached data still works - try again after the session, or set OPENF1_USERNAME/OPENF1_PASSWORD.") from None
            raise APIError(f"HTTP {e.code} for {url}: {body[:200]}") from None
        except urllib.error.URLError as e:
            if stale is not None:
                return stale
            raise APIError(f"Network error for {url}: {e.reason}") from None
    else:
        if stale is not None:
            return stale
        raise APIError(f"Gave up after repeated rate-limit/server errors: {url}")

    if isinstance(data, dict) and "detail" in data:
        raise APIError(f"{url}: {data['detail']}")
    if not cache:
        _record(endpoint, url, {k: v for k, v in params.items()}, data)
        return data
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "fetched": time.time(), "data": data}))
    return data


def get_external(url, ttl=24 * 3600):
    """Fetch a JSON URL OpenF1 links to (e.g. meetings.circuit_info_url), cached like everything else."""
    path = _cache_path(url)
    if path.exists():
        cached = json.loads(path.read_text())
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
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "fetched": time.time(), "data": data}))
    return data

