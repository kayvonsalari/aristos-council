# Backtest — did the lens actually beat its own cohort?

A lens says BUY on a handful of companies in a cohort. The backtest asks the one question that
decides whether that vote deserves trust: **if you had held that lens's BUY names, would you have
done better than holding the whole cohort?**

It is a separate, slow, offline measurement. It calls no language model and it changes no verdict —
a normal run reads it only to LABEL a vote in plain English (the "Track-record badges" section
below), never to change one (see "What it will be used for").

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
unadjusted split or a currency mix inside one name's price series, not yet confirmed. Since
BACKTEST-2's badges never gate a vote (see "Track-record badges" below), this affects only how
those two cohort × lens badges READ, not any vote — but it should still be run down from the CSVs
before either result is trusted; it is documented here, deliberately left unfixed, so it is not lost.

**First run scope:** the 13 locally watched cohorts (`data/local/cohorts/watch.yaml`, personal, not in
the repo — see `docs/COHORTS.md`) × the 5 voting stock lenses (`magic_formula_raw_v1`,
`magic_formula_momentum_v1`, `growth_garp_v2`, `conservative_plus_v1`, `cyclical_income_v1`),
2016-09 to 2026-09, 12-month holds, monthly steps. Results are committed under
`backtests/<cohort_slug>/<lens_id>.csv`; BACKTEST-2 reads them to badge every vote — see
"Track-record badges" below.

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

**BACKTEST-2 reads this verdict** (see "Track-record badges" below) to label a lens's vote in plain
English — it does NOT gate on it. A future gate that changes a vote's WEIGHT because of its badge
is a separate, still-unbuilt step; a lens "not beyond luck" is exactly the case such a gate would
need to catch, but nothing in this build caps or reweights a vote.

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

## Track-record badges (BACKTEST-2, shipped 2026-09-30)

Every lens's VOTE, in the Analyse tab's list side and in its Company mode, carries a
plain-English **badge** naming its own track record in the relevant cohort — read straight off
the committed `backtests/` files above. TAB-MERGE-1 part 2: a badge is attached only to a lens
that actually voted (status "ranked"); a "does not apply" row (excluded by the lens's own
screen, too few peers, no data, …) shows its reason with no badge — a lens's historical hit
rate says nothing about a company it never ranked. **No gating.** A badge is display only:
every lens keeps its vote whatever its badge says, and the equal-vote agreement count is
exactly what it would be with no badges shown at all. A future gate that changes a vote's
weight because of its badge is a separate, still-unbuilt step (see "What it will be used for").

### The badge scale

One of five labels, checked in this order, each with the ONE sentence that explains it — written
here once and reused verbatim by the app's hover/expander, so the two can never drift
(`BADGE_MEANINGS` in `backtest.py`):

| Badge | Meaning |
|---|---|
| **proven here** | Beat its benchmark in this cohort by enough, often enough, for long enough, that fewer than 1 in 20 random stock-pickers matched it. |
| **promising here** | Beat its benchmark in this cohort by a real margin most years, and did better than most random stock-pickers - not yet enough history or separation from luck to call it proven. |
| **worked against you here** | In this cohort, picking names at random would have matched or beaten this lens at least 9 times in 10. |
| **no edge shown here** | In this cohort, this lens's record does not clear the bar, and does no better than picking names at random. |
| **untested here** | There is not yet enough measured history for this cohort and this lens to say anything. |

The rule, precisely (`track_record()` in `backtest.py`):

1. no committed result for this cohort + lens at all, or its own `verdict()` reads
   "insufficient" (too little measured history to trust anything below) → **untested here**
2. `verdict()` == "proven" → **proven here**
3. the plain excess/years bar is met (mean excess ≥ +2%/yr, positive in ≥ 6 of 10 years) AND
   `luck_pct_mean` ≤ 25% → **promising here**
4. `luck_pct_mean` ≥ 90% — regardless of whether the bar was met — → **worked against you here**
5. everything else that was actually tested → **no edge shown here**

### Where the cohort comes from

- **The Analyse tab's list side.** The universe being run either IS one of the 13 backtested
  cohorts or it is not — its display name is slugified the same way a cohort's name is
  (`cohort_slug()`) and matched directly against `backtests/<slug>/`. Most lists (an ad-hoc
  paste, a saved list of a different shape) match none of the 13, and show no track-record
  section at all; that is the ordinary case, not an error.
