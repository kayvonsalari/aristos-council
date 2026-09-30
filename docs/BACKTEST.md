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

## How to read the results

Four verdicts, in one line each:

- **proven** — mean excess at least +2%/yr over the equal-weight cohort, after the 0.5% round-trip
  cost, AND positive in at least 6 of 10 measured years, AND beats a random-basket luck baseline
  (BACKTEST-1C — see "Skill versus luck" below).
- **not beyond luck** — the excess/years bar above is met, but not the luck test: too many random
  pickers, drawing the same-size basket from the same names, would have done about as well.
- **not proven** — enough data to judge, and the excess/years bar was not met (luck is not even
  asked in this case).
- **insufficient** — fewer than 6 measured calendar years, or fewer than 60 rounds that held a
  position.

**"Insufficient" means the lens could not be tested on that cohort — it is not a failing grade.** The
typical cause is a small cohort (15–20 names, even with a full 10 years of history) paired with a
selective lens (`growth_garp_v2`, `conservative_plus_v1`): most months, fewer than **`MIN_BUYS` = 3**
names clear the screen (`backtest.py`), so the round holds no position and is excluded from every
average. A cohort that reads "insufficient" for a strict lens may read "proven" or "not proven" for a
looser one over the same names and years — that is the cohort telling you it is too thin for a
selective lens, not the lens telling you anything about itself.

`magic_formula_momentum_v1` and `magic_formula_raw_v1` produce **the same BUY names for the first ~18
months of any window** — momentum needs about that much price history to compute before it starts
reordering the value+quality rank. This is expected, not a bug in either lens or in the loop.

**A verdict is per cohort.** A lens "proven" on one cohort is not proven on another; nothing carries
across. An untested cohort — one with no CSV under `backtests/` for that lens — stays ungated: it is
read as "no evidence either way," never as a silent pass or fail.

**Known open check (not fixed here):** a small number of single rounds show excess beyond ±100% —
seen so far in `comms_media_entertainment` × `conservative_plus_v1` (round 2023-12-31) and
`tech_semiconductor_equipment` × `magic_formula_raw_v1` (round 2025-04-30). The likely cause is an
unadjusted split or a currency mix inside one name's price series, not yet confirmed. This needs to be
investigated from the CSVs before BACKTEST-2 gates any vote on these results; it is documented here,
deliberately left unfixed, so it is not lost.

**First run scope:** the 13 locally watched cohorts (`data/local/cohorts/watch.yaml`, personal, not in
the repo — see `docs/COHORTS.md`) × the 5 voting stock lenses (`magic_formula_raw_v1`,
`magic_formula_momentum_v1`, `growth_garp_v2`, `conservative_plus_v1`, `cyclical_income_v1`),
2016-09 to 2026-09, 12-month holds, monthly steps. Results land in
`backtests/<cohort_slug>/<lens_id>.csv`; once committed, BACKTEST-2 will read them.

## The pass bar (the owner's ruling, 2026-09-26)

A lens is **proven** on a cohort when both hold:

- its **average yearly excess is at least +2%**, and
- it **beat the benchmark in at least 6 of 10 years** (in proportion, if fewer than 10 years could be
  measured: 60% of the measured years — 5 of 8 passes, 5 of 9 does not, 6 of 9 does).

The numbers live as named constants in `backtest.py` with the date of the ruling. Changing them is a
new ruling, not a tuning.

## How to read the four answers

| Answer | Meaning |
|---|---|
| **proven** | Cleared the excess/years bar AND the luck test over enough history. The lens has earned a vote in this cohort — subject to the two limits below. |
| **not beyond luck** | Cleared the excess/years bar, but too many random pickers drawing the same-size basket from the same names would have done about as well. Read as "not distinguishable from chance," not "the lens is bad." |
| **not proven** | Enough history, and it did not even clear the excess/years bar. This is **not** "the lens is bad": it may have missed by a hair or won in some years. Read the numbers on the summary line. It means the lens has not shown it beats simply holding the cohort. |
| **insufficient** | Fewer than 6 measured calendar years, or fewer than 60 months with a position. There is not enough evidence to say either way. A two-year sample run always reads this. |

The summary line for each run looks like:

```
tech_semiconductor_equipment x magic_formula_momentum_v1: insufficient - mean annual excess +3.1%,
1 of 2 years positive, hit rate 58%, 10 of 12 rounds held a position, worst round -9.4%, max drawdown
-6.2%, luck 12%
```

