# The market index

A local table of every listed common stock with its classification and size. Built once
from EODHD, stored as parquet, queried with **no network at all**. It exists so that
"who are this company's peers?" is a local lookup rather than an API call per report.

```
python -m aristos_council.market_index status
python -m aristos_council.market_index build [--exchanges US,XETRA,LSE] [--limit N] [--budget N]
python -m aristos_council.market_index refresh [--older-than 30]
python -m aristos_council.market_index peers TICKER [--floor 12] [--cap 40]
```

## Why a table and not an endpoint

This was measured, not assumed, on 2026-09-18:

| Path | What it actually does |
|---|---|
| Finnhub `/stock/peers` | **403 for every non-US symbol** on this plan — ten of twenty-one portfolio holdings got nothing. Where it answers: at most **12** names, includes the subject itself, ignores size entirely (Eaton peered with two micro-caps), and returns its own symbology including tickers with spaces. |
| EODHD `/screener` | **403** on this plan (found building COHORT-1). |
| S&P 500 + STOXX 600 | 1,026 names over 132 industries — a **median of six** companies per industry, only eight buckets holding twenty or more. Too small to be a peer source. |

One path does work: `exchange-symbol-list` for the listings, `/fundamentals?filter=General`
for the classification and size. That is a call per symbol, which is far too slow to do per
report — so it is done **once**, into a table.

The table also fixes COHORT-1's thin cohorts (Integrated Oil & Gas came back at eleven
names for exactly this reason), so it pays for itself twice.

## What a row holds

`ticker` (EODHD form) and `yahoo_ticker` (what the ranker resolves), `name`, `exchange`
(the **venue** — NYSE, NASDAQ, PINK), `market` (the exchange **code** the build requested,
`US`), `country`, `currency`, **`primary_ticker`** and **`isin`** (which listing this is —
see below), `sector`, `industry`, the three GICS levels, `market_cap`, `market_cap_usd`
with a `market_cap_usd_source` tag, `fetched_at` and `source` (which carries the parser
**generation**, so "never asked" can be told from "asked and got nothing").

Venue and market were one field until MARKET-INDEX-2, which is why an OTC listing looked
like a plain US listing to every consumer.

**`market_cap` is kept exactly as served, in the local quoted currency.** EODHD does not
convert it, and the unit does not always match the currency code — Victrex reads 831m
against a `GBX` code, which is pounds rather than pence. So the USD figure is a *separate,
tagged* column, converted through the repo's existing FX helper (`factors._fetch_fx_rate`,
which goes via the ranker's own cached price path) and marked `abstained` for any currency
it cannot price. A guess is never written into the reported figure.

## Building it

The build is **resumable and safe to interrupt**. Rows already present and younger than
`max_age_days` are skipped without a call, so a build that dies at symbol 4,000 resumes at
symbol 4,000. It writes the whole table every 200 new rows via a temp file and a replace,
so an interruption can never leave a truncated table where a readable one was. It backs
off on HTTP 429 (a rate limit is "wait") and stops cleanly on 402/403 (a quota is "stop"),
reporting how far it got and how many calls it used.

**A gap the provider does not have is not asked about every build (INDEX-CAP-RETRY-1).**
Measured 2026-09-24: 2,566 rows carry no market cap and 1,767 no classification, and *every one*
was fetched with a name — the provider answered and simply had nothing. Probing
`/fundamentals/0052.TW` returns `"MarketCapitalization": "NA"`, an explicit *not available*
rather than a blank or a timeout. The 2026-09-23 build refetched 1,502 such rows and filled
**none**, for roughly 15,000 charged units. So a row that has come back empty
`empty_retry_after` (2) times is left alone for `empty_retry_days` (30), both set in
`market_index.yaml`; a refetch that *fills* something resets the count, because that row is
making progress. Rows written before the counter existed are not treated as unasked — the
evidence is on the row (listing parser + a name, and `fetched_at` says when) — so the saving
starts on the next build rather than one build later. `status` splits the gap-less rows into
**due** and **waiting** so the next build's cost is visible before it is spent.