- **Company mode.** A company's own industry label (EODHD, or the GICS sub-industry a cohort is
  narrowed to) is matched against `data/cohort_definitions.yaml` by `cohort_for_industry()` — the
  SAME rule the cohort builder itself uses to select members. This is independent of the
  company's PEER GROUP (`market_index.peers`), which is a different, wider ladder built for
  comparison, not for backtesting; a company can have a peer group with no matching backtested
  cohort, and vice versa.

### Read this before trusting a badge

- **Ten years is one market regime.** 2016–2026 favoured growth and momentum over cheap, out-of-
  favour stocks for most of its length. A badge earned in this decade says nothing about how the
  same lens would have read in, say, 2000–2010.
- **The baskets are thin.** Three to nine names, month to month — a badge is a statement about a
  small, concentrated basket's own history, not a diversified portfolio's.
- **39 tests were run at a bar fixed up front** (13 cohorts × 5 lenses, minus the pairs that read
  "insufficient" — too thin a cohort for a selective lens) **— about 2 "proven" results are
  expected by chance alone**, even with zero real skill anywhere (see "Skill versus luck" above
  and the multiple-testing line `summary` prints). A "proven" badge is evidence, not proof.
- **A badge describes the past, in one cohort — never a forecast.** It says how this lens's own
  picks did over the last ten years against this particular group of similar companies, not what
  it will do next. The optional plain-English summary is held to the same rule: it may repeat a
  badge's label, but must never turn it into a claim that a lens predicts anything.

### The 13×5 results matrix

Generated from `backtests/SUMMARY.csv` by `scripts/backtest_badge_matrix.py` — re-run it after
every refresh (below) and paste its output back in here; never hand-edited, so this table can
never silently drift from what is actually committed:

```
python -m scripts.backtest_badge_matrix
```

**P** = proven, **L** = not beyond luck, **N** = not proven, **U** = insufficient (untested — too
little measured history), each cell's mean annual excess in parentheses:

| Cohort | conservative_plus_v1 | cyclical_income_v1 | growth_garp_v2 | magic_formula_momentum_v1 | magic_formula_raw_v1 |
|---|---|---|---|---|---|
| Comms - Interactive Media & Gaming | N (0.4%) | L (2.1%) | P (8.7%) | N (3.1%) | L (2.4%) |
| Comms - Media & Entertainment | N (-10.2%) | N (-4.4%) | U | P (9.3%) | N (-1.0%) |
| Consumer - Auto Manufacturers | U (-6.7%) | N (2.5%) | N (-14.6%) | N (-3.4%) | L (6.2%) |
| Health - Large Pharma | U (6.9%) | N (-5.3%) | U | N (-2.8%) | N (-1.1%) |
| Health - Medical Devices & Instruments | N (0.9%) | N (-1.5%) | U | N (-2.2%) | N (2.6%) |
| Industrials - Airlines & Airports | U | N (-9.6%) | U | N (-1.3%) | N (-0.2%) |
| Industrials - Grid & Electrical Machinery | U | U (-33.6%) | U | U | P (6.3%) |
| Materials - Diversified Mining | U (-6.0%) | U (-8.5%) | U (2.5%) | U (8.2%) | P (16.0%) |
| Tech - Hardware | U (-19.2%) | N (-12.4%) | U (37.7%) | N (2.4%) | N (-1.0%) |
| Tech - IT Services | N (-4.7%) | N (-7.3%) | U (2.0%) | N (-1.8%) | N (-1.4%) |
| Tech - Semiconductor Equipment | U | U (3.7%) | U (7.5%) | U (-2.5%) | U (9.0%) |
| Tech - Semiconductors | U (9.0%) | L (4.7%) | U (5.7%) | N (-6.8%) | N (-7.9%) |
| Tech - Systems Software | N (-6.7%) | N (-2.2%) | U | N (-0.2%) | N (-6.8%) |

As of this run: 4 proven, 4 not beyond luck, 31 not proven, 26 insufficient — consistent with
"about 2 proven expected by chance" being a floor, not a ceiling, and with most cells reading
"not proven" or "insufficient" rather than either extreme. (Refreshed 2026-10-04 —
BASELINE-CHECK-1 below is why some numbers moved from the previous refresh.)

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

## Size floor experiment (SIZE-FLOOR-1)

All five voting lenses carry the same $5bn minimum market cap today. `run_lens_backtest` can
measure whether Magic Formula RAW or Growth would do better (or worse) at a lower floor —
**without changing the floor the live app actually uses**: `strategies/*.yaml` is never
touched, and a run with no override is byte-identical to one before this item existed.

