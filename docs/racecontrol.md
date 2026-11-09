# Stewards, radio and reliability

This is phase 7 of the [roadmap](roadmap.md). It covers:

- stewarding consistency: what each kind of offence got, and decisions worded alike that ended differently;
- team radio transcribed, classified by topic, and checked against what the timing data shows;
- reliability since 1950: why cars didn't finish, per team, decade and hybrid-era power unit.

```bash
python3 -m f1hub compile 2025 --stewards    # read every stewards' decision of the season from fia.com (kept)
python3 -m f1hub compile 2025 --radio       # download and transcribe the season's team radio (kept)
python3 -m f1hub stewards 2025              # outcomes by offence, similar decisions, drivers
python3 -m f1hub radio 2025                 # radio topics, and the calls checked against the data
python3 -m f1hub reliability --fetch        # download race results since 1950 (about an hour the first time)
python3 -m f1hub reliability                # retirements by decade, team and power unit (--since YEAR)
```

In the dashboard it is Explore > Stewards, radio & reliability (`#explore/stewards`). Its buttons do the same
downloads in the background: **Read the 2025 decisions**, **Add the 2025 team radio** and **Download results
since 1950**.

## Stewards' decisions

`compile --stewards` reads every decision document the FIA published for each Grand Prix (the PDFs are kept in
`.f1cache/fia`, spaced three seconds apart when downloading). Each one is split into its fields (car,
competitor, time, session, fact, infringement, decision, reason, articles) and stored in the `stewards` table.
An event is read again until three days after its race, when its documents are taken as final.

Each decision is then sorted, on reading, so that improved patterns apply without downloading again:

| Field | How it is worked out |
|---|---|
| Offence | From the document title, the Fact and the Infringement, into one of 19 categories: causing a collision, leaving the track and gaining an advantage, track limits, forcing another driver off, more than one change of direction, impeding, erratic driving, unsafe release, driving a damaged car, pit lane speeding, pit lane conduct, pit entry or exit line, flags, Safety Car or VSC procedure, start procedure, technical and power unit, procedural (DRS, attending the stewards, setup sheets and similar), conduct, and protests. "Unsafe condition" titles are settled by the Fact: a car released unsafely or a car driven while damaged. |
| Outcome | From the Decision, the most severe of: no further action, warning, reprimand, fine, lap time deleted, time penalty, drive-through, stop-go, grid drop, pit-lane start, disqualification. Seconds, grid places, penalty points and the fine are kept too. A rejected or dismissed protest is no further action. |
| Blame | From the Reason, for collisions: wholly to blame, predominantly to blame, not wholly or predominantly to blame, or a racing incident. |

**Consistency** for an offence is the share of its decisions given the most common outcome. A low figure is not
a fault in itself: practice and race offences are treated differently by design (pit lane speeding is fined in
practice and penalised in a race), and so is a second offence.

### Similar decisions, different outcomes

Each decision is compared with every other one of the same offence by the wording of its offence, Fact and
Reason (TF-IDF cosine similarity, 0 to 1). A pair is listed when:

- the wording is at least 0.35 alike;
- the outcomes are at least two steps apart on the scale above (a reprimand against a time penalty, not a
  warning against a reprimand);
- both happened in a race or sprint, or neither did;
- both are first offences, or both are repeats;
- the offence is not technical, procedural or a protest, where the Fact says little about the incident.

The list is a set of cases worth reading, not proof of inconsistency. In 2025 five pairs qualify, and the
Reason explains most of them. Verstappen's no further action at turn 1 in Barcelona sits beside Bearman's and
Albon's 10 seconds for leaving the track at the same corner, but Verstappen had been pushed off in a collision.
Bortoleto's no further action for passing under a red flag in Montreal practice sits beside Tsunoda's 10-place
grid drop; Bortoleto was at 325 km/h on the back straight when the flag came out.

### 2025

| Offence | Decisions | Most common outcome |
|---|---:|---|
| Technical and power unit | 37 | pit-lane start (28) |
| Causing a collision | 35 | no further action (17), then 12 time penalties |
| Procedural | 27 | no further action (14) |
| Pit lane speeding | 18 | fine (12, all in practice) |
| Flags | 17 | no further action (11) |

