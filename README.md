# Aristos Council

A multi-agent financial research analyst. Specialist agents deliberate over a single security, a dedicated Critic argues the opposite case before any verdict is reached, and a Decision agent issues a **buy / hold / sell** call with an explicit confidence score and noted dissent. A human holds the veto.

The name nods to the [Dividend Aristocrats](https://en.wikipedia.org/wiki/S%26P_500_Dividend_Aristocrats) — and to the idea that a recommendation should have to survive a council, not just one model's first instinct.

Two strategies run on the same engine today: an income screen (**dividend aristocrats**) and a growth-at-a-reasonable-price screen (**growth**). Each pins its own criteria and thresholds in versioned config; the council, the Critic, and the human veto are shared.


## How it works

<p align="center">

<svg width="900" viewBox="0 0 980 1400" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif">
  <defs>
    <marker id="arrow" markerWidth="10" markerHeight="10" refX="6" refY="3" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L6,3 L0,6 Z" fill="#8590a3"/>
    </marker>
  </defs>

  <rect x="0" y="0" width="980" height="1400" fill="#ffffff"/>

  <!-- Title -->
  <text x="44" y="50" font-size="27" font-weight="700" fill="#1e2a45">Aristos Council — how a verdict is reached</text>
  <text x="44" y="80" font-size="15" fill="#5b6478">AI does the reasoning; mechanical rules set the floor and hold the final controls.</text>

  <!-- Left group labels -->
  <text x="30" y="290" font-size="11" font-weight="700" fill="#9aa3b2" letter-spacing="1.5" transform="rotate(-90 30 290)">FLOOR</text>
  <text x="30" y="560" font-size="11" font-weight="700" fill="#9aa3b2" letter-spacing="1.5" transform="rotate(-90 30 560)">AI DELIBERATION</text>
  <text x="30" y="975" font-size="11" font-weight="700" fill="#9aa3b2" letter-spacing="1.5" transform="rotate(-90 30 975)">CONTROLS</text>

  <!-- 1. INPUT -->
  <rect x="190" y="130" width="600" height="80" rx="12" fill="#f6ecd2" stroke="#d8be86" stroke-width="1.5"/>
  <text x="210" y="160" font-size="17" font-weight="700" fill="#1e2a45">Input</text>
  <text x="210" y="182" font-size="13" fill="#5b6478">A ticker and a chosen strategy. The previous verdict is included too, so a change of</text>
  <text x="210" y="200" font-size="13" fill="#5b6478">mind on the same name can be flagged.</text>
  <line x1="490" y1="210" x2="490" y2="240" stroke="#8590a3" stroke-width="1.5" marker-end="url(#arrow)"/>

  <!-- 2. THE SCREEN -->
  <rect x="190" y="240" width="600" height="92" rx="12" fill="#e9eef7" stroke="#6982ad" stroke-width="1.5"/>
  <text x="210" y="270" font-size="17" font-weight="700" fill="#1e2a45">The screen <tspan font-size="12" font-weight="400" fill="#6982ad">· mechanical rules</tspan></text>
  <text x="210" y="292" font-size="13" fill="#5b6478">The company is tested against the strategy's criteria; each one passes, fails, or is</text>
  <text x="210" y="310" font-size="13" fill="#5b6478">marked not-evaluated when data is missing. No AI — the result is fully repeatable.</text>
  <line x1="490" y1="332" x2="490" y2="362" stroke="#8590a3" stroke-width="1.5" marker-end="url(#arrow)"/>

  <!-- 3. THE PANEL -->
  <rect x="190" y="362" width="600" height="140" rx="12" fill="#f1f1f4" stroke="#9a9bab" stroke-width="1.5"/>
  <text x="210" y="388" font-size="17" font-weight="700" fill="#1e2a45">The panel <tspan font-size="12" font-weight="400" fill="#8a8a98">· four independent analysts</tspan></text>
  <text x="210" y="410" font-size="13" fill="#5b6478">Four separate views over the same evidence. Every figure must be traced back to its</text>
  <text x="210" y="428" font-size="13" fill="#5b6478">source, and no analyst may do arithmetic or bring in outside facts.</text>
  <rect x="210" y="446" width="132" height="28" rx="14" fill="#ffffff" stroke="#b9bcc8"/>
  <text x="276" y="464" font-size="12" fill="#1e2a45" text-anchor="middle">Fundamental</text>
  <rect x="350" y="446" width="132" height="28" rx="14" fill="#ffffff" stroke="#b9bcc8"/>
  <text x="416" y="464" font-size="12" fill="#1e2a45" text-anchor="middle">Technical</text>
  <rect x="490" y="446" width="132" height="28" rx="14" fill="#ffffff" stroke="#b9bcc8"/>
  <text x="556" y="464" font-size="12" fill="#1e2a45" text-anchor="middle">Sentiment*</text>
  <rect x="630" y="446" width="132" height="28" rx="14" fill="#ffffff" stroke="#b9bcc8"/>
  <text x="696" y="464" font-size="12" fill="#1e2a45" text-anchor="middle">Risk</text>
  <text x="210" y="492" font-size="11" fill="#8a8a98">*Sentiment currently abstains — its data feed is not yet connected.</text>
  <line x1="490" y1="502" x2="490" y2="532" stroke="#8590a3" stroke-width="1.5" marker-end="url(#arrow)"/>

  <!-- 4. THE CRITIC -->
  <rect x="190" y="532" width="600" height="92" rx="12" fill="#f7ebeb" stroke="#c1908f" stroke-width="1.5"/>
  <text x="210" y="562" font-size="17" font-weight="700" fill="#1e2a45">The critic <tspan font-size="12" font-weight="400" fill="#b07f7e">· the built-in skeptic</tspan></text>
  <text x="210" y="584" font-size="13" fill="#5b6478">A dedicated agent argues the other side of the emerging view. Concerns it cannot</text>
  <text x="210" y="602" font-size="13" fill="#5b6478">prove become open questions for a human — never asserted as facts.</text>
  <line x1="490" y1="624" x2="490" y2="654" stroke="#8590a3" stroke-width="1.5" marker-end="url(#arrow)"/>

  <!-- 5. THE DECISION -->
  <rect x="190" y="654" width="600" height="92" rx="12" fill="#f1f1f4" stroke="#9a9bab" stroke-width="1.5"/>
  <text x="210" y="684" font-size="17" font-weight="700" fill="#1e2a45">The decision <tspan font-size="12" font-weight="400" fill="#8a8a98">· buy / hold / sell + confidence</tspan></text>
  <text x="210" y="706" font-size="13" fill="#5b6478">A recommendation with a confidence score, weighing the panel against the critic.</text>
  <text x="210" y="724" font-size="13" fill="#5b6478">Any dissent is recorded by name, not dropped.</text>
  <line x1="490" y1="746" x2="490" y2="776" stroke="#8590a3" stroke-width="1.5" marker-end="url(#arrow)"/>

  <!-- 6. THE GATE (highlighted) -->
  <rect x="190" y="776" width="600" height="104" rx="12" fill="#fbefd6" stroke="#cf9f44" stroke-width="2.5"/>
  <rect x="612" y="764" width="166" height="24" rx="12" fill="#cf9f44"/>
  <text x="695" y="780" font-size="11" font-weight="700" fill="#ffffff" text-anchor="middle">CANNOT BE OVERRIDDEN</text>
  <text x="210" y="806" font-size="17" font-weight="700" fill="#1e2a45">The gate <tspan font-size="12" font-weight="400" fill="#b07f1e">· the safety control</tspan></text>
  <text x="210" y="828" font-size="13" fill="#5b6478">If a make-or-break criterion is a confirmed fail, the verdict is capped at SELL —</text>
  <text x="210" y="846" font-size="13" fill="#5b6478">however positive the analysis argued. The system cannot be talked out of a hard fail.</text>
  <text x="210" y="868" font-size="12.5" font-style="italic" fill="#9a7a2e">This is what makes a clever rationalisation unable to rescue a genuinely failing name.</text>
  <line x1="490" y1="880" x2="490" y2="910" stroke="#8590a3" stroke-width="1.5" marker-end="url(#arrow)"/>

  <!-- 7. THE AUDIT -->
  <rect x="190" y="910" width="600" height="92" rx="12" fill="#e9eef7" stroke="#6982ad" stroke-width="1.5"/>
  <text x="210" y="940" font-size="17" font-weight="700" fill="#1e2a45">The audit <tspan font-size="12" font-weight="400" fill="#6982ad">· mechanical check</tspan></text>
  <text x="210" y="962" font-size="13" fill="#5b6478">Every number behind the verdict is re-checked against the source data. A misread</text>
  <text x="210" y="980" font-size="13" fill="#5b6478">figure is flagged for review, never silently corrected.</text>
  <line x1="490" y1="1002" x2="490" y2="1032" stroke="#8590a3" stroke-width="1.5" marker-end="url(#arrow)"/>

  <!-- 8. THE VETO -->
  <rect x="190" y="1032" width="600" height="130" rx="12" fill="#e9eef7" stroke="#6982ad" stroke-width="1.5"/>
  <text x="210" y="1058" font-size="17" font-weight="700" fill="#1e2a45">The veto <tspan font-size="12" font-weight="400" fill="#6982ad">· five mechanical checks</tspan></text>
  <text x="210" y="1080" font-size="13" fill="#5b6478">These decide whether a person must review the verdict before it stands. Any single</text>
  <text x="210" y="1098" font-size="13" fill="#5b6478">one firing is enough to pause for a human.</text>
  <rect x="210" y="1118" width="104" height="30" rx="15" fill="#ffffff" stroke="#8ea0bd"/>
  <text x="262" y="1137" font-size="11" fill="#1e2a45" text-anchor="middle">Low confidence</text>
  <rect x="322" y="1118" width="116" height="30" rx="15" fill="#ffffff" stroke="#8ea0bd"/>
  <text x="380" y="1137" font-size="11" fill="#1e2a45" text-anchor="middle">Analyst conflict</text>
  <rect x="446" y="1118" width="100" height="30" rx="15" fill="#ffffff" stroke="#8ea0bd"/>
  <text x="496" y="1137" font-size="11" fill="#1e2a45" text-anchor="middle">Data quality</text>
  <rect x="554" y="1118" width="96" height="30" rx="15" fill="#ffffff" stroke="#8ea0bd"/>
  <text x="602" y="1137" font-size="11" fill="#1e2a45" text-anchor="middle">Verdict flip</text>
  <rect x="658" y="1118" width="116" height="30" rx="15" fill="#ffffff" stroke="#8ea0bd"/>
  <text x="716" y="1137" font-size="11" fill="#1e2a45" text-anchor="middle">Majority override</text>

  <!-- Branch to outcomes -->
  <path d="M490,1162 L490,1178 L335,1178 L335,1192" fill="none" stroke="#8590a3" stroke-width="1.5" marker-end="url(#arrow)"/>
  <path d="M490,1178 L645,1178 L645,1192" fill="none" stroke="#8590a3" stroke-width="1.5" marker-end="url(#arrow)"/>

  <!-- 9a. HUMAN REVIEW -->
  <rect x="190" y="1192" width="290" height="92" rx="12" fill="#f7e3bd" stroke="#d2a64f" stroke-width="1.5"/>
  <text x="210" y="1220" font-size="16" font-weight="700" fill="#1e2a45">Human review</text>
  <text x="210" y="1242" font-size="13" fill="#5b6478">A check fired. A person approves,</text>
  <text x="210" y="1260" font-size="13" fill="#5b6478">overrides, or investigates.</text>

  <!-- 9b. AUTO-PROCEED -->
  <rect x="500" y="1192" width="290" height="92" rx="12" fill="#e7f0e8" stroke="#7da87d" stroke-width="1.5"/>
  <text x="520" y="1220" font-size="16" font-weight="700" fill="#1e2a45">Auto-proceed</text>
  <text x="520" y="1242" font-size="13" fill="#5b6478">No check fired. The verdict stands,</text>
  <text x="520" y="1260" font-size="13" fill="#5b6478">with its full audit trail attached.</text>

  <!-- Legend -->
  <rect x="190" y="1322" width="16" height="16" rx="4" fill="#e9eef7" stroke="#6982ad"/>
  <text x="214" y="1335" font-size="12" fill="#5b6478">Mechanical rules (no AI)</text>
  <rect x="392" y="1322" width="16" height="16" rx="4" fill="#f1f1f4" stroke="#9a9bab"/>
  <text x="416" y="1335" font-size="12" fill="#5b6478">AI analysis</text>
  <rect x="520" y="1322" width="16" height="16" rx="4" fill="#fbefd6" stroke="#cf9f44" stroke-width="2"/>
  <text x="544" y="1335" font-size="12" fill="#5b6478">The control that cannot be overridden</text>
</svg>


</p>

For how a verdict is reached and what each strategy screens on, read the full [Council Explainer](docs/COUNCIL_EXPLAINER.md).

---

## Why this design

Most "AI stock analyst" demos are a single prompt that emits a confident answer with no audit trail. This project is built around the opposite premise: **a recommendation is only as trustworthy as the disagreement it survived and the numbers it can trace.**

Three principles drive the architecture:

1. **Adversarial by construction.** A Critic agent is required to argue against the emerging consensus before the Decision agent rules. Dissent is recorded in the output, never smoothed over.
2. **Deterministic math, always.** No LLM does arithmetic. Every figure is produced by a pure, unit-tested tool and carries provenance back to the exact tool call that created it. A number that can't be traced is a hard failure.
3. **Humans hold the veto.** The pipeline pauses for human review whenever confidence is low, specialists conflict, data quality is questionable, the recommendation flips from a prior run, or the Decision overrides the majority of the panel.

## Architecture

- **Orchestration:** LangGraph, with `ResearchState` threaded through every node.
- **Strategy as config:** the investment thesis lives in versioned YAML — not in code. Changing strategy means adding a new versioned file, so past decisions stay reproducible. Two strategies are live: **dividend aristocrats** (income) and **growth** (growth at a reasonable price).
- **Data behind an adapter:** every tool talks to a provider-agnostic `MarketDataAdapter`, never a vendor SDK. Develops on yfinance; swaps to EODHD with a one-line change.
- **Observability:** LangSmith tracing; tiered models via `init_chat_model`.

## Project structure

```
aristos-council/
├── app.py                        # Council Station — local Streamlit UI (Sprint 3)
├── src/aristos_council/
│   ├── state.py                  # ResearchState + Figure/Provenance/veto types — the schema contract
│   ├── graph.py                  # LangGraph wiring: gather → specialists → critic → decision → audit → veto
│   ├── agents/                   # the deliberators (LLM-backed, behind a Runner seam)
│   │   ├── nodes.py              # gather + specialist/critic/decision nodes, prompts, figure validation
│   │   ├── runners.py            # model seam: tiered Runner protocol + LangChain impl
│   │   ├── schemas.py            # structured-output schemas (tolerant parsing)
│   │   └── veto.py               # deterministic five-trigger human-veto gate
│   ├── audit/                    # deep provenance audit (Sprint 1)
│   │   └── provenance.py         # resolve every cited figure's field_path against the ledger
│   ├── data/                     # provider-agnostic market & sentiment data
│   │   ├── adapter.py            # MarketDataAdapter interface + DTOs + DataUnavailable
│   │   ├── yfinance_adapter.py   # dev market-data provider
│   │   ├── eodhd_adapter.py      # planned market-data provider (stub)
│   │   ├── sentiment.py          # SentimentAdapter interface + DTOs
│   │   └── finnhub_adapter.py    # sentiment provider (news + analyst trends)
│   ├── persistence/              # IO-at-the-edge sinks (Sprint 2–3)
│   │   ├── verdicts.py           # append-only verdict log feeding the vetoes (Sprint 2)
│   │   └── reports.py            # full per-run deliberation for the UI (Sprint 3)
│   ├── strategy/                 # strategy config
│   │   ├── loader.py             # validated strategy YAML loader
│   │   └── versioning.py         # edit-as-new-version; never mutates published files (Sprint 3)
│   └── tools/                    # deterministic tools — ALL arithmetic lives here
│       ├── screening.py          # dividend-aristocrat screen math
│       ├── technical.py          # price / technical snapshot
│       └── sentiment_tools.py    # sentiment aggregation
├── strategies/                   # versioned strategy YAMLs (dividend_aristocrats_v1/v2, growth_v1)
├── verdicts/                     # committed run data — append-only verdict history per ticker
├── reports/                      # committed run data — full per-run reports (<TICKER>/<run_at>.json)
├── assets/                       # brand mark (SVG logo)
├── .streamlit/                   # Council Station theme (config.toml)
├── examples/run_council.py       # CLI entrypoint (single council run)
├── tests/                        # pytest suite
└── CLAUDE.md                     # working agreement + sprint log for contributors
```

Run artifacts under `verdicts/` and `reports/` are checked in as project data: the
verdict history feeds the recommendation-flip veto, and the reports back Council
Station's past-run browsing.

## Stack

| Concern | Choice |
|---|---|
| Orchestration | LangGraph |
| Market data (dev) | yfinance, behind a provider-agnostic adapter |
| Market data (prod) | EODHD *(planned)* |
| Sentiment | Finnhub (free tier) — company news + analyst recommendation trends, behind a provider-agnostic `SentimentAdapter` |
| Filings | SEC EDGAR → RAG *(planned)* |
| Vector store | ChromaDB *(planned)* |
| LLM routing | `init_chat_model` (tiered) |
| Monitoring | LangSmith |
| Tests / CI | pytest + GitHub Actions |

## Project status

**Phase 1 — data substrate (complete):** `ResearchState` schema with figure-level provenance, provider-agnostic adapter (yfinance + EODHD stub), deterministic screening tools, versioned strategy config + validating loader.

**Phase 2 — the council (complete):** full LangGraph pipeline — deterministic `gather` node (the only node that touches data or math), four specialists with enforced figure provenance, a provenance-bound Critic arguing the opposite case (unverifiable quantitative concerns become open questions for a human, never asserted facts), Decision agent with recorded dissent, and a fully deterministic five-trigger human-veto gate. LLMs sit behind a `Runner` seam with env-configurable model tiers, so the entire graph is tested end-to-end with fakes — no API keys in CI.

**Phase 3 — sentiment (complete):** Finnhub news + analyst recommendation trends behind a provider-agnostic `SentimentAdapter`, aggregated by a deterministic `sentiment_snapshot` tool. Without a `FINNHUB_API_KEY` the Sentiment specialist abstains exactly as before; a provider outage degrades to a data-quality veto flag, never a crash.

**Phase 4 — audit, persistence & Council Station (current, Sprint 3):** a deep post-run **provenance audit** that resolves every cited figure's `field_path` against the tool-call ledger and feeds the data-quality veto; an append-only **verdict history** (`verdicts/`) powering the recommendation-flip and majority-override vetoes; full per-run **reports** (`reports/`); **strategy versioning** (edit-as-new-version, never mutating a published file); and **Council Station** — a local Streamlit UI to run the council, read the full deliberation, browse past runs across tickers, chart verdict/confidence history, and edit strategies. See `CLAUDE.md` for the sprint log.

**285 unit tests**, green on Python 3.11+, run end-to-end with fakes — no API keys in CI. Try it live: **Council Station** via `pip install -e ".[ui,yfinance,llm]"` then `streamlit run app.py`, or a single run with `python examples/run_council.py JNJ` (both need an Anthropic API key for live runs).

**Next:** SEC EDGAR filings RAG for the Fundamental specialist, EODHD adapter (fixes the dividend-streak-floor undercount), LangSmith tracing, nightly watchlist runs via GitHub Actions cron.

## A note on honesty

The yfinance development provider cannot verify the canonical 25-year dividend-growth streak — its history is too short. The screen does **not** paper over this: the streak criterion returns *unverifiable* (distinct from pass or fail) and trips the data-quality veto by design. Confirming that streak is one of the concrete reasons EODHD is the planned upgrade.

## Running

Run the tests:

```bash
pip install -e ".[dev]"
pytest
```

Launch **Council Station** (the local Streamlit UI):

```bash
pip install -e ".[ui,yfinance,llm]"
streamlit run app.py
```

Browsing saved runs needs only `.[ui]`; launching a council from the UI bills API credits and additionally needs the runtime extras above plus `ANTHROPIC_API_KEY` (and optionally `FINNHUB_API_KEY`) in the environment or a local `.env`.

Or run a single council from the CLI:

```bash
python examples/run_council.py JNJ
```

---

*Portfolio project by Kayvon Salari.*
