# Aristos Council — The Calculations

Every number that decides a verdict, in one place. This document is generated from the
code and points to it; where they disagree, the code wins. Sources:
`src/aristos_council/rank_engine.py`, `factors.py`, `tools/screening.py`, `tools/technical.py`,
and the strategy YAMLs under `strategies/`.

The reading order mirrors a run: **screen → rank → gates → verdict**. The LLM layer
appears nowhere in this document — by design, no language model computes or judges
anything below.

---

## 1. The rank engine (`rank_engine.py`)

**Rank, don't weight.** For each factor in the strategy, every name in the universe is
ranked 1..N (1 = best, ties get the average of their positions). The per-factor ranks are
summed; **lowest combined rank wins**. This is Greenblatt's Magic-Formula mechanic and the
van Vliet–Blitz Conservative Formula combine — there are no tuned point-weights anywhere.

**Verdict cut** (config per strategy):
- `quintile` (default): top 20% BUY · middle 60% HOLD · bottom 20% SELL.
- `top_k` / `top_percentile`: BUY for the top k / top fraction, HOLD otherwise — for
  small, curated universes where a quintile is an artifact.

**Missing factor values** (`missing`, per strategy or per factor):
- `worst` — a missing value takes rank N (absence treated as maximally bad).
- `neutral` — the factor is omitted for that name and its rank is **imputed** as the mean
  of the name's present-factor ranks (judged on what it has; used e.g. for
  `net_payout_yield` on buyback-only names). Imputed factors are marked `*` in output.
- `exclude` — the name is removed before ranking.

Verdicts are **universe-relative**: the same name can rank differently in a different
universe. That is a property of the method, not a bug — the universe is part of the input.

## 2. The factors (`factors.py`)

All factors are pure functions of adapter data; each returns a float or `None`
(not-evaluated). Directions come from the registry; a strategy YAML may override.

| Factor | Formula | Direction | Notes |
|---|---|---|---|
| `earnings_yield` | EBIT / market cap; fallback 1/PE | high | Proxy for Greenblatt's EBIT/EV — free data lacks a reliable net-debt line for EV. A documented approximation. |
| `roic` | Through-cycle ROIC: NOPAT / invested capital, averaged over a 4-year window | high | Negative-equity-safe (uses provided invested capital, not equity). |
| `momentum_12m` / `momentum_6m` | Trailing total return over ~252 / ~126 trading days | high | Price-derived from the close series. |
| `low_volatility` | Annualized volatility of daily returns | **low** | Pairs with momentum to exclude falling knives (a crashing name is high-vol *and* negative-momentum). |
| `net_payout_yield` | (dividends + buybacks) / market cap; falls back to dividend yield where buyback data is unavailable on free data | high | The fallback under-credits heavy repurchasers — documented, not hidden. |
| `dividend_streak` | Consecutive calendar years of dividend **increases** (see §3) | high | `None` when history is too short to derive. |
| `revenue_growth` | Revenue CAGR over the fundamentals window, with a cyclical-base guard | high | |

## 3. Dividend streak — flat is not a cut (`tools/screening.py`)

Annual dividend totals are built per calendar year (partial current year excluded), then
walked backwards from the latest complete year:

- a year **within ±0.5%** (`flat_tol = 0.005`) of the prior is **FLAT** — it ends the
  growth streak but is **not** a cut;
- a drop of **more than 0.5%** is a **reduction** and sets `last_reduction_year`;
- a strict increase extends the streak.

