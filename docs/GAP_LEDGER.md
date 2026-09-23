# Gap Ledger (GAP-LEDGER-1)

A daily pre-market shortlist of US stocks moving on news, with every pick logged and scored
afterwards. **Maths picks the names.** An LLM may write one optional line of prose about
headlines it was handed, and nothing else. No trading, no recommendations, no verdicts — the
output is a list and a record.

It lives beside Aristos and shares its adapters, its market index and its `.env`. It shares
nothing else: no Aristos surface imports it, no council is convened, no strategy is loaded,
and no verdict is written. A gap is a fact about a morning, not a thesis.

```
src/aristos_council/gap_ledger/     the package
gap_ledger_app.py                   the read-only viewer (streamlit run gap_ledger_app.py)
start_gap_ledger.bat                the Windows launcher
data/local/gap_ledger/YYYY-MM-DD.csv  one record per trading day (gitignored)
```

## The three commands

```bash
python -m aristos_council.gap_ledger run        # the pre-market screen, 09:00 ET
python -m aristos_council.gap_ledger outcomes   # after the close, fill the four readings
python -m aristos_council.gap_ledger score      # candidates vs control group, all days
```

`run` writes the day's CSV and posts one Todoist task. `--explain` (off by default) adds one
cheap LLM call. `--no-news` skips the charged EODHD calls, `--no-todoist` skips the task,
`--dry-run` writes nothing, `--tickers <file>` screens a hand-written list instead of the
index, `--limit N` takes the first N names for a smoke test, `--date` / `--at` screen a past
morning.

Everything runs on **New York time**. The rest of this repo displays Europe/Berlin because
that is where the owner reads reports; a pre-market screen is a statement about a session,
and a session has one clock.

## What the screen does

**Step 1 — who is eligible.** The pool is every US common stock in the local market index,
read-only, on NYSE / NASDAQ / NYSE ARCA / AMEX. The index's location comes from
`market_index.yaml` (`root:`), never an assumed path. 5,972 names as of 2026-09-22. Market
cap is **not** read: a gap is not about size, and the index's cap column is best-effort, so
gating on it would silently drop every name the provider happened not to serve. ETFs are
excluded by asset kind — the index admits `Type == "Common Stock"` only, and
`universe.looks_like_fund` re-states the boundary for a `--tickers` file, which has no index
row behind it.

**Warrants, units, rights and preferreds are dropped here (GAP-UNIVERSE-1)** — the market
index classifies on EODHD's `Type` field, which calls all of them "Common Stock" (196 of
5,972 US rows), so the filter is on the ticker shape (`-WS`/`-WT`/`-W`, `-U`/`-UN`, `-R`/`-RI`,
`-P`/`-PR`/`-Px`, and `TFINP`-style five-letter preferreds whose four-letter root is listed
beside them). The index is **not** changed and **Aristos reads the same index**, so those rows
are still there for every other consumer; a genuine share class (`BF-B`, `AKO-A`, `CIG-C`,
`MKC-V`) is never dropped. The count and its breakdown by kind appear in the run summary, and
yfinance's per-ticker error lines are suppressed in favour of one "N names returned no data".

Then three thresholds on yesterday's daily bars: previous close ≥ $10, 20-session average
volume ≥ 1M shares, ≥ 250 trading days of history. The daily bars are cached per market day.

**Previous close is the dividend- and split-adjusted close** (`adj_close`). With
`auto_adjust=False` yfinance serves `Close` split-adjusted but not dividend-adjusted, so on
an ex-dividend morning the raw close sits a dividend above where the stock actually reopens
— a 2% payer would read as a 2% gap down on no news at all.

**Step 2 — the move.**

| reading | definition |
| --- | --- |
| gap | `(last pre-market price − previous close) / previous close`, keep \|gap\| ≥ 3% |
| relative pre-market volume | today's volume 04:00 ET → run time, ÷ the **median** of the same clock window over the prior 20 sessions; keep ≥ 3× |
| spread | `(ask − bid) / midpoint`; **marked**, never a reason to drop |

