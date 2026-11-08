"""Track elevation for the 3D map (f1hub/live.py elevation_profile): heights from one lap of location samples,
put on the circuit outline the map is drawn from."""
import math
import unittest

from f1hub.live import elevation_profile


def lap(n=200, r=5000, hill=400, z0=7400):
    """A round circuit with one hill: [x, y, z] in OpenF1 units (tenths of a metre)."""
    return [[r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n),
             z0 + hill * max(0, math.cos(2 * math.pi * i / n))] for i in range(n)]


class ElevationProfile(unittest.TestCase):
    def test_heights_in_metres_above_the_lowest_point(self):
        trace = lap()
        outline = [[x, y] for x, y, _ in trace[::4]]
        e = elevation_profile(outline, trace)
        self.assertEqual(len(e), len(outline))
        self.assertEqual(min(e), 0)
        self.assertAlmostEqual(max(e), 40, delta=3)          # 400 dm hill, slightly smoothed
        self.assertGreater(e[0], e[len(e) // 2])             # the hill is at the start of this lap

    def test_samples_without_a_height_are_ignored(self):
        trace = lap()
        trace[10][2] = trace[11][2] = 0                      # the pit-lane / garage rows OpenF1 sends with z = 0
        e = elevation_profile([[x, y] for x, y, _ in trace[::4]], trace)
        self.assertAlmostEqual(max(e), 40, delta=3)

    def test_nothing_when_the_lap_does_not_match_the_outline(self):
        far = [[x + 50000, y, z] for x, y, z in lap()]       # another layout / coordinate frame
        self.assertIsNone(elevation_profile([[x, y] for x, y, _ in lap()[::4]], far))

    def test_nothing_without_enough_samples(self):
        self.assertIsNone(elevation_profile([[0, 0]] * 20, lap(n=20)))
        self.assertIsNone(elevation_profile([[0, 0]] * 20, [[x, y, 0] for x, y, _ in lap()]))


if __name__ == "__main__":
    unittest.main()
