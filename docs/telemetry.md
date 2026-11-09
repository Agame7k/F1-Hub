# Per-corner telemetry

Phase 4 of the [roadmap](roadmap.md). This step reads every car's telemetry through a session (speed,
throttle, brake, gear, RPM, DRS and position on track). From it, it finds the circuit's corners and measures
every corner and straight of every lap. [cars.md](cars.md) builds car characteristics and circuit groups on it.

```bash
python3 -m f1hub compile 2025 --telemetry            # qualifying sessions
python3 -m f1hub compile 2025 --telemetry all        # qualifying and races (lift and coast, consistency)
python3 -m f1hub compile 2025 --telemetry --round 24 # one round
```

In the dashboard: Explore > Corners (`#explore/corners`). Its **Measure** buttons run the same thing in the
background.

## Cost

Telemetry comes from OpenF1: one request per driver for car data and one for location, three drivers at a
time.

| | Time the first run takes | Raw telemetry kept |
|---|---|---|
| Qualifying session | about 4-5 minutes | about 3 MB |
| Race | about 10-15 minutes | about 6 MB |

- **Keeping it.** The raw telemetry is kept, gzipped, in `.f1data/telemetry/<session_key>/`, one file per
  driver. Measuring again (after an F1 Hub update, or with `--refresh`) needs no download.
- **Resuming.** An interrupted download carries on from the drivers already saved.
- **What goes in the dataset.** Only the measurements, not the millions of raw samples.

## How it works

1. **Reference line.** The session's fastest lap, with clean telemetry, is the reference. Its x/y path,
   closed into a loop, defines distance round the lap.
2. **Corners.** A corner is a dip in speed on the reference lap: at least 12 km/h below the fastest point
   since the previous corner, with the driver braking or lifting. Dips less than 140 m apart are one corner
   (a chicane). Corners are named after the official corner numbers from the circuit data OpenF1 links to,
   each official number going to the nearest corner found. "T6-7" is a chicane; flat-out kinks have no
   corner of their own.
3. **Corner type.** Set by the reference lap's minimum speed: slow (under 120 km/h), medium (120-200) or fast
   (200 and over).
4. **Track ownership.** Each corner owns the track from halfway to the previous corner to halfway to the next,
   so the corner times of a lap add up to the lap time.
5. **Placing every lap.** Every sample of every lap is projected onto the reference line, so all laps and
   drivers compare at the same point on the track.

## Tables

| Table | One row per | Columns |
|---|---|---|
| `tel_sessions` | session measured | reference driver and lap, `length` (m), `outline` (x/y points for a map) |
| `tel_corners` | corner | `label` (T1, T6-7...), `apex`, `start`, `end` (m from the line), `x`, `y`, reference `min_speed`, `kind` |
| `tel_corner_laps` | corner of every lap | see below |
| `tel_straights` | straight | `label` (after T5...), `start`, `end`, `length` (m), reference `top_speed`, `wraps` (crosses the line) |
| `tel_straight_laps` | straight of every lap | `entry_speed`, `accel` (km/h gained over the first 200 m), `top_speed`, `top_at` (m into the straight), `end_speed`, `clip_drop` (km/h lost at full throttle before braking), `clip_dist`, `plateau` (m within 3 km/h of top speed), `drs` (share of the straight with DRS open) |
| `tel_laps` | lap | `top_speed`, `full_throttle` and `brake_pct` (% of the lap's distance), `lico_time` and `lico_count`, `gear_changes`, `samples`, `off_line` (average distance from the reference line, m) |

`tel_corner_laps` columns:

| Column | Meaning |
|---|---|
| `seg_time` | Seconds through the corner's stretch of track |
| `entry_speed`, `min_speed`, `exit_speed` | km/h: fastest before the corner, slowest at it, and 150 m after the apex |
| `apex_dist` | Where this lap's slowest point was, in m from the reference apex |
| `brake_dist`, `lift_dist` | Where the brakes went on, or the throttle went under 50%, in m before the reference apex (bigger = earlier). `brake_dist` is empty when the corner is taken without braking |
| `peak_decel` | Hardest braking, in g |
| `throttle_dist` | Where the throttle is fully open (95%+) again, in m after the reference apex |
| `gear` | Gear at the apex |

**Lift and coast** is a stretch of at least 0.4 s with the throttle under 10% and no brake, above 200 km/h,
that ends on the brakes. Drivers do it to save fuel, energy and brakes, and to cool the tyres.

## In the dashboard

| Section | What it shows |
|---|---|
| Map | Each corner coloured by which of the two chosen drivers is quicker through it |
| Corner by corner | Both drivers side by side: braking point, minimum speed, throttle, exit speed and time, and who in the field was quickest |
| Where time is lost | Every driver's time lost to the quickest, totalled for slow, medium and fast corners |
| Top speed (qualifying) | Each driver's highest speed on any lap |
| Lift and coast, consistency (races) | Lift and coast per clean lap and how it changed through the race; spread of corner times from lap to lap; big moments (corners taken more than 0.5 s slower than usual) |
| Team-mates' styles | Across every qualifying session measured: per corner type, the time each team-mate gains, apex speed and braking point, with 95% intervals |

A driver's typical corner is the median over their three quickest laps in qualifying, or over their clean
laps in a race.

## Limits

- **Sample rate.** OpenF1 samples car data and location about four times a second, so about every 20 m at
  300 km/h. A braking point is good to about ±10 m. A minimum speed can read a few km/h high in a corner
  whose apex is short and sharp. Medians over several laps, and season averages, smooth much of this out.
- **Lock-ups.** There is no wheel-speed data, so lock-ups can't be seen directly. Big moments and the
  distance from the reference line are the nearest measures.
- **Coverage.** Practice sessions aren't measured.
- **Missing data.** Laps with a hole of more than 1.6 s in the car data are skipped.
