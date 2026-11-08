"""The OpenF1 client's on-disk cache (f1hub/api.py): damaged entries, atomic writes, stale fallbacks."""
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from f1hub import api
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b"", headers=None):
    return urllib.error.HTTPError("https://api.openf1.org/v1/x", code, "err", headers or {}, io.BytesIO(body))


class Online(unittest.TestCase):
    """Each test gets its own empty cache, the network replaced by `self.answers`, and no rate-limit waits."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.answers = []
        self.requests = []

        def urlopen(req, timeout=None):
            self.requests.append(req.full_url)
            a = self.answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return Response(json.dumps(a).encode())
        for p in (mock.patch.object(api, "CACHE_DIR", self.dir / "cache"), mock.patch.object(api, "offline", False),
                  mock.patch.object(api, "RECORD_DIR", self.dir / "rec"), mock.patch.object(api, "_throttle", lambda: None),
                  mock.patch.object(api.urllib.request, "urlopen", urlopen), mock.patch("time.sleep", lambda s: None)):
            p.start()
            self.addCleanup(p.stop)

    def path(self, endpoint="laps", **params):
        return api._cache_path(api._build_url(endpoint, params or {"session_key": 1}))


class DamagedEntries(Online):
    def test_truncated_file_is_fetched_again(self):
        self.answers = [[{"lap": 1}]]
        p = self.path()
        p.parent.mkdir(parents=True)
        p.write_text('{"url": "https://api.openf1.org/v1/laps?session_key=1", "fetched": 1, "data": [{"la')
        with self.assertLogs("f1hub.api", "WARNING"):
            self.assertEqual(api.get("laps", session_key=1), [{"lap": 1}])
        self.assertEqual(json.loads(p.read_text())["data"], [{"lap": 1}])     # replaced with a good copy

    def test_wrong_shape_is_a_miss(self):
        self.answers = [[]]
        p = self.path()
        p.parent.mkdir(parents=True)
        p.write_text("[1, 2, 3]")
        with self.assertLogs("f1hub.api", "WARNING"):
            self.assertEqual(api.get("laps", session_key=1), [])

    def test_offline_with_a_damaged_entry_says_not_cached(self):
        p = self.path()
        p.parent.mkdir(parents=True)
        p.write_text("{")
        with mock.patch.object(api, "offline", True), self.assertLogs("f1hub.api", "WARNING"), \
                self.assertRaisesRegex(api.APIError, "Not cached"):
            api.get("laps", session_key=1)


class AtomicWrites(Online):
    def test_no_temporary_files_left(self):
        self.answers = [[{"a": 1}]]
        api.get("laps", session_key=1)
        self.assertEqual([f.name for f in (self.dir / "cache").iterdir()], [self.path().name])

    def test_failed_write_keeps_the_old_copy(self):
        target = self.dir / "x.json"
        api.write_atomic(target, "old")
        with mock.patch("os.replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            api.write_atomic(target, "new")
        self.assertEqual(target.read_text(), "old")
        self.assertEqual([f.name for f in self.dir.iterdir()], ["x.json"])

    def test_unwritable_cache_still_returns_the_data(self):
        self.answers = [[{"a": 1}]]
        with mock.patch.object(api, "write_atomic", side_effect=OSError("read-only")), \
                self.assertLogs("f1hub.api", "WARNING"):
            self.assertEqual(api.get("laps", session_key=1), [{"a": 1}])


class Freshness(Online):
    def seed(self, data, age):
        api.write_atomic(self.path(), json.dumps({"url": "u", "fetched": time.time() - age, "data": data}))

    def test_fresh_copy_needs_no_request(self):
        self.seed(["cached"], age=10)
        self.assertEqual(api.get("laps", ttl=60, session_key=1), ["cached"])
        self.assertEqual(self.requests, [])

    def test_stale_copy_beats_a_network_error(self):
        self.seed(["old"], age=3600)
        self.answers = [urllib.error.URLError("down")]
        self.assertEqual(api.get("laps", ttl=60, session_key=1), ["old"])

    def test_saved_too_early_is_stale_whatever_the_ttl(self):
        self.seed(["partial"], age=100)
        self.answers = [["final"]]
        self.assertEqual(api.get("laps", fetched_after=time.time() - 50, session_key=1), ["final"])

    def test_404_means_no_rows_and_is_cached(self):
        self.answers = [http_error(404)]
        self.assertEqual(api.get("laps", session_key=1), [])
        self.assertTrue(self.path().exists())

    def test_rate_limit_is_retried(self):
        self.answers = [http_error(429, headers={"Retry-After": "1"}), [{"ok": 1}]]
        self.assertEqual(api.get("laps", session_key=1), [{"ok": 1}])
        self.assertEqual(len(self.requests), 2)

    def test_live_lock(self):
        self.answers = [http_error(401, b'{"detail": "Live F1 session in progress"}')]
        with self.assertRaises(api.LiveSessionLock):
            api.get("laps", session_key=1)
        self.seed(["old"], age=3600)
        self.answers = [http_error(401, b"live session")]
        self.assertEqual(api.get("laps", ttl=60, session_key=1), ["old"])     # a stale copy is better than nothing


class LiveRequests(Online):
    def test_live_requests_are_handed_to_the_recorder_not_cached(self):
        self.answers = [[{"position": 1}], [{"position": 2}]]
        got = []
        api.get("position", cache=False, record=lambda *a: got.append(a), session_key=9)
        self.assertFalse((self.dir / "cache").exists())
        self.assertEqual(got, [("position", "https://api.openf1.org/v1/position?session_key=9", {"session_key": 9},
                                [{"position": 1}])])
        api.get("position", cache=False, session_key=9)          # no recorder: nothing kept anywhere
        self.assertFalse((self.dir / "cache").exists())


if __name__ == "__main__":
    unittest.main()
