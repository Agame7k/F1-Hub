"""Race control and radio (phase 7 of docs/roadmap.md), for the dashboard (Explore > Stewards, radio & reliability)
and the `stewards`, `radio` and `reliability` commands: stewarding consistency (f1hub/stewarding.py), team radio
against the data (f1hub/radiodata.py) and reliability since 1950 (f1hub/reliability.py)."""
from . import history, radiodata, reliability, seasonreport as sr, stewarding


def stewards(year, upto=None):
    db = sr.open_dataset(year)
    if db is None:
        return {"year": year, "exists": False}
    try:
        return dict(stewarding.report(db, upto), year=year, exists=True)
    finally:
        db.close()


def radio(year, upto=None):
    db = sr.open_dataset(year)
    if db is None:
        return {"year": year, "exists": False}
    try:
        last = db.execute("SELECT MAX(round) FROM sessions").fetchone()[0] or 0
        upto = last if upto is None else min(upto, last)
        teams = sr._Teams(db, upto)
        fits = {f["session_key"]: f for f in sr.model(db, upto, teams)["races"]}
        return dict(radiodata.report(db, fits, upto), year=year, exists=True)
    finally:
        db.close()


def report(year, upto=None):
    return {"year": year, "stewards": stewards(year, upto), "radio": radio(year, upto),
            "reliability": reliability.report(), "history": {"have": history.have()}}
