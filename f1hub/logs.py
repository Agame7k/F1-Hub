"""Logging for F1 Hub: short lines in the terminal, full detail (tracebacks too) in a log file.

The file is the cache folder's logs/f1hub.log, rotated at 1 MB with three older copies kept. The latest warnings
and errors are also kept in memory (RECENT) for the dashboard's Diagnostics panel and `python3 -m f1hub doctor`.
"""
import logging
import logging.handlers
import sys
from collections import deque

from . import api

RECENT = deque(maxlen=30)      # {"at", "level", "where", "message"}: the latest warnings and errors
FILE_SIZE = 1_000_000
FILE_COPIES = 3
_ready = False


def log_file():
    return api.CACHE_DIR / "logs" / "f1hub.log"


class _Recent(logging.Handler):
    def emit(self, record):
        try:
            text = record.getMessage().strip().splitlines()
            RECENT.append({"at": record.created, "level": record.levelname, "where": record.name,
                           "message": (text[0] if text else "")[:400] + _exception_line(record)})
        except Exception:
            pass        # logging must never take anything down


def _exception_line(record):
    """': ValueError: bad data' for a logged exception, else ''."""
    if not record.exc_info or not record.exc_info[1]:
        return ""
    e = record.exc_info[1]
    msg = str(e).strip().splitlines()
    return f": {type(e).__name__}" + (f": {msg[0][:200]}" if msg else "")


class _Terminal(logging.Formatter):
    """One line per message; warnings and errors say so. A traceback goes to the log file only."""

    def format(self, record):
        msg = record.getMessage()
        if record.levelno >= logging.WARNING:
            msg = f"{record.levelname.capitalize()}: {msg}{_exception_line(record)}"
            if record.exc_info:
                msg += f"  (details: {log_file()})"
        return msg


def setup(level=logging.INFO, to_file=True):
    """Send f1hub's logging to the terminal (stderr) and the log file. Safe to call more than once."""
    global _ready
    if _ready:
        return
    _ready = True
    root = logging.getLogger("f1hub")
    root.setLevel(level)
    root.propagate = False
    term = logging.StreamHandler(sys.stderr)
    term.setFormatter(_Terminal())
    root.addHandler(term)
    recent = _Recent(logging.WARNING)
    root.addHandler(recent)
    if to_file:
        path = log_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(path, maxBytes=FILE_SIZE, backupCount=FILE_COPIES,
                                                      encoding="utf-8")
        except OSError as e:
            root.warning("Can't write the log file %s (%s) - logging to the terminal only", path, e)
        else:
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            root.addHandler(fh)


def recent_from_file(limit=10):
    """The last warnings and errors in the log file (for `doctor`, which runs in a process of its own)."""
    path = log_file()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-2000:]
    except OSError:
        return []
    out, last = [], None
    for line in lines:
        parts = line.split(" ", 3)        # date time LEVEL logger: message
        if len(parts) == 4 and parts[1][:2].isdigit() and parts[2] in LEVELS:
            last = None
            if parts[2] in ("WARNING", "ERROR", "CRITICAL"):
                where, _, message = parts[3].partition(": ")
                last = {"at": f"{parts[0]} {parts[1].split(',')[0]}", "level": parts[2], "where": where,
                        "message": message.strip()[:400]}
                out.append(last)
        elif last is not None and line[:1].strip() and not line.startswith("Traceback"):
            last["message"] = last["message"].split(" -> ")[0] + " -> " + line.strip()[:200]   # the exception
    return out[-limit:]


LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
