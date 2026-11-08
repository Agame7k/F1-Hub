"""Keeps a test module off the network and out of the real cache and recordings.

Each test module does `from isolation import setUpModule, tearDownModule`: while its tests run, OpenF1 is
offline (anything not mocked fails fast instead of hitting the network), nothing logs in with the credentials
in .openf1.env, and the cache and recordings go to a temporary folder.
Run the suite with `python3 -m unittest discover -s tests`, which puts this folder on the path.
"""
import tempfile
from pathlib import Path
from unittest import mock

from f1hub import api

REAL_AUTH_TOKEN = api._auth_token     # for the tests of the login itself (it never reaches OpenF1 there)
_active = []


def setUpModule():
    tmp = tempfile.TemporaryDirectory()
    patches = [mock.patch.object(api, "CACHE_DIR", Path(tmp.name) / "cache"),
               mock.patch.object(api, "RECORD_DIR", None),
               mock.patch.object(api, "RECORDINGS", Path(tmp.name) / "recordings"),
               mock.patch.object(api, "offline", True),
               mock.patch.object(api, "_auth_token", lambda: None)]      # never log in with real credentials
    for p in patches:
        p.start()
    _active.append((tmp, patches))


def tearDownModule():
    tmp, patches = _active.pop()
    for p in reversed(patches):
        p.stop()
    tmp.cleanup()
