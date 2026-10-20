# Retired cohorts

Experiment and retired-cohort results live here, never in `backtests/` (docs/BACKTEST.md, "Refreshing the
results").

## industrials_aerospace_defense (retired 2026-10-20, OLD-DEFENCE-RETIRE-1)

The COHORT-1 all-markets "Industrials - Aerospace & Defense" cohort (48 names at $5bn, every exchange). It is
replaced by DEFENCE-COHORT-1, "Industrials - Aerospace & Defense (Europe + US)" (`industrials_aerospace_defense_europe_us`),
which has all seven lenses backtested.

- Its definition is removed from `data/cohort_definitions.yaml`. The industry code now resolves to the
  Europe + US cohort, so a defence company's track-record badges read that cohort's record.
- Its frozen membership moved out of the cohorts folder (local, git-ignored) to
  `data/local/cohorts_retired/industrials_aerospace_defense/`, so `python -m aristos_council.cohorts status`
  no longer lists it.
- **Backtest CSVs: none exist.** `git log` finds no commit that ever added a file under
  `backtests/industrials_aerospace_defense/`, and `backtests/` never held one (BD14-T1 records it as "no
  backtest yet"), so there is nothing to move into this folder.