```python
from aristos_council.backtest import run_lens_backtest, to_csv
result = run_lens_backtest("Tech: Semiconductors", "magic_formula_raw_v1",
                           start=date(2016, 1, 31), end=date(2026, 8, 31),
                           min_cap_usd=1e9)               # instead of the lens's own $5bn
to_csv(result, "backtests/")
```

**Why the as-of floor matters more here, not less.** A lower floor does not just admit more
names — it reaches further down the size scale into names more likely to have failed, merged
away, or gone illiquid *before* today, and a membership frozen from TODAY's survivors cannot
see any of that. The as-of scaling (above) is the only thing standing between "what a $1bn
floor would have ranked in 2017" and "what today's winners looked like in 2017, relabelled
as a $1bn universe" — survivorship bias dressed up as a finding. `min_cap_usd` **replaces**
the cohort/lens file's own floor outright (never the lesser of the two) and is applied through
the exact same as-of mechanism, and it is also passed through as the lens's own
`min_market_cap_override` (FLOOR-1/2) so the lens's own gate — and its screen's, if it has one
— agree with the floor the run actually used; without that second part the lens would
re-exclude everything the loosened as-of floor had just admitted, and the run would measure
nothing.

**The liquidity guard.** Below $5bn, survivorship bias is not fully answered by as-of scaling
alone, so a second guard applies automatically: a name needs at least `min_adv_usd` (default
$3m) in mean daily dollar value traded (close × volume, from the same price feed the backtest
already reads) over the trailing 30 calendar days, estimated as of the round — not today's
volume, same principle as the cap. A name that clears the cap but fails this is excluded and
counted separately: `Round.n_illiquid` per round, `BacktestResult.total_illiquid` summed, both
round-tripped through the CSV (`n_illiquid` column, `min_cap_usd_override` / `min_adv_usd`
header lines) so "how many did the guard remove" is answered from the file itself, never
silently. The guard never applies at or above $5bn — nothing that illiquid clears $5bn anyway,
and every existing, already-validated backtest must stay untouched.

