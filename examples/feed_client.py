"""A minimal client for F1 Hub's data feed: prints the running order and race control as they change.

    python3 -m f1hub gui                # in one terminal
    python3 examples/feed_client.py     # in another (add --channels positions.VER to follow one car)

Standard library only. The feed is described in docs/feed.md.
"""
import argparse
import json
import urllib.request


def events(url):
    """Yield (channel, payload) from a Server-Sent Events stream."""
    with urllib.request.urlopen(url) as r:
        channel, data = None, []
        for raw in r:
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith("event: "):
                channel = line[7:]
            elif line.startswith("data: "):
                data.append(line[6:])
            elif not line and data:              # a blank line ends the event
                yield channel, json.loads("\n".join(data))
                channel, data = None, []


def gap(v):
    return "" if v is None else v if isinstance(v, str) else f"+{v:.3f}"


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--channels", default="session,leaderboard,race_control")
    args = p.parse_args()
    url = f"http://localhost:{args.port}/api/feed?channels={args.channels}"
    print(f"Listening on {url}  (Ctrl+C to stop)")
    try:
        for channel, data in events(url):
            if channel == "session":
                print(f"\n== {data['meeting']} - {data['session']} | lap {data['lap']}/{data['scheduled_laps']} | {data['status']}")
            elif channel == "leaderboard":
                top = [f"P{r['pos']} {r['code']} {gap(r['gap'])}".strip() for r in data if r["pos"]][:5]
                print("   " + "  ".join(top))
            elif channel == "race_control":
                print(f"   [RC {data['time']}] {data['text']}")
            else:
                print(f"   {channel}: {data}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
