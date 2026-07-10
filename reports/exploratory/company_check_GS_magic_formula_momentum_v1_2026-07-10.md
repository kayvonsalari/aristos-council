# Company Check — GS under magic_formula_momentum_v1

_Worked example (FIN-1 ITEM 4). NO verdict; deterministic, no LLM._

```
Company Check — The Goldman Sachs Group, Inc. (GS) · single-name diagnostic · NO VERDICT.
Verdicts are cohort statements (see docs/SCOREBOARD.md).
  strategy: magic_formula_momentum_v1  ·  lens screen: magic_value_screen_v1  ·  reference: 

SCREEN (all criteria evaluated for diagnosis; universe runs exclude on first confirmed fail):
  NOT-EVALUATED  min_roic                   observed — vs threshold 0.12  [non-gating]
  (min_market_cap — same floor as the universe gate; shown once, under GATES below)

GATES (sector / cap / payout):
  FAIL           sector                     sector 'Financial Services' is excluded by this strategy
                 ↳ EBIT/EV and ROIC are not computable on a comparable basis for financials (Greenblatt exclusion). Note the factor block: roic abstains, earnings_yield falls back to P/E.
  PASS           min_market_cap             market cap 312,557,404,160 vs floor 5,000,000,000

FACTOR VALUES + CONTEXT (reference: none available — run the universe once for context):
  Return on invested capital (roic): — [abstained] — no reference run available — run the universe once for context
  Earnings yield (EBIT/EV) (earnings_yield): 0.05163 [fallback:pe] — no reference run available — run the universe once for context
  12-month price momentum (momentum_12m): +54% [computed] — no reference run available — run the universe once for context

DATA INTEGRITY:
  fundamentals: ok  ·  price: ok
  criteria not evaluated (abstained): min_roic
  factors not evaluated: roic

Would be EXCLUDED from a universe list (a screen fail, NOT a SELL) on: sector. A rank/verdict is a cohort statement — run the universe to place it.
```
