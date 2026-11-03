# Gate calibration (absolute_gate_v2)

Run on 2026-11-03. Universe: 2020 names (the union of the built cohort members). Names with accounts: 2018; without: 2. EODHD units spent: 20200 (10 units a fundamentals request). Aggregates only: no company is named on this page.

The gate has four rules that can exclude a company and four warnings that never do (docs/DESIGN_absolute.md, section b). This page says how often each one fires on our names, tests the owner's guardrail (an exclusion rule that removes more than one name in ten is mis-specified), and lists any industry the rules exclude wholesale. It changes no threshold, no vote and no verdict.

## The measured boundaries

- Accrual ratio: the 90th percentile of the 2018 names with the ratio is **0.0054**. Gate version 1 froze that figure as its boundary; gate version 2 uses the owner's 0.10 and reports this one for information.
- Abnormal investment, top fifth: the 80th percentile of the 1992 names with the measure is **0.2676**. Recorded for phase 2; nothing uses it yet.

## All names

| Result | Share of names |
|---|---|
| Excluded | 3% |
| Qualifies | 97% |
| Not enough data | 0% |
| Not covered | 0% |

## How often each rule fires

| Rule | Excludes | Not evaluated | Does not apply |
|---|---|---|---|
| Earnings quality (accrual ratio) | 1% | 0% | 0% |
| Solvency (cash flow over debt) | 1% | 1% | 0% |
| Accounting health (F-score 0 or 1) | 0% | 3% | 0% |
| Cash runway under 12 months | 1% | 0% | 96% |

## The owner's guardrail

An exclusion rule that removes more than one name in 10 at calibration is the wrong instrument and becomes a mark until re-specified. This page reports; it changes no rule.

**No exclusion rule breaks it.** Largest share excluded by one rule: 1%.

| Warning | Fires | Not evaluated |
|---|---|---|
| Highly leveraged (above 6 times net debt / EBITDA, or cash flow under 12% of debt) | 9% | 1% |
| Distress score low (Z'' under 1.10) | 20% | 0% |
| Dividend cover under 1.0 (not covered) | 11% | 1% |
| Cash runway 12 to 24 months | 1% | 0% |

## Distributions

| Measure | Names | 5th | 25th | 50th | 75th | 90th | 95th |
|---|---|---|---|---|---|---|---|
| Accrual ratio | 2018 | -0.161 | -0.081 | -0.049 | -0.024 | 0.005 | 0.035 |
| Net debt / EBITDA (times) | 1907 | -3.118 | -0.258 | 1.239 | 2.838 | 4.919 | 6.405 |
| Cash flow before working capital / total debt | 1373 | 0.093 | 0.190 | 0.292 | 0.480 | 0.765 | 1.089 |
| Abnormal investment | 1992 | -0.704 | -0.298 | -0.065 | 0.181 | 0.545 | 0.921 |

## Industries excluded wholesale

None. (An industry needs at least 5 names and more than half of them excluded.)

## By industry

