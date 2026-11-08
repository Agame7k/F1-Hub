"""Records F1's live-timing feed with FastF1. Runs in the project's .venv (started by f1hub/ff1.py's Recorder).

  python ff1_record.py <file> [--timeout S] [--no-auth]

The same as `python -m fastf1.livetiming save`, plus a workaround: FastF1 3.9 hands signalrcore `None` as the
token factory when there's no login, which signalrcore rejects ("access_token_factory is not function").
Leaving the option out instead connects to F1's free feed as intended.
"""
import argparse

from signalrcore.hub_connection_builder import HubConnectionBuilder

_with_url = HubConnectionBuilder.with_url


def _with_url_no_none(self, url, options=None):
    if options and options.get("access_token_factory", 0) is None:
        options = {k: v for k, v in options.items() if k != "access_token_factory"}
    return _with_url(self, url, options=options)


HubConnectionBuilder.with_url = _with_url_no_none


def _no_interactive_sign_in():
    """Like ff1_worker.no_interactive_sign_in: a browser sign-in would print its URL into the log and wait
    forever, so without a stored token (or a password login) the recorder uses F1's free feed instead."""
    import os
    from fastf1.exceptions import F1AuthError
    from fastf1.internals import f1account
    try:
        method = f1account.get_auth_method()
    except ValueError:
        method = "browser"
    if method == "password" and os.environ.get("F1_USERNAME") and os.environ.get("F1_PASSWORD"):
        return

    def refuse():
        raise F1AuthError("not signed in to F1 TV (sign in from Explore > FastF1 in F1 Hub)")
    f1account.token_manager._default_auth_func = refuse


def main():
    p = argparse.ArgumentParser()
    p.add_argument("file")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--no-auth", action="store_true")
    a = p.parse_args()
    _no_interactive_sign_in()
    from fastf1.livetiming.client import SignalRClient
    SignalRClient(a.file, filemode="a", timeout=a.timeout, no_auth=a.no_auth).start()


if __name__ == "__main__":
    main()
