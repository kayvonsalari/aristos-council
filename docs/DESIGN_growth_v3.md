# Design note: Growth v3 (GROWTH-V3-DEF)

Status: **frozen 2026-10-20, under test, hidden.** Nothing in Growth v2 changed (rule 7); v3 is new
files only: `strategies/growth_screen_v3.yaml` and `strategies/growth_garp_v3.yaml`. A change to
either is a v4, never an edit.

## The frozen rule

- **Scope (the screen, absolute floors only):** worth at least $5bn; revenue growing at all
  (3-year CAGR at least 0%); profitable on its capital (through-cycle return on invested capital at
  least 0%). No PEG criterion. There is no "positive operating income" criterion in the registry, so
  the existing ROIC criterion at a zero floor is the profitability floor; it fails a company whose
  after-tax operating profit is negative over the window and abstains (not a fail) on a data gap.
- **Ranking:** the same four factors as v2 (revenue growth, return on invested capital, earnings
  yield, 12-month momentum), quintile cut, no weights, banks and insurers out of scope as in v2.
- **One relative gate, as a BUY cap:** `buy_requires: {revenue_growth: top_half}`. A name the cut
  would buy whose revenue-growth rank is in the bottom half of the ranked set is held at HOLD, never
  BUY, and the page and exports say why ("ranked 2nd, but growing slower than the industry median,
  so HOLD"). The median name is not in the bottom half and keeps its BUY. The cap never excludes a
  name, never creates a SELL and never touches a name that was not a BUY. It is a general rank-engine
  feature (any factor, `top_half` or `top_third`); a strategy without `buy_requires` is byte-identical
  to before (pinned by test).
- **Caption:** "Growing faster than most of its industry at a fair price: a BUY needs sales growth
  above the industry median; names are then ranked on sales growth, return on capital, price and
  trend, among profitable, growing companies worth at least $5bn."

## Why (coverage and meaning, not performance)

v2 could not be judged in 12 of 14 industry groups, because its screen (growth of at least 10%, return
of at least 12%, PEG at most 2.0) leaves fewer than three names to buy. That is a coverage failure, and
it was chosen against, not against v2's returns: no backtest return was read to pick these floors.
"Growing faster than most of its industry" is also a statement about the group, so v3 states it as a
relative gate rather than a fixed 10% that means different things in software and in mining.

## The pre-registered bar (unchanged)

Beat the group average by at least 2 points a year in at least 6 of 10 years, and fewer than 1 in 20
random pickers of the same basket size match it (`docs/BACKTEST.md`, the pass bar). Nothing about the
bar is loosened for v3.

## Coverage and performance are reported separately

When the backtest is committed (GROWTH-V3-RESULTS-1), the record states first how many names v3 ranked
and in how many rounds it held a position, per industry group, and only then what its excess returns
were. A lens that now has enough names to be tested has not thereby passed the test; a good return on
a thin basket is not a coverage success.

## No badge transfer from v2

v3 is a different lens. Its badge reads "under test here" (`status: under_test`) until its own
backtest is committed; nothing earned or failed by v2 in any industry carries over, in either
direction.

## The promotion rule (decided now)

v3 replaces v2 in the picker only when its committed backtest shows it **ranked at least 10 names and
held a position in at least 50% of rounds in at least 10 of the 14 cohorts**. Otherwise it stays
hidden and the record says "under test". Passing the promotion rule says the lens can be judged; it
does not say it passed the test.

## Ships dormant

v3 is `ui: hidden` and `status: under_test`: no picker offers it, no badge exists for it and no
verdict of record depends on it. What activates it is the Colab backtest run over the 14 cohorts, then
GROWTH-V3-RESULTS-1 (committing the CSVs with their compare output and applying the promotion rule).

## Promotion (2026-10-21)

**Status: promoted.** The committed backtest (`backtests/<group>/growth_garp_v3.csv`, 14 groups, code
`6dbce3e`) was read against the rule above. "Ranked at least 10 names" is not defined by the rule, so it was
counted three ways, each with "held a position in at least half the rounds": the median round, the last
round, and at least half the rounds ranking 10 or more. All three give **10 of 14 groups**, exactly the
threshold. Ten groups held a position in at least half the rounds; four did not (Airlines & Airports 46%,
Diversified Mining 21%, Semiconductor Equipment 22%, Systems Software 22%). Only the reading "every single
round" fails (1 of 14), because every group is thin in its first rounds.

Promotion was done by **flag-only edits** (the owner approved option (a)): `ui: hidden` and
`status: under_test` removed from `growth_garp_v3.yaml`, and `ui: hidden` added to `growth_garp_v2.yaml`.
Nothing else in either file changed, and `tests/test_growth_v3_promotion.py` pins every other field of both
files and of their screen lenses against their pre-promotion content (CLAUDE.md rule 7, display and status
flags). v3's badges now come from its own CSVs; nothing v2 earned or failed carries over. Passing the
promotion rule says the lens can be judged, not that it passed: v3 passed the test in Media & Entertainment
and Aerospace & Defense, cleared the bar with luck not ruled out in three more, and did not clear it in the
rest (`docs/BACKTEST_RECORD.md`).

