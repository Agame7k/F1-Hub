"""Frees disk space F1 Hub no longer needs.

    python3 -m f1hub cleanup                              show what can go (nothing is deleted)
    python3 -m f1hub cleanup --yes                        delete it
    python3 -m f1hub cleanup --recordings-older-than 60   ...and recordings of sessions over 60 days old

What it looks for:
  - replay windows: replays used to cache the car positions and telemetry for each step of the replay clock
    in a file of its own, which nothing ever read again (now they're fetched by the minute and shared);
  - .part files: writes that never finished (the program was killed part-way);
  - with --recordings-older-than: recorded sessions (OpenF1 and FastF1) older than that.
Everything else in the cache is reused, and is only reported.
"""
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from . import api

PART_AGE = 3600         # s: a .part file this old belongs to a write that never finished
_URL = re.compile(r'^\{"url": "([^"]+)"')
_REPLAY_WINDOW = re.compile(r"/v1/(location|car_data)\?.*\bdate>(?!=)")


def _url_of(path):
    """The URL a cache entry was fetched from (it's the first thing in the file)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            m = _URL.match(f.read(600))
    except OSError:
        return None
    return m.group(1) if m else None


def _size(path):
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _, names in os.walk(path):
        for n in names:
            try:
                total += os.stat(os.path.join(root, n)).st_size
            except OSError:
                pass
    return total


def replay_windows():
    return [p for p in api.CACHE_DIR.glob("*.json") if _REPLAY_WINDOW.search(_url_of(p) or "")]


def unfinished_writes(now=None):
    now = now or time.time()
    out = []
    for root in (api.CACHE_DIR, api.RECORDINGS):
        if root.exists():
            for p in root.rglob("*.part"):
                try:
                    if now - p.stat().st_mtime > PART_AGE:
                        out.append(p)
                except OSError:
                    pass
    return out


def old_recordings(days, now=None):
    """Recordings of sessions that started more than `days` days ago: OpenF1 session folders, the older
    per-day folders, and FastF1 recordings (each with its .log and .json)."""
    cut = (now or time.time()) - days * 86400
    out = []
    root = api.RECORDINGS
    for folder in sorted((root / "openf1").glob("*")) if (root / "openf1").is_dir() else []:
        when = _session_start(folder / "session.json") or folder.stat().st_mtime
        if folder.is_dir() and when < cut:
            out.append(folder)
    for folder in sorted(root.glob("????-??-??")) if root.is_dir() else []:
        try:
            day = datetime.strptime(folder.name, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        if folder.is_dir() and day < cut:
            out.append(folder)
    ff = root / "fastf1"
    for rec in sorted(ff.glob("*.txt")) if ff.is_dir() else []:
        if rec.stat().st_mtime < cut:
            out += [p for p in (rec, rec.with_suffix(".log"), rec.with_suffix(".json")) if p.exists()]
    return out


def _session_start(path):
    import json
    try:
        start = json.loads(path.read_text())["session"]["date_start"]
        return datetime.fromisoformat(start).timestamp()
    except (OSError, ValueError, KeyError, TypeError):
        return None


def plan(recordings_days=None):
    """[(what, [paths], bytes)]: everything cleanup would delete."""
    groups = [("Replay windows in the cache (never reused)", replay_windows()),
              ("Unfinished writes (.part files)", unfinished_writes())]
    if recordings_days is not None:
        groups.append((f"Recordings older than {recordings_days} days", old_recordings(recordings_days)))
    return [(what, paths, sum(_size(p) for p in paths)) for what, paths in groups]


def kept():
    """[(what, bytes)]: the space each part of the cache and the recordings takes."""
    c, r = api.CACHE_DIR, api.RECORDINGS
    known = {c / "fastf1", c / "fia", c / "radio", c / "logs"}
    openf1 = sum(p.stat().st_size for p in c.glob("*.json")) if c.exists() else 0
    rest = [("OpenF1 cache", openf1), ("FastF1 cache", _size(c / "fastf1") if (c / "fastf1").exists() else 0),
            ("FIA documents", _size(c / "fia") if (c / "fia").exists() else 0),
            ("Team radio clips", _size(c / "radio") if (c / "radio").exists() else 0),
            ("Logs", _size(c / "logs") if (c / "logs").exists() else 0),
            ("Recordings", _size(r) if r.exists() else 0)]
    other = [p for p in c.iterdir() if p.is_dir() and p not in known] if c.exists() else []
    if other:
        rest.append(("Other", sum(_size(p) for p in other)))
    return rest


def mb(n):
    return f"{n / 1e6:,.1f} MB"


def delete(paths):
    freed = 0
    for p in paths:
        try:
            size = _size(p)
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            freed += size
        except OSError as e:
            print(f"  couldn't delete {p}: {e}")
    return freed


def run(yes=False, recordings_days=None):
    from .display import dim
    groups = plan(recordings_days)
    total = sum(size for _, _, size in groups)
    print("\n  F1 Hub cleanup" + ("" if yes else dim("  (showing only - add --yes to delete)")) + "\n")
    for what, paths, size in groups:
        print(f"  {what:46s} {len(paths):6d} {'item' if len(paths) == 1 else 'items':5s} {mb(size):>10s}")
    print()
    if not total:
        print("  Nothing to clear.")
    elif yes:
        freed = delete([p for _, paths, _ in groups for p in paths])
        print(f"  Freed {mb(freed)}.")
    else:
        print(f"  {mb(total)} can be freed: python3 -m f1hub cleanup --yes")
    if recordings_days is None:
        print(dim("  Recordings are kept; add --recordings-older-than DAYS to clear old ones too."))
    print(dim("\n  Space used: " + ", ".join(f"{what} {mb(size)}" for what, size in kept() if size)))
    print(dim(f"  Cache: {api.CACHE_DIR}\n  Recordings: {api.RECORDINGS}\n"))
    return 0
