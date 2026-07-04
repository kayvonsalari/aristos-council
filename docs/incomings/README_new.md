# Aristos Council

Most AI stock analysis gives you one model's snap opinion. Aristos went further: it built
the multi-agent council, ran it under controlled conditions — and demoted it. **The math
judges; the AI narrates.**

A deterministic decision core — screen, multi-factor rank, hard gates — produces the
verdict: BUY, HOLD, or SELL, or INSUFFICIENT_EVIDENCE when the data cannot support a call.
Every number traces to its source; the same inputs always produce the same verdict. A
panel of specialist LLM agents then writes the narrative around that verdict — the factor
story, the strategy fit, the open questions worth a human's attention — under a hard rule:
**the language models explain; they do not judge, and they never do arithmetic.**

That split is a measured conclusion, not a design fashion. The LLM council originally held
the verdict. Testing showed it flipped on identical inputs, and in a pre-registered
controlled experiment its "second opinion" disagreed with 100% of verdicts across three
strategies — including after its best objection (momentum) had been handled
deterministically. Its valid insights were extracted and hardened into rules (a momentum
factor; a screen-as-prefilter); what remained was noise. The narrative layer is what an
LLM demonstrably does well here, so that is the job it keeps.

Three strategies, one engine: **defensive income** (Conservative Formula: low volatility +
net payout + momentum), **classic value** (Greenblatt Magic Formula), and
**value + momentum**. Strategy logic lives in versioned YAML; the math lives in
unit-tested tools.

New here? **[How a verdict is reached](docs/COUNCIL_EXPLAINER.md)** — the plain-language
walkthrough. Want the formulas? **[The Calculations](docs/CALCULATIONS.md)** — every
factor, criterion, and guard, generated from the code.

## How a verdict is reached

1. **Screen (deterministic).** The strategy's lens screen evaluates absolute floors —
   income, coverage, balance-sheet, momentum-breakdown, quality. Three states per
   criterion: pass / fail / not-evaluated. Only a confirmed FAIL excludes; missing data
   never silently disqualifies. Names with no data at all (delisted tickers) are declared
   **UNRATEABLE** and receive no verdict.
2. **Rank (deterministic).** Survivors are ranked per factor across the universe
   (1 = best), ranks are summed, lowest combined rank wins — Greenblatt's mechanic; no
   tuned weights exist anywhere. A quintile cut assigns BUY / HOLD / SELL.
3. **Gates (deterministic).** A confirmed gating-criterion failure caps the verdict at
   SELL no matter what any narrative says; a not-evaluated gating criterion yields
   INSUFFICIENT_EVIDENCE and unconditional human review. Gate firings are recorded.
4. **Narrative (LLM, non-judging).** Specialists — Fundamental, Technical, Sentiment,
   Risk — write the evidence-bound story of the verdict. Every figure they cite must
   carry provenance to the exact tool call that produced it; a post-run audit re-resolves
   every citation and flags mismatches. The narrator is barred from reinterpreting
   accounting and from asserting forward deterioration as fact — anything beyond the
   evidence is phrased as an open question. (An optional `second_opinion` mode lets the
   council issue its own verdict for comparison; it exists behind a flag as the
   experimental instrument that produced the demotion evidence.)
5. **The human holds the veto.** Contested runs — low confidence, material data-quality
   gaps, verdict flips, gate overrides — are escalated for review. The system's job is to
   surface candidates and show its work, not to replace judgment.

## Why this design

1. **Deterministic verdicts are the only auditable verdicts.** An LLM asked to compress
   ambiguous evidence into a discrete call flips on borderline names — measured, not
   assumed. A rank-sum over unit-tested factors is reproducible, inspectable, and
   explains itself: every verdict decomposes into named factor ranks.
2. **One definition per strategy.** The screen says who qualifies; the ranking orders
   survivors. Rank-relative factors cannot enforce absolute floors, so the screen runs as
   a prefilter — the gap where a name ranks well while failing the strategy's own quality
   floor is closed in code.
3. **Honesty over coverage.** Missing data abstains rather than guesses; abstention never
   excludes; a name without data gets no verdict at all. INSUFFICIENT_EVIDENCE is a
   first-class outcome.

## Architecture

- **Decision core:** `rank_engine.py` (rank-sum + verdict cuts) + `factors.py` (factor
  registry) + `tools/` (all arithmetic; pure, unit-tested) + screens in versioned YAML.
- **Orchestration:** LangGraph; `ResearchState` threaded through every node; LLMs behind
  a `Runner` seam (tiered models via `init_chat_model`), so the graph tests end-to-end
  with fakes — no API keys in CI.
- **Data behind adapters:** provider-agnostic `MarketDataAdapter`
  (`yfinance` | `eodhd` | `hybrid` via `ARISTOS_MARKET_PROVIDER`); Finnhub behind a
  `SentimentAdapter`; per-adapter unit normalization with sanity guards.
- **Persistence & audit:** append-only verdict history, full per-run reports, deep
  provenance audit resolving every cited figure against the tool-call ledger.
- **Council Station:** local Streamlit UI — run, read the deliberation, browse history,
  edit strategies (edit-as-new-version; published files are never mutated).

[Project structure, stack table, and run instructions unchanged below — CC: keep the
existing sections, but update Project status with the v2 phase and current test count
from pytest, and replace the "note on honesty" with a pointer to CALCULATIONS.md §6.]