The volume window is **clamped at 09:30**. The brief says "04:00 ET to run time" and the
default run is 09:00, comfortably inside pre-market — but a run launched at 11:00 would
otherwise count ninety minutes of regular-session volume as pre-market volume, against a
baseline that never contained any. Past the open the window ends at the open, and the record
stamps `window_end_et` so the reader can see it did.

Median, not mean: one earnings morning in the baseline would otherwise raise the bar for the
next month. A prior session with no pre-market trade contributes a true 0 and belongs in the
median. A date is a *session* only if the regular hours traded — a partial feed can serve a
stray pre-market bar on a day the market never opened, and counting that would pull a zero
into the baseline.

A zero-width or crossed book (`ask ≤ bid`) is reported as **spread unknown**, not as a 0%
spread: it is a stale quote, not a free round trip. And a run for a **past** date does not
read the book at all — a book is a snapshot of now, so the mark is "spread unknown —
historical run". (Live, 2026-09-22: a backfill of 2026-09-21 marked RIOT "wide spread 52.42%"
from a pre-dawn book that had nothing to do with the morning being screened.)

**A gap is only evaluated when the price behind it is trustworthy (GAP-PRICE-TRUST-1).** The
first full run gave 98 candidates and roughly 80 were junk — XEL +11%, LNT +12% on no news,
dozens of spreads at 40-57% — because with no pre-market volume one odd print reads as a gap
and nothing contradicts it. The spread turned out *not* to be the test (ALNY: 1.99% spread and
junk, opening 21% away; VKTX: 7.28% and the day's most genuine mover), so it is a loose
configurable backstop at `max_trusted_spread` = 10%.

**What separates a real move from a stray print is tape density**, measured against the live
2026-09-22 tape for the twelve names above. yfinance *omits* a five-minute slot in which nothing
traded rather than forward-filling it, so the number of bars in a window **is** the number of
printed intervals:

| | bars in the final 30 min | bars in the 5h window |
|---|---|---|
| junk (7 names) | 1, 1, 1, 1, 1, 2, 3 | 2 – 13 |
| genuine (5 names) | 6, 6, 6, 6, 6 | 55 – 60 |

Six is the ceiling for a 30-minute window, so every genuine mover printed in *every* slot of the
final half hour. This is effectively a **volume proxy** — it partially restores the leg the
provider will not serve. So a price is trusted when it has at least `min_premarket_prints` = 2
prints, at least `min_confirm_prints` = 4 of the final six slots filled, and a last print within
`max_confirm_drift` = 5% of their average. Each failure abstains with its own reason — `single
pre-market print`, `last print not confirmed`, `spread above limit` — so the CSV says which test
fired. All are NOT-EVALUATED **markings**, never rejections, and an untrusted name never enters
the control group: an unbelievable price is a missing reading about a name, not a finding about it.

> Two thresholds are **deliberately not** what the brief sketched, because the live tape
> contradicted it. Counting *distinct prices* rather than prints does not catch a sparse tape at
> all (XEL's five bars carried five different prices), and a **1%** drift limit rejects genuine
> movers while passing strays — five of the seven junk names scored 0.000% drift, since a
> one-bar window agrees with itself perfectly, whereas VKTX scored 2.797% and ONON 1.128%. On
> the twelve cited names the shipped gate keeps all five genuine movers and abstains on all
> seven junk ones.

`outcomes` fills a `premarket_vs_open` column — the pre-market price against the 09:30 open, as
a signed fraction. That is how these thresholds get tuned, and old CSVs written before the
column still load (an absent value reads as missing, never as 0).

> **Open question — pre-market coverage.** On 2026-09-22, **234** step-1 survivors had no
> pre-market print at all, including **AIG, AMT and AFL**. That is suspicious for companies of
> that size and it is not yet explained; it may be a genuine absence of extended-hours trade,
> or a gap in what yfinance serves. The run summary states the count every day so the question
> stays visible instead of being inferred from a short list.

## Interactive Brokers verifies the gap (GAP-IBKR-1)

```bash
pip install -e ".[ibkr]"      # ib_async — optional, and only Gap Ledger uses it
```

yfinance publishes no pre-market volume, so everything above is inference. A local **IB Gateway**
(read-only, port 4001) serves 5-minute TRADES bars *with* volume, and every name yfinance says
gapped is checked against it — **including the ones the trust tests abstained on**, because those
are exactly the cases IB can settle.

| | |
|---|---|
| **check 1**, one request per name | today's bars: IB's own last pre-market price, its volume 04:00 ET → run time, and the gap against the adjusted previous close already held from yfinance. No trades, or a gap inside the threshold, and the name is **rejected** — `IBKR: no real pre-market move`. That is a reading, not an abstention: IB looked and there was nothing there. |
| **check 2**, survivors only | the 20-session baseline in **one** request (5-minute bars over two months — the allowed pairs were probed, and 5-minute bars keep the clock window identical to the yfinance path for any run time, where 30-minute bars would force a floored comparison). Relative volume is finally **evaluated**: ≥ 3× passes, below fails. |
| **spread** | a brief *streaming* quote per surviving name (`snapshot=False`, `regulatorySnapshot=False` — never the paid one-off), cancelled immediately. |

**A verified name overrides yfinance** — its price, volume and spread replace yfinance's, and the
tape-density trust tests do not apply to it. Those tests exist only because the volume was
missing; where it is measured they are a worse proxy for the same thing. `source` on the row says
`ibkr` or `yfinance`, and IB's own readings are kept beside the screen's (`ib_last_price`,
`ib_gap_pct`, `ib_premarket_volume`, `ib_baseline_median`, `ib_relative_volume`, `ib_bid`,
`ib_ask`) so a disagreement between providers stays visible. Old CSVs load unchanged.

**If the Gateway is unreachable the run proceeds on yfinance** with the trust tests and says so
once — `!! IBKR UNAVAILABLE: volume not checked` — in the report, the CSV (`ibkr_note`), the
Todoist task and the viewer. Never fatal. `--no-ibkr` does the same deliberately.

Two measured facts about this data. **IB volume is shares, but a partial tape**: summed against
consolidated daily volume it came to 0.34×–0.67×, varying by day, so an absolute IB volume
threshold is unsafe and an IB figure must never be compared with another provider's — a ratio of
IB windows is the right construction. And **this subscription does not cover API streaming
quotes** (error 10089; historical bars work fine), so the spread abstains with that reason rather
than substituting delayed data; the code is ready for the day it is added.

> **Licence boundary.** IBKR data is licensed for the owner's personal, non-professional use. It
> stays inside `gap_ledger/`: **nothing in Aristos imports `gap_ledger.ibkr`**, and a test
> asserts it (`test_gap_ledger_verify.py`). A lens that ranked on it would be redistributing it.

**Two fetch passes.** The relative volume needs twenty sessions of 5-minute bars per name;
fetching that for every liquid US stock, to produce a list of a handful, is about two orders
of magnitude more data than the run needs. So pass A reads today's bars for every step-1
survivor (→ the gap) and pass B reads twenty sessions for the gappers only (→ the relative
volume). Pass B's window includes today, so the maths is identical to a single-pass run. The
order of the filters is unchanged: a name is a candidate only if it clears both.

## ⚠ yfinance does not publish pre-market volume

**Probed 2026-09-22, and it governs how this screener behaves.** yfinance serves pre-market
**prices** but never pre-market **volume**: every extended-hours bar comes back with
`Volume == 0`. Confirmed on AMD and TSLA, at 5-minute and 1-minute intervals, through both
`yf.download` and `Ticker.history`. The prices are real — AMD's last pre-market print on
2026-09-21 was 583.89 and the 09:30 open was 583.94 — the volume simply is not published.

So the relative-volume ratio is a **NOT-EVALUATED** reading on this provider, and
[hard project rule 3](../CLAUDE.md) governs: null is not false, and a missing input may
never act as a confirmed fail. The name is **kept and marked**, exactly the treatment the
screen already gives a missing spread and a missing headline.

- A ratio that **can** be computed and falls below 3× still **fails** and still drops the
  name. The filter is in full force wherever the data exists; it abstains only where the
  provider is silent.
- The absence is said **once, at the run** (`!! DATA GAP — …`), not as N identical per-name
  faults, and it reaches every surface: the report, the `relative_volume_note` column, the
  Todoist task, and a warning in the viewer.
- `GapConfig.require_relative_volume = True` restores the brief's literal reading. On
  yfinance alone that produces an **empty list every day**, which is why it is not the
  default.

The first real run found this: 50 liquid US names, 7 gappers, and **zero candidates**, every
one abstaining on the identical message. Left alone it would have been the VALBAND-1 failure
repeated exactly — a green suite, a registered feature, and nothing in the output, for a
reason no report ever stated.

**To get the volume leg back**, a provider that publishes extended-hours volume is needed.
EODHD intraday is explicitly not on the current plan.

## The record and the control group

`data/local/gap_ledger/YYYY-MM-DD.csv`, 42 columns, one row per name per day, gitignored.
Every candidate with all its numbers, flags and links — plus an **equal-size control group**.

The control group is the point of the file. "Gapping names carried on 58% of the time" is not
a finding; it is a fact about the market that week. The answerable question is whether the
screen's names carried on *more often* than comparable names that did not qualify, and that
needs the comparison logged on the same days, from the same pool, at the same checkpoints.
Drawing it from step-1 survivors holds price, liquidity and history roughly fixed, so what
differs is the gap — which is what the screen selects on.

It is drawn **at random with a seed fixed by the date**, from the pool sorted first
(`random.sample` over an unordered set answers differently per process, which would defeat
the seed). Reproducible without being cherry-picked. Restricted to names whose gap could
actually be *read*: a name that did not trade pre-market has no direction and can never be
scored either way.

Outcome columns are written **empty** and filled by `outcomes` after the close — so empty
means "not filled in yet", and a blank is never read as a zero.

## Scoring

`outcomes` fills four same-day raw readings per logged name, candidates and control alike:
the regular-session open, the price at 10:00 ET, the price at 11:30 ET, and the close. A
checkpoint price is the **open of the first bar starting at or after** that time, within 15
minutes — beyond that it reads as missing rather than as a 10:00 price that was nothing of
the sort. `outcomes` refuses a session that has not closed.

`score` asks, at 10:00, 11:30 and the close:

```
continued  ==  sign(checkpoint price − open)  ==  sign(gap)
```

**From the open, not from the previous close.** The gap is already realized by the bell —
nobody in this record traded it — so measuring from the previous close would count it twice
and would score positive on a name that opened up 8% and fell all morning. A flat reading is
**not** a continuation, and a missing price is **not** a failure to continue (it shrinks the
denominator).

Below **40 scored days** the verdict is "not enough days" and no rate is presented as a
finding. A fortnight of mornings is a mood, not evidence.

## News and the optional reason line

For each candidate, EODHD News over the last 18 hours: headline, publisher, timestamp, link.
**A story counts as this name's news only on positive evidence (GAP-NEWS-MATCH-1)** — the
provider's own primary symbol, or the ticker in the headline (3+ characters), or the company's
name as the index spells it, short forms included ("Alnylam reports…", "Ford recalls…") but
never an ordinary leading word on its own ("American", "Capital"). EODHD's `symbols` is a
loose tag list, so without this one AXT Inc. headline arrived as news for AXTI, CPRI and DK at
once, ONON got a Quest/Labcorp article and JAZZ got an Iambic story. Everything unattributed
is kept in the CSV as **"related, not matched"** (`related_count`/`related_headline`/
`related_link`) and never printed as the name's news, never shown to the model; `news_match`
records *how* the printed story was attributed. With `--explain` off the header says "Reason
line: off" once and no row carries a reason — `no clear reason found` is reserved for an
`--explain` run that asked and came back empty.

"no news found" is a **mark, never a drop** — a stock up 9% with no headline in the feed is
the case where the reason is not public yet, which is exactly the one worth seeing. The
publisher is **derived from the link's host**; EODHD's news rows carry no publisher field,
and inventing one would be a claim the data does not support.

`--explain` is off by default. When on, **one** call per run turns the already-fetched
headlines into one plain-English line per name. The fence is structural, not a polite
request:

- it never picks a name — the list is decided before the module is reached, and any ticker
  the model invents is discarded;
- it never produces a number — the prompt contains headlines only, so there is no figure in
  scope;
- a name with no headlines is never put to the model — it gets `no clear reason found`
  without a call;
- every line cites the link it rests on;
- a model failure degrades to `no clear reason found` **with a stated note**. Silence is the
  one unacceptable outcome: an absent line cannot be told from a line never asked for.

## Delivery

One Todoist task per run with candidates, in the project **Gap Ledger**, from
`TODOIST_API_TOKEN` in the local `.env`. Names, gap, relative volume, flags and headline
links. It speaks the unified **API v1** (`https://api.todoist.com/api/v1`) — `/rest/v2` is
retired and answered the first live run with HTTP 410 (GAP-TODOIST-1); list endpoints there
are paginated (`{"results": …, "next_cursor": …}`) and the project lookup follows the cursor,
because a "Gap Ledger" on page two would read as absent and create a second one every morning. **Nothing on an empty day** — a daily "no candidates" task trains you to ignore the
project. The project is created when it does not exist, and the outcome says so. A delivery
failure is reported and never fatal: the CSV is the record, Todoist is a convenience.

## The viewer

`streamlit run gap_ledger_app.py` — three tabs: **Today**, **Past days**, **Scorecard**.
`GAP_LEDGER_ROOT` overrides which directory it reads.

**It never starts a screen.** A screen costs news calls, optionally an LLM call, and posts a
Todoist task; a browser button firing all three on a stray click is not offered in this
version. The CLI is the trigger. A test asserts the absence structurally — the module does
not import the run entry at all.

## Thresholds

All of them live in `GapConfig` (`config.py`) and are stamped into every CSV row, so a
threshold change can never silently reinterpret yesterday's record.

| setting | default |
| --- | --- |
| `min_price` | $10 |
| `min_avg_volume` / `avg_volume_days` | 1,000,000 shares / 20 sessions |
| `min_history_days` | 250 trading days |
| `min_abs_gap` | 3% |
| `min_relative_volume` / `relative_volume_days` | 3× / 20 sessions |
| `require_relative_volume` | `False` — see the yfinance section above |
| `wide_spread` | 0.1% (marked, never dropped) |
| `news_lookback_hours` | 18 |
| `chunk_size` / `chunk_pause_seconds` | 40 tickers / 1.0s |
| `min_days_to_score` | 40 |

## Testing

`tests/test_gap_ledger_ibkr.py` needs the optional extra — the adapter imports `ib_async` at
call time (`_contract` reaches for `Stock`), so an injected IB handle is not enough to run it
without the module. It therefore opens with `pytest.importorskip("ib_async")`: a clean checkout
**skips** that file rather than failing it, and CI installs `.[dev,ibkr]` so there it runs.

No test reaches a live provider: `gap_ledger.bars.YFinanceBars` and `gap_ledger.ibkr.IBKRBars`
are both registered with the TEST-ISOLATION-1 guard in `tests/conftest.py` alongside the
market-data factories, so constructing one inside the suite raises. Tests inject `tests/gap_ledger_fakes.py`, whose
fakes also **record what they were asked for** — which is how the two-pass fetch and the
day-cache window rule are tested at all.

The day cache carries the VALBAND-1 scar deliberately: a cached series is served only when
the cached window **covers** the requested one. VALBAND-1 shipped green and produced
"insufficient history" for every name on every run because a day cache ignored the requested
window and served a shorter series someone else had asked for.
