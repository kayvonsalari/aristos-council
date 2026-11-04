# The ten-year backtest of the absolute method (ABSOLUTE-BACKTEST-1, phase 4)

Question: did names the absolute method likes beat (a) a world index fund, (b) cash and (c) an equal-weight basket of the
same industry, over ten years? Run per industry, 58 industries, no model calls. Results are in `backtests_absolute/`
(one folder per industry; `SUMMARY.csv` and `COUNTS.csv` across all). That is the **published first run, kept untouched as the
baseline**; the **corrected run** (split fix, two-sided cost, re-centred luck test) is in `backtests_absolute/corrected/` with its
own `SUMMARY.csv`, `COUNTS.csv` and `COMPARISON.md`, and is what the company page reads (see "Corrected run" below). Design: `docs/DESIGN_absolute.md` section j.

```
python -m aristos_council.absolute_backtest run        # resumes at the last finished industry
python -m aristos_council.absolute_backtest status
python -m aristos_council.absolute_backtest summarise  # writes SUMMARY.csv and COUNTS.csv
```

`notebooks/absolute_backtest.ipynb` is the same run for Colab (install, key, `--budget`, resume). The run spends EODHD units
only (19,738 for the full run), never Anthropic credits.

## Method

- Monthly rounds over ten years, using accounts as they were visible at each date. Gate `absolute_gate_v2`, valuation
  `absolute_value_v3`. Each form is held equal-weight for 12 and for 36 months with 0.5% round-trip cost.
- **Form 1, gate alone:** every name that passes the gate.
- **Form 2, gate plus relative cheapness:** inside each industry, the cheapest third of the qualifiers on free cash flow
  yield. Ranked within the industry, so no bet on cheap industries. This is the form with published support.
- **Form 3, gate plus the absolute verdict:** only names reading "clears hurdle" on the backtest form (own 5-year and
  10-year records plus the downside case, no consensus). This is the owner's question; it has no published evidence.
- Comparisons, each separately: the equal-weight basket of all the industry's members with data that month; the world
  index fund; the US 3-month bill. Excess is a yearly average (a 36-month hold is annualised).
- **Luck:** share of 500 random baskets of the same size and turnover, drawn from the same industry, that did as well.
  Lower is better. **Drop-best:** result with the single best name removed.
- **The bar (12-month hold only):** at least 2 points a year over the industry basket and luck under 5%. Winning years are
  reported, not required, because cheapness has long droughts. The 36-month hold has far fewer independent rounds and is
  reported without pass or fail. An industry with too few rounds reads "thin".

## What cannot be tested

Everything that rests on analysts. The data holds no analyst forecasts as they stood on a past date, so consensus growth,
forward earnings yield and the consensus case are **not tested** here. The downside case uses its fallback (own 5-year rate
in years 1 and 2). The company page shows those parts for today; no number in this document says whether they work.

## Limits, said plainly

- **Survivorship.** The company list is today's market index. Companies that were delisted, merged or went bankrupt are not
  in it, and a test that removes failing companies is flattered most by their absence. The gate in particular can never be
  called proven by this test. Read every result as "no edge shown in a test that cannot see failures", never as proof.
- **Restatement.** Accounts are the provider's restated record, not the figures first published.
- **Filing-date coverage.** Only 44% of names (40% of decisions) had real filing dates (EODHD accounts); in practice these are the
  US names, and every other market used the fallback. The rest used
  period end plus 90 days, a rule of thumb that can let the test see numbers early.
- **Few names.** Form 2 held a median of about 6.5 names and form 3 about 6; in about a third of form-3 rounds fewer than 4.
  Small baskets move on one stock.
- **Prices** come from yfinance, adjusted for splits and dividends.

## Independent review (ABSOLUTE-BACKTEST-REVIEW, ABSOLUTE-BACKTEST-RECHECK)

`ABSOLUTE_BACKTEST_REVIEW.md` reviews this run and cuts it three ways. Read the tables above with three things in mind: the
results **predate the split fix** (names that split after a round date were kept out of forms 2 and 3); the **industry basket
paid no cost** while the forms paid 0.5%, which lowers every 12-month excess by exactly 0.5 points; and the **luck scores are
too kind** (the random baskets sit about 0.35 points below the pool). A recheck on 12 industries with all three corrected moved
form 2 by +0.46 and form 3 by +0.35 points pooled and took one industry (Consumer - Auto Parts, form 2) off the pass list; a full
re-run was needed before these tables are quoted as final, and it has been done: see "Corrected run" below. The tables in
"Results, all industries", "Results by industry" and "Reading the numbers" are the **baseline** and are kept as published.
Nothing in the review shows an edge.

