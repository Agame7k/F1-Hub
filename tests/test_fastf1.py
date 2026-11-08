"""FastF1 bridge (f1hub/ff1.py, ff1_signin.py): F1 TV sign-in rules, recorder status, logs, leftover processes.
Runs without FastF1: its sign-in modules are stood in for."""
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from f1hub import ff1, ff1_signin
from isolation import setUpModule, tearDownModule  # noqa: F401  (offline, temporary cache)

CREDS = {"F1_USERNAME": "me@example.com", "F1_PASSWORD": "secret"}


class TempDir(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)


class RejectionNote(TempDir):
    def test_same_credentials_only(self):
        with mock.patch.dict(os.environ, CREDS):
            self.assertIsNone(ff1_signin.rejected(str(self.dir)))
            ff1_signin.note_rejection(str(self.dir), "There was a problem with your password")
            self.assertEqual(ff1_signin.rejected(str(self.dir))["error"], "There was a problem with your password")
            self.assertNotIn("secret", (self.dir / ff1_signin.NOTE).read_text())      # never the password itself
        with mock.patch.dict(os.environ, dict(CREDS, F1_PASSWORD="new one")):
            self.assertIsNone(ff1_signin.rejected(str(self.dir)))                    # changed: try again
        with mock.patch.dict(os.environ, CREDS):
            ff1_signin.forget_rejection(str(self.dir))
            self.assertIsNone(ff1_signin.rejected(str(self.dir)))


class FakeFastF1:
    """Just enough of fastf1 (and requests) for ff1_signin.install()."""

    def __init__(self, method="password", sign_in=None):
        class F1AuthError(Exception):
            pass

        class RequestException(Exception):
            pass
        self.F1AuthError, self.RequestException = F1AuthError, RequestException
        self.calls = 0

        def default():
            self.calls += 1
            return sign_in(self)
        manager = types.SimpleNamespace(_default_auth_func=default)
        f1account = types.SimpleNamespace(get_auth_method=lambda: method, token_manager=manager)
        self.manager = manager
        self.modules = {"fastf1": types.ModuleType("fastf1"), "fastf1.exceptions": types.SimpleNamespace(F1AuthError=F1AuthError),
                        "fastf1.internals": types.SimpleNamespace(f1account=f1account), "fastf1.internals.f1account": f1account,
                        "requests": types.SimpleNamespace(RequestException=RequestException)}

    def install(self, cache):
        with mock.patch.dict(sys.modules, self.modules):
            ff1_signin.install(cache)

    def sign_in(self):
        with mock.patch.dict(sys.modules, self.modules):
            return self.manager._default_auth_func()


class SignInRules(TempDir):
    def test_rejected_password_is_sent_once(self):
        def reject(fake):
            raise fake.F1AuthError("There was a problem with your password or email address.")
        fake = FakeFastF1(sign_in=reject)
        with mock.patch.dict(os.environ, CREDS):
            fake.install(str(self.dir))
            for _ in range(9):              # one session load used to try nine times
                with self.assertRaises(fake.F1AuthError):
                    fake.sign_in()
            self.assertEqual(fake.calls, 1)
            self.assertIsNotNone(ff1_signin.rejected(str(self.dir)))
            other = FakeFastF1(sign_in=reject)         # another process (the recorder) doesn't try at all
            other.install(str(self.dir))
            with self.assertRaisesRegex(other.F1AuthError, "not sending them again"):
                other.sign_in()
            self.assertEqual(other.calls, 0)
            ff1_signin.forget_rejection(str(self.dir))  # "Try the password again" on the page
            with self.assertRaises(fake.F1AuthError):
                fake.sign_in()
            self.assertEqual(fake.calls, 2)

    def test_network_failure_is_retried_later(self):
        def offline(fake):
            try:
                raise fake.RequestException("connection refused")
            except fake.RequestException as e:
                raise fake.F1AuthError("Sign-in failed: connection refused") from e
        fake = FakeFastF1(sign_in=offline)
        with mock.patch.dict(os.environ, CREDS):
            fake.install(str(self.dir))
            for _ in range(3):
                with self.assertRaises(fake.F1AuthError):
                    fake.sign_in()
            self.assertEqual(fake.calls, 1)
            self.assertIsNone(ff1_signin.rejected(str(self.dir)))     # not a rejection: nothing noted
            with mock.patch("time.time", return_value=time.time() + ff1_signin.RETRY + 1), \
                    self.assertRaises(fake.F1AuthError):
                fake.sign_in()
            self.assertEqual(fake.calls, 2)

    def test_browser_method_never_signs_in_here(self):
        fake = FakeFastF1(method="browser", sign_in=lambda f: "token")
        fake.install(str(self.dir))
        with self.assertRaisesRegex(fake.F1AuthError, "not signed in"):
            fake.sign_in()
        self.assertEqual(fake.calls, 0)

    def test_good_password_signs_in(self):
        fake = FakeFastF1(sign_in=lambda f: "token")
        with mock.patch.dict(os.environ, CREDS):
            fake.install(str(self.dir))
            self.assertEqual(fake.sign_in(), "token")


