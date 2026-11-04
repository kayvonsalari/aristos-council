"""ABSOLUTE-BACKTEST-1 (phase 4 of docs/DESIGN_absolute.md, section j): the ten-year backtest of the absolute method.

    python -m aristos_council.absolute_backtest run|status|summarise

Beside ``backtest.py`` (the relative-lens backtest, whose results under ``backtests/`` are untouched); the output is
``backtests_absolute/``. No model is called. The method under test is the gate (version 2) and the valuation (version 3),
in three forms:

1. gate alone: every name that passes the gate;
2. gate plus relative cheapness: the cheapest third of the gate-passers in the industry on the base yield;
3. gate plus the absolute verdict: names reading "clears hurdle" on the backtest form (own 5-year, own 10-year, and the
   downside case with its own-5-year start; no consensus; the own-record rule applies).

Each form is held equal-weight for 12 and for 36 months and compared with a broad index fund, cash and the equal-weight
basket of the industry. The statement below goes at the top of every output.
"""
from __future__ import annotations

SURVIVORSHIP = ("Survivorship and restatement limits: the company list is today's market index, so companies that were "
                "delisted, merged or went bankrupt are not in it, and a test that removes failing companies is flattered "
                "most by their absence. Accounts are the provider's restated record, not the figures first published. "
                "No edge shown in a test that cannot see failures; nothing here is proof.")

BACKTEST_DIR = "backtests_absolute"
CORRECTED_SUBDIR = "corrected"   # the corrected run (split fix, two-sided cost, re-centred luck) beside the published baseline
HOLDS = (12, 36)
COST_BPS = 50.0                  # the existing 0.5% round-trip cost
YEARS = 10
LAG_DAYS = 90                    # period end + 90 days where EODHD gives no filing date
FORMS = (1, 2, 3)
FORM_NAMES = {1: "gate alone", 2: "gate plus relative cheapness", 3: "gate plus the absolute verdict"}
FORM_FILES = {1: "form1_gate.csv", 2: "form2_cheapest_third.csv", 3: "form3_clears_hurdle.csv"}
PASS_MIN_EXCESS = 0.02           # decision 13: 2 points a year over the equal-weight industry basket ...
PASS_MAX_LUCK = 0.05             # ... with the luck score under 5%
CHEAP_FRACTION = 3               # the cheapest third
MIN_HELD = 3                     # fewer priced names than this is "no position" (the existing MIN_BUYS)
THIN_BELOW = 4                   # a round with fewer names than this is reported as thin
INDEX_TICKER = "VT"              # Vanguard Total World Stock ETF, yfinance adjusted close, from 2008
CASH_TICKER = "^IRX"             # the US 13-week bill yield, yfinance
UNITS_PER_FUNDAMENTALS = 10      # one EODHD /fundamentals call
UNITS_PER_BOND_SERIES = 1