| Industry | Names | Excluded | Accrual | Solvency | F-score | Runway | Highly leveraged | Cover warning | Not enough data |
|---|---|---|---|---|---|---|---|---|---|
| Advertising Agencies | 7 | 0% | 0% | 0% | 0% | 0% | 14% | 14% | 0% |
| Aerospace & Defense | 38 | 5% | 3% | 3% | 0% | 0% | 8% | 0% | 0% |
| Agricultural Inputs | 9 | 0% | 0% | 0% | 0% | 0% | 0% | 22% | 0% |
| Airlines | 23 | 0% | 0% | 0% | 0% | 0% | 9% | 4% | 0% |
| Airports & Air Services | 11 | 0% | 0% | 0% | 0% | 0% | 9% | 9% | 0% |
| Aluminum | 9 | 11% | 0% | 11% | 0% | 0% | 11% | 0% | 0% |
| Apparel Manufacturing | 15 | 7% | 0% | 7% | 0% | 0% | 13% | 13% | 0% |
| Apparel Retail | 14 | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| Auto Manufacturers | 29 | 17% | 0% | 7% | 0% | 10% | 21% | 14% | 0% |
| Auto Parts | 35 | 0% | 0% | 0% | 0% | 0% | 6% | 17% | 0% |
| Beverages - Brewers | 13 | 0% | 0% | 0% | 0% | 0% | 0% | 31% | 0% |
| Beverages - Non-Alcoholic | 14 | 0% | 0% | 0% | 0% | 0% | 0% | 7% | 0% |
| Beverages - Wineries & Distilleries | 6 | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| Biotechnology | 35 | 3% | 0% | 0% | 3% | 0% | 3% | 3% | 0% |
| Broadcasting (thin) | 3 | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| Building Materials | 26 | 0% | 0% | 0% | 0% | 0% | 8% | 4% | 0% |
| Building Products & Equipment | 31 | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| Chemicals | 15 | 0% | 0% | 0% | 0% | 0% | 20% | 40% | 0% |
| Communication Equipment | 27 | 7% | 4% | 0% | 0% | 4% | 4% | 4% | 0% |
| Computer Hardware | 34 | 12% | 12% | 0% | 0% | 0% | 3% | 6% | 0% |
| Confectioners | 6 | 0% | 0% | 0% | 0% | 0% | 17% | 0% | 0% |
| Consumer Electronics | 9 | 11% | 11% | 0% | 0% | 0% | 0% | 0% | 0% |
| Copper | 19 | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| Department Stores (thin) | 3 | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| Diagnostics & Research | 36 | 3% | 0% | 3% | 0% | 0% | 11% | 0% | 0% |
| Discount Stores | 13 | 0% | 0% | 0% | 0% | 0% | 0% | 8% | 0% |
| Drug Manufacturers - General | 21 | 5% | 5% | 0% | 0% | 0% | 10% | 19% | 0% |
| Drug Manufacturers - Specialty & Generic | 29 | 3% | 3% | 0% | 0% | 0% | 3% | 7% | 0% |
| Electrical Equipment & Parts | 36 | 3% | 0% | 0% | 0% | 3% | 11% | 6% | 0% |
| Electronic Components | 45 | 2% | 2% | 0% | 0% | 0% | 0% | 9% | 0% |
| Electronic Gaming & Multimedia | 9 | 0% | 0% | 0% | 0% | 0% | 0% | 11% | 0% |
| Engineering & Construction | 42 | 7% | 2% | 2% | 2% | 0% | 12% | 0% | 0% |
| Entertainment | 21 | 5% | 0% | 5% | 0% | 0% | 19% | 19% | 0% |
| Farm & Heavy Construction Machinery | 28 | 0% | 0% | 0% | 0% | 0% | 7% | 0% | 0% |
| Footwear & Accessories | 12 | 8% | 0% | 8% | 0% | 0% | 17% | 0% | 0% |
| Gambling | 9 | 0% | 0% | 0% | 0% | 0% | 0% | 33% | 0% |
| Gold | 51 | 4% | 0% | 0% | 0% | 4% | 0% | 10% | 0% |
| Grocery Stores | 22 | 0% | 0% | 0% | 0% | 0% | 9% | 9% | 0% |
| Health Information Services (thin) | 4 | 25% | 25% | 0% | 0% | 0% | 0% | 0% | 0% |
| Healthcare Plans | 8 | 0% | 0% | 0% | 0% | 0% | 12% | 0% | 0% |
| Home Improvement Retail | 5 | 0% | 0% | 0% | 0% | 0% | 20% | 20% | 0% |
| Household & Personal Products | 27 | 0% | 0% | 0% | 0% | 0% | 15% | 15% | 0% |
| Information Technology Services | 40 | 12% | 2% | 5% | 2% | 2% | 8% | 5% | 0% |
| Integrated Freight & Logistics | 20 | 0% | 0% | 0% | 0% | 0% | 5% | 5% | 0% |
| Internet Content & Information | 28 | 4% | 4% | 0% | 0% | 0% | 0% | 14% | 0% |
| Internet Retail | 22 | 9% | 5% | 5% | 0% | 0% | 14% | 0% | 0% |
| Leisure | 15 | 0% | 0% | 0% | 0% | 0% | 7% | 7% | 0% |
| Lodging | 10 | 0% | 0% | 0% | 0% | 0% | 20% | 0% | 0% |
| Luxury Goods | 13 | 0% | 0% | 0% | 0% | 0% | 8% | 8% | 0% |
| Marine Shipping | 27 | 4% | 4% | 0% | 0% | 0% | 4% | 11% | 0% |
| Medical Care Facilities | 14 | 0% | 0% | 0% | 0% | 0% | 7% | 7% | 0% |
| Medical Devices | 23 | 0% | 0% | 0% | 0% | 0% | 0% | 4% | 0% |
| Medical Distribution | 8 | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| Medical Instruments & Supplies | 22 | 0% | 0% | 0% | 0% | 0% | 14% | 5% | 0% |
| Oil & Gas Drilling | 6 | 0% | 0% | 0% | 0% | 0% | 0% | 33% | 0% |
| Oil & Gas E&P | 38 | 3% | 3% | 0% | 0% | 0% | 0% | 21% | 0% |
| Oil & Gas Equipment & Services | 26 | 0% | 0% | 0% | 0% | 0% | 0% | 15% | 0% |
| Oil & Gas Integrated | 19 | 5% | 5% | 0% | 0% | 0% | 0% | 5% | 0% |
| Oil & Gas Midstream | 37 | 0% | 0% | 0% | 0% | 0% | 8% | 35% | 0% |
| Oil & Gas Refining & Marketing | 23 | 4% | 0% | 4% | 0% | 0% | 30% | 26% | 0% |
| Other Industrial Metals & Mining | 23 | 9% | 4% | 0% | 0% | 4% | 4% | 13% | 0% |
| Packaged Foods | 32 | 0% | 0% | 0% | 0% | 0% | 3% | 22% | 0% |
| Packaging & Containers | 28 | 7% | 4% | 7% | 0% | 0% | 14% | 21% | 0% |
| Paper & Paper Products | 10 | 0% | 0% | 0% | 0% | 0% | 30% | 20% | 0% |
| Pollution & Treatment Controls | 6 | 17% | 0% | 0% | 0% | 17% | 17% | 0% | 0% |
| Publishing (thin) | 3 | 0% | 0% | 0% | 0% | 0% | 0% | 33% | 0% |
| Railroads | 23 | 0% | 0% | 0% | 0% | 0% | 13% | 0% | 0% |
| Residential Construction | 22 | 0% | 0% | 0% | 0% | 0% | 5% | 9% | 0% |
| Resorts & Casinos | 17 | 6% | 6% | 0% | 0% | 0% | 35% | 12% | 0% |
| Restaurants | 27 | 0% | 0% | 0% | 0% | 0% | 15% | 15% | 0% |
| Semiconductor Equipment & Materials | 40 | 0% | 0% | 0% | 0% | 0% | 5% | 8% | 0% |
| Semiconductors | 54 | 4% | 4% | 0% | 0% | 0% | 6% | 4% | 0% |
| Software - Application | 51 | 4% | 2% | 0% | 0% | 4% | 0% | 0% | 0% |
| Software - Infrastructure | 43 | 2% | 2% | 0% | 0% | 0% | 2% | 0% | 0% |
| Specialty Chemicals | 49 | 6% | 0% | 4% | 0% | 2% | 16% | 29% | 0% |
| Specialty Industrial Machinery | 71 | 3% | 3% | 0% | 0% | 0% | 1% | 4% | 0% |
| Specialty Retail | 17 | 0% | 0% | 0% | 0% | 0% | 6% | 12% | 0% |
| Steel | 35 | 14% | 3% | 6% | 0% | 6% | 17% | 17% | 0% |
| Telecom Services | 58 | 2% | 0% | 2% | 0% | 0% | 7% | 34% | 0% |
| Tobacco | 8 | 0% | 0% | 0% | 0% | 0% | 0% | 25% | 0% |
| Travel Services | 12 | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| Trucking | 8 | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| Utilities - Diversified | 16 | 0% | 0% | 0% | 0% | 0% | 19% | 31% | 0% |
| Utilities - Independent Power Producers | 13 | 8% | 0% | 8% | 0% | 0% | 23% | 23% | 0% |
| Utilities - Regulated Electric | 43 | 2% | 0% | 2% | 0% | 0% | 26% | 7% | 0% |
| Utilities - Regulated Gas | 21 | 0% | 0% | 0% | 0% | 0% | 24% | 19% | 0% |
| Utilities - Regulated Water | 11 | 0% | 0% | 0% | 0% | 0% | 55% | 27% | 0% |
| Utilities - Renewable | 21 | 10% | 0% | 10% | 0% | 0% | 43% | 48% | 5% |
| Waste Management | 16 | 6% | 6% | 0% | 0% | 0% | 6% | 12% | 0% |
