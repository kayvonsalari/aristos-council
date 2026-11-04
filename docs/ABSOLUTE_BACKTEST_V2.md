# Absolute backtest, second pass (ABSOLUTE-BACKTEST-2)

Five analyses and one pre-registered experiment on top of the corrected ten-year backtest (`docs/ABSOLUTE_BACKTEST.md`).
**The corrected baseline is unchanged:** forms 1 to 3 keep their rules and thresholds, nothing was re-run, and the files under
`backtests_absolute/corrected/` were only read. Rebuilding forms 1 to 3 from the new name tables reproduced the corrected run's
selections in all 18,966 industry-rounds (0 differ) and its pooled excess and luck to the last digit (below).

Everything new is in `backtests_absolute/v2/`: `PREREGISTRATION.md` (every definition, committed before any new number was
computed), `SUMMARY.md` (all tables), the CSVs, and one `names.csv.gz` per industry (each eligible name on each round date: gate and
verdict state, base yield, size, gross profit over total assets, 12-month return). Re-run with
`python examples/run_absolute_v2.py extract` then `analyse` (code: `absolute_backtest/v2_extract.py`, `v2.py`, `v2_report.py`).

**Data and limits.** Day-cache files dated 2026-11-04 only (the date is fixed in code, so it holds past midnight); the EODHD key is blank
in-process and the feed raises on any fetch, so no units were spent. 2,044 members: 2 without accounts and 7 without prices, skipped
and counted, as in the corrected run. 12-month hold; every basket and benchmark pays the same 0.5% round trip. Pooling is the
corrected run's: by calendar year of entry inside an industry, then across industries, then across years. Survivorship and restatement
limits apply unchanged: today's index, provider-restated accounts; nothing here is proof.

| Form | Excess (this job) | Excess (corrected) | Luck (this job) | Luck (corrected) |
|---|---|---|---|---|
| 1 | -0.27 | -0.27 | 100.0% | 100.0% |
| 2 | +0.04 | +0.04 | 44.8% | 44.8% |
| 3 | -0.49 | -0.49 | 79.4% | 79.4% |

## 1. Two extra benchmarks (forms 2 and 3)

(a) the equal-weight basket of the names that passed the gate that month; (b) for each pick, the equal-weight basket of the industry
members in the same size third that month. Luck uses the same random baskets as the corrected test, each against its own benchmark.
Points a year; "passing" counts industries with at least +2 points and luck under 5%.

| Form | Benchmark | Pooled excess | Luck | Industries passing |
|---|---|---|---|---|
| 2 | industry basket | +0.04 | 44.8% | 7 |
| 2 | gate-passer basket | +0.36 | 26.8% | 7 |
| 2 | size-matched basket | -0.06 | 51.8% | 5 |
| 3 | industry basket | -0.49 | 79.4% | 5 |
| 3 | gate-passer basket | -0.31 | 69.6% | 5 |
| 3 | size-matched basket | -0.63 | 86.4% | 3 |

**Reading.** Against the names that passed the gate, form 2 is ahead by a third of a point, which is no more than chance gives
(luck 27%), and against same-size companies it is level or slightly behind. So the cheapest third does not beat what a size-matched or
gate-matched basket earns, and form 3 is behind all three.

## 2. Head to head, form 3 minus form 2

