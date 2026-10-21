# Aristos lens track record: what the backtest says

Figures come from `backtests/SUMMARY.csv` and the per-pair CSVs beside it. Every number below sits
inside a generated block (`<!-- GEN:… -->`), rewritten by `python -m scripts.backtest_badge_matrix`
and never typed by hand; method in `docs/BACKTEST.md`. These results are exploratory, not proof of a
repeatable edge.

<!-- GEN:EVIDENCE -->
Across all tested industries, 10 of 62 cleared the bar (about 8 would by chance) and 4 also passed the luck test (about 3 would by chance; the luck test itself still runs somewhat hot, so treat these as approximate). These results are exploratory, not proof of a repeatable edge.

Luck-test calibration: 8 pairs in the 0-5% tail and 6 in the 95-100% tail, about 3.1 expected in each; either tail is over twice its expectation, so the luck test is approximate.
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
7 lenses were tested in 14 industry groups, 98 pairs in all. 4 passed the test, 6 passed the bar but luck is not ruled out, 52 did not clear the bar, and 36 could not be judged.
<!-- /GEN:COUNTS -->

The industry matters more than the lens: the pairs that clear the bar cluster in a few industries,
and the table under "Read per industry" shows which. Read results one industry at a time, never added
up across industries: "Quality passed the test in Mining" is a statement the record supports;
"Quality works" is not.

## What remains uncertain

- **Restated accounts.** The statements are what the data vendor carries today (restated), dated by
  fiscal-period end plus a 90-day lag. A reader on the day saw the original figures, which may differ.
- **Real filing dates exist mostly for US companies.** EODHD carries true filing dates mainly for US
  companies; for many non-US companies its "filing date" is just the fiscal period end, so a filing-date
  re-run falls back to the 90-day lag for them (the CSV header counts how often). The timing rule can be
  tightened for US names; it cannot yet be for the rest.
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
- **Roadmap (evidence that would strengthen or overturn these results):**
  - Delisted US companies: EODHD publishes a list of them, but fundamentals only go back to 2018, so any
    reconstruction of the survivors-only gap is partial.
  - Drop the top 2 and top 3 picks each round, not only the best one, against random baskets treated the
    same way.
  - A portfolio-level return series (one compounded line per lens), alongside the per-round excess.
  - A longer window where the data allows it.
  - A full re-run dated by filing date (27B) with its compare output committed beside the CSVs.
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
| Cyclical Income | 0 | 0 | 10 | 4 | -0.5 (10 groups) | none |
| Defensive Income | 0 | 0 | 2 | 12 | -1.1 (2 groups) | none |
| Earnings Power Value | 0 | 2 | 11 | 1 | -2.1 (13 groups) | none |
| Growth | 0 | 0 | 2 | 12 | +3.5 (2 groups) | none |
| Magic Formula RAW | 2 | 3 | 9 | 0 | +1.5 | Diversified Mining +11.9, Semiconductor Equipment +8.4 |
| Quality | 1 | 1 | 12 | 0 | +1.4 | Semiconductor Equipment +11.0 |
| Value + Momentum | 1 | 0 | 6 | 7 | -1.7 (7 groups) | Diversified Mining +13.9 |
<!-- /GEN:LENSTABLE -->

"Average excess" is the average, over the groups where the lens could be judged, of the amount by
which it beat (or trailed) its industry group per year. A lens that is untested in most groups has
an average over a few groups only; the count is shown.

## Read per industry

Pairs that cleared the bar, each with the same pair without its best pick each year (the exclusion
method is stated once, in `docs/BACKTEST.md`) and the gap to random baskets treated the same way.
A positive gap means the lens leans on its best pick less than random picking does.

<!-- GEN:INDUSTRY -->
- **Interactive Media & Gaming**: Earnings Power Value +3.9 (luck not ruled out; without its best pick each year -7.1, +3.4 against random baskets); Magic Formula RAW +3.0 (luck not ruled out; without its best pick each year -7.9, +2.7 against random baskets); Quality +5.3 (luck not ruled out; without its best pick each year -5.0, +5.0 against random baskets).
- **Auto Manufacturers**: Magic Formula RAW +6.3 (luck not ruled out; without its best pick each year -14.7, +0.3 against random baskets).
- **Grid & Electrical Machinery**: Earnings Power Value +4.8 (luck not ruled out; without its best pick each year -10.4, +9.1 against random baskets); Magic Formula RAW +4.7 (luck not ruled out; without its best pick each year -7.5, +7.8 against random baskets).
- **Diversified Mining**: Value + Momentum +13.9 (passed the test); Magic Formula RAW +11.9 (passed the test; without its best pick each year -3.9, +12.8 against random baskets).
- **Semiconductor Equipment**: Magic Formula RAW +8.4 (passed the test; without its best pick each year -4.6, +8.4 against random baskets); Quality +11.0 (passed the test; without its best pick each year -0.1, +13.7 against random baskets).

No lens cleared the bar in: Media & Entertainment (3 of 7 untested); Large Pharma (2 of 7 untested); Medical Devices & Instruments (2 of 7 untested); Aerospace & Defense (Europe + US) (2 of 7 untested); Airlines & Airports (3 of 7 untested); Hardware (3 of 7 untested); IT Services (2 of 7 untested); Semiconductors (3 of 7 untested); Systems Software (4 of 7 untested).
<!-- /GEN:INDUSTRY -->

