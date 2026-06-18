# Aristos Council — How It Works

Aristos Council is an auditable, multi-agent equity research system. Given a ticker and a
strategy, it produces a single recommendation — BUY, HOLD, or SELL — with a confidence
score and a complete evidence trail: every number that influenced the verdict is traceable
to the exact data source it came from. The design goal is not to predict prices. It is to
apply a stated investment policy consistently, show its work, and refuse to assert anything
it cannot evidence.

This document has two parts. Part A describes how a verdict is reached. Part B describes
what is actually measured under each strategy.

---

## Part A — How the Council Decides

A run moves through six stages. The first and the last two are deterministic code, not
language models. The language models do the reasoning in the middle, but they are bounded
on both sides by mechanical checks they cannot override.

**1. The deterministic screen.** Before any model runs, a rules engine evaluates the ticker
against the active strategy's criteria (Part B). Each criterion returns one of three states:
pass, fail, or not-evaluated. The thresholds live in a versioned strategy file, not in code,
so the same ticker run against the same strategy version reproduces the same screen result.

**2. The specialist panel.** Four specialists each form an independent view: Fundamental,
Technical, Sentiment, and Risk. Each returns a stance, a confidence level, a written thesis,
and a list of cited figures. (In the current build the Sentiment specialist abstains, because
its data feed is not yet wired in; the verdict effectively runs on the other three voices
until then.) A hard rule applies to all of them: every number a specialist cites must carry
the exact reference to the tool output it was read from. Numbers without a valid reference are
discarded and flagged. Specialists are forbidden from doing arithmetic or introducing outside
knowledge; they reason only over the evidence placed before them.

**3. The Critic.** A dedicated adversarial agent challenges the panel — surfacing weak
reasoning, contestable figures, and questions the evidence cannot answer. The Critic is
held to the same citation contract as the specialists.

**4. The Decision.** A synthesis step weighs the panel and the Critic's challenge into a
single recommendation and confidence.

**5. The provenance audit.** After the verdict is proposed, every cited figure is
re-resolved against the source data and classified: verified, mismatch, unresolvable, or
non-comparable. This catches the failure mode where an agent attaches a valid source
reference to a misread value — for example, claiming a criterion "could not be evaluated"
when the record shows it was evaluated and failed. Mismatches are surfaced for review, not
silently corrected, so a wrong number never quietly disappears from the trail.

**6. The disposition gate and veto.** Two mechanical guards sit at the end.

The disposition gate is the system's central trust anchor. If a criterion designated as
*gating* is a confirmed failure, the verdict is capped at SELL — regardless of how bullish
the language models argued. This exists because of a measured failure mode: the Decision
agent could be talked into overriding a hard screen failure whenever the Critic supplied a
plausible "the data is unreliable" argument. No amount of clever rationalization can lift a
gated failure above SELL. The system records when this override fired and what the models had
originally proposed, so the disagreement between code and model is visible, not hidden.

The veto layer flags a verdict for human review on any of five triggers: confidence below
the strategy's floor (0.6), unresolved disagreement among specialists, a data-quality
problem (including provenance mismatches and unverifiable inputs), a flip from the previous
verdict on the same name, or a Decision that overrode the majority of the panel.

The output is the recommendation, the confidence, the rationale, the full figure-level
provenance, any criteria that failed or could not be evaluated, and any vetoes raised.

A note on the three-valued logic, because it matters for trust: "not-evaluated" is a
first-class state, distinct from "failed." When the underlying data is missing or
unreliable, the council abstains on that criterion rather than guessing a pass or
manufacturing a fail. A verdict built on abstentions is reported as such, not dressed up as
conviction.

---

## Part B — What the Council Measures

Two strategies are implemented. Both select their criteria by name from a shared registry
and pin their own thresholds; the strategies share the engine but not the policy. Strategies
are versioned — a published strategy is never edited in place, a new version is created
instead, so historical decisions remain reproducible.

### Dividend Aristocrats (v1)

A conservative income strategy. It targets large, financially durable companies with a long,
unbroken record of growing dividends, and it prioritizes the sustainability of the payout
over headline yield. The thesis is that a multi-decade record of rising dividends is a
hard-to-fake signal of disciplined capital allocation and earnings durability; a high yield
paired with a stretched payout ratio is treated as a warning, not an attraction.

| Criterion | Threshold | What it checks |
|---|---|---|
| Minimum dividend yield | ≥ 2.5% | A yield floor, to exclude near-zero-yield names |
| Maximum payout ratio | ≤ 75% | A sustainability ceiling; above this, the payout is questioned |
| Minimum market cap | ≥ $10B | A large-cap durability proxy |
| Minimum dividend growth streak | ≥ 25 years | The canonical aristocrat signal: consecutive years of dividend increases |

Known limitation, stated plainly: the development data provider (yfinance) supplies only a
short dividend history and cannot confirm a 25-year streak. Until a longer-history provider
is integrated, the streak criterion frequently returns not-evaluated and triggers the
data-quality veto by design. This is intended behavior — the council refuses to assert an
aristocrat streak it cannot evidence rather than pass the criterion on faith.

### Growth at a Reasonable Price (v1)

The mirror image of the income screen: durable top-line compounding bought at a sane price,
with capital efficiency as the quality gate. The discipline is refusing to overpay for
growth — revenue growth evidences the compounding, return on invested capital evidences that
the growth creates value rather than just consuming capital, and the PEG ratio keeps
valuation honest relative to the growth on offer.

| Criterion | Threshold | What it checks |
|---|---|---|
| Minimum revenue CAGR | ≥ 10% | Durable top-line growth (in-house 3-year compound rate) |
| Minimum ROIC | ≥ 12% | Capital efficiency / quality |
| Maximum PEG ratio | ≤ 2.0 | Valuation relative to growth |
| Minimum market cap | ≥ $5B | Mid/large-cap scope |

Known limitation: the three growth criteria degrade to not-evaluated rather than guessing
when the financial statements are too short or earnings are negative. Revenue CAGR needs
enough clean annual data points; ROIC needs a provided invested-capital figure; PEG needs
positive earnings and positive in-house growth. The council refuses to assert a growth thesis
it cannot evidence.

### Data sources

Fundamentals and price history come from yfinance in the current development build. Three
further sources are designed but not yet active, and the system is built around their absence
rather than pretending they are present: Finnhub for analyst and news sentiment (until it is
wired, the Sentiment specialist abstains rather than guess), a retrieval layer over SEC EDGAR
filings to further ground the Fundamental specialist, and a production-grade fundamentals and
dividend-history provider (EODHD) that would, among other things, verify the 25-year dividend
streak the development provider cannot confirm. The strategy notes above flag exactly where
these gaps affect results today.

---

*This explainer describes the system as built. The thresholds, criteria, and decision
mechanics above are read directly from the live strategy configuration and decision code.*
