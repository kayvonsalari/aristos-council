# COUNCIL_EXPLAINER.md — Part A replacement (CC: apply)

INSTRUCTIONS: Replace Part A ("How the Council Decides") with the text below. Move the
existing Part B (per-strategy criteria detail) into docs/CALCULATIONS.md §4 (provided
separately — merge, don't duplicate) and replace Part B in this file with two paragraphs
pointing there. Part C (guarantees + tests) stays, with one edit noted at the end.

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

PART C EDIT: In the guarantees list, replace any statement that "the Decision agent makes
the call" with: "the verdict is a pure function of adapter data through the screen, rank,
and gates; no language model output can change it. The LLM layer can only annotate."
