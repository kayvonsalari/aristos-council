# ABSOLUTE-BACKTEST-REVIEW: independent review of the ten-year backtest, and three cuts

**Verdict: the result stands, but it is weaker evidence than it looks.** No form beat the industry basket, and nothing below
turns that into an edge. But the test was handicapped against the forms by about half a point a year, its "luck" baseline
flatters them, its gate is close to a no-op, it threw out future stock-splitters, and the loss sits in 2016 to 2020 (form 2
earns +1.1 points over the basket in 2021 to 2025). "No edge shown" is the right reading; "no edge exists" is not what this
test can say.

Reviewed: PR #204 (`src/aristos_council/absolute_backtest/`, `backtests_absolute/`, `docs/ABSOLUTE_BACKTEST.md`), merged at
`7c01b7b`. No rule, threshold, form or definition was changed. One data-handling bug was fixed (finding A1). Everything below
was computed from the 2026-11-04 day cache: no EODHD units were spent and no model was called.

## How this was checked

- **Reproduction.** `examples/review_absolute_pass.py` reads every eligible name of every round once, as the runner does, and
  keeps what the result files do not (the names that were not chosen, their base yields, the pool each round was drawn from).
  `examples/review_absolute_cuts.py` rebuilds each industry's 12-month result from that with the runner's own selection and
  scoring functions. It reproduces the published numbers exactly: largest difference in excess over the basket 0.0000
  points over 58 industries x 3 forms, luck identical, and the chosen names equal the form files in 6,322 of 6,322 rounds for
  form 2 and for form 3. So the cuts below are cuts of the published results, not of a new run.
- **Hand-check** (`examples/review_absolute_handcheck.py`, output in `backtests_absolute/cuts/handcheck.txt`), round of
  2019-10-31, figures computed from the raw cached EODHD payload, raw yfinance bars and raw bond rows, independently of the
  pipeline's code:

  | | Microsoft (USD / USD) | Tencent 0700.HK (HKD price, CNY accounts) |
  |---|---|---|
  | Newest visible year | FY2019, filed 2019-08-01 (a real date) | FY2018, no filing date: period end + 90 days |
  | Free cash flow per share, three years (before working capital, minus capital spending, over shares) | 4.436, 1.512, 3.384 | 7.821, 7.941, 5.402 |
  | Base: lower of latest and three-year average | 3.111 | 7.055 |
  | Price | 143.37 USD | 295.73 HKD / 1.1108 HKD per CNY = 266.23 CNY |
  | Yield on base, independent / pipeline | 0.02170 / 0.021697 | 0.02650 / 0.026499 |
  | Hurdle bond, independent / pipeline | US10Y 1.6894 on 2019-10-31 / same | CN10Y 3.281 on 2019-10-31 / same |

  The bond is the statements' currency (CNY for Tencent), the price is converted into the statements' currency by the
  month-end rate on or before the round date, and the per-share conversion uses the share count of the same fiscal year.
  Both agree to four digits. I did not check the cached closes against an exchange record.

## Part A: the review

### A.1 Look-ahead

**Held up.**
- Statements: filing-date mode shows a period when its filing date is on or before the round date
  (`data/asof_adapter.py:115-125`, `_visible`); a period is "filed" when the latest date across its statements is
  (`:127-139`). Every dated list is cut by its own dates (`:229-288`). Microsoft's FY2019 appears on 2019-08-01, not before.
- Price, share count, bond yield: the price is the last close on or before the date within 10 days
  (`absolute_backtest/market.py:33-38`, `backtest.py:222`); the share count is the visible period's own
  (`asof_adapter.py:195-199`); the bond row is the last on or before the date, lapsing after 7 days
  (`market.py:163-182`). The FX rate is built through the as-of adapter, so no future bar enters it
  (`abs_readings.py:1112-1132`).
- The three-year average takes the three newest VISIBLE periods (`absolute_value/engine.py:233-296` over the cut lists), so it
  cannot reach a year not yet filed. The own 5- and 10-year windows are built from the cut record only
  (`absolute_backtest/evaluate.py:87-111`, `asof_growth_history`).

**Did not hold up (A1, B4, B5 below).** The split rule used the future to remove names; the cohort itself is today's list; the
90-day fallback can show a late filer's accounts early.

