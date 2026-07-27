# Bundled dashboard demo data

These files are a bounded, derived snapshot from
[OpenF1](https://openf1.org/) for portfolio demonstration:

- 2024 and 2025 Grand Prix grid/result rows;
- detailed clean-lap and pit context for the latest completed race in that
  range;
- a static end-of-session replay leaderboard;
- metrics from the same leakage-safe chronological model gate used by the
  warehouse path.

Regenerate the snapshot from source:

```bash
make demo-data
```

Exact generation time, source URL, covered years, and detailed session are in
`manifest.json`.

OpenF1 is unofficial and not affiliated with Formula 1, FIA, or Formula One
Management. Its data is intended for educational, research, and
non-commercial fan analysis. Review the current OpenF1 terms and license
before reuse.