## Corrected run (ABSOLUTE-BACKTEST-CORRECTED)

All 58 industries re-run on the same rounds, rules, thresholds and forms (window 2016-10-31 to 2026-10-31), with three
corrections and nothing else changed: (1) the **split fix** (a name that splits after a round date is restated to today's share
basis instead of being withheld; withheld name-rounds fall from 9,836 to 6,277); (2) the **industry basket pays the same 0.5%
round trip** as the forms (once per hold, so +0.50 points a year on a 12-month hold and about +0.15 on a 36-month hold);
(3) the **luck test is re-centred**: each year's 500 random series are shifted so they average zero against the basket, spread
kept (the drop-best comparison gets the same shift). Day cache only, no data bought: 2,044 members, 2 without accounts and 7
without prices, skipped and counted as in the baseline. Output: `backtests_absolute/corrected/`; the baseline-to-corrected
tables are regenerated by `examples/compare_absolute_corrected.py`.

**Pooled over every industry** (points a year over the industry basket / share of random baskets that matched):

| Form | Hold | Baseline | Corrected | Change | Verdict (12-month) |
|---|---|---|---|---|---|
| 1 gate alone | 12 | -0.77 / 99.2% | -0.27 / 100% | +0.50 | does not pass |
| 1 gate alone | 36 | -0.92 / 100% | -0.78 / 100% | +0.14 | reported only |
| 2 cheapest third | 12 | -0.44 / 26.0% | +0.04 / 44.8% | +0.47 | does not pass |
| 2 cheapest third | 36 | -1.15 / 33.2% | -0.99 / 98.4% | +0.16 | reported only |
| 3 absolute verdict | 12 | -0.98 / 55.4% | -0.49 / 79.4% | +0.49 | does not pass |
| 3 absolute verdict | 36 | -1.36 / 35.8% | -1.23 / 99.4% | +0.13 | reported only |

Corrected, against the world index fund the forms read +2.0, +1.9 and +1.6 (12-month) and against cash +13.6, +13.4 and +14.0;
drop-best is negative on every form (-4.6, -8.2, -7.9); winning years are 3, 6 and 2 of 10 (baseline 0, 4, 2); rounds under four
names 0%, 15.4% and 31.1%. The very high 36-month luck scores are the re-centring at work: the baseline's random baskets sat
below the pool, the corrected ones do not, and a form that is below the basket now has almost every random basket ahead of it.

**Pass lists** (12-month hold: at least 2 points a year over the basket and luck under 5%):

- Form 1: none (baseline none).
- Form 2: **7** (baseline 8): Comms - Interactive Media & Gaming (+7.5, luck 3.2%), Consumer - Luxury, Apparel & Footwear (+4.7,
  2.4%), Industrials - Grid & Electrical Machinery (+14.3, 0.4%), Materials - Copper & Aluminum (+15.1, 0.0%), Materials -
  Diversified Mining (+15.9, 4.8%), Materials - Packaging & Paper (+5.5, 0.2%), Utilities - Gas, Water & Multi-Utilities (+4.1,
  2.0%). **Dropped:** Consumer - Auto Parts (+3.2, luck 2.6% to 6.6%). New: none.
- Form 3: **5** (baseline 6): Consumer - Auto Parts (+4.3, 0.8%), Industrials - Engineering & Construction (+7.8, 1.6%),
  Industrials - Grid & Electrical Machinery (+12.1, 2.8%), Materials - Building Materials (+5.4, 1.6%), Tech - IT Services (+3.2,
  3.4%). **Dropped:** Health - Life Science Tools & Diagnostics (+4.5, luck 4.0% to 9.4%). New: none.
- Both forms: one industry, Grid & Electrical Machinery (baseline two).

**Baseline to corrected, by industry.** In most industries the only change is the cost, +0.50 on a 12-month hold. Where the
split fix applies the moves are larger than the 12-industry recheck suggested, in both directions (12-month excess, points):
Industrials - Waste & Environmental form 2 -0.20 to +4.80 (luck 44% to 6.2%) and form 3 +1.27 to +4.43; Energy - Exploration &
Production form 2 +4.22 to +0.15; Energy - Oilfield Services & Equipment form 3 +4.60 to -2.16 (luck 20% to 65%); Energy -
Midstream & Pipelines form 2 -0.33 to +1.71; Industrials - Logistics & Trucking form 2 -0.86 to +0.89; Comms - Media &
Entertainment form 3 -8.81 to -7.02; Materials - Copper & Aluminum form 3 +0.15 to -1.24. None of these moves puts a new industry
on a pass list (Waste & Environmental form 2 reaches +4.8 with luck 6.2%, just short). The pooled change is what the recheck
estimated: form 2 about +0.06 (found +0.04), form 3 about -0.5 (found -0.49). The cost adds half a point to the 12-month figures;
the split fix takes a little back in form 2 and moves a few industries a long way.

