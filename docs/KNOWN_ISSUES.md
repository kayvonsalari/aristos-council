# Known issues

Things that are true today, known, and deliberately left as they are. Each says what a reader will see
and what would change it. (Newest first.)

## ETF fund size abstains for most funds (ETF-MODE-1, Batch 18A)

Fund sizes are compared in USD. A fund whose size currency is **not stated** and which is **not listed
in USD** abstains on size, with the reason shown: *"fund size withheld - the currency it is reported
in is not stated, so it cannot be compared in USD"*. **Most rows in `data/etf_static.csv` predate the
`fund_size_currency` column**, so most European and London funds abstain on size. The ETF lenses use
`missing: neutral`, so the fund is judged on its other factors and is not penalised.
**What changes it:** a human fills the `fund_size_currency` column from each fund's factsheet (the
monthly refresh ritual in the file's header). Nothing is inferred in the meantime.

## VWRL.L has no yield and no fee from any provider

Yahoo's record for this London fund carries neither a distribution yield nor an expense ratio (the
fields are absent, so it is not a mis-mapped field). A yield could be derived from the dividend history,
but the currency basis of those amounts is unclear, and a fee cannot be derived at all, so both read as
not stated. **What changes it:** a human-verified static row, or a provider that carries London fund
fees.

## The report sweep's Markdown half needs streamlit

The Markdown exports are built in `app.py`, which imports streamlit, so the sweep
(`tests/test_report_sweep.py`) can only check them where streamlit is installed. CI installs the `ui`
extra for exactly this reason (Batch 18B), and on CI a missing Markdown export fails the sweep rather
than skipping it. A bare developer checkout without the extra skips that half.

## Gap Ledger still prints "(s)" in a few lines

Gap Ledger is a separate tool under a feature freeze (only bug fixes until 40 scored days exist), so
its report wording was not touched by the Batch 18B plural clean-up. The Aristos reports, exports and
screens use the single `plurals.plural` helper everywhere.

## The narrator may still name a lens by its key

The council's structured evidence attributes each verdict to a lens by id (it has to), so a narration
can in principle repeat an id in its own prose. Reader text produced by the code (reports, tables,
headings, exports) carries no ids; narration prose is not covered by the sweep because the sweep runs
ranker-only. A narration check for ids is not built.
