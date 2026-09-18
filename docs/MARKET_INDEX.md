# The market index

A local table of every listed common stock with its classification and size. Built once
from EODHD, stored as parquet, queried with **no network at all**. It exists so that
"who are this company's peers?" is a local lookup rather than an API call per report.

```
python -m aristos_council.market_index status
python -m aristos_council.market_index build [--exchanges US,XETRA,LSE] [--limit N]
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

`ticker` (EODHD form) and `yahoo_ticker` (what the ranker resolves), `name`, `exchange`,
`country`, `currency`, `sector`, `industry`, the three GICS levels, `market_cap`,
`market_cap_usd` with a `market_cap_usd_source` tag, `fetched_at` and `source`.

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
