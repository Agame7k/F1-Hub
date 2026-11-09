"""Race control and radio (phase 7): reading stewards' decisions, classifying team radio, and reliability - offline."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from f1hub import dataset, history, radiodata, reliability, stewarding
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)


class Decisions(unittest.TestCase):
    def test_outcomes(self):
        cases = {
            "5 second time penalty.\n1 penalty point (total of 6 for the 12 month period).": ("time", 5, None, 1),
            "10 second time penalty.": ("time", 10, None, None),
            "Drive through penalty.": ("drive_through", None, None, None),
            "10 second stop and go penalty.": ("stop_go", None, None, None),
            "Drop of 3 grid positions for the next Race in which the driver participates.": ("grid", None, 3, None),
            "5 place grid penalty": ("grid", None, 5, None),
            "Required to start the Sprint from the pit lane.": ("pit_start", None, None, None),
            "No further action.": ("no_action", None, None, None),
            "No penalty applied.": ("no_action", None, None, None),
            "Reprimand (driving).": ("reprimand", None, None, None),
            "The competitor is fined €10,000\nA formal warning is issued to the driver.": ("fine", None, None, None),
            "The lap time of 1:31.998 of Car 27 recorded in Q1 is deleted.": ("lap_deleted", None, None, None),
            "Disqualified from the results of the Race.": ("dsq", None, None, None),
            "15. The Protest is rejected as it is not founded.": ("no_action", None, None, None),
        }
        for text, (out, secs, grid, points) in cases.items():
            o, det = stewarding.outcome(text)
            self.assertEqual((o, det["seconds"], det["grid"], det["points"]), (out, secs, grid, points), text)
        self.assertEqual(stewarding.outcome("The competitor is fined €10,000")[1]["fine"], 10000)

    def test_most_severe_outcome_wins(self):
        self.assertEqual(stewarding.outcome("10 second time penalty. Fine of €5,000. Reprimand.")[0], "time")

    def test_categories(self):
        cases = [("Alleged causing a collision", None, "collision"),
                 ("More than one change of direction", None, "direction"),
                 ("Leaving the track without a justifiable reason multiple times.", None, "track_limits"),
                 ("Alleged leaving the track and gaining an advantage", None, "off_advantage"),
                 ("Pit Lane Speeding", None, "pit_speeding"),
                 ("Unsafe Release From Garage", None, "unsafe_release"),
                 ("Unsafe Condition", "Car 4 was released in an unsafe condition.", "unsafe_release"),
                 ("Driving a car in an unsafe condition", "Car 16 continued on track with significant and obvious damage", "damaged_car"),
                 ("Impeding of Car 4", None, "impeding"),
                 ("Changes made during Parc Ferme", None, "technical"),
                 ("Protest against Car 63", None, "protest"),
                 ("Fan Engagement Activity", None, "procedure"),
                 ("Something new", "Nothing we know", "other")]
        for offence, fact, want in cases:
            self.assertEqual(stewarding.category(offence, fact), want, offence)

    def test_blame(self):
        self.assertEqual(stewarding.blame("Car 4 was wholly to blame for the collision."), "wholly")
        self.assertEqual(stewarding.blame("Car 4 was predominantly to blame."), "predominantly")
        self.assertEqual(stewarding.blame("no driver was wholly or predominantly at fault"), "not_predominantly")
        self.assertEqual(stewarding.blame("We consider this a racing incident."), "racing_incident")
        self.assertIsNone(stewarding.blame("Car 4 hit Car 5."))


class Consistency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = dataset.connect(Path(self.tmp.name) / "t.sqlite")
        self.db.row_factory = sqlite3.Row

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def add(self, doc, n, offence, fact, decision, session="Race", reason=""):
        self.db.execute(f"INSERT INTO stewards VALUES ({','.join('?' * 25)})",
                        (1, 1, doc, f"https://www.fia.com/{doc}.pdf", f"Doc {doc} - Decision - Car {n} - {offence}", "decision",
                         "", session, 1, n, "", "", fact, "", decision, reason, "[]", "", "", None, None, None, None, None, offence))

    def test_report(self):
        self.db.execute("INSERT INTO stew_events VALUES (1, 1, 0, 1)")
        fact = "Car {} left the track at turn 1 and gained a lasting advantage overtaking Car 44 on the outside"
        self.add(1, 1, "Leaving the track and gaining an advantage", fact.format(1), "No further action.")
        self.add(2, 4, "Leaving the track and gaining an advantage", fact.format(4), "10 second time penalty.")
        self.add(3, 16, "Pit lane speeding", "Speeding in the pit lane by 2.1 km/h", "Fine of €200", session="Practice 1")
        r = stewarding.report(self.db)
        self.assertEqual(r["decisions"], 3)
        cat = next(c for c in r["categories"] if c["category"] == "off_advantage")
        self.assertEqual((cat["n"], cat["consistency"]), (2, 0.5))
        self.assertEqual(len(r["pairs"]), 1)                # alike, but one was penalised and the other wasn't
        self.assertEqual({r["pairs"][0]["a"]["outcome"], r["pairs"][0]["b"]["outcome"]}, {"no_action", "time"})
        d4 = next(d for d in r["drivers"] if d["number"] == 4)
        self.assertEqual((d4["penalties"], d4["seconds"]), (1, 10))


class Radio(unittest.TestCase):
    def test_classify(self):
        cases = {"Box, box. Box this lap for hards.": {"box"},
                 "The tyres are gone mate, no grip at all": {"tyres"},
                 "Stay out, stay out, we're staying out": {"stay_out"},
                 "Lift and coast into turn one, save fuel": {"saving"},
                 "Let him through please, we'll swap back": {"team_orders"},
                 "I've lost power, something is wrong": {"car"},
                 "Plan B, we're going to try the undercut": {"strategy"},
                 "Rain expected in five minutes": {"weather"},
                 "We're swapping cars into turn one": {"team_orders"},
                 "The brake pedal's going long": {"car"}}
        for text, want in cases.items():
            self.assertTrue(want <= set(radiodata.classify(text)), (text, radiodata.classify(text)))
        self.assertNotIn("tyres", radiodata.classify("Tyre update. No sign of graining on any compound."))
        self.assertEqual(radiodata.classify(""), [])
        self.assertEqual(radiodata.classify(None), [])

    def test_lap_of(self):
        laps = [("2025-01-01T12:00:00", 1), ("2025-01-01T12:01:30", 2), ("2025-01-01T12:03:00", 3)]
        self.assertEqual(radiodata._lap_of(laps, "2025-01-01T12:02:00"), 2)
        self.assertIsNone(radiodata._lap_of(laps, "2025-01-01T11:59:00"))
        self.assertEqual(radiodata._lap_of(laps, "2025-01-01T13:00:00"), 3)


class Reliability(unittest.TestCase):
    def test_groups(self):
        for status, want in [("Finished", "finished"), ("+1 Lap", "finished"), ("+12 Laps", "finished"), ("Lapped", "finished"),
                             ("Engine", "engine"), ("Power Unit", "engine"), ("ERS", "engine"), ("Gearbox", "transmission"),
                             ("Hydraulics", "hydraulics"), ("Accident", "incident"), ("Collision damage", "incident"),
                             ("Spun off", "incident"), ("Disqualified", "dsq"), ("Did not start", "dns"), ("Withdrew", "dns"),
                             ("Retired", "unknown"), ("Something new", "other")]:
            self.assertEqual(reliability.group(status), want, status)

    def test_power_unit_map(self):
        self.assertEqual(reliability.PU[2014]["red_bull"], "Renault")
        self.assertEqual(reliability.PU[2015]["mclaren"], "Honda")
        self.assertEqual(reliability.PU[2021]["mclaren"], "Mercedes")
        self.assertEqual(reliability.PU[2025]["rb"], "Honda RBPT")
        for y, m in reliability.PU.items():           # every hybrid-era season has 10-11 teams on record
            self.assertTrue(10 <= len(m) <= 11, (y, len(m)))

    def test_rate_interval(self):
        r = reliability._rate(5, 100)
        self.assertEqual(r["est"], 0.05)
        self.assertLess(r["lo"], 0.05)
        self.assertGreater(r["hi"], 0.05)
        self.assertGreaterEqual(reliability._rate(0, 50)["lo"], 0)

    def test_report_from_kept_seasons(self):
        def race(rnd, results):
            return {"round": str(rnd), "raceName": f"GP {rnd}", "Circuit": {"circuitId": f"c{rnd}"}, "date": "2015-01-01",
                    "Results": [{"Driver": {"driverId": d, "code": d[:3].upper()}, "Constructor": {"constructorId": t, "name": t.title()},
                                 "grid": "1", "position": str(i + 1), "positionText": str(i + 1), "laps": "50", "status": s}
                                for i, (d, t, s) in enumerate(results)]}
        page = {"MRData": {"total": "4", "RaceTable": {"Races": [
            race(1, [("ham", "mercedes", "Finished"), ("vet", "ferrari", "Engine")]),
            race(2, [("ham", "mercedes", "Gearbox"), ("vet", "ferrari", "Finished")])]}}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(dataset, "DATA_DIR", Path(tmp)), \
                mock.patch.object(history, "_get", return_value=page), mock.patch.object(history, "SPACING", 0):
            rows = history.season(2015)
            self.assertEqual(len(rows), 4)
            self.assertEqual(history.have(), [2015])
            r = reliability.report()
            self.assertEqual(r["by_year"][0]["mechanical"], 0.5)
            pu = {p["maker"]: p for p in r["power_units"]}
            self.assertEqual(pu["Ferrari"]["pu"]["k"], 1)          # the engine failure
            self.assertEqual(pu["Mercedes"]["pu"]["k"], 0)         # a gearbox isn't the power unit
            self.assertEqual(pu["Mercedes"]["mechanical"]["k"], 1)
            json.dumps(r)


if __name__ == "__main__":
    unittest.main()
