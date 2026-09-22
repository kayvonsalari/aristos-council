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
links. **Nothing on an empty day** — a daily "no candidates" task trains you to ignore the
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

No test reaches a live provider: `gap_ledger.bars.YFinanceBars` is registered with the
TEST-ISOLATION-1 guard in `tests/conftest.py` alongside the market-data factories, so
constructing one inside the suite raises. Tests inject `tests/gap_ledger_fakes.py`, whose
fakes also **record what they were asked for** — which is how the two-pass fetch and the
day-cache window rule are tested at all.

The day cache carries the VALBAND-1 scar deliberately: a cached series is served only when
the cached window **covers** the requested one. VALBAND-1 shipped green and produced
"insufficient history" for every name on every run because a day cache ignored the requested
window and served a shorter series someone else had asked for.
