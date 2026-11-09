"""Cars and circuits (f1hub/carreport.py): the circuit grouping maths - offline."""
import unittest

from f1hub import carreport
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)


class Maths(unittest.TestCase):
    def test_kmeans_finds_obvious_groups(self):
        pts = [[0, 0], [0.1, 0], [0, 0.2], [5, 5], [5.2, 5], [10, 0], [10, 0.3]]
        labels, centres = carreport.kmeans(pts, 3)
        self.assertEqual(len(set(labels)), 3)
        self.assertEqual(labels[0], labels[1])
        self.assertEqual(labels[0], labels[2])
        self.assertEqual(labels[3], labels[4])
        self.assertEqual(labels[5], labels[6])
        self.assertEqual(carreport.kmeans(pts, 3), carreport.kmeans(pts, 3))     # deterministic

    def test_pca_first_component_follows_the_spread(self):
        pts = [[float(i), 0.1 * (i % 2)] for i in range(10)]
        coords, comps, share = carreport.pca2(pts)
        self.assertGreater(abs(comps[0][0]), 0.99)
        self.assertGreater(share[0], 0.95)
        self.assertEqual(len(coords), 10)

    def test_group_names(self):
        keys = [k for k, _, _ in carreport.FEATURES]
        ix = {k: i for i, k in enumerate(keys)}
        centres = [[0.0] * len(keys) for _ in range(3)]
        centres[1][ix["full_throttle"]] = 2.0          # Monza-like
        centres[2][ix["corner_speed"]] = 1.5           # Suzuka-like
        centres[0][ix["slow_km"]] = 1.5                # Monaco-like
        self.assertEqual(carreport._name_groups(centres, keys), {1: "Power", 2: "High-speed corners", 0: "Slow and twisty"})

    def test_mean_and_correlation(self):
        self.assertEqual(carreport._mean_se([1.0, 3.0]), {"est": 2.0, "se": 1.0, "n": 2})
        self.assertEqual(carreport._corr([(1, 2), (2, 4), (3, 6), (4, 8), (5, 10)]), 1.0)
        self.assertIsNone(carreport._corr([(1, 2)]))

    def test_no_dataset(self):
        self.assertEqual(carreport.report(1999), {"year": 1999, "exists": False})


if __name__ == "__main__":
    unittest.main()
