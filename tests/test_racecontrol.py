"""Reading race control (f1hub/racecontrol.py), against real OpenF1 messages: what kind each one is, and what it
does to the race. Live timing, replays, race reports and the analysis all go by these."""
import unittest
from datetime import datetime, timedelta, timezone

from f1hub.live import LiveTracker
from f1hub.racecontrol import RaceControl, classify, neutral_periods, neutralisation, neutralisation_counts
from f1hub.report import RaceReport
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)

# (category, flag, scope, message) as OpenF1 sends them -> (classify kind, neutralisation)
REAL = [
    ("SafetyCar", None, None, "SAFETY CAR DEPLOYED", "sc", "sc"),
    ("SafetyCar", None, None, "SAFETY CAR IN THIS LAP", "sc_end", "sc_end"),
    ("SafetyCar", None, None, "SAFETY CAR THROUGH THE PIT LANE", "sc", None),
    ("SafetyCar", None, None, "VIRTUAL SAFETY CAR DEPLOYED", "vsc", "vsc"),
    ("SafetyCar", None, None, "VIRTUAL SAFETY CAR ENDING", "sc_end", "vsc_end"),
    ("SafetyCar", None, None, "VSC DEPLOYED", "vsc", "vsc"),
    ("SafetyCar", None, None, "VSC ENDING", "sc_end", "vsc_end"),
    ("Other", None, None, "SAFETY CAR LIGHTS ON", "sc", None),
    ("Other", None, None, "ALL CARS MAY OVERTAKE THE SAFETY CAR", "sc", None),
    ("Other", None, None, "MEDICAL CAR DEPLOYED", "incident", None),
    ("Flag", "RED", "Track", "RED FLAG", "red", "red"),
    ("Other", None, None, "RED FLAG - RACE SUSPENDED", "red", "red"),
    ("SessionStatus", None, None, "SESSION ABORTED", "session", "red"),
    ("SessionStatus", None, None, "SESSION STARTED", "session", "started"),
    ("SessionStatus", None, None, "SESSION FINISHED", "session", None),
    ("Other", None, None, "RACE WILL RESUME AT 14:06 - ROLLING START PROCEDURE", "start", None),
    ("Other", None, None, "Q1 WILL RESUME AT 07:47", "info", None),
    ("Other", None, None, "Q2 WILL NOT BE RESUMED", "info", None),
    ("Other", None, None, "RESUMPTION ORDER: 3, 16, 12, 44, 1, 63", "start", None),
    ("Flag", "CHEQUERED", "Track", "CHEQUERED FLAG", "chequered", None),
    ("Flag", "GREEN", "Track", "GREEN LIGHT - PIT EXIT OPEN", "green", None),
    ("Flag", "CLEAR", "Track", "TRACK CLEAR", "track_clear", None),
    ("Flag", "YELLOW", "Sector", "YELLOW IN TRACK SECTOR 13", "sector", None),
    ("Flag", "DOUBLE YELLOW", "Sector", "DOUBLE YELLOW IN TRACK SECTOR 13", "sector", None),
    ("Flag", "CLEAR", "Sector", "CLEAR IN TRACK SECTOR 12", "sector", None),
    ("Flag", "BLUE", "Driver", "WAVED BLUE FLAG FOR CAR 24 (ZHO) TIMED AT 13:32:27", "blue", None),
    ("Flag", "BLACK AND WHITE", "Driver", "BLACK AND WHITE FLAG FOR CAR 18 (STR) - TRACK LIMITS", "black_white", None),
    ("Other", None, None, "FIA STEWARDS: BLACK FLAG FOR CAR 27 (HUL)", "black", None),
    ("Other", None, None, "FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 50 (BEA) - CAUSING A COLLISION", "penalty", None),
    ("Other", None, None, "FIA STEWARDS: PENALTY SERVED - DRIVE THROUGH PENALTY FOR CAR 55 (SAI) - SPEEDING IN THE PIT LANE",
     "penalty_served", None),
    ("Other", None, None, "FIA STEWARDS: REPRIMAND (DRIVING) FOR CAR 87 (BEA) - REVERSING IN THE PIT LANE (14:59:59)",
     "reprimand", None),
    ("Other", None, None, "TURN 10 INCIDENT INVOLVING CARS 50 (BEA) AND 43 (COL) NOTED - CAUSING A COLLISION",
     "investigation", None),
    ("Other", None, None, "FIA STEWARDS: INCIDENT INVOLVING CARS 63 (RUS), 22 (TSU) AND 30 (LAW) WILL BE INVESTIGATED "
     "AFTER THE RACE - STARTING PROCEDURE INFRINGEMENT", "investigation", None),
    ("Other", None, None, "FIA STEWARDS: LAP 1 TURN 1 INCIDENT INVOLVING CARS 23 (ALB) AND 20 (MAG) REVIEWED NO FURTHER "
     "INVESTIGATION - CAUSING A COLLISION", "decision", None),
    ("Other", None, None, "CAR 18 (STR) TIME 1:38.172 DELETED - TRACK LIMITS AT TURN 12 LAP 3 7:34:27", "lap_deleted", None),
    ("Other", None, None, "CAR 77 (BOT) LAP DELETED - DOUBLE YELLOW AT TURN 12", "lap_deleted", None),
    ("Drs", None, None, "DRS ENABLED", "overtake_mode", None),
    ("Other", None, None, "OVERTAKE ENABLED", "overtake_mode", None),
    ("Other", None, None, "MARSHALS ON TRACK AT TURN 6", "incident", None),
    ("Other", None, None, "RECOVERY VEHICLE ON TRACK AT TURN 17", "incident", None),
    ("Other", None, None, "PIT EXIT CLOSED", "pit", None),
    ("Other", None, None, "RISK OF RAIN FOR F1 RACE IS 100%", "weather", None),
    ("Other", None, None, "LOW GRIP CONDITIONS", "weather", None),        # Sao Paulo 2026
    ("Other", None, None, "LOW GRIP DELTA ACTIVE", "weather", None),
    ("Other", None, None, "NORMAL GRIP CONDITIONS", "weather", None),
    ("Other", None, None, "AWNINGS MAY BE USED", "weather", None),
    ("Other", None, None, "OVERTAKE DISABLED", "overtake_mode", None),
    ("Other", None, None, "YELLOW IN PIT LANE", "pit", None),
    ("Other", None, None, "RACE START", "start", None),
    ("Other", None, None, "INCIDENT INVOLVING CAR 27 (HUL) NOTED - FAILING TO FOLLOW RACE DIRECTORS INSTRUCTIONS – "
     "PRACTICE START INFRINGEMENT (13:20:45)", "investigation", None),
    ("Other", None, None, "FORMATION LAP WILL START AT 12:47", "start", None),
    ("Other", None, None, "FIRST CAR TO TAKE THE FLAG - CAR 44 (HAM)", "info", None),
]


