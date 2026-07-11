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
| `earnings_yield` | EBIT / EV, where **EV = market cap + total debt − cash & short-term investments**; falls back to EBIT/market cap when EV components are missing, then 1/PE | high | Only a deeply cash-rich name whose cash exceeds market cap + debt (**EV ≤ 0**) **abstains** — a merely net-cash mega-cap (cash > debt but < market cap, e.g. NVDA/GOOGL) still has a large positive EV and ranks normally. EV is a refined proxy (see §6). |
| `roic` | Through-cycle ROIC (return on invested capital): **NOPAT** (net operating profit after tax — operating profit with taxes removed) / invested capital, averaged over a 4-year window | high | Negative-equity-safe (uses provided invested capital, not equity). |
| `momentum_12m` / `momentum_6m` | Trailing total return over ~252 / ~126 trading days | high | Price-derived from the close series. |
| `low_volatility` | Annualized volatility of daily returns | **low** | Pairs with momentum to exclude falling knives (a crashing name is high-vol *and* negative-momentum). |
| `net_payout_yield` | (dividends + buybacks) / market cap; falls back to dividend yield where buyback data is unavailable on free data | high | The fallback under-credits heavy repurchasers — documented, not hidden. |
| `dividend_streak` | Consecutive calendar years of dividend **increases** (see §3) | high | `None` when history is too short to derive. |
| `revenue_growth` | Revenue **CAGR** (compound annual growth rate — the smoothed year-over-year rate) over the fundamentals window, with a cyclical-base guard | high | |

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
| `max_payout_ratio_fcf` | 0.80 | Coverage measured against **cash, through-cycle**: CURRENT-year dividends_paid / the MEAN free cash flow over the last up-to-4 fiscal years (≥2 required; FCF = free_cash_flow, else operating_cash_flow + capex). Single-year FCF carries one-off cash events (KO's fairlife earnout crushed one year to a 2.81 payout) exactly as GAAP earnings carried non-cash charges (ABBV 3.26); the through-cycle mean, matching ROIC's window, dampens both — the numerator stays current-year. Mean FCF ≤ 0 abstains (utilities); EPS payout is a MARKED fallback (< 2 years of FCF history). **0.80 against FCF follows the common 70–80% cash-coverage prudence band; like all thresholds it is a stated convention, never fitted to outcomes.** (The EPS `max_payout_ratio` at 0.85 stays in the registry for other strategies.) |
| `min_market_cap` | strategy-specific | Micro-cap noise. |
| `min_price_momentum` | −0.10 (12m) | **Breakdowns, not flatness**: a defensive down >10% on the year is breaking (T at −26%); a quiet staple down 0–10% passes. The ranker handles the gradient among survivors. |
| `min_dividend_streak` | 10 years | Cut history: T (cut 2022 → streak 0) and MMM (cut 2024) fail; PG/KO/JNJ/MCD pass. |
| `max_debt_to_market_cap` | 1.0 | Balance-sheet risk: total debt ≤ market cap. VZ (~1.13×, $201B) fails. Uses debt/market-cap, **not** debt/equity — robust to negative-equity buyback names (MCD). |
| `min_roic` | 0.12 (magic_value_screen) | The quality floor for value strategies. |
| `revenue_cagr`, `peg_ratio` | growth_v1 | GARP criteria; **PEG** (the P/E ratio divided by the earnings-growth rate — a valuation-against-growth measure, where roughly ≤1 reads as "reasonably priced for the growth") uses in-house earnings-growth with a cyclical-base guard. |

**Growth-metric cyclicality guards** (the GARP screen, `growth_v1`). The three growth
criteria are hardened against a single trough or peak year flattering a metric:
`revenue_cagr` is a base-year-robust log-linear **trend** over the window — not a naive
two-point endpoint ratio — and the note flags when the two diverge (a cyclical-base
signal); `peg_ratio` winsorizes (caps an extreme value at a set percentile so one
outlier can't dominate) an extreme growth input, so a trough-inflated CAGR
cannot make a stock look spuriously cheap; and `roic` is computed on through-cycle
(multi-year mean) operating income, not a single peak. Each degrades to **not-evaluated**
rather than guessing when the statements are too short or earnings are negative.

**Screen-as-prefilter.** Rank strategies set `prefilter_screen: true`: only names that
pass the lens screen's absolute floors are ranked. This enforces **one definition per
strategy** — the screen says who qualifies, the ranking orders survivors. It closes the
rank-relative-vs-absolute-floor gap (a name can rank top-quintile on relative ROIC while
failing the strategy's own 12% floor — BMY did exactly this until the prefilter).
Exclusion happens **only on a confirmed FAIL**; a not-evaluated criterion never excludes.

**Borderline tag** (legibility, no logic change). A confirmed fail whose observed value
is within **5% (relative)** of its threshold is tagged `[borderline]` in the exclusion
reason, e.g. `screen: min_roic (observed 0.1198 vs threshold 0.12) [borderline]`. The
margin is the symmetric relative gap `|observed − threshold| / |threshold|`, correct for
both `min_*` (fail below) and `max_*` (fail above) since an excluded value always sits on
the failing side. The floor is unchanged — a borderline fail is still a fail; the tag
just flags a knife-edge miss to the reader (`factors.is_borderline_fail`).

**Diverging-exclusions flag** (disclosure, no logic change). When an excluded name has ANY
CONFIRMED fundamental-criterion FAIL *and* its trailing 12-month price momentum is at or
above **+0.30**, the exclusion line is annotated
`[⚠ price diverging: +XX% 12m — cyclical inflection or mania; human review]` (the actual
momentum is shown). The **0.30** threshold is a stated convention, not fitted to outcomes —
like every threshold here. The price-momentum criterion itself is excluded from
"fundamental" (a price criterion can't be the price-vs-fundamentals tell), and an
ABSTENTION never counts as a fail (rule 3). Base-rate warning: *the flag also decorates
value traps whose price has not finished falling; it marks disagreement, not direction.*
The flag NEVER alters a verdict or an exclusion (`factors.price_divergence_flag`).

*Worked example (Company Check, `magic_formula_momentum_v1`).* **MU** — up **+711%** over
12 months while `min_roic` is a confirmed FAIL (**0.048** vs the 0.12 floor): a fundamental
fail with a runaway price, so the flag **fires**. **GS** — up **~+50%** but excluded by the
financials **sector gate**, with `min_roic` merely *abstaining* (ROIC isn't computable for a
bank), i.e. NO confirmed fundamental fail: the flag is **correctly silent**. The pair shows
the two guards working — a real fail + momentum trips it; an abstention or a non-fundamental
(sector) exclusion does not.

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

- **GAAP payout noise — the honest post-validation account**: AbbVie and Merck were
  GAAP-noise victims (rescued by the FCF basis); Kimberly-Clark was a single-year-cash
  victim (single-year FCF payout 1.57, four-year mean ~0.7 — rescued by the through-cycle
  basis); PepsiCo remains excluded on both bases (~1.08 on the 4y mean) — the one original
  exclusion that was correct as issued. Each claim was revised when pre-registered
  validation contradicted it; the revisions are the record. `conservative_screen_v1`
  measures coverage against through-cycle free cash flow (`max_payout_ratio_fcf`, §4); the
  EPS basis remains a MARKED fallback when cash-flow history is too short.
- **A defended exclusion — Coca-Cola**: Coca-Cola (KO) is excluded at 1.198 on the
  four-year-mean basis and stays excluded knowingly: two of its four window years carry
  large one-off cash outflows (a tax deposit and an acquisition earnout — attribution to be
  verified against filings), which inflate the ratio; but even on its clean years KO's
  dividend consumes ~0.91 of free cash flow. Its EPS payout (0.65) and 23-year growth
  streak say the dividend is safe by earnings and history; the 0.80 cash bar says it is
  tight by cash. This screen is deliberately the strict one — a less conservative variant
  would set the bar at 0.90 and say so. The stop-rule was exercised, the underlying series
  read, and the threshold not moved: excluding a >90%-cash-payout name is the criterion's
  definition operating, not a defect.
- **Knife-edge floors**: absolute thresholds exclude at any margin (PFE at ROIC 0.1198 vs
  a 0.12 floor). That is what floors do, but two hundredths of a percent is inside
  measurement noise for a computed ROIC. These near-misses are now flagged `[borderline]`
  in the exclusion line (§4) — legible, though the floor still governs.
- **Small universes**: a quintile cut on 6 survivors makes BUY = top 2 — an artifact. Use
  `top_k`, or treat the screen as the product on curated lists.
- **Trailing data**: every factor is historical. Momentum is the only forward-leaning
  signal; there is no estimate-revision input on free data.
- **EV is refined, not exact**: `earnings_yield` uses EBIT/EV (EV = market cap + total
  debt − cash & short-term investments), shipped after the diagnostic confirmed the
  components populate for **95% of growth_40** (`scripts/check_ev_fields.py`, 38/40; the
  two gaps were delisted PARA/WBA). It remains a REFINED proxy: yfinance `totalDebt`
  includes operating leases, and there is no minority-interest or pension adjustment.
  Missing EV components fall back to EBIT/market cap; only a name whose cash exceeds market
  cap + debt (EV ≤ 0 — a deeply cash-rich small cap) abstains rather than emit a
  negative-yield rank artifact (a merely net-cash mega-cap like NVDA/GOOGL keeps a large
  positive EV and ranks normally).

## 7. Evidence coverage — what gates the escalation (not the LLM's number) (`coverage.py`)

The low-confidence human-review escalation used to consume the NARRATOR's self-assigned
confidence — an LLM number moving a mechanical outcome, the failure class the council was
demoted for. It now consumes a **deterministic evidence-coverage score** in `[0, 1]`: a
pure function of what the run actually saw. The narrator may still express verbal nuance
in prose (shown as a non-gating "note on conviction"), but its number gates nothing.

Five components, each in `[0, 1]`, combined by fixed weights (sum = 1.0):

| Component | Weight | Definition |
|---|---|---|
| `criteria` | 0.30 | screen criteria EVALUATED / total (a NOT-EVAL is not evidence) |
| `factors` | 0.20 | `1 − fraction of ranker factors imputed` (absent factor values) |
| `provenance` | 0.25 | figures VERIFIED / audited (mismatch + unresolvable discount) |
| `fundamentals` | 0.15 | core fundamentals fields present / expected (market_cap, pe, eps, fcf) |
| `price` | 0.10 | price history sufficient for the technical snapshot (0 or 1) |

`coverage = Σ weightᵢ · componentᵢ`. A component whose data is **absent** (never gathered
in this state — a standalone/legacy run has no ranker factors; a bare unit-test state has
no tool calls) defaults to **1.0**: it never invents a penalty from context that was never
collected. A component whose fetch was **attempted and failed** (a real fundamentals/price
error) scores **0.0** — that IS a coverage gap. The escalation fires when
`coverage < veto.min_confidence` (the same YAML floor, now read as a coverage floor).

No LLM anywhere in this score. It is the deterministic replacement for "the model felt
0.55 sure", and it is unit-tested (full data → high; a two-criteria screen or an
imputation-heavy rank → discounted; a failed fundamentals fetch → penalized).

## 8. Anatomy of a strategy (`strategies/magic_formula_momentum_v1.yaml`)

A strategy is a versioned YAML file, not code. The flagship, annotated — one line on what
changing each field does:

```yaml
id: magic_formula_momentum_v1     # unique id; must encode a version (…_v1). Names the file.
name: Magic Formula + Momentum    # human label shown in the UI dropdown.
version: 1                        # bump on any published change (files are immutable).
factors:                          # the rank factors (rank-sum, equal weight, no tuning):
  - name: roic                    #   quality — return on invested capital (high = better).
  - name: earnings_yield          #   value — EBIT/EV (high = better). Drop one -> a different strategy.
  - name: momentum_12m            #   trend — 12m return; remove it and you have classic Magic Formula.
cut: quintile                     # verdict cut: top 20% BUY / 60% HOLD / 20% SELL. top_k for small lists.
missing: worst                    # a NOT-EVAL factor -> worst rank. 'neutral' imputes; 'exclude' drops.
min_market_cap: 5.0e9             # universe floor; raise it to exclude smaller names.
exclude_sectors:                  # confirmed-only sector exclusion (ROIC/EV are meaningless here):
  - Financial Services            #   financials — balance-sheet businesses.
  - Financials
  - Utilities                     #   utilities — structural negative FCF by design.
council_screen_strategy: magic_value_screen_v1   # the lens the narrator judges a pick against.
prefilter_screen: true            # rank ONLY names passing that lens's absolute floors (min_roic 12%).
```

Everything that decides a verdict is here; the arithmetic behind each factor is in §1–§2.

**Adding a strategy.** Write a new YAML naming registry factors and a screen; the
schema-split classifier surfaces it in Council Station's dropdown automatically — no code
changes. A published file is never edited in place: change it by saving a new version
(`edit-as-new-version`), so every recorded verdict stays reproducible against the exact
file it ran under.

## 9. Scope: where the metrics apply

The factors and screens are honest on operating businesses and disclose their limits
elsewhere — a documented boundary beats an untested feature.

> Plain-English rationale — why the value lenses exclude banks and utilities, how the
> `financials_v1` lens inverts the gate to rank banks on P/B + ROE, why utilities are
> covered by the defensive lens, and the V/MA payment-network odd corner, with the GS and
> DUK worked examples — is in **[Which lens for which company](../README.md#which-lens-for-which-company)**.

| Tier | Sectors | Why | Revisit trigger |
|---|---|---|---|
| **Excluded by design** | Financials (banks, insurers) | ROIC and EV are category errors for balance-sheet businesses; the sector exclusion fires by name. | A funded use case — basic P/B + ROE data is already free. |
| **Supported, distortion disclosed** | Deep cyclicals (energy, miners, autos, memory); REITs & utilities | Trailing metrics snapshot the cycle (4-year through-cycle averaging dampens, does not fix); payout/FCF (free cash flow) semantics half-fit — REITs need **FFO** (funds from operations — the REIT-specific cash-earnings measure), utilities run structural negative FCF by regulated design (the documented council-era lesson). | An FFO / regulated-asset data source. |
| **Clean fit** | Asset-light & industrial operating businesses — mature tech, staples, discretionary, pharma, industrials, retail, defence | Trailing fundamentals and cash-based coverage describe them well. | — (the current manifests, minus the distortion cases). |

## 10. Future work & data dependencies

Honest direction of travel — each entry is what, its concrete requirement, and the
trigger. No dates.

- **Financials strategy** (P/B + ROE, banks-only universe): the data is already
  sufficient on free tiers; the trigger is a real user, not budget.
- **Estimate-revision signals + point-in-time backtesting**: requires a paid
  fundamentals tier (~€50–150/mo class — Sharadar / EODHD upper tiers); trigger is the
  prospective scoreboard maturing enough to justify historical validation.
- **REIT / utility coverage**: requires FFO / regulated-asset data; trigger is a
  defensive user who needs those sectors.

These are considered extensions with named requirements — not commitments. The system
prefers a documented boundary to an untested feature.
