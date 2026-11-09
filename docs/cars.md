# Cars and circuits

This is phase 5 of the [roadmap](roadmap.md). It is built on the per-corner telemetry from
[telemetry.md](telemetry.md). It covers:

- each car's trade-off between drag and downforce at every circuit, and across the season;
- energy clipping on the straights;
- the circuits grouped by layout, and which cars suit each group.

```bash
python3 -m f1hub compile 2025 --telemetry   # measure the qualifying sessions first
python3 -m f1hub cars 2025                  # the season in the terminal
```

In the dashboard it is Explore > Cars & circuits (`#explore/cars`).

## Why qualifying only

In qualifying, every car is on low fuel and flat out, so the differences come from the cars (and drivers),
not from strategy, fuel loads or tyre saving.

A team's figure is the mean of its two drivers' typical laps, which are the median of each driver's three
quickest laps. Every figure is a difference from the median team.

## Car characteristics

| Figure | Meaning |
|---|---|
| Straights | Top speed on each straight against the median team, averaged over the straights (km/h). Higher means less drag or more power. |
| Slow, medium, fast | Minimum speed through the slow (under 120 km/h), medium and fast (200 km/h and over) corners, against the median team (km/h). Higher means more grip or downforce. |
| Traction | Speed gained over the first 200 m of each straight, against the median team (km/h). |
| Clip | Speed lost while still at full throttle before the braking point, on the longest straight (km/h). |
| Flat | Metres at full throttle within 3 km/h of top speed before braking, on the longest straight. |

Plotting straight-line speed against corner speed shows each car's set-up:
- low and to the right is trimmed for low drag;
- high and to the left, for downforce;
- high and to the right is quick everywhere.

Across the season, each figure is the average over the rounds measured, with a 95% interval.

The pit straight is left out in qualifying, because drivers back off once they cross the line.

## Energy clipping

A car whose speed stops rising, or falls, while the driver is still at full throttle is either:
- running out of electrical energy (the battery's deployment runs out before the end of the straight), or
- meeting its drag limit.

Under the 2025 rules the effect is small, typically 0-2 km/h. From 2026 about half the power is electric,
and it is expected to show far more. The Straights table lists every straight's top speed for every team
and marks speed lost at full throttle.

## Circuits by layout

These features are measured from each circuit's fastest qualifying lap:
- share of the lap at full throttle;
- slow, medium and fast corners per km;
- braking zones per km;
- average corner speed;
- top speed;
- longest straight.

The features are standardised, and k-means groups the circuits into three, so it needs four or more
qualifying sessions. Each group is named after the profile its centre fits best:
- **Power:** full throttle, long straights, top speed.
- **High-speed corners:** fast corners and high corner speeds.
- **Slow and twisty:** slow corners, many braking zones, low corner speeds.

The names are chosen together, as the one-to-one assignment that fits best overall.

The map shows the circuits on their first two principal components, so circuits nearer each other are more
alike.

**Which cars suit which circuits:** each team's qualifying gap to pole (from the season report), averaged over
each group, with a 95% interval. The table also gives the correlation of that gap with the share of the lap
at full throttle. A negative correlation means a team is closer to pole at power circuits.

## Limits

- **Sample rate.** Speeds come from OpenF1's roughly four samples a second, so a single reading is good to a
  few km/h. Averages over many corners, straights and rounds are steadier.
- **Driver effect.** The two drivers are averaged, but a team with one much slower driver looks slower
  everywhere.
- **Clipping and drag.** The telemetry has no battery state, so the two can't be told apart. A speed that
  falls at full throttle points to clipping; one that only stops rising may be either.
- **Rough group figures.** With few rounds in a group, its figures are rough. The table shows how many rounds
  each average is from.