class Status(TempDir):
    def test_login_is_honest_about_a_rejected_password(self):
        with mock.patch.dict(os.environ, CREDS), mock.patch.object(ff1, "CACHE", self.dir), \
                mock.patch.object(ff1, "VENV_PY", Path(sys.executable)), \
                mock.patch.object(ff1, "available", lambda: True), \
                mock.patch.object(ff1, "auth", lambda force=False: {"signed_in": False}):
            os.environ.pop("FASTF1_AUTH_METHOD", None)
            self.assertTrue(ff1.status()["login"])         # not tried yet: expected to work
            ff1_signin.note_rejection(str(self.dir), "There was a problem with your password")
            st = ff1.status()
            self.assertFalse(st["login"])
            self.assertEqual(st["password"]["rejected"]["error"], "There was a problem with your password")
            self.assertEqual(ff1._record_auth(), "none")    # a recording goes straight to the free feed


LOG = """2026-11-07 23:28:15,048 - INFO: Starting FastF1 live timing client [v3.9.2]
2026-11-07 23:28:17,428 - WARNING: Failed to authenticate with Formula 1 account, continuing without authentication: There was a problem with your password or email address.
2026-11-07 23:28:18,253 - INFO: Connection established
""" + "".join(f"2026-11-07 23:{30 + i // 60:02d}:{i % 60:02d},000 - INFO: still recording\n" for i in range(20))


class RecorderStatus(TempDir):
    def test_sign_in_failure_found_anywhere_in_the_log(self):
        rec = ff1.Recorder()
        rec.file, rec.log, rec.meta_file = self.dir / "r.txt", self.dir / "r.log", self.dir / "r.json"
        rec.file.write_text("x" * 2000)
        rec.log.write_text(LOG)
        rec.meta = {"auth": "password", "file": str(rec.file)}
        st = rec.status()
        self.assertTrue(st["auth_failed"])
        self.assertEqual(st["auth"], "none")
        self.assertIn("problem with your password", st["auth_error"])
        self.assertEqual(json.loads(rec.meta_file.read_text())["auth"], "none")   # saved, for the recordings list

    def test_recordings_list_corrects_older_entries(self):
        with mock.patch.object(ff1, "REC_DIR", self.dir):
            (self.dir / "a.txt").write_text("x" * 2000)
            (self.dir / "a.log").write_text(LOG)
            (self.dir / "a.json").write_text(json.dumps({"auth": "password", "file": str(self.dir / "a.txt")}))
            self.assertEqual([r["auth"] for r in ff1.recordings()], ["none"])


class Logs(TempDir):
    def test_appends_with_a_marker_and_rotates(self):
        log = self.dir / "worker.log"
        log.write_text("why it failed last time\n")
        with ff1.open_log(log) as f:
            f.write("new run\n")
        text = log.read_text()
        self.assertTrue(text.startswith("why it failed last time"))
        self.assertIn(ff1.LOG_MARK, text)
        with mock.patch.object(ff1, "LOG_KEEP", 10):
            ff1.open_log(log).close()
        self.assertIn("new run", (self.dir / "worker.log.1").read_text())
        self.assertTrue(log.read_text().startswith(ff1.LOG_MARK))

    def test_error_message_skips_the_marker(self):
        w = ff1.Worker("t")
        w.log = self.dir / "w.log"
        w.log.write_text("ImportError: no fastf1\n=== 2026-11-08 started\n")
        self.assertEqual(w._stderr_tail(), "ImportError: no fastf1")


class Leftovers(unittest.TestCase):
    def test_only_orphans(self):
        py = str(ff1.VENV_PY)
        procs = [(100, 1, "/sbin/init"), (200, 100, "python3 -m f1hub gui"),
                 (300, 1, f"{py} -I -u -m fastf1 auth f1tv --authenticate --method browser"),       # orphaned
                 (400, 200, f"{py} -I -u -m fastf1 auth f1tv --authenticate --method browser"),     # a live dashboard's
                 (500, 1, f"{py} -I {ff1.RECORD} /x/rec.txt --timeout 1800"),
                 (600, 1, "python3 something_else.py")]
        self.assertEqual(ff1.leftovers(procs), {"sign_in": [300], "recorder": [500]})


class SignInTimeout(unittest.TestCase):
    def test_gives_up_without_anyone_watching(self):
        login = ff1.Login()
        login.TIMEOUT = 0.3
        login.proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        login.started = time.time()
        t0 = time.time()
        login._watchdog(login.proc)
        self.assertLess(time.time() - t0, 6)
        self.assertIsNotNone(login.proc.poll())
        self.assertEqual(login.status()["state"], "failed")
        self.assertIn("timed out", login.status()["error"])


if __name__ == "__main__":
    unittest.main()
