# ABSOLUTE-BACKTEST-2: SUMMARY

> Survivorship and restatement limits: the company list is today's market index, so companies that were delisted, merged or went bankrupt are not in it, and a test that removes failing companies is flattered most by their absence. Accounts are the provider's restated record, not the figures first published. No edge shown in a test that cannot see failures; nothing here is proof.

58 industries; 12-month hold; every basket and benchmark pays 0.5% round trip. Excess in points a year, luck = share of 500 random baskets that did as well (lower is better). Corrected baseline untouched.

## Check against the corrected baseline

Forms 1 to 3 rebuilt from the name tables: 18966 industry-rounds compared with the corrected run's files, 0 differ.

| Form | Excess (this job) | Excess (corrected) | Luck (this job) | Luck (corrected) |
|---|---|---|---|---|
| 1 | -0.27 | -0.27 | 100.0% | 100.0% |
| 2 | +0.04 | +0.04 | 44.8% | 44.8% |
| 3 | -0.49 | -0.49 | 79.4% | 79.4% |

## 1. Extra benchmarks (forms 2 and 3)

| Form | Benchmark | Pooled excess | Luck | Industries passing the bar | Industries |
|---|---|---|---|---|---|
| 2 gate + cheapest third | industry basket | +0.04 | 44.8% | 7 | 58 |
| 2 gate + cheapest third | gate-passer basket | +0.36 | 26.8% | 7 | 58 |
| 2 gate + cheapest third | size-matched basket | -0.06 | 51.8% | 5 | 58 |
| 3 gate + absolute verdict | industry basket | -0.49 | 79.4% | 5 | 58 |
| 3 gate + absolute verdict | gate-passer basket | -0.31 | 69.6% | 5 | 58 |
| 3 gate + absolute verdict | size-matched basket | -0.63 | 86.4% | 3 | 58 |

## 2. Head to head, form 3 minus form 2 (4938 industry-months where both hold 3 or more names)

| Version | Metric | Industry-months | Mean | Median | Share positive |
|---|---|---|---|---|---|
| unmatched | count | 4938 | +0.41 | +0.00 | 44.1% |
| unmatched | average size ($bn) | 4926 | +9.94 | +1.97 | 62.7% |
| unmatched | cash yield (points) | 4938 | -4.37 | -2.18 | 8.2% |
| unmatched | 12-month return (points) | 4938 | -0.22 | +0.08 | 50.3% |
| matched (equal sizes) | average size ($bn) | 4926 | +8.73 | +0.99 | 58.4% |
| matched (equal sizes) | cash yield (points) | 4938 | -4.88 | -1.68 | 0.0% |
| matched (equal sizes) | 12-month return (points) | 4938 | -0.38 | +0.00 | 47.8% |

By year of entry (return: mean, median, share positive; yield and size: mean of the difference):

