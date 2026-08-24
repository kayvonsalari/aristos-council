# Universe run — adhoc:bc619e1d under 3 lenses

**Cohort: adhoc:bc619e1d — 4 names**
**Lenses: Classic Value (magic_formula_v1); Magic Formula RAW (magic_formula_raw_v1); Value + Momentum (magic_formula_momentum_v1)**
**Run: 24.08.2026 13:49 CEST — ranker only, no AI commentary**

_Verdict: deterministic ranker. No LLM ran — narration stays a per-strategy run._

### 3 lenses × 4 names — 1 name rated BUY by every lens, 3 of 4 ranked by at least one


## Rules applied — by lens

_Each lens screens on its own rules; a name excluded by one may be ranked by another. The rules below are read from the strategies that actually ran._

### Classic Value (magic_formula_v1)
## Rules applied

**Screen: Quality-value screen (magic_value_screen_v1)**

Used as a prefilter — names failing any rule below were never ranked.

| Rule | Limit | What it did |
|---|---|---|
| Return on invested capital `min_roic` | at least 12% | passed 2 · failed 1 |
| Company size `min_market_cap` | at least $5.0bn | passed 3 |

- Ranking: top 20% BUY, bottom 20% SELL, middle HOLD (quintile cut).
- Names ranked on: Return on invested capital, Earnings yield (EBIT/EV).
- Company size: at least $5.0bn (applied by the ranker, before the screen).
- Sectors excluded: Financial Services, Financials, Utilities.
- Asset kinds admitted: equity.
- Missing factor values are ranked worst.

### Magic Formula RAW (magic_formula_raw_v1)
## Rules applied

**Screen: none**

This strategy screens nothing: no rule filtered the cohort, and quality enters only through the ranking below.

- Ranking: top 20% BUY, bottom 20% SELL, middle HOLD (quintile cut).
- Names ranked on: Return on invested capital, Earnings yield (EBIT/EV), 12-month price momentum.
- Company size: at least $5.0bn (applied by the ranker, before the screen).
- Sectors excluded: Financial Services, Financials, Utilities.
- Asset kinds admitted: equity.
- Missing factor values are ranked worst.

### Value + Momentum (magic_formula_momentum_v1)
## Rules applied

**Screen: Quality-value screen (magic_value_screen_v1)**

Used as a prefilter — names failing any rule below were never ranked.

| Rule | Limit | What it did |
|---|---|---|
| Return on invested capital `min_roic` | at least 12% | passed 2 · failed 1 |
| Company size `min_market_cap` | at least $5.0bn | passed 3 |

- Ranking: top 20% BUY, bottom 20% SELL, middle HOLD (quintile cut).
- Names ranked on: Return on invested capital, Earnings yield (EBIT/EV), 12-month price momentum.
- Company size: at least $5.0bn (applied by the ranker, before the screen).
- Sectors excluded: Financial Services, Financials, Utilities.
- Asset kinds admitted: equity.
- Missing factor values are ranked worst.

## Verdict by lens

One row per name, one column per lens. A ranked cell gives the name's position in THAT lens's cohort and its verdict; an excluded cell names the rule it failed; a name with no usable data reads “no data” and keeps its reason in that lens's own section below. Rank-sum adds the per-lens POSITIONS and is comparable only across names ranked by EVERY lens — ‡ marks a sum over fewer, and nothing is imputed for an exclusion.

| Name | Classic Value | Magic Formula RAW | Value + Momentum | Rank-sum | Graded by |
|---|---|---|---|---|---|
| **A** | #1 of 2 · BUY | #1 of 3 · BUY | #1 of 2 · BUY | 3 | 3 of 3 |
| **B** | #2 of 2 · HOLD | #2 of 3 · HOLD | #2 of 2 · HOLD | 6 | 3 of 3 |
| **C** | excluded — return on invested capital 7.7%; the rule requires at least 12%. | #3 of 3 · HOLD | excluded — return on invested capital 7.7%; the rule requires at least 12%. | 3‡ | 1 of 3 |
| **DEAD** | no data | no data | no data | — | 0 of 3 |

## Share price & 52-week position

The last close in the name's OWN quoted currency (never converted) with the date of that close, and where it sits between the trailing 52-week low and high. Display only — not ranked, not screened.

| Name | Price | 12-month low | 12-month high |
|---|---|---|---|
| **A** | 121.90 | not evaluated — only 0 weeks of closes | — |
| **B** | 121.90 | not evaluated — only 0 weeks of closes | — |

- Prices are the last close on 2026-01-01, each in the name's own quoted currency, never converted. A stale cache shows up here.

## Classic Value (magic_formula_v1) — detail

- Ranked: 2 of 4 names

**Excluded — did not pass a rule, so was never ranked**

- **C** — return on invested capital 7.7%; the rule requires at least 12%. `min_roic`

**No usable data — no verdict was formed**

- **DEAD** — UNRATEABLE: no data — possibly delisted

**Where the numbers came from**

- Return on invested capital — real data for all 2 names.
- Earnings yield (EBIT/EV) — from the EBIT / market-cap proxy for all 2 names.

## Magic Formula RAW (magic_formula_raw_v1) — detail

- Ranked: 3 of 4 names

**No usable data — no verdict was formed**

- **DEAD** — UNRATEABLE: no data — possibly delisted

**Where the numbers came from**

- Return on invested capital — real data for all 3 names.
- Earnings yield (EBIT/EV) — from the EBIT / market-cap proxy for all 3 names.
- 12-month price momentum — no usable data for all 3 names.

## Value + Momentum (magic_formula_momentum_v1) — detail

- Ranked: 2 of 4 names

**Excluded — did not pass a rule, so was never ranked**

- **C** — return on invested capital 7.7%; the rule requires at least 12%. `min_roic`

**No usable data — no verdict was formed**

- **DEAD** — UNRATEABLE: no data — possibly delisted

**Where the numbers came from**

- Return on invested capital — real data for all 2 names.
- Earnings yield (EBIT/EV) — from the EBIT / market-cap proxy for all 2 names.
- 12-month price momentum — no usable data for all 2 names.

## Cohort graded (exact membership)

- list: `adhoc:bc619e1d` · 4 names · members `bc619e1d`

A, B, C, DEAD


---

_Verdicts are deterministic — math judges, the LLM only writes; a fact-checker annotates the prose._

_Research tool, not financial advice. Nothing here is a recommendation to buy or sell any security. Figures come from third-party vendor data and may be incomplete, stale or wrong; abstentions and warnings in this document are part of the record, not noise._
