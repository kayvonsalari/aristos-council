# Company Check — DUK under conservative_plus_v1

_Worked example (FIN-1 ITEM 4). NO verdict; deterministic, no LLM._

```
Company Check — Duke Energy Corporation (DUK) · single-name diagnostic · NO VERDICT.
Verdicts are cohort statements (see docs/SCOREBOARD.md).
  strategy: conservative_plus_v1  ·  lens screen: conservative_screen_v1  ·  reference: 

SCREEN (all criteria evaluated for diagnosis; universe runs exclude on first confirmed fail):
  PASS           min_dividend_yield         observed 0.03401 vs threshold 0.015  [gating]
  NOT-EVALUATED  max_payout_ratio_fcf       observed — vs threshold 0.8  [non-gating]
  PASS           min_market_cap             observed 97,648,320,512 vs threshold 5,000,000,000  [gating]
  PASS           min_price_momentum         observed 0.1146 vs threshold -0.1  [gating]
  PASS           min_dividend_streak        observed 19 vs threshold 10  [gating]
  PASS           max_debt_to_market_cap     observed 0.9341 vs threshold 1  [gating]

GATES (sector / cap / payout):
  PASS           min_market_cap             market cap 97,648,320,512 vs floor 1,000,000,000

FACTOR VALUES + CONTEXT (reference: none available — run the universe once for context):
  Annualized volatility (low best) (low_volatility): 0.1522 [computed] — no reference run available — run the universe once for context
  Net payout yield (net_payout_yield): 0.03385 [fallback:dividend_yield] — no reference run available — run the universe once for context
  12-month price momentum (momentum_12m): +11% [computed] — no reference run available — run the universe once for context

DATA INTEGRITY:
  fundamentals: ok  ·  price: ok
  criteria not evaluated (abstained): max_payout_ratio_fcf

Passes the screen — a verdict requires a universe run (a rank is a cohort statement, never issued for one name).
```
