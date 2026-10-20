# Aristos lens track record: what the backtest says

Figures come from `backtests/SUMMARY.csv` and the per-pair CSVs beside it. Every number below sits
inside a generated block (`<!-- GEN:… -->`), rewritten by `python -m scripts.backtest_badge_matrix`
and never typed by hand; method in `docs/BACKTEST.md`. These results are exploratory, not proof of a
repeatable edge.

<!-- GEN:EVIDENCE -->
Across all tested industries, 12 of 68 cleared the bar (about 11 would by chance) and 5 also passed the luck test (about 3 to 4 would by chance; the luck test itself still runs somewhat hot, so treat these as approximate). These results are exploratory, not proof of a repeatable edge.

Luck-test calibration: 5 pairs in the 0-5% tail and 9 in the 95-100% tail, about 3.4 expected in each; either tail is over twice its expectation, so the luck test is approximate.
<!-- /GEN:EVIDENCE -->

## What Aristos can show today

- Each lens was run backwards over ten years in 14 industry groups: follow its BUY picks every month,
  hold 12 months, charge trading costs, see only accounts 90 days after they were published, and only
  companies of the size they were at the time. The question asked of each pair is whether it beat the
  group average by at least 2 points a year in at least 6 of 10 years, and whether fewer than 1 in 20
  random pickers of the same basket size and turnover did as well.
- The badge on a lens in the app states that one result for that industry group, in plain words, and
  never removes a vote.

<!-- GEN:COUNTS -->
7 lenses were tested in 14 industry groups, 98 pairs in all. 5 passed the test, 7 passed the bar but luck is not ruled out, 56 did not clear the bar, and 30 could not be judged.
<!-- /GEN:COUNTS -->

The industry matters more than the lens: the pairs that clear the bar cluster in a few industries,
and the table under "Read per industry" shows which. Read results one industry at a time, never added
up across industries: "Quality passed the test in Mining" is a statement the record supports;
"Quality works" is not.

## What remains uncertain

- **Restated accounts.** The statements are what the data vendor carries today (restated), dated by
  fiscal-period end plus a 90-day lag. A reader on the day saw the original figures, which may differ.
- **Survivors only, direction unknown.** The groups are today's companies; names that were delisted,
  merged or dropped out are absent, and the benchmark shares that gap. This flatters a lens, but the
  size of the effect on any one result is not known.
- **One decade cannot detect a 2 to 3 point edge.** With ten yearly observations and baskets of three
  to nine names, an edge of that size is within what chance produces; the pass bar is exactly that
  small.
- **The luck test runs hot.** Its extreme bins hold more pairs than a fair test would put there (see
  the calibration line above), so the luck figures are approximate.
- **Concentration.** A pair that clears the bar usually depends on its single best pick each round;
  the per-industry list below shows each pair without it, against random baskets without theirs. Where
  two lenses cleared the bar on nearly the same picks they are one result, not two (list below).
- **One cycle.** Nothing here says how a lens behaves in a different market.

## What is being done

- **Batch 27B** (Growth v3 definition; an as-of filing-date mode so accounts are dated by the date
  they were actually filed, not an assumed lag). Neither changes a committed verdict until a
  re-run is committed with its compare output (`scripts/backtest_compare.py`, `docs/BACKTEST.md`).
- **Proposals, not yet made:** Earnings Power Value as a reading beside the valuation band rather than
  a vote; a wider pool or looser rule for Growth, with its own backtest and the basket size shown; and
  results read per industry only.
- **The forward test, a separate frozen process.** From this quarter the live verdicts of the watched
  cohorts are frozen every three months (`docs/SCOREBOARD.md`), and the 12-month judge record
  (`docs/JUDGE_PANEL.md`) is kept apart from the backtest. Neither is edited after the fact, so from
  2027 there is evidence nobody can accuse of hindsight.

## Results by lens

<!-- GEN:LENSTABLE -->
| Lens | Passed the test | Passed the bar, luck not ruled out | Did not clear the bar | Untested | Average excess, %/yr (judged pairs) | Where it passed the test |
| --- | --- | --- | --- | --- | --- | --- |
| Cyclical Income | 0 | 2 | 9 | 3 | -3.1 (11 groups) | none |
| Defensive Income | 0 | 0 | 5 | 9 | -4.1 (5 groups) | none |
| Earnings Power Value | 0 | 2 | 11 | 1 | -2.1 (13 groups) | none |
| Growth | 1 | 0 | 1 | 12 | -3.0 (2 groups) | Interactive Media & Gaming +8.7 |
| Magic Formula RAW | 2 | 2 | 9 | 1 | +0.6 (13 groups) | Grid & Electrical Machinery +6.3, Diversified Mining +16.0 |
| Quality | 1 | 1 | 11 | 1 | +0.8 (13 groups) | Diversified Mining +15.9 |
| Value + Momentum | 1 | 0 | 10 | 3 | -1.2 (11 groups) | Media & Entertainment +9.3 |
<!-- /GEN:LENSTABLE -->