**Plain-English reading, re-checked against these numbers.**

- *No form beat the industry basket* still holds, but only just for form 2: it now sits level with the basket (+0.04), where the
  baseline had it half a point behind. Form 1 (-0.3) and form 3 (-0.5) are still behind. The half point the baseline lost was the
  test's own unfairness; the corrected run shows no edge, not a hidden one.
- *Adding the absolute verdict does not improve on relative cheapness* still holds, and the gap is the same size (form 3 half a
  point below form 2). The ten-year question adds nothing measurable here.
- *The pass lists are about what chance gives* needs softer wording. By luck alone about three of 58 industries would pass at
  5%; form 2 shows 7 and form 3 shows 5, so there are more passes than chance alone predicts. But the threshold is uncorrected for
  58 tests, the industries are not independent, only one industry passes both forms (the baseline had two), and the biggest form 2
  passes (+14 to +16) are the commodity and power-equipment booms the target section says to treat as errors until explained. The
  list is a place to look, not a finding.
- *The 36-month hold supports no pass* still holds; it is below the 12-month hold on every form and below the basket on all
  three.
- *All three beat the index fund and cash* still holds (+1.6 to +2.0 and +13.4 to +14.0), mostly equities beating bills.
- Unchanged and still true: the test cannot see companies that failed and vanished, so nothing here is proof, and the
  consensus part is untested.

## Results, all industries (baseline, published first run)

| Form | Hold | Verdict | vs basket | vs index | vs cash | Luck | Drop best | Winning years | Rounds under 4 names |
|---|---|---|---|---|---|---|---|---|---|
| 1 gate alone | 12 | does not pass | -0.8 | +2.0 | +13.6 | 99.2% | -5.1 | 0 of 10 | 0% |
| 1 gate alone | 36 | reported only | -0.9 | +0.8 | +9.9 | 100% | -3.8 | 1 of 8 | 0% |
| 2 cheapest third | 12 | does not pass | -0.4 | +2.1 | +13.5 | 26.0% | -8.5 | 4 of 10 | 17.8% |
| 2 cheapest third | 36 | reported only | -1.1 | +0.5 | +9.6 | 33.2% | -6.0 | 2 of 8 | 21.2% |
| 3 absolute verdict | 12 | does not pass | -1.0 | +1.6 | +13.9 | 55.4% | -8.3 | 2 of 10 | 32.0% |
| 3 absolute verdict | 36 | reported only | -1.4 | +0.9 | +10.0 | 35.8% | -6.1 | 0 of 8 | 36.0% |

Points a year. Pooled over every industry and round, none of the three forms beat the industry basket. All three beat the
index fund and cash, by a margin that mostly reflects equities beating bills over this decade. Drop-best is negative
everywhere: remove the best name and the result falls.

## Results by industry (12-month hold, baseline)

Each cell: points a year over the industry basket / luck / winning years / verdict (PASS meets the bar, thin = too few
rounds, no = fails). Full figures for both holds are in `SUMMARY.csv`.

**Passing the bar:** form 1 in none. Form 2 in 8: Comms - Interactive Media & Gaming, Consumer - Auto Parts, Consumer -
Luxury, Apparel & Footwear, Industrials - Grid & Electrical Machinery, Materials - Copper & Aluminum, Materials -
Diversified Mining, Materials - Packaging & Paper, Utilities - Gas, Water & Multi-Utilities. Form 3 in 6: Consumer - Auto
Parts, Health - Life Science Tools & Diagnostics, Industrials - Engineering & Construction, Industrials - Grid & Electrical
Machinery, Materials - Building Materials, Tech - IT Services. Only Auto Parts and Grid & Electrical Machinery pass in both.