### A.2 Lag

- Real filing dates exist only for US names: 899 of 993 US members have them, and almost none elsewhere (Toronto 10 of
  117; Hong Kong, London, Germany, Korea, Australia, Paris, Switzerland, Stockholm, Madrid: none). So the "44% with filing
  dates" is "the US", and the "56% on the 90-day fallback" is "everything else". Any difference between the halves is a
  US-versus-the-rest difference, not a freshness difference (`backtests_absolute/cuts/lag_split.json`).
- Real US filings come a median 58 days after the period end (10th to 90th percentile 44 to 90 days); 9.5% come later than 90.
  So the fallback shows a name's accounts about a month LATER than a US-style filing would (conservative), but for a late
  filer it can show them EARLY. If the other markets' late tail resembles the US one, roughly one period in ten for the
  undated names is seen up to a few weeks early. The size of that cannot be measured with the data held.
- Mean age of the newest visible accounts at a round: 236 days for dated names, 258 for undated (median 243 and 273). The code
  treats the two halves identically: no flag, no discount (`asof_adapter.py:115-125`).
- The signal is old in both halves: annual accounts only, so the newest year is on average eight months old, and up to
  fifteen at the end of a filing cycle. A cheapness signal of that age against a current price is a design choice, not a bug,
  but it is a reason a real edge could be blurred.

### A.3 Returns

- **Measured round date close to exit date close, adjusted for splits and dividends** (`market.py:43-51`: the ratio of
  yfinance adjusted closes). Correct. The return is in the LOCAL currency (see B.8).
- **Each round equal, not each stock.** Each round is one equal-weight basket; rounds are averaged within a calendar year and
  years averaged (`scoring.py:95-102`, `131-144`). Correct. A side effect: 2016 has three rounds and counts as much as 2017's
  twelve.
- **Cost once per round trip.** `net = mean of the held names - cost` (`scoring.py:79`): once per basket, not per name, and
  once for a 36-month hold. Correct as written, but the comparison basket pays none (B.1).
- **A name that delists mid-hold** has no exit price, so its return is None and it is left out of the form AND of the basket
  for that round (`runner.py:244-248`, `market.py:49-51`). It does not get its last price and no delisting loss is booked.
  Since the list is today's, delisted companies are not in it at all (the documented survivorship limit), so this path
  affects only suspended or thinly traded names.

### A.4 The basket

Built from every member with a price at entry and exit that clears the size floor, whether or not the gate excluded it
(`runner.py:244-250`); held buy-and-hold for the same window as the forms; equal-weight; no cost. It excludes only the 386
name-rounds with no accounts visible. Correct, apart from the cost.

### A.5 Luck test and drop-best

Same size (`n_held`) and same turnover (`n_new`) as the form each round, drawn from the same pool of eligible priced names
(`scoring.py:155-187`); drop-best compared with the median of random baskets' drop-best (`:184-186`). As designed.
**But the random baskets are not centred on the pool** (B.2).

### A.6 Signs and units

