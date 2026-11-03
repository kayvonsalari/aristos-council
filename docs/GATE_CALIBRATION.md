# Gate calibration (ABSOLUTE-GATE-1)

Run on 2026-11-03. Universe: 2020 names (the union of the built cohort members). Names with accounts: 2018; without: 2. EODHD units spent: 12080 (10 units a fundamentals request). Aggregates only: no company is named on this page.

The gate has four rules that can exclude a company and two warnings that never do (docs/DESIGN_absolute.md, section b). This page says how often each one fires on our names, so the one threshold that is measured, not argued, can be frozen. It changes no vote and no verdict.

## The two measured boundaries

- Accrual ratio, top tenth: the 90th percentile of the 2018 names with the ratio is **0.0054**. Names above it are excluded. The stand-in was 0.10.
- Abnormal investment, top fifth: the 80th percentile of the 1992 names with the measure is **0.2676**. Recorded for phase 2; nothing uses it yet.

## All names

| Result | Share of names |
|---|---|
| Excluded | 29% |
| Qualifies | 70% |
| Not enough data | 0% |
| Not covered | 0% |

## How often each rule fires

| Rule | Excludes | Not evaluated | Does not apply |
|---|---|---|---|
| Earnings quality (accrual ratio) | 10% | 0% | 0% |
| Distress (Z'') | 20% | 0% | 0% |
| Accounting health (F-score 0 or 1) | 0% | 3% | 0% |
| Cash runway under 12 months | 1% | 0% | 95% |

| Warning | Fires | Not evaluated |
|---|---|---|
| Cash runway 12 to 24 months | 1% | |
| Leverage above 6 times | 5% | 6% |
| Dividend cover under 1.5 | 34% | 0% |

## Distributions

| Measure | Names | 5th | 25th | 50th | 75th | 90th | 95th |
|---|---|---|---|---|---|---|---|
| Accrual ratio | 2018 | -0.161 | -0.081 | -0.049 | -0.024 | 0.005 | 0.035 |
| Net debt / EBITDA (times) | 1907 | -3.118 | -0.258 | 1.239 | 2.838 | 4.919 | 6.405 |
| Abnormal investment | 1992 | -0.704 | -0.298 | -0.065 | 0.181 | 0.545 | 0.921 |

## Industries excluded wholesale

More than half of the names in these industries are excluded by the rules. They are marked "not covered by the gate in this release". The thresholds are not loosened to admit them.

| Industry | Names | Excluded | Main rule |
|---|---|---|---|
| Airlines | 23 | 65% | Distress (Z'') (65%) |
| Entertainment | 21 | 52% | Distress (Z'') (48%) |
| Healthcare Plans | 8 | 62% | Earnings quality (accrual ratio) (38%) |
| Lodging | 10 | 70% | Distress (Z'') (60%) |
| Pollution & Treatment Controls | 6 | 67% | Earnings quality (accrual ratio) (33%) |
| Residential Construction | 22 | 59% | Earnings quality (accrual ratio) (59%) |
| Resorts & Casinos | 17 | 59% | Distress (Z'') (53%) |
| Travel Services | 12 | 58% | Distress (Z'') (50%) |
| Utilities - Independent Power Producers | 13 | 69% | Distress (Z'') (62%) |
| Utilities - Regulated Electric | 43 | 67% | Distress (Z'') (63%) |
| Utilities - Regulated Water | 11 | 55% | Distress (Z'') (55%) |

## By industry

