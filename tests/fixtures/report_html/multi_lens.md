# Universe run — Pasted list — 4 names · 3 lenses

`adhoc:bc619e1d`

**Cohort: adhoc:bc619e1d — 4 names**
**Lenses: Classic Value (magic_formula_v1); Magic Formula RAW (magic_formula_raw_v1); Value + Momentum (magic_formula_momentum_v1)**
**Run: 24.08.2026 13:49 CEST — ranker only, no AI commentary**

_Verdict: deterministic ranker.  Narrative: none (ranker-only — no LLM ran)._

### 3 lenses × 4 names — 1 name rated BUY by every lens, 3 of 4 ranked by at least one — shortlist: 1 of 1 BUY

**Classic Value** — Quality-value screen, 2 rules · **Magic Formula RAW** — no screen (ranking only) · **Value + Momentum** — Quality-value screen, 2 rules  ([details](#rules-applied--by-lens))


## Contents

- Shortlist — 1 of 1 BUY survived the checks
- What the run could not see
- Verdict by lens
- Valuation band (absolute — vs each name's own history)
- Rules applied — by lens
  - Classic Value
  - Magic Formula RAW
  - Value + Momentum
- Per-lens detail
  - Classic Value
  - Magic Formula RAW
  - Value + Momentum
- What the terms mean


## Shortlist — 1 of 1 BUY survived the checks

_BUY under Classic Value and below the 80th percentile of its own valuation history, unless every test rated it BUY (then kept with a price warning)._

| Name | Rank | Valuation percentile | Note |
|---|---|---|---|
| **A** | #1 | not evaluated |  |

## What the run could not see

_Evidence channels that returned nothing, stated BEFORE the prose that rests on the channels that did. A dark channel is not a neutral reading — nothing was measured, so nothing below is informed by it._

| Channel | Why it is dark | Names affected |
|---|---|---|
| **Company data** | no usable data, so the name was never ranked | DEAD |

## Verdict by lens

One row per name, one column per lens. A ranked cell gives the name's position in THAT lens's cohort and its verdict; an excluded cell names the rule it failed; a name with no usable data reads “no data” and keeps its reason in that lens's own section below.

| Name | Classic Value | Magic Formula RAW | Value + Momentum |
|---|---|---|---|
| **A** | #1 of 2 · BUY | #1 of 3 · BUY | #1 of 2 · BUY |
| **B** | #2 of 2 · HOLD | #2 of 3 · HOLD | #2 of 2 · HOLD |
| **C** | excluded — return on invested capital 7.7%; the rule requires at least 12%. | #3 of 3 · HOLD | excluded — return on invested capital 7.7%; the rule requires at least 12%. |
| **DEAD** | no data | no data | no data |

## Share price & 52-week position

The last close in the name's OWN quoted currency (never converted) with the date of that close, and where it sits between the trailing 52-week low and high. Display only — not ranked, not screened.

| Name | Price | 12-month low | 12-month high |
|---|---|---|---|
| **A** | 121.90 | not evaluated — only 0 weeks of closes | — |
| **B** | 121.90 | not evaluated — only 0 weeks of closes | — |
| **C** | 121.90 | not evaluated — only 0 weeks of closes | — |

- Prices are the last close on 2026-01-01, each in the name's own quoted currency, never converted. A stale cache shows up here.

## Rules applied — by lens

_Each lens screens on its own rules; a name excluded by one may be ranked by another. The rules below are read from the strategies that actually ran._

### Classic Value (magic_formula_v1)
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

_Cheap and good: high profit on the money invested, a low price for that profit, and a rising share._
**Screen: none**

This strategy screens nothing: no rule filtered the cohort, and quality enters only through the ranking below.

- Ranking: top 20% BUY, bottom 20% SELL, middle HOLD (quintile cut).
- Names ranked on: Return on invested capital, Earnings yield (EBIT/EV), 12-month price momentum.
- Company size: at least $5.0bn (applied by the ranker, before the screen).
- Sectors excluded: Financial Services, Financials, Utilities.
- Asset kinds admitted: equity.
- Missing factor values are ranked worst.

### Value + Momentum (magic_formula_momentum_v1)

_The same as Magic Formula RAW, but only for companies earning at least 12% on their capital._
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

## Classic Value (magic_formula_v1) — detail

**Ranked 2 of 4 names. Excluded 1: by rule below, worst miss first.**

**Return on invested capital** `min_roic` · rule: at least 12% · **1 name**

_A business that earns less on the capital it employs than that capital costs destroys value by growing, so its growth is a reason for concern rather than for a premium._

| Name | Measured | Note |
|---|---|---|
| **C** | 7.7% |  |

**No usable data — no verdict was formed**

- **DEAD** — UNRATEABLE: no data — possibly delisted

**Where the numbers came from**

| Factor | Real data | Abstained |
|---|---|---|
| **Return on invested capital** | 2 of 2 | — |
| **Earnings yield (EBIT/EV)** | 2 of 2 | — |

## Magic Formula RAW (magic_formula_raw_v1) — detail

_Cheap and good: high profit on the money invested, a low price for that profit, and a rising share._

**Ranked 3 of 4 names.**

**No usable data — no verdict was formed**

- **DEAD** — UNRATEABLE: no data — possibly delisted

**Where the numbers came from**

| Factor | Real data | Abstained |
|---|---|---|
| **Return on invested capital** | 3 of 3 | — |
| **Earnings yield (EBIT/EV)** | 3 of 3 | — |
| **12-month price momentum** | 0 of 3 | 3 — A, B, C |

## Value + Momentum (magic_formula_momentum_v1) — detail

_The same as Magic Formula RAW, but only for companies earning at least 12% on their capital._

**Ranked 2 of 4 names. Excluded 1: by rule below, worst miss first.**

**Return on invested capital** `min_roic` · rule: at least 12% · **1 name**

_A business that earns less on the capital it employs than that capital costs destroys value by growing, so its growth is a reason for concern rather than for a premium._

| Name | Measured | Note |
|---|---|---|
| **C** | 7.7% |  |

**No usable data — no verdict was formed**

- **DEAD** — UNRATEABLE: no data — possibly delisted

**Where the numbers came from**

| Factor | Real data | Abstained |
|---|---|---|
| **Return on invested capital** | 2 of 2 | — |
| **Earnings yield (EBIT/EV)** | 2 of 2 | — |
| **12-month price momentum** | 0 of 2 | 2 — A, B |

## Cohort graded (exact membership)

- list: `adhoc:bc619e1d` · 4 names · members `bc619e1d`

A, B, C, DEAD


## What the terms mean

_Every term this report uses, in plain English. Terms the run did not use are left out._

- **12-month price momentum** — What buying the share twelve months ago would have returned by today. Measures trend, not value.
- **Company size (min_market_cap)** — The rule requires the company to be worth at least this much in total, so names too small to trade sensibly are skipped.
- **Earnings yield (EBIT/EV)** — Operating profit divided by what the whole company costs to buy — shares plus debt. The inverse of how expensive it is: higher means more profit per euro paid.
- **EPS (earnings per share)** — Annual profit divided by the number of shares.
- **Not tested / not evaluated** — The data this check needed was missing, so the check was skipped honestly rather than guessed at. It is not a pass and it is not a fail.
- **Percentile (valuation band)** — Where today's multiple sits in that five-year history. The 10th percentile is cheaper than 90% of the period; the 90th is dearer than 90% of it.
- **Quintile cut** — The top fifth of ranked names are rated BUY, the bottom fifth SELL, and everything between them HOLD.
- **Return on invested capital** — The profit the business earns on the money tied up in it. High means every euro invested in the company works hard.
- **Return on invested capital (min_roic)** — The rule requires the company to earn at least this much profit on the money tied up in it — it screens out businesses that need a lot of capital to make a little profit.

---

_Verdicts are deterministic — math judges, the LLM only writes; a fact-checker annotates the prose._

_Research tool, not financial advice. Nothing here is a recommendation to buy or sell any security. Figures come from third-party vendor data and may be incomplete, stale or wrong; abstentions and warnings in this document are part of the record, not noise._