def msg(cat, flag, scope, text, **kw):
    return dict({"category": cat, "flag": flag, "scope": scope, "message": text}, **kw)


class RealMessages(unittest.TestCase):
    def test_each_kind(self):
        for cat, flag, scope, text, kind, neutral in REAL:
            with self.subTest(text):
                m = msg(cat, flag, scope, text)
                self.assertEqual(classify(m), kind)
                self.assertEqual(neutralisation(m), neutral)


T0 = datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc)


def at(s, cat, text, flag=None, lap=None):
    return {"date": (T0 + timedelta(seconds=s)).isoformat(), "category": cat, "flag": flag, "scope": None,
            "message": text, "lap_number": lap}


# a race with a VSC, a safety car and a red flag announced three ways
RACE = [at(0, "SessionStatus", "SESSION STARTED", lap=1),
        at(600, "SafetyCar", "VIRTUAL SAFETY CAR DEPLOYED", lap=8),
        at(660, "SafetyCar", "VIRTUAL SAFETY CAR ENDING", lap=9),
        at(1200, "SafetyCar", "SAFETY CAR DEPLOYED", lap=15),
        at(1500, "SafetyCar", "SAFETY CAR IN THIS LAP", lap=18),
        at(2400, "Flag", "RED FLAG", flag="RED", lap=27),
        at(2401, "Other", "RED FLAG - RACE SUSPENDED", lap=27),
        at(2402, "SessionStatus", "SESSION ABORTED", lap=27),
        at(3000, "Other", "RACE WILL RESUME AT 21:00", lap=27),
        at(3600, "SessionStatus", "SESSION STARTED", lap=27)]


class OneReading(unittest.TestCase):
    def test_counts_a_stoppage_once(self):
        self.assertEqual(neutralisation_counts(RACE), {"sc": 1, "vsc": 1, "red": 1})

    def test_periods(self):
        t = T0.timestamp()
        self.assertEqual(neutral_periods(RACE), [(t + 600, t + 680), (t + 1200, t + 1590), (t + 2400, t + 3620)])

    def test_report_agrees(self):
        rep = RaceReport.__new__(RaceReport)
        rep.race_control = RACE
        self.assertEqual(rep.sc_summary(), {"safety_cars": 1, "vscs": 1, "red_flags": 1, "track_limits": 0})
        self.assertEqual(rep.neutralised_laps(), set(range(8, 10)) | set(range(15, 19)))

    def test_live_status(self):
        t = LiveTracker.__new__(LiveTracker)
        t.track_status = "GREEN"
        seen = []
        for m in RACE:
            t._track_status(m)
            seen.append(t.track_status)
        self.assertEqual(seen, ["GREEN", "VSC", "GREEN", "SC", "GREEN", "RED", "RED", "RED", "RED", "GREEN"])


class Stewarding(unittest.TestCase):
    def test_a_served_penalty_is_not_a_second_one(self):
        rc = RaceControl("-03:00:00")
        rc.add(at(0, "Other", "FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 23 (ALB) - REJOINING UNSAFELY (14:50:05)"))
        rc.add(at(240, "Other", "FIA STEWARDS: PENALTY SERVED - 5 SECOND TIME PENALTY FOR CAR 23 (ALB) (14:50:05)"))
        d = rc.per_driver[23]
        self.assertEqual(d["penalties"], 1)
        self.assertEqual([i["kind"] for i in d["items"]], ["penalty", "penalty_served"])
        self.assertEqual(rc.counts.get("penalty"), 1)

    def test_low_grip_conditions(self):
        rc = RaceControl()
        self.assertIsNone(rc.low_grip)
        rc.add(at(0, "Other", "LOW GRIP CONDITIONS"))
        self.assertTrue(rc.low_grip)
        rc.add(at(60, "Other", "NORMAL GRIP CONDITIONS"))
        self.assertIs(rc.low_grip, False)
        self.assertIs(rc.snapshot(str)["low_grip"], False)


if __name__ == "__main__":
    unittest.main()