Why the tolerance exists: T cut in 2022 and MMM in 2024, then held flat — a naive
`current > previous` comparison mislabels every flat year since as a fresh cut. Payment-
timing drift (an extra ex-date inflating one calendar year's sum) is the other failure
mode the calendar-year totals + tolerance absorb. Too-short history → `None` (abstain),
never a fabricated pass or fail.

## 4. Screen criteria (three-state, abstention never excludes)

Each criterion returns **pass / fail / not-evaluated**. Thresholds live in versioned
strategy YAML, not code. Current registry (thresholds shown from the live strategies):

| Criterion | Threshold (current) | What it catches |
|---|---|---|
| `min_dividend_yield` | 0.015 | Names not actually paying meaningful income (WMT at 0.9%). |
| `max_payout_ratio` | 0.85 | Uncovered/stretched payouts (KMB 0.98). Confirmed-only: missing payout excludes nothing. |
| `min_market_cap` | strategy-specific | Micro-cap noise. |
| `min_price_momentum` | −0.10 (12m) | **Breakdowns, not flatness**: a defensive down >10% on the year is breaking (T at −26%); a quiet staple down 0–10% passes. The ranker handles the gradient among survivors. |
| `min_dividend_streak` | 10 years | Cut history: T (cut 2022 → streak 0) and MMM (cut 2024) fail; PG/KO/JNJ/MCD pass. |
| `max_debt_to_market_cap` | 1.0 | Balance-sheet risk: total debt ≤ market cap. VZ (~1.13×, $201B) fails. Uses debt/market-cap, **not** debt/equity — robust to negative-equity buyback names (MCD). |
| `min_roic` | 0.12 (magic_value_screen) | The quality floor for value strategies. |
| `revenue_cagr`, `peg_ratio` | growth_v1 | GARP criteria; PEG uses earnings-growth with a cyclical-base guard. |

**Screen-as-prefilter.** Rank strategies set `prefilter_screen: true`: only names that
pass the lens screen's absolute floors are ranked. This enforces **one definition per
strategy** — the screen says who qualifies, the ranking orders survivors. It closes the
rank-relative-vs-absolute-floor gap (a name can rank top-quintile on relative ROIC while
failing the strategy's own 12% floor — BMY did exactly this until the prefilter).
Exclusion happens **only on a confirmed FAIL**; a not-evaluated criterion never excludes.

## 5. Guards

- **UNRATEABLE** — a ticker with failed fundamentals *and* no usable price history (a
  delisted name: PARA, WBA) is listed separately with the reason, receives **no verdict**,
  and never reaches the narrator. A SELL implies an assessment was made; "no data exists"
  is a different statement.
- **Sector exclusion** — confirmed-only, case-insensitive (e.g. financials under Magic
  Formula, where ROIC is not meaningful). An unknown sector excludes nothing.
- **Disposition gate** — if a criterion designated *gating* is a confirmed failure, the
  verdict is capped at SELL regardless of any narrative; a *not-evaluated* gating
  criterion yields **INSUFFICIENT_EVIDENCE** (off the buy/hold/sell ladder, unconditional
  human review). Gate firings are recorded, never silent.
- **Yield normalization** — dividend yield is normalized to a decimal per-adapter
  (yfinance reports percent) with a >100% sanity guard; the unit bug this fixed silently
  disabled the income floor for weeks. Documented as a warning to future adapters.

## 6. Known limitations (measured, not hypothetical)

- **GAAP payout noise**: payout ratios computed on GAAP EPS exclude names whose earnings
  are depressed by non-cash charges (KMB, PEP, MRK at 0.89–0.98). The eventual fix is
  payout-on-FCF; until then these exclusions are legible but contestable.
- **Knife-edge floors**: absolute thresholds exclude at any margin (PFE at ROIC 0.1198 vs
  a 0.12 floor). That is what floors do, but two hundredths of a percent is inside
  measurement noise for a computed ROIC.
- **Small universes**: a quintile cut on 6 survivors makes BUY = top 2 — an artifact. Use
  `top_k`, or treat the screen as the product on curated lists.
- **Trailing data**: every factor is historical. Momentum is the only forward-leaning
  signal; there is no estimate-revision input on free data.
- **EBIT/market-cap proxy**: understates leverage-adjusted cheapness vs true EBIT/EV.