"Average excess" is the average, over the groups where the lens could be judged, of the amount by
which it beat (or trailed) its industry group per year. A lens that is untested in most groups has
an average over a few groups only; the count is shown.

## Read per industry

Pairs that cleared the bar, each with the same pair without its best pick each year (the exclusion
method is stated once, in `docs/BACKTEST.md`) and the gap to random baskets treated the same way.
A positive gap means the lens leans on its best pick less than random picking does.

<!-- GEN:INDUSTRY -->
- **Interactive Media & Gaming**: Cyclical Income +2.1 (luck not ruled out; without its best pick each year -9.0, +3.9 against random baskets); Earnings Power Value +3.0 (luck not ruled out; without its best pick each year -6.9, +4.2 against random baskets); Growth +8.7 (passed the test; without its best pick each year -24.0, -15.2 against random baskets); Magic Formula RAW +2.4 (luck not ruled out; without its best pick each year -8.3, +3.0 against random baskets).
- **Media & Entertainment**: Value + Momentum +9.3 (passed the test; without its best pick each year -9.2, +4.5 against random baskets).
- **Auto Manufacturers**: Magic Formula RAW +6.2 (luck not ruled out; without its best pick each year -14.1, +3.3 against random baskets); Quality +8.5 (luck not ruled out; without its best pick each year -13.2, +3.0 against random baskets).
- **Grid & Electrical Machinery**: Earnings Power Value +5.6 (luck not ruled out; without its best pick each year -10.2, +9.1 against random baskets); Magic Formula RAW +6.3 (passed the test; without its best pick each year -16.0, +2.5 against random baskets).
- **Diversified Mining**: Magic Formula RAW +16.0 (passed the test; without its best pick each year -5.1, +11.7 against random baskets); Quality +15.9 (passed the test; without its best pick each year -5.4, +11.2 against random baskets).
- **Semiconductors**: Cyclical Income +4.7 (luck not ruled out; without its best pick each year -16.0, +0.8 against random baskets).

No lens cleared the bar in: Large Pharma (2 of 7 untested); Medical Devices & Instruments (1 of 7 untested); Aerospace & Defense (Europe + US) (2 of 7 untested); Airlines & Airports (2 of 7 untested); Hardware (2 of 7 untested); IT Services (1 of 7 untested); Semiconductor Equipment (7 of 7 untested); Systems Software (1 of 7 untested).
<!-- /GEN:INDUSTRY -->

### Do two passes share the same picks?

For every pair of lenses that both cleared the bar in the same group: how much their baskets
overlapped, month by month.

<!-- GEN:OVERLAP -->
- Diversified Mining: Magic Formula RAW and Quality - mean overlap 98%, identical baskets in 59 of 62 months. These two results share 98% of their picks and count as one result, not two.
- Interactive Media & Gaming: Earnings Power Value and Magic Formula RAW - mean overlap 56%, identical baskets in 7 of 107 months
- Auto Manufacturers: Magic Formula RAW and Quality - mean overlap 50%, identical baskets in 7 of 108 months
- Interactive Media & Gaming: Cyclical Income and Magic Formula RAW - mean overlap 32%, identical baskets in 0 of 108 months
- Interactive Media & Gaming: Growth and Magic Formula RAW - mean overlap 31%, identical baskets in 0 of 103 months
- Grid & Electrical Machinery: Earnings Power Value and Magic Formula RAW - mean overlap 30%, identical baskets in 1 of 104 months
- Interactive Media & Gaming: Earnings Power Value and Growth - mean overlap 27%, identical baskets in 0 of 102 months
- Interactive Media & Gaming: Cyclical Income and Earnings Power Value - mean overlap 25%, identical baskets in 0 of 107 months
- Interactive Media & Gaming: Cyclical Income and Growth - mean overlap 15%, identical baskets in 0 of 103 months
<!-- /GEN:OVERLAP -->

Badges remain informational and never gate a vote (ruling of 2026-09-30).

## Caveats a reviewer should know

- Companies under $5bn were not tested; the app analyses them on request against companies of similar
  size, labelled as untested, with no badge.
- Small groups: several groups had only 10 to 20 companies early in the window, so a BUY basket is
  three to four names and one winner can carry the whole result.
- A lens counts a month as a held position from three BUY names; the app gives a verdict from five
  ranked names. The two differ on purpose (`docs/BACKTEST.md`).

Sources: `backtests/SUMMARY.csv`, one CSV per industry and lens under `backtests/`, method in
`docs/BACKTEST.md`.