Hand-checked above. Pre-working-capital base: operating cash flow minus the change in working capital (as the provider reports it; I checked the arithmetic, not the provider's sign convention), minus capital spending taken as positive, over the period's share count: matches by hand for both
names. Hurdle currency is the statements' currency; the price is converted into it. One thing to know: the EODHD payload
carries no diluted weighted-average shares in its dated lists (`diluted_shares_annual` is empty), so the base uses the
period's reported count ("basic shares"), not the diluted one the definition names. It reads the lists by position, not by
date, for EODHD names (`tools/screening.py:1625-1668`): 23 of 2,018 names (1.1%) have a list whose dates differ from the
others' in the newest six periods, so a year's working-capital change or share count may come from another year for those.

### A.7 Anything else

- **The gate is almost a no-op.** 95.6% of eligible name-rounds pass (162,615 of 170,049); 3.6% are excluded, 0.7% are
  "not enough data", and no name was "not covered". Form 1 is therefore the basket minus 4% of it, minus the cost.
- **The test cannot see what the gate is for** (documented): failures are not in today's list.
- **Power.** Over 58 industries the random baskets' pooled mean excess has a spread of 0.62 points a year, so the pooled
  number resolves differences of a point or so. Per industry the spread is 3.2 points (form 2, median; 2.4 to 4.5 across
  industries). To reach "luck under 5%" an industry needs roughly 5 points over a random basket; the "2 points" part of
  the bar never binds. A true edge of 2 to 3 points a year in one industry is invisible to this test.
- **Splits inside the share-count band** (a 3-for-2 is a ratio of 1.5, inside `SPLIT_RATIO_BAND = (0.6, 1.7)`) are not seen at
  all, so a per-share base across one is off by that factor; a split among the newest four periods withholds the name for
  about three years (`evaluate.py:167`, by design).

## Findings by severity

### A. A bug that changes results

**A1. The split rule used the future to remove names (fixed).** `AsOfAdapter` withholds the market cap of any name whose
provider share count jumps by a split-sized ratio in ANY later period, up to today (`asof_adapter.py:290-299`,
`_split_after`), and `evaluate_name` then gave such a name no base yield and no verdict. So a name that split in 2022 was
absent from forms 2 and 3 in every round before 2022, while the basket, form 1 and the random baskets held it. Splitters are
mostly winners.

- Size: 9,836 of 162,615 gate-passing name-rounds (6.0%) were withheld; 6.2% of priced names in the measured sample; 77% of
  them were forward splits. Over the next 12 months they earned 31.7% against 23.8% for the other names
  (`backtests_absolute/cuts/lag_split.json`). A picker that cannot hold them falls behind the basket by 0.46 points a year
  (round-weighted; 0.38 equal-weighted across industries; negative in 24 of 57 industries). Form 2 picks cheap names and
  splitters are probably rarely cheap (not measured), so the effect on form 2 and 3 is probably smaller than that bound; the
  gold run below shows it is not zero.
- Fix (`absolute_backtest/evaluate.py:33-84, 167-175`): a split after the visible accounts now restates the visible share
  counts to today's basis (the closes are split-adjusted to today), but only when the jump is within 12% of a real split
  ratio; any other jump still withholds. A split inside the visible record still withholds. Tests first
  (`tests/test_absolute_backtest.py`, `test_a_split_that_happens_after_the_round_date_...` failed before the change,
  `test_a_split_inside_the_visible_accounts_still_withholds_...` pins what did not change).
- Effect, re-run from the cache with the old rule against the new one (`examples/review_absolute_rerun.py`,
  `backtests_absolute/cuts/rerun/`): the two cheapest industries (21 and 23 members) do not change: Grid & Electrical
  Machinery form 2 +13.8 / luck 1% before and after; Commodity Chemicals form 2 -7.2 / luck 88% before, 89% after. The
  old rule reproduces the published numbers exactly. As a demonstration where it bites (a third run, the most exposed
  industry, 16.5% of names withheld): Gold form 2 reads +3.1 / luck 22% before and +3.8 / luck 15% after at 12 months, and
  -2.1 / 72% before and -0.9 / 58% after at 36 months.
- **Not re-run for all industries, as instructed: the published `backtests_absolute/` files and `docs/ABSOLUTE_BACKTEST.md`
  still carry the old rule.** The pooled effect is not measured; the bound above is the most it can be for a random picker.

### B. A weakness that blurs the result

**B1. The comparison basket pays no cost, the forms pay 0.5% (`scoring.py:76` against `:79`).** On a 12-month hold the
excess is lowered by exactly 0.5 points a year. Pooled, 12-month, over the industry basket: form 1 -0.77 becomes -0.27, form
2 -0.44 becomes **+0.06**, form 3 -0.98 becomes -0.48. The published statement that none of the forms beat the basket rests on
a margin the cost alone explains for forms 1 and 2. (The random baskets pay the cost too, so luck is not affected by this.)

**B2. The luck test's random baskets are centred 0.33 to 0.36 points BELOW the pool.** An unbiased random basket earns the
pool's return minus the cost, so -0.50 over the basket. The persistent baskets of `backtest._TurnoverBaskets`
(`backtest.py:1131-1186`) earn -0.83 (form 2 size) and -0.89 (form 3 size) pooled over the 58 industries; fresh uniform draws
of the same sizes earn -0.51 and -0.49 (`backtests_absolute/cuts/luck_check.txt`). The probable cause, measured but not
isolated: persistent baskets under-hold names that are new to the pool, and those names are the ones that rose into today's
list (B.4: 31.6% over the next year against 19.1%), while the forms and the basket hold them at once. Effect on luck, with
the persistent spread kept and the centre moved to -0.50:

| Form | Published luck | Recentred luck | Fresh uniform draws (too narrow: no persistence) |
|---|---|---|---|
| 1 | 99.2% | 100% | 100% |
| 2 | 26.0% | 43.8% | 36.8% |
| 3 | 55.4% | 77.8% | 93.2% |

Every luck score in the results is too kind to the forms, so the lists of industries "passing the bar" (8 for form 2, 6 for
form 3) are longer than they should be. It does not touch the excess over the basket.

**B3. The gate does almost nothing here, and the test could not credit it.** See A.7 and Cut 3.

**B4. The pool is today's list, which has look-ahead of its own.** A name enters the pool when its value crosses the size
floor; to be in today's list at all it had to end above it. Names new to the pool in a round earned 31.6% over the next twelve
months against 19.1% for the rest (5.4% of the pool in those rounds). The forms and the basket share this; the random baskets
do not (B2).

**B5. The 90-day fallback is the entire non-US universe** (A.2): staler on average by about 20 days, and early for the late tail.
Not flagged, not separated, and the halves differ in country as much as in freshness.

**B6. Annual accounts only**: the newest year is about eight months old (A.2).

**B7. Returns are in each name's own currency and the index fund is in dollars.** The "vs index" and "vs cash" columns mix
currencies for non-US names. The industry basket mixes currencies equally with the forms, so the bar is not affected.

**B8. Overlap and weighting**: twelve overlapping 12-month holds a year are not twelve independent tests (about ten
independent years per industry), and years weigh equally whatever the number of rounds in them.

**B9. Low power** (A.7).

### C. Cosmetic

- `docs/ABSOLUTE_BACKTEST.md` states "44% of names (40% of decisions) had real filing dates" without saying they are the US.
- "Median names held" in the overall rows is the mean of the industries' medians.
- The positional read for 1.1% of names (A.6) and the missing diluted-share count (A.6) are worth a line in the doc.

## Part B: three cuts

Twelve-month hold; points a year over the industry basket; pooled as `SUMMARY.csv` pools (each industry once per year, then the
years). Luck is recomputed exactly (500 persistent random baskets, same seeds) on the subset, so it carries the bias of B2: read
it as "lower is better", not as a probability. Files: `backtests_absolute/cuts/` (`cut1_by_period.csv`, `cut1_by_year.csv`,
`cut2_basket_size.csv`, `cut3_gate_effect.csv`, `cut3_by_industry.csv`, `cuts_summary.txt`).

### Cut 1: by period

| Form | 2016-2020 entries | luck | 2021-2025 entries | luck | all |
|---|---|---|---|---|---|
| 1 gate alone | -0.45 | 21% | -1.10 | 100% | -0.77 |
| 2 cheapest third | **-1.96** | 92% | **+1.09** | 2.2% | -0.44 |
| 3 absolute verdict | -1.81 | 87% | -0.15 | 23% | -0.98 |

Entry years, form 2: 2016 -1.4, 2017 -0.3, 2018 -2.3, **2019 -8.0**, 2020 +2.2, 2021 +2.5, 2022 +2.7, 2023 -0.4, 2024 -0.6,
2025 +1.2. The loss is in the value drought: one year (2019) carries most of it, and 2020 to 2022 give it back. Form 2 wins 1
of 5 years in the first half and 3 of 5 in the second. The pooled "no edge" averages a drought and a recovery, so it says
less about the method than the single number suggests; it is also the pattern one expects of a plain free-cash-flow-yield tilt,
which is what form 2 is. Form 3 shows the same shape, weaker (+4.2 in 2021, -2.1 in 2024 and -2.8 in 2025). Form 1 is flat at about the cost in the
first half and worse in the second (-1.1), probably because the few names the gate removes did better than the rest in the recovery; not tested.

### Cut 2: basket size

| Form | Restriction | Industries | Median names | Excess | Luck | Winning years |
|---|---|---|---|---|---|---|
| 2 | all rounds (as published) | 58 | 6 | -0.44 | 26.0% | 4 of 10 |
| 2 | rounds holding 4 or more names | 58 | 7 | -0.57 | 32.0% | 5 of 10 |
| 2 | industries with 4+ names in 80%+ of rounds | 39 | 8 | -1.43 | 81.2% | 2 of 10 |
| 3 | all rounds (as published) | 58 | 6 | -0.98 | 55.4% | 2 of 10 |
| 3 | rounds holding 4 or more names | 56 | 7 | -0.43 | 21.2% | 3 of 10 |
| 3 | industries with 4+ names in 80%+ of rounds | 26 | 8 | -0.84 | 51.6% | 4 of 10 |

Small baskets are not hiding an edge: with at least four names the numbers barely move, and the industries that reliably
have four or more names (the well-populated ones) do worse for form 2, not better. Form 3 on baskets of four or more
(-0.43, luck 21%) is no better than form 2 on the same footing (-0.57): the ten-year question adds nothing visible, on a test
too coarse to rule out a small gain. Neither reaches luck under 5%.

### Cut 3: the gate's own contribution

| | Industries | Excess | Luck | Winning years |
|---|---|---|---|---|
| Form 2: cheapest third of the gate-passers | 58 | -0.44 | 26.0% | 4 of 10 |
| Cheapest third of ALL eligible names, no gate | 58 | -0.45 | 26.0% | 4 of 10 |

The gate's contribution to form 2 is 0.01 points. It takes 998 name-rounds out of the cheapest third, of 41,637 (2.4%); those
earned a mean 12.9% over the next year against the basket's 14.1% in the same rounds, which is the right direction and a tiny
amount. Everything form 2 earns or loses comes from the cheapness rule. The test cannot give the gate credit for the failures
it was built to avoid (A.7); what it shows is only that on survivors, in this universe, the gate neither helps nor hurts.

## What this says about "the method or the test"

- **The gate (as a return rule): the test cannot say.** It is a no-op on a list of survivors.
- **Form 2 is a plain value tilt,** and it behaves like one: roughly zero over the decade net of an unfair half point, minus
  two a year in the 2016 to 2020 drought, plus one in 2021 to 2025. It is not evidence for or against the method.
- **Form 3 (the ten-year question) adds nothing measurable over form 2**, on any cut. This is the owner's question, and the
  answer so far is "not shown", from a test with 3 to 4 points of per-industry noise.
- **The test is the larger part of why the answer is soft.** Fix the cost symmetry, recentre the luck baseline, keep the
  future-splitters, and rerun, and the published conclusions that would move are the luck scores and the pass lists, and form 2
  and form 3 by an estimated half point or less. None of that makes an edge. All of it makes "no edge" less certain.

## If the backtest is run again (not done here; no rule changes)

1. Charge the basket the same round-trip cost, or add it back (B1).
2. Centre the random baselines (B2): draw them so that a name new to the pool is as likely to be held as any other.
3. Run with the A1 fix and publish the pooled before-and-after.
4. Report US names (real filing dates) and the rest separately (A.2).

## Files

`examples/review_absolute_pass.py`, `_cuts.py`, `_luck_check.py`, `_lag_split.py`, `_rerun.py`, `_handcheck.py`;
`backtests_absolute/cuts/`; fix in `src/aristos_council/absolute_backtest/evaluate.py` with two tests in
`tests/test_absolute_backtest.py`.

## Recheck on a subset of 12 industries (ABSOLUTE-BACKTEST-RECHECK)

**Verdict: full re-run needed.** The pooled change is +0.46 points for form 2 and +0.35 for form 3 (both over the 0.3 line), and
one industry (Consumer - Auto Parts, form 2) changes pass/fail. Estimated effect of a full re-run: every 12-month excess rises by
exactly 0.5 points from the cost correction (arithmetic, all 58 industries); the split fix moves the pooled figure by about
-0.2 to 0 on this subset and an industry by up to 1.9 points either way (Copper & Aluminum form 3 -1.9, Gold form 2 +0.7); the
luck scores move by several points either way, so the pass lists change at the margin. Pooled over 58, form 2 would read about
+0.06 and form 3 about -0.5 against the basket, and no form would clear the bar pooled.

Setup. Subset: the 8 form-2 passers, Gold, and 3 drawn at random (`random.Random(20261104).sample` of the other 49):
Utilities - Electric, Tech - Communications Equipment, Consumer - Restaurants. 12-month hold, same window and code, day cache
only, `EODHD_API_KEY` blank so nothing could be fetched: 0 of 418 members lacked accounts, 2 lacked prices (skipped, counted).
Corrections: **split** = the fix on this branch; **cost** = the industry basket pays the same 0.5% round trip as the forms (on a
12-month hold this adds exactly 0.5 points to every excess), together with a **luck baseline re-centred** on the pool (each
year's 500 random series keep their spread, their mean is set to the unbiased value; this is what makes the luck scores
comparable once the basket pays the cost). The "old" column reproduces the published numbers to 0.0000 points. Scripts:
`examples/review_absolute_pass.py --fixed`, `examples/recheck_absolute_subset.py`; output `backtests_absolute/cuts/recheck/`.

Pooled over the 12 (excess / luck / median names; old, split only, cost + centring only, both):

| Form | Old | Split only | Cost + centring only | Both | Both minus old |
|---|---|---|---|---|---|
| 1 | -0.61 / 82% | -0.61 / 82% | -0.11 / 81% | -0.11 / 81% | +0.50 |
| 2 | +4.39 / 0.0% | +4.35 / 0.0% | +4.89 / 0.0% | +4.85 / 0.0% | +0.46 |
| 3 | +0.25 / 34% | +0.10 / 39% | +0.75 / 33% | +0.60 / 34% | +0.35 |

(The subset holds the form-2 passers, so its form 2 figure is far above the all-industry one; compare old with new, not with
the 58-industry table.) Basket size is unchanged everywhere except that the split fix changes the form 3 basket of Copper & Aluminum from 3 to 4 names.

**Form 2** (excess over the basket, points a year / luck % / median names held)

| Industry | Old (published) | Split fix only | Cost + luck centring only | Both | Both minus old | Bar old to new |
|---|---|---|---|---|---|---|
| comms_interactive_media_gaming | +6.97 / 4.4% / 8 | +6.97 / 4.4% / 8 | +7.47 / 3.2% / 8 | +7.47 / 3.2% / 8 | +0.50 | PASS to PASS |
| consumer_auto_parts | +2.70 / 2.6% / 9 | +2.70 / 2.6% / 9 | +3.20 / 6.6% / 9 | +3.20 / 6.6% / 9 | +0.50 | PASS to no |
| consumer_luxury_apparel_footwear | +4.24 / 1.8% / 10 | +4.24 / 1.8% / 10 | +4.74 / 2.4% / 10 | +4.74 / 2.4% / 10 | +0.50 | PASS to PASS |
| industrials_grid_electrical_machinery | +13.77 / 0.6% / 3 | +13.77 / 0.6% / 3 | +14.27 / 0.4% / 3 | +14.27 / 0.4% / 3 | +0.50 | PASS to PASS |
| materials_copper_aluminum | +14.86 / 0.0% / 5 | +14.58 / 0.0% / 5 | +15.36 / 0.0% / 5 | +15.08 / 0.0% / 5 | +0.23 | PASS to PASS |
| materials_diversified_mining | +15.42 / 4.0% / 3 | +15.42 / 4.0% / 3 | +15.92 / 4.8% / 3 | +15.92 / 4.8% / 3 | +0.50 | PASS to PASS |
| materials_packaging_paper | +5.46 / 0.0% / 8 | +5.03 / 0.2% / 8 | +5.96 / 0.0% / 8 | +5.53 / 0.2% / 8 | +0.07 | PASS to PASS |
| utilities_gas_water_multi_utilities | +3.59 / 2.0% / 8 | +3.58 / 2.2% / 8 | +4.09 / 1.0% / 8 | +4.08 / 2.0% / 8 | +0.49 | PASS to PASS |
| materials_gold | +3.11 / 21.8% / 4 | +3.80 / 15.0% / 4 | +3.61 / 22.0% / 4 | +4.30 / 15.0% / 4 | +1.19 | no to no |
| utilities_electric | +0.63 / 26.2% / 4 | +0.63 / 26.2% / 4 | +1.13 / 28.6% / 4 | +1.13 / 28.6% / 4 | +0.50 | no to no |
| tech_communications_equipment | -11.01 / 90.2% / 4 | -11.01 / 90.2% / 4 | -10.51 / 87.6% / 4 | -10.51 / 87.6% / 4 | +0.50 | no to no |
| consumer_restaurants | -1.56 / 62.8% / 7 | -1.61 / 68.4% / 7 | -1.06 / 65.4% / 7 | -1.11 / 68.4% / 7 | +0.45 | no to no |

**Form 3** (excess over the basket, points a year / luck % / median names held)

| Industry | Old (published) | Split fix only | Cost + luck centring only | Both | Both minus old | Bar old to new |
|---|---|---|---|---|---|---|
| comms_interactive_media_gaming | +1.32 / 34.6% / 7 | +1.32 / 34.6% / 7 | +1.82 / 29.8% / 7 | +1.82 / 29.8% / 7 | +0.50 | no to no |
| consumer_auto_parts | +3.75 / 0.4% / 10 | +3.75 / 0.4% / 10 | +4.25 / 0.8% / 10 | +4.25 / 0.8% / 10 | +0.50 | PASS to PASS |
| consumer_luxury_apparel_footwear | -0.64 / 41.8% / 10 | -0.64 / 41.8% / 10 | -0.14 / 51.8% / 10 | -0.14 / 51.8% / 10 | +0.50 | no to no |
| industrials_grid_electrical_machinery | +11.60 / 3.4% / 3 | +11.60 / 3.4% / 3 | +12.10 / 2.8% / 3 | +12.10 / 2.8% / 3 | +0.50 | PASS to PASS |
| materials_copper_aluminum | +0.15 / 45.6% / 3 | -1.74 / 64.0% / 4 | +0.65 / 41.0% / 3 | -1.24 / 52.6% / 4 | -1.40 | no to no |
| materials_diversified_mining | -6.37 / 74.2% / 2 | -6.37 / 74.2% / 2 | -5.87 / 77.6% / 2 | -5.87 / 77.6% / 2 | +0.50 | no to no |
| materials_packaging_paper | +0.52 / 27.0% / 9 | +0.52 / 27.0% / 9 | +1.02 / 28.6% / 9 | +1.02 / 28.6% / 9 | +0.50 | no to no |
| utilities_gas_water_multi_utilities | -1.25 / 66.6% / 6 | -1.25 / 66.6% / 6 | -0.75 / 63.0% / 6 | -0.75 / 63.0% / 6 | +0.50 | no to no |
| materials_gold | +0.04 / 39.6% / 1 | +0.04 / 39.6% / 1 | +0.54 / 45.4% / 1 | +0.54 / 45.4% / 1 | +0.50 | no to no |
| utilities_electric | -3.80 / 76.8% / 1 | -3.80 / 76.8% / 1 | -3.30 / 76.8% / 1 | -3.30 / 76.8% / 1 | +0.50 | no to no |
| tech_communications_equipment | -6.07 / 59.4% / 4 | -6.07 / 59.4% / 4 | -5.57 / 57.2% / 4 | -5.57 / 57.2% / 4 | +0.50 | no to no |
| consumer_restaurants | -2.72 / 75.8% / 4 | -2.72 / 75.8% / 4 | -2.22 / 76.2% / 4 | -2.22 / 76.2% / 4 | +0.50 | no to no |

What drove what. The **cost** correction is +0.50 everywhere and is the whole of the change except in four cases. The **split fix**
changes Copper & Aluminum (form 2 -0.28, form 3 -1.89), Packaging & Paper (form 2 -0.43), Gold (form 2 +0.69) and Restaurants
(form 2 -0.05), and nothing else; it is small pooled (-0.04 form 2, -0.15 form 3) and mixed in sign, so the review's bound of
0.46 for a random picker overstated it for these cheapness-based forms. The **luck re-centring** moves luck
by a few points in either direction (Auto Parts form 2 2.6% to 6.6%, which takes it off the pass list; Grid & Electrical Machinery form 2 0.6% to 0.4%). Of the 8 form-2 passers, 7 keep their
pass; Gold stays a "no" (+4.30, luck 15%). Form 3 keeps its two passers in the subset (Auto Parts, Grid & Electrical Machinery).
