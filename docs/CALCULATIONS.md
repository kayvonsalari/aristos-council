# Aristos Council — The Calculations

Every number that decides a verdict, in one place. This document is generated from the
code and points to it; where they disagree, the code wins. Sources:
`src/aristos_council/rank_engine.py`, `factors.py`, `etf_static.py`, `tools/screening.py`,
`tools/technical.py`, and the strategy YAMLs under `strategies/`.

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

### 1.1 Rank display semantics (RANK-DISPLAY-1)

Two different numbers are easy to confuse — a name's **position** in the cohort and its
**combined rank-sum** (the score). Every display therefore renders them through ONE shared
formatter (`rank_engine.format_position_cell`), used by the CLI ranked table, the Run tab's
table, the markdown download, and Company Check:

```
#1 of 9 · score 11 (best 3 · worst 27)
```

- **`rank 1 = best`** — on every factor, and a **lower** combined rank-sum is better.
- **`#N of M`** — the 1-based cohort **position**, leading the cell so the rank-sum can never
  be misread as a position. `M` is the **rateable** cohort size (excluded and UNRATEABLE names
  are not in it). Ties **share** a position (competition ranking) and are marked `(tied)`.
- **`score S`** — the combined rank-sum itself.
- **`(best B · worst W)`** — the bounds that give the score its scale: `B` = number of factors
  (rank 1 on all of them), `W` = factors × cohort size. Without them, "score 11" says nothing.
- When no position is known (an excluded name), the cell degrades to a bare `score S`.

This is **presentation only** — no verdict, rank, or gate reads any of it.

## 2. The factors (`factors.py`)

All factors are pure functions of adapter data; each returns a float or `None`
(not-evaluated). Directions come from the registry; a strategy YAML may override.

| Factor | Formula | Direction | Notes |
|---|---|---|---|
| `earnings_yield` | EBIT / EV, where **EV = market cap + total debt − cash & short-term investments**; falls back to EBIT/market cap when EV components are missing, then 1/PE | high | Only a deeply cash-rich name whose cash exceeds market cap + debt (**EV ≤ 0**) **abstains** — a merely net-cash mega-cap (cash > debt but < market cap, e.g. NVDA/GOOGL) still has a large positive EV and ranks normally. EV is a refined proxy (see §6). For a foreign issuer on a US listing, debt/cash/EBIT are **converted** from the accounts currency to the price currency before EV is formed (tagged `[ev, DKK→USD @ rate (date)]`); a failed FX fetch **abstains** rather than mix currencies (VERIFY-2). |
| `roic` | Through-cycle ROIC (return on invested capital): **NOPAT** (net operating profit after tax — operating profit with taxes removed) / invested capital, averaged over a 4-year window | high | Negative-equity-safe (uses provided invested capital, not equity). A loss-mixed window whose through-cycle effective tax rate degenerates (∉ [0, 0.6]) or whose pretax sum is non-positive **abstains** rather than print a "−0" NOPAT (VERIFY-2); a single loss year inside a net-positive window still computes (the through-cycle design). |
| `momentum_12m` / `momentum_6m` | Trailing total return over ~252 / ~126 trading days | high | Price-derived from the close series. |
| `low_volatility` | Annualized volatility of daily returns | **low** | Pairs with momentum to exclude falling knives (a crashing name is high-vol *and* negative-momentum). |
| `net_payout_yield` | (dividends + buybacks) / market cap; falls back to dividend yield where buyback data is unavailable on free data | high | The fallback under-credits heavy repurchasers — documented, not hidden. |
| `dividend_streak` | Consecutive calendar years of dividend **increases** (see §3) | high | `None` when history is too short to derive. |
| `revenue_growth` | Revenue **CAGR** (compound annual growth rate — the smoothed year-over-year rate) over the fundamentals window, with a cyclical-base guard | high | |
| `price_to_book` | Price / book value: vendor `priceToBook`, else market cap / closing shareholders' equity | **low** | The financials-lens value leg (FIN-1). **Abstains** on non-positive or absent book equity — a negative-book value trap can't read as cheap. |
| `return_on_equity` | Net income / equity: vendor `returnOnEquity` (TTM), else latest net income / mean(opening+closing equity) | high | The financials-lens quality leg (FIN-1). **Abstains** on non-positive equity or missing income. |
| `distribution_yield` | The fund's trailing distribution/dividend yield (`dividend_yield`, a DECIMAL) | high | ETF lenses only — the income leg of the dividend-ETF lens. See §2.1. |
| `expense_ratio` | The fund's ongoing charge (`net_expense_ratio`), vendor value as-is | **low** | ETF lenses only — the sole LOW-direction ETF leg: cost compounds against the holder forever, so cheaper ranks better. See §2.1 for the percent-not-fraction unit trap. |
| `fund_size` | The fund's net assets (`total_assets`) | high | ETF lenses only — a liquidity and closure-risk proxy. See §2.1. |
| `piotroski_f_score` | The Piotroski F-Score: nine binary accounting checks over the two most recent annual periods, 0–9 | high | Registered but selected by **no strategy**; shares its arithmetic with the `min_f_score` screen criterion. **Abstains** below 5 computable checks. A coarse integer, so it ties heavily on a small cohort — better as a screen than a rank leg. Full definition in **§4.1**. |

