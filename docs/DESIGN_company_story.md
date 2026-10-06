# COMPANY-STORY-1: the single-company page reads like a one-page note

Status: design for approval, 2026-10-05. Build after Batch 19B, before the tester round.

## The problem

Every company report in the 2026-10-05 hand-test round (Ford, Viking, NovoCure, BYD, JPMorgan) needed a human explanation before it made sense. The page opens with tables and ends with diagnostics. The one readable part, the plain-English summary, is optional, costs a model call, and was missing from three of the five reports. A reader who knows finance but not Aristos cannot tell, in the first ten seconds, what the tool concluded and why.

## The rule

The first screen answers three questions in order, in prose a careful non-specialist can follow: what did Aristos conclude, why, and how much should I trust it. Everything that supports the answer stays on the page but below a fold. No number is removed. No verdict logic changes.

## Page order

### 1. The answer (two lines, written by code)

The first line states the verdict of record in plain words. The second states the single most important reason. Both are built from templates filled with the run's own figures; no model is involved, so they are always present and cost nothing.

Examples, generated from the five test runs:

- Ford: "No lens voted on Ford. Five lenses need an operating profit, and Ford lost money over the last twelve months; the two income lenses need a dividend record it does not have."
- BYD: "One of three votes says BUY (Quality); the other two say HOLD. Five lenses did not apply, mostly because BYD's return on capital (9.3%) is below their 12% floor."
- JPMorgan: "HOLD, on the one lens built for banks (Financials, 23rd of 29). The other seven lenses are not for banks. No track record exists for this industry yet."
- Viking (under $5bn): "No lens voted on Viking. It has no profit, no revenue and no dividend, so nothing here can rank it. It is also outside the tested range: no track record applies."

Template rules: name the count of votes and the split; name the reason that excluded the most lenses, with its figure; if the company is under $5bn or in an untested industry, say so in the second line; never use a lens id, a column name or the word "cohort".

### 2. The story (five short paragraphs, written by code)

The same five questions the model summary already answers, filled from templates so they read the same every time:

1. What this run asked: company, peer group size and how it was found, lenses run.
2. What happened: votes, exclusions with their figures, Forensic's mark, the band's reading.
3. What survived: debt and cash, growth record, analysts, in that order, each one sentence, each with its as-of date.
4. What to doubt: anything abstained, any badge that is not "proven", the peer-group caveat, the small-company caveat.
5. What this cannot tell you: one fixed sentence, plus the untested-range sentence when it applies.

When the plain-English summary box is ticked, the model's summary replaces this block; it never appears beside it. If the model's summary is withheld, the code-written story stays and a one-line note says why the model's was withheld.

### 3. One table

Lens, vote or mark, badge, one-line reason. Nine rows. The "what it asks" captions move to a tooltip (HTML) or a footnote (Markdown). The repeated small-company tag appears once, above the table.

### 4. Show the workings (folded, open on click)

In this order: valuation band, price and cash, absolute readings, what analysts say, council opinion, peers, how this peer group was built, narration check (one line, folded), sources. Each section is unchanged from today except where Batch 19A and 19B already change it. The text download keeps every section; the fold is a display choice.

## What does not change

Votes, ranks, badges, the agreement rule, the peer ladder, the council, the sweep, the CLI, the list (cohort) report. The results JSON of every evidence run must be byte-identical before and after.

## Acceptance

- The five evidence runs re-rendered: the first screen of each is at most 25 lines before the fold; paste each "answer" block.
- A fixture test per template branch: no votes; votes with a BUY; votes without a BUY; bank; under $5bn; untested industry; summary withheld.
- The sweep gains a rule: no lens id, column name or the word "cohort" in sections 1 to 3.
- Results JSON byte-identical to main for the evidence runs.
- Three screenshots: Ford, BYD, JPMorgan, first screen only.

## Open questions for Kayvon

1. Section 2 by code, or by the model only? Code means it is always there and free but reads plainer; the model reads better but costs a call and can be withheld. The design says code by default, model when ticked.
2. Should the council opinion, when ticked, sit above the fold (it is the part a reader asks for) or stay in the workings?
3. Does the list (cohort) report get the same treatment later, or is it fine as a table-first document for a different reader?
