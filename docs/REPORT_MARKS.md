# Aristos Council — Marks on a Report

Every annotation you can meet on an Aristos output — a ranked table, a Company Check, a
snapshot note, a narrated report — in one place. For each: the **exact mark as rendered**,
**what fired it**, and **what it does *not* mean**. The theme throughout: these marks
*disclose*; they never silently change a number or a verdict. Sources in
`src/aristos_council/` are named per row.

| Mark (as rendered) | What fired it | What it does **not** mean |
|---|---|---|
| `[⚠ price diverging: +35% 12m — cyclical inflection or mania; human review]` | An **excluded** name has a CONFIRMED fundamental-criterion FAIL **and** trailing 12-month momentum **≥ +0.30**. The actual momentum is shown. (`factors.price_divergence_flag`) | **Not a direction call.** It marks *disagreement* — price ran up while a quality floor failed — never the sign of the next move; it also decorates value traps still falling. It never alters a verdict or an exclusion. |
| `[⚠ narration check: "…" contradicts rank table — table is authoritative]` | The LLM narrative made an ordinal/superlative claim (best, worst, top-, bottom-) that the deterministic rank table contradicts. (`narration_check.py`) | **Not a rewrite.** The prose is left exactly as written and the verdict is untouched — the line annotates the contradiction and defers to the table. |
| `[borderline]` | A confirmed FAIL whose observed value is within **5% (relative)** of its threshold, e.g. `screen: min_roic (observed 0.1198 vs threshold 0.12) [borderline]`. (`factors.is_borderline_fail`) | **Not a pass, not a softened floor.** A borderline fail is still a fail; the tag only flags a knife-edge miss to the reader. |
| `(=21.0 — tie broken alphabetically)` | Two or more names share the same combined rank-sum; the order between them is broken by ticker, alphabetically (deterministic). (`pipeline` tie disclosure) | **Not a quality distinction** between the tied names — display-only disclosure that the order among equals is arbitrary-but-stable. |
| `†` (suffix on a ranked row) + footnote `† PEP — screen criterion not evaluated: max_payout_ratio_fcf (mean FCF ≤ 0)` | A **ranked** name passed the screen while one screen criterion could not be evaluated (abstained). (`pipeline.ranked_abstention_footnotes`) | **Not a failure or an exclusion.** Abstention never excludes — the name qualified; the footnote just makes the un-evaluated check visible. |
| `[gating]` / `[non-gating]` (Company Check screen rows) | Derived from the **enforcing runner**: `[gating]` if a confirmed fail of this criterion would cap the disposition at SELL; `[non-gating]` if this evaluation cannot exclude/cap (e.g. it only abstains here). (`company_check`) | **`[non-gating]` does not mean "unimportant".** It means *this evaluation cannot exclude the name* — it's about enforcement, not weight. |
| `[ev, DKK→USD @ 0.1452 (2026-07-10)]` | A foreign issuer on a US listing: the EV earnings-yield was computed **after converting** debt/cash/EBIT from the accounts currency to the price currency at the shown rate and date. (`factors.CurrencyConversion`, VERIFY-2) | **Not an FX opinion.** It's a *receipt* — proof the conversion happened and at what rate, so a mixed-currency figure can't hide. On a failed FX fetch the factor **abstains** rather than mix. |
| `[computed]` · `[ev]` · `[fallback:pe]` · `[fallback:ebit_mcap]` · `[fallback:dividend_yield]` · `[abstained]` | The **basis tag** — which computation path produced a factor value. `ev` = true EBIT/EV; `fallback:pe` = EV components missing, used 1/PE; `fallback:dividend_yield` = buybacks unavailable, used dividend yield; `abstained` = not computable. (`factors` source tags) | **Not a quality score.** A fallback tag discloses a *coarser basis*, not a bad number — it exists so a proxy never masquerades as the real thing. |
| `*` (on a factor rank in the ranked table) | Under `missing: neutral`, a name lacking one factor value has that factor's rank **imputed** from the mean of its present ranks (judged on what it has). (`rank_engine`) | **Not a computed value.** The `*` marks a filled-in rank that neither helps nor hurts the name on the absent factor. |
| `dividend_yield 0.2393 (>15%) — vendor value implausible — flagged` | A cheap boundary sanity check caught an absurd vendor value: dividend yield > 15%, negative market cap, unit-confused debt/equity (> 10000), P/B > 100, or ROE > 300%. (`data.adapter.implausible_fields`, VERIFY-2 / FIN-1) | **Not a correction or a failure.** The value is flagged and **withheld from narrator evidence** — never silently fixed, never used to fail a name; it surfaces in Company Check's DATA INTEGRITY. |
| `NOT-EVALUATED` (screen criterion status) | A criterion could not be evaluated — missing/insufficient data, non-positive earnings, or a USD-threshold criterion on a non-USD listing (honest abstention, no FX). | **Not a FAIL.** Three-valued: pass / fail / not-evaluated. A NOT-EVALUATED **never excludes**; only a confirmed FAIL does. |
| `UNRATEABLE: no data — possibly delisted` | A ticker with failed fundamentals **and** no usable price history (a delisted / all-404 name). (`factors.is_unrateable`) | **Not a SELL.** No assessment was made — the name is listed separately, gets no verdict, and never reaches the narrator. "No data exists" ≠ "we assessed it and it's bad". |
| `SCREEN: no lens screen — this strategy screens nothing; …` (Company Check) | The rank strategy declares **no lens screen** (e.g. `financials_v1`, `magic_formula_raw_v1`); quality enters through ranking only. (`company_check`, `screen_less`) | **Not a data gap or a pass.** There is simply no screen to run — the name is ranked, not floored. |
| `VERDICT OF RECORD: in the latest frozen run of financials_16_v1 (run 2026-07-10): SELL, rank 12 of 16.` (Company Check) | The checked name already had a verdict (or an exclusion + reason) in the **latest frozen run** of the selected reference universe; the line quotes it **verbatim** from that run record. (`company_check`, Spec 4D) | **Not a fresh verdict.** Company Check never issues one for a single name — this is a *reported historical fact* from a past universe run, never recomputed from live data. When it renders, it replaces the closing "a verdict requires a universe run" boilerplate. |

