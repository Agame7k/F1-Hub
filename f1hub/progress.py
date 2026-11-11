"""Progress reporting for long jobs (compile): phases with a known amount of work, elapsed time and an ETA.

Producers call progress(msg) as before, plus two optional hooks that do nothing for a plain callable:

    progress.begin(progress, "telemetry", 24)          a phase of 24 units starts
    progress.begin(progress, "telemetry", 24, work)    ... and work[i] is how many requests unit i still needs
    progress.done(progress, "R5 Canada - Qualifying", "412 laps")    one unit finished

With the requests known (and a Reporter that can count the requests sent), the ETA is there from the start: a unit
takes overhead + requests x seconds-a-request. Those two figures are what earlier compiles measured (Pace, kept in a
small JSON file), refined by this run's finished units. A request is never counted as quicker than the rate limit
allows, but it is usually slower: they go one after another and a big one (a race's intervals) takes seconds.
With no earlier compile to go on, the overhead is unknown until a unit finishes, so the ETA is a lower bound (">=").

Without them, a message containing "N/M" (e.g. "lap 120/412") tells the reporter how far through the current unit
it is, which gives an estimate before the first unit has finished."""
import json
import os
import re
import shutil
import sys
import threading
import time

_FRACTION = re.compile(r"(\d+)\s*/\s*(\d+)")


def begin(progress, phase, total, work=None):
    f = getattr(progress, "begin", None)
    if f:
        if work is None:
            f(phase, total)
        else:
            f(phase, total, work)


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


def fit(units, s, rate):
    """(overhead s a unit, s a request) from finished units [(requests, seconds)]. s: the request time to assume
    unless the units vary enough in their requests to measure it; never below rate (the rate limit's pace)."""
    n = len(units)
    rs, ts = [r for r, _ in units], [t for _, t in units]
    mr, mt = sum(rs) / n, sum(ts) / n
    var = sum((r - mr) ** 2 for r in rs) / n
    if n >= 3 and var >= 4 and sum(rs) >= 30:           # enough spread in the request counts to fit both
        s = sum((r - mr) * (t - mt) for r, t in zip(rs, ts)) / n / var
    s = max(s, rate)
    return max(mt - s * mr, 0.0), s


class Pace:
    """What earlier compiles measured, per phase: (overhead s a unit, s a request), kept in a JSON file so the next
    compile - and its plan - starts from real figures rather than the rate limit's pace."""
    KEEP = 20          # units of history a new run's units are weighed against

    def __init__(self, path=None, rate=1.0):
        self.path, self.rate = path, rate
        self.data = {}
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    self.data = {k: v for k, v in json.load(f).items() if isinstance(v, dict)}
            except (OSError, ValueError):
                pass

    def get(self, phase):
        """(overhead, s a request) for a phase, or None if no compile has measured it."""
        v = self.data.get(phase)
        try:
            return float(v["o"]), max(float(v["s"]), self.rate)
        except (TypeError, KeyError, ValueError):
            return None

    def estimate(self, phase, units, requests):
        """(seconds for units of work needing these requests, known): known False = the rate limit's pace alone,
        a lower bound."""
        got = self.get(phase)
        if got is None:
            return requests * self.rate, False
        o, s = got
        return units * o + requests * s, True

    def learn(self, phase, units):
        """Fold a run's finished units [(requests, seconds)] into the phase's figures and save them."""
        if not units:
            return
        old = self.get(phase)
        o, s = fit(units, old[1] if old else self.rate, self.rate)
        if old:
            w = min(self.data[phase].get("n", 1), self.KEEP)
            n = len(units)
            o, s = (old[0] * w + o * n) / (w + n), (old[1] * w + s * n) / (w + n)
        self.data[phase] = {"o": round(o, 3), "s": round(s, 3), "n": self.data.get(phase, {}).get("n", 0) + len(units)}
        if self.path:
            try:
                tmp = f"{self.path}.part"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, indent=1)
                os.replace(tmp, self.path)
            except OSError:
                pass


