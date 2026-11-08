"""Makes sense of OpenF1 race_control messages: categories, sector flags, stewarding, timeline."""
import re
from datetime import datetime, timedelta, timezone

CAR_RE = re.compile(r"\b(\d{1,2}) \(([A-Z]{3})\)")
TIME_RE = re.compile(r"\((\d{2}:\d{2}:\d{2})\)")
RESUME_AT_RE = re.compile(r"(?:RESUME|RESTART|START)\w*\s+(?:AT|TIME:?)\s+(\d{1,2})[:.](\d{2})")
NUMS_RE = re.compile(r"\b\d{1,2}\b")
RED_FLAG_RE = re.compile(r"\bRED FLAG\b")       # word boundary: "CHEQUERED FLAG" contains "RED FLAG"!


def is_red_flag(m):
    return m.get("flag") == "RED" or bool(RED_FLAG_RE.search((m.get("message") or "").upper()))

# group -> kinds, used by the GUI filter buttons
GROUPS = {
    "flags": ("red", "sector", "blue", "green", "chequered", "black_white", "black_orange", "track_clear"),
    "safety": ("sc", "vsc", "sc_end", "incident"),
    "stewards": ("penalty", "investigation", "decision", "track_limits", "lap_deleted", "reprimand"),
    "session": ("session", "overtake_mode", "info", "pit", "weather", "start"),
}


def cars_in(message):
    """Car numbers mentioned in a message, e.g. 'CARS 1 (VER) AND 4 (NOR)' -> [1, 4]."""
    return [int(n) for n, _ in CAR_RE.findall(message or "")]


def classify(m):
    msg = (m.get("message") or "").upper()
    flag, cat, scope = m.get("flag"), m.get("category"), m.get("scope")
    if cat == "SessionStatus":
        return "session"
    if is_red_flag(m):
        return "red"
    if "VIRTUAL SAFETY CAR" in msg or re.search(r"\bVSC\b", msg):
        return "sc_end" if ("ENDING" in msg or "END" in msg.split()) else "vsc"
    if cat == "SafetyCar" or "SAFETY CAR" in msg:
        return "sc_end" if ("IN THIS LAP" in msg or "ENDING" in msg) else "sc"
    if flag == "CHEQUERED":
        return "chequered"
    if flag == "BLACK AND WHITE":
        return "black_white"
    if flag == "BLACK AND ORANGE":
        return "black_orange"
    if flag == "BLUE":
        return "blue"
    if scope == "Sector" and flag in ("YELLOW", "DOUBLE YELLOW", "CLEAR"):
        return "sector"
    if flag == "GREEN":
        return "green"
    if flag == "CLEAR" or "TRACK CLEAR" in msg:
        return "track_clear"
    if "PENALTY" in msg and "NO FURTHER" not in msg and "WITHOUT" not in msg:
        return "penalty"
    if "REPRIMAND" in msg:
        return "reprimand"
    if "NO FURTHER" in msg or "NO INVESTIGATION" in msg or "NOT NECESSARY" in msg:
        return "decision"
    if "INVESTIGATION" in msg or "NOTED" in msg or "REVIEWED" in msg or "SUMMONED" in msg:
        return "investigation"
    if "DELETED" in msg:
        return "lap_deleted"
    if "TRACK LIMITS" in msg:
        return "track_limits"
    if "OVERTAKE" in msg or "DRS" in msg:
        return "overtake_mode"
    if "MARSHALS" in msg or "MEDICAL CAR" in msg or "RECOVERY VEHICLE" in msg or "DEBRIS" in msg:
        return "incident"
    if "PIT EXIT" in msg or "PIT ENTRY" in msg or "PIT LANE" in msg:
        return "pit"
    if "RAIN" in msg or "WET" in msg:
        return "weather"
    if "FORMATION" in msg or "START" in msg or "RESUMPTION" in msg or "PUSH CARS" in msg:
        return "start"
    return "info"


def group_of(kind):
    return next((g for g, kinds in GROUPS.items() if kind in kinds), "session")