### 2.1 ETF factors

The three ETF lenses (`etf_dividend_v1`, `etf_growth_v1`, `etf_core_v1`) rank **fund
attributes**, because a fund has no ROIC and no earnings yield. `momentum_12m` is the same
price-derived factor the stock lenses use; the three above exist only for funds. Full lens
descriptions are in the README's **[ETF lenses](../README.md#etf-lenses)**.

- **`expense_ratio` — LOWER is better.** The one inverted ETF leg. **Unit trap (ETFCHK-3):
  the vendor value is a PERCENT, not a fraction** — SCHD's 0.06% arrives as `0.06`. Ranking is
  unit-invariant, so the factor is untouched by this; but any *absolute* presentation must
  divide by 100 first. Company Check's plain-English gloss does exactly that, reporting the
  annual fee per €1,000 held (`0.06` → €0.60 per €1,000, every year).
- **`fund_size` — HIGHER is better.** Net assets, standing in for liquidity and closure risk,
  not for quality. A big fund is not a good fund; it is a fund that can be traded and is
  unlikely to be wound up.
- **`distribution_yield` — HIGHER is better.** A DECIMAL. A true zero is a **product finding,
  not a data gap**: an **ACC** (accumulating) share class reinvests rather than distributing,
  so its zero is honest and is ranked as such — distinct from an abstention, where the value
  is absent or was withheld. That distinction is why the index-tracker lens carries **no
  `distribution_yield` factor at all**: its cohorts deliberately mix ACC and DIST classes, so
  yield there is a share-class artefact and ranking on it would penalise an ACC class for a
  structural zero.
- **Abstention never excludes.** All three return `None` when the provider omits the field,
  and the ETF lenses run `missing: neutral` with no screens and no floors: the rank is imputed
  from the fund's present-factor ranks (marked `*`) and the fund stays in the table.
- **What they cannot measure.** A fund's real quality is its index methodology and its tracking
  accuracy; no free vendor field captures either. Each ETF lens carries that admission verbatim
  in its own YAML `rationale` so it travels with every render.

**Asset-kind gate.** Each ETF lens declares `asset_kinds: [etf]`, and the gate fires *before*
any screen or factor path — a vendor serves look-through "fundamentals" for an index tracker
happily, so a leak across asset classes would produce quiet garbage rather than an honest
exclusion. Confirmed-only, like the sector gates: a missing vendor `quoteType` never gates. It
renders as `asset kind 'ETF' outside this strategy's scope`
(`factors.is_asset_kind_out_of_scope`).

### 2.2 The ETF static layer

