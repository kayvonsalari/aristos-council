# Why does Valero rank BUY at 34x EV/EBIT?

**Date:** 2026-09-16 · **Classification: C — not a bug.**
**Lens:** `magic_formula_raw_v1`, ranker-only, valuation band ON.
**Cohort:** the 14 diagnostic names (VLO MPC PSX EC LNG XOM CVX APA EOG CNQ SU TTE E HAL).

**Verdict in one line:** the ranker and the band use the *same* EBIT and the *same* EV.
Valero's earnings-yield rank is **dead last in the cohort**, exactly as 34x implies. It
ranks well because through-cycle ROIC and momentum outvote it two legs to one.

The 2026-09-15 Colab run was not frozen on this machine (the only VLO freeze under `runs/`
is 2026-07-09), so this is a fresh local re-run. It reproduces the reported band figures to
the decimal: VLO 34.1x/99th, MPC 17.3x/99th, EC 7.5x/99th, LNG 8.7x/34th.

---

## Step 1 — the printed factors

### The ranked cohort (14 live of 14)

| # | Name | Verdict | Rank sum | ROIC rank | EY rank | Mom rank | ROIC | Earnings yield | Momentum 12m |
|---|---|---|---|---|---|---|---|---|---|
| 1 | APA | buy | 6 | 1 | 2 | 3 | 0.2651 | 0.1531 | +114.8% |
| 2 | EC | buy | 12 | 7 | **1** | 4 | 0.1480 | 0.2482 | +109.7% |
| 3 | MPC | buy | 16 | 5 | 9 | 2 | 0.1703 | 0.0594 | +131.7% |
| 4 | **VLO** | hold | 18 | **3** | **14 (last)** | **1** | 0.2020 | 0.0302 | +158.7% |
| 5 | LNG | hold | 20 | 2 | 4 | 14 | 0.2386 | 0.1114 | +15.9% |
| 12 | PSX | hold | 29 | 12 | 12 | 5 | 0.0915 | 0.0530 | +108.6% |
| 14 | CVX | sell | 38 | 13 | 13 | 12 | 0.0777 | 0.0460 | +44.1% |

VLO is #4/hold in this 14-name cohort rather than #10/buy in the 81-name one — the quintile
cut lands differently on 14 names. The factor *structure* is identical, and that is what was
in question.

### VLO — Valero Energy (accounts USD, price USD, no FX)

| | |
|---|---|
| `f.ebit` (newest-first) | 3,561m (FY2025) · 4,254m (FY2024) · 12,360m (FY2023) · 15,869m (FY2022) |
| `f.operating_income` | 4,312m · 3,755m · 11,858m · 15,751m |
| Element the ranker used (`f.ebit[0]`) | **3,561m** (FY2025) |
| market cap / total debt / total cash | 114,318.7m / 11,349.0m / 7,874.0m |
| `enterprise_value(f, fi.fx)` | **117,793.7m** |
| Earnings yield | **0.030231** → implied **33.1x** |
| Source tag | **`ev`** (true EBIT/EV — no fallback, no abstention) |
| Band basis (`_choose_basis`) | **`ev_ebit`**, net debt **`asof`** |
| Band EBIT series | 2025-12-31: 3,561m · 2024-12-31: 4,254m · 2023-12-31: 12,360m · 2022-12-31: 15,869m · 2021-12-31: None |
| Band `_asof` earnings at today | **3,561m** |
| Band current multiple | **34.07x** — 99th percentile, "43 of 61 months computable" |

**The two sides agree.** Ranker EBIT 3,561m == band `current_earnings` 3,561m — the same
FY2025 element, no TTM-vs-annual mismatch. 33.1x against 34.07x is a 2.9% spread explained
entirely by the band pricing net debt **as-of the dated statements at a month-end close**
while the ranker uses **current** market cap and current debt/cash. Same basis, different
valuation instant. Neither A nor B applies.

### MPC — Marathon Petroleum (accounts USD, price USD, no FX)

| | |
|---|---|
| `f.ebit` | 8,427m · 7,265m · 15,254m · 21,667m |
| `f.operating_income` | 5,768m · 5,248m · 12,586m · 18,970m |
| `f.ebit[0]` used | **8,427m** |
| mcap / debt / cash | 115,374.0m / 34,292.0m / 7,768.0m |
| `enterprise_value` | **141,898.0m** |
| Earnings yield | **0.059388** → implied **16.8x**, source **`ev`** |
| Band | `ev_ebit`, `asof`, `_asof` earnings **8,427m**, current **17.33x**, 99th pct |

Same story: identical EBIT element, 16.8x vs 17.3x from the same as-of-vs-current net-debt gap.

### EC — Ecopetrol (accounts **COP**, price **USD**, FX 0.0003211 COP→USD)

| | |
|---|---|
| `f.ebit` | 29,021,000m · 40,550,000m · 46,020,000m · 61,684,522m (COP) |
| `f.ebit[0]` used | **29,021,000m COP** → 9,318m USD at 0.0003211 |
| mcap / debt / cash | 37,539.5m USD / **None** / **None** |
| `enterprise_value(f, fi.fx)` | **None** — debt and cash both absent |
| Earnings yield | **0.248221** → implied **4.03x**, source **`fallback:ebit_mcap, COP→USD @ 0.0003211 (2026-09-16)`** |
| Band | `ev_ebit`, `asof`, `_asof` earnings 29,021,000m COP, current **7.49x**, 99th pct |

