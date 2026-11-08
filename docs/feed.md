# Data feed for other programs

While the dashboard is running (`python3 -m f1hub gui`), it streams what it shows to other programs at:

```
http://localhost:8765/api/feed?channels=leaderboard,race_control
```

You can use it for a stream overlay, a Stream Deck button, a second-screen app or your own analysis script. The feed follows the dashboard's own view: live, delayed to match your TV, or a replay on its virtual clock (seeks, pauses and speed changes included). Two programs reading the feed always see the same moment as the page.

## The protocol

The feed is [Server-Sent Events](https://developer.mozilla.org/docs/Web/API/Server-sent_events): a long-lived HTTP response of `text/event-stream`. Each event is named after its channel, and its `data` line holds the payload as JSON:

```
event: session
data: {"mode":"replay","meeting":"Mexico City Grand Prix","session":"Race","status":"SC","lap":34,...}

event: race_control
data: {"lap":34,"time":"21:14:07","category":"SafetyCar","flag":null,"text":"SAFETY CAR DEPLOYED"}
```

- On connect you get every channel you asked for once. After that, a channel sends an event only when its payload changes.
- `race_control` sends one event per message. On connect it sends the recent ones, oldest first. A seek or a new session sends them again.
- A comment line (`: keep-alive`) arrives after 15 s with no events.
- Up to 8 feed clients can connect at once. These are counted separately from the dashboard's own connections.

From a browser, use `new EventSource(url)` with `addEventListener("leaderboard", e => JSON.parse(e.data))`. From Python, see [`examples/feed_client.py`](../examples/feed_client.py), which uses only the standard library.

## Channels

Leave out `channels` to get all of them. An unknown channel returns a 400 with the list of valid ones.

| Channel | Payload |
|---|---|
| `session` | `mode` (`live` / `replay`), `session_key`, `meeting`, `circuit`, `session`, `status` (`GREEN`, `SC`, `VSC`, `RED`, `CHEQUERED`), `lap`, `scheduled_laps`, `delayed_by` (seconds, when delayed), `replay` (`{playing, speed}` or null) |
| `leaderboard` | The running order, a list of `pos`, `number`, `code`, `name`, `team`, `colour`, `gap`, `interval`, `lap`, `last_lap`, `best_lap`, `fastest`, `compound`, `tyre_age`, `pits`, `grid`, `out` (the reason a car retired, or null). Gaps are seconds, or a string such as `"+1 LAP"` |
| `positions` | Every car's newest map position: `number`, `code`, `pos`, `colour`, `x`, `y` (OpenF1 units, tenths of a metre) and `t` (epoch ms of that sample) |
| `positions.<CODE>` | One car, by its three-letter code (`positions.VER`) or car number (`positions.1`) |
| `race_control` | One message: `lap`, `time` (UTC, `HH:MM:SS`), `category`, `flag`, `text` |
| `weather` | `track`, `air` (°C), `rain`, `humidity`, `wind` |
| `track` | `status` and `sector_flags` (marshal sector number to `YELLOW` / `DOUBLE YELLOW`) |

Positions update a few times a second while cars are moving, so ask for `positions` only if you use it.

There is no full-field car telemetry (speed, throttle, gear) channel. OpenF1's rate limits only allow the dashboard to fetch it for the car you've selected.

## Using it from a web page elsewhere

By default the server only answers its own page, so another site can't read your data (see [Server safety](dashboard.md#server-safety-and-resilience)). Programs that aren't browsers (scripts, apps, curl) aren't affected. A web page from another origin, such as an OBS browser-source overlay opened as a local file, also needs:

```bash
python3 -m f1hub gui --open-feed
```

This lets any page read `/api/feed`, and only `/api/feed`, with `Access-Control-Allow-Origin: *`. The feed is read-only, and every other route still turns other sites away.