| Industry | Form 1: gate alone | Form 2: cheapest third | Form 3: absolute verdict |
|---|---|---|---|
| Chemicals | -0.5 / 57% / 0 of 10 / no | -0.7 / 52% / 6 of 10 / no | +0.6 / 35% / 4 of 9 / no |
| Comms - Interactive Media & Gaming | -0.3 / 36% / 2 of 10 / no | +7.0 / 4% / 6 of 10 / PASS | +1.3 / 35% / 8 of 10 / no |
| Comms - Media & Entertainment | -1.1 / 92% / 2 of 10 / no | -9.0 / 96% / 3 of 10 / no | -8.8 / 99% / 2 of 10 / no |
| Comms - Telecoms | -0.8 / 98% / 1 of 10 / no | -1.1 / 55% / 5 of 10 / no | -2.1 / 75% / 4 of 10 / no |
| Consumer - Auto Manufacturers | +1.3 / 10% / 6 of 10 / no | -2.3 / 55% / 4 of 9 / no | -10.5 / 91% / 2 of 8 / no |
| Consumer - Auto Parts | -0.3 / 20% / 2 of 10 / no | +2.7 / 3% / 7 of 10 / PASS | +3.8 / 0% / 7 of 10 / PASS |
| Consumer - Beverages & Tobacco | -0.7 / 95% / 1 of 10 / no | -2.5 / 83% / 2 of 10 / no | -1.7 / 67% / 5 of 10 / no |
| Consumer - Discretionary Retail | -0.3 / 7% / 1 of 10 / no | -2.1 / 67% / 5 of 10 / no | -0.4 / 24% / 3 of 10 / no |
| Consumer - Grocery & Discount Retailers | -0.3 / 16% / 3 of 10 / no | -0.3 / 41% / 5 of 10 / no | -0.1 / 34% / 6 of 10 / no |
| Consumer - Homebuilders | -1.4 / 79% / 2 of 10 / no | -0.7 / 52% / 3 of 10 / no | -2.8 / 80% / 5 of 10 / no |
| Consumer - Hotels, Resorts & Gaming | -0.3 / 2% / 3 of 10 / no | -3.4 / 69% / 3 of 10 / no | +2.9 / 16% / 7 of 10 / no |
| Consumer - Household & Personal Products | -0.8 / 82% / 1 of 10 / no | -4.9 / 95% / 2 of 10 / no | -4.0 / 71% / 4 of 8 / thin |
| Consumer - Internet Retail | +0.4 / 8% / 4 of 10 / no | +2.1 / 35% / 2 of 4 / thin | +3.2 / 26% / 3 of 5 / thin |
| Consumer - Leisure & Travel | -1.1 / 80% / 1 of 10 / no | -5.8 / 80% / 3 of 8 / no | +6.7 / 6% / 6 of 8 / no |
| Consumer - Luxury, Apparel & Footwear | -0.7 / 86% / 2 of 10 / no | +4.2 / 2% / 8 of 10 / PASS | -0.6 / 42% / 3 of 10 / no |
| Consumer - Packaged Foods & Confectionery | -0.4 / 18% / 2 of 10 / no | +0.6 / 16% / 7 of 10 / no | +0.8 / 23% / 4 of 10 / no |
| Consumer - Restaurants | -0.5 / 52% / 1 of 10 / no | -1.6 / 63% / 5 of 10 / no | -2.7 / 76% / 6 of 10 / no |
| Energy - Exploration & Production | -0.6 / 80% / 0 of 10 / no | +4.2 / 11% / 4 of 6 / thin | -8.1 / 70% / 2 of 6 / thin |
| Energy - Integrated Oil & Refining | -9.6 / 100% / 2 of 10 / no | +5.8 / 23% / 7 of 10 / no | -0.8 / 43% / 4 of 10 / no |
| Energy - Midstream & Pipelines | -0.9 / 98% / 1 of 10 / no | -0.3 / 32% / 2 of 7 / no | -6.0 / 74% / 2 of 6 / thin |
| Energy - Oilfield Services & Equipment | -0.6 / 39% / 3 of 10 / no | +2.8 / 19% / 7 of 8 / no | +4.6 / 20% / 2 of 3 / thin |
| Health - Biotechnology | -0.2 / 34% / 5 of 10 / no | +7.1 / 5% / 3 of 5 / thin | -4.7 / 63% / 2 of 3 / thin |
| Health - Large Pharma | -0.1 / 4% / 2 of 10 / no | +0.6 / 33% / 5 of 10 / no | +0.5 / 30% / 4 of 10 / no |
| Health - Life Science Tools & Diagnostics | +0.2 / 0% / 4 of 10 / no | +1.0 / 22% / 5 of 10 / no | +4.0 / 4% / 7 of 10 / PASS |
| Health - Medical Devices & Instruments | -0.1 / 0% / 4 of 10 / no | -4.4 / 98% / 3 of 10 / no | +3.7 / 12% / 7 of 9 / thin |
| Health - Services & Managed Care | -0.5 / 55% / 1 of 10 / no | -1.1 / 62% / 3 of 10 / no | -1.0 / 54% / 4 of 10 / no |
| Health - Specialty & Generic Pharma | -0.9 / 76% / 3 of 10 / no | -3.9 / 74% / 5 of 10 / no | +1.7 / 28% / 5 of 10 / no |
| Industrials - Aerospace & Defense (Europe + US) | +0.9 / 0% / 4 of 10 / no | -3.9 / 86% / 3 of 10 / no | -4.1 / 70% / 6 of 9 / no |
| Industrials - Airlines & Airports | +0.1 / 4% / 4 of 10 / no | +3.2 / 6% / 6 of 10 / no | -0.6 / 45% / 5 of 10 / no |
| Industrials - Building Products | -0.9 / 99% / 1 of 10 / no | -1.1 / 56% / 4 of 10 / no | -1.8 / 70% / 3 of 10 / no |
| Industrials - Construction & Farm Machinery | -0.1 / 10% / 2 of 10 / no | +0.9 / 29% / 6 of 10 / no | -2.1 / 75% / 2 of 8 / no |
| Industrials - Electrical Equipment | -1.4 / 81% / 3 of 10 / no | -8.5 / 72% / 2 of 8 / no | -11.6 / 79% / 2 of 7 / no |
| Industrials - Engineering & Construction | -2.4 / 99% / 2 of 10 / no | -1.1 / 40% / 5 of 10 / no | +7.2 / 1% / 7 of 10 / PASS |
| Industrials - Grid & Electrical Machinery | +1.0 / 2% / 7 of 10 / no | +13.8 / 1% / 8 of 10 / PASS | +11.6 / 3% / 8 of 10 / PASS |
| Industrials - Industrial Machinery | -0.7 / 100% / 0 of 10 / no | +0.4 / 16% / 4 of 10 / no | +0.4 / 28% / 5 of 10 / no |
| Industrials - Logistics & Trucking | -0.1 / 1% / 3 of 10 / no | -0.9 / 54% / 3 of 10 / no | -1.2 / 49% / 5 of 10 / no |
| Industrials - Marine Shipping | +0.1 / 35% / 2 of 10 / no | +13.0 / 9% / 6 of 6 / no | +10.8 / 17% / 3 of 5 / thin |
| Industrials - Railroads | -0.8 / 91% / 0 of 10 / no | -2.2 / 72% / 5 of 10 / no | +3.1 / 13% / 7 of 10 / no |
| Industrials - Waste & Environmental | +0.7 / 0% / 7 of 10 / no | -0.2 / 44% / 3 of 9 / no | +1.3 / 21% / 3 of 6 / thin |
| Materials - Building Materials | -0.3 / 14% / 2 of 10 / no | +0.4 / 37% / 6 of 10 / no | +4.0 / 2% / 7 of 9 / PASS |
| Materials - Commodity Chemicals & Agri Inputs | -1.3 / 5% / 5 of 10 / no | -7.2 / 88% / 4 of 10 / no | -3.5 / 49% / 2 of 7 / no |
| Materials - Copper & Aluminum | -1.1 / 97% / 2 of 10 / no | +14.9 / 0% / 6 of 9 / PASS | +0.2 / 46% / 4 of 8 / no |
| Materials - Diversified Mining | -0.7 / 66% / 2 of 10 / no | +15.4 / 4% / 4 of 6 / PASS | -6.4 / 74% / 3 of 5 / thin |
| Materials - Gold | +0.1 / 12% / 3 of 10 / no | +3.1 / 22% / 4 of 6 / no | +0.0 / 40% / 1 of 3 / thin |
| Materials - Packaging & Paper | -0.6 / 78% / 1 of 10 / no | +5.5 / 0% / 7 of 10 / PASS | +0.5 / 27% / 5 of 10 / no |
| Materials - Specialty Chemicals | -0.4 / 9% / 1 of 10 / no | -0.4 / 35% / 4 of 10 / no | -4.6 / 95% / 2 of 10 / no |
| Materials - Steel | -0.9 / 82% / 1 of 10 / no | -4.5 / 90% / 3 of 10 / no | +0.7 / 35% / 4 of 8 / no |
| Tech - Application Software | -1.4 / 100% / 3 of 10 / no | -7.0 / 97% / 3 of 10 / no | -5.0 / 82% / 3 of 10 / no |
| Tech - Communications Equipment | -3.4 / 100% / 1 of 10 / no | -11.0 / 90% / 6 of 10 / no | -6.1 / 59% / 6 of 10 / no |
| Tech - Electronic Components | -0.8 / 82% / 1 of 10 / no | -3.7 / 65% / 4 of 10 / no | +1.5 / 25% / 5 of 10 / no |
| Tech - Hardware | -3.7 / 100% / 2 of 10 / no | -5.7 / 84% / 3 of 10 / no | -10.5 / 100% / 2 of 10 / no |
| Tech - IT Services | -0.4 / 26% / 6 of 10 / no | +2.8 / 10% / 6 of 10 / no | +2.7 / 3% / 7 of 10 / PASS |
| Tech - Semiconductor Equipment | -1.3 / 99% / 0 of 10 / no | +3.3 / 11% / 6 of 10 / no | +1.0 / 21% / 6 of 9 / no |
| Tech - Semiconductors | -0.5 / 46% / 2 of 10 / no | -9.4 / 99% / 4 of 10 / no | -9.7 / 99% / 2 of 10 / no |
| Tech - Systems Software | -0.4 / 54% / 3 of 10 / no | -9.7 / 98% / 2 of 10 / no | -6.8 / 89% / 3 of 10 / no |
| Utilities - Electric | -0.6 / 75% / 1 of 10 / no | +0.6 / 26% / 4 of 10 / no | -3.8 / 77% / 0 of 3 / thin |
| Utilities - Gas, Water & Multi-Utilities | -0.2 / 0% / 3 of 10 / no | +3.6 / 2% / 7 of 10 / PASS | -1.2 / 67% / 4 of 10 / no |
| Utilities - Renewable & Independent Power | -2.4 / 97% / 5 of 10 / no | +0.8 / 27% / 4 of 10 / no | -3.6 / 67% / 3 of 9 / no |

