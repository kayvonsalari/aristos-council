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

The bands compare **`market_cap_usd`, never the local figure**. Tokyo and Korea are next
in the build order, and a 900bn JPY company is about 6bn USD: banded in local units
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

This is exactly why **the peer list is shown by name** on the Company Check page and in
the CLI. A group assembled from someone else's classification can be wrong in ways no
amount of internal consistency will reveal, and the only honest defence is to let the
reader see who the company was measured against.

## The peer ladder

`peers(ticker, floor=12, cap=40)` widens only as far as it must, and says how far it went:

| Rung | Classification | Size band |
|---|---|---|
| a | same GICS sub-industry | ¼× – 4× market cap |
| b | same GICS sub-industry | ⅒× – 10× |
| c | same GICS industry | ⅒× – 10× |
| — | abstain: "only N comparable companies found" | |

It widens the **band** before the **classification**, because keeping the industry is the
cheaper concession. Where GICS fields are missing it falls back to the EODHD industry
field and says so in the reasons.

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