**What the 2026-10-21 passes have in common (read from the table above, no earlier text carried over).**
The four passes of the test sit in two groups: Diversified Mining (Value + Momentum +13.9, Magic Formula
RAW +11.9) and Semiconductor Equipment (Magic Formula RAW +8.4, Quality +11.0). Six more cleared the bar
with luck not ruled out, in Interactive Media & Gaming (three), Auto Manufacturers (one) and Grid &
Electrical Machinery (two). The tidy story "capital-heavy industries" does not survive this table: Airlines
& Airports and Auto Manufacturers are as capital-heavy as Mining and have no pass of the test, and
Interactive Media & Gaming, which is not capital-heavy, has three bar-clearing pairs. What can be said is
narrower: the passes are in cyclical groups, they held a position in 60 to 80 of 108 months (Mining Value
+ Momentum 60, RAW 80; Semiconductor Equipment RAW 68, Quality 77), and in each group the two passing
lenses overlap by only 40% (Mining) and 47% (Semiconductor Equipment), under the 80% line at which two
results count as one. Without its best pick each year Mining RAW is at -3.9, Semiconductor Equipment RAW at
-4.6 and Quality at -0.1 (Mining Value + Momentum buys too few names for that check), so the passes lean on
a few winners. One more window of data in a group would change this; read each group alone.

## Lenses under test (GROWTH-V3-RESULTS-1)

A lens whose definition is frozen but whose result has not earned a badge (`status: under_test`) is run in
the same grid and reported here, in two separate tables: coverage first, then performance. It is left out
of the matrix, the pooled chance line and the overlap check above, and nothing earned or failed by an
earlier version carries over. Whether it is offered in the picker is decided by the promotion rule in
`docs/DESIGN_growth_v3.md`, not by this table.

<!-- GEN:UNDERTEST -->
**Growth (`growth_garp_v3`, under test, no badge) - coverage.** Names ranked per round and rounds with a position held (3 or more BUY names).

| Industry group | Ranked, median round | Ranked, last round | Rounds ranking 10+ | Rounds held |
| --- | --- | --- | --- | --- |
| Interactive Media & Gaming | 19 | 19 | 100% | 91 of 108 (84%) |
| Media & Entertainment | 16 | 20 | 84% | 84 of 108 (78%) |
| Auto Manufacturers | 15 | 18 | 84% | 91 of 108 (84%) |
| Large Pharma | 15 | 14 | 84% | 76 of 108 (70%) |
| Medical Devices & Instruments | 28 | 31 | 84% | 91 of 108 (84%) |
| Aerospace & Defense (Europe + US) | 18 | 28 | 84% | 82 of 108 (76%) |
| Airlines & Airports | 13 | 22 | 62% | 50 of 108 (46%) |
| Grid & Electrical Machinery | 13 | 15 | 67% | 67 of 108 (62%) |
| Diversified Mining | 14 | 15 | 82% | 23 of 108 (21%) |
| Hardware | 18 | 21 | 84% | 86 of 108 (80%) |
| IT Services | 22 | 26 | 84% | 92 of 108 (85%) |
| Semiconductor Equipment | 9 | 11 | 35% | 24 of 108 (22%) |
| Semiconductors | 21 | 18 | 84% | 77 of 108 (71%) |
| Systems Software | 12 | 13 | 80% | 24 of 108 (22%) |

Promotion rule (10+ names ranked and a position held in at least half the rounds, in at least 10 of 14 groups); 10 groups hold a position in half the rounds or more. Groups meeting both, by what "ranked 10+" is read to mean:
- median round: 10 of 14
- last round: 10 of 14
- half the rounds or more: 10 of 14

**Growth - performance (reported apart from coverage; no badge, nothing carried over from an earlier version).**

| Industry group | Verdict | Mean excess, %/yr | Years positive | Chance a random picker did as well |
| --- | --- | --- | --- | --- |
| Interactive Media & Gaming | not beyond luck | +5.1 | 5 of 8 | 10% |
| Media & Entertainment | proven | +6.7 | 5 of 8 | 4% |
| Auto Manufacturers | not proven | -1.7 | 4 of 8 | 59% |
| Large Pharma | not proven | -0.2 | 5 of 8 | 48% |
| Medical Devices & Instruments | not proven | -0.3 | 3 of 8 | 44% |
| Aerospace & Defense (Europe + US) | proven | +5.7 | 6 of 8 | 4% |
| Airlines & Airports | insufficient | +0.7 | 4 of 6 | 29% |
| Grid & Electrical Machinery | not beyond luck | +7.6 | 6 of 8 | 15% |
| Diversified Mining | insufficient | +13.8 | 2 of 3 | 7% |
| Hardware | not proven | -1.8 | 5 of 8 | 59% |
| IT Services | not proven | -2.9 | 3 of 9 | 81% |
| Semiconductor Equipment | insufficient | +6.7 | 3 of 6 | 15% |
| Semiconductors | not beyond luck | +4.9 | 5 of 8 | 25% |
| Systems Software | insufficient | +0.7 | 3 of 6 | 41% |
<!-- /GEN:UNDERTEST -->

### Do two passes share the same picks?

For every pair of lenses that both cleared the bar in the same group: how much their baskets
overlapped, month by month.

<!-- GEN:OVERLAP -->
- Semiconductor Equipment: Magic Formula RAW and Quality - mean overlap 47%, identical baskets in 3 of 60 months
- Interactive Media & Gaming: Earnings Power Value and Magic Formula RAW - mean overlap 46%, identical baskets in 2 of 108 months
- Interactive Media & Gaming: Magic Formula RAW and Quality - mean overlap 42%, identical baskets in 0 of 108 months
- Diversified Mining: Value + Momentum and Magic Formula RAW - mean overlap 40%, identical baskets in 0 of 60 months
- Interactive Media & Gaming: Earnings Power Value and Quality - mean overlap 35%, identical baskets in 0 of 108 months
- Grid & Electrical Machinery: Earnings Power Value and Magic Formula RAW - mean overlap 30%, identical baskets in 0 of 100 months
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