## Reading the numbers against the realistic target (baseline; re-checked under "Corrected run")

`DESIGN_absolute.md` section o sets the realistic target at one to three points a year over the equal-weight industry
basket, with droughts of several years, and says a result far above that is to be treated as an error until explained.

**In plain words:** the method showed no edge over the industry basket in any of the three forms. The industries that
pass the bar are about what chance gives. This result is the baseline: the rules, thresholds and forms were not tuned
after seeing it, and any later change to the method is measured against these numbers, not against a re-run that was
adjusted to look better.

- **Overall, the method did not meet even the low end.** Pooled across 58 industries, forms 1, 2 and 3 sit at -0.8, -0.4 and
  -1.0 points against the industry basket. Adding the absolute verdict (form 3) did not improve on relative cheapness
  (form 2). That gap is the answer to "does the ten-year question add anything", and here it is no.
- **Some industries pass, but that is not far from what chance gives.** Fifty-eight industries at a 5% luck threshold would
  put about three on the list by luck alone; form 2 shows 8 and form 3 shows 6. The threshold was not corrected for the
  number of tests, and only two industries pass in both forms, so the pass list is a place to look, not a finding.
- **The largest passes are suspect, not good news.** Form 2 reads +13.8 points in Grid & Electrical Machinery, +14.9 in
  Copper & Aluminum and +15.4 in Diversified Mining. By section o those are errors until explained: few names, a commodity
  or power-equipment boom, survivorship, or one stock. The passes in the 2 to 7 point range (Auto Parts, Luxury, Packaging,
  Utilities, Life Science Tools, Building Materials, IT Services) are nearer the target but are still small baskets.
- **The 36-month hold supports no pass.** It is reported only, and it is weaker than the 12-month hold on every form.
- **What this does not say.** It does not say the gate is useless as a check on accounting quality or distress: a test that
  cannot see failed companies cannot credit a rule for avoiding them. It says that, on survivors, nothing here shows excess
  return, and that the consensus part is untested.

## How the company page uses this

The "what this cannot tell you" sentence on the company page reads `backtests_absolute/corrected/<industry>/result.json` (the corrected run) when the
industry has a finished result. It names the form 3, 12-month figure against the industry basket and the share of random
baskets that matched it, marks thin results, and repeats the survivorship limit. With no finished result the page keeps its
earlier wording. The workings list all three forms for both holds (`absolute_backtest/lookup.py`).
