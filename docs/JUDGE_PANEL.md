# The judge panel (JUDGE-PANEL-1)

A blind, multi-model panel on the company page. Tick **Judge panel (blind, no vote)**, directly under
*Council opinion*, and three independent models — one from each of Anthropic, OpenAI and Google —
are each shown only the company's raw facts and asked what they would say. Their answers are shown
on the page and written to a log. Nothing else happens.

## What it is for

The doctrine of the project is *the math judges and the LLM writes*. The panel does **not** change
that. It exists to answer, with evidence and in a year's time, a question the earlier experiment
could not: **does model judgement add anything to the lenses?** To find out you have to have written
down, at the time and without the lenses' opinion in view, what the models would have said. That
record is the product. The page section is a convenience for reading it.

## Why it has no vote

- A judge's answer never changes a lens verdict, a rank, a badge, the agreement table, the
  shortlist or any saved result. A test runs the same company with the panel on and off and asserts
  the lens output is byte-identical (the only difference in any download is the added section).
- There is **no average, no combined verdict and no adjudicator call.** The page shows one row per
  judge, then one line for the split ("2 BUY, 1 ABSTAIN") and one line saying whether the panel
  agrees with the verdict of record. That is all the arithmetic there is.
- If the panel disagrees with (or is mixed against) the verdict of record, the narrator writes ONE
  paragraph of at most 80 words saying where and why, as a reading — never a recommendation. It
  runs on the narrator's own model tier, is limited to what the judges themselves said, and is
  checked before it is shown (see *The narrator's paragraph*). It is skipped when the panel agrees
  or no judge is stable.

## The blind pack

Every judge receives the **same** facts pack, built from the readings the company page already
prints, and nothing else. Before anything is sent, the pack is scanned and any line that still
carries a forbidden word is **dropped and recorded**, not sent.

| Kept | Stripped |
|---|---|
| Identity: name, ticker, exchange, country, sector, industry, market capitalisation | Every lens verdict, rank and badge; "proven" / "no edge" labels |
| Peer **names** only (no peer ranks) | The agreement count, shortlist status, any lens name |
| Price and trading readings, the annual financial series (oldest first, each figure with its year and currency) | The council opinion and the narrator's text |
| The valuation band as a plain number against the company's own five-year history | "priced high" / "dearest" wording (a verdict) |
| Analyst counts (worded "strongest positive … strongest negative", not buy/hold/sell), price target, forecasts | Recent news headlines (they carry broker language) |
| Debt and cash, growth record, cash runway | |
| Forensic raw readings: accrual ratio, Altman Z, F-Score as numbers | The clean / doubted label and the readings' ranks |
| Dividend record (the streak is stated as a floor) | |

The lens names are read off `strategies/*.yaml`, so a lens added tomorrow is covered the day it is
added. `tests/test_judge_panel.py` asserts the pack carries no lens name, no BUY/HOLD/SELL token and
no badge word.

**A limit worth stating plainly.** *Blind* means blind to the lenses' opinion. It does not mean the
model knows nothing: a model that recognises "The Coca-Cola Company" brings what it learned in
training, and the prompt cannot take that away. "No internet, no other data" is an instruction, not
a guarantee. The pack carries the company's name because the spec keeps identity; if the 12-month
review suggests recognition is doing the work, an anonymised variant is the next experiment.

## The prompt

One prompt, identical for all judges, stored next to the code
(`src/aristos_council/judges/judge_prompt_v1.md`). It asks for strict JSON:

```json
{"verdict": "BUY|HOLD|SELL|ABSTAIN", "confidence": "low|medium|high",
 "reason": "<one sentence>", "would_change_my_mind": "<one sentence, a specific observable fact>"}
```

**ABSTAIN is explicitly allowed and expected** when the pack is thin or the business is of a kind
the numbers cannot describe. The reply is validated strictly (exactly those four keys, the allowed
words, non-empty sentences; a single code fence is tolerated, prose around the object is not). A
malformed reply is retried once, then recorded as **invalid output** for that call. A failed call
(network, HTTP status) is an **error** and is not retried. The prompt version and the pack's hash are
logged, so every row says exactly what was asked and on which facts.

## The stability rule

Each judge is called **twice** with the same input. Temperature is not set — the provider's default
stands, because the panel measures how stable each model is as it ships.

| Status | Meaning | Shown as |
|---|---|---|
| stable | both calls gave the same verdict (ABSTAIN counts as a verdict) | the verdict, once |
| unstable | both calls were valid and **differed** | "unstable: BUY / HOLD" — both kept |
| incomplete | one call valid, the other not | the valid verdict, flagged; not stable |
| invalid output | malformed twice | "invalid output" |
| error | the call could not be made | "error: …" |
| not run | the key is missing | "not run — no key" |

Only a **stable** judge is compared with the record. An ABSTAIN takes no side.

**Agreement with the verdict of record.** The record's single verdict is the verdict held by a strict
majority of the lenses that voted (the same >50% rule as the MAJORITY_OVERRIDE veto); no strict
majority is *MIXED*. The panel *agrees* if every stable BUY/HOLD/SELL judge matches it, *disagrees* if
none does, and is *mixed* otherwise (or when the record itself is split). *No stable judge* when none
gave a BUY/HOLD/SELL.

## Setting it up

- **Models** are named in `data/judges.yaml` — change them without touching code. The defaults are a
  mid-tier model per family. The OpenAI and Gemini names are the most likely to need updating as
  providers retire models; a name the provider no longer serves shows as that judge's *error*, not a
  crash.
- **Keys** are read from the environment or the local `.env`, like the Anthropic and EODHD keys:
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`. A missing key marks that judge *not run —
  no key* and the panel runs with the rest. Keys travel only in a request header and are scrubbed
  from any error text; they are never printed or logged.
- **Cost.** About six calls per company (3 judges × 2), shown next to the tick box (it counts only
  judges that have a key), plus one short paragraph when the panel differs. Off by default.
- **From the command line:** `python -m aristos_council.company_report KO --judges`.
- **No lens verdict, no panel.** With no lens verdict of record no judge is asked and nothing is
  logged; the page says so in one line rather than going quiet.

## Where the logs are

Both files are under `data/local/judges/` and are git-ignored — nothing about what you look at
belongs in the repo.

- `judge_log.csv` — one row per judge **per call**: `run_date, ticker, judge_family, model_name,
  call_number, verdict, confidence, stable, reason, would_change_my_mind, verdict_of_record,
  lens_split, pack_hash, call_status`. `verdict_of_record` and `lens_split` are the lenses' state *at
  that moment*. A judge with no key leaves one `call_number 0 / NOT_RUN` row, so its absence is in
  the data. `call_status` (`ok | invalid | error | not_run`) is the one column beyond the agreed
  list; it keeps a malformed call distinguishable from a verdict.
- `judge_summary.csv` — one row per company run: the record and its split, each family's stable
  verdict and confidence (`claude_verdict`, `openai_verdict`, `gemini_verdict` …, or `UNSTABLE:BUY/HOLD`,
  `NOT_RUN`, `INVALID`, `ERROR`), the panel's split and its agreement.

The headers are fixed. A file whose columns differ from what the code writes is **refused**, not
migrated, so a changed column order can never shift a year of data under the wrong titles.

## How to read it

```
python -m aristos_council.judges report [--ticker KO] [--dir data/local/judges]
```

prints, per ticker, every dated panel result beside the verdict of record at that moment:

```
KO  (2 panels)
  2026-10-19 09:01  record: HOLD [BUY 0 / HOLD 1 / SELL 0]
      panel: Claude: HOLD (medium)  Openai: NOT_RUN  Gemini: NOT_RUN
      1 HOLD, 2 not run -> agrees   (facts c71b0d381eb3)
```

No prices and no scoring yet — scoring is a later job.

## The narrator's paragraph

When due, the paragraph comes from the narrator's own model tier. It is withheld, with its reason,
(never rewritten) if it is longer than 80 words, is not a single plain paragraph, reads as a
recommendation or says which side is right, combines the judges into a new verdict, or asserts a
BUY/HOLD/SELL word that neither the record nor a judge gave. One corrective retry, as the
plain-English summary has.

## Hand-off to SNAPSHOT-2

The quarterly forward freeze (SNAPSHOT-2) was not on `main` when this was built, so it could not be
extended. **When SNAPSHOT-2 is built it must read `data/local/judges/judge_summary.csv`** and add, per
ticker and family, the latest *stable* judge verdict as columns (`claude_verdict`, `openai_verdict`,
`gemini_verdict`), taking the most recent summary row on or before the freeze date. The summary file
already carries those columns for exactly that purpose.

## The 12-month review plan

What is recorded now is only a log. The review, around twelve months after the first rows:

1. **Fix the rules before looking.** Horizon (12 months from each `run_date`), the benchmark (the
   company's own peer-group median, as in the backtest), and the minimum number of stable
   judgements worth reporting — written down and dated before any scoring. They are deliberately not
   invented here.
2. **Measure stability first.** For each family: the share of runs stable, unstable, abstained,
   invalid, errored. A judge that cannot repeat itself has nothing to score.
3. **Score only stable judgements against the benchmark**, per family, and the lenses' own verdict of
   record on the same names and dates — the question is whether the panel adds anything *beyond* the
   lenses, so compare where they differ (panel *disagrees* or *mixed*), not where they agree.
4. **Count the abstentions as information,** not as misses: which businesses did the models refuse
   to judge, and did the lenses do better or worse on them?
5. **Report luck honestly.** With a small number of names a hit rate means little; use the same
   skill-versus-luck framing as `docs/BACKTEST.md`.
6. **Decide.** If the panel adds nothing, remove it. If it does, the next step is a separate,
   pre-registered proposal — the panel itself never gets a vote by default.
