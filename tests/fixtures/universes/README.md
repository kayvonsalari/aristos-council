# Cohort fixtures (formerly `universes/*.yaml`)

These five manifests were the SHIPPED demo cohorts until FUND-UI-2. The app ships no
demo lists any more — a universe is a plain, editable ticker list you save yourself
(`universes/local/`) — but the files are kept here, verbatim and unedited, because
several tests and validation scripts are only meaningful against these exact members:

| manifest | who still reads it |
| --- | --- |
| `growth_40_v1` | `test_universe`, `test_etf_baselines_mirror` (ETF-lens-on-stocks kind-leak mirror), `scripts/check_ev_fields.py`, `scripts/scout_verdicts.py`, `examples/etf_baselines.py` |
| `defensive_16_v1` | `test_universe` (the validated trap-control bench) |
| `defensive_income_16_v1` | `test_universe`, `acceptance_check.py` — the graded scoreboard cohort in `snapshots/verdict_consensus.csv` |
| `financials_16_v1` | `test_company_check` (the frozen reference cohort a Company Check quotes) |
| `energy_watch_v1` | `test_demo_surface` / `acceptance_check.py` — the never-graded observation role |

They are fixtures, not product data: nothing in `app.py` reads this directory. A past
scoreboard row that names one of these ids stays resolvable here, and every run recorded
from FUND-UI-2 onward carries its own membership anyway (`universe_members` +
`universe_member_hash` in the run meta), so a run record no longer depends on a manifest
surviving at all.
