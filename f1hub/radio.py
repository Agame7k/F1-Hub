"""Team radio downloads: single clips and a zip of a whole session (optionally one driver).

Clips live on livetiming.formula1.com; only URLs there are fetched, so the endpoint can't be used
to pull arbitrary files. Downloaded clips are cached on disk - they never change.
"""
import hashlib
import io
import re
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote, urlparse

from .api import CACHE_DIR, write_atomic

CACHE = CACHE_DIR / "radio"           # clips, their transcripts (.json) and intro lengths (.intro)
ALLOWED_HOST = "livetiming.formula1.com"


def _check(url):
    u = urlparse(url)
    if u.scheme != "https" or u.hostname != ALLOWED_HOST or "/TeamRadio/" not in u.path or not u.path.endswith(".mp3"):
        raise ValueError("not an F1 team radio clip")


def fetch_clip(url):
    _check(url)
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".mp3")
    if f.exists():
        return f.read_bytes()
    # folder names carry the event name ("2026-11-08_São_Paulo_Grand_Prix"): percent-encode it for the request,
    # but keep the original URL as the cache key so transcripts and downloads still line up
    u = urlparse(url)
    req = urllib.request.Request(u._replace(path=quote(u.path, safe="/%")).geturl(), headers={"User-Agent": "f1hub"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    write_atomic(f, data)
    return data


def clip_name(url, lap=None, meeting=None):
    """VER_3_20261101_162901.mp3 -> Mexico_City_Grand_Prix_L71_VER_162901.mp3"""
    base = Path(urlparse(url).path).name
    m = re.match(r"([A-Z]+)_\d+_\d{8}_(\d{6})\.mp3", base)
    race = meeting or (urlparse(url).path.split("/")[3].split("_", 1)[-1] if url.count("/") > 5 else "")
    if not m:
        return base
    return "_".join(x for x in (race, f"L{lap}" if lap and str(lap) != "None" else "", m.group(1), m.group(2)) if x) + ".mp3"


def session_zip(snap, driver=None):
    radio = [r for r in snap.get("radio") or [] if r.get("url")]
    if driver:
        radio = [r for r in radio if str(r.get("number")) == str(driver) or r.get("code") == driver]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:     # mp3 doesn't compress
        for r in reversed(radio):                                 # oldest first
            try:
                z.writestr(clip_name(r["url"], r.get("lap")), fetch_clip(r["url"]))
            except Exception:
                continue
    meeting = re.sub(r"\W+", "_", snap.get("meeting") or "session").strip("_")
    who = f"_{radio[0]['code']}" if driver and radio else ""
    return buf.getvalue(), f"{meeting}_{snap.get('session', '')}{who}_team_radio.zip".replace(" ", "_")
