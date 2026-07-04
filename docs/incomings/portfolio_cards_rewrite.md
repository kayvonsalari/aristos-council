# Portfolio project cards — rewrite (kayvonsalari.github.io)

Diagnosis of the current cards: each is a single dense paragraph that leads with
mechanism (agent lists, framework names) instead of the problem, and buries the most
distinctive fact in the middle. The fix applied below is one consistent shape:
**problem → what the system does → the one thing that makes it different → honest
scope.** Two to four sentences each. Tech tags stay as they are on the site.

---

## Aristos Council — Multi-Agent Equity Research  [REWRITE — also factually stale]
Can an AI's stock recommendation be trusted to stay the same tomorrow? Aristos answers by
splitting the job: a deterministic decision core — screen, multi-factor rank, hard gates —
issues the Buy/Hold/Sell verdict (same inputs, same answer, every figure traced to
source), while a panel of specialist LLM agents writes the narrative around it, barred
from judging and from doing arithmetic. That split is the project's finding, not its
premise: the original LLM council was demoted after a pre-registered controlled experiment
showed its verdicts flipped on identical inputs and its dissent was pick-independent —
its valid insights were hardened into deterministic rules instead. Degrades honestly:
missing data abstains, delisted names get no verdict, and when a gating criterion can't
be evaluated the answer is INSUFFICIENT_EVIDENCE, not a guess.

## AI-Powered Investment Research Platform (DualLens)
Analysts burn hours cross-referencing market data against strategy documents. DualLens
ranks companies on both at once — three years of stock performance joined with insights
RAG-extracted from strategy PDFs — surfacing which names are financially strong *and*
positioned for AI adoption. One query replaces the manual cross-referencing loop.

## Autonomous Financial Research Analyst
Per-company investment research takes an analyst 4–6 hours; this LangGraph agent does the
gathering in minutes. It pulls real-time market data, news sentiment, and private analyst
reports, then produces a Buy/Hold/Sell recommendation with every source cited — auditable
enough to check, fast enough to run across a watchlist. The predecessor to Aristos
Council, where its single-agent verdict grew into a gated, deterministic decision core.

## Senior Mortgage Underwriting System
Mortgage underwriting takes 3–5 days largely because four assessments — credit, income,
assets, collateral — queue behind one another. This system runs them as specialist agents
under a Supervisor, cutting the cycle to hours while keeping the parts regulators care
about: Fair-Lending compliance, PII protection, bias detection, and mandatory
human-in-the-loop escalation on every contested file.

## MS Risk Screening Agent
Early multiple-sclerosis signals sit scattered across EHR records where no one has time
to connect them. This multi-agent system scans records and flags patients showing early
risk patterns for neurologist review — with adjustable autonomy, a transparent rationale
for every flag, and PHI governance throughout. It surfaces candidates for clinical
judgment; it does not replace it.

## AGI Research Intelligence Platform
Keeping up with AGI-relevant research on arXiv is a weeks-long manual effort with partial
coverage. A Planner→Discovery→Evaluation agent pipeline automates it, scoring every paper
against a standardized 10-parameter AGI framework — literature review in hours, with 3–5×
the coverage of manual survey.

## LinkedIn Post Generator
A content pipeline with editorial standards built in: a Researcher gathers material, a
Writer drafts, a Critic scores against a quality rubric, and a Supervisor loops them
until the draft clears the bar. The interesting part is the loop — generation that
doesn't ship until an adversarial agent approves it.

## Healthcare Intelligence Assistant
Clinicians wait on DBAs for every data question. This Natural Language-to-SQL system lets
them ask directly — and makes it safe by classifying every query before execution: READ
runs automatically, WRITE requires human approval, UNSAFE is rejected outright. Full
audit trails keep it inside HIPAA and GDPR.

## MucAtlas — Municipal Knowledge Agent
City caseworkers lose time hunting answers across fragmented internal sources of unknown
currency. MucAtlas (Munich Innovation Challenge 2026 entry) is a five-agent RAG system
that connects those sources, validates whether legal documents are still current, detects
contradictions between sources, and returns cited answers in seconds — with full data
sovereignty via confidential computing. Architecture and design; competition entry.

## Warehouse Robot Navigation
Programming warehouse robot routes by hand doesn't scale past the first layout change.
This reinforcement-learning agent (PPO) learns goal-directed navigation from scratch and
generalizes to layouts it has never seen — pathfinding as a learned skill rather than a
maintained ruleset.
