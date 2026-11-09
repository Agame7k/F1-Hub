"""Every World Championship race result since 1950, from Jolpica (the community successor to the Ergast API), for
reliability across the history of the sport: why cars didn't finish, per team, per power unit and per era.

Results are kept one JSON file per season in DATA_DIR/history/ (a season is fetched once it is over and never
again). Jolpica allows about 500 requests an hour; a season is 2-9 requests, so the whole history (about 550
requests) takes a little over an hour the first time, and carries on where it left off if interrupted.
"""
import json
import re
import time
import urllib.error
import urllib.request

from . import dataset
from .api import write_atomic

BASE = "https://api.jolpi.ca/ergast/f1"
FIRST = 1950
SPACING = 7.5            # s between requests: 480 an hour, under Jolpica's 500
UA = "f1hub/1.0 (personal F1 dashboard)"
_last = 0.0


def _dir():
    d = dataset.DATA_DIR / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(url, tries=5):
    global _last
    for attempt in range(tries):
        wait = _last + SPACING - time.time()
        if wait > 0:
            time.sleep(wait)
        _last = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:                       # over the hourly budget: wait it out
                time.sleep(int(e.headers.get("Retry-After") or 300))
                continue
            if e.code >= 500 and attempt < tries - 1:
                time.sleep(10 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, OSError, ValueError):
            if attempt == tries - 1:
                raise
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"Jolpica kept refusing {url}")


def _row(race, res):
    c = res["Constructor"]
    return {"round": int(race["round"]), "race": race["raceName"], "circuit": race["Circuit"]["circuitId"],
            "date": race.get("date"), "driver": res["Driver"]["driverId"],
            "name": f"{res['Driver'].get('givenName', '')} {res['Driver'].get('familyName', '')}".strip(),
            "code": res["Driver"].get("code"), "team": c["constructorId"], "team_name": c["name"],
            "grid": int(res.get("grid") or 0), "position": int(res["position"]) if res.get("position", "").isdigit() else None,
            "text": res.get("positionText"), "laps": int(res.get("laps") or 0), "status": res.get("status") or ""}


def season(year, refresh=False, progress=lambda m: None):
    """[result rows] for a season: from disk, or fetched (and kept, once the season is over)."""
    path = _dir() / f"results_{year}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    rows, offset, total = [], 0, None
    while total is None or offset < total:
        progress(f"{year}: results {offset}-{offset + 100}")
        d = _get(f"{BASE}/{year}/results.json?limit=100&offset={offset}")["MRData"]
        total = int(d["total"])
        for race in d["RaceTable"]["Races"]:
            for res in race["Results"]:
                rows.append(_row(race, res))
        offset += 100
    # a page boundary can split a race: Jolpica repeats the race header, so rows are already flat; de-duplicate
    seen, out = set(), []
    for r in rows:
        k = (r["round"], r["driver"], r["team"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    if year < time.gmtime().tm_year or _season_over(year, out):
        write_atomic(path, json.dumps(out).encode())
    return out


def _season_over(year, rows):
    try:
        n = int(_get(f"{BASE}/{year}.json?limit=1")["MRData"]["total"])
    except Exception:
        return False
    return bool(rows) and max(r["round"] for r in rows) >= n


def fetch_all(first=FIRST, last=None, progress=lambda m: None):
    """Every season from `first` to `last` (default: last year), fetching only what isn't kept yet."""
    last = last or time.gmtime().tm_year - 1
    done = 0
    for y in range(first, last + 1):
        season(y, progress=progress)
        done += 1
    return done


def have():
    """Seasons kept on disk."""
    out = []
    for p in _dir().glob("results_*.json"):
        m = re.match(r"results_(\d{4})\.json", p.name)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)
