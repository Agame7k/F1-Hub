"""Pre-race briefing: grid penalties and the qualifying recap, from canned OpenF1 rows."""
import unittest
from types import SimpleNamespace
from unittest import mock

from f1hub import api, prerace
import isolation  # noqa: F401
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)


def fake_get(endpoint, **kw):
    if endpoint == "sessions":
        return [{"session_key": 9, "session_name": "Qualifying"}]
    if endpoint == "session_result":
        return [{"driver_number": n, "position": i + 1, "duration": [90.0, 89.0 + i / 10, None]}
                for i, n in enumerate((1, 2, 3, 4))]
    return []


class Quali(unittest.TestCase):
    def tracker(self, grid):
        return SimpleNamespace(is_sprint=False, session={"meeting_key": 5}, grid=grid,
                               code=lambda n: f"D{n}", colour=lambda n: "FFFFFF")

    def test_penalty_is_the_driver_who_drops_not_the_ones_moving_up(self):
        with mock.patch.object(api, "get", side_effect=fake_get):
            q = prerace._quali(self.tracker({2: 1, 3: 2, 1: 3, 4: 4}), None)
        self.assertEqual([(p["code"], p["quali"], p["grid"]) for p in q["penalties"]], [("D1", 1, 3)])
        self.assertEqual([x["code"] for x in q["front_row"]], ["D2", "D3"])
        self.assertEqual(q["pole"]["time"], 89.0)

    def test_missing_from_grid_is_a_pit_lane_start(self):
        with mock.patch.object(api, "get", side_effect=fake_get):
            q = prerace._quali(self.tracker({1: 1, 2: 2, 3: 3}), None)
        self.assertEqual([x["code"] for x in q["missing"]], ["D4"])
        self.assertEqual(q["penalties"], [])