- **mean annual excess** — the average of the yearly excess figures.
- **years positive** — years where the BUY basket beat the benchmark.
- **luck** — `luck_pct_mean` (BACKTEST-1C): the share of the 500 random baskets that did as well or
  better. Low is good — see "Skill versus luck" below. Absent when `random_baskets=0`.
- **hit rate** — the share of months (with a position) that beat it. Overlapping months, so read it
  lightly.
- **worst round** — the single worst month's excess.
- **max drawdown** — the biggest fall of the BUY basket's compounded net return. It is taken at the
  end of each hold, over non-overlapping chains of holds (worst chain, no-position months as cash),
  so it understates a dip *inside* a hold.

**Reading `excess_drop_best` (BACKTEST-1B).** Each round with 4 or more BUY names also carries
`excess_drop_best` — the excess recomputed with the single best-returning BUY name removed (`None`
below 4 names, or when the round held no position). `verdict()` never reads this column; it is for
you. If a lens's mean yearly excess (`mean_annual_excess_drop_best` in the summary) collapses once
the best pick is dropped, the lens is not really "proven" on a basket — it is proven on one stock
that happened to be in the basket. Read `mean_annual_excess` against `mean_annual_excess_drop_best`
together: a small gap between them is a lens spreading its edge across names; a large one is a lens
riding a single winner.

## Skill versus luck (BACKTEST-1C/1D)

The first watched-cohort run (13 cohorts × 5 lenses = 65 tests) gave 7 proven, 32 not proven, 26
insufficient. Proven verdicts changed a lot between runs, hit rates sit near 50%, and **with 65
tests against a modest bar (2%/yr, 6 of 10 years), some passes are expected by chance alone** — a
coin flipped 65 times is not surprising if a few runs come up heads eight times running. Beating the
bar is not the same question as beating chance, so this build adds the second question.

**What a random basket is.** At every round the lens *held a position*, alongside its own BUY
basket, 500 random baskets are also drawn — each the same size as the lens's own basket that round,
picked without replacement from the exact same priced names the lens could actually have bought (the
same eligible, as-of-sized pool its own benchmark is built from). Each random basket is scored
exactly like the lens's: same entry and exit dates, same round-trip cost, same benchmark. The draw is
seeded from a hash of the cohort, the lens and the round date, so a re-run draws the *identical* 500
baskets — reproducible, not re-rolled.

**The luck score.** Take random basket *i* from every round the lens held a position, and you have a
random "lens" — summarised the same way the real one is (mean annual excess, years positive). Doing
that 500 times gives 500 random performance records to compare the real one against:

- **`luck_pct_mean`** — the share of the 500 random records whose own mean annual excess is at
  least as good as this lens's. Low is good: **"luck 3%" means only 3 of 100 random pickers did as
  well or better** — this lens's edge looks real. "Luck 40%" means two in five random pickers would
  have matched it — this is not evidence of skill.
- **`luck_pct_pass`** — the share of the 500 random records that would themselves clear the plain
  excess/years bar. This is the cohort's own **chance pass rate**: how often a random picker, with
  no skill at all, "proves" itself on names this thin or this volatile, just by the bar being loose
  relative to the noise.

**The new verdict.** "Proven" now additionally requires `luck_pct_mean <= 5%` (`max_luck`, a
parameter). A lens that clears the excess/years bar but not the luck test reads **"not beyond
luck"** instead — the bar alone was never enough to call something skill.