| Industry | Names | Excluded | Accrual | Distress | F-score | Runway | Leverage warning | Cover warning | Not enough data |
|---|---|---|---|---|---|---|---|---|---|
| Advertising Agencies | 7 | 43% | 0% | 43% | 0% | 0% | 14% | 29% | 0% |
| Aerospace & Defense | 38 | 24% | 16% | 11% | 0% | 3% | 0% | 24% | 0% |
| Agricultural Inputs | 9 | 0% | 0% | 0% | 0% | 0% | 0% | 33% | 0% |
| Airlines | 23 | 65% | 0% | 65% | 0% | 0% | 9% | 35% | 0% |
| Airports & Air Services | 11 | 18% | 0% | 18% | 0% | 0% | 9% | 45% | 0% |
| Aluminum | 9 | 11% | 0% | 11% | 0% | 0% | 11% | 33% | 0% |
| Apparel Manufacturing | 15 | 7% | 7% | 0% | 0% | 0% | 7% | 33% | 0% |
| Apparel Retail | 14 | 0% | 0% | 0% | 0% | 0% | 0% | 7% | 0% |
| Auto Manufacturers | 29 | 48% | 21% | 31% | 0% | 10% | 7% | 28% | 0% |
| Auto Parts | 35 | 23% | 6% | 17% | 0% | 0% | 6% | 31% | 0% |
| Beverages - Brewers | 13 | 15% | 0% | 15% | 0% | 0% | 0% | 62% | 0% |
| Beverages - Non-Alcoholic | 14 | 21% | 7% | 14% | 0% | 0% | 0% | 43% | 0% |
| Beverages - Wineries & Distilleries | 6 | 17% | 0% | 17% | 0% | 0% | 0% | 67% | 0% |
| Biotechnology | 35 | 40% | 14% | 26% | 3% | 0% | 3% | 14% | 0% |
| Broadcasting (thin) | 3 | 33% | 33% | 0% | 0% | 0% | 0% | 67% | 0% |
| Building Materials | 26 | 15% | 8% | 8% | 0% | 0% | 8% | 31% | 0% |
| Building Products & Equipment | 31 | 13% | 6% | 6% | 0% | 3% | 0% | 13% | 0% |
| Chemicals | 15 | 13% | 7% | 7% | 0% | 0% | 13% | 53% | 0% |
| Communication Equipment | 27 | 30% | 11% | 19% | 0% | 7% | 4% | 37% | 0% |
| Computer Hardware | 34 | 50% | 38% | 12% | 0% | 0% | 0% | 50% | 0% |
| Confectioners | 6 | 50% | 50% | 0% | 0% | 0% | 0% | 0% | 0% |
| Consumer Electronics | 9 | 33% | 22% | 11% | 0% | 0% | 0% | 11% | 0% |
| Copper | 19 | 21% | 16% | 11% | 0% | 0% | 0% | 26% | 0% |
| Department Stores (thin) | 3 | 0% | 0% | 0% | 0% | 0% | 0% | 33% | 0% |
| Diagnostics & Research | 36 | 28% | 3% | 25% | 0% | 0% | 3% | 31% | 0% |
| Discount Stores | 13 | 31% | 0% | 31% | 0% | 0% | 0% | 31% | 0% |
| Drug Manufacturers - General | 21 | 38% | 24% | 14% | 0% | 0% | 10% | 48% | 0% |
| Drug Manufacturers - Specialty & Generic | 29 | 28% | 17% | 14% | 0% | 0% | 0% | 21% | 0% |
| Electrical Equipment & Parts | 36 | 33% | 22% | 11% | 0% | 8% | 6% | 25% | 0% |
| Electronic Components | 45 | 20% | 20% | 0% | 0% | 0% | 0% | 18% | 0% |
| Electronic Gaming & Multimedia | 9 | 33% | 11% | 22% | 0% | 0% | 0% | 33% | 0% |
| Engineering & Construction | 42 | 48% | 21% | 33% | 2% | 0% | 5% | 24% | 0% |
| Entertainment | 21 | 52% | 5% | 48% | 0% | 0% | 19% | 33% | 0% |
| Farm & Heavy Construction Machinery | 28 | 11% | 7% | 7% | 0% | 0% | 4% | 11% | 0% |
| Footwear & Accessories | 12 | 25% | 17% | 0% | 0% | 8% | 8% | 33% | 0% |
| Gambling | 9 | 44% | 0% | 44% | 0% | 0% | 0% | 56% | 0% |
| Gold | 51 | 16% | 8% | 6% | 0% | 4% | 0% | 16% | 0% |
| Grocery Stores | 22 | 50% | 5% | 50% | 0% | 0% | 5% | 32% | 5% |
| Health Information Services (thin) | 4 | 25% | 25% | 0% | 0% | 0% | 0% | 0% | 0% |
| Healthcare Plans | 8 | 62% | 38% | 25% | 0% | 0% | 12% | 25% | 0% |
| Home Improvement Retail | 5 | 20% | 0% | 20% | 0% | 0% | 20% | 40% | 0% |
| Household & Personal Products | 27 | 26% | 11% | 19% | 0% | 0% | 11% | 44% | 0% |
| Information Technology Services | 40 | 18% | 5% | 10% | 2% | 2% | 2% | 22% | 0% |
| Integrated Freight & Logistics | 20 | 25% | 0% | 25% | 0% | 0% | 5% | 25% | 0% |
| Internet Content & Information | 28 | 29% | 14% | 14% | 0% | 0% | 0% | 36% | 0% |
| Internet Retail | 22 | 41% | 9% | 32% | 0% | 0% | 9% | 14% | 0% |
| Leisure | 15 | 27% | 7% | 20% | 0% | 0% | 7% | 33% | 0% |
| Lodging | 10 | 70% | 10% | 60% | 0% | 0% | 10% | 10% | 0% |
| Luxury Goods | 13 | 8% | 8% | 0% | 0% | 0% | 0% | 23% | 0% |
| Marine Shipping | 27 | 4% | 4% | 0% | 0% | 0% | 0% | 37% | 4% |
| Medical Care Facilities | 14 | 29% | 0% | 29% | 0% | 0% | 7% | 7% | 0% |
| Medical Devices | 23 | 9% | 4% | 4% | 0% | 0% | 0% | 30% | 0% |
| Medical Distribution | 8 | 50% | 12% | 38% | 0% | 0% | 0% | 25% | 0% |
| Medical Instruments & Supplies | 22 | 0% | 0% | 0% | 0% | 0% | 14% | 32% | 0% |
| Oil & Gas Drilling | 6 | 33% | 17% | 17% | 0% | 0% | 0% | 50% | 0% |
| Oil & Gas E&P | 38 | 13% | 5% | 8% | 0% | 0% | 0% | 39% | 0% |
| Oil & Gas Equipment & Services | 26 | 19% | 4% | 15% | 0% | 0% | 0% | 38% | 0% |
| Oil & Gas Integrated | 19 | 11% | 5% | 5% | 0% | 0% | 0% | 26% | 5% |
| Oil & Gas Midstream | 37 | 43% | 5% | 38% | 0% | 0% | 5% | 59% | 0% |
| Oil & Gas Refining & Marketing | 23 | 30% | 0% | 26% | 0% | 4% | 13% | 39% | 0% |
| Other Industrial Metals & Mining | 23 | 26% | 17% | 9% | 0% | 4% | 0% | 43% | 0% |
| Packaged Foods | 32 | 31% | 12% | 19% | 0% | 0% | 3% | 56% | 0% |
| Packaging & Containers | 28 | 14% | 4% | 11% | 0% | 0% | 4% | 54% | 0% |
| Paper & Paper Products | 10 | 0% | 0% | 0% | 0% | 0% | 30% | 40% | 0% |
| Pollution & Treatment Controls | 6 | 67% | 33% | 33% | 0% | 17% | 17% | 17% | 0% |
| Publishing (thin) | 3 | 33% | 0% | 33% | 0% | 0% | 0% | 33% | 0% |
| Railroads | 23 | 30% | 17% | 17% | 0% | 0% | 0% | 22% | 0% |
| Residential Construction | 22 | 59% | 59% | 0% | 0% | 5% | 5% | 27% | 0% |
| Resorts & Casinos | 17 | 59% | 6% | 53% | 0% | 0% | 29% | 41% | 0% |
| Restaurants | 27 | 48% | 4% | 48% | 0% | 0% | 11% | 26% | 0% |
| Semiconductor Equipment & Materials | 40 | 15% | 15% | 2% | 0% | 2% | 2% | 30% | 0% |
| Semiconductors | 54 | 15% | 11% | 4% | 0% | 0% | 6% | 48% | 0% |
| Software - Application | 51 | 31% | 6% | 25% | 0% | 4% | 0% | 20% | 0% |
| Software - Infrastructure | 43 | 35% | 2% | 35% | 0% | 0% | 2% | 33% | 0% |
| Specialty Chemicals | 49 | 6% | 0% | 6% | 0% | 2% | 10% | 47% | 0% |
| Specialty Industrial Machinery | 71 | 24% | 13% | 15% | 0% | 0% | 1% | 24% | 0% |
| Specialty Retail | 17 | 12% | 0% | 12% | 0% | 0% | 0% | 29% | 0% |
| Steel | 35 | 26% | 11% | 17% | 0% | 6% | 9% | 54% | 0% |
| Telecom Services | 58 | 48% | 5% | 43% | 0% | 0% | 3% | 71% | 0% |
| Tobacco | 8 | 50% | 38% | 12% | 0% | 0% | 0% | 50% | 0% |
| Travel Services | 12 | 58% | 8% | 50% | 0% | 0% | 0% | 17% | 0% |
| Trucking | 8 | 0% | 0% | 0% | 0% | 0% | 0% | 38% | 0% |
| Utilities - Diversified | 16 | 44% | 6% | 44% | 0% | 0% | 19% | 44% | 0% |
| Utilities - Independent Power Producers | 13 | 69% | 8% | 62% | 0% | 0% | 15% | 46% | 0% |
| Utilities - Regulated Electric | 43 | 67% | 7% | 63% | 0% | 0% | 21% | 56% | 0% |
| Utilities - Regulated Gas | 21 | 48% | 10% | 43% | 0% | 0% | 10% | 57% | 0% |
| Utilities - Regulated Water | 11 | 55% | 0% | 55% | 0% | 0% | 45% | 36% | 0% |
| Utilities - Renewable | 21 | 33% | 5% | 29% | 0% | 0% | 33% | 67% | 10% |
| Waste Management | 16 | 25% | 6% | 25% | 0% | 0% | 6% | 31% | 0% |