Some fields the ETF lenses rank on — expense ratio, fund size, distribution yield — are served
unevenly or not at all by the free vendor, yet they change rarely and are trivially
human-verifiable from a factsheet. A committed, dated CSV (`data/etf_static.csv`, read by
`etf_static.py`) fills those gaps for **ETF-kind names only**. A stock never reads it.

Four disciplines, each matching the rest of the codebase:

1. **Vendor precedence.** A vendor value that is present *and* plausible always wins; static
   fills only what the vendor doesn't serve or serves implausibly. Plausibility is deliberately
   loose — the lens ranks these relatively, so units don't matter: it rejects only what cannot
   be real (a non-positive expense ratio or fund size, a distribution yield outside `[0, 1]`).
2. **It shows its work.** Every static-sourced number carries the provenance receipt
   `static: <as_of>, <source>` — e.g. `static: 2026-06-01, Schwab factsheet` — which flows
   through the same factor-source path the FX receipt uses, so a report renders it as
   `[static: <as_of>, <source>]`. It surfaces in the per-run FACTOR INTEGRITY block alongside
   `computed` / `fallback:…` / `abstained`, and static-served values are handed to the narrator
   as ledger entries carrying that receipt, so a cited fee can be audited back to the factsheet
   and the date. **Only values actually served from static are tagged** — never a phantom fill.
   As of NARR-LEDGER-1, the narrator's ledger is not limited to the static-served subset: the
   expense_ratio / fund_size / distribution_yield triad is surfaced from ANY source with a real
   value — vendor-`computed` included, the common case since static is a fallback — each with
   its own actual provenance tag, so a cited fee can be audited whether it came from the
   factsheet or the vendor.
3. **No silent stale data.** An entry whose `as_of` is more than **90 days** old — or is
   unparseable, since an unverifiable freshness cannot be trusted fresh — **abstains**: the
   field is *not* filled and the note `static data stale — refresh required` is surfaced
   instead. Stale data is never served quietly. The file's header records the monthly
   re-verification ritual against each fund's official factsheet.
4. **Replay-safe.** The CSV is committed, so a frozen run replays it byte-identically — the
   static data lives in the record's world like every other frozen input. A *missing* file is
   tolerated: the layer simply does nothing.

**Where the rows come from.** `scripts/generate_etf_static_rows.py <universe_id>` fetches each
ticker's EODHD `/fundamentals` payload and prints paste-ready CSV rows to STDOUT — it never
writes to disk; a human reviews, verifies, and commits them. The script carries the guards
learned live: the fee is read from `ETF_Data.Ongoing_Charge` **only** (EODHD's
`NetExpenseRatio` returns a fake `0.0000` for a whole class of UCITS funds, and a phantom 0%
fee is worse than a blank), an implausible `TotalAssets` outside `[1e7, 1.5e12]` is blanked
rather than served (the mis-scaled-AUM lesson), the percent yield is converted to the CSV's
decimal, and ACC/DIST is inferred from the yield (a true zero → `acc`) with a missing yield
left blank — omit, never invent.

The columns `share_class` and `domicile` are **descriptive only**: no factor reads them.

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
| `min_f_score` | *none — enabled by no lens* | Accounting quality: nine binary checks on the annual statements (see **§4.1**). Registered and optional; no threshold is documented because no strategy adopts one yet. |
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

### 4.1 Accounting quality — the Piotroski F-Score (PIOTROSKI-1/2)

Nine binary accounting checks over the **two most recent annual periods**, one point each,
score 0–9 (Piotroski, 2000). There is **ONE implementation**
(`tools/screening.piotroski_f_score`) with **TWO consumers**, so a screened and a ranked
F-Score can never diverge:

- the optional screen criterion **`min_f_score`** (`tools/criteria/registry.py`), and
- the rankable quality factor **`piotroski_f_score`** (`factors.py`, direction **high**).

Write `X₀` for the current annual period and `X₁` for the prior one. All nine inputs are
annual statement series on `Fundamentals`, newest-first by adapter contract; nothing here
reads a TTM scalar, and no value is derived from price.

