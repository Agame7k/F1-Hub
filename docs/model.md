# Lap-time model

Phase 3 of the [roadmap](roadmap.md). For every race in a compiled [season dataset](dataset.md), the model splits
each clean dry lap into its parts:

    lap time = driver pace + fuel x lap number + compound offset + compound degradation x tyre age
               + dirty-air cost (lap ending within 1.5 s of the car ahead) + noise

```bash
python3 -m f1hub model 2025              # every race, then the season
python3 -m f1hub model 2025 --round 24   # one race, with every driver's corrected pace
```

In the dashboard it is the **Lap-time model** part of Explore > Season report.

## What it gives

| Figure | Meaning |
|---|---|
| Corrected race pace | Each driver's pace with fuel, tyres and dirty air taken out, as a gap to the quickest. A team's figure is its quicker driver. Across the season: % off the quickest team, round by round. |
| Fuel + track | Seconds per lap the field gets quicker as fuel burns and the track rubbers in. The two can't be separated. About -0.05 s a lap in 2025. |
| Degradation | Seconds per lap of tyre age, per compound, per race. |
| Compound offset | How much slower or quicker a new set is than the reference compound (the medium when it was raced). |
| Tyre curves | Offset plus degradation over the ages actually run, so you can see where one compound becomes slower than another. |
| Dirty air | Extra time on laps ending within 1.5 s of the car ahead, per race, and per team over the season. |
| Tyre cliffs | A tyre age after which degradation clearly steepens (change-point search). |
| Steps in pace | The round where a team's corrected pace changed most, when the change is clear (t-statistic of 3 or more, at least 3 races either side), for example an upgrade. |
| Degradation and temperature | Each race's degradation against its median track temperature, with the correlation. |

Every estimate has a standard error. The dashboard and the command line show 95% intervals (± 1.96 standard errors).
A gap smaller than its interval is not a real difference.

## How it is fitted

- **Laps.** Laps flagged `clean` in the dataset (no race start, pit laps, Safety Car, VSC, red flag or deleted
  laps, and within 7% of the driver's typical lap), on dry tyres, without rain.
- **Who is included.** A driver needs 10 such laps. A compound needs 30 laps over 3 stints; otherwise its laps
  are left out and the race says so (Verstappen's five laps on hards at Catalunya 2025, for example). A race
  needs 200 laps; wet races (Melbourne and Silverstone 2025) are not modelled.
- **Method.** Ordinary least squares in plain Python. Laps more than 3.5 robust standard deviations from the
  fit are dropped and the model is fitted again.
- **Uncertainty.** Standard errors are cluster-robust by stint. Laps on one set of tyres are not independent,
  and treating them as independent would make every interval too narrow.

## Limits

- **Fuel and tyre age.** The two are told apart only because drivers stop on different laps. If every driver
  ran one dry stint (Spa 2025), the fuel effect is fixed at the season figure, and that race's degradation also
  picks up the track drying or rubbering in. It is shown faded and left out of the season's degradation
  figures.
- **Compound offset.** An offset needs at least one driver who ran that compound and another one. Without such
  a driver, the offset stays in the pace of the drivers who used it.
- **Track temperature.** It usually rises or falls steadily through a race, so within one race it can't be
  separated from fuel, and it is not a term in the model. Its link with wear is measured across races instead.
- **Dirty air.** A quicker car that has just caught the one ahead also counts as being in dirty air, so the
  cost leans low.
- **Degradation shape.** Within a stint, degradation is modelled as a straight line. The cliff search looks
  for a single later, steeper section, at tyre age 8 or more, and only reports it at 4 standard errors (every
  possible age is tried, so a lower bar would find false cliffs).
