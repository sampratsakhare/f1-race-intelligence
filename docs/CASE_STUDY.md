# Case study: what determined race outcomes in 2024–2025?

## Executive summary

Across the bundled 48-Grand-Prix OpenF1 snapshot, the starting grid was a
strong but incomplete predictor of finishing order: mean race-level Spearman
correlation was 0.762, 56.3% of winners started on pole, and 70.8% of podium
finishers started in the top three. That establishes a demanding baseline for
any pre-race model.

The leakage-safe candidate improved classified-finisher MAE from 3.04 to 2.70
positions on the final 10 races. It beat the grid in 8 of those races, with a
95% race-cluster bootstrap improvement interval of +0.02 to +0.93 positions.
The gain is promising but narrow enough that continued future-race monitoring
is more appropriate than claiming the grid has been solved.

## Scope and definitions

- 48 Grands Prix across 2024 and 2025
- 958 driver-race rows
- 855 rows, or 89.2%, with a non-null classified finishing position
- 10.3% DNF rate; DNS and DSQ are tracked separately
- grid from the OpenF1 qualifying-session `starting_grid` endpoint, mapped to
  the target Grand Prix during extraction
- result and status from `session_result`

Average finish and grid movement use classified rows. Reliability rates retain
all driver-race rows. Clean-lap pace excludes pit-out laps and laps outside
±20% of the race median.

## Finding 1: the grid is the right benchmark

| Measure | 2024–2025 result |
|---|---:|
| Mean race-level grid/finish Spearman | 0.762 |
| Median race-level grid/finish Spearman | 0.823 |
| Winners starting from pole | 27 of 48 (56.3%) |
| Podium finishers starting in top three | 102 of 144 (70.8%) |

This is why a generic mean predictor is not a meaningful comparison. A useful
pre-race model must add signal beyond starting position and must be evaluated
on later, complete races.

## Finding 2: front-running performance and reliability moved together

| Team | Average classified finish | DNF rate | Average positions gained |
|---|---:|---:|---:|
| McLaren | 3.98 | 3.1% | -0.33 |
| Ferrari | 5.16 | 8.3% | +1.09 |
| Mercedes | 5.97 | 8.3% | +0.73 |
| Red Bull Racing | 6.40 | 9.4% | +0.83 |

McLaren combined the best average classified finish with the lowest DNF rate
among these teams. Its slightly negative average grid movement is not evidence
of poor race execution by itself: starting near the front leaves less upside
and more positions available to lose. Grid movement should therefore be read
together with starting-position distribution and reliability.

Source team names are not normalized across rebrands; for example, `RB` and
`Racing Bulls` remain separate labels.

## Finding 3: pace alone did not explain the Yas Marina result

In the detailed 2025 Yas Marina race:

- Lando Norris had the fastest average retained clean-lap pace at 88.678
  seconds, 0.074 seconds faster than winner Max Verstappen.
- Verstappen had lower retained-lap variability: 0.937 seconds versus Norris's
  1.375 seconds, and converted pole into the win.
- Nico Hülkenberg recorded the largest classified grid gain, moving from 18th
  to 9th.
- Charles Leclerc had the shortest observed pit-lane duration at 20.902
  seconds, while the nearest positions around that stop moved from 4th to 5th.

The last comparison is deliberately descriptive. Traffic, tire state, safety
cars, undercut/overcut timing, and the position sampling boundary all prevent
it from being interpreted as the causal effect of the stop.

## Finding 4: the candidate adds modest, measurable pre-race signal

| Final 10-race holdout | Candidate | Grid baseline |
|---|---:|---:|
| Classified-finisher MAE | **2.70** | 3.04 |
| Mean race-level Spearman | **0.741** | 0.708 |
| Top-three accuracy | 73.3% | 73.3% |

The candidate uses the grid plus driver, team, and circuit history available
before each race. It does not use current-race lap pace, pit behavior, or the
result. Promotion requires:

1. at least five future test races;
2. lower aggregate MAE than the grid;
3. a positive lower bound on the race-cluster bootstrap interval.

The current snapshot passes those gates, but unchanged top-three accuracy and
the near-zero lower confidence bound show that the improvement should be
monitored rather than oversold.

## Reproduce

```bash
make demo-data
make check
streamlit run dashboard/app.py
```

The generated inputs and exact model metrics are in `data/demo/`. Metric
definitions and caveats are in [DATA_DICTIONARY.md](DATA_DICTIONARY.md).

Data source: [OpenF1](https://openf1.org/), an unofficial,
community-operated source for educational and non-commercial analysis.
