"""How F1 Hub's FastF1 processes (the workers and the recorder, in the .venv) may sign in to F1 TV.

FastF1 signs in again before every request that has no token, and never remembers that a sign-in failed. With a
wrong F1_PASSWORD that meant one rejected sign-in for every file of every session loaded (nine for a qualifying
session), slowing each load and risking F1 locking the account. So here:

- A password F1 has rejected is not sent again until it changes. The rejection is noted in a small file in the
  FastF1 cache, shared by every process, which also lets the dashboard say what went wrong. It holds a salted
  hash to recognise the same credentials, never the password itself.
- A sign-in that failed for another reason (no network) is tried again after RETRY seconds.
- The browser method never starts a sign-in in these processes: it would print a link into a log nobody reads and
  then wait forever. The dashboard runs that sign-in (ff1.LOGIN); FastF1 stores the token, and these processes
  read it from disk.

Standard library only at import time: the dashboard (plain python3) reads the same file through this module.
"""
import hashlib
import json
import os
import time

RETRY = 300                       # s before trying again after a failure that wasn't a rejection
NOTE = "signin-rejected.json"
_ITERATIONS = 100_000


def _credentials():
    return os.environ.get("F1_USERNAME") or "", os.environ.get("F1_PASSWORD") or ""


def _fingerprint(salt):
    user, pw = _credentials()
    return hashlib.pbkdf2_hmac("sha256", f"{user}\0{pw}".encode(), bytes.fromhex(salt), _ITERATIONS).hex()


def note_path(cache_dir):
    return os.path.join(cache_dir, NOTE)


def rejected(cache_dir):
    """F1's rejection of the current F1_USERNAME / F1_PASSWORD, as {"error", "at"}, or None (not rejected, no
    credentials, or they've changed since)."""
    user, pw = _credentials()
    if not (user and pw):
        return None
    try:
        with open(note_path(cache_dir), encoding="utf-8") as f:
            note = json.load(f)
        if note.get("hash") == _fingerprint(note["salt"]):
            return {"error": note.get("error"), "at": note.get("at")}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def note_rejection(cache_dir, error):
    salt = os.urandom(16).hex()
    data = {"error": str(error)[:300], "at": time.time(), "salt": salt, "hash": _fingerprint(salt)}
    os.makedirs(cache_dir, exist_ok=True)
    tmp = note_path(cache_dir) + f".{os.getpid()}.part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, note_path(cache_dir))


def forget_rejection(cache_dir):
    """Let the password be tried again (the dashboard's "Try the password again")."""
    try:
        os.remove(note_path(cache_dir))
    except FileNotFoundError:
        pass


def _is_rejection(exc):
    """F1 turned the sign-in down (wrong password, no subscription), as opposed to not being reached at all."""
    try:
        import requests
    except ImportError:
        return True
    return not isinstance(exc.__cause__, requests.RequestException)


def install(cache_dir):
    """Apply the rules above to FastF1's sign-in in this process. Returns the method FastF1 would use."""
    try:
        from fastf1.exceptions import F1AuthError
        from fastf1.internals import f1account
    except ImportError:
        return None
    try:
        method = f1account.get_auth_method()
    except ValueError:
        method = "browser"
    manager = f1account.token_manager
    user, pw = _credentials()
    if method != "password" or not (user and pw):
        def refuse():
            raise F1AuthError("not signed in to F1 TV (sign in from Explore > FastF1 in F1 Hub)")
        manager._default_auth_func = refuse
        return method

    sign_in = manager._default_auth_func
    state = {"at": 0.0, "error": None, "rejected": False}

    def once():
        if state["rejected"]:
            if os.path.exists(note_path(cache_dir)):
                raise F1AuthError(state["error"])
            state.update(rejected=False, error=None)          # the dashboard said to try the password again
        elif state["error"] and time.time() - state["at"] < RETRY:
            raise F1AuthError(state["error"])
        note = rejected(cache_dir)
        if note:
            state.update(rejected=True, error=f"F1 rejected F1_USERNAME / F1_PASSWORD ({note['error']}); "
                                              "not sending them again until they change")
            raise F1AuthError(state["error"])
        try:
            return sign_in()
        except F1AuthError as e:
            if _is_rejection(e):
                try:
                    note_rejection(cache_dir, e)
                    state["rejected"] = True
                except OSError:
                    pass
            state.update(at=time.time(), error=str(e))
            raise
    manager._default_auth_func = once
    return method