4,938 industry-months where both forms hold at least 3 names. Matched = the larger basket trimmed to the smaller by base-yield rank.
Size in US dollars billions (mean of the held names' market value), yield and return in points, return net of cost.

| Version | Metric | Mean | Median | Share positive |
|---|---|---|---|---|
| unmatched | count | +0.41 | +0.00 | 44.1% |
| unmatched | average size ($bn) | +9.94 | +1.97 | 62.7% |
| unmatched | cash yield | -4.37 | -2.18 | 8.2% |
| unmatched | 12-month return | -0.22 | +0.08 | 50.3% |
| matched | average size ($bn) | +8.73 | +0.99 | 58.4% |
| matched | cash yield | -4.88 | -1.68 | 0.0% |
| matched | 12-month return | -0.38 | +0.00 | 47.8% |

By year of entry, the return difference (form 3 minus form 2, mean / share positive; the matched version in brackets):
2016 -2.51 / 42% (-2.58 / 41%); 2017 -1.07 / 48% (-1.31 / 39%); 2018 +1.42 / 57% (+1.44 / 55%); 2019 +7.05 / 72% (+6.27 / 67%);
2020 -4.44 / 41% (-4.17 / 43%); 2021 +1.61 / 58% (+1.37 / 56%); 2022 -1.75 / 46% (-1.56 / 45%); 2023 -0.77 / 47% (-0.88 / 43%);
2024 +0.72 / 47% (+0.01 / 44%); 2025 -3.61 / 42% (-3.54 / 41%). Full table with sizes and yields: `SUMMARY.md`.

**Reading.** Form 3 buys larger companies on a much lower cash yield (about 4 points lower, and lower in 92% of industry-months), and its
return minus form 2's is a coin flip (median about zero, half the months positive, mean slightly negative). The sign swings by year
(form 3 far ahead in 2019, far behind in 2020 and 2025), so the verdict adds no steady return over relative cheapness.

## 3. Honest error bars

Block bootstrap over time of the pooled monthly excess over the industry basket (each entry month: mean over industries holding a
position): 12-month moving blocks, 1,000 draws, seed 20261104, 90% interval. The point on this monthly basis differs a little from the
calendar-year basis the luck score uses (form 3 reads +0.01 monthly, -0.49 yearly), which is itself a sign of how unstable the
means are.

| Form | Pooled excess (year basis) | Luck | Monthly point | 90% interval |
|---|---|---|---|---|
| 1 gate alone | -0.27 | 100.0% | -0.29 | -0.53 to -0.09 |
| 2 gate + cheapest third | +0.04 | 44.8% | +0.09 | -1.76 to +1.65 |
| 3 gate + absolute verdict | -0.49 | 79.4% | +0.01 | -1.02 to +1.70 |
| 4 (pre-registered) | +0.48 | 25.8% | +0.28 | -1.64 to +1.76 |

**Reading.** Only form 1 is distinguishable from zero, and on the negative side; forms 2 to 4 have intervals about 3.4 points wide
that include both zero and the 1 to 3 point target. The ten years cannot tell a one-point edge from none.

## 4. Fixed calendar regimes and the valuation spread

Regimes by entry date, decided in advance. Excess over the industry basket / luck (the last regime holds entries to October 2025
because of the 12-month hold; 2016 has three entry months).

| Form | 2016-2019 | 2020-2021 | 2022-2026 |
|---|---|---|---|
| 1 gate alone | +0.00 / 50.4% | -0.42 / 92.0% | -0.48 / 100.0% |
| 2 gate + cheapest third | -2.54 / 100.0% | +2.57 / 8.8% | +1.35 / 6.0% |
| 3 gate + absolute verdict | -1.38 / 95.2% | +1.34 / 18.8% | -0.51 / 72.6% |
| 4 (pre-registered) | -1.02 / 82.8% | +0.36 / 31.0% | +2.03 / 3.8% |

Valuation spread: per industry and round, the median base yield of the cheapest third of gate-passers minus that of the dearest third
(at least 6 passers with a yield). Rounds are split at the industry's own median over its first five years (entries before
2021-10-01), frozen before looking; all 58 industries qualified. Form 2 excess over the industry basket:

| Rounds | Form 2 excess | Positioned industry-rounds |
|---|---|---|
| wide spread (above the industry's own early median) | +1.63 | 3,763 |
| narrow spread (at or below it) | -1.51 | 2,194 |

**Reading.** Cheapness lost in 2016 to 2019 and won afterwards, so the pooled number hides a regime change rather than a steady
edge. Form 2 earned +1.6 points when the gap between cheap and dear was wide and lost 1.5 when narrow, which fits the idea that
cheapness pays when valuations are stretched apart, but wide rounds are mostly the later years, so this and the regime split are
largely the same observation, not two.

## 5. Form 4, pre-registered

Gate, then the cheapest third on the base yield, then the half (rounded up) with the higher gross profit over total assets (the Quality
lens's reading on the accounts visible at the round date; a name with no reading cannot be chosen but is not failed); fewer than 3
names: the round is skipped and counted. Same holds, costs, basket, luck test and bar as the other forms. Judged once.

| Measure | Value |
|---|---|
| Pooled 12-month excess over the industry basket | +0.48 points a year |
| Luck | 25.8% |
| Bootstrap 90% interval (monthly basis) | -1.64 to +1.76 |
| Industries individually at +2 and luck under 5% | 4 of 58 |
| Industry-rounds held / skipped (fewer than 3 chosen) | 4,642 / 1,680 of 6,322 |
| Median names held | 4 |
| Form 2 on the same industry-rounds / on all rounds | -0.28 / +0.04 |
| **Verdict against the bar (+2.00 points and luck under 5%)** | **FAIL** |

**Reading.** Form 4 is the best pooled number of the four (+0.5 points) but is about a quarter of the bar, nowhere near the luck
threshold (26%), and its interval includes zero. Its +2.0 in 2022 to 2026 (luck 3.8%) was seen only after the fact in a regime split, so
it is a lead for a future pre-registration, not a pass.

## Overall reading against the realistic target

`DESIGN_absolute.md` section o puts the realistic target at one to three points a year over the equal-weight industry basket. Pooled,
no form reaches the low end: -0.27, +0.04, -0.49 and +0.48 points for forms 1 to 4, none with luck under 5%, and all error bars
(about plus or minus 1.7 points) are wider than the target itself. The new benchmarks take away the remaining comfort: form 2's edge
over gate-passers is within chance and over same-size peers is nil, form 3 never improves on form 2, and whatever cheapness earned came
after 2019 and when valuation spreads were wide. The honest statement is the baseline's: on survivors, in these ten years, no edge is
shown, the ten-year verdict adds nothing over relative cheapness, and the data cannot rule out a modest one.
