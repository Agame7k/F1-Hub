# Strategy simulation and predictions

This is phase 6 of the [roadmap](roadmap.md). It is built on the season dataset ([dataset.md](dataset.md)) and
the lap-time model ([model.md](model.md)). It covers:

- the inputs a strategist works with, measured from the season: pit loss, Safety Car chances and overtaking;
- a race simulator, and from it the best strategies for every race;
- every pit stop graded against the alternatives at the time;
- win and podium chances before each race, scored against what happened;
- title chances after every round.

```bash
python3 -m f1hub strategy 2025              # inputs, prediction record, title odds
python3 -m f1hub strategy 2025 --round 24   # one race: strategies, prediction, graded pit calls
python3 -m f1hub strategy 2025 --grade      # grade the pit calls of every race (about 10 s a race, kept)
```

In the dashboard it is Explore > Strategy & predictions (`#explore/strategy`).

## Inputs

| Input | How it is measured |
|---|---|
| Pit loss | In-lap plus out-lap, against what the driver would have done on those two laps without stopping: their usual pace plus those laps' conditions, both taken from the cars that didn't stop. Median over the race's green-flag stops; the season figure with fewer than three. |
| Pit loss under a Safety Car | The same, from stops made with the Safety Car or VSC already out for a whole lap. Few stops qualify, so it is one season-wide share of the green-flag loss (about 70% in 2025). |
| Safety Car and VSC chance | Per lap, from race control messages. One race says little, so each circuit's record is blended with the season's rate, as if it had 120 more laps at that rate. |
| Overtaking | A logistic model of whether a car that finished a lap within a second of the car ahead passed it on track on the next lap. The input is its pace advantage: the lap-time model's prediction for both cars on their tyres at the time (driver pace, compound and wear). Each circuit has its own effect, shrunk toward the average. Laps with stops or neutralisations are left out. |

In 2025, a car within a second behind passed on the next lap 3% of the time when level on pace, 12% with
0.5 s a lap in hand and 38% with a second. Spielberg, Montreal and Jeddah were the easiest places to pass;
Monaco, Singapore and Lusail the hardest.

## The simulator

A race is run lap by lap, many times over:

- **Lap time.** Each car's lap is its pace, plus fuel, plus its compound's offset and wear times tyre age, plus
  random variation (the lap-time model's residual spread).
- **Passing.** A car that would finish the lap ahead of the car in front has to pass it. It succeeds with the
  overtaking model's chance; otherwise it is held 0.35 s behind. A car in the pit lane is passed freely.
- **Neutralisations.** Safety Cars (4 laps) and VSCs (2 laps) arrive at the circuit's rate. Under a Safety Car,
  the field queues 0.8 s apart in the order it has after any stops. A car due to stop within six laps stops
  under it, for the smaller loss.
- **Retirements.** At the season's rate, in predictions only.
- **Repeatability.** Random numbers are seeded, so the same inputs give the same answer. Options are compared
  on the same random races, so a small difference between them is a real one, not luck.

### Strategies

Every one- and two-stop plan is scored: each start compound, stop lap and new compound, with at least two
compounds used, stints of at least six laps, and no stint longer than the longest run on that compound at that
race (plus 10%). Each plan is first timed in free air, then the leading few hundred are timed again over 300
Safety Car scenarios.

The chart shows, for each tyre sequence, the time lost against the best plan by lap of the first stop. The table
gives the best plan for each sequence beside how many cars ran it.

### Grading the pit calls

For every stop, the race is simulated 60 times from the lap before it, with every other car on the plan it
actually ran. The options are:
- the call made;
- stopping two or four laps later;
- another compound;
- no stop, when the car has already used two compounds and the tyres would last.

Each option gets an expected finishing position. A better option is reported only when it beats the call made by
more than two standard errors of the paired difference, so noise is not reported as a mistake.

**Hindsight.** Strategies and grading use the pace and tyre wear measured over the whole race. At the time the
teams could only estimate them. A call graded as costly may have been right on what the team knew.

## Predictions

Before each race, using only what was known at the time:

- **Pace.** Each driver's expected race pace, as % off the quickest, is half their qualifying gap and half their
  corrected race pace from earlier races. Earlier races are weighted to halve every four races back. A driver
  with fewer than two races measured takes their team's pace plus 0.25%. Wet races, which the lap-time model
  can't fit, fall back on qualifying.
- **Race-day form.** A random shift per driver per simulated race. Its size is measured from how far drivers'
  race pace strayed from the predictions in earlier rounds: 0.46% of a lap in 2025.
- **The race.** It is simulated 240 times from the grid. Each car runs one of the three best tyre sequences, and
  wear is the season's average so far.

Each prediction is scored with the Brier score, the mean squared difference between the predicted chance and
what happened (0 or 1), against a baseline. The baseline is how often each grid slot has won, or reached the
podium, earlier in the season, smoothed toward a falling curve.

| 2025 | Simulation | Grid baseline | Skill |
|---|---|---|---|
| Win | 0.0268 | 0.0283 | +5% |
| Podium | 0.0565 | 0.0614 | +8% |

The favourite won 13 of the 24 races. The calibration charts plot predicted chances against how often they came
true. Points on the diagonal mean that a 30% chance came true about 30% of the time.

### Title chances

After each round, the rest of the season is simulated 1,500 times: every remaining Grand Prix and sprint, with
points. In each simulated season:
- every driver's pace is moved by its measurement error (race-day form over the square root of the races it is
  from);
- every team's pace drifts at random for development, with a standard deviation of 0.15% of a lap over ten races
  left (scaled by the square root of the races left);
- every event adds race-day form and retirements;
- the finishing order is the order of pace that day.

Ties on points go to the first driver found, because countback is not modelled. Constructors' points are
credited to the team a driver raced for at the time.

In 2025 the model gave Piastri around 70% from Zandvoort to Singapore. Norris passed him at Mexico City and went
into Abu Dhabi at 77%, with Verstappen at 14%.

## Caching

The season part takes about 40 seconds: inputs, 24 races predicted, title odds after every round. Grading takes
about 10 seconds a race. Both are kept in `.f1data/strategy/` and recomputed only when the season is compiled
again or the strategy model changes (`strategy.VERSION`).

## Limits

- **One race per circuit.** Within one season each circuit's pit loss and Safety Car record come from a single
  race. That is why the Safety Car rate is blended with the season's.
- **Wet races.** The lap-time model can't fit a wet race, so wet races have no strategies or grading. Their
  stops still count toward the season's pit loss, and their Safety Cars toward the season's rate.
- **Start and lap 1.** Cars start 0.25 s apart by grid slot, with no first-lap chaos beyond random variation and
  Safety Cars.
- **Team orders, damage and penalties** are not modelled. The title simulation also ignores grid positions and
  orders each event by pace and form.
- **Tyre allocation.** It is not modelled: any compound is available for any stint.