| Version | Year | Rows | Size ($bn) mean | Yield (pts) mean | Return mean | Return median | Return share + |
|---|---|---|---|---|---|---|---|
| unmatched | 2016 | 114 | +1.55 | -1.87 | -2.51 | -4.05 | 42.1% |
| unmatched | 2017 | 428 | +6.57 | -6.30 | -1.07 | -0.18 | 48.4% |
| unmatched | 2018 | 492 | +7.31 | -3.93 | +1.42 | +1.76 | 56.5% |
| unmatched | 2019 | 530 | +5.52 | -3.48 | +7.05 | +4.98 | 72.1% |
| unmatched | 2020 | 540 | +9.31 | -4.39 | -4.44 | -2.99 | 40.9% |
| unmatched | 2021 | 430 | +16.91 | -3.51 | +1.61 | +2.04 | 58.1% |
| unmatched | 2022 | 633 | +14.39 | -3.76 | -1.75 | -0.55 | 46.4% |
| unmatched | 2023 | 623 | +9.74 | -6.85 | -0.77 | -0.36 | 46.7% |
| unmatched | 2024 | 617 | +5.29 | -3.84 | +0.72 | -0.40 | 47.0% |
| unmatched | 2025 | 531 | +16.56 | -3.78 | -3.61 | -2.17 | 41.8% |
| matched (equal sizes) | 2016 | 114 | +4.53 | -1.93 | -2.58 | -1.44 | 41.2% |
| matched (equal sizes) | 2017 | 428 | +4.67 | -15.15 | -1.31 | -0.84 | 39.3% |
| matched (equal sizes) | 2018 | 492 | +7.08 | -5.28 | +1.44 | +1.40 | 55.3% |
| matched (equal sizes) | 2019 | 530 | +4.24 | -3.40 | +6.27 | +4.33 | 66.8% |
| matched (equal sizes) | 2020 | 540 | +8.12 | -4.20 | -4.17 | -1.06 | 43.1% |
| matched (equal sizes) | 2021 | 430 | +17.73 | -4.39 | +1.37 | +2.00 | 56.3% |
| matched (equal sizes) | 2022 | 633 | +11.33 | -3.61 | -1.56 | -0.02 | 44.5% |
| matched (equal sizes) | 2023 | 623 | +8.00 | -5.25 | -0.88 | +0.00 | 42.9% |
| matched (equal sizes) | 2024 | 617 | +5.95 | -2.97 | +0.01 | +0.00 | 44.4% |
| matched (equal sizes) | 2025 | 531 | +13.21 | -2.77 | -3.54 | -1.77 | 41.4% |

## 3. Block bootstrap of the pooled excess over the industry basket (12-month blocks, 1,000 draws)

| Form | Pooled excess (years basis) | Luck | Monthly-basis point | 90% interval | Months |
|---|---|---|---|---|---|
| 1 gate alone | -0.27 | 100.0% | -0.29 | -0.53 to -0.09 | 109 |
| 2 gate + cheapest third | +0.04 | 44.8% | +0.09 | -1.76 to +1.65 | 109 |
| 3 gate + absolute verdict | -0.49 | 79.4% | +0.01 | -1.02 to +1.70 | 109 |
| 4 cheapest third + gross profit (pre-registered) | +0.48 | 25.8% | +0.28 | -1.64 to +1.76 | 109 |

## 4a. Fixed regimes (excess / luck against the industry basket)

| Form | 2016-2019 | 2020-2021 | 2022-2026 |
|---|---|---|---|
| 1 gate alone | +0.00 / 50.4% | -0.42 / 92.0% | -0.48 / 100.0% |
| 2 gate + cheapest third | -2.54 / 100.0% | +2.57 / 8.8% | +1.35 / 6.0% |
| 3 gate + absolute verdict | -1.38 / 95.2% | +1.34 / 18.8% | -0.51 / 72.6% |
| 4 cheapest third + gross profit (pre-registered) | -1.02 / 82.8% | +0.36 / 31.0% | +2.03 / 3.8% |

## 4b. Valuation spread: form 2 excess over the industry basket

| Rounds | Form 2 excess | Positioned industry-rounds | Years |
|---|---|---|---|
| wide spread (above the industry's own first-five-year median) | +1.63 | 3763 | 10 |
| narrow spread (at or below it) | -1.51 | 2194 | 10 |

Industries left out (fewer than 12 defined spreads in their first five years): 0.

## 5. Form 4 (pre-registered)

| Measure | Value |
|---|---|
| Pooled 12-month excess over the industry basket | +0.48 points a year |
| Luck | 25.8% |
| Bootstrap 90% interval (monthly basis, point +0.28) | -1.64 to +1.76 |
| Industries with a pooled excess at or above 2 points and luck under 5% | 4 |
| Industry-rounds: held / skipped for fewer than 3 chosen / no cheap third | 4642 / 1680 / 0 of 6322 |
| Median names held | 4.0 |
| Form 2, same industry-rounds as form 4 (context) | -0.28 |
| Form 2, all rounds (context) | +0.04 |
| Bar: at least +2.00 points and luck under 5% | FAIL |