class Phase:
    def __init__(self, name, total, work=None, rate=1.0, requests=None, prior=None):
        """work: requests each unit needs (used only when requests, () -> requests sent so far, can count them).
        rate: s a request takes at the rate limit's pace. prior: (overhead, s a request) from earlier compiles."""
        self.name, self.total = name, total
        self.finished = 0
        self.spent = 0.0                     # seconds spent on the finished units
        self.started = self.unit_started = time.monotonic()
        self.fraction = 0.0                  # how far through the current unit (from an "N/M" message)
        self.work = list(work) if work is not None and requests and len(work) == total else None
        self.rate, self.prior = rate, prior
        self.requests = requests
        self.unit_req0 = requests() if requests else 0
        self.units = []                      # (requests, seconds) of each finished unit

    def finish_unit(self, now):
        took = now - self.unit_started
        sent = self.requests() if self.requests else 0
        self.units.append((sent - self.unit_req0, took))
        self.unit_req0 = sent
        self.spent += took
        self.finished += 1
        self.unit_started, self.fraction = now, 0.0
        return took

    PRIOR_WEIGHT = 2       # units the earlier compiles' overhead counts as, against this run's

    def model(self):
        """(overhead s a unit, s a request): earlier compiles' figures, refined by this run's finished units."""
        o0, s0 = self.prior or (0.0, self.rate)
        if not self.units:
            return o0, max(s0, self.rate)
        o, s = fit(self.units, s0, self.rate)
        if self.prior:
            n = len(self.units)
            o = (o0 * self.PRIOR_WEIGHT + o * n) / (self.PRIOR_WEIGHT + n)
        return o, s

    @property
    def bounded(self):
        """True while the ETA is a lower bound (requests only: the per-unit overhead isn't known yet)."""
        return self.work is not None and not self.units and not self.prior

    def eta(self, now):
        """Seconds left in this phase, or None while there is nothing to estimate from."""
        left = self.total - self.finished
        if left <= 0:
            return 0.0
        in_unit = now - self.unit_started
        if self.work is not None:
            o, s = self.model()
            sent = self.requests() - self.unit_req0
            current = max(self.work[self.finished] - sent, 0) * s + max(o - max(in_unit - sent * s, 0), 0)
            return current + sum(o + s * w for w in self.work[self.finished + 1:])
        # no requests to count (fia.com, Whisper): time a unit, from this run and earlier compiles
        w = self.PRIOR_WEIGHT if self.prior else 0
        if self.finished or w:
            avg = ((self.prior[0] if w else 0) * w + self.spent) / (w + self.finished)
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

    def __init__(self, stream=sys.stderr, style=lambda s: s, tick=1.0, requests=None, pace=None, steps=()):
        """requests: () -> requests sent so far (api.request_count), which lets a phase's ETA count requests;
        pace: a Pace - earlier compiles' figures to start from, and where this run's are kept;
        steps: the whole job's phases in order, [(phase, units, requests or None)] - the status line then shows
        which step of how many is running, and an ETA for the whole job."""
        self.stream, self.style = stream, style
        self.requests, self.pace = requests, pace or Pace()
        self.steps = [st for st in steps if st[1]]
        self.begun = set()
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

    def begin(self, phase, total, work=None):
        with self.lock:
            self._end_phase()
            self.msg = ""
            self.begun.add(phase)
            self.phase = Phase(phase, total, work, self.pace.rate, self.requests, self.pace.get(phase)) if total else None
            if total:
                need = f", {sum(work):,} requests" if work is not None and self.phase.work is not None else ""
                self._line(f"{phase}: {total} to do{need}")

    def done(self, label, detail=""):
        with self.lock:
            p = self.phase
            now = time.monotonic()
            took = ""
            if p:
                took = fmt_duration(p.finish_unit(now))
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
        eta = p.eta(now) if p else None
        if p:
            names = [st[0] for st in self.steps]
            step = f"step {names.index(p.name) + 1}/{len(names)} " if p.name in names else ""
            bits.insert(0, f"{step}{p.name} {min(p.finished + 1, p.total)}/{p.total}")
            bits.append("ETA " + ("estimating" if eta is None else
                                  (">=" if p.bounded else "~") + fmt_duration(eta)))
        total = self.total_eta(now, eta)
        if total:
            left, known = total
            bits.append(f"all {'~' if known else '>='}{fmt_duration(left)}")
        return "[" + " | ".join(bits) + "] " + self.msg

    def total_eta(self, now, eta=None):
        """(seconds left in the whole job, known) - the running step's ETA plus the steps not begun yet, from
        Pace - or None without steps. known is False when any part is only a lower bound or not timed yet."""
        if not self.steps:
            return None
        p = self.phase
        left, known = (eta or 0.0), bool(p is None or (eta is not None and not p.bounded))
        for phase, units, requests in self.steps:
            if phase not in self.begun:
                t, k = self.pace.estimate(phase, units, requests or 0)
                left, known = left + t, known and k
        return left, known

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
        if p:
            self.pace.learn(p.name, p.units)
        if p and p.finished:
            self.phase = None
            self._line(f"{p.name}: {p.finished}/{p.total} in {fmt_duration(time.monotonic() - p.started)}")
        self.phase = None
