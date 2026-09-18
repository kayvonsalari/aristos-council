# Cohorts

A cohort is a **rule**, not a list of names. You write the rule down, the builder derives
the membership mechanically, four quality checks say whether the result is worth ranking,
and then it is frozen and versioned. Nothing in the builder path accepts a hand-picked
member.

```
python -m aristos_council.cohorts build --all
python -m aristos_council.cohorts build --name "Pharma EU-US" --rebuild
python -m aristos_council.cohorts check --name "Pharma EU-US"
python -m aristos_council.cohorts diff  --name "Pharma EU-US"
```

No model is ever called. The quality report ranks with the deterministic ranker only, and
`tests/test_cohorts_no_llm.py` asserts that no module in the package imports a model
library — in a subprocess, because by the time the rest of the suite has run, langchain is
already in `sys.modules` for honest reasons and an in-process check would pass regardless.

## The definition file

`data/local/cohorts/definitions.yaml`. One entry per cohort:

```yaml
cohorts:
  - name: "Pharma EU-US"
    industry:
      - "Drug Manufacturers - General"
      - "Drug Manufacturers - Specialty & Generic"
    exchanges: [US, XETRA, LSE, EURONEXT, SIX]
    min_market_cap: 1000000000
    min_history_years: 5
    exclude: [financials, reits]
    anchors: []
```

| Field | What it means |
|---|---|
| `name` | The display name. The slug derived from it is the directory name, so renaming a cohort makes a **new** cohort, not a new version of this one. |
| `industry` | One or more EODHD `General::Industry` strings, **or** GICS sub-industry names, which are mapped across (`GICS_TO_EODHD`). A string in neither table is an error at load — never a cohort that silently builds to nothing. |
| `exchanges` | Umbrella names (`EURONEXT`, `SIX`) mapped to the EODHD venue codes they cover. Main listings only. |
| `min_market_cap` | See below — **the name's own currency, deliberately unconverted**. |
| `min_history_years` | Years of price history the name must have. |
| `exclude` | Defaults to `[financials, reits]` for every cohort. An explicit `[]` turns the exclusions off; a missing key means the default is on (an absent value is not a `false` one — CLAUDE.md rule 3). |
| `anchors` | 0–2 tickers to **look at** in the report. An anchor is included only if it passes the same rules as everything else, and an anchor that failed is reported as absent. It is never a way to hand-pick membership. |

### Why the market-cap floor is not converted

`min_market_cap: 1000000000` is applied in each name's own reporting currency: one billion
USD for a US name, one billion EUR for a German one, one billion CHF for a Swiss one. This
is deliberate and follows CLAUDE.md rule 8, which already refuses FX conversion for
absolute money thresholds elsewhere in the repo.

The floor's job is to say *"not a micro-cap"*, not to measure size precisely. It does that
job equally well in all four currencies, and the alternative — one FX rate applied to
forty names on the day the cohort happened to be built — would be fake precision that also
changes the membership depending on when you ran it. An honest stated approximation beats
forty precise-looking numbers that move with the euro.

`members.csv` records each name's currency next to its cap, so the approximation is
visible rather than implied.

## Where the names come from

Two paths. Which one runs is **probed at startup, not assumed**, and every report says
which one built it.

1. **EODHD `/screener`**, filtered by industry, exchange and market cap — the primary.
2. **Index constituents** (`GSPC.INDX` + `STOXX.INDX`) filtered by the fundamentals'
   industry field — the fallback.

The probe asks the screener for one known filter set and checks the rows that come back
*against the filter it asked for*. An endpoint that accepts a filter and ignores it is
worse than one that refuses, because it answers confidently with the wrong universe — so
an ignored industry filter counts as unavailable.

> On the EODHD subscription this was written against, `/screener` answers **HTTP 403** and
> the fallback is what runs. That is a fact about a plan, not about the code: a key that
> can screen takes the primary path with no change. `/eod` is also 403 on this plan, which
> is why price history comes from the ranker's own provider (see rule 2 below).

**yfinance is the secondary source**, used *only* to fill an industry or market cap the
primary left blank for a name it had already found. It can never discover a name — a
second source that adds members is a second rule nobody wrote down. Every filled value is
tagged on the member, counted in the report's source summary, and visible in `members.csv`.

## One bulk job per day

The cohort build, the MARKET-INDEX-1 build (`docs/MARKET_INDEX.md`) and any scheduled
watcher all draw on the same EODHD daily quota, and a job that trips the quota stops
half-finished. Run one of them per day. Each reports where it got to, and each resumes
rather than starting again.

## The four cleanup rules

Applied **in this order**, which is part of the contract (`cleanup.RULES`, pinned by a
test). Every removal is logged with its rule and the value that tripped it.

1. **One line per company.** Drop ADRs, GDRs, preferred lines, secondary listings and
   duplicate share classes; keep the primary listing. Survivor chosen by: the issuer's own
   `PrimaryTicker`, then the exchange order the definition itself lists, then market cap,
   then the symbol so the result never depends on dict order.
