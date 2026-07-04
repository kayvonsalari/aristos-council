# Aristos Council — How It Works

Aristos Council is an auditable, multi-agent equity research system. Given a ticker and a
strategy, it produces a single recommendation — BUY, HOLD, or SELL, or else
INSUFFICIENT_EVIDENCE (an off-the-ladder state meaning the screen cannot render a verdict,
distinct from any directional call) — with a confidence score and a complete evidence trail:
every number that influenced the verdict is traceable to the exact data source it came from. The design goal is not to predict prices. It is to
apply a stated investment policy consistently, show its work, and refuse to assert anything
it cannot evidence.

This document has three parts. Part A describes how a verdict is reached. Part B points to
[The Calculations](CALCULATIONS.md) — every factor, criterion, and threshold, generated
from the code. Part C describes what the system mechanically guarantees, and how those
guarantees are tested.

---

## Part A — How a Verdict Is Reached

A run moves through five stages. The first three — the ones that decide — are
deterministic code. The language models enter only afterwards, to explain.

**1. The screen.** A rules engine evaluates the ticker against the strategy's absolute
criteria (thresholds live in versioned YAML). Each criterion returns pass, fail, or
not-evaluated. Only a confirmed FAIL excludes; missing data abstains and never silently
disqualifies. A ticker with no usable data at all — a delisted name — is declared
UNRATEABLE: listed with its reason, given no verdict, sent to no model.

**2. The rank.** Names that pass are ranked on the strategy's factors across the whole
universe — rank 1 is best, ties average — and the per-factor ranks are summed. Lowest
combined rank wins. This is the Magic-Formula mechanic: there are no tuned weights to
guess, the ranking is the decision, and every verdict decomposes into named, inspectable
factor ranks. A quintile cut (or top-k for small universes) maps positions to
BUY / HOLD / SELL. The formulas behind every factor are in [The Calculations](CALCULATIONS.md).

**3. The gates.** Two mechanical guards no model can argue past. A confirmed failure on a
*gating* criterion caps the verdict at SELL — the system records when this fired and what
was originally proposed, so code-versus-model disagreement stays visible. A gating
criterion that could not be evaluated yields INSUFFICIENT_EVIDENCE: off the
buy/hold/sell ladder entirely, an unconditional pause for human review rather than a
guessed direction.

<img src="decision_logic.png" alt="How the four verdicts (buy/hold/sell/insufficient-evidence) are decided" width="760">

**4. The narrative.** Only now do the language models run. Four specialists —
Fundamental, Technical, Sentiment, Risk — write the evidence-bound story of the verdict:
why the name ranked where it did, how it fits the strategy, what a human should check.
Hard rules bound them on every side: every cited figure must carry provenance to the
exact tool output it was read from (a post-run audit re-resolves every citation and
surfaces mismatches rather than correcting them silently); no arithmetic; no outside
knowledge; no reinterpretation of accounting; nothing beyond the evidence asserted as
fact — open questions are phrased as open questions.