**`drop_best_vs_random` — why drop-best needs a random comparison.** BACKTEST-1B's
`excess_drop_best` (the lens's excess with its single best pick removed) cannot be read on its own:
removing the best of ANY 4–5 stock basket lowers its return by roughly 10%/yr even for *random*
picks, purely from basket arithmetic — a small basket's mean is sensitive to its single best member
whoever picked it. `drop_best_vs_random` is this lens's `mean_annual_excess_drop_best` minus the
*median* of the 500 random records' own drop-best figure (computed the identical way). Positive means
the lens depends on its single best pick **less** than random picking does — the more meaningful
reading `excess_drop_best` was always trying to give.

**The multiple-testing line.** `python -m aristos_council.backtest summary` prints, after the
per-file lines, one more: how many tests were even measurable (insufficient ones excluded), how many
came out "proven", and how many "proven"s chance alone would be expected to produce at this sample
size — the sum of every measured test's own `luck_pct_pass`. A proven count noticeably above the
expected-by-chance number is the first real evidence that something here beats a random picker; a
proven count close to it is not.

**BACKTEST-2 (the vote gate, not yet built) must read this verdict**, not the plain excess/years bar
— a lens "not beyond luck" is exactly the case gating exists to catch.

### Why the random pickers had to get stickier (BACKTEST-1D)

The first 1C watched-cohort run put 7 of 39 testable pairs at `luck_pct_mean <= 2%` and 14 at
`luck_pct_mean >= 95%` — against roughly 2 of 39 expected in EACH tail under a calibrated test. That
is not "a lot of skilled lenses and a lot of hopeless ones"; it is a sign the luck test itself was
miscalibrated.

**The cause:** a 1C random basket is redrawn from scratch every round — pure chance, no memory of
what it held last month. A REAL lens is not like that: it holds most of the same names month to
month, and a run of good (or bad) months clusters into a streak, because the same handful of stocks
are driving it the whole time. Averaged over ten years, a from-scratch-every-round random basket's
ups and downs cancel out fast and its yearly average bunches tightly around zero excess. A real
lens's average does not cancel out nearly as fast, because its months are not independent draws.
Measured against that too-tight bunch of independent random baskets, ANY persistent strategy —
good, bad, or middling — looks like an extreme outlier, in whichever direction its actual streak
happened to run. **The random pickers being compared against have to hold their picks with the same
stickiness the lens does, or the comparison is not fair.**

**The fix:** `random_mode="turnover"` (the new default) makes each of the 500 random series a
PERSISTENT basket that evolves one round at a time, matching the lens's own **turnover** — the
number of names it changed since last round (`n_new` on each round: all of them on the very first
round the lens held a position, otherwise the count that were not in last round's basket). Each
random series: drops whatever it holds that stopped being eligible; on top of that, swaps out
exactly `n_new` more of its own holdings, at random; then draws fresh names to fill back up to that
round's basket size. It ends every round the same size as the lens's own basket, having changed by
almost exactly the same amount the lens did — no more, no less. `random_mode="independent"` (the
1C behaviour) is kept available for comparison; a file records which one produced it
(`random_mode` header line, and every row's own numbers).

**Reading the calibration table.** `python -m aristos_council.backtest summary` now also prints a
table: every testable result's `luck_pct_mean` sorted into five bins (0-5%, 5-25%, 25-75%, 75-95%,
95-100%), observed counts beside what a genuinely fair, well-calibrated test would produce (5%, 20%,
50%, 20%, 5% of the tests, respectively). If the two extreme bins between them still hold more than
about twice their expected share, a line prints: **`luck test may still be over-confident`** — read
that as "do not trust these luck scores at face value yet," not as a verdict on any one lens. This
is the check that would have caught the 1C miscalibration on its own, without needing to eyeball the
distribution by hand.

## How the universe is chosen per round (BACKTEST-1B)

The cohort's *membership* is fixed — today's frozen `members.csv` — but a fixed membership is not
the same as a fixed *universe to rank each month*. Several members were micro-caps years ago and are
giants today (some by 10–50×, e.g. names that returned +700% to +5,500% over the window), so a rank
computed as if they had always been today's size would let the benchmark (and the lens) hold
companies that were, in truth, far too small to be candidates at the time.

Each round therefore estimates every member's market cap **as of that round's date**:

```
estimated cap at d  =  market_cap_usd (today's snapshot, from members.csv)
                        × (adjusted close at d ÷ the latest cached adjusted close)
```

A member is **eligible** that round only if the estimate clears the cohort's USD floor — its
definition's `min_market_cap_usd` when there is one, else the smallest `market_cap_usd` among
today's members — **and** it has a price on that date. **The lens's ranking universe (what it can
rank and buy) and the benchmark are built from exactly the same eligible set**, every round; neither
ever sees a name the other does not. A member with no cap snapshot, or no price that day, is simply
excluded from that round, never assumed eligible.

This is an *estimate*, not a measurement — it scales today's known cap by a price ratio, holding the
share count fixed, because that is what a members.csv snapshot and a price series can support without
guessing. The CSV records the floor used on its `as_of_size_floor` header line, and each round's
`n_eligible` column (the as-of-floor-eligible count) alongside `n_ranked` (how many of those the
lens's own screen kept) shows the effect directly — a strategy over a small cohort with a steep
floor may see `n_eligible` shrink to almost nothing in its earliest rounds.

## The two honesty limits

These are stamped into every result and every CSV. Both **flatter** the lens, and neither can be
removed with the data available.

1. **Restated accounts.** The yearly accounts come from EODHD as they stand *today* — restated,
   corrected, re-cut for later accounting changes. A reader on the day saw the *original* figures. The
   90-day lag keeps a year's accounts from being used before it could have been filed, but it cannot
   bring back the numbers as first printed. A lens may look better here than it would have then.
2. **Survivorship.** A company delisted, merged, or acquired out of today's cohort is still absent
   from every round, including ones it would have qualified for — the as-of size floor above corrects
   a *survivor's* own past size, it cannot resurrect a name that did not survive to be in today's
   membership at all.

Two smaller things, also stated in the file: a company whose accounts cannot be dated, or whose price
is missing at the entry or exit day, is left out of the basket **and** the benchmark that month (never
counted as zero); and a company whose share count jumps by a split-like factor after its accounts'
date has its market cap withheld for that month, because today's split-adjusted price times an old
share count would be wrong.

**Price sanity (flagged, never auto-excluded).** Every cached price series is scanned for a one-day
adjusted-close move beyond 3× either way; each one is written to the CSV as its own
`price_warning: <ticker> <date> x<ratio>` header line. This is a flag, not a fix — the series is still
used exactly as fetched. Two were run down by hand from the first watched-cohort pass: **TYT.LSE**
(Toyota's London line) spiked ×9.9 on 2017-04-28 and gave all of it back the very next session — a bad
tick, confirmed against yfinance's own split history (nothing recorded near that date) and excluded via
`data/size_corrections.yaml`. **1396.HK** spiked ×4.15 on 2024-05-14 and *stayed* there, on rising
volume over the following days — a real move, left alone. EODHD's own `/eod` endpoint 403s on the
current plan, so this check relies on yfinance's series and action history alone; a warning that has
not yet been run down by hand should be treated as unverified, not as an error.

## What it costs to run

Every company's accounts are one EODHD `/fundamentals` request (10 API units), fetched once and kept
for the whole ten years. Prices and dividends come from yfinance (free). The whole run is memoised
and day-cached, so re-running on the same day makes no new requests. A **40-company cohort costs 40
EODHD requests (400 units) and 80 yfinance requests**, however many years or months are tested.

The 500 random baskets per round (BACKTEST-1C/1D) cost no extra requests at all — they are drawn
from prices already fetched for the lens's own scoring, in memory, with `numpy` and (for the default
turnover mode) plain Python sets. Measured on a warm cache, `Tech: Semiconductor Equipment` ×
`magic_formula_raw_v1`, 10 years (108 rounds, 54 of them holding a position and so drawing baskets):
**4.0 seconds** end to end in the default `"turnover"` mode, **15.4 seconds** in `"independent"`
mode — both well inside the 60-seconds-per-run target this was built to. `--random-baskets 0` turns
the whole thing off if you ever need the plain excess/years engine alone.

## How to run it

```
python -m aristos_council.backtest run --cohort "Tech: Semiconductor Equipment" \
    --lens magic_formula_momentum_v1 --years 10 [--hold 12 --step 1 --cost-bps 50 --lag-days 90 \
    --random-baskets 500 --max-luck 0.05 --random-mode turnover --out backtests/]
python -m aristos_council.backtest summary backtests/
```

`run` writes `backtests/<cohort>/<lens>.csv`: a header block (the as-of rule, lag, costs, benchmark,
the as-of size floor, the luck baseline's own settings and results, caveats, any price warnings,
verdict, and the git commit of the lens files used), then one row per month. The file is
deterministic — no timestamps — so re-running the same window on the same data gives the same bytes
(the random baskets are seeded, not re-rolled). `summary` prints one line per cohort × lens with its
verdict, followed by the multiple-testing line (see "Skill versus luck").

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
that is not "proven" there — "not beyond luck", "not proven" or "insufficient" alike — will be shown
as a vote without the weight of a proven one. Nothing does that today. Until then this is a
measurement you read, not a rule the system applies — and it ships as such, deliberately: no
strategy, screen or verdict reads a `backtests/` file. **BACKTEST-2 must gate on the full verdict**
(the one `luck_pct_mean` already gates INTO "proven"), never on the plain excess/years bar alone —
see "Skill versus luck" above for why the bar by itself is not enough.

Further out, and also not built, this backtest-gated vote is planned to be one of four quantitative
methods (alongside fair-multiple valuation, a composite score, and Gap Ledger's earnings drift) that
converge on one shared engine called from both the Run tab and Company Check — see
[docs/GAP_LEDGER.md § Relationship to Aristos Council](GAP_LEDGER.md#relationship-to-aristos-council).
