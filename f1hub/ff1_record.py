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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("file")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--no-auth", action="store_true")
    a = p.parse_args()
    from fastf1.livetiming.client import SignalRClient
    SignalRClient(a.file, filemode="a", timeout=a.timeout, no_auth=a.no_auth).start()


if __name__ == "__main__":
    main()
