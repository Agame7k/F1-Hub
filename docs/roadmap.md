# Analytics roadmap

The goal: compile an entire season into one local dataset, then answer deeper questions about it than a
single session can - why a car is fast, where a driver gains time, which strategy was right, and how
confident we can be in each answer.

Every phase builds on the one before it. Each lists what it delivers and the questions it answers.

## Principles

- **Real data, labelled estimates.** Anything modelled says so, and shows its uncertainty (a ± range or an
  interval), so a 0.05 s gap that is really noise reads as noise.
- **Honest backtesting.** The `before=` cut that already powers spoiler mode lets any prediction be run "as
  of lap 20" and scored against what actually happened afterwards.
- **Plain Python core.** The dataset is SQLite (in the standard library). Heavier modelling may use the
  `.venv` that FastF1 already brings (pandas, numpy), in a worker, the same way FastF1 does today.

## Phase 1 - Season dataset (done)

`python3 -m f1hub compile 2025` writes every finished race, sprint and qualifying session of a season to
`.f1data/season_2025.sqlite`. See [dataset.md](dataset.md) for the tables.

- One table per kind of data: sessions, drivers, results, laps, stints, pits, weather, race control,
  overtakes, positions, intervals.
- Every lap is enriched: tyre compound and age, stint, pit in / out, neutralised (SC / VSC / red),
  position and gap to the car ahead at the end of the lap, track and air temperature, and a `clean` flag.
- Incremental: sessions already compiled and final are skipped, so re-running after each race is quick.
- CSV export of every table (`--csv DIR`) for pandas, R, a spreadsheet or a notebook.

## Phase 2 - Season report (done)

Explore > Season report in the dashboard (`#explore/report`), built purely on the dataset. Descriptive, but season-wide:

- Laps led, places gained on lap 1 and from grid to flag, overtakes made and lost on track. (By grid slot and grid
  side: still to do.)
- Qualifying gaps to pole across the season, team by team, and team-mate deltas over time.
- Tyre usage by compound and circuit; stint lengths; who ran the longest stints.
- Pit stops: pit-lane loss per circuit, stationary times per team (mean, spread, slow-stop rate).
- Safety Car, VSC and red flag frequency per circuit, and on which laps.
- Overtakes per race and per circuit, and who made and lost the most positions on track.
- Weather: track temperature range and rain at every race (its link with degradation is in phase 3).
- Race and qualifying pace for every team, round by round, as % off the quickest (corrected race pace is phase 3).

## Phase 3 - Lap-time model (done)

`python3 -m f1hub model 2025`, and the Lap-time model section of the Season report. See [model.md](model.md).

    lap time = driver pace + fuel x lap number + compound offset + compound x tyre age
               + dirty air (lap ending within 1.5 s of the car ahead) + noise

- Fitted per race by least squares, with stint-clustered standard errors. Every figure has a 95% interval.
- Fuel effect fitted per race instead of a fixed estimate. It is fuel burn and track evolution together, about
  -0.05 s per lap in 2025. Fixed at the season figure where nobody stopped.
- Degradation per compound with intervals; tyre cliffs by change-point search; tyre curves per race.
- Fuel- and traffic-corrected race pace for every driver at every race, and for every team across the season,
  with the round of any clear step in pace (upgrades).
- The cost of dirty air per circuit and per team.
- Track temperature is not a term: within a race it moves with the lap number, so it can't be separated from
  fuel. Its link with degradation is shown across races instead.

## Phase 4 - Circuit model and per-corner telemetry (done)

`python3 -m f1hub compile 2025 --telemetry [all]`, and Explore > Corners. See [telemetry.md](telemetry.md).

- Opt-in: every car's telemetry for each session, kept raw (gzipped) beside the dataset so it can be measured
  again offline; only the measurements go into the dataset.
- A reference line for each circuit from the session's fastest lap, split into corners automatically (speed dips,
  chicanes merged) and named after the official corner numbers; slow, medium and fast corners.
- Per driver, per corner, every lap: braking point, peak braking, entry, minimum and exit speed, throttle pick-up,
  gear, and time through the corner (corner times add up to the lap).
- Who is quickest in each corner, time lost in slow, medium and fast corners, top speed.
- Two drivers side by side on a map and corner by corner; team-mates' styles across the season with 95% intervals.
- Races: lift and coast per lap and through the race, consistency (lap-to-lap spread of corner times) and big
  moments.
- Still to do: lock-ups (no wheel-speed data), practice sessions.

## Phase 5 - Car characteristics and energy (done)

`python3 -m f1hub cars 2025`, and Explore > Cars & circuits. See [cars.md](cars.md).

- Straights found on each circuit's reference lap and measured on every lap: entry speed, traction (speed gained
  over the first 200 m), top speed and where, and energy clipping (speed lost, and metres flat, at full throttle).
- Car characteristics from qualifying: straight-line speed against minimum speed in slow, medium and fast corners,
  against the median team, per circuit and across the season with 95% intervals - each car's drag / downforce
  trade-off.
- Circuits grouped by layout (k-means on full-throttle share, corners per km by type, braking zones, corner and
  top speed, longest straight), mapped on their principal components, and each team's qualifying gap by group
  and its correlation with full throttle: which cars suit which circuits.
- Clipping is small under the 2025 rules; the same measures apply unchanged to 2026, when it is expected to matter.

## Phase 6 - Strategy simulation and predictions (done)

`python3 -m f1hub strategy 2025`, and Explore > Strategy & predictions. See [strategy.md](strategy.md).

- Pit loss per circuit from real stops, green and under a Safety Car; Safety Car and VSC probability per lap and
  circuit, blended with the season's rate; overtaking probability from the pace advantage on the tyres at the
  time, with a shrunk effect per circuit.
- A lap-by-lap Monte Carlo race simulator (passing, Safety Cars that bunch the field and cheapen stops,
  retirements), and from it every viable one- and two-stop strategy for a race, ranked with Safety Car risk.
- Every pit stop graded against stopping later, on another tyre or not at all, from the state of the race at
  the time, reported only when an alternative is clearly better.
- Win and podium chances before every race from what was known at the time, scored afterwards against a
  grid-position baseline (Brier score, calibration); title chances after every round.
- Still to do: the simulator on live timing during a race.

## Phase 7 - Race control and radio

- Stewarding consistency: similar incidents, the penalties they got, and the rule behind each.
- Reliability: DNF causes per team and power unit, using Ergast results back to 1950.
- Radio transcripts classified (tyre complaints, strategy calls, saving, team orders) and lined up against
  what the data shows, such as a "tyres are gone" call before the tyre cliff the model finds.

## Later

- Seasons 2018-2022 in the dataset through FastF1.
- Car-adjusted driver ratings across seasons, linked by team-mate comparisons.
