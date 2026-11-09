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

## Phase 4 - Circuit model and per-corner telemetry

- Build a reference line for each circuit from fastest laps and split it into corners and straights
  automatically (speed minima, braking zones, full-throttle stretches).
- Opt-in telemetry compilation (car data and location for every lap is millions of rows per race).
- Per driver, per corner: braking point, peak braking, minimum speed, throttle pick-up, consistency
  (lap-to-lap spread) and mistakes (lock-ups, off-line moments).
- Driving-style comparison, especially between team-mates in the same car.
- Lift-and-coast and tyre-management detection.

## Phase 5 - Car characteristics and energy (2026 rules)

- Straight-line speed against cornering speed in slow, medium and fast corners: each car's aero / drag
  trade-off at each circuit.
- Energy clipping: full throttle with speed flat or falling late on a straight, measured per car, per
  straight and per lap.
- Circuit clustering by layout (share of full throttle, slow corners, braking events), to predict which
  cars suit which circuits.

## Phase 6 - Strategy simulation and predictions

- Pit loss per circuit from real stops; overtaking probability from pace and tyre-age gaps; Safety Car
  probability per lap and circuit from history.
- Monte Carlo simulation of every viable strategy, before the race and live.
- Grading of the calls teams made, against what the simulation said was best at the time.
- Win, podium and championship probabilities, each scored afterwards (Brier score, calibration).

## Phase 7 - Race control and radio

- Stewarding consistency: similar incidents, the penalties they got, and the rule behind each.
- Reliability: DNF causes per team and power unit, using Ergast results back to 1950.
- Radio transcripts classified (tyre complaints, strategy calls, saving, team orders) and lined up against
  what the data shows, such as a "tyres are gone" call before the tyre cliff the model finds.

## Later

- Seasons 2018-2022 in the dataset through FastF1.
- Car-adjusted driver ratings across seasons, linked by team-mate comparisons.
