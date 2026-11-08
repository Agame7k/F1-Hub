"""Find F1's radio intro sting at the start of a team radio clip, so the player can skip it.

F1 prepends the same ~1.7 s sting to nearly every published clip. Each clip is encoded separately, so the files
differ byte for byte, but the decoded audio is sample-identical until the clip's own audio begins - which can be
partway through the sting when the driver starts talking early. A clip is compared to the sting 10 ms at a time;
only the part that matches *exactly* is skipped, minus a safety margin, so nothing that belongs to the clip is cut.
Clips without the sting (a few have none) match for a few milliseconds at most and get no skip.

Needs ffmpeg on the PATH; without it nothing is skipped.
"""
import array
import hashlib
import math
import shutil
import subprocess
import threading
import time
from collections import deque

from .api import write_atomic
from .radio import CACHE, fetch_clip

RATE, FRAME = 16000, 160          # mono 16 kHz, 10 ms frames
TOLERANCE = 0.15                  # RMS difference per frame; identical audio differs only by the rounding below
MIN_MATCH = 30                    # frames (0.3 s): shorter matches are just shared silence, not the sting
MARGIN = 0.06                     # s: a frame that changes at its very end can still match - stay well clear

# RMS of each 10 ms frame of the sting (16 kHz mono, as decoded by ffmpeg), measured from cached 2026 clips
STING = (
    377.6, 342.9, 343.2, 367.3, 304.5, 355.0, 334.9, 276.5, 285.1, 321.7, 341.4, 276.4, 315.0, 275.3, 347.8, 277.6,
    259.2, 284.0, 284.6, 274.6, 320.0, 240.8, 293.7, 258.6, 259.1, 268.0, 225.8, 283.5, 260.6, 267.0, 270.7, 281.2,
    283.4, 277.5, 258.0, 272.0, 258.6, 274.3, 255.2, 276.3, 308.1, 358.4, 295.4, 350.6, 277.5, 283.1, 289.9, 368.3,
    298.3, 349.4, 369.9, 311.6, 298.5, 322.0, 341.1, 322.0, 306.5, 314.1, 355.3, 319.8, 323.6, 284.7, 340.2, 343.4,
    352.4, 315.2, 338.6, 338.4, 332.7, 337.9, 369.5, 357.3, 366.9, 423.7, 379.9, 371.0, 300.4, 324.2, 314.5, 299.8,
    347.0, 330.8, 388.3, 392.4, 371.9, 364.3, 369.9, 329.1, 322.2, 290.5, 392.2, 473.6, 312.8, 320.5, 339.1, 316.7,
    301.7, 286.5, 272.7, 296.4, 289.9, 328.5, 353.0, 330.0, 303.0, 310.7, 332.6, 344.5, 329.0, 334.3, 334.8, 406.1,
    348.4, 320.6, 338.1, 312.2, 327.3, 305.2, 338.3, 319.5, 315.1, 263.1, 330.3, 297.1, 307.7, 355.0, 355.5, 330.0,
    360.1, 383.8, 332.4, 365.3, 382.0, 355.1, 339.3, 350.2, 405.8, 353.5, 353.7, 355.5, 376.2, 315.4, 373.7, 351.4,
    392.8, 374.8, 397.7, 342.4, 382.6, 367.3, 382.4, 338.2, 359.5, 378.1, 383.4, 375.6, 374.4, 433.8, 388.5, 451.5,
    390.4, 426.5, 403.3, 361.1, 424.2, 358.9, 414.9, 389.2
)


def _rms(pcm):
    return [math.sqrt(sum(x * x for x in pcm[i:i + FRAME]) / FRAME) for i in range(0, len(pcm) - FRAME + 1, FRAME)]


def intro_seconds(mp3):
    """Seconds of F1's sting at the start of this clip that can be skipped (0 if it has none)."""
    if not shutil.which("ffmpeg"):
        return 0.0
    out = subprocess.run(["ffmpeg", "-v", "quiet", "-i", "pipe:0", "-t", "2.5", "-ac", "1", "-ar", str(RATE),
                          "-f", "s16le", "pipe:1"], input=mp3, capture_output=True, timeout=30).stdout
    env = _rms(array.array("h", out[:len(out) // 2 * 2]))
    k = 0
    while k < len(STING) and k < len(env) and abs(env[k] - STING[k]) <= TOLERANCE:
        k += 1
    return round(max(0.0, k * FRAME / RATE - MARGIN), 2) if k >= MIN_MATCH else 0.0


class IntroFinder:
    """Works out the skip for each clip in the background; results are kept next to the cached clip."""

    def __init__(self):
        self.skip = {}                # url -> seconds
        self.failed = {}              # url -> when it last failed (retried after a minute)
        self.queue, self.queued = deque(), set()
        self.lock, self.wake = threading.Lock(), threading.Event()
        self.enabled = bool(shutil.which("ffmpeg"))
        if self.enabled:
            threading.Thread(target=self._run, daemon=True, name="radio-intro").start()

    def _file(self, url):
        return CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".intro")

    def get(self, url):
        if url in self.skip:
            return self.skip[url]
        f = self._file(url)
        if f.exists():
            try:
                self.skip[url] = float(f.read_text())
                return self.skip[url]
            except ValueError:
                pass
        return None

    def request(self, urls):
        """Queue clips (oldest first in the list -> the newest is checked first)."""
        if not self.enabled:
            return
        with self.lock:
            for u in urls:
                if u and u not in self.queued and self.get(u) is None and time.time() - self.failed.get(u, 0) > 60:
                    self.queued.add(u)
                    self.queue.append(u)
        self.wake.set()

    def _run(self):
        while True:
            self.wake.wait()
            with self.lock:
                url = self.queue.pop() if self.queue else None
                if url is None:
                    self.wake.clear()
                    continue
            try:
                s = intro_seconds(fetch_clip(url))
                write_atomic(self._file(url), str(s))
                self.skip[url] = s
            except Exception:
                self.failed[url] = time.time()     # network blip or not published yet: try again later
            with self.lock:
                self.queued.discard(url)