class RaceControl:
    """Feed it messages in time order; ask it for state."""

    def __init__(self, gmt_offset=None):
        self.gmt_offset = _parse_offset(gmt_offset)   # local track time -> UTC (messages quote local times)
        self.red = None             # current / last red flag, see _red_flag()
        self.red_history = []
        self.messages = []          # enriched dicts
        self.sector_flags = {}      # sector -> "YELLOW" / "DOUBLE YELLOW"
        self.sector_cleared = {}    # sector -> date it last went green
        self.max_sector = 0
        self.per_driver = {}        # number -> tallies + list of stewarding items
        self.periods = []           # neutralisations: {"kind", "start_lap", "end_lap", "start", "end"}
        self.overtake_mode = None   # True / False / None (unknown)
        self.counts = {}

    def _driver(self, n):
        return self.per_driver.setdefault(n, {"track_limits": 0, "lap_deleted": 0, "black_white": 0,
                                              "blue": 0, "investigations": 0, "penalties": 0, "items": []})

    def add(self, m):
        kind = classify(m)
        msg = m.get("message") or ""
        cars = cars_in(msg)
        if m.get("driver_number") and m["driver_number"] not in cars:
            cars.insert(0, m["driver_number"])
        rec = {"date": m["date"], "time": m["date"][11:19], "lap": m.get("lap_number"), "kind": kind,
               "group": group_of(kind), "flag": m.get("flag"), "sector": m.get("sector"), "text": msg, "cars": cars}
        self.messages.append(rec)
        self.counts[kind] = self.counts.get(kind, 0) + 1

        if kind == "sector" and m.get("sector") is not None:
            s = m["sector"]
            self.max_sector = max(self.max_sector, s)
            if m.get("flag") == "CLEAR":
                self.sector_flags.pop(s, None)
                self.sector_cleared[s] = m["date"]
            else:
                self.sector_flags[s] = m["flag"]
        elif kind in ("track_clear", "green") and m.get("scope") in (None, "Track"):
            self.sector_flags.clear()
        elif kind in ("sc", "vsc", "red"):
            if not self.periods or self.periods[-1]["end"] is not None or self.periods[-1]["kind"] != kind:
                if self.periods and self.periods[-1]["end"] is None:      # e.g. SC -> red flag
                    self.periods[-1]["end"], self.periods[-1]["end_lap"] = m["date"], m.get("lap_number")
                self.periods.append({"kind": kind, "start": m["date"], "start_lap": m.get("lap_number"),
                                     "end": None, "end_lap": None})
        elif kind in ("sc_end", "green", "track_clear") or (kind == "session" and "STARTED" in msg.upper()):
            if self.periods and self.periods[-1]["end"] is None and kind != "track_clear":
                self.periods[-1]["end"], self.periods[-1]["end_lap"] = m["date"], m.get("lap_number")
        if kind == "overtake_mode":
            up = msg.upper()
            if "ENABLED" in up:
                self.overtake_mode = True
            elif "DISABLED" in up:
                self.overtake_mode = False

        self._red_flag(m, kind, msg.upper())

        tally = {"track_limits": "track_limits", "lap_deleted": "lap_deleted", "black_white": "black_white",
                 "blue": "blue", "investigation": "investigations", "penalty": "penalties"}
        for n in cars:
            d = self._driver(n)
            if kind in tally:
                d[tally[kind]] += 1
            if kind in ("penalty", "investigation", "decision", "reprimand", "black_white", "black_orange",
                        "lap_deleted", "track_limits"):
                d["items"].append({"lap": rec["lap"], "time": rec["time"], "kind": kind, "text": msg, "cars": cars})
        return rec

    def _red_flag(self, m, kind, up):
        """Tracks a stoppage from RED FLAG to restart (or 'will not be resumed')."""
        lap, date = m.get("lap_number"), m["date"]
        if kind == "red" and (self.red is None or self.red["state"] != "suspended"):
            self.red = {"state": "suspended", "since": date, "lap": lap, "resume_at": None, "resume_local": None,
                        "order": [], "push_to_front": [], "start_type": None, "signals": [], "pit_exit": None,
                        "resumed_at": None, "resumed_lap": None, "duration_s": None}
            self.red_history.append(self.red)
            return
        r = self.red
        if not r or r["state"] != "suspended":
            return
        if "NOT BE RESUMED" in up or "WILL NOT RESUME" in up or "NOT BE RESTARTED" in up:
            r["state"] = "abandoned"
            r["signals"].append({"time": date[11:19], "text": m.get("message")})
            return
        hit = RESUME_AT_RE.search(up)
        if hit and self.gmt_offset is not None:
            hh, mm = int(hit.group(1)), int(hit.group(2))
            since = datetime.fromisoformat(r["since"])
            local = since + self.gmt_offset
            at_local = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if at_local < local - timedelta(hours=1):
                at_local += timedelta(days=1)
            r["resume_at"] = (at_local - self.gmt_offset).astimezone(timezone.utc).isoformat()
            r["resume_local"] = f"{hh:02d}:{mm:02d}"
        if "RESUMPTION ORDER" in up or "RESTART ORDER" in up:
            r["order"] = [int(x) for x in NUMS_RE.findall(up.split(":", 1)[-1])]
        if "PUSH CARS" in up:
            r["push_to_front"] = [int(x) for x in NUMS_RE.findall(up.split(":", 1)[-1])]
        if "STANDING START" in up:
            r["start_type"] = "Standing start"
        elif "BEHIND THE SAFETY CAR" in up or "SAFETY CAR START" in up or "ROLLING START" in up:
            r["start_type"] = "Behind the Safety Car"
        if "PIT EXIT" in up:
            r["pit_exit"] = "open" if "OPEN" in up else "closed" if "CLOSED" in up else r["pit_exit"]
        if re.search(r"\b\d+ MINUTE", up) or "SIGNAL" in up or "FORMATION" in up or "START PROCEDURE" in up \
                or hit or r["start_type"] and ("START" in up):
            r["signals"].append({"time": date[11:19], "text": m.get("message")})
        resumed = (kind == "session" and ("STARTED" in up or "RESUMED" in up)) or \
                  ("RACE RESUMED" in up or "SESSION RESUMED" in up) or (kind == "green" and m.get("scope") == "Track")
        if resumed:
            r["state"] = "resumed"
            r["resumed_at"], r["resumed_lap"] = date, lap
            r["duration_s"] = round((datetime.fromisoformat(date) - datetime.fromisoformat(r["since"])).total_seconds())

    def stewards(self):
        """Every stewarding message, newest first."""
        return [m for m in reversed(self.messages) if m["group"] == "stewards" or m["kind"] in ("black_white",)]

    def snapshot(self, code_of):
        drivers = []
        for n, d in self.per_driver.items():
            if any(d[k] for k in ("track_limits", "lap_deleted", "black_white", "investigations", "penalties")) \
                    or d["blue"]:
                drivers.append(dict(d, number=n, code=code_of(n), items=d["items"][-12:]))
        drivers.sort(key=lambda d: (-d["penalties"], -d["investigations"], -(d["track_limits"] + d["lap_deleted"])))
        return {
            "messages": [dict(m, codes=[code_of(n) for n in m["cars"]]) for m in reversed(self.messages[-300:])],
            "sector_flags": {str(k): v for k, v in sorted(self.sector_flags.items())},
            "sector_cleared": {str(k): v for k, v in self.sector_cleared.items()},
            "sectors": self.max_sector,
            "drivers": drivers,
            "periods": self.periods,
            "overtake_mode": self.overtake_mode,
            "counts": self.counts,
            "stewards": [dict(m, codes=[code_of(n) for n in m["cars"]]) for m in self.stewards()[:60]],
            "red_flag": dict(self.red, order_codes=[code_of(n) for n in self.red["order"]],
                             push_codes=[code_of(n) for n in self.red["push_to_front"]]) if self.red else None,
            "red_flags_total": len(self.red_history),
        }


def _parse_offset(s):
    if not s:
        return None
    sign = -1 if s.startswith("-") else 1
    h, m, *_ = (int(x) for x in s.lstrip("+-").split(":"))
    return sign * timedelta(hours=h, minutes=m)
