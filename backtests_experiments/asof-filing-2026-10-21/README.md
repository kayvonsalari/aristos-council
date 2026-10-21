# As-of filing mode vs lag mode - Tech - Systems Software (2026-10-21)

**Not part of the badges.** An experiment (ASOF-FILING-1): the same cohort (v1), window (2016-10-01 to
2026-10-01), hold, cost and 500 random baskets as `backtests/`, with `--as-of-mode filing` (a fiscal period
becomes visible on EODHD's filing date) instead of `lag` (period end + 90 days). Seven lenses; the
under-test `growth_garp_v3` was not run in this mode.

Run on code `f299734` (main before PR #186), so these CSVs carry `lens_commit: f299734` and no
`code_commit` / `data_feed` line; the lag-mode set they are compared with ran on `6dbce3e`. The change
between them (#186) only added header lines.

## Result

- **Verdicts: identical** for all seven lenses (`python -m scripts.backtest_compare <lag> <filing>`:
  verdict changed 0, label changed 0).
- **Mean annual excess within 0.8 points** of lag mode for every lens that has one: Earnings Power Value
  -0.9 -> -1.0, Magic Formula RAW -1.8 -> -1.6, Quality +2.4 -> +1.6, Value + Momentum +4.5 -> +5.2,
  Growth v2 -0.5 -> -0.5. The two dividend lenses are untested in both modes.
- **27.4% of fiscal periods fell back to the lag** (19,193 of 70,042 had no usable filing date), so the
  filing run still rests on the lag for about a quarter of its decisions; EODHD carries real filing dates
  mainly for US companies.

## Decision

The default stays `lag`. Filing mode changes neither a verdict nor a badge here, and it cannot be made
exact for non-US names. Re-run it in its own folder if the data improves; never over `backtests/`.
