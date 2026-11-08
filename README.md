# F1 Hub

Real Formula 1 data in your terminal, from the [OpenF1 API](https://openf1.org/docs/). Nothing here is simulated: every result, lap time, tyre stint, pit stop, overtake, flag and telemetry sample comes from OpenF1 (2023 season onwards). The only thing the app works out for itself is the championship maths: points, tie-breaks, and when a title is mathematically won.

No dependencies, just Python 3.9+.

## Live race (needs your OpenF1 login)

```bash
python3 -m f1hub gui                     # live dashboard in your browser: http://localhost:8765
python3 -m f1hub live                    # same live timing in the terminal
```

The dashboard tabs:

| Tab | What it shows (all from OpenF1) |
|---|---|
| **Live timing** | Race clock and time left to the 3-hour limit, track status, lap X of Y, driver names, last-lap S1/S2/S3 in F1 colours (purple = session best, green = personal best, yellow = slower), live championship points, gaps and intervals, tyre and its age, pits, last and best laps (fastest in purple), places gained since the grid, overtakes, mini track map, weather, live title fight |
| **Race control** | Every message tagged and filterable (flags / safety car / stewards / session). Live marshal-sector flag board, SC/VSC/red-flag timeline, stewards' desk, and a per-driver record of deleted laps, black-and-white flags, investigations, penalties and blue flags |
| **Red flag panel** | How long the race has been suspended, a countdown to the announced restart, the official restart order, standing or SC start, pit exit status, and a **RACE RESUMED** banner with the stoppage length. Also catches "will not be resumed" and shows the reduced-points title outcome |
| **Track map** | Official circuit outline (MultiViewer data linked from OpenF1) at its real rotation, with corner numbers. **Marshal sectors are coloured by live flags**: yellow, flashing double yellow, green when just cleared, amber under SC/VSC, all red on a red flag. Cars sit on the track from live location data, with labels that don't overlap |
| **Insights** | Battles within 1 s, a "pit now" rejoin predictor using the circuit's published pit loss (green / SC / VSC), race pace over the last 5 clean laps, a lap chart (position every lap), theoretical best laps (best S1+S2+S3), speed traps (trap / I1 / I2), weather trend |
| **Strategy & pits** | Stint-by-stint tyre chart for the whole field, every pit stop (pit-lane and stationary time), live overtakes feed |
| **Gaps** | Gap-to-leader chart for the top 10, with SC/VSC/red-flag periods shaded |
| **Driver telemetry** | Click any driver: live speed / gear / RPM / throttle / brake with traces, lap-time chart, sector times, stewarding history |
| **Team radio** | Published radio clips you can play in the page |
| **Championship / Calendar** | Standings with who's still in contention, rounds and winners |

The poller is tiered to stay inside **60 requests/minute**:
- **Every 10 s:** position, intervals, race control, laps, location, and car data for the driver you've selected.
- **Every 30 s:** stints, pits, weather, overtakes and team radio.
- **Every 60 s:** championship standings.

### Real-time stream, priorities and no-spoiler mode

- **Real-time stream:** with a paid login, the GUI subscribes to OpenF1's MQTT stream (`mqtt.openf1.org:8883`, using your token as the password; the client is `f1hub/stream.py` and needs no extra packages). Timing and positions arrive within a few seconds and don't count against the 60 requests/minute limit, and the server refreshes every second. The header chip reads **REAL-TIME STREAM**, or **POLLING** when it falls back to REST.
- **Priority fetching:** each browser tab reports which dashboard tab it's showing. Whatever is on screen in any tab gets fetched most often, and data for hidden tabs slows down (`VIEW_NEEDS` and `CADENCE` in `live.py`). Switching tabs fetches straight away.
- **Fluid map:** every car comes with its recent position samples, about 4 a second, and the map animates between them. It runs a few seconds behind the timing so there's always a next point to move towards; that buffer adapts to OpenF1's measured latency.
- **⚙ Settings:**
  - **Delay** (0–600 s) matches your TV or stream; the quick-sync button sets it the moment the leader crosses the line.
  - If you ask for more delay than has been buffered, the picture holds still and a countdown shows when it starts moving, so nothing is spoiled.
  - **Spoiler mode** hides this race's result, standings and calendar winner while you're delayed or replaying.
  - **Replay** any race on a virtual clock, with a scrubber, flag markers, play/pause and 1–10× speed.
  - FastF1 (`pip install fastf1`) could be added later for deeper post-session telemetry. During a live race it reads the same F1 live-timing feed, so it isn't faster than the stream.

### More on the dashboard

- **Celebrations:**
  - The race winner gets a chequered flag, a podium and confetti. A newly decided World Champion gets a gold screen with a trophy, fireworks and the season stats counting up.
  - Each plays once, timed to your delay or replay. 🏆 in the header plays it again.
- **Championship tab:**
  - A points-progression chart; the dashed line shows "if this race finished now".
  - A title simulator: pick results for the leader and a rival over the remaining rounds.
  - Click any driver for a season card with their race-by-race results.
- **Strategy tab:**
  - Undercut watch: whether a fresh set beats the gap to the car ahead (an estimate).
  - Pit stop league, by driver and by team.
  - Tyre degradation: seconds lost per lap of tyre age on the current stint.
- **Driver telemetry tab:** head-to-head lap compare, covering speed, the gap along the lap, throttle and brake, and both drivers' braking zones on the map (`/api/lap`, `f1hub/laptrace.py`).
- **Track map:** 👻 a ghost of the session's fastest lap, running against your selected driver's current lap.
- **Qualifying mode:** gaps to pole and the Q1/Q2 drop-zone lines in the tower.
- **Alerts:** in-page pop-ups, with optional sound and desktop notifications, for:
  - flags
  - lead changes
  - fastest laps
  - penalties
  - retirements
  - rain
  - leaders pitting
- **Team radio:**
  - Clips play without being cut off by updates.
  - Filter by driver, play everything in order, or auto-play new messages from any tab.
  - Download one clip (⬇) or a zip of the session, optionally for one driver. The server fetches clips only from F1's team-radio files (`f1hub/radio.py`).
- **Radio speech-to-text:** every clip is transcribed on this computer with OpenAI's Whisper (`small.en` via faster-whisper), so it's free and private, and you can search what was said. It's optional; to set it up once:
  ```bash
  python3 -m venv .venv && .venv/bin/pip install faster-whisper
  ```
  - The first run downloads the model (~250 MB). Each clip takes ~3 s, newest first, and transcripts are cached in `.f1cache/radio/`.
  - Set `F1HUB_STT_MODEL=base.en` for a faster but rougher model.
  - Audio is decoded with the system `ffmpeg`.
- **⚠ Crash detection:** a big hazard-striped banner names the driver the moment a car crashes, with the corner, the speed it was doing (from its telemetry) and what race control has said since (yellow in that marshal sector, red flag, medical car). Also a pulsing marker on the map, a list on the Race control tab, and an alert (sound / desktop notification optional).
- **Provisional starting grid** (Live timing, in qualifying and practice): the qualifying order with the weekend's grid penalties applied in the order the Sporting Regulations set out (Article B2.5.4b). Penalties include PU-element drops, "back of the grid", pit-lane starts, and race penalties carried from the previous Grand Prix. Each penalty links to its FIA decision. While qualifying runs, it follows the live order. It's unofficial: the FIA publishes the real one 2 hours before the start.
- **Penalty points & reprimands** (Race control tab): every driver's live penalty points (12 in 12 months is a ban), when the next ones expire, and this season's reprimands. A warning shows one reprimand before the 10-place drop. Click a driver to see each decision. The first run reads the last 12 months of FIA decision PDFs in the background (a few minutes; fia.com rate-limits). They're cached for good after that.
- **Team radio alert words:** transcripts mentioning a pit call, damage, a car problem, rain, tyres or team orders get a coloured tag, a "Flagged only" filter and a pop-up alert. Edit the words under *Alert words* on the Team radio tab (one per line, `group: phrase`).
  - Detected from every car's position, ~4 times a second: racing speed to a standstill within a few seconds. Checked against real crashes (2024 São Paulo qualifying: Colapinto, Sainz, Stroll, Alonso, Albon - each caught 6-9 s before the red flag).
  - A car that drives off again becomes "spun"; a slow stop (beached, mechanical) only shows once race control flags that sector or names the car. Pit stops, red-flag periods and race starts are ignored (`f1hub/incidents.py`).
- **📄 The rule behind a penalty:** every stewards' message (race control, stewards' desk, a driver's record) has a 📄 Rule button. It finds the stewards' official decision on fia.com and shows:
  - the fact, the rule broken, the decision (penalty, points) and the stewards' reasons
  - the exact text of the article they cite, straight from the FIA regulations (2026: Sections A-F, e.g. B4.1.1; earlier: the Sporting Regulations; Appendix L of the International Sporting Code for driving offences), with any articles it refers to
  - links to the official decision PDF, the regulations PDF at that page, and all the event's documents
  - before the stewards publish anything, the articles whose wording best matches the message, clearly marked as a guess
  - in spoiler mode while delayed or replaying, nothing published after the moment you're watching
  - Reading the PDFs needs poppler's `pdftotext` (`sudo apt install poppler-utils`); everything is cached in `.f1cache/fia/` (`f1hub/fia.py`).
- **⧉ Mini tower:** a floating picture-in-picture timing tower to keep over your stream.
- **Layouts:** Standard, Compact, Large (TV) and Tower only.
- **Full replays:** any session (practice, qualifying, sprint and race) from any season OpenF1 has (2023 onwards). The title maths uses the standings as they were at that round, so a replay never crowns anyone early.

### Between sessions: the Home page

When nothing is on, the dashboard opens on **🏠 Home** (press `H` any time):
- a countdown to the next session, with the round and circuit, and the race time if that's a different session
- the weekend's schedule in your local time, plus a button that adds the whole weekend to your calendar (.ics)
- the latest race (and qualifying on Saturday night) as a podium and top 10. In spoiler mode the result
  and the championship stay blurred until you click "Show the result", so you can catch up on a replay first
- one-click full replays of the last few sessions, the championship top 5 and the next rounds

The server follows the "latest" session by itself: when FP1 starts it switches over without a restart.
Once a session has been over for 30 minutes it stops polling it every second, which leaves the API for the home page.

### Keyboard shortcuts and safety

Press `?` on the dashboard for the list: `H` home, `1`–`0` switch tabs, `[`/`]` step through them, `S` settings,
`M` mini tower, `Space` play/pause a replay, `←`/`→` seek 10 s (`Shift` for 1 min), `L` back to live,
`+`/`-` nudge the delay, `Esc` closes things. Your tab is remembered and is in the URL (`#map`, `#radio`, …),
so you can bookmark a view.

The local server only answers its own page: other websites can't change your settings or read the data
(cross-site requests and foreign hostnames get a 403). Bad parameters get a clear 400 instead of a crash,
season standings reload every 30 minutes, and `/api/health` reports what is connected. If one panel ever
fails to draw, the rest of the dashboard keeps working and a small note says which part failed; if the
server stops, the page shows a reconnect banner and picks up again when it's back.

### Recordings

Every live API response is saved to `recordings/<date>/<endpoint>.jsonl.gz`, one JSON line per request with the time, the request and the full response. This lets a race be replayed and studied later. Set `F1HUB_RECORD=0` to turn it off. It takes roughly 30-40 MB per race hour, mostly location data.

## Run it

```bash
python3 -m f1hub                         # interactive menu, current season
python3 -m f1hub standings               # drivers' + constructors' standings
python3 -m f1hub title                   # title fight, clinch watch, champion banner
python3 -m f1hub calendar                # rounds, winners, poles, what's next
python3 -m f1hub race 5                  # full race report for round 5 (or: race monaco)
python3 -m f1hub race china --sprint     # sprint report
python3 -m f1hub braking monza VER NOR   # braking telemetry on each driver's fastest lap
python3 -m f1hub h2h                     # teammate qualifying/race head-to-heads
python3 -m f1hub export season.json
python3 -m f1hub --year 2024 title       # any season from 2023
python3 -m f1hub --offline standings     # cached data only
```

## What it shows

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

## API access, limits and caching

- Historical data is free and needs no key. **While a live F1 session is running, OpenF1 locks all free access** (past seasons included) until the session ends. Data you've already fetched still works from the cache.
- If you have a paid OpenF1 account, set these on your own machine (not in any file you share):

  ```bash
  export OPENF1_USERNAME="you@example.com"
  export OPENF1_PASSWORD="..."
  ```

  Or put the same two lines (without `export`) in a `.openf1.env` file in the project folder. There's a template in `.openf1.env.example`.
  The app swaps them for an access token automatically. If you already have a token, `OPENF1_TOKEN` works too.
- Requests are rate limited client-side to **6 per second and 60 per minute**. On a 429 or a 5xx the client backs off exponentially and honours `Retry-After`.
- Responses are cached in `.f1cache/`. Results from sessions more than 4 days old are cached for good, and recent ones refresh every 15 minutes. Delete the folder to refetch everything.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Customising

- `f1hub/rules.py`: points tables, if the regulations change.
- `f1hub/display.py`: everything that gets printed.