| # | Group | Check (registry name) | The comparison the code makes | `Fundamentals` fields |
|---|---|---|---|---|
| 1 | Profitability | `roa_positive` | `ROA₀ > 0`, where `ROA = net_income / total_assets` (**same-year** assets) | `net_income`, `total_assets_annual` |
| 2 | Profitability | `ocf_positive` | `OCF₀ > 0` | `operating_cash_flow_annual` |
| 3 | Profitability | `roa_improved` | `ROA₀ > ROA₁` | `net_income`, `total_assets_annual` |
| 4 | Profitability | `ocf_exceeds_net_income` | `OCF₀ > net_income₀` — the **accrual** (cash-quality) check: earnings not backed by cash. Compared as raw amounts; dividing both by the same total assets is equivalent, so this check needs **no** balance sheet | `operating_cash_flow_annual`, `net_income` |
| 5 | Leverage & liquidity | `ltd_ratio_decreased` | `long_term_debt₀ / total_assets₀ < long_term_debt₁ / total_assets₁` | `long_term_debt_annual`, `total_assets_annual` |
| 6 | Leverage & liquidity | `current_ratio_improved` | `current_assets₀ / current_liabilities₀ > current_assets₁ / current_liabilities₁` | `current_assets_annual`, `current_liabilities_annual` |
| 7 | Leverage & liquidity | `no_new_share_issuance` | `shares₀ ≤ shares₁` — **non-strict**, so a flat share count scores the point | `shares_outstanding_annual` |
| 8 | Efficiency | `gross_margin_improved` | `gross_profit₀ / revenue₀ > gross_profit₁ / revenue₁` | `gross_profit_annual`, `total_revenue` |
| 9 | Efficiency | `asset_turnover_improved` | `revenue₀ / total_assets₀ > revenue₁ / total_assets₁` | `total_revenue`, `total_assets_annual` |

**Two annual periods are the requirement.** Six checks (3, 5, 6, 7, 8, 9) compare the two
years and need both; three (1, 2, 4) read the current year alone. A name with a single
annual period therefore scores at most 3 of 9 computable checks and **abstains** (below the
five-check minimum), which is the intended outcome — not a low score.

**Field-name trap.** The balance-sheet line is `total_assets_annual`, **not**
`total_assets` — that field already exists and means an ETF's *net assets* (a fund-size
scalar). Overloading it would mix a fund-size number into a balance-sheet series.

**Period matching (PIOTROSKI-2).** The cross-statement checks must compare the same fiscal
period, and the positional lists cannot guarantee that (NaN cells are dropped *per series*,
so index `[0]` of two statements can refer to different fiscal years — the AAPL/GIS/F
implausible-cell class). When the adapter supplies the period-labelled series
(`aligned_annual` + `aligned_period_ends`), the two reference years are the two most recent
period-end dates across all supplied series, and a series with no value at a reference date
contributes nothing for that year — **the dependent check counts unavailable rather than
comparing the wrong years**. When those dicts are absent (EODHD, fakes, older fixtures) the
score falls back to positional `[0]`/`[1]`, the original PIOTROSKI-1 behaviour.

**Computability — a missing check is never a failed check.** This is project rule 3
(`null ≠ false`) applied at check granularity. A check whose inputs are missing, **or whose
denominator is zero or negative**, scores no point *and* is counted **unavailable**; it is
never counted as a check the company failed. (A non-positive denominator makes the ratio
meaningless — a negative-asset ROA flips sign — so the check that needs it is unavailable.)
With `C` = computable checks and `M = 9 − C` unavailable:

- **`C < 5` → the whole score ABSTAINS** (`score = None`). A partial tally reads as a
  terrible company when it is actually an absent statement, so nothing is reported rather
  than something misleadingly low. The raw point tally is still carried on the result for
  the note, but it is not a score.
- **`C ≥ 5` → the score is the point tally out of 9**, published *with* the unavailability
  accounting, so a 6/9 built from seven checks is never mistaken for a 6/9 built from nine.
