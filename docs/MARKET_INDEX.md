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
`US`), `country`, `currency`, `sector`, `industry`, the three GICS levels, `market_cap`,
`market_cap_usd` with a `market_cap_usd_source` tag, `fetched_at` and `source`.

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
