ARISTOS — DIAGRAM UPDATE to v2 (the last stale artifacts in the repo; docs text is already v2).
Both current diagrams depict the demoted architecture (LLM Decision agent issues the verdict).
Reviewers screenshot diagrams first — these contradict the README they sit next to.

=== ITEM 1: replace docs/council_diagram.png -> aristos_architecture.png (two-lane v2) ===
Layout: TWO LANES with a hard visual boundary; the boundary IS the pitch.
LANE 1 — "DECIDES (deterministic)":
  input (universe + rank strategy) -> SCREEN (absolute floors; exits: Excluded w/ named
  reason; UNRATEABLE for no-data names) -> RANK ENGINE (per-factor ranks 1..N -> rank-sum
  -> quintile/top-k cut) -> GATES (confirmed-fail gating criterion -> capped SELL;
  not-evaluated gating criterion -> INSUFFICIENT_EVIDENCE) -> VERDICT-OF-RECORD.
LANE 2 — "EXPLAINS (LLM, non-judging)":
  verdict + evidence -> specialists (Fundamental/Technical/Sentiment/Risk) + Critic,
  provenance-bound, no arithmetic, no accounting reinterpretation -> NARRATIVE + open
  questions ("worth checking") -> provenance audit -> HUMAN VETO (review triggers).
Boundary annotation (verbatim): "Verdict: deterministic ranker. Narrative: LLM (non-judging)."
Dashed optional path from lane 2: "second_opinion mode (experimental flag) — council issues
its own verdict for comparison; demoted from default after a pre-registered experiment."
Keep the existing color language (blue=mechanical, green=LLM, orange=human) — it already
encodes the right idea; the v1 diagram's error was WHERE the verdict box sat, not the palette.

=== ITEM 2: fix docs/decision_logic.png (gate logic is still correct; the SOURCE box is wrong) ===
- Top box "Provisional verdict — the LLM weighs panel vs critic -> buy/hold/sell + confidence"
  -> "Ranker verdict — screen -> per-factor ranks -> rank-sum -> quintile cut".
- Subtitle "...over the LLM's provisional verdict..." -> "...over the ranker's verdict...".
- Every "LLM verdict stands" box -> "Ranker verdict stands".
- Gate branches UNCHANGED (confirmed-fail -> SELL cap; not-evaluated gating -> INSUFFICIENT_
  EVIDENCE; precedence note stays). Footnote to add: "Names with no data at all never reach
  this logic — they exit earlier as UNRATEABLE."
- Drop the "(e.g. growth today)" aside unless still true of current strategy configs.

=== ITEM 3: housekeeping ===
- Delete docs/council_flow.png and docs/council_flow.svg (unreferenced, v1).
- README "How a verdict is reached": embed the new architecture diagram at the top of the
  section. Explainer keeps decision_logic.png where it is.
- Keep source SVGs alongside PNGs (the repo convention) so the next edit isn't a redraw.
Commit "Diagrams to v2: two-lane architecture (math decides / LLM explains); decision-logic
source box corrected to ranker; v1 flow diagrams removed".
