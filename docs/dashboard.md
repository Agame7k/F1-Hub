# Dashboard reference

The full guide to the browser dashboard (`python3 -m f1hub gui`). For setup, see the [README](../README.md).

- [Live tabs](#live-tabs)
- [Real-time stream, priorities and no-spoiler mode](#real-time-stream-priorities-and-no-spoiler-mode)
- [Championship, strategy and telemetry extras](#championship-strategy-and-telemetry-extras)
- [Alerts and crash detection](#alerts-and-crash-detection)
- [Team radio and speech-to-text](#team-radio-and-speech-to-text)
- [Stewards, penalties and the rulebook](#stewards-penalties-and-the-rulebook)
- [Home page](#home-page)
- [Explore tab](#explore-tab)
- [FastF1 and recording the live-timing feed](#fastf1-and-recording-the-live-timing-feed)
- [Live OpenF1 recordings](#live-openf1-recordings)
- [Status and troubleshooting](#status-and-troubleshooting)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Data feed for other programs](feed.md)
- [Server safety and resilience](#server-safety-and-resilience)

## Live tabs

| Tab | What it shows (all from OpenF1) |
|---|---|
| **Live timing** | Race clock and time left to the 3-hour limit, track status, lap X of Y, driver names, last-lap S1/S2/S3 in F1 colours (purple = session best, green = personal best, yellow = slower), live championship points, gaps and intervals, tyre and its age, pits, last and best laps (fastest in purple), places gained since the grid, overtakes, mini track map, weather, live title fight |
| **Race control** | Every message tagged and filterable (flags / safety car / stewards / session). Live marshal-sector flag board, SC/VSC/red-flag timeline, stewards' desk, and a per-driver record of deleted laps, black-and-white flags, black flags, investigations, penalties and blue flags |
| **Red flag panel** | How long the race has been suspended, a countdown to the announced restart, the official restart order, standing or SC start, pit exit status, and a **RACE RESUMED** banner with the stoppage length. Also catches "will not be resumed" and shows the reduced-points title outcome |
| **Track map** | Official circuit outline (MultiViewer data linked from OpenF1) at its real rotation, with corner numbers. **Marshal sectors are coloured by live flags**: yellow, flashing double yellow, green when just cleared, amber under SC/VSC, all red on a red flag. A chequered start/finish line and an arrow for the direction of travel. Cars glide along the track from live location data, with labels that don't overlap. **Click a car** (or a name in the running order) to select it without leaving the map: it gets a pulsing ring, and the ghost button races the session's fastest lap against its current lap. Click it again to let go. **3D view** (the 2D / 3D switch): the circuit at its real elevation (taken from the cars' own position data, stretched with the Height slider so the hills show), with an orbiting camera that spins slowly when left alone, or a camera that rides behind the selected car (or the leader). Drag to rotate, scroll to zoom, double-click to reset, click a car to select it. An elevation profile of the lap shows where every car is on the climbs. The choice is remembered. In 3D, each car also has a tail, a nose pointing the way it's going, and a tyre ring. See [Race moments on the map](#race-moments-on-the-map) for what else is drawn |
| **Insights** | Battles within 1 s, a "pit now" rejoin predictor using the circuit's published pit loss (green / SC / VSC), race pace over the last 5 clean laps, a lap chart (position every lap), theoretical best laps (best S1+S2+S3), speed traps (trap / I1 / I2), weather trend |
| **Strategy & pits** | Stint-by-stint tyre chart for the whole field, every pit stop (pit-lane and stationary time), live overtakes feed |
| **Gaps** | Gap-to-leader chart for the top 10, with SC/VSC/red-flag periods shaded |
| **Driver telemetry** | Click any driver: live speed / gear / RPM / throttle / brake with traces, lap-time chart, sector times, stewarding history |
| **Team radio** | Published radio clips you can play in the page |
| **Championship / Calendar** | Standings with who's still in contention, rounds and winners |

Other views:
- **Qualifying mode:** gaps to pole and the Q1/Q2 drop-zone lines in the tower.
- **Provisional starting grid** (Live timing, in qualifying and practice): the qualifying order with the weekend's grid penalties applied in the order the Sporting Regulations set out (Article B2.5.4b). Penalties include PU-element drops, "back of the grid", pit-lane starts, and race penalties carried from the previous Grand Prix. Each penalty links to its FIA decision. While qualifying runs, it follows the live order. *Unofficial:* the FIA publishes the real one 2 hours before the start.
- **Mini tower:** a floating picture-in-picture timing tower to keep over your stream.
- **Layouts:** Standard, Compact, Large (TV) and Tower only.
- **Celebrations:** the race winner gets a chequered flag, a podium and confetti. A newly decided World Champion gets a gold screen with a trophy, fireworks and the season stats counting up. A newly decided Constructors' Champion gets a screen in the team's colours with both drivers, the points, wins and winning margin. Each plays once, timed to your delay or replay; the trophy button in the header plays it again. The constructors' title is worked out live: the teams' points before the weekend plus what each driver is scoring now, credited to the team they drive for today.

### Race moments on the map

Drawn on the 2D map on the map's own clock (a few seconds behind the timing), so they line up with the cars (`f1hub/web/racefx.js`):

- **Tails and tyre rings:** a short fading tail behind each car, and a ring in its tyre colour (hover it for the compound and age). Cars are drawn where their position data puts them, not snapped to the track, so pit stops and cars going off still show.
- **Battles:** a dashed line between cars within 1 s of each other, with the interval. Racing only (not under SC/VSC, red flag or on lap 1).
- **Places gained:** a ring and "+1 P4" on a car when it moves up. Gains from cars that pitted or retired don't count.
- **Pit stops:** "PIT LANE" while a car is at the limiter beside the track, a stationary-time counter while it's stopped, then the stop time and its new tyre. The counter is measured from position data (shown with ~); OpenF1's official stationary time replaces it once published.
- **Purple sectors:** the part of the track lights up purple with the driver's code. The S1/S2/S3 boundaries aren't in the data, so the map learns them from where cars were when each sector ended; it needs about a lap of position history first.
- **Selected car:** a readout beside it (speed, gear, throttle, brake). The **Track / Speed / Throttle / Brake** switch colours the track with its last completed lap, and with the ghost on, the track between the car and the ghost shows green when it's ahead, red when behind.
- **Chequered flag:** waves at the line when the session ends.
- **Safety car** (2D and 3D): OpenF1 doesn't publish the safety car's position, so it's drawn on the track's centre line ahead of the leader: 500 m when it comes out, closing to about 150 m as the field bunches up. It fades in with a pulsing glow ("SC DEPLOYING"), shows "SC IN" once race control says it's in this lap, and peels off into the pit lane when it reaches the line. A red flag takes it off the map. It's an illustration of where it is, not a measurement.
- **Show** buttons over the map switch each layer on or off: driver names (also `N`; the selected car keeps its label), trails and tyre rings, battles, pit stops and the safety car. The choice is remembered.

Across the page, on any tab (race and sprint):

- **Start lights:** when the field stops on the grid after driving onto it, five red lights come on a second apart, then go out the moment the cars launch. Position data runs a few seconds ahead of what's on screen, so the last light is timed to land just before the launch. It doesn't trigger before the formation lap, or when you open the page onto cars already parked. Standing restarts after a red flag get lights too.
- **Banners:** safety car, virtual safety car, red flag, green flag after a neutralisation, fastest lap, and final lap.

**Test mode** (Settings → Test mode) shows a panel of buttons that plays each of these on demand using the loaded session: start lights, the banners (the safety car one also sends the safety car round the map for 20 s), an overtake, a battle, a pit stop, purple sectors, the chequered flag, selecting the leader (readout and speed colours), and the race win, World Champion and Constructors' Champion celebrations (marked "Test mode - not a real result"). Map effects open the Track map and need cars on it, so load a live session or a replay first. It only changes what's on your screen; nothing is sent to the server. The choice is remembered (`f1hub/web/demo.js`).

### Polling budget

The poller is tiered to stay inside **60 requests/minute**:
- **Every 10 s:** position, intervals, race control, laps, location, and car data for the driver you've selected.
- **Every 30 s:** stints, pits, weather, overtakes and team radio.
- **Every 60 s:** championship standings.

## Real-time stream, priorities and no-spoiler mode

- **Real-time stream:** with a paid login, the GUI subscribes to OpenF1's MQTT stream (`mqtt.openf1.org:8883`, using your token as the password; the client is `f1hub/stream.py` and needs no extra packages). Timing and positions arrive within a few seconds and don't count against the 60 requests/minute limit, and the server refreshes every second. The header chip reads **REAL-TIME STREAM**, or **POLLING** when it falls back to REST.
- **Pushed to the page:** a streamed timing message (positions, intervals, laps, race control, pits...) rebuilds the dashboard within ~0.1 s, at most 4 times a second, and the server pushes each frame to the browser over `/api/events` (Server-Sent Events). If that can't connect (an old browser, or more than 4 dashboard tabs open), the page polls once a second instead. Hover the chip to see OpenF1's own measured delay.
- **Priority fetching:** each browser tab reports which dashboard tab it's showing. Whatever is on screen in any tab gets fetched most often, and data for hidden tabs slows down (`VIEW_NEEDS` and `CADENCE` in `live.py`). Switching tabs fetches straight away.
- **Fluid map:** every car comes with its recent position samples, about 4 a second, and the map animates between them. It runs a few seconds behind the timing so there's always a next point to move towards; that buffer adapts to OpenF1's measured latency.
- **Settings:**
  - **Delay** (0–600 s) matches your TV or stream; the quick-sync button sets it the moment the leader crosses the line.
  - If you ask for more delay than has been buffered, the picture holds still and a countdown shows when it starts moving, so nothing is spoiled.
  - **Spoiler mode** hides this race's result, standings and calendar winner while you're delayed or replaying.
  - **Replay** any race on a virtual clock, with a scrubber, flag markers, play/pause and ¼× to 64× speed (<kbd>+</kbd> / <kbd>-</kbd> step through them).
- **Full replays:** any session (practice, qualifying, sprint and race) from any season OpenF1 has (2023 onwards), or 2018–2022 with FastF1. The title maths uses the standings as they were at that round, so a replay never crowns anyone early.

## Championship, strategy and telemetry extras

- **Championship tab:**
  - A points-progression chart; the dashed line shows "if this race finished now".
  - A title simulator: pick results for the leader and a rival over the remaining rounds.
  - Click any driver for a season card with their race-by-race results.
- **Strategy tab** (these are estimates built on real data):
  - Undercut watch: whether a fresh set beats the gap to the car ahead.
  - Pit stop league, by driver and by team.
  - Tyre degradation: seconds lost per lap of tyre age on the current stint.
- **Driver telemetry tab:** lap compare for 2 to 4 drivers (each on their best or last lap), covering speed, the gap along the lap to the first driver, throttle and brake, and everyone's braking zones on the map (`/api/lap`, `f1hub/laptrace.py`). **Shift-click** rows in the timing tower to pick drivers; a bar at the bottom of the page lists them, and *Compare laps* opens them here. A driver whose team colour is already taken gets a spare colour.

## Alerts and crash detection

**Alerts:** in-page pop-ups, with optional sound and desktop notifications, for flags, lead changes, fastest laps, penalties, retirements, rain, leaders pitting, crashes and flagged team-radio words.

**Crash detection:** a big hazard-striped banner names the driver the moment a car crashes, with the corner, the speed it was doing (from its telemetry) and what race control has said since (yellow in that marshal sector, red flag, medical car). Also a pulsing marker on the map, a list on the Race control tab, and an alert.
- Detected from every car's position, ~4 times a second: racing speed to a standstill within a few seconds. Checked against real crashes (2024 São Paulo qualifying: Colapinto, Sainz, Stroll, Alonso, Albon - each caught 6-9 s before the red flag).
- A car that drives off again becomes "spun"; a slow stop (beached, mechanical) only shows once race control flags that sector or names the car. Pit stops, red-flag periods and race starts are ignored (`f1hub/incidents.py`).

## Team radio and speech-to-text

- Clips play without being cut off by updates.
- Filter by driver, play everything in order, or auto-play new messages from any tab.
- Download one clip or a zip of the session, optionally for one driver. The server fetches clips only from F1's team-radio files (`f1hub/radio.py`).
- **Speech-to-text:** every clip is transcribed on this computer with OpenAI's Whisper (`small.en` via faster-whisper), so it's free and private, and you can search what was said. Optional; set it up once with:
  ```bash
  python3 -m venv .venv && .venv/bin/pip install faster-whisper
  ```
  - Needs the system `ffmpeg` to decode audio.
  - The first run downloads the model (~250 MB). Each clip takes ~3 s, newest first, and transcripts are cached in `.f1cache/radio/`.
  - Set `F1HUB_STT_MODEL=base.en` for a faster but rougher model, and `F1HUB_STT_THREADS` for the CPU threads (default 4).
- **Alert words:** transcripts mentioning a pit call, damage, a car problem, rain, tyres or team orders get a coloured tag, a "Flagged only" filter and a pop-up alert. Edit the words under *Alert words* on the Team radio tab (one per line, `group: phrase`).

## Stewards, penalties and the rulebook

- **The rule behind a penalty:** every stewards' message (race control, stewards' desk, a driver's record) has a Rule button. It finds the stewards' official decision on fia.com and shows:
  - the fact, the rule broken, the decision (penalty, points) and the stewards' reasons
  - the exact text of the article they cite, straight from the FIA regulations (2026: Sections A-F, e.g. B4.1.1; earlier: the Sporting Regulations; Appendix L of the International Sporting Code for driving offences), with any articles it refers to
  - links to the official decision PDF, the regulations PDF at that page, and all the event's documents
  - before the stewards publish anything, the articles whose wording best matches the message, clearly marked as a guess
  - in spoiler mode while delayed or replaying, nothing published after the moment you're watching
  - Reading the PDFs needs poppler's `pdftotext` (`sudo apt install poppler-utils`); everything is cached in `.f1cache/fia/` (`f1hub/fia.py`).
- **Penalty points & reprimands** (Race control tab): every driver's live penalty points (12 in 12 months is a ban), when the next ones expire, and this season's reprimands. A warning shows one reprimand before the 10-place drop. Click a driver to see each decision. The first run reads the last 12 months of FIA decision PDFs in the background (a few minutes; fia.com rate-limits). They're cached for good after that.

## Home page

When nothing is on, the dashboard opens on **Home** (press `H` any time):
- a countdown to the next session, with the round and circuit, and the race time if that's a different session
- the weekend's schedule in your local time, plus a button that adds the whole weekend to your calendar (.ics)
- the latest race (and qualifying on Saturday night) as a podium and top 10. In spoiler mode the result and the championship stay blurred until you click "Show the result", so you can catch up on a replay first
- one-click full replays of the last few sessions, the championship top 5 and the next rounds
- a compact preview of the next circuit and shortcuts into Explore

The server follows the "latest" session by itself: when FP1 starts it switches over without a restart. Once a session has been over for 30 minutes it stops polling it every second, which leaves the API for the home page.

## Explore tab

**Explore** (press `E`) is for digging into anything that has already happened:

| View | What it shows |
|---|---|
| **Session analysis** | Any finished session: race pace as box plots (clean laps only: no lap 1, pit laps, SC/VSC/red-flag laps or slow outliers), lap times for the drivers you pick, a lap chart, tyre stints, best laps with the ideal lap (best three sectors) and speed trap, team-mate gaps on one lap and on race pace, and tyre degradation per stint, raw and fuel-corrected (adds back an estimated 0.06 s/lap of fuel burn) |
| **Telemetry lab** | Any two laps of any session (not just the one on screen): speed, the gap along the lap, throttle, brake, gear and (FastF1) RPM against distance, plus a map of who's quicker in each of 25 mini-sectors |
| **Season stats** | Any season from 2018 (2018-2022 need FastF1): points, wins, podiums, poles, average qualifying and finishing positions, places gained from the grid, Q3 appearances, points per round, last-5 form, points progression for drivers and constructors, and team-mate head-to-heads in qualifying and races. Click a driver for their season card |
| **Season report** | Season-wide analysis from the compiled season dataset ([dataset.md](dataset.md)): race pace and qualifying pace for every team round by round (% off the quickest, click teams to highlight them), team-mate qualifying gaps, race craft (on-track overtakes made and lost, laps led, lap-1 places, grid to flag, sortable), tyre compound mix and stint lengths, pit-stop times per team (every stop as a dot), pit-lane time per circuit, Safety Cars and weather at every race. **Compile season** / **Update** builds the dataset in the background; spoiler mode leaves out the meeting you're replaying. Link straight to it with `#explore/report` |
| **Circuit preview** | The next round (or any round this season): the track with corner numbers, pit-stop loss (green / SC / VSC), and every past race there with podium, where the winner started, pole, fastest lap, the winner's tyres, safety cars, overtakes and track temperature. Also how many stops cars made and the most-used tyre orders. With FastF1, winners back to 2018 too, each one a click from a replay |
| **FastF1 & recordings** | FastF1's status and the F1 TV sign-in, the live-timing recorder and its recordings, and your [live OpenF1 recordings](#live-openf1-recordings) with how late their data arrived |

In spoiler mode, a session from the last few days (and this season's stats, while the latest race is still hidden) stays covered until you reveal it. That's the same switch as on Home, so revealing in one place reveals in both. While you're replaying a session, its analysis and lab only use what had happened by the replay clock.

## FastF1 and recording the live-timing feed

[FastF1](https://docs.fastf1.dev/) reads F1's own live-timing archive. It needs pandas, so it runs in the project's `.venv` in a separate worker process, the same way as the radio speech-to-text, and everything still works without it.

What it adds:
- **2018-2022** (OpenF1 starts in 2023) in replays, session analysis, the telemetry lab and season stats (results from the Jolpica-Ergast archive). Replays of these seasons run on the same tracker as OpenF1 replays, without team radio or overtakes.
- **Smoother lap telemetry** in the lab: car and position data merged on one timeline, distance in metres, RPM and DRS. Choose *FastF1* as the telemetry source (it's automatic for 2018-2022).
- **A stand-in while OpenF1 is locked.** During a live session OpenF1 blocks free access to everything; replays and analysis of past sessions then switch to FastF1 by themselves.
- **Recording the live-timing feed** (Explore > FastF1 > *Start recording now*). This saves everything F1's feed sends (timing, positions, car telemetry, race control, weather) to `recordings/fastf1/`. FastF1 can't process it in real time, so live timing stays on OpenF1; recordings are for playing back afterwards, or part-way through to see what has been recorded so far. Each recording appears in the list with *Play back* (a full replay) and *Analysis* buttons. It stops by itself 30 minutes after the feed goes quiet.
  - A recording started after the session began still plays back in full from when it joined: F1 sends a snapshot of the state on connecting (session start, drivers, starting tyres, grid, race control and team radio so far), which F1 Hub reads in.
  - Recordings include team radio. When FastF1 has no classification yet (it takes it from the Jolpica-Ergast archive, which lags a fresh race), the result, grid, DNFs and points come from the recording's own timing. They are provisional, because stewards' decisions after the flag aren't in them.
  - A recording started after the session has finished only holds that snapshot, so there is nothing to play back.

**Signing in to F1 TV.** There are two ways:
- **In the browser:** Explore > FastF1 > *Sign in to F1 TV* opens F1's sign-in page through FastF1 (f1login.fastf1.dev). It needs FastF1's browser add-on, which that page links to. While it waits, the card shows how long is left before the sign-in gives up, and it updates when you're signed in. FastF1 stores the token and reuses it until it expires (*Sign out* removes it), so you only do this occasionally. No restart is needed.
- **With a password:** put `F1_USERNAME` and `F1_PASSWORD` in `.openf1.env` (see [Configuration](../README.md#configuration)) and restart F1 Hub. FastF1 signs in with them the first time it needs to.

If F1 turns the password down, the card says so, with F1's reason. FastF1 doesn't send a rejected password again by itself: every load would otherwise repeat the rejected sign-in, which slows loading and can get the account locked. Check the details on formula1.com, then click *Try the password again* (after changing `.openf1.env`, restart F1 Hub instead), or sign in in the browser.

F1 Hub never starts a sign-in in the background. Without a login, FastF1 and the recorder carry on with F1's free data.

With an **F1 TV Pro login**:
- recordings get the full feed, including car telemetry and positions, which F1's free feed leaves out in recent seasons
- FastF1 can read F1's archive for **2023 onwards**. Without a login it only serves 2022 and earlier, so the OpenF1-locked fallback and the FastF1 telemetry source for recent sessions need it.

FastF1's downloads are cached in `.f1cache/fastf1/`. A race with telemetry is about 100 MB the first time (a minute or two), then loads in seconds. Session keys for FastF1 sessions look like `202122050` (year, round, session number, recording number).

## Live OpenF1 recordings

During a live session, F1 Hub saves every OpenF1 response and stream message it receives to `recordings/openf1/<session>/`, and stops by itself once the session is over. Set `F1HUB_RECORD=0` to turn this off; recordings already saved still play back.

Explore > FastF1 & recordings lists them under *Live OpenF1 recordings*:
- **Play back** opens the session as a full replay, from the recorded data.
- **Delays** shows how late each kind of data reached F1 Hub: the median and how long 90% took, from when something happened (its own timestamp) to when it arrived, for polling and for the stream separately. Polling includes the wait for the next request. Data that came in a first answer (F1 Hub had just started, or was catching up after a gap) isn't counted, because when it was published isn't known.

## Status and troubleshooting

The **Status** button in the header (or `D`) runs a setup check. Its dot is green when everything is fine, amber when something needs a look, and red when something is broken. The panel lists each check with what to do about it:
- the settings in `.openf1.env` (names only, never values): unknown names, half-set pairs, and whether other users on the machine can read the file
- the OpenF1 login: paid, free, or turned down (F1 Hub then carries on with free access and tries the login again every 5 minutes)
- the season data, the live feed and the real-time stream
- recording, FastF1 and the F1 TV sign-in
- the tools for radio transcripts and FIA documents, free disk space, and sign-in windows left over from an earlier run
- the latest warnings and errors, and where the log file is (`.f1cache/logs/f1hub.log`)

*Check again* reruns it; it also refreshes by itself every minute. When `.openf1.env` changes while the dashboard is running, a banner says that a restart is needed to use the new settings.

`python3 -m f1hub doctor` runs the same checks in the terminal, with or without the dashboard running. It also logs in to OpenF1, tries the stream and checks the dashboard's port (`--no-network` skips contacting OpenF1).

While the season loads in the background (the server starts straight away), Championship and Calendar say so; if loading fails, they say why and it's tried again shortly. Results that OpenF1 hasn't published yet are listed and fetched again later.

## Keyboard shortcuts

Press `?` on the dashboard for the list.

| Key | Action |
|---|---|
| `Ctrl`+`K` | Command palette: type a few letters to jump to any tab, Explore view, driver or action (also the search box at the end of the tab bar) |
| `H` / `E` | Home / Explore |
| `1`–`0` | Switch tabs |
| `[` / `]` | Previous / next tab |
| `S` | Settings |
| `D` | Status (setup check) |
| `M` | Mini tower |
| `N` | Track map: driver names on / off |
| `Space` | Play/pause a replay |
| Left / Right arrow | Seek 10 s (`Shift` for 1 min) |
| `L` | Back to live |
| `+` / `-` | Live: nudge the delay. Replay: step the speed (¼× to 64×) |
| `Shift`+click | Timing tower: pick drivers for the lap compare |
| `Esc` | Close things |

Your tab is remembered and is in the URL (`#map`, `#radio`, …), so you can bookmark a view.

Each tab keeps its own scroll position, and the tab bar stays at the top of the window as you scroll. Animations follow your system's reduced-motion setting.

## Server safety and resilience

- The local server only answers its own page: other websites can't change your settings or read the data (cross-site requests and foreign hostnames get a 403). The one exception is opt-in: `gui --open-feed` lets web pages from anywhere read the [data feed](feed.md), which is read-only.
- Bad parameters get a clear 400 instead of a crash, and `/api/health` reports what is connected and whether changed settings need a restart.
- When OpenF1 or the network fails, requests answer with the reason (a 502) instead of a server error, and the log records it once, not on every retry. Live timing keeps trying and logs when it's working again.
- If the port is already taken (F1 Hub is probably running already), it says so and suggests `--port`.
- Season standings reload every 30 minutes.
- If one panel ever fails to draw, the rest of the dashboard keeps working and a small note says which part failed. If the server stops, the page shows a reconnect banner and picks up again when it's back.
