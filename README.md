# F1 Hub

Real Formula 1 data in your terminal and browser, from the [OpenF1 API](https://openf1.org/docs/) (2023 season onwards, and 2018-2022 with [FastF1](#optional-extras)).

All the data is real: every result, lap time, tyre stint, pit stop, overtake, flag and telemetry sample comes from OpenF1 or F1's own timing archive. On top of it the app works out the championship maths (points, tie-breaks, when a title is mathematically won) and a few clearly labelled estimates: the undercut watch, pit rejoin predictor, fuel-corrected tyre degradation, the provisional grid and crash detection.

The core needs nothing but Python 3.9+. Optional extras are listed [below](#optional-extras).

<!-- TODO: add a screenshot, e.g. ![F1 Hub dashboard](docs/screenshot.png) -->

## Quick start

No login needed:

```bash
python3 -m f1hub          # interactive menu: standings, title fight, race reports...
python3 -m f1hub gui      # browser dashboard at http://localhost:8765 (replays, Explore, Home)
```

For **live** timing during a session you need a paid OpenF1 account (see [Configuration](#configuration)):

```bash
python3 -m f1hub gui      # the dashboard switches to the live session by itself
python3 -m f1hub live     # the same live timing in the terminal
```

## What needs which access

| | Free (no login) | Paid OpenF1 | + F1 TV Pro login (FastF1) |
|---|---|---|---|
| Past seasons 2023+: CLI, replays, Explore | Yes (not while a session is live*) | Yes | Yes |
| Live timing and the real-time MQTT stream | | Yes | |
| 2018-2022 replays, analysis, season stats | with FastF1 installed | with FastF1 installed | Yes |
| FastF1 archive for 2023+ (smoother telemetry, fallback while OpenF1 is locked) | | | Yes |
| Full live-timing recordings, including car telemetry and positions | | | Yes |

\* While a live F1 session is running, OpenF1 locks all free access, past seasons included, until it ends. Data you've already fetched still works from the cache, and with FastF1 + an F1 TV login, past sessions switch over to FastF1.

## The dashboard

`python3 -m f1hub gui` opens a dashboard with these tabs:

| Tab | Highlights |
|---|---|
| **Home** | Countdown to the next session, the weekend in your local time (.ics export), the latest result (spoiler-safe), one-click replays |
| **Live timing** | Timing tower with sector colours, gaps, tyres, pits, live championship points, mini map, weather. Qualifying drop zones and a provisional grid with penalties applied |
| **Race control** | Every message tagged and filterable, marshal-sector flag board, SC/VSC/red-flag timeline, per-driver stewarding record, penalty points and reprimands, the exact FIA rule behind each penalty |
| **Red flag panel** | Suspension timer, restart countdown and order, reduced-points title outcome |
| **Track map** | Official circuit outline with marshal sectors coloured by live flags, cars animated from location data, a ghost of the fastest lap, battles, pit stops, purple sectors and start lights |
| **Insights** | Battles, pit-now rejoin predictor, race pace, lap chart, theoretical best laps, speed traps |
| **Strategy & pits** | Tyre stints for the whole field, pit stops, undercut watch, tyre degradation |
| **Gaps** | Gap-to-leader chart with neutralised periods shaded |
| **Driver telemetry** | Live speed / gear / RPM / throttle / brake, lap compare for up to 4 drivers (shift-click them in the timing tower) |
| **Team radio** | Playable clips, downloads, local speech-to-text with searchable transcripts and alert words |
| **Championship / Calendar** | Standings with who's still in contention, points progression, a title simulator |
| **Explore** | Session analysis, a telemetry lab for any two laps, season stats from 2018, circuit previews, FastF1 recordings |

Plus: crash detection, alerts (with optional sound and desktop notifications), a delay setting to match your TV, a **no-spoiler mode**, full replays on a virtual clock (¼× to 64×), a mini picture-in-picture tower, TV layouts and keyboard shortcuts (`?`).

**The full reference, covering every tab, the real-time stream, replays, FastF1 and shortcuts, is in [docs/dashboard.md](docs/dashboard.md).**

### Data feed for other programs

While the dashboard runs, `http://localhost:8765/api/feed` streams what it shows (live, delayed or a replay) as Server-Sent Events, by channel: `session`, `leaderboard`, `positions` (or `positions.VER` for one car), `race_control`, `weather` and `track`. Use it for stream overlays, a Stream Deck or a second screen. `examples/feed_client.py` is a 50-line client; [docs/feed.md](docs/feed.md) lists every field.

## Command line

```bash
python3 -m f1hub                         # interactive menu, current season
python3 -m f1hub standings               # drivers' + constructors' standings
python3 -m f1hub title                   # title fight, clinch watch, champion banner
python3 -m f1hub calendar                # rounds, winners, poles, what's next
python3 -m f1hub race 5                  # full race report for round 5 (or: race monaco)
python3 -m f1hub race china --sprint     # sprint report
python3 -m f1hub braking monza VER NOR   # braking telemetry on each driver's fastest lap
python3 -m f1hub h2h                     # teammate qualifying/race head-to-heads
python3 -m f1hub export season.json      # everything as JSON
python3 -m f1hub live                    # live timing in the terminal (paid OpenF1 login)
python3 -m f1hub gui                     # browser dashboard
python3 -m f1hub doctor                  # check the setup: logins, stream, FastF1, tools, disk, recent errors
python3 -m f1hub cleanup                 # show what disk space can be freed (add --yes to free it)
```

The command line covers OpenF1 seasons (2023 onwards); 2018-2022 are in the dashboard with FastF1.

| Option | Applies to | Meaning |
|---|---|---|
| `--year 2024` | all | Season to load (default: current) |
| `--offline` | all | Use cached data only |
| `--no-color` | all | Plain output (`NO_COLOR` works too) |
| `--sprint` | `race`, `braking` | Use the sprint instead of the Grand Prix |
| `--session KEY` | `live`, `gui` | OpenF1 `session_key` to follow (default: `latest`) |
| `--interval N` | `live`, `gui` | Seconds between REST refreshes (default 10, min 8) |
| `--once` | `live` | Print one snapshot and exit |
| `--port N` | `gui`, `doctor` | Port for the dashboard (default 8765) |
| `--no-browser` | `gui` | Don't open a browser tab |
| `--no-network` | `doctor` | Skip the checks that contact OpenF1 |
| `--yes` | `cleanup` | Delete what it lists (without it, nothing is deleted) |
| `--recordings-older-than DAYS` | `cleanup` | Also clear recordings of sessions older than that |

### What the reports show

| Feature | Where the data comes from |
|---|---|
| Points and standings (GP 25-18-15..., sprint 8-7-6...) | `session_result`, checked against the official `championship_drivers` / `championship_teams` |
| Poles, wins, podiums, DNFs | `session_result` (race, sprint, qualifying) |
| Track positions: grid vs finish, positions gained, lead changes | `starting_grid`, `position`, `overtakes` |
| Tyres: compound per stint, stint lengths, used sets, degradation in s/lap | `stints` + `laps` (fitted on clean laps only, with SC/VSC laps left out) |
| Pit stops: number of stops, fastest stationary time | `pit` |
| Braking: brake zones, entry vs minimum speed, time on the brakes, approximate g, top speed | `car_data` (~3.7 Hz) |
| Safety cars, VSCs, red flags, penalties, investigations | `race_control` |
| Weather: rain, track/air temperature, humidity | `weather` |

### When the champion is declared

After every round, the app checks every rival against the leader:

- A rival is **eliminated** once their points plus every point still available (25 per GP, plus 8 per sprint weekend; 43 and 15 for constructors) can't reach the leader.
- If the best a rival can do is **tie on points**, the countback decides it (most wins, then most 2nds, and so on), assuming the rival wins every remaining race.
- Once nobody else can catch the leader, they're **declared World Champion**, along with the round where it was clinched and how many rounds were left to spare.
- Before that happens, **Clinch watch** tells you how many points the leader has to outscore P2 by at the next round to seal it.
- The **title timeline** shows who was still mathematically in the fight after each round.

## Optional extras

Everything works without these; each one unlocks a feature.

| Extra | Install | Unlocks |
|---|---|---|
| FastF1 | `python3 -m venv .venv && .venv/bin/pip install fastf1` | 2018-2022 seasons, smoother lap telemetry, a fallback while OpenF1 is locked, live-timing recordings ([details](docs/dashboard.md#fastf1-and-recording-the-live-timing-feed)) |
| faster-whisper | `python3 -m venv .venv && .venv/bin/pip install faster-whisper` | Local, private team-radio transcripts (the model downloads ~250 MB on first run) |
| ffmpeg | `sudo apt install ffmpeg` | Decoding radio audio for transcription |
| poppler | `sudo apt install poppler-utils` | Reading FIA decision and regulation PDFs (the Rule button, penalty points) |

FastF1 and Whisper both run in the project's `.venv` as separate worker processes, so the main app stays dependency-free.

## Configuration

Credentials go in a `.openf1.env` file in the project folder (copy `.openf1.env.example`; it's git-ignored), or in your environment. Keep them out of any file you share.

```
OPENF1_USERNAME=you@example.com   # paid OpenF1 account: live timing + MQTT stream
OPENF1_PASSWORD=...
F1_USERNAME=you@example.com       # optional F1 TV Pro account, for FastF1
F1_PASSWORD=...
```

Restart F1 Hub after changing them. All the settings:

| Variable | Default | Meaning |
|---|---|---|
| `OPENF1_USERNAME`, `OPENF1_PASSWORD` | | Paid OpenF1 login; swapped for an access token automatically |
| `OPENF1_TOKEN` | | Use an OpenF1 token you already have instead |
| `F1_USERNAME`, `F1_PASSWORD` | | F1 TV Pro login for FastF1 (2023+ archive, full recordings) |
| `FASTF1_AUTH_METHOD` | | Set to `browser` to ignore `F1_USERNAME`/`F1_PASSWORD` and use only the browser sign-in (Explore > FastF1 > *Sign in to F1 TV*) |
| `F1HUB_RECORD` | on | `0` turns off saving live API responses and the MQTT stream (saved recordings still play back) |
| `F1HUB_RECORD_DIR` | `recordings/` | Where recordings go |
| `F1HUB_CACHE` | `.f1cache/` | Where API responses, FastF1 data, FIA documents, radio clips, transcripts and the log file are kept |
| `F1HUB_STT_MODEL` | `small.en` | Whisper model; `base.en` is faster but rougher |
| `F1HUB_STT_THREADS` | `4` | CPU threads for transcription |
| `NO_COLOR` | | Plain terminal output |

## Data, limits and storage

- **Rate limits:** requests are limited client-side to **6 per second and 60 per minute**. On a 429 or a 5xx the client backs off exponentially and honours `Retry-After`. During a live session the dashboard spreads its polling across those 60 a minute ([details](docs/dashboard.md#polling-budget)); the MQTT stream doesn't count against it.
- **Cache:** responses are cached in `.f1cache/`. Results from sessions more than 4 days old are cached for good, and recent ones refresh every 15 minutes. `python3 -m f1hub cleanup` lists what can safely go (and how much space each part of the cache uses); delete the folder to refetch everything.
- **Recordings:** during live sessions, every API response is saved to `recordings/openf1/<session>/<endpoint>.jsonl.gz` (one JSON line per request: the time, the request and the full response), and every MQTT message to `stream.jsonl.gz` in the same folder with its arrival time. Recording stops by itself once the session is over. Recorded sessions play back like any other replay, and Explore > FastF1 & recordings shows how late the data reached you (*Delays*). Expect roughly 30-40 MB per race hour. Recordings from older versions, in `recordings/<date>/`, still play back. FastF1 recordings go to `recordings/fastf1/`. Set `F1HUB_RECORD=0` to turn recording off.
- **Logs:** warnings and errors go to the terminal as one line each, and in full (with details) to `.f1cache/logs/f1hub.log`, rotated at 1 MB with three older copies kept.

## When something isn't working

- Click **Status** in the dashboard's header (or press `D`). It checks the settings, the OpenF1 login, the season data, the live feed and the stream, recording, FastF1 and the F1 TV sign-in, the tools (ffmpeg, poppler, faster-whisper), disk space and leftover sign-in windows, and lists the latest warnings. The button turns amber or red when something needs a look, and a banner says when a changed `.openf1.env` needs a restart.
- Run `python3 -m f1hub doctor` for the same checks from the terminal, with or without the dashboard running. It also logs in to OpenF1, tries the stream and checks that the dashboard's port is free (`--no-network` skips contacting OpenF1). It shows only the names of the settings in `.openf1.env`, never their values.
- The full detail of any error is in `.f1cache/logs/f1hub.log`.

## Project layout

| Path | What it does |
|---|---|
| `f1hub/__main__.py` | Command line and interactive menu |
| `f1hub/api.py` | OpenF1 client: auth, rate limiting, caching, recording |
| `f1hub/season.py`, `championship.py`, `rules.py` | Season loading, points tables (edit `rules.py` if the regulations change), title maths |
| `f1hub/report.py`, `display.py` | Race reports and everything the terminal prints |
| `f1hub/live.py`, `stream.py` | Live poller (`VIEW_NEEDS`, `CADENCE`) and the MQTT client |
| `f1hub/gui.py`, `dashboard.html`, `web/` | Dashboard server, the page, and its scripts and styles |
| `f1hub/datafeed.py`, `examples/feed_client.py` | The data feed for other programs (`/api/feed`) and an example client |
| `f1hub/home.py`, `replay.py`, `quali.py` | Home page, replays on a virtual clock, the qualifying picture (Q1/Q2/Q3, knockouts) |
| `f1hub/racecontrol.py` | Race control messages: categories, sector flags, timeline |
| `f1hub/stewards.py`, `fia.py` | Provisional grid, penalty points and reprimands; FIA decisions and regulation text |
| `f1hub/incidents.py` | Crash detection |
| `f1hub/laptrace.py`, `analysis.py` | Lap compare, the Explore tab's analysis |
| `f1hub/radio.py`, `sting.py`, `transcribe.py`, `stt_worker.py` | Team radio, intro-sting skipping, speech-to-text |
| `f1hub/ff1.py`, `ff1_worker.py`, `ff1_record.py`, `ff1_signin.py` | FastF1 bridge, worker process, recorder and the F1 TV sign-in |
| `f1hub/recordings.py` | Live recordings: writing, playback and the delay figures |
| `f1hub/logs.py`, `doctor.py`, `cleanup.py` | Logging, the setup check, freeing disk space |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline with a temporary cache and never read your credentials. GitHub Actions runs them on Python 3.9, 3.12 and 3.14 (`.github/workflows/tests.yml`), and syntax-checks the dashboard scripts.

## Credits and disclaimer

F1 Hub is an unofficial fan project. It isn't associated with Formula 1, the FIA or F1 TV. F1, Formula 1 and related marks belong to Formula One Licensing B.V.

Data comes from [OpenF1](https://openf1.org/), [FastF1](https://docs.fastf1.dev/), the [Jolpica-Ergast](https://github.com/jolpica/jolpica-f1) archive, circuit outlines from [MultiViewer](https://multiviewer.app/), and FIA documents from [fia.com](https://www.fia.com/).

## License

The code is under the [MIT License](LICENSE). It doesn't cover the data, documents or radio clips the app fetches; those belong to their owners.
