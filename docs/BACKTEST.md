# Backtest — did the lens actually beat its own cohort?

A lens says BUY on a handful of companies in a cohort. The backtest asks the one question that
decides whether that vote deserves trust: **if you had held that lens's BUY names, would you have
done better than holding the whole cohort?**

It is a separate, slow, offline measurement. It calls no language model, it changes no verdict, and
nothing in a normal run reads it yet (see "What it will be used for").

## What is measured

At the end of every month, going back ten years, the backtest:

1. **Ranks the cohort with the real lens** — the same code that ranks it today — but feeding it only
   what was knowable on that day: the closing prices up to that day, and the yearly accounts whose
   fiscal year had ended at least 90 days earlier (the time a company takes to file). Nothing later
   is read. This is `data/asof_adapter.py`.
2. **Buys the lens's BUY names, equal amounts of each,** and holds them for 12 months.
3. **Compares** that basket with the **benchmark: an equal-weight holding of every company the lens
   ranked that day** — the cohort itself.
4. **Charges 0.5% (50 basis points) once per round trip** on the BUY basket. The benchmark pays
   nothing, so the test is deliberately a little unfair to the lens.

The gap — BUY basket minus benchmark, after cost — is the **excess**. Returns are total returns
(prices adjusted for dividends and splits), measured from the last close on or before the entry day to
the last close on or before the exit day.

**A month with fewer than three BUY names is "no position."** One or two stocks is a bet, not a
lens's verdict. Such months are counted, kept in the file, and left out of every average.

Because a new 12-month hold starts every month, holds overlap. Months are therefore **not independent
evidence**; the honest unit is the **calendar year**. Each year's excess is the average of the months
that started in it, and the pass bar below is about years, not months.

## The pass bar (the owner's ruling, 2026-09-26)

A lens is **proven** on a cohort when both hold:

- its **average yearly excess is at least +2%**, and
- it **beat the benchmark in at least 6 of 10 years** (in proportion, if fewer than 10 years could be
  measured: 60% of the measured years — 5 of 8 passes, 5 of 9 does not, 6 of 9 does).

The numbers live as named constants in `backtest.py` with the date of the ruling. Changing them is a
new ruling, not a tuning.

## How to read the three answers

| Answer | Meaning |
|---|---|
| **proven** | Cleared both tests over enough history. The lens has earned a vote in this cohort — subject to the two limits below. |
| **not proven** | Enough history, and it did not clear the bar. This is **not** "the lens is bad": it may have missed by a hair or won in some years. Read the numbers on the summary line. It means the lens has not shown it beats simply holding the cohort. |
| **insufficient** | Fewer than 6 measured calendar years, or fewer than 60 months with a position. There is not enough evidence to say either way. A two-year sample run always reads this. |

The summary line for each run looks like:

```
tech_semiconductor_equipment x magic_formula_momentum_v1: insufficient - mean annual excess +3.1%,
1 of 2 years positive, hit rate 58%, 10 of 12 rounds held a position, worst round -9.4%, max drawdown -6.2%
```

- **mean annual excess** — the average of the yearly excess figures.
- **years positive** — years where the BUY basket beat the benchmark.
- **hit rate** — the share of months (with a position) that beat it. Overlapping months, so read it
  lightly.
- **worst round** — the single worst month's excess.
- **max drawdown** — the biggest fall of the BUY basket's compounded net return. It is taken at the
  end of each hold, over non-overlapping chains of holds (worst chain, no-position months as cash),
  so it understates a dip *inside* a hold.

## The two honesty limits

These are stamped into every result and every CSV. Both **flatter** the lens, and neither can be
removed with the data available.

1. **Restated accounts.** The yearly accounts come from EODHD as they stand *today* — restated,
   corrected, re-cut for later accounting changes. A reader on the day saw the *original* figures. The
   90-day lag keeps a year's accounts from being used before it could have been filed, but it cannot
   bring back the numbers as first printed. A lens may look better here than it would have then.
2. **Survivorship.** The members are today's frozen cohort (the current `members.csv`). Companies that
   were delisted, taken over, or fell out of the cohort's size band during the ten years are absent.
   The benchmark is built from the same survivors, so the *excess* suffers less than the absolute
   returns — but it is not immune.

Two smaller things, also stated in the file: a company whose accounts cannot be dated, or whose price
is missing at the entry or exit day, is left out of the basket **and** the benchmark that month (never
counted as zero); and a company whose share count jumps by a split-like factor after its accounts'
date has its market cap withheld for that month, because today's split-adjusted price times an old
share count would be wrong.

## What it costs to run

Every company's accounts are one EODHD `/fundamentals` request (10 API units), fetched once and kept
for the whole ten years. Prices and dividends come from yfinance (free). The whole run is memoised
and day-cached, so re-running on the same day makes no new requests. A **40-company cohort costs 40
EODHD requests (400 units) and 80 yfinance requests**, however many years or months are tested.

## How to run it

```
python -m aristos_council.backtest run --cohort "Tech: Semiconductor Equipment" \
    --lens magic_formula_momentum_v1 --years 10 [--hold 12 --step 1 --cost-bps 50 --lag-days 90 --out backtests/]
python -m aristos_council.backtest summary backtests/
```

`run` writes `backtests/<cohort>/<lens>.csv`: a header block (the as-of rule, lag, costs, benchmark,
caveats, verdict, and the git commit of the lens files used), then one row per month. The file is
deterministic — no timestamps — so re-running the same window on the same data gives the same bytes.
`summary` prints one line per cohort × lens with its verdict.

From Python (for example in Colab):

```python
from datetime import date
from aristos_council.backtest import run_lens_backtest, verdict, to_csv, from_csv
result = run_lens_backtest("Tech: Semiconductor Equipment", "magic_formula_momentum_v1",
                           start=date(2016, 1, 31), end=date(2026, 8, 31),
                           members=[...])       # pass members when the cohort is not built locally
print(verdict(result), result.summary, result.year_excess)
to_csv(result, "backtests/")
```

## Re-running yearly

The verdict is only as fresh as its end date. Once a year, re-run each cohort × lens with
`--years 10` (the window moves forward a year), commit the new CSVs under `backtests/`, and read
`summary`. A cohort that has been re-built (`v2`) is a different cohort: its file records the version,
and a rebuilt cohort should be re-run rather than compared with the old file.

## What it will be used for

Later (BACKTEST-2, **not in this build**), a lens's vote in a cohort will depend on this file: a lens
that is "not proven" there will be shown as a vote without the weight of a proven one. Nothing does
that today. Until then this is a measurement you read, not a rule the system applies — and it ships
as such, deliberately: no strategy, screen or verdict reads a `backtests/` file.
