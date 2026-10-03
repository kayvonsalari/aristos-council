# Aristos Council — Acceptance Tests

Hand-test cases for a human to click through in Council Station (`streamlit run app.py`).
These are NOT a substitute for the automated suite (`python -m pytest`) — they exist for
the things a test can assert but a reader should still *see*: layout, wording, and the
overall feel of a flow. Each case: an ID (kept stable across edits — never renumber, only
append), the steps, the expected result, and a plain-English note on why it matters.

## TAB-MERGE-1 (parts 1 and 2)

The Run tab and Company Check were merged into one tab, **Analyse**, with an explicit
Company / Cohort-list switch (part 1); the page order, track-record badges, and
click-through from a list result were then reworked (part 2). See `CLAUDE.md`'s
Architecture section and the PR descriptions for the full design.

| ID | Steps | Expected result |
|---|---|---|
| **TM-T1** | Open the app fresh. | ONE tab named **Analyse** (plus **Scoreboard**) — no separate "Company Check" tab. Inside Analyse, an **Input** switch reading "Company" / "Cohort / list". |
| **TM-T2** | Look at the sidebar. | The Stocks/ETFs switch is labelled **"Asset type"**, not "Analyse" — it must not share the tab's own name. Default selection: **Stocks**. |
| **TM-T3** | In Company mode, look at the controls. Switch to Cohort / list, look again. | **Company mode:** the "Find a company" box and the Ticker box are present; there is no Narrate/cap/threshold/size-floor control anywhere (not greyed — ABSENT). **List mode:** the List selector, the ticker textarea and the "Advanced (list only)" expander (threshold + size floor) are present; there is no "Find a company" box. |
| **TM-T4** | Company mode: pick or type a company you know is under $5bn market cap. Then try one you know is over (e.g. EL.PA). | **Under $5bn:** an info line states the market cap and that the company is compared with other small companies, **before** you click Run; the results carry an "outside the tested range" caveat. **Over $5bn (EL.PA):** no such line anywhere. |
| **TM-T5** | Company mode: leave "Plain-English summary" and "Council opinion" both unticked. Run. | The run button says the run is **free**/deterministic. After running, your Anthropic usage/bill does not move. |
| **TM-T6** | Company mode: tick "Council opinion" on a company every ticked lens excludes (e.g. a tiny company below every lens's own screen). | The page shows a **"No lens voted"**-style note where the Council opinion would be, and the model bill does not move — the council makes no call when there is nothing to narrate. |
| **TM-T7** | Run a Company page with at least one lens ticked, the summary ticked, and Council opinion ticked. | The sections appear, top to bottom, on screen **and** in both downloads (`.txt` and `.html`), in this order: (1) Plain-English summary, (2) Verdict and lens votes, (3) Valuation band, (4) Price and cash, (5) Debt and growth, (6) What analysts say, (7) Council opinion, (8) the full peers table, (9) Sources. |
| **TM-T8** | On that same run, look at the Lens votes table. Find a lens whose row reads "does not apply — …". | A lens that actually voted (ranked the company) shows its track-record badge (e.g. "(proven here)"). A "does not apply" row shows **only its reason** — no badge text beside it anywhere. The "Track record: …" summary line under the agreement counts only the voted, badged lenses. |
| **TM-T9** | Run a list (2+ names, any lens). Under the results, find "Open a company page". Pick a name, click Open. | The Analyse tab's input switches to **Company**, the ticker box fills with that name — **nothing runs automatically** (the company run button is present, unclicked, still showing its own cost label). Press that button; once the page renders, it states near the top: "Ranked against its *N* industry peers, not against your list," plus a free "In your list: …" line naming that lens's rank and verdict **in the list you came from**. |
| **TM-T10** | Switch Asset type to ETFs. | The Company/Cohort-list switch is gone entirely (not greyed) — ETF mode is list-only (there is no per-fund peer group to check one against). An ETF list still runs normally. |
| **TM-T11** | Open a `.html`/`.txt` file saved **before** this merge from `reports/universe_runs/` or `runs/<stamp>_company_check_<TICKER>/`. | It opens and reads exactly as it did before — on-disk formats were kept byte-stable through the merge; nothing here re-renders an old file. |
| **TM-T12** | List mode: type or paste exactly **one** ticker into the list box. | A small caption appears: "Just **TICKER** — open this as a company page instead?" — a hint only; it does not switch anything on its own. |
| **TM-T13** | On the Company page's full peers table (bottom section), find the company's own row. | It carries the existing "(this company)" text marker **and** a visible highlight (a distinct background, bold text) — not just the text marker alone. |
| **TM-T14** | List mode: open the "Advanced (list only)" expander. | It holds exactly two controls: the spend-confirmation threshold and the company-size floor override. The Narrate-level / "Up to N" / "Skip names doubted by Forensic" controls are **not** inside it — they sit in their usual place, visible whenever the run narrates. |
| **TM-T15** | Company mode: type a ticker NOT in the local market index (so its size is unknown at pick time), one you happen to know is small. | The info line still appears once the size is resolved (a live, cached fetch) — with an extra sentence noting the size "was not known in advance; this was decided once its market cap was read." |
| **TM-T16** | List mode: run with "Council opinion" ticked but every ticked lens producing zero names to narrate (e.g. an empty agreement). | The free ranking is reported; no narration step runs and the bill does not move — an empty plan spends nothing. |

### What to check once per release, not per change

- Restart Streamlit; the Analyse tab opens on **Company** mode and **Stocks**, nothing
  remembered from the last session (session-only state, by design — see README "Stocks and
  ETFs").
- Download both a list run and a company run as `.md`/`.txt` and `.html`; confirm the two
  formats agree on every figure (one of REPORT-HTML-1's own invariants).