2. **Size and history.** Below the cap floor, or too short a history. A **missing** cap is
   not a failure here — nobody measured it — it falls to rule 4, with a reason that says so.
3. **Sector exclusions.** Financials and REITs, when `exclude` says so. Matched on sector
   first, then on industry prefix, because "Financial Services" is a sector while
   "REIT - Retail" is an industry.
4. **Nothing for the lenses to read.** Names missing the fields every lens needs. They
   would abstain anyway, and keeping them would push the cohort's abstention rate up for a
   reason about data coverage rather than about the market.

Rule 1 runs first so that a company is collapsed to one line *before* anything is
measured — otherwise the size test, not rule 1, decides which listing survives.

### Size band

The result must land between **20 and 60** names.

- **Under 20 → "too thin".** The report names the exchange that would fill it, with the
  number of extra names it would actually add, and says so honestly when that would still
  not be enough. **The cohort is not padded and not frozen.**
- **Over 60 → "too wide".** The report says to narrow the industry code. **The cohort is
  not truncated and not frozen.**

Not freezing an out-of-band cohort is the point: "do not pad" means the build stops, not
that it ships anyway.

## The four quality checks

Written to `data/local/cohorts/<slug>/vN/report.md`, in plain sentences with one figure
each, from a single **ranker-only** run (no LLM).

| Check | The figure | Flagged when |
|---|---|---|
| **Abstention rate** | Share of the cohort the ranker could not fully read — names it could not read at all, plus names ranked with at least one factor missing | **> 15%** |
| **Band spread** | Share of names per valuation band (five 20-point buckets of each name's own history) | **> 60% in one band** |
| **Drop-one stability** | Remove each name in turn, re-rank, report the largest place shift any *other* name suffers | **> 3 places on a cohort of 30**, scaled — i.e. one tenth of the cohort |
| **Anchor check** | Each anchor's verdict and rank | Never. It is shown, not judged. |

Plus a **source summary**: counts from screener / constituents / yfinance-filled.

Three of these deserve a note about what they refuse to do:

- **Abstention** counts both the unreadable and the partly-read. Counting only names that
  never made the table would flatter the cohort; counting only the holes would miss the
  names that never made it.
- **Band spread** never invents a percentile. A band that abstained is counted separately
  and never folded into a bucket — a fabricated 50th is exactly the lie the band exists to
  refuse. A cohort with four names in five in the dearest fifth of their own history is not
  a set of choices, it is one bet, and the ranker will sort within it without saying so.
- **Drop-one** compares against the base order *restricted to the surviving names*.
  Removing the name in 3rd place moves everyone below it up a seat; that is bookkeeping,
  not instability. What counts is a name changing places with another name. The re-ranking
  happens in memory from factor values the run already produced, so N+1 rankings cost one
  fetch — and it re-ranks with the run's own factors, cut, k and missing-mode, or the
  "instability" reported would be its own settings disagreeing with the pipeline's.

## Freezing and versioning

On a successful build, `data/local/cohorts/<slug>/vN/` gets:

| File | What it is |
|---|---|
| `members.csv` | **The membership of record.** ticker, yahoo_ticker, exchange, industry, market cap, currency, ISIN, name, source, filled |
| `definition.yaml` | A **copy** of the rule as it was when this version was cut — not a pointer to the editable file |
| `report.md` | The quality report |
| `removals.log` | The build log and every removal, with rule and reason |

**The rest of Aristos reads `members.csv`, never the definition.** Editing a rule therefore
cannot silently change what a past run graded — the same discipline strategy files have
(CLAUDE.md rule 7), for the same reason.

`vN` **increments only on `--rebuild`**. A plain `build` over an already-built cohort does
nothing and says so, so a scheduled job cannot quietly re-cut a cohort under a scoreboard.
`diff --name NAME` shows what a rebuild *would* add or remove without doing it.

Each built cohort also registers as a local **stock** list (`asset_kind: stocks`,
ASSET-MODE-1) under `universes/local/cohort_<slug>_v<n>.yaml`, so it appears in the UI like
any other list. The list carries the **Yahoo** symbols the ranker resolves, translated from
EODHD's by one table (`cohorts/symbols.py`) — a name with no translation is left out of the
list and named in its description rather than written as a symbol that would come back
UNRATEABLE. The universe id carries the same version number as the frozen directory, so a
list in the sidebar and a cut on disk can never disagree about which one it is.

## What is committed

`definitions.yaml` is source and is committed. The built cohorts under
`data/local/cohorts/<slug>/` are machine-generated local data and are **not** — they are
rebuilt from the rule on any machine, and committing one cohort built today while nine
others were not would be a confusing half-state. Flip it by removing the entry in
`.gitignore` if you want them tracked the way `verdicts/` and `reports/` are.
