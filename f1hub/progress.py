"""Progress reporting for long jobs (compile): phases with a known amount of work, elapsed time and an ETA.

Producers call progress(msg) as before, plus two optional hooks that do nothing for a plain callable:

    progress.begin(progress, "telemetry", 24)          a phase of 24 units starts
    progress.done(progress, "R5 Canada - Qualifying", "412 laps")    one unit finished

A message containing "N/M" (e.g. "lap 120/412") tells the reporter how far through the current unit it is, which
gives an estimate before the first unit has finished."""
import re
import shutil
import sys
import threading
import time

_FRACTION = re.compile(r"(\d+)\s*/\s*(\d+)")


def begin(progress, phase, total):
    f = getattr(progress, "begin", None)
    if f:
        f(phase, total)


def done(progress, label, detail=""):
    f = getattr(progress, "done", None)
    if f:
        f(label, detail)


def fmt_duration(s):
    s = int(round(max(0, s)))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{s % 3600 // 60:02d}m"


class Phase:
    def __init__(self, name, total):
        self.name, self.total = name, total
        self.finished = 0
        self.spent = 0.0                     # seconds spent on the finished units
        self.started = self.unit_started = time.monotonic()
        self.fraction = 0.0                  # how far through the current unit (from an "N/M" message)

    def eta(self, now):
        """Seconds left in this phase, or None while there is nothing to estimate from."""
        left = self.total - self.finished
        if left <= 0:
            return 0.0
        in_unit = now - self.unit_started
        if self.finished:
            avg = self.spent / self.finished
            if self.fraction >= 0.05:        # blend the average with what the current unit is showing
                avg = (avg + in_unit / self.fraction) / 2
        elif self.fraction >= 0.05:
            avg = in_unit / self.fraction
        else:
            return None
        return max(avg - in_unit, 0) + avg * (left - 1)


class Reporter:
    """Renders progress to a terminal: a live status line (redrawn every second, so the clock keeps moving through
    a long step) and a permanent line per finished unit. Off a terminal, only the permanent lines are written."""

    def __init__(self, stream=sys.stderr, style=lambda s: s, tick=1.0):
        self.stream, self.style = stream, style
        self.live = stream.isatty()
        self.t0 = time.monotonic()
        self.phase = None
        self.msg = ""
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._ticker = None
        if self.live and tick:
            self._ticker = threading.Thread(target=self._tick, args=(tick,), daemon=True)
            self._ticker.start()

    # -- the producer's side
    def __call__(self, msg):
        with self.lock:
            self.msg = msg
            if self.phase:
                m = None
                for m in _FRACTION.finditer(msg):
                    pass
                if m and int(m.group(2)):
                    self.phase.fraction = min(int(m.group(1)) / int(m.group(2)), 1.0)
            self._draw()

    def begin(self, phase, total):
        with self.lock:
            self._end_phase()
            self.msg = ""
            self.phase = Phase(phase, total) if total else None
            if total:
                self._line(f"{phase}: {total} to do")

    def done(self, label, detail=""):
        with self.lock:
            p = self.phase
            now = time.monotonic()
            took = ""
            if p:
                took = fmt_duration(now - p.unit_started)
                p.spent += now - p.unit_started
                p.finished += 1
                p.unit_started, p.fraction = now, 0.0
                count = f"{p.name} {p.finished}/{p.total}"
            else:
                count = ""
            self.msg = ""
            parts = [x for x in (count, label, detail, took and f"({took})") if x]
            self._line("  ".join(parts))

    def log(self, text):
        with self.lock:
            self._line(text)

    def close(self):
        self._stop.set()
        if self._ticker:
            self._ticker.join(timeout=2)
        with self.lock:
            self._end_phase()
            self._clear()

    # -- rendering
    def status(self, now=None):
        now = now or time.monotonic()
        bits = [fmt_duration(now - self.t0)]
        p = self.phase
        if p:
            eta = p.eta(now)
            bits.insert(0, f"{p.name} {min(p.finished + 1, p.total)}/{p.total}")
            bits.append("ETA " + (f"~{fmt_duration(eta)}" if eta is not None else "estimating"))
        return "[" + " | ".join(bits) + "] " + self.msg

    def _tick(self, every):
        while not self._stop.wait(every):
            with self.lock:
                self._draw()

    def _width(self):
        return max(20, shutil.get_terminal_size((100, 20)).columns - 1)

    def _draw(self):
        if self.live:
            w = self._width()
            self.stream.write("\r" + self.style(("  " + self.status())[:w].ljust(w)))
            self.stream.flush()

    def _clear(self):
        if self.live:
            self.stream.write("\r" + " " * self._width() + "\r")
            self.stream.flush()

    def _line(self, text):
        self._clear()
        self.stream.write("  " + text + "\n")
        self.stream.flush()
        self._draw()

    def _end_phase(self):
        p = self.phase
        if p and p.finished:
            self.phase = None
            self._line(f"{p.name}: {p.finished}/{p.total} in {fmt_duration(time.monotonic() - p.started)}")
        self.phase = None