## Narrator guardrails

The LLM layer **narrates the deterministic verdict; it never decides one.** Four disciplines
keep the prose honest, and each is enforced in code, not merely requested in a prompt:

- **Rank-semantics legend.** The narrator is handed an explicit legend — *rank 1 = best on
  every factor; lower combined rank-sum = better; N = <cohort size>; ordinal claims (best,
  worst, second-, top-, bottom-) must be derived only from the rank table.* A post-check
  (`narration_check.py`) appends the `[⚠ narration check: …]` line above when the prose
  contradicts the table, and defers to the table.
- **Quarantined headline FCF.** The vendor's headline (TTM) `free_cash_flow` can embed
  one-off cash events (NVO printed −12.04B beside a positive annual series). It is withheld
  from the narrator's evidence and annotated *"ttm_incl_one_offs — do not use for
  sustainability claims; cite `free_cash_flow_annual` instead"*, so the only citable FCF
  basis is the annual series the screens use.
- **Open questions are not findings.** Anything the narrator cannot ground in the evidence
  is confined to an `OPEN QUESTIONS (unresolved, for human review — not facts)` block — it is
  never asserted as a finding, and forward deterioration is never stated as fact.
- **Narration explains, it does not judge.** The verdict of record is the deterministic
  ranker's; the header states the division of labor plainly (*Verdict: deterministic ranker.
  Narrative: LLM (non-judging).*), and no narrator output can change screen, rank, or gate.

See **[The Calculations](CALCULATIONS.md)** for the arithmetic behind the values these marks
annotate, and **[How It Works](COUNCIL_EXPLAINER.md)** for the five-stage flow.