- An abstaining `min_f_score` **never excludes** a name, and the factor returns `None`
  (imputed or ranked-worst per the strategy's `missing` policy), never a zero.

**Conventions that differ from a published F-Score** — decided deliberately, so a
third-party number may differ by a point:

- **Zero long-term debt is STRICT.** A firm carrying no LTD in either year has a ratio of
  `0.0` both years, which did *not* decrease, so check 5 scores **no point** — and counts as
  *computed*, not unavailable. Comparability with published F-Scores beats economic charity.
- **ROA and asset turnover use same-year (ending) total assets**, not Piotroski's
  beginning-of-year / average-assets denominator. One convention applied identically to both
  years, so the year-over-year comparison stays apples-to-apples. A stated simplification.

**Display convention.** The factor value renders as a score over its ceiling — **`7/9`**, not
a bare `7`, because the bare number hides the scale (`company_check.format_factor_value`).
The unavailability accounting travels in the **criterion note**, which is the only place that
has the counts:

```
F-Score 6/9 — computed from 9 checks, 0 unavailable
F-Score 3/9 — computed from 7 checks, 2 unavailable (unavailable: current_ratio_improved, gross_margin_improved)
F-Score not computable: only 0 of 9 checks available (minimum 5) — abstained (unavailable: …)
```

**Why `min_f_score` ships disabled, with no documented threshold.** It is registered with a
0–9 integer threshold whose registry *default* is 5, and **no lens selects it** — no strategy
YAML names the criterion or the factor. That default is a widget default, not an adopted
bar, so this document deliberately states **no threshold** for it: a threshold gets adopted
only after live evidence on the actual universes, the way the 12% ROIC floor was. It is also
**not gating-eligible** — the score aggregates nine checks whose availability depends on
provider statement coverage, so a hard deterministic veto on it would fail names for data
gaps; no `is_gating` flag is set or proposed.

**Better as a screen than as a rank leg.** The F-Score is a **coarse 0–9 integer**, so on a
20–40 name cohort it produces large **tied blocks** that the rank engine can only resolve by
averaging their positions. Measured on the committed probe output (`probe_stocks.csv`, from
`examples/piotroski_probe.py`, over the demo cohorts now kept under
`tests/fixtures/universes/`): the 39 names of `growth_40_v1` that scored occupy just **six
distinct values** (2, 4, 5, 6, 7, 8), with the two largest blocks **11 names each** at 5/9
and 6/9 — more than half the cohort in two ties. That is an argument for using it as a
*floor* on who qualifies, not as a leg that orders survivors; it is **not** to be "fixed"
with a tiebreaker. The factor is also deliberately absent from `PRICE_DERIVED_FACTORS`, so
the backtest cannot validate it point-in-time (historical point-in-time fundamentals are not
available on free data).

**Coverage, measured.** From the same probe output: across `defensive_16_v1`,
`defensive_income_16_v1`, `growth_40_v1` and `energy_watch_v1`, every name that had annual
statements at all computed **all nine** checks (`0 unavailable`) — so the score is not
systematically depressed by data gaps there. The one exception is the delisted **WBA**, which
reaches `0` computable checks and **abstains** — the intended path, and distinct from a score
of 0. **Banks and insurers lose exactly two checks**: they report no current
assets/liabilities and no gross-profit line, so `current_ratio_improved` and
`gross_margin_improved` are unavailable and the thirteen bank/insurer names in
`financials_16_v1` score out of **7** computable — still above the five-check minimum, so
they score rather than abstain, but on a structurally shorter ruler than a staple's. The
three non-lender financials in that cohort (BLK, V, MA) compute all nine. A per-check
availability report over any cohort is what `examples/piotroski_probe.py` exists to produce;
it is the gate question ("is it computable here at all?") that precedes any question about
whether the score is *useful* here.

## 5. Guards

- **UNRATEABLE** — a ticker with failed fundamentals *and* no usable price history (a
  delisted name: PARA, WBA) is listed separately with the reason, receives **no verdict**,
  and never reaches the narrator. A SELL implies an assessment was made; "no data exists"
  is a different statement.
- **Sector exclusion** — confirmed-only, case-insensitive (e.g. financials under Magic
  Formula, where ROIC is not meaningful). An unknown sector excludes nothing.
- **Sector inclusion** (the mirror, `include_sectors`, FIN-1) — when set, admits **only**
  the listed sectors and gates any confirmed out-of-scope name with `sector '<X>' outside
  this strategy's scope`. `financials_v1` uses it to build a financials-only table.
  Confirmed-only like the exclusion gate: a missing sector is never gated. The two gates
  are independent — a strategy sets one or neither.
- **Asset-kind gate** (`asset_kinds`, ETF-1) — the wall between asset classes, fired **before**
  screen, cap, sector, and factor paths so a fund's look-through "fundamentals" can never leak
  into a stock lens (or an operating company into an ETF lens). Confirmed-only: a missing
  vendor `quoteType` never gates. Renders as `asset kind 'ETF' outside this strategy's scope`.
  A strategy that omits `asset_kinds` scopes nothing and is unchanged. See §2.1.
- **Vendor sanity flags** (VERIFY-2 / FIN-1) — cheap boundary checks flag absurd vendor
  values (dividend yield > 15%, negative market cap, unit-confused debt/equity > 10000,
  P/B > 100, ROE > 300%). A flag **never corrects and never fails** a name: the value is
  withheld from the narrator's evidence and surfaced in Company Check's DATA INTEGRITY, so
  vendor junk can neither be quoted nor silently used.
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
| **Excluded from the value lenses; covered by `financials_v1`** | Financials (banks, insurers, payment networks) | ROIC and EV are category errors for balance-sheet businesses, so the value lenses exclude them by name — but the exploratory `financials_v1` lens (FIN-1) ranks them on price-to-book + return on equity + momentum instead, the gate inverted. | Grading `financials_v1` on the prospective scoreboard (today it is exploratory only). |
| **Supported, distortion disclosed** | Deep cyclicals (energy, miners, autos, memory); REITs & utilities | Trailing metrics snapshot the cycle (4-year through-cycle averaging dampens, does not fix); payout/FCF (free cash flow) semantics half-fit — REITs need **FFO** (funds from operations — the REIT-specific cash-earnings measure), utilities run structural negative FCF by regulated design (the documented council-era lesson). | An FFO / regulated-asset data source. |
| **Clean fit** | Asset-light & industrial operating businesses — mature tech, staples, discretionary, pharma, industrials, retail, defence | Trailing fundamentals and cash-based coverage describe them well. | — (the current manifests, minus the distortion cases). |

**ETFs are a separate axis, not a sector.** The tiers above scope *company* metrics; a fund is
gated out of every stock lens by the asset-kind gate (§5) and ranked instead by the three ETF
lenses on fund attributes (§2.1). Their disclosed boundary is not sectoral but methodological:
**index methodology and tracking accuracy are not vendor-measurable on free data**, so the ETF
lenses compare cost, scale and trend among self-declared funds of a category — and say so in
their own YAML. All three are exploratory; none is on the prospective scoreboard.

## 10. Future work & data dependencies

Honest direction of travel — each entry is what, its concrete requirement, and the
trigger. No dates.

- **Financials strategy** (P/B + ROE, banks-only universe): **shipped** as the exploratory
  `financials_v1` lens over `financials_16_v1` (FIN-1). Remaining work: grading it on the
  prospective scoreboard, and a foreign-financials universe once the currency-conversion
  layer (VERIFY-2) has field history — the first universe is deliberately all-US
  (currency-clean) so the P/B and ROE fallbacks never mix currencies.
- **Estimate-revision signals + point-in-time backtesting**: requires a paid
  fundamentals tier (~€50–150/mo class — Sharadar / EODHD upper tiers); trigger is the
  prospective scoreboard maturing enough to justify historical validation.
- **REIT / utility coverage**: requires FFO / regulated-asset data; trigger is a
  defensive user who needs those sectors.

These are considered extensions with named requirements — not commitments. The system
prefers a documented boundary to an untested feature.
