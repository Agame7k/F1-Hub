"""Team radio transcripts: a background queue in front of the Whisper worker (f1hub/stt_worker.py).

Newest clips are transcribed first (that's what you're listening to live); results are cached on disk
next to the downloaded clips, so each clip is only ever transcribed once. If the .venv with
faster-whisper isn't set up, everything here quietly reports "unavailable" and the app carries on.
"""
import hashlib
import json
import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from .radio import CACHE, fetch_clip

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / ".venv" / "bin" / "python"
WORKER = Path(__file__).resolve().parent / "stt_worker.py"


def _key(url):
    return hashlib.sha1(url.encode()).hexdigest()


RETRY_AFTER = 120        # seconds before a clip that failed is tried again

class Transcriber:
    def __init__(self):
        self.texts = {}             # url -> {"text", "segments"} or {"error"}
        self.queue = deque()        # urls; newest appended last, popped first
        self.queued = set()
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.proc = None
        self.model = None
        self.state = "idle" if VENV_PY.exists() else "unavailable"
        self.error = None if VENV_PY.exists() else "speech-to-text isn't installed (see README: .venv + faster-whisper)"
        self.done = 0
        self.busy_with = None
        if self.state != "unavailable":
            threading.Thread(target=self._run, daemon=True, name="radio-stt").start()

    # ---------------------------------------------------------------- called from the feed / web threads
    def get(self, url):
        hit = self.texts.get(url)
        if hit and "error" in hit and time.time() - hit.get("at", 0) > RETRY_AFTER:
            self.texts.pop(url, None)        # a failed clip gets another go (network blip, F1 not published yet...)
            hit = None
        if hit is None:
            f = CACHE / (_key(url) + ".json")
            if f.exists():
                try:
                    hit = self.texts[url] = json.loads(f.read_text())
                except ValueError:
                    hit = None
        return hit

    def request(self, urls):
        """Queue clips (oldest first in the list -> the newest is transcribed first)."""
        if self.state == "unavailable":
            return
        added = False
        with self.lock:
            for u in urls:
                if u and u not in self.queued and self.get(u) is None:
                    self.queued.add(u)
                    self.queue.append(u)
                    added = True
        if added:
            self.wake.set()

    def status(self):
        return {"state": self.state, "model": self.model, "queued": len(self.queue), "done": self.done,
                "error": self.error}

    # ---------------------------------------------------------------- worker side
    def _start(self):
        env = dict(os.environ, PYTHONNOUSERSITE="1")
        self.proc = subprocess.Popen([str(VENV_PY), "-I", str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
        self.state = "loading model"
        hello = json.loads(self.proc.stdout.readline() or "{}")     # first run downloads the model (~250 MB)
        if not hello.get("ready"):
            raise RuntimeError("speech-to-text worker failed to start")
        self.model = hello.get("model")
        self.state = "ready"

    def _run(self):
        while True:
            self.wake.wait(5)
            self.wake.clear()
            while True:
                with self.lock:
                    url = self.queue.pop() if self.queue else None
                if url is None:
                    break
                path = CACHE / (_key(url) + ".mp3")
                try:
                    if not path.exists():
                        fetch_clip(url)
                except Exception as e:          # the download failed: the Whisper worker is fine, keep it running
                    self.error = f"Couldn't download the clip - {type(e).__name__}: {e}"
                    self.texts[url] = {"error": self.error, "at": time.time()}
                    with self.lock:
                        self.queued.discard(url)
                    continue
                try:
                    if self.proc is None or self.proc.poll() is not None:
                        self._start()
                    self.state, self.busy_with = "transcribing", url
                    self.proc.stdin.write(json.dumps({"id": url, "path": str(path)}) + "\n")
                    self.proc.stdin.flush()
                    res = json.loads(self.proc.stdout.readline() or "{}")
                    if res.get("id") != url:
                        raise RuntimeError("worker out of step")
                    res.pop("id", None)
                    self.texts[url] = res
                    if "error" not in res:
                        (CACHE / (_key(url) + ".json")).write_text(json.dumps(res))
                    self.done += 1
                    self.error = None
                except Exception as e:
                    self.error = f"{type(e).__name__}: {e}"
                    self.texts[url] = {"error": self.error, "at": time.time()}
                    if self.proc:
                        self.proc.kill()
                        self.proc = None
                    time.sleep(2)
                finally:
                    with self.lock:
                        self.queued.discard(url)
                    self.busy_with = None
                    if self.state == "transcribing":
                        self.state = "ready"
