# Universe run — growth_garp_v2

**Running growth_garp_v2 on growth_40_v1 in ranker-only.**

_Verdict: deterministic ranker.  Narrative: none (ranker-only — no LLM ran)._

- screen: `growth_screen_v2`
- mode: ranker-only
- ranked: 7 / 40

## Ranked (verdict of record)

| # | Name | Verdict | Combined | revenue_growth | roic | earnings_yield | momentum_12m |
|---|---|---|---|---|---|---|---|
| 1 | NVIDIA Corporation (NVDA) | BUY | 12.0 | 1 | 2 | 5 | 4 |
| 2 | Eli Lilly and Company (LLY) | BUY | 15.0 | 2 | 4 | 7 | 2 |
| 3 | Adobe Inc. (ADBE) | HOLD | 16.0 | 7 | 1 | 1 | 7 |
| 4 | Alphabet Inc. (GOOGL) | HOLD | 16.0 | 6 | 5 | 4 | 1 |
| 5 | Meta Platforms, Inc. (META) | HOLD | 16.0 | 3 | 6 | 2 | 5 |
| 6 | Microsoft Corporation (MSFT) | HOLD | 17.0 | 5 | 3 | 3 | 6 |
| 7 | GE Aerospace (GE) | SELL | 20.0 | 4 | 7 | 6 | 3 |

## Factor integrity

- **revenue_growth** — computed 7/7 · abstained 0
- **roic** — computed 7/7 · abstained 0
- **earnings_yield** — EV 7/7 · abstained 0
- **momentum_12m** — computed 7/7 · abstained 0

## Excluded (screen / cap / sector)

- **Apple Inc. (AAPL)** — screen: min_revenue_cagr (observed 0.01833 vs threshold 0.1) [⚠ price diverging: +50% 12m — cyclical inflection or mania; human review]
- **AbbVie Inc. (ABBV)** — screen: min_revenue_cagr (observed 0.01947 vs threshold 0.1) [⚠ price diverging: +39% 12m — cyclical inflection or mania; human review]
- **Advanced Micro Devices, Inc. (AMD)** — screen: min_roic (observed 0.02811 vs threshold 0.12) [⚠ price diverging: +284% 12m — cyclical inflection or mania; human review]
- **Amazon.com, Inc. (AMZN)** — screen: min_roic (observed 0.08674 vs threshold 0.12)
- **Broadcom Inc. (AVGO)** — screen: min_roic (observed 0.1118 vs threshold 0.12) [⚠ price diverging: +43% 12m — cyclical inflection or mania; human review]
- **Bristol-Myers Squibb Company (BMY)** — screen: min_revenue_cagr (observed 0.02021 vs threshold 0.1)
- **Caterpillar Inc. (CAT)** — screen: min_revenue_cagr (observed 0.03582 vs threshold 0.1) [⚠ price diverging: +145% 12m — cyclical inflection or mania; human review]
- **Salesforce, Inc. (CRM)** — screen: min_revenue_cagr (observed 0.09709 vs threshold 0.1) [borderline]
- **Chevron Corporation (CVX)** — screen: min_revenue_cagr (observed -0.07263 vs threshold 0.1)
- **Deere & Company (DE)** — screen: min_revenue_cagr (observed -0.05735 vs threshold 0.1)
- **Ford Motor Company (F)** — screen: min_revenue_cagr (observed 0.05733 vs threshold 0.1)
- **Gilead Sciences, Inc. (GILD)** — screen: min_revenue_cagr (observed 0.02915 vs threshold 0.1)
- **General Motors Company (GM)** — screen: min_revenue_cagr (observed 0.0602 vs threshold 0.1) [⚠ price diverging: +48% 12m — cyclical inflection or mania; human review]
- **The Home Depot, Inc. (HD)** — screen: min_revenue_cagr (observed 0.01811 vs threshold 0.1)
- **Intel Corporation (INTC)** — screen: min_revenue_cagr (observed -0.05356 vs threshold 0.1) [⚠ price diverging: +401% 12m — cyclical inflection or mania; human review]
- **The Coca-Cola Company (KO)** — screen: min_revenue_cagr (observed 0.03605 vs threshold 0.1)
- **Lockheed Martin Corporation (LMT)** — screen: min_revenue_cagr (observed 0.04459 vs threshold 0.1)
- **Lowe's Companies, Inc. (LOW)** — screen: min_revenue_cagr (observed -0.03774 vs threshold 0.1)
- **Merck & Co., Inc. (MRK)** — screen: min_revenue_cagr (observed 0.03479 vs threshold 0.1) [⚠ price diverging: +61% 12m — cyclical inflection or mania; human review]
- **NIKE, Inc. (NKE)** — screen: min_revenue_cagr (observed -0.002301 vs threshold 0.1)
- **Oracle Corporation (ORCL)** — screen: min_roic (observed 0.09351 vs threshold 0.12)
- **Pfizer Inc. (PFE)** — screen: min_revenue_cagr (observed -0.1285 vs threshold 0.1)
- **The Procter & Gamble Company (PG)** — screen: min_revenue_cagr (observed 0.01755 vs threshold 0.1)
- **QUALCOMM Incorporated (QCOM)** — screen: min_revenue_cagr (observed 0.009018 vs threshold 0.1)
- **RTX Corporation (RTX)** — screen: min_roic (observed 0.05043 vs threshold 0.12) [⚠ price diverging: +36% 12m — cyclical inflection or mania; human review]
- **Starbucks Corporation (SBUX)** — screen: min_revenue_cagr (observed 0.04421 vs threshold 0.1)
- **Target Corporation (TGT)** — screen: min_revenue_cagr (observed -0.01288 vs threshold 0.1) [⚠ price diverging: +36% 12m — cyclical inflection or mania; human review]
- **Tesla, Inc. (TSLA)** — screen: min_revenue_cagr (observed 0.04762 vs threshold 0.1) [⚠ price diverging: +34% 12m — cyclical inflection or mania; human review]
- **Texas Instruments Incorporated (TXN)** — screen: min_revenue_cagr (observed -0.04755 vs threshold 0.1) [⚠ price diverging: +45% 12m — cyclical inflection or mania; human review]
- **UnitedHealth Group Incorporated (UNH)** — screen: max_peg_ratio (observed n/a vs threshold 2.0) [⚠ price diverging: +44% 12m — cyclical inflection or mania; human review]
- **ExxonMobil Holdings Corporation (XOM)** — screen: min_revenue_cagr (observed -0.05914 vs threshold 0.1) [⚠ price diverging: +31% 12m — cyclical inflection or mania; human review]

## Unrateable (no data — no verdict)

- **PARA** — UNRATEABLE: no data — possibly delisted
- **WBA** — UNRATEABLE: no data — possibly delisted