Why don't the models judge? Because we measured what happened when they did. The council
originally held the verdict and flipped on identical inputs. Moved to a second-opinion
role, it disagreed with 100% of verdicts in a pre-registered controlled experiment across
three strategies — and when its best objection (momentum) was handled deterministically,
it began objecting to momentum itself. Its valid insights were hardened into rules: the
momentum objections became a momentum factor; its one legitimate catch (a name ranking
well while failing the strategy's own quality floor) became the screen-as-prefilter. The
`second_opinion` mode remains behind a flag as the experimental instrument; the verdict
header states the division of labor plainly: *Verdict: deterministic ranker. Narrative:
LLM (non-judging).*

**5. The veto.** Contested runs escalate to a human: low confidence, material
data-quality gaps, a verdict flip from the prior run on the same name, a gate override of
a confident call, INSUFFICIENT_EVIDENCE always. Benign noise — a single abstention, one
non-gating not-evaluated criterion — is recorded but does not escalate on its own. The
human holds the veto; the system surfaces candidates and shows its work.

---

## Part B — What the Council Measures

The per-strategy detail — every factor, criterion, threshold, and guard — lives in one
generated-from-code reference: **[The Calculations](CALCULATIONS.md)**. Its §2 gives the
factors and their formulas, §3 the dividend-streak counting method (why a *flat* year is
not a cut), §4 the screen criteria with current thresholds, and §5 the guards. Where this
document and the code ever disagree, the code — and The Calculations, which is generated
from it — win.

Three rank strategies ship on one engine, each pinning its own factors and floors in a
versioned YAML (a published strategy is never edited in place — a new version is created
instead): **Conservative Formula** (defensive income — low volatility + net payout +
12-month momentum, screened for covered income, real yield, leverage, and a
momentum-breakdown floor), **Greenblatt Magic Formula** (classic value — earnings yield +
return on capital, financials excluded), and **value + momentum** (the Magic Formula with a
momentum factor added to demote falling knives). Each pairs with an absolute-floor lens
screen run as a prefilter — the screen says who qualifies, the ranking orders survivors.
The dividend-aristocrat and growth (GARP) screens remain available as standalone strategies.
All thresholds are in [The Calculations §4](CALCULATIONS.md#4-screen-criteria-three-state-abstention-never-excludes).

### Data sources

The market-data provider is selectable at runtime via `ARISTOS_MARKET_PROVIDER`: **yfinance**
(default), **EODHD**, or a **hybrid** that sources dividend history from EODHD and
fundamentals/prices from yfinance. EODHD is live for dividend history — its deep, clean record
is what makes the 20-year streak verifiable across US and EU names; its fundamentals endpoint
requires EODHD's paid tier, which is exactly why the hybrid exists. Sentiment comes from
**Finnhub** (analyst recommendation trends + company news); without a `FINNHUB_API_KEY` the
Sentiment specialist abstains rather than guess. One source remains designed-but-not-yet-active
— a retrieval layer over **SEC EDGAR** filings to further ground the Fundamental specialist.
The strategy notes above flag exactly where these gaps affect results today.

### What you can change in the app

Council Station exposes two ways to change how a run behaves, and the difference matters:

- **Run overrides — this run only** (sidebar). Ephemeral toggles applied to a single run; the
strategy file is never touched, the change is stamped "overrides this run" on the report, and the run
is excluded from flip-comparison baselines. Two controls: whether a partial screen pass can still be
a HOLD (the partial-pass policy), and whether the deterministic gate is on or off for a given
criterion (per criterion) — the experiment knob.
- **Edit as a new version** (Strategy tab). A deliberate, persisted change that creates a *new*
strategy version rather than mutating a published one: the partial-pass policy and the human-veto
confidence floor, saved under a new version id.

In short: a run override is "try a setting for this run, nothing saved"; editing as a new version is
"change the strategy going forward." Threshold values themselves are changed through the new-version
flow, not as casual per-run sidebar sliders — only the gate toggles and the partial-pass policy are
per-run.

---

## Part C — What's mechanically guaranteed (and how it's tested)

If you are deciding whether to trust this system, the honest answer is that the trust does not
come from the language models. It comes from the deterministic code that surrounds them and from
an automated test suite — over 550 tests at last count — that runs the entire pipeline on every
change with fake models and fake data, no API keys and no network. Each guarantee below is
enforced by that code and re-checked by those tests; none of it depends on a model behaving well
on the day.

**No model makes the call.** The verdict is a pure function of adapter data through the screen,
rank, and gates; no language model output can change it. The LLM layer can only annotate.

**Every figure is traceable.** No language model does arithmetic. Each number is produced by a
pure, deterministic tool and is audited after the fact back to the exact source call it came
from. A number that cannot be resolved to its origin is treated as a hard failure that surfaces
for review, not a footnote that quietly survives.

**The risk discipline cannot be talked around.** The rule that caps the verdict on a confirmed
screen failure is deterministic code, not a prompt: a confirmed failure of a gating criterion
holds the verdict at SELL no matter how bullishly the model argued. This is deliberate —
prompt-level control over the same rule was tried first and proved evadable, so enforcement was
moved into code the model cannot override.

**The system refuses to fake a verdict.** When a gating criterion genuinely cannot be evaluated
— for example, a dividend history too short to confirm the growth streak — the result is
INSUFFICIENT_EVIDENCE, a verdict off the buy/hold/sell ladder that forces unconditional human
review, rather than a false HOLD presented as a real call.

**Human review fires on the things that matter.** Seven deterministic triggers can pause a run
for a human, and the data-quality trigger is severity-aware: a real fetch failure or a screen
that is mostly blind escalates, while a single optional-source gap (such as a missing sentiment
feed) is recorded but does not, on its own, raise the flag — so the flag keeps its meaning
instead of firing on nearly every run.

**Data provenance is honest about its source.** Dividends, fundamentals, and prices are each
attributed to the provider that actually produced them — including the hybrid configuration,
where dividends come from one provider and fundamentals and prices from another — never
collapsed into a single undifferentiated label. The dividend-growth streak is computed by the
counting method matched to each provider's data shape.

**Growth metrics resist cyclical distortion.** Revenue growth is measured as a base-year-robust
trend rather than a naive two-point comparison, and it flags when the two diverge — the warning
sign of a cyclical base year. Return on capital is measured through the cycle (a multi-year
average) rather than off a single peak, and the valuation-to-growth ratio caps an extreme growth
input so a trough-inflated number cannot make a stock look cheap.

**The whole pipeline runs end-to-end in continuous integration**, with fakes standing in for the
models and the data providers, so none of the guarantees above depend on a live key or a network
call — they are re-checked automatically on every change. A dedicated deterministic eval suite
freezes these core guarantees — the disposition gate, the INSUFFICIENT_EVIDENCE short-circuit, and
the human-veto triggers — as fast assertions, so a change that broke one would turn the suite red
before it shipped.

These tests verify the machinery — that figures trace to source, that the gate holds, that the
system abstains rather than guesses. They are a foundation for trust in the process, not a
guarantee that any single verdict is right. Treat its output as rigorous input to your own
judgment.

---

*This explainer describes the system as built. The thresholds, criteria, and decision
mechanics above are read directly from the live strategy configuration and decision code.*
