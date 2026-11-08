"""Records F1's live-timing feed with FastF1. Runs in the project's .venv (started by f1hub/ff1.py's Recorder).

  python ff1_record.py <file> [--timeout S] [--no-auth]

The same as `python -m fastf1.livetiming save`, plus:
- a workaround: FastF1 3.9 hands signalrcore `None` as the token factory when there's no login, which signalrcore
  rejects ("access_token_factory is not function"). Leaving the option out instead connects to F1's free feed;
- F1 Hub's sign-in rules (ff1_signin.py): no browser sign-in from here, and a password F1 has rejected isn't sent
  again;
- FastF1's cache in F1 Hub's folder (F1HUB_FF1_CACHE) rather than its default one.
"""
import argparse
import os
import sys

from signalrcore.hub_connection_builder import HubConnectionBuilder

sys.path.append(os.path.dirname(os.path.abspath(__file__)))     # (-I leaves this folder off the path)
import ff1_signin  # noqa: E402

CACHE = os.environ.get("F1HUB_FF1_CACHE") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                          ".f1cache", "fastf1")

_with_url = HubConnectionBuilder.with_url


def _with_url_no_none(self, url, options=None):
    if options and options.get("access_token_factory", 0) is None:
        options = {k: v for k, v in options.items() if k != "access_token_factory"}
    return _with_url(self, url, options=options)


HubConnectionBuilder.with_url = _with_url_no_none


def _negotiate_with_a_timeout():
    """FastF1's request before connecting has no timeout: if F1's server never answers, the recording hangs
    before it has begun. Give it one."""
    import fastf1.livetiming.client as client
    options = client.requests.options

    def options_with_timeout(url, **kwargs):
        kwargs.setdefault("timeout", 30)
        return options(url, **kwargs)
    client.requests.options = options_with_timeout


def main():
    p = argparse.ArgumentParser()
    p.add_argument("file")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--no-auth", action="store_true")
    a = p.parse_args()
    import fastf1
    try:
        os.makedirs(CACHE, exist_ok=True)
        fastf1.Cache.enable_cache(CACHE)
    except Exception:
        pass
    ff1_signin.install(CACHE)
    _negotiate_with_a_timeout()
    from fastf1.livetiming.client import SignalRClient
    SignalRClient(a.file, filemode="a", timeout=a.timeout, no_auth=a.no_auth).start()


if __name__ == "__main__":
    main()
