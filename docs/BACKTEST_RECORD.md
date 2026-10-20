# Aristos lens track record: what the backtest says

As of 2026-10-05. Figures from `backtests/SUMMARY.csv` (committed 2026-09-30); method in `docs/BACKTEST.md`. These figures are scheduled for a full re-run (see Caveats).

## What was tested and how to read a verdict

We asked a simple question of each lens: if you had followed its BUY picks every month from October 2016 to September 2025, would you have done better than just owning the whole industry group? We ran this in 13 industry groups (we call them cohorts). Each month the lens picks, we hold the picks for 12 months, and we charge 0.5% in trading costs. The lens only sees company accounts 90 days after they were published, and only companies that were big enough at the time count, so there is no peeking at the future.

Two tests decide the verdict. The bar: beat the industry group by at least 2% a year in at least 6 of the 10 years. The luck test: we also build 500 baskets of randomly chosen companies, same size, same turnover. The luck figure is the share of those random baskets that did at least as well as the lens. Lower is better; 25% or less means the lens is doing something random picking cannot.

- **proven**: passed the bar and the luck test
- **not beyond luck**: passed the bar, but a quarter or more of the random baskets did just as well
- **not proven**: did not pass the bar
- **insufficient**: the lens found fewer than three companies to buy in most months, so there is too little to judge

In the app, each lens shows its verdict for that industry group as a badge next to its vote. The badge never removes a vote. It tells the reader how much weight the vote deserves.

## Results by lens, 14 cohorts

We tested 7 lenses in 14 industry groups, 98 pairs in all. 5 pairs are proven, 7 passed the bar but not the luck test, 56 did not pass the bar, and 30 could not be judged. "Avg excess" is the average amount by which the lens beat (or trailed) its industry group, per year, across the groups where it could be measured.

| Lens | What it asks | Proven | Not beyond luck | Not proven | Insufficient | Avg excess %/yr | Where it is proven |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Magic Formula RAW | cheap and good | 2 | 2 | 9 | 1 | +1.2 | Mining +16.0, Grid & Electrical +6.3 |
| Quality | strong, durable business | 1 | 1 | 11 | 1 | +1.7 | Mining +15.9 |
| Growth | growing fast at a fair price | 1 | 0 | 1 | 12 | +7.1 (7 cohorts only) | Interactive Media +8.7 |
| Value + Momentum | RAW with a 12% capital-return floor | 1 | 0 | 10 | 3 | -0.6 | Media & Entertainment +9.3 (73 of 108 months) |
| Earnings Power Value | cheap for the profit it already makes | 0 | 2 | 11 | 1 | -1.4 | none |
| Cyclical Income | income that survives the cycle | 0 | 2 | 9 | 3 | -5.2 | none |
| Defensive Income | steady income, trend intact | 0 | 0 | 5 | 9 | -3.1 | none |

The industry matters more than the lens. Mining is where stock picking works at all: RAW, Quality and EPV all beat the group by more than 10% a year there (EPV only had picks in 62 of the 108 months). In Semiconductors, Software, Hardware and Pharma no lens shows an edge, and in most of those groups random picking did better more than half the time.

## What the numbers mean

The honest reading: the lenses sort companies sensibly, but they rarely beat the industry average by enough to convince a sceptic. Five proven pairs out of 98 is what you would expect if the edge is real but small and only shows up in a few industries. It is not a system that works everywhere.

One more check makes this sharper. If we take away each lens's single best pick in each industry, almost every positive result turns negative. Quality in Mining goes from +15.9% to -5.4% a year. In other words, the lenses earn their returns from one or two big winners per group, not from steadily ranking good companies above bad ones. That is why the badge so often says "promising" rather than "proven".

Growth is a special case. It could not be judged in 12 of the 14 groups, because among companies worth $5bn or more its rules rarely leave three names to buy. In the one group where it ran properly (Interactive Media) it gave the best result in the whole test. The lens may be fine and the pool of companies too narrow for it.

Earnings Power Value behaves like a way of describing a price, not a way of picking stocks. It is proven nowhere, loses to its industry group on average, and in six groups more than 80% of random baskets beat it.

## Read per industry: what the 14-cohort table shows

Of 98 cohort/lens pairs, 5 are proven, 7 passed the bar but not beyond luck, 56 show no edge and 30 are untestable.

Proven results sit in capital-heavy, cyclical industries: Diversified Mining (Magic Formula RAW +16.0% a year over the cohort average, Quality +15.9%) and Grid & Electrical Machinery (Magic Formula RAW +6.3%). In software, semiconductors, hardware and large pharma, Magic Formula RAW and EPV are both negative. The lenses work where today's accounts describe the business, not where the market prices the future.

Every proven result collapses without the single best pick per round (drop-best between -5 and -24), so none stands as a strategy alone.

Growth is untestable in 12 of 14 cohorts because its screen keeps too few names; its one proven result (Interactive Media & Gaming, +8.7%) rests on a thin sample.

The Aerospace & Defence cohort shows no edge under any lens (Magic Formula + Momentum -9.8%, Magic Formula RAW -6.1%). Semiconductor Equipment is untestable under all seven lenses.

This supports the three proposals below: EPV as a reading, not a vote; a wider Growth pool; results read per industry only. Badges remain informational and never gate a vote (ruling of 2026-09-30).

## Caveats a reviewer should know

- One cycle only. Nine years, 2016 to 2025, is one kind of market. Nothing here says how a lens behaves in the next one.
- A hindsight problem was found and fixed. The first run built each industry group from today's company sizes, so it included small companies that later grew 10 to 55 times. Groups are now built from company sizes as they were each month. Companies that have since been delisted are still under-counted, because the data vendor drops them.
- Small groups. Several groups had only 10 to 20 companies in 2016, so a BUY basket is 2 to 4 names. One winner can carry the whole result; see the "best pick" check above.
- The numbers are being re-run. Two recent code changes (how missing accounts are treated, and a rule that a company must have an operating profit) can move rankings. All 98 pairs will be re-run before any badge is shown to outside testers. We do not expect the picture to change.
- Companies under $5bn were not tested. The app will analyse them on request against companies of similar size, clearly labelled as untested, with no badge.
- No live record yet. Everything here looks backwards. From this quarter we freeze the live verdicts every three months, so that from 2027 there is evidence nobody can accuse of hindsight.

## Proposed changes and questions for the reviewer

Three changes follow from the results. None has been made yet.

1. Earnings Power Value stops voting. It becomes a reading shown next to the valuation band ("the market is paying X times what this business would be worth if it never grew"), which is what it is good at. As a vote it added noise in every group.
2. Growth gets a bigger pool or a looser rule. If a lens cannot be judged in 12 of 14 groups, it is almost never allowed to speak. Either the $5bn size floor is lowered for Growth only, with its own backtest, or the minimum of three BUYs becomes two, with the basket size shown.
3. Results are read one industry at a time, never added up across industries. The app's badges already do this. The documentation should say plainly: "Quality is proven" is false; "Quality is proven in Mining" is true.

Questions for the reviewer:

- Is the pass bar (2% a year, 6 of 10 years, luck 25% or less) the right one, or would you set it differently?
- Is a 12-month holding period a fair test for the income lenses, which are meant for longer holds?
- Does the "best pick" check change how you would present these results to someone who is not a specialist?

Sources: `backtests/SUMMARY.csv`, one CSV per industry and lens under `backtests/`, method in `docs/BACKTEST.md`.
