# How companies are classified, and how peers are chosen

A peer group answers one question: *who is this company fairly measured against?* That depends
on how companies are filed by industry, so this page says where the filing comes from, where it
can be wrong, and exactly how Aristos turns it into a peer group. Nothing here is a judgement about
a company; every rule is mechanical and every correction is dated, reasoned and shown on the page.

Code: `src/aristos_council/market_index.py` (`peers`, `clean_pool`). Companion pages:
`docs/MARKET_INDEX.md` (the table itself) and `docs/COHORTS.md` (rule-defined cohorts, which read
the same cleaned pool).

## 1. The two label systems in the index

Every row of the market index carries two independent kinds of industry label.

**GICS** is the Global Industry Classification Standard, maintained jointly by MSCI and S&P Dow
Jones Indices. It has four levels: 11 sectors, about 25 industry groups, about 74 industries and
about 160 sub-industries. A company is placed by its **main business**, judged mainly by revenue,
with earnings and market perception as secondary evidence. Aristos stores the GICS sector, industry
and sub-industry where the provider serves them.

**EODHD's own industry** is the provider's second label, in the style of Yahoo and Morningstar
("Drug Manufacturers - General", "Oil & Gas Integrated", "Semiconductors"). It is finer in some places
and coarser in others than GICS, and unlike GICS it has **no equivalent public rulebook**: the
labels are what the provider serves.

**A peer qualifies on either system, and each system is compared only with itself.** GICS is matched
against GICS and EODHD's industry against EODHD's; a GICS name is never matched against an EODHD
one because the words happen to coincide ("Semiconductors" is both an EODHD industry and a GICS
sub-industry). Either system alone is enough to admit a peer, so one wrong label does not hide a
rival: GICS files two grid-equipment makers under general machinery while EODHD files them with the
electrical-equipment group, and the second system finds what the first hides. A peer admitted on
**one** of the two systems only is marked **†** on the page ("counted as a peer on one industry
classification only"); the per-peer detail is kept in the run record.

## 2. Where the GICS rulebook is public, and where it is not

The **method** is published:

- S&P Dow Jones Indices, *GICS methodology* —
  <https://www.spglobal.com/spdji/en/documents/methodologies/methodology-gics.pdf>
- MSCI, *Global Industry Classification Standard (GICS) methodology* (August 2024) —
  <https://www.msci.com/indexes/documents/methodology/1_MSCI_Global_Industry_Classification_Standard_GICS_Methodology_20240801.pdf>
- S&P, *GICS mapbook* (the structure, level by level) —
  <https://www.spglobal.com/content/dam/spglobal/mi/en/documents/general/GICS-Mapbook-Brochure.pdf>

The **per-company list** — which company sits in which sub-industry today — is licensed data. Aristos
does not have it and does not pretend to: it uses **EODHD's copy** of GICS, which can be stale or
plainly wrong. Three cases found by looking at the peer lists themselves:

- **Shell** was filed under Oil & Gas Exploration & Production, which put ConocoPhillips, EOG and
  CNOOC in its peer group; the official classification is Integrated Oil & Gas.
- **Micron**'s US line was filed under semiconductor equipment; Micron makes memory chips.
- **Siemens Energy** was filed as general machinery; it makes grid and turbine equipment, the same
  business as GE Vernova and Vestas.

That is why the peer list is always **shown by name**. A group assembled from someone else's
classification can be wrong in ways no amount of internal consistency reveals, and the only honest
defence is to let the reader see who the company was measured against.

## 3. The peer ladder

`peers(ticker)` starts from the company's own labels and widens **only as far as it must**, saying how
far it went. It stops at the first step that finds at least **12** companies, and keeps at most **40**
(the nearest in size on a log scale, so half the size and twice the size are equally near).

| Step | Classification | Size band (USD market cap) |
|---|---|---|
| 1 | same GICS sub-industry, or same EODHD industry | ¼× – 4× |
| 2 | the same | ⅒× – 10× |
| 3 | same GICS industry, or same EODHD industry | ⅒× – 10× |
| 4 | same GICS **sector**, or same EODHD **sector** | ⅒× – 10× |

It widens the size band before the classification because keeping the industry is the cheaper
concession.

**Step 4 is a broad group, and says so.** Some companies sit in an industry with almost no
comparable company at any size: Nestlé's "Packaged Foods & Meats" has one, four and six comparable
companies at steps 1–3, while its sector holds 45. Rather than give such a company no peers, the
ladder tries the whole sector (the same ⅒×–10× band, the same floor of 12 and cap of 40 nearest in
size), and the page then reads "found at step 4 of 4 … **broad sector group - wider than a normal
peer group**". It only fires when steps 1–3 have all fallen under the floor, and financials still
meet only financials. Only if the sector also has fewer than 12 comparable companies does the ladder
abstain, naming the widest step it tried and how many companies it found. **Sizes are compared in
USD**, never in local currency: a 900bn-yen company is about 6bn dollars.

**One row per company.** A company listed on several exchanges, as an ADR, with several share classes
or with a preference line is one company, grouped by the identity handles the provider gives
(PrimaryTicker, ISIN) and, where none links them, by the same reduced company name with market caps
within 25%. The ordinary home line wins the seat; the rest are counted, not listed. A company is
never its own peer.

**What is left out of every pool** (kept in the table, so a reader can still look a company up):

- **Secondary trading lines**: depositary receipts of a company that has a home line (Brazilian BDRs,
  Canadian CDRs, London `0xxx` lines and GDRs, Swiss lines of foreign stocks), Hong Kong RMB counters
  and Korean preference shares.
- **Funds** listed as common stock, and rows whose classification contradicts their own name.
- **Size suspects**: a market cap more than 5× away from every other line of the same company while
  those lines agree with each other.
- **Excluded markets**: São Paulo, by one setting (`peer_exclude_markets` in `market_index.yaml`;
  delete the line to allow it back). About half its rows are receipts of foreign companies.
- **Financials, unless the subject is a financial**: a bank's balance sheet is its product, so
  leverage and EV-based measures mean something different for it.
- Companies with **no market cap** or **no USD conversion** (counted separately in the page's notes,
  so a broken FX rate is not hidden behind "missing data").

The result is deterministic for a given snapshot of the index, and the run saves the members, the
step, the snapshot date and how each peer matched, so a verdict can be read later against the list
it was measured on.

## 4. The correction files, and the rule they follow

Three small files in `data/` correct the provider's data where it is plainly wrong. They change one
field on **copies** of the rows a query reads; the table on disk is never rewritten.

| File | What it corrects |
|---|---|
| `data/label_overrides.yaml` | a company's GICS sub-industry |
| `data/identity_aliases.yaml` | which company a line belongs to (a self-alias says "this line is its own company") |
| `data/size_corrections.yaml` | a market cap no other line can refute: `set` a stated figure, or `exclude` the company |

**The rule.** A **label correction only restores the official GICS label**, and cites a public source —
an S&P or MSCI index factsheet, or the company's annual report. It is never an opinion about what a
company "really" is, and never made to improve a grade. An **identity** or **size** correction cites
the evidence that establishes it (a second line of the same company, an independent quoted figure).
When a figure cannot be established it is **excluded, not guessed**.

Every entry carries a **date and a reason**, and every use is **shown on the page**: in a peer list
the reasons name each label overridden, identity aliased or size corrected, and the Sources block at
the bottom of the page names the files a group actually drew on. In cohort lists the same corrections
carry symbols (†, ‡, §, ¶) with a legend; see `docs/COHORTS.md`.
