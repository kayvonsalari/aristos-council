# Holdings check

A private, on-demand check of your own holdings, run on your PC. It replaces the weekly scout
holdings job, which ran on GitHub and wrote its reports into this public repository.

## What it does

For each **stock** in your holdings file it runs the same engine as the Analyse tab's Company page:
the company is ranked against its own peer group (from the local market index), every stock lens
votes with its track-record badge, and the report carries the valuation band, the cash runway, the
small-company path for a company under $5bn, and every abstention with its reason. The verdicts are
the deterministic ranker's. Nothing here changes them.

* **ETFs, funds and other types** are listed as *skipped* with a reason. They are never graded.
* **A ticker that does not resolve** is listed with the reason. It is never guessed from the name.
* **No model is called** unless you pass `--council` (see below).

## How to run it

```
python -m aristos_council.holdings_check                 # every stock in the file
python -m aristos_council.holdings_check --names AAA,BBB # only these (name or ticker)
python -m aristos_council.holdings_check --dated         # also keep a dated copy + "changed since last run"
python -m aristos_council.holdings_check --council       # also the council narrator: ~6 model calls per stock
```

The first run, with no file yet, writes `data/local/holdings/holdings.csv`. It seeds that file from
`data/local/holdings.json` when that exists, and otherwise writes a template with two example rows.
Edit it and run again.

### The holdings file

`data/local/holdings/holdings.csv`, with the columns `name, ticker, broker, type, note`. `type` is
`stock`, `etf`, `fund` or `other`. There is **no amounts column** by design. Use the ticker form the
Analyse tab accepts (for example `AZN.L`, `SAP.DE`).

### What it writes

All under `data/local/holdings/`:

| File | Contents |
|---|---|
| `summary.md`, `summary.csv` | One row per holding: name, ticker, group used, verdict (the Analyse tab's own headline), rank in group, lenses voting, valuation band, warnings (small group, outside the tested range, data gaps), link to the report |
| `reports/<ticker>.md` | The full company report, in the Analyse tab's Markdown export format |
| `dated/<date>/` | With `--dated`: a copy of the summary and the reports, so runs can be compared. A "Changed since <date>" section lists verdict, rank and band moves, and names that are new or gone |

## What stays local

Everything above lives in `data/local/`, which is git-ignored, and the tool refuses to write anywhere
outside it. The repository is public: **never commit a holdings list, a sheet link or a report.**
`tests/test_holdings_check.py` fails if a holdings file, a retired scout report or a Google Sheets
link is tracked by git. The tool reads only EODHD and yfinance, the sources Aristos already uses, and
never touches Google Sheets.

## Cost

Free by default: no model, only the market-data calls the Analyse tab makes (cached for the day).
`--council` narrates each stock with about six model calls billed to `ANTHROPIC_API_KEY`.

## Retired

The weekly `scout-verdicts` GitHub workflow no longer has a schedule. It used to read a private
Google Sheet and commit the results, sheet link included, to `reports/scout/`.
