# ETF field-coverage probe (ETF-1 ITEM 1)

_Run outside the dev sandbox (yfinance network) on 2026-07-15 and pasted on issue #5.
A field is IN a lens when present for ≥ 80% of that universe's lines._

## Raw probe output (as run)

```
ticker   expense_ratio      fund_size          yield      quoteType  12m_prices
VIG               0.04   129462992896   0.0150999995            ETF         251
VYM               0.04    96168181760          0.023            ETF         251
SCHD              0.06    95734071296          0.033            ETF         251
DVY               0.38    22900787200         0.0337            ETF         251
SDY               0.35    21393180672    0.024500001            ETF         251
NOBL              0.35    11533949952         0.0207            ETF         251
HDV               0.08    13659947008    0.029000001            ETF         251
SPYD              0.07     7374172160    0.042600002            ETF         251
DGRO              0.08    41227829248         0.0195            ETF         251
FVD               0.62     8036226560         0.0232            ETF         251
VUG               0.03   379207221248         0.0039            ETF         251
QQQ               0.18   490103177216         0.0041            ETF         251
IWF               0.18   128899137536   0.0034999999            ETF         251
SPYG              0.04    52988604416   0.0047999998            ETF         251
SCHG              0.04    59069509632         0.0039            ETF         251
VONG              0.06    53361160192         0.0045            ETF         251
MGK               0.05    33301635072   0.0033000002            ETF         251
IWY                0.2    16964288512         0.0034            ETF         251

Dividend set: ['VIG', 'VYM', 'SCHD', 'DVY', 'SDY', 'NOBL', 'HDV', 'SPYD', 'DGRO', 'FVD']
Growth set: ['VUG', 'QQQ', 'IWF', 'SPYG', 'SCHG', 'VONG', 'MGK', 'IWY']
```

## Field coverage + ≥80% decision

Every candidate field is present on **18/18** lines (251 closes each) — **100% coverage
on both universes**. Every field clears the 80% floor, so nothing is dropped for v1.

### Dividend set

| field | present | total | coverage | decision |
|---|---|---|---|---|
| net_expense_ratio | 10 | 10 | 100% | IN |
| total_assets | 10 | 10 | 100% | IN |
| dividend_yield | 10 | 10 | 100% | IN |
| quote_type | 10 | 10 | 100% | IN |
| price_history_12m | 10 | 10 | 100% | IN |

### Growth set

| field | present | total | coverage | decision |
|---|---|---|---|---|
| net_expense_ratio | 8 | 8 | 100% | IN |
| total_assets | 8 | 8 | 100% | IN |
| dividend_yield | 8 | 8 | 100% | IN |
| quote_type | 8 | 8 | 100% | IN |
| price_history_12m | 8 | 8 | 100% | IN |

## ITEM 3 factor sets (decided by this probe)

- **etf_dividend_v1**: distribution_yield, expense_ratio, fund_size, momentum_12m — all IN.
- **etf_growth_v1**: expense_ratio, momentum_12m, fund_size — all IN.

No factor dropped; no coverage gap to report.
