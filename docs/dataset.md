# Season dataset

`python3 -m f1hub compile` writes a whole season to one SQLite file, so you can query it across every round
instead of one session at a time. It is the foundation for the analysis on the [roadmap](roadmap.md).

```bash
python3 -m f1hub compile 2025                   # races, sprints and qualifying -> .f1data/season_2025.sqlite
python3 -m f1hub compile 2025 --practice        # practice sessions too
python3 -m f1hub compile 2025 --csv out/2025    # also every table as CSV
python3 -m f1hub compile 2025 --refresh         # compile again sessions already in the file
python3 -m f1hub compile 2025 --out my.sqlite   # somewhere else
python3 -m f1hub compile 2025 --telemetry       # also measure every corner from car telemetry (telemetry.md)
python3 -m f1hub compile 2025 --stewards --radio   # also stewards' decisions and team radio (racecontrol.md)
python3 -m f1hub compile 2025 --round 20-24     # only these rounds
```

- Only finished sessions are compiled. A session's results count as final four days after it ends; until then
  it is compiled again on every run, so steward decisions are picked up.
- Final sessions are skipped, so re-running after each race only fetches the new one.
- Everything goes through the normal OpenF1 cache and rate limits. The first compile of a season makes about
  ten requests per session (a few minutes at OpenF1's 60 requests a minute); after that it is mostly cached.
- Seasons from 2023 (OpenF1). Older seasons through FastF1 are on the roadmap.
- `F1HUB_DATA` changes the default folder (`.f1data/`). A file built by an older version of F1 Hub is rebuilt.

After compiling, the command prints a few season-wide numbers: laps led, positions gained, overtakes, the
longest stints, pit lane time by circuit and the share of racing under a Safety Car.

## Using it

Any SQLite tool works: the `sqlite3` command line, DB Browser for SQLite, DuckDB (`ATTACH 'x.sqlite'`),
pandas (`pd.read_sql("SELECT * FROM laps_full", sqlite3.connect(path))`) or R. With `--csv` every table and the
`laps_full` view is also written as a CSV file.

```sql
-- median clean race lap per team at each round, on the dry tyres
SELECT round, circuit, team, COUNT(*) AS laps, AVG(lap_time) AS mean_lap
FROM laps_full
WHERE session_name = 'Race' AND clean = 1 AND compound NOT IN ('INTERMEDIATE', 'WET')
GROUP BY round, team ORDER BY round, mean_lap;

-- how much each driver lost running close behind another car (dirty air)
SELECT code, AVG(CASE WHEN interval_ahead < 1.0 THEN lap_time END) - AVG(CASE WHEN interval_ahead > 2.5 THEN lap_time END)
FROM laps_full WHERE session_name = 'Race' AND clean = 1 GROUP BY code;
```

## Tables

Every table has `session_key`, which joins it to `sessions`. Driver tables join to `drivers` on
`(session_key, driver_number)`, which gives the code, name and team at the time. Times are in seconds, dates are
ISO 8601 in UTC, and flags are 0 or 1.

### sessions
One row per compiled session: `year`, `round`, `meeting_key`, `meeting_name`, `circuit`, `location`,
`country`, `session_name` (Race, Sprint, Qualifying, Sprint Qualifying, Practice 1...), `session_type` (Race,
Qualifying, Practice), `date_start`, `date_end`, `source` (openf1 / fastf1), `final`, `compiled_at`.

### drivers
`driver_number`, `code`, `name`, `team`, `colour`.

### results
The classification: `position`, `status` (Finished, DNF, DNS, DSQ), `points`, `laps`, `duration` (race time),
`gap_to_leader`, `laps_down`, `q1` / `q2` / `q3` (qualifying), `grid` and `grid_source`. OpenF1 often has no
starting grid; `grid_source` is then `qualifying`: the qualifying order, without grid penalties.

### laps
One row per lap, from OpenF1's laps with these added:

| Column | Meaning |
|---|---|
| `lap_time`, `s1`, `s2`, `s3` | Lap and sector times |
| `i1_speed`, `i2_speed`, `st_speed` | Intermediate and speed-trap speeds, km/h |
| `pit_out`, `pit_in` | Out-lap from the pits; lap that ended in the pit lane |
| `stint`, `compound`, `tyre_age` | Tyre set and its age in laps, including laps run on it before the stint |
| `neutral` | Any part of the lap under a Safety Car, VSC or red flag |
| `deleted` | Lap time deleted by race control (track limits) |
| `clean` | A representative lap: not the race start, a pit lap, neutralised or deleted, and within 7% of the driver's median (races) or best (other sessions), with wet and dry tyres judged separately |
| `position` | Race position at the end of the lap (races and sprints) |
| `interval_ahead`, `gap_to_leader` | Gap to the car ahead and to the leader at the end of the lap, seconds |
| `track_temp`, `air_temp`, `rainfall` | The latest weather reading when the lap started |

The `laps_full` view adds `year`, `round`, `meeting_name`, `circuit`, `session_name`, `session_type`, `code`,
`name` and `team` to every lap.

### stints
`stint`, `compound`, `lap_start`, `lap_end`, `tyre_age_at_start`.

OpenF1 sometimes splits one set of tyres into several stints, for example when the Safety Car leads the field
through the pit lane (Melbourne 2025, laps 2-4). Consecutive stints on the same compound are merged when no pit
stop separates them or the tyre age simply carries on, and the stints are numbered again from 1.

### pits
`lap_number` (the in-lap), `date`, `lane_duration` (pit entry to exit), `stop_duration` (stationary, where
OpenF1 has it) and `stop`: 1 for a tyre change. OpenF1 also lists drive-throughs behind the Safety Car (the whole
field at Montreal 2025 on laps 66-69), penalties and cars retiring in the pits; use `WHERE stop = 1` to count
pit stops or measure pit-lane time.

### weather
About one reading a minute: `air_temp`, `track_temp`, `humidity`, `pressure`, `rainfall`, `wind_speed`,
`wind_direction`.

### race_control
Every message: `date`, `lap_number`, `category`, `flag`, `scope`, `sector`, `driver_number`, `message`.

### overtakes
Races and sprints: `date`, `lap_number` (the overtaking car's), `overtaking` and `overtaken` (driver numbers),
`position` (the position gained) and `on_track`.

OpenF1 records every swap of position, so a busy race shows several hundred "overtakes" (642 in Bahrain 2025).
Use `WHERE on_track = 1` for overtaking counts. It leaves out:

- swaps while either car is in the pit lane, or within about twenty seconds of leaving it,
- passing a slow car (a lap more than 15% slower than its usual one: damage, a spin, retiring),
- swaps under a Safety Car, VSC or red flag, or after the chequered flag,
- several swaps by one car at the same instant (a timing correction),
- cascades: one car passed by four or more cars within 30 seconds, or passing four or more after lap 1
  (a timing glitch, or a car that has stopped),
- ping-pong: the same two cars swapping back within 10 seconds (timing loops flip the order of cars nose to tail).

That leaves 6 passes at Monaco and 61 in Bahrain for 2025. It is a heuristic: a real pass re-taken within 10
seconds is not counted.

### tel_sessions, tel_corners, tel_corner_laps, tel_laps
Per-corner telemetry, added with `compile --telemetry`: the corners of each circuit and every corner of every lap
measured (braking point, minimum speed, throttle, time through it), plus top speed and lift-and-coast per lap.
See [telemetry.md](telemetry.md).

### stewards, stew_events, radio, radio_sessions
Stewards' decisions and team radio, added with `compile --stewards` and `compile --radio`, and kept when
sessions are compiled again. See [racecontrol.md](racecontrol.md).

### positions, intervals
Races and sprints, the raw time series: every position change, and the gaps about every four seconds
(`gap_to_leader`, `interval`, `laps_down` for lapped cars).