**EC is the one name where the denominators genuinely differ** — and it is *disclosed*, not
silent. `total_debt`/`total_cash` come back `None` from the provider, so `enterprise_value`
abstains and `_earnings_yield_outcome` takes the documented `fallback:ebit_mcap` branch:
EBIT / **market cap**, no net debt. The band, reading the *dated* `total_debt`/`cash` series
out of `aligned_annual` (which are present), still computes a true EV/EBIT. Hence 4.03x
against 7.49x on the same EBIT. The source tag says exactly this, which is the ITEM 1 design
working as intended. Flagged separately below, because the fallback is not rank-neutral —
but it is not the Valero question and not a regression.

### LNG — Cheniere (the control)

`f.ebit[0]` 9,230m, EV 82,819.6m, EY 0.111447 → **8.97x**, source `ev`; band 8.71x, 34th
percentile. Agrees, and its low percentile is what a name that is *actually* cheap against
its own history looks like.

---

## Step 2 — classification: **C**

All three conditions hold for VLO:

1. **Same EBIT.** Ranker `f.ebit[0]` = 3,561m = band `current_earnings` = 3,561m.
2. **Same EV basis.** Both `ev_ebit`; 33.1x vs 34.07x is the as-of/current net-debt instant,
   not a different denominator. Source tag is `ev`, not a fallback.
3. **Earnings-yield rank genuinely poor.** Rank **14 of 14** — the worst in the cohort. The
   ranker is not being fooled about the price; it is being outvoted.

### What actually carries VLO

The magic formula is a rank-sum of three equal legs. VLO's are 3 + 14 + 1 = 18.

**Momentum** is rank 1 at +158.7% — unambiguous, and the formula's design.

**ROIC is the load-bearing one.** `through_cycle_roic` uses `window=4`: the mean operating
income over the last four fiscal years, over the *latest* invested capital.

```
VLO operating income:  4,312m (FY25)  3,755m (FY24)  11,858m (FY23)  15,751m (FY22)
mean over the window = 8,919m        latest year = 4,312m = 0.48x the mean
invested capital     = 34,344m       effective tax (through-cycle) = 0.222

through-cycle ROIC = 0.2020   -> rank 3 of 14
latest-year  ROIC  = 0.0938   -> rank 9 of 14
```

FY2022 (15,751m) and FY2023 (11,858m) are the refining boom. In September 2026 they are
still inside the four-year window, so a business currently earning 9.4% on capital is ranked
as though it earns 20.2%. MPC is the same shape: mean 10,643m against latest 5,768m (0.54x),
through-cycle 17.0% against latest-year 9.6%.

Re-ranking the cohort with a one-year ROIC window and nothing else changed:

| Name | With 4y through-cycle | With 1y |
|---|---|---|
| VLO | #4 (ROIC rank 3) | **#9** (ROIC rank 9) |
| MPC | #3 **buy** | #3 **hold** |

The boom years are worth roughly five places to Valero. This is Greenblatt's formula behaving
exactly as specified on a cyclical just past peak — the quality leg is deliberately
backward-looking, and `through_cycle_roic`'s own docstring says the window exists to stop a
*single peak year* flattering a cyclical. What it cannot do is stop a *multi-year boom*
flattering one, because the boom fills the window.

**No code change is warranted, and none was made.** The valuation band is the designed
counterweight, and it fired correctly: 34.1x at the 99th percentile of Valero's own 5-year
range is the band saying, in the same report, that the price does not support the quality
score. Both numbers are right; they disagree on purpose.

### One thing to log separately (not VLO, not a regression)

EC's `fallback:ebit_mcap` hands a levered name a debt-free earnings yield and a #1 rank on
that leg. Disclosed by the source tag, so nothing is silent, but the fallback is not
rank-neutral: it systematically favours names whose provider record is missing balance-sheet
scalars. Worth its own item — the band already holds the dated `total_debt`/`cash` series
that `enterprise_value` wanted, so a shared accessor is plausible. Out of scope here.

---

## Step 3 — the test that would have caught a real A or B

Had this been a basis mismatch, the test is a single name with a known EBIT series asserting
that the ranker's implied multiple and the band's `current` agree within a tolerance that
admits only the as-of/current net-debt gap:

```python
def test_ranker_and_band_agree_on_basis():
    fi = fake_inputs(ebit=[3_561e6, 4_254e6, 12_360e6, 15_869e6],
                     market_cap=114_318e6, total_debt=11_349e6, total_cash=7_874e6)
    ey, src = _earnings_yield_outcome(fi)
    assert src == SRC_EV
    band = fi.valuation_band
    assert band.current == pytest.approx(1.0 / ey, rel=0.05)   # same EBIT, same EV
```

It passes on today's data, which is why this is a diagnosis and not a patch.

## Reproducing

```
run_rank_pipeline(NAMES, "magic_formula_raw_v1", ranker_only=True,
                  with_valuation_band=True, today=date.today(), use_cache=True)
```

with `NAMES = VLO MPC PSX EC LNG XOM CVX APA EOG CNQ SU TTE E HAL`; the per-name figures come
from `gather_factor_inputs(...)`, `enterprise_value(f, fi.fx)`,
`_earnings_yield_outcome(fi)` and `valuation_band._choose_basis(f)`.
