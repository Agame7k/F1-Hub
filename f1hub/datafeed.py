"""The data feed for other programs: GET /api/feed (Server-Sent Events), for overlays, a Stream Deck, a second screen.

It sends the same picture the dashboard shows - live, delayed to match your TV, or a replay on its virtual clock -
split into channels. A client asks for the ones it wants (?channels=leaderboard,race_control) and gets an event when
one of them changes, never the same payload twice in a row. docs/feed.md describes each channel.
"""
import json

CHANNELS = ("session", "leaderboard", "positions", "race_control", "weather", "track")
MAX_CHANNELS = 32


class BadChannels(ValueError):
    pass


def parse_channels(text):
    """'leaderboard,positions.VER' -> {'leaderboard', 'positions.VER'}; nothing asked for = every channel.
    A per-driver channel is positions.<CODE> (three letters, as on the timing tower) or positions.<car number>."""
    if not text or not text.strip():
        return set(CHANNELS)
    out = set()
    for part in text.split(","):
        c = part.strip()
        if not c:
            continue
        base, dot, who = c.partition(".")
        driver_ok = (who.isdigit() and len(who) <= 3) or (who.isalpha() and len(who) == 3)
        if base not in CHANNELS or (dot and not (base == "positions" and driver_ok)):
            raise BadChannels(f"unknown channel {c!r} - the channels are {', '.join(CHANNELS)}, and positions.<CODE>")
        out.add(base + ("." + who.upper() if who else ""))
    if len(out) > MAX_CHANNELS:
        raise BadChannels(f"at most {MAX_CHANNELS} channels")
    return out


def _session(f):
    r = f.get("replay") or {}
    return {"mode": f.get("mode") or (f.get("settings") or {}).get("mode"), "session_key": f.get("session_key"),
            "meeting": f.get("meeting"), "circuit": f.get("circuit"), "session": f.get("session"),
            "status": f.get("status"), "lap": f.get("lap"), "scheduled_laps": f.get("scheduled_laps"),
            "delayed_by": f.get("delayed_by"),
            "replay": {"playing": r.get("playing"), "speed": r.get("speed")} if r else None}


def _leaderboard(f):
    keep = ("pos", "number", "code", "name", "team", "colour", "gap", "interval", "lap", "last_lap", "best_lap",
            "fastest", "compound", "tyre_age", "pits", "grid")
    return [dict({k: r.get(k) for k in keep}, out=(r["out"] or {}).get("reason") if r.get("out") else None)
            for r in f.get("rows") or []]


def _positions(f):
    """Each car's newest position on the track map, in OpenF1's units (tenths of a metre), with when it was there."""
    out = []
    for c in (f.get("map") or {}).get("cars") or []:
        trail = c.get("trail") or []
        out.append({"number": c.get("number"), "code": c.get("code"), "pos": c.get("pos"), "colour": c.get("colour"),
                    "x": c.get("x"), "y": c.get("y"), "t": trail[-1][0] if trail else None})
    return out


def _track(f):
    rc = f.get("rc") or {}
    return {"status": f.get("status"), "sector_flags": rc.get("sector_flags") or {}}


def snapshot(frame):
    """Every channel's payload for one dashboard frame (feed.current())."""
    return {"session": _session(frame), "leaderboard": _leaderboard(frame), "positions": _positions(frame),
            "weather": frame.get("weather"), "track": _track(frame)}


class Subscriber:
    """One connected client: turns each new frame into the events it hasn't had yet."""

    def __init__(self, channels):
        self.channels = channels
        self.sent = {}              # channel -> the JSON last sent on it
        self.seen_rc = set()        # race-control messages already sent (time + text)
        self.where = None           # (session, seek): a new session or a seek starts race control over

    def events(self, frame):
        """[(channel, json text)] for this frame: the channels that changed, and each new race-control message."""
        if "rows" not in frame:
            return []               # still starting up / an error: nothing to tell yet
        out = []
        where = (frame.get("session_key"), frame.get("seek"))
        if where != self.where:
            self.where, self.seen_rc = where, set()
        snap = snapshot(frame)
        per_driver = [c for c in self.channels if c.startswith("positions.")]
        for name in CHANNELS:
            if name == "race_control":
                continue
            if name in self.channels:
                self._emit(out, name, snap[name])
        for c in sorted(per_driver):
            who = c.split(".", 1)[1]
            car = next((p for p in snap["positions"] if str(p["number"]) == who or (p["code"] or "").upper() == who), None)
            if car:
                self._emit(out, c, car)
        if "race_control" in self.channels:
            for m in reversed(frame.get("messages") or []):        # the frame lists them newest first
                key = (m.get("time"), m.get("text"))
                if key not in self.seen_rc:
                    self.seen_rc.add(key)
                    out.append(("race_control", json.dumps(m, separators=(",", ":"))))
        return out

    def _emit(self, out, name, payload):
        text = json.dumps(payload, separators=(",", ":"))
        if self.sent.get(name) != text:
            self.sent[name] = text
            out.append((name, text))


def encode(channel, text):
    """One Server-Sent Event."""
    return f"event: {channel}\ndata: {text}\n\n".encode()