**Running the grid.** `notebooks/aristos_backtest.ipynb` has a cell ("9. Size floor
experiment") that runs `[magic_formula_raw_v1, growth_garp_v2]` × `[$1bn, $2bn, $5bn]` ×
five watched cohorts (30 runs) and writes `SIZE_FLOOR_SUMMARY.csv` — one row per (lens,
floor, cohort), with the same verdict/mean-excess/years-positive/luck columns the main
summary table uses, plus `n_eligible` and `max drawdown` (both already computed by the engine
but not previously surfaced in a summary row) and `n_illiquid`. Run it the same way as the
main grid (same Drive, same cache, same EODHD key via `getpass`, never saved) — it is an
independent cell and does not require having run the main grid first.

**The $5bn row is not a control for the main grid's `SUMMARY.csv`, unless the cohort's own
floor happens to be $5bn too.** A grid row's `min_cap_usd` REPLACES the as-of floor the
default (no-override) run would have used — and that default is `cohort_native_floor()`'s
own value, read from the cohort definition's `min_market_cap_usd` (a COHORT-3 size-TIER
value, $1–10bn by how crowded the industry is), never the lens's own $5bn gate. None of
the five grid cohorts happen to carry one at exactly $5bn (Materials - Diversified Mining
and Comms - Interactive Media & Gaming are both $2bn; Consumer - Auto Manufacturers and
Industrials - Grid & Electrical Machinery are both $1bn; Tech - Semiconductors is $3bn) —
so every row in `SIZE_FLOOR_SUMMARY.csv`, including every $5bn one, is EXPECTED to show a
smaller `n_eligible` and a different excess than `SUMMARY.csv`, because the floor itself
genuinely changed which names were eligible, not because anything is broken. The summary
cell prints each cohort's own floor and carries an `is cohort's own floor` column so this
is visible rather than something to debug: `True` on a row means it should reproduce
`SUMMARY.csv` (verified by a fixture test, `tests/test_lens_backtest.py`, where the
override exactly equals the cohort's own floor); `False` means it is a genuinely
different, stricter-or-looser experiment and a difference from `SUMMARY.csv` is the floor
working as intended, not a discrepancy.

**If two lenses read identically anyway, check `n_ranked` before suspecting a bug.** A
2026-10-02 bug report also found growth_garp_v2 and magic_formula_raw_v1 producing
IDENTICAL baskets (same excess, years positive, drawdown — only luck % differed, which is
expected: its random draw is seeded by `lens_id` too). Reproduced in a fixture
(`tests/test_lens_backtest.py`, `test_a_real_lens_screen_can_go_silently_inert_on_thin_as_of_accounts_coverage`)
with no code bug anywhere: growth_garp_v2's three screen criteria each need as-of data
that is genuinely plausible to be missing for a smaller or more recently-listed name early
in a long backtest — `min_revenue_cagr`/`max_peg_ratio` need 4 annual points surviving the
90-day lag cut (3y of change), and `min_roic` needs `invested_capital` dated via its own
`period_ends` entry (a derived balance-sheet line, plausibly less completely reported than
the income-statement lines the other two read). When every candidate in a round is short
on this data, every criterion abstains (NOT-EVAL, never a confirmed fail) for every name —
the null != false discipline doing exactly what it should, just with nothing left for it
to discriminate on — and a screened lens ranks the exact same universe an unscreened one
does. `Round.n_ranked` (how many of `n_eligible` the lens's OWN screen actually kept) was
already computed and already round-trips through the CSV; it just was not surfaced in a
summary row before this bug report. `magic_formula_raw_v1` has no screen, so its
`n_ranked` always equals `n_eligible` — that is normal. For a SCREENED lens,
`n_ranked == n_eligible` on every round means its screen confirmed zero fails that round:
check this column first whenever two lenses' numbers agree unexpectedly.

## Size floor experiment, take two (SIZE-FLOOR-2)

A 2026-10-02 real-data Colab run still showed `growth_garp_v2` reading `n_ranked ==
n_eligible` in **every** cohort and round of the grid above, including
Industrials - Grid & Electrical Machinery at its *own* $1bn floor — where SIZE-FLOOR-1's
floor-framing explanation does not apply at all (the gate matched the cohort's own
membership floor exactly), yet the committed/default run on the same cohorts did screen
(e.g. Interactive Media: Growth +8.1%/yr vs RAW +2.6%/yr). That ruled out "coincidental
thin accounts" as the sole explanation and pointed at the grid's own mechanics.

**The actual defect**: SIZE-FLOOR-1's `min_cap_usd` replaced BOTH the cohort's own as-of
membership floor (`eligible_at`'s pre-filter) AND the lens's own `min_market_cap` gate with
one number. Blending the two meant a grid row's *eligible population* — and therefore its
as-of accounts depth — was never the same population the committed/default run ever
screened, for any gate that did not happen to equal the cohort's own floor.

**The fix**: `min_cap_usd` now moves **only** the lens's own gate
(`run_rank_pipeline`'s `min_market_cap_override`). The cohort's own as-of membership floor
(`size_floor`) is never touched by it — it is always `cohort_native_floor()`'s own value,
exactly what the committed/default run has always applied. Two consequences, both load-
bearing:

- **The $5bn row now reproduces `SUMMARY.csv` for every cohort**, regardless of that
  cohort's own membership floor — not because the gate happens to match it (SIZE-FLOOR-1's
  accidental, narrow case), but because `magic_formula_raw_v1` and `growth_garp_v2` both
  already declare `min_market_cap: 5.0e9` in their own files, making a $5bn gate a no-op
  for the lens too. Pinned by a fixture test
  (`test_the_5bn_row_reproduces_the_default_run_even_when_the_cohorts_own_floor_is_not_5bn`).
- **A gate below a cohort's own membership floor is now a no-op by construction**: a name
  below that floor was never in the eligible population the lens could rank, so loosening
  the lens's own gate further can admit nothing new. `run_lens_backtest` reports this as
  `below_cohort_floor` (zero rounds run) rather than silently measuring nothing; the grid
  cell records it as its own row ("below cohort floor"), never a CSV.

**`accounts_coverage`** (`Round.accounts_coverage`, `SIZE_FLOOR_SUMMARY.csv`'s
`accounts_coverage_pct` column) is new: the share of `n_eligible` names whose as-of
`Fundamentals` carried *any* dated accounts at all (non-empty `period_ends` — the opposite
of `AsOfAdapter`'s identity-only abstain shell). It is a fact about the **names**, not the
lens — read it alongside `n_ranked` (a fact about the **lens's screen**) rather than in
place of it: a name can have 3 years of real accounts (`accounts_coverage` reads 1.0) and
still make `min_revenue_cagr`/`max_peg_ratio` abstain for want of the 4th year `min_roic`'s
own `invested_capital` series needs — `n_ranked` catches that, `accounts_coverage` alone
would not.

**Null excludes, not imputes.** A name whose screen could not evaluate a single
*substantive* criterion (`min_market_cap` excluded — a trivial, separately-gated check; see
the one actual run where this fired) now reads as "does not apply" for that lens that
round — excluded from ranking entirely, matching Company mode's own convention — rather
than reaching factor ranking where a missing factor used to be imputed under `missing:
worst`. This is a real behaviour change, not only a visibility one: it applies to every
`run_lens_backtest` call, including the committed/default runs, not only the grid.
**The existing 65 committed CSVs have not been re-run against this change** — doing so
needs live EODHD access this environment does not have; re-running cell 5 and diffing
against the committed files is the way to find which (cohort, lens) pairs actually move.

**The decision rule (item 2c), pre-registered before any result is read**: for each lens
and each gate below $5bn, count the cohorts (among those actually run at both that gate and
$5bn) where the lower gate beats the $5bn row by ≥2 points of annual excess *and* its own
`luck_pct` is ≤25% ("better"), and where it is worse by ≥2 points ("worse"). The grid cell
prints "lower the gate" only when better ≥9 of 13 and worse ≤2 — otherwise "keep $5bn". The
threshold is fixed at 9-of-13 (the cohort count `watch.yaml` carried when this rule was
written), not rescaled to however many cohorts actually finished a given session: a partial
grid is read as a partial answer, never a looser bar.

**Not run against real data from this environment** — no EODHD/live-data access here. The
grid, the decision rule's printed verdict per (lens, gate), and the "4-cohort RAW result"
a prior report referenced were all measured under the now-replaced SIZE-FLOOR-1 design and
are not valid under SIZE-FLOOR-2's: eligibility itself may have changed for any cohort
whose own floor is not $5bn. **Re-run notebook cells 9 and 10** (`run_lens_backtest`,
`cohort_native_floor`) against real Drive-cached data to get numbers that mean what this
section says they mean, then record the actual decision-rule lines and any numeric example
here — not before.

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

## Refreshing the results

The 13-cohort × 5-lens run behind the matrix above (and every badge in the app) is only as fresh
as its end date (2026-09-01). To refresh it:

1. In Colab, re-run `notebooks/aristos_backtest.ipynb` end to end.
2. Run cell 8, which zips the 13 cohort folders plus `SUMMARY.csv` into `backtests_export.zip`
   and offers it for download.
3. Unzip it locally, over `backtests/` (`backtests/<cohort_slug>/<lens_id>.csv` and
   `backtests/SUMMARY.csv` — the same layout already committed).
4. `python -m scripts.backtest_badge_matrix` — regenerates the matrix above (paste its output
   over the table) and rewrites `backtests/RUN.md` with today's date.
5. Commit the refreshed `backtests/` (results, RUN.md) and the updated matrix in this file
   together, as one commit.

A single cohort × lens can also be refreshed on its own from the CLI (`--years 10` moves the
window forward a year): `python -m aristos_council.backtest run --cohort ... --lens ... --out
backtests/`, then `python -m aristos_council.backtest summary backtests/` to read it — but the
matrix above and `backtests/RUN.md` should still be regenerated afterward so they describe
exactly what is committed. A cohort that has been re-built (`v2`) is a different cohort: its file
records the version, and a rebuilt cohort should be re-run rather than compared with the old file.

`backtests/RUN.md` (written by the script above) states the date `backtests/` was last refreshed,
so a reader of the committed results — or of a badge in the app — can tell at a glance how stale
they are.

## What it will be used for

**Shipped:** every lens's vote now carries a plain-English track-record badge, read from this
file — see "Track-record badges" above. It is display only; no strategy, screen or verdict reads
a `backtests/` file to decide anything, and no vote is capped, reweighted or hidden because of its
badge.

**Not yet built:** a GATE that changes a vote's weight because of its badge — a lens that is not
"proven" somewhere (whatever its badge reads) shown as a vote without the weight of a proven one.
That would need to read the FULL verdict (the one `luck_pct_mean` already gates INTO "proven"),
never the plain excess/years bar alone — see "Skill versus luck" above for why the bar by itself
is not enough. Nothing in the repo does this today.

Further out, and also not built, a backtest-gated vote is planned to be one of four quantitative
methods (alongside fair-multiple valuation, a composite score, and Gap Ledger's earnings drift) that
converge on one shared engine called from both sides of the Analyse tab — see
[docs/GAP_LEDGER.md § Relationship to Aristos Council](GAP_LEDGER.md#relationship-to-aristos-council).