**Milan is gone (INDEX-CAP-RETRY-1).** `/exchange-symbol-list/MI` answers HTTP 404 on every
build, and on 2026-09-24 every plausible alternative was tried against the endpoint — `MI`,
`MTA`, `BIT`, `MIL`, `IT`, `XMIL`, all 404. There is no code to correct it to, so `MI` was
removed from `market_index.yaml` with the evidence in a comment rather than left to be skipped
at the cost of a request and a line of noise every build. **Korea and Tokyo (INDEX-EXCHANGE-CODES-1,
2026-09-25):** `KS` was never EODHD's code — Korea is two exchanges, and `/exchange-symbol-list/KO`
(KOSPI, 941 common stocks) and `/KQ` (KOSDAQ, 1,849) both answer, so the yaml now lists `KO` and
`KQ`. `T` and `TSE` both 404, so **Tokyo is not tracked** until a code that answers is found. A related correction — the
earlier claim that **HK** was absent was wrong: `/exchanges-list` omits it, yet
`/exchange-symbol-list/HK` serves 3,512 names (build log, 2026-09-23T09:22:20). The
exchange-list endpoint is not authoritative about what the symbol-list endpoint will serve.

**An exchange whose listing fails is skipped, not fatal (MARKET-INDEX-SKIP-1).** One
unlistable venue used to end the whole run — on 2026-09-22 the European build died at Milan
(`/exchange-symbol-list/MI` → HTTP 404) and never attempted the exchanges after it. Such an
exchange is now skipped, named in the build log and counted in the summary ("4 exchanges
skipped: MI, HTTP 404; …"), and the rest of the build proceeds; a *quota* refusal still stops
everything. Probing `/exchanges-list` on 2026-09-23 (70 exchanges) showed **MI, HK, T and KS
absent from that list** (HK and Korea turned out to be served anyway — see above) — there is no Italian exchange in the list at all, so `MI` is not
a mis-spelled code. (Superseded: `MI` and Tokyo are now removed and `KS` is replaced by `KO` and `KQ`, as above.)

**A network error is not a 404 (INDEX-SKIP-RETRY-1).** On 2026-09-23 a network blip skipped nine
healthy exchanges in one second, because a URLError was treated exactly like "not found". A
listing that gets an HTTP **404** is skipped at once and reported as *not available (404)*; one that
gets **no answer** (URLError, timeout, dropped connection, 5xx) is retried on a backoff — three
tries over about two minutes — before it is skipped, and reported as *network error, will retry
next build*, with the exact command to run those exchanges again. Only the listing call retries; a
per-symbol fundamentals failure is still counted and moved past, and a quota refusal still stops
the build.

`market_index.yaml` is tracked and lists the venues. The built table is **not** tracked —
it is machine-generated and rebuildable, like the cohorts under `data/local/cohorts/`.

### Do not run it on the same day as another bulk job

The market index build, a COHORT-1 cohort build and any scheduled watcher all draw on the
same EODHD daily quota, and a build that trips the quota stops half-finished. Run **one
bulk job per day**. `status` tells you where a previous build got to, and `refresh` picks
it up rather than starting again.

## What a build costs, and what it refuses to buy

**EODHD does not charge one unit per request.** A `/fundamentals` call costs **10** and an
`exchange-symbol-list` call costs **1**, so a build reporting "6,000 calls" has really
spent 60,000 of the daily allowance. Every summary prints both numbers, because only one
of them is the one you run out of:

```
20 row(s) fetched (20 refetched for a missing cap), 0 still fresh, 337 dropped on venue,
0 failed; 21 request(s) = 201 charged of 90,000
```

`--budget` is in **charged units** (default 90,000). The build stops *before* the request
that would exceed it, flushes, prints the summary and prints the exact resume command.

### The venue filter is what makes a US build affordable

The US listing returned **17,829 common stocks** on 2026-09-18, of which **11,508 were
OTC** — PINK 8,644, OTCQB 1,252, OTCQX 498, OTCGREY 486, OTCCE 475, OTCMKTS 144, OTC 6,
OTCBB 3. Fetching those is roughly **118,000 charged units**, more than a whole day's
budget, for names no lens should ever rank.

So the filter is applied to the **listing row**, before any fundamentals call, and an
excluded venue therefore costs nothing at all. `venues:` in `market_index.yaml` sets it
per exchange; an exchange absent from the map is unrestricted. Every venue string seen is
printed with a count once per build — *including the ones dropped* — so a venue worth
admitting is visible rather than silently missing. `BATS` (39), `NYSE MKT` (19) and a bare
`US` (227) were seen and are **not** admitted by default.

Rows already in the store whose venue is not allowed are **removed** on the next build,
with a count. The first build left 337 OTC rows in the table; they are gone.

### Two things that make a row get refetched

A row is skipped only if it is **complete and fresh**. "Complete" means it carries a
market cap: a capless row was never usable as a peer, so keeping it fresh is keeping a
hole fresh, and it is refetched whatever its age.

That mattered because of a real bug. The first build fetched 526 rows and **every single
one had no market cap**: the request asked for `filter=General`, and the cap lives under
`Highlights`. The request is now
`filter=General,Highlights::MarketCapitalization`, and its response shape was **probed
against the live API before the parser was written**, because the filter changes the shape
and not only the contents:

```
filter=General
  -> {"Code": ..., "Name": ..., "Exchange": ...}          General is FLATTENED

filter=General,Highlights::MarketCapitalization
  -> {"General": {...37 keys...},
      "Highlights::MarketCapitalization": 4918238773248}  General is NESTED, cap at top
```

The parser reads both layouts, so rows already on disk stay readable. A cap that is
absent, zero, or the literal string `"NA"` — which EODHD really does send — is a genuine
abstention, counted by `status` as "no market cap" rather than stored as a figure.

> Provider quirk worth knowing: EODHD types **rights and warrant lines as "Common Stock"**.
> `AACPR.US` ("Apogee Acquisition Corp Rights") comes back as Common Stock on NASDAQ with
> a cap of `"NA"`. It is admitted by the venue filter, costs a call, and abstains. Nothing
> in the index is wrong about it — it simply is not a company — but a `-R`/`-WS`/`-WT`
> guard on the listing row would save the call.

### No silent exits

Any exception at all ends with the store flushed, the summary printed, and
`STOPPED: <type>: <message>` plus the traceback appended to
`data/local/market_index/build.log`, timestamped. Every progress line goes to the same
log. The exit code is non-zero for any stop that is not `--limit`. A build that dies
quietly after 4,000 fetches has lost 40,000 charged units and told nobody why.

## One row per company, and one currency

Two defects showed up the first time the ladder ran over a real 9,018-row index.

### Home listings

TSMC's peer group contained **AMD.US, AMD.TO and AMD.XETRA**, and NVDA.US beside
NVD.XETRA. Those are one company each. A peer group that counts a company three times is
not a comparison, it is a weighted average nobody asked for.

`PrimaryTicker` and `ISIN` are now stored on every row. Probed on 2026-09-20, one real
response each:

| symbol | PrimaryTicker | ISIN | |
|---|---|---|---|
| `AAPL.US` | `AAPL.US` | `US0378331005` | home listing — ticker equals primary |
| `AMD.XETRA` | `AMD.US` | `US0079031078` | cross-listing — names its home, shares its ISIN |

So the peer pool is **deduplicated by company**, and the home listing is *preferred*
rather than required. One row per company always survives; which one is decided, in
order, by: it is the home listing; its country matches the issuer country in the ISIN;
then the ticker, so the answer never depends on the order rows came back in.

**Identity is PrimaryTicker first, then ISIN, linked transitively (PEER-DEDUP-1, 2026-09-25).** A US ADR carries its *own* ISIN but names its home line, so ISIN-first kept `TSM.US` apart from `2330.TW` and TSMC was its own peer; rows now group when they share a primary ticker *or* an ISIN, and a company is never its own peer — none of its lines stands in its pool.

**Preferred, not required, on purpose.** A strict "home listings only" filter loses
companies, which is the same defect wearing a different hat. Of ten German blue chips
probed the same day, **four name a primary this index does not track** — SAP → `SAP.F`,
Mercedes-Benz → `MBG.F`, Rheinmetall → `RHM.F`, and VOW3 → `VOW.XETRA` (a different share
class). Dropping every non-home row would delete SAP from every software peer group.

Cross-listings **stay in the table** — a reader may look a company up by one — and
`peers()` resolves them: asking for `NVO.US` answers for `NOVO-B.CO` and says so. When
the home listing is not in the index, the row is used as it stands and that is said too.

> Known gap: `AMD.TO` is a Canadian Depositary Receipt for which EODHD publishes **neither**
> PrimaryTicker nor ISIN, so nothing links it to `AMD.US` and it survives as its own row.
> `status` counts these under "unresolved". Nothing is wrong with the row; there is simply
> no data to join on.

### Size in one currency

The bands compare **`market_cap_usd`, never the local figure**. Korea (`KO`, `KQ`) is in
the build order and Tokyo may follow, and a 900bn JPY company is about 6bn USD: banded in local units
against a 9bn USD subject it would look a hundred times too large.

A row with a local cap but **no USD conversion** is excluded from the pool and counted
*separately* from a row with no cap at all — folding the two together would hide a broken
FX rate behind what looks like missing data. The peers table shows both figures, local
with its currency and USD beside it.

### Refetching for the new fields

A row is complete only if it has a market cap **and** was fetched by a parser that asked
for the listing fields (`source: eodhd+listing`). The 9,018 rows of the first build were
not, so they are refetched regardless of age — about 90,000 charged units, one day's
budget. The build prints the count before it starts.

The generation tag exists so that "never asked" can be told from "asked and got nothing".
Without it AMD.TO would be refetched on every build forever, for an answer that will not
change.

## Whose classification is this?

**EODHD's.** The index stores the sector and industry the provider reports and does not
second-guess them. U-Haul comes back under *Passenger Airlines*; that is their label, not
a judgement of ours, and correcting it by hand would mean maintaining a private taxonomy
that silently disagrees with the source every report cites.

**One narrow exception: `data/label_overrides.yaml` (PEER-LABEL-RECALL-1).** A provider label that is plainly
wrong hides a rival from every cohort it belongs to, so a small dated file corrects a GICS sub-industry per ticker
(seeded: Siemens Energy and Schneider Electric → Heavy Electrical Equipment; Micron's US line → Semiconductors).
Every entry carries a date and a reason; it corrects a label only — never a size, a listing or a peer; the index
on disk is not rewritten; and every use is printed in the cohort report as `label overridden`.

This is exactly why **the peer list is shown by name** on the Company Check page and in
the CLI. A group assembled from someone else's classification can be wrong in ways no
amount of internal consistency will reveal, and the only honest defence is to let the
reader see who the company was measured against.

**Two kinds of row are kept but never used as peers (INDEX-CLASS-SANITY-1).** EODHD lists Taiwan
ETFs as "Common Stock" and labels them nonsense (`0052.TW` "Fubon Taiwan Technology" →
Pharmaceuticals; `00939.TW` "China Construction Bank Corp Class H" → Semiconductor Materials), so a
fund became a "pharmaceutical peer". A row is a **fund, not a company** on a strong name pattern
(ETF/ETN, UCITS, *Fundo*, *Series Trust*, closed-end / investment trust, leveraged product), on its
exchange's fund code shape (Taiwan `00xx`, LSE `0P…` fund ids), or on a fund word with nothing to
say otherwise; a REIT called an "investment trust", Northern Trust and an income fund that makes
chemicals are not funds. A row whose classification **contradicts its own name** (a "Bank" under
semiconductors) is **classification suspect**; a row with no classification is never suspect. Both
are counted in `status`, and a fund or suspect subject gets no peer group and a stated reason.

**Secondary trading lines are kept but never peers (PEER-RECEIPTS-1, 2026-09-25).** EODHD serves Brazilian BDRs (`E1TN34.SA`), Canadian CDRs (`AMD.TO`), Swiss lines of foreign stocks (`NVDA.SW`), London `0xxx` lines (`0NMK.LSE`: Vestas at $5bn there, $31bn at home) and London GDRs as ordinary common stock, mostly with no PrimaryTicker or ISIN, so each stood as a company of its own. They stay in the table, are skipped in every pool, and `status` counts them by kind — including how many are the *only* line their company has here (that company then sits in no peer group). A receipt looked up by its own symbol is answered for the company it mirrors.

**A size the company's other lines refute is kept but never a peer (PEER-SIZE-SANITY-1, 2026-09-25).** A row whose USD cap is more than `size_suspect_factor` (default 5×, in `market_index.yaml`) from *every* other own line of its company, while those others agree with each other, is *size suspect*: excluded from pools and counted in `status`. Two lines that disagree cannot say which is wrong, so neither is flagged — they are counted as "cannot be adjudicated" instead. On the 2026-09-25 table this flagged 21 rows, all real errors (20 London `GBX` lines reading ~100× low, and `NOKIA.ST`), and left 34 disagreeing companies unjudged. **Known upstream defect surfaced by this:** London lines quoted in `GBX` carry a pounds figure that `_UsdConverter` scales as pence, so e.g. `RR.LSE` reads $1.6bn against $158bn elsewhere; the converter is not changed here.

## The peer ladder

`peers(ticker, floor=12, cap=40)` widens only as far as it must, and says how far it went:

| Rung | Classification | Size band |
|---|---|---|
| a | same GICS sub-industry | ¼× – 4× market cap |
| b | same GICS sub-industry | ⅒× – 10× |
| c | same GICS industry | ⅒× – 10× |
| — | abstain, naming the **widest rung tried** and its count | |

It widens the **band** before the **classification**, because keeping the industry is the
cheaper concession. **Labels are compared like with like (PEER-LABEL-MATCH-1, 2026-09-25):** GICS against
GICS, EODHD's own `industry` against EODHD's — never one against the other, so a row with no GICS label is not
matched against GICS names by coincidence of wording ("Semiconductors" is both an EODHD industry and a GICS
sub-industry), and the provider's `Other` is not a label at all. A subject with no GICS sub-industry is matched
on its EODHD industry only, and the reasons say so. The cohort report states **which step (1, 2 or 3) found the
cohort and how many distinct companies it holds**, and the peer table shows which label system matched each member.

**A peer qualifies on either label system (PEER-LABEL-RECALL-1, 2026-09-25):** it matches the subject on its GICS
sub-industry (steps 1–2) or industry (step 3) *or* on its EODHD industry, so a wrong label in one system does not
hide a rival — GICS files Siemens Energy and Schneider as machinery while EODHD files them with Eaton. Size bands
and the floor of 12 are unchanged, and the report says how many members matched on each system.

Always excluded: the subject itself; rows with no market cap (counted in the reasons);
and financials unless the subject is itself a financial — a bank's balance sheet is its
product, so leverage and EV-based measures do not mean for it what they mean for an
operating company (the same judgement `cohorts.excluded_by` makes).

Over the cap, the group is trimmed to the nearest in size **on a log scale**, so "half the
size" and "twice the size" are equally near. On a linear scale a $400bn peer of a $200bn
subject looks closer than a $1bn one, which would keep the giant and drop the near match.

The result is **deterministic for a given snapshot**: every rung is a filter over the same
rows, the trim is by log-distance then ticker, and the members come back in ticker order.
Feeding the index in reverse gives the same group.

`peer_snapshot(group)` is what a report saves so a rerun is reproducible — the members and
the snapshot date, the same discipline as `universe.member_hash` and COHORT-1's frozen
`members.csv`. A verdict that depends on a peer list is unreadable later unless the list
was recorded.

## What it does not do

No verdicts. The Company Check tab shows the peer group, the rung, the band and the
snapshot date, and stops there. Ranking the peers is separate work.
