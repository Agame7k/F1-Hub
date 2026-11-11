"""Progress reporting (f1hub/progress.py): the ETA from counted requests, and the fallback without them."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from f1hub import progress
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Requests:
    def __init__(self):
        self.n = 0

    def __call__(self):
        return self.n


class RequestETA(unittest.TestCase):
    def setUp(self):
        self.clock, self.sent = Clock(), Requests()
        patch = mock.patch.object(progress.time, "monotonic", self.clock)
        patch.start()
        self.addCleanup(patch.stop)

    def phase(self, work, per_request=1.0):
        return progress.Phase("telemetry", len(work), work, per_request, self.sent)

    def unit(self, p, requests, seconds):
        self.sent.n += requests
        self.clock.now += seconds
        p.finish_unit(self.clock.now)

    def test_estimate_from_the_start(self):
        p = self.phase([40, 40, 0])
        self.assertTrue(p.bounded)
        self.assertEqual(p.eta(self.clock.now), 80)       # 80 requests at the rate limit's pace; overhead unknown

    def test_current_unit_counts_its_requests(self):
        p = self.phase([40, 40])
        self.sent.n += 10
        self.clock.now += 10
        self.assertEqual(p.eta(self.clock.now), 70)

    def test_overhead_is_learned_from_finished_units(self):
        p = self.phase([40, 40, 0])
        self.unit(p, 40, 70)                              # 40 s of requests + 30 s measuring corners
        self.assertFalse(p.bounded)
        self.assertEqual(p.model(), (30.0, 1.0))
        self.assertEqual(p.eta(self.clock.now), (30 + 40) + 30)

    def test_request_time_is_fitted_when_counts_vary(self):
        p = self.phase([10, 20, 30, 40, 50])
        for r in (10, 20, 30, 40):
            self.unit(p, r, 5 + 2 * r)                    # 2 s a request (slow downloads), 5 s overhead
        o, s = p.model()
        self.assertAlmostEqual(s, 2.0)
        self.assertAlmostEqual(o, 5.0)
        self.assertAlmostEqual(p.eta(self.clock.now), 105.0)

    def test_without_a_request_counter_falls_back(self):
        p = progress.Phase("stewards", 3, [5, 5, 5], 1.0, None)
        self.assertIsNone(p.work)
        self.assertIsNone(p.eta(self.clock.now))          # nothing to go on until a unit finishes or reports N/M
        self.clock.now += 20
        p.finish_unit(self.clock.now)
        self.assertEqual(p.eta(self.clock.now), 40)

    def test_unit_time_from_earlier_compiles_without_requests(self):
        p = progress.Phase("team radio", 3, None, 1.0, self.sent, prior=(300.0, 1.0))
        self.assertEqual(p.eta(self.clock.now), 900)      # Whisper: no requests to count, but a known time a race
        self.clock.now += 600
        p.finish_unit(self.clock.now)                     # a slow one pulls the average up
        self.assertAlmostEqual(p.eta(self.clock.now), 2 * (300 * 2 + 600) / 3)

    def test_earlier_compiles_give_the_start(self):
        p = progress.Phase("sessions", 2, [6, 9], 1.0, self.sent, prior=(3.0, 1.6))
        self.assertFalse(p.bounded)
        self.assertAlmostEqual(p.eta(self.clock.now), 3 + 6 * 1.6 + 3 + 9 * 1.6)

    def test_requests_never_beat_the_rate_limit(self):
        p = self.phase([10, 20, 30, 40])
        for r in (10, 20, 30):
            self.unit(p, r, 0.2 * r)                      # a burst: quicker than 60 a minute can last
        self.assertEqual(p.model()[1], 1.0)


class Pace(unittest.TestCase):
    def test_learned_and_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pace.json"
            pace = progress.Pace(path, rate=1.0)
            self.assertEqual(pace.estimate("sessions", 2, 15), (15.0, False))     # the rate limit alone
            pace.learn("sessions", [(6, 8.6), (9, 15.7)])                          # the 2026 Sao Paulo run
            again = progress.Pace(path, rate=1.0)
            t, known = again.estimate("sessions", 2, 15)
            self.assertTrue(known)
            self.assertAlmostEqual(t, 8.6 + 15.7)
            self.assertIsNone(again.get("telemetry"))

    def test_a_damaged_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pace.json"
            path.write_text("{not json")
            self.assertEqual(progress.Pace(path).estimate("sessions", 1, 5), (5.0, False))


class Reporter(unittest.TestCase):
    def test_status_line_and_plan(self):
        out, sent = io.StringIO(), Requests()
        r = progress.Reporter(out, tick=0, requests=sent)
        progress.begin(r, "sessions", 2, [12, 0])
        self.assertIn("sessions: 2 to do, 12 requests", out.getvalue())
        self.assertIn("ETA >=12s", r.status())
        progress.done(r, "R1 Bahrain - Race", "1000 laps")
        self.assertIn("sessions 1/2  R1 Bahrain - Race  1000 laps", out.getvalue())
        r.close()
        self.assertIsNotNone(r.pace.get("sessions"))      # the run's pace is kept for next time

    def test_step_and_whole_job_eta(self):
        pace = progress.Pace(rate=1.0)
        pace.data = {"sessions": {"o": 0, "s": 1.0, "n": 5}, "telemetry": {"o": 60, "s": 1.0, "n": 5}}
        steps = [("sessions", 2, 10), ("telemetry", 3, 30), ("stewards", 0, None)]
        r = progress.Reporter(io.StringIO(), tick=0, requests=Requests(), pace=pace, steps=steps)
        progress.begin(r, "sessions", 2, [5, 5])
        status = r.status()
        self.assertIn("step 1/2 sessions 1/2", status)                 # a step with nothing to do isn't counted
        self.assertIn("ETA ~10s", status)
        self.assertIn("all ~3m40s", status)                            # 10 s + telemetry: 3 x 60 s + 30 requests
        progress.begin(r, "telemetry", 3, [10, 10, 10])
        self.assertIn("step 2/2 telemetry", r.status())
        self.assertIn("all ~3m30s", r.status())
        r.close()

    def test_untimed_steps_make_the_whole_job_a_lower_bound(self):
        steps = [("sessions", 1, 4), ("team radio", 2, None)]
        r = progress.Reporter(io.StringIO(), tick=0, requests=Requests(), steps=steps)
        progress.begin(r, "sessions", 1, [4])
        self.assertIn("all >=4s", r.status())
        r.close()

    def test_steps_without_requests_are_timed_too(self):
        r = progress.Reporter(io.StringIO(), tick=0, requests=Requests())
        progress.begin(r, "stewards", 2)
        progress.done(r, "R1 Bahrain")
        r.close()
        self.assertIsNotNone(r.pace.get("stewards"))

    def test_plain_callable_ignores_the_hooks(self):
        got = []
        progress.begin(got.append, "sessions", 2, [1, 2])
        progress.done(got.append, "x")
        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()