225 decisions from 24 Grands Prix. Tsunoda had the most decisions go against him (14), then Sainz (12) and
Bearman (11, with 45 seconds, 20 grid places and 10 penalty points).

## Team radio

`compile --radio` downloads the clips OpenF1 publishes for each race (the ones the broadcast played, about 25 a
race) and transcribes them with Whisper (`small.en`) on this machine, about three seconds a clip when the
machine is otherwise idle. Transcripts are kept in `.f1cache/radio`, shared with the live transcriber, so a clip
is only transcribed once. Each clip is filed on the lap its timestamp falls in, in the `radio` table.

Clips are classified by topic on reading, from their words: tyre trouble, pit call, stay out, strategy, saving,
push, team orders, car problem, other drivers, weather, result and praise, and gaps and pace. A clip can have
several topics or none, and a negated remark ("no sign of graining") doesn't count.

The calls are then checked against the timing data and the lap-time model ([model.md](model.md)):

| Call | Checked against |
|---|---|
| Tyre trouble | The driver's corrected pace in the three laps before the call, against early in the same stint, beside the same measure for every lap at that tyre age (more than 10 laps old) as a control. Also how many laps until the next stop. |
| Pit call | Whether the car stopped from the lap before the call to two laps after (a clip's timestamp can trail the call). |
| Stay out | Whether it did not stop in that window. |
| Team orders | Whether the team-mates had swapped places three laps after the call. |
| Saving | Corrected pace in the laps after the call against the laps before. |
| Car problem | How often the car retired, against the rate for every start. |

### Limits

- Only the broadcast clips are published, so the radio heard is a selection, weighted to the front of the field
  and to moments the broadcaster found interesting.
- A clip is filed under the driver it was sent to, even when it is about a rival ("Oscar's tyres are going").
- Whisper mishears car terms ("grading" for graining), so some patterns allow for that, and some clips are
  missed.
- Topics come from patterns, not understanding. The share in each topic is a rough guide; the checks against the
  data use only the clips that matched.

## Reliability

`reliability --fetch` downloads every season's race results from the Jolpica mirror of Ergast (about 500
requests an hour are allowed, so about an hour the first time) into `.f1data/history`. A finished season is
kept and not downloaded again. The Indianapolis 500, part of the championship from 1950 to 1960, is left out.

Every result's status is grouped: finished (including laps down), engine and power unit, fuel, transmission,
electrical, hydraulics, brakes, suspension and steering, wheels and tyres, bodywork, other mechanical, accident
or collision, driver (illness, injury), disqualified, did not start, and not classified. Rates are per start,
with Wilson 95% intervals.

| View | What it shows |
|---|---|
| Season by season | The share of starts that finished, ended mechanically or ended in an accident. |
| Decade by decade | Mechanical retirements per 100 starts, by cause. |
| Teams | Mechanical retirements per start for each team over the last 12 seasons (`--since` to change), and all-time for teams with 400 starts or more. Teams with fewer than 20 starts are left out. |
| Power units | For 2014 on, engine, ERS, fuel and electrical failures per start for every car using each maker's power unit (Mercedes, Ferrari, Renault, Honda, Honda RBPT), from a map of which team used which unit each season. |

From the 1950s to the 1980s about 35 to 40 starts in every 100 ended with a mechanical failure, most often the
engine and then the gearbox.

### Causes from 2023

Ergast stopped publishing causes after 2022: from 2023 every retirement is "Retired". Where the season dataset
([dataset.md](dataset.md)) has been compiled for that year, its race control messages split these into likely
accidents (a message about an incident, collision, crash, spin or contact naming the car, from the lap before it
retired to two laps after) and likely mechanical. Without the dataset they stay unknown and are shown apart.

## Tables

`compile --stewards` and `compile --radio` add four tables to the season dataset. They are kept when sessions are
compiled again.

| Table | Contents |
|---|---|
| `stewards` | One row per decision document: meeting, round, document number, URL, title, session, car, the parsed fields, and the offence, outcome, seconds, grid places, penalty points, fine and blame as last stored. |
| `stew_events` | One row per Grand Prix read, with when and whether its documents are final. |
| `radio` | One row per clip: session, car, time, lap, URL, transcript and topics. |
| `radio_sessions` | One row per race: clips published, clips transcribed, when. |
