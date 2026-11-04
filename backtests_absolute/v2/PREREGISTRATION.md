# ABSOLUTE-BACKTEST-2: pre-registration (written before any new number was computed)

Baseline: `backtests_absolute/corrected/`, unchanged. Forms 1 to 3: no rule or threshold changes, no re-run. All analyses use the
12-month hold, the 0.5% round-trip cost on every basket and benchmark, and the corrected run's pooling (average inside an
industry by calendar year of entry, then across industries that have positions that year, then across years).

Data pin: day-cache files dated 2026-11-04 (cache date fixed in code, not read from the clock); the EODHD key is blank in-process;
a name that is not cached is skipped and counted; no units are spent.

## Form 4 (judged once, against the bar, not adjusted afterwards)
1. Gate (`absolute_gate_v2`): names that qualify, eligible and priced, as in the other forms.
2. Cheapest third of the qualifiers on the base yield (the form 2 selection, unchanged).
3. Of that third, keep half by gross profit over total assets, high first. "Half" = ceil(n/2) of the third's n names. The reading is
   the Quality lens's (`tools.quality_epv.gross_profitability`: latest fiscal year, gross profit and total assets period-matched),
   computed on the accounts visible at the round date (filing-date mode). A name with no reading (for instance a bank, which
   has no gross-profit line) is not failed; it simply cannot be chosen (null is not false). The half is taken from the third's size,
   and names are taken in reading order until the half is filled or the readable names run out.
4. Fewer than 3 names chosen: the round is skipped and counted. Fewer than 3 PRICED names at entry: no position, as in every form.
5. Bar: pooled 12-month excess over the industry basket at least 2.0 points a year AND luck under 5% (the same 500 random baskets of
   the same size and turnover, same seeds, re-centred per year). Bootstrap interval is reported beside it, not part of the bar.
   Verdict: pass or fail, once.

## Fixed definitions for the analyses
- Size third (benchmark b, and the matched basket): the industry's eligible priced names that month, ranked by market value in US
  dollars on the round date (the size the run already uses), cut into three equal groups by rank (ties by ticker). A pick with no
  size uses the whole industry basket for its benchmark leg and is counted.
- Benchmark (a): equal-weight basket of the names that passed the gate that month (priced at entry and exit), same cost.
  Needs at least 3 priced names, else that round has no (a) comparison and is counted.
- Benchmark (b): for each pick, the equal-weight return of the industry members in the pick's size third; the benchmark is the
  average of those over the picks; same cost.
- Luck against (a) and (b): the same random baskets as the corrected luck test (same seeds), each measured against its own
  benchmark (for (b) the random basket's own picks' thirds), re-centred per year as in the corrected run.
- Head to head: industry-months where both form 2 and form 3 hold at least 3 priced names. Records: count, average size (mean
  market value of the held names, US dollars), cash yield (mean base yield), 12-month return net of cost. Difference = form 3 minus
  form 2. Distribution over industry-months (mean, median, share positive), pooled and by calendar year of entry. Matched version:
  trim the larger basket to the smaller by base-yield rank (keep the highest yields), names recomputed on the trimmed sets.
- Bootstrap: the pooled monthly series (each entry month: mean over industries that have a position that month of the excess over the
  industry basket); moving blocks of 12 months; 1,000 draws; seed 20261104; statistic = mean of the resampled series;
  interval = 5th and 95th percentile. The point estimate is on the same monthly basis.
- Regimes by entry date: 2016-2019, 2020-2021, 2022-2026 (the last holds entries to Oct 2025 because of the 12-month hold).
- Valuation spread: per industry and round, over gate-passers with a base yield (at least 6 of them): median yield of the cheapest
  third minus median yield of the dearest third (thirds of n//3 names). Threshold = that industry's median spread over its first
  five years of rounds (entries before 2021-10-01); an industry needs at least 12 defined spreads in that window or it is left out
  (counted). Wide = spread strictly above the industry's own threshold, narrow = at or below. Form 2's excess over the industry
  basket is then pooled separately for each group.
