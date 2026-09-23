"""GAP-LEDGER-1 — every threshold this screener uses, in one place.

The brief's numbers are the defaults, and they live here rather than being spelled at
their call sites so that "what did we screen on?" is a question with one answer. Every
run stamps the config it used into the day's CSV header fields, so a threshold change
never silently reinterprets yesterday's record.

Time is NEW YORK time throughout. The rest of this repo displays Europe/Berlin because
that is where the owner reads reports; a pre-market screen is a statement about a
session, and a session has one clock — ``America/New_York``. Nothing here converts to
Berlin, and nothing here uses ``date.today()`` (which is the machine's calendar, not the
market's).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

# The market's clock. One constant, used by the screen, the ledger filename, the outcome
# checkpoints and the news window alike.
NY = ZoneInfo("America/New_York")

# The pre-market window opens here. yfinance serves pre-market 5m bars from 04:00 ET on
# a normal session; earlier bars do not exist rather than being empty.
PREMARKET_OPEN = time(4, 0)
# The regular session. ``MARKET_OPEN`` also CLOSES the pre-market window: a run launched
# at 11:00 must not count regular-session volume as pre-market volume (see
# ``screen.premarket_window``).
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

# The brief's default run time: late enough that pre-market volume means something,
# early enough to be a pre-market list.
DEFAULT_RUN_TIME = time(9, 0)

# Outcome checkpoints, in ET. ``open`` and ``close`` come from the daily bar; these two
# are read off 5m bars.
CHECKPOINTS: tuple[time, ...] = (time(10, 0), time(11, 30))

# Where the day's CSV lands. Gitignored: it is a local record of a local run, and it
# grows one file per trading day.
DEFAULT_ROOT = "data/local/gap_ledger"

# The venues the brief admits. Deliberately a copy of the market index's US allow-list
# rather than a read of it: the index's list governs what gets FETCHED into the table,
# and this one governs what this screen is willing to look at. They agree today, and if
# the index ever admits OTC venues this screen still will not.
US_VENUES: tuple[str, ...] = ("NYSE", "NASDAQ", "NYSE ARCA", "AMEX")


@dataclass(frozen=True)
class GapConfig:
    """The thresholds, with the brief's defaults."""

    # -- step 1, the pre-filter (yesterday's daily bars) ------------------- #
    min_price: float = 10.0                 # yesterday's close, in dollars
    min_avg_volume: float = 1_000_000.0     # mean daily shares over ``avg_volume_days``
    avg_volume_days: int = 20
    min_history_days: int = 250             # trading days of daily history

    # -- step 2, the screen (today's pre-market) --------------------------- #
    min_abs_gap: float = 0.03               # |gap| >= 3%
    min_relative_volume: float = 3.0        # today's pre-market vs its 20-session median
    relative_volume_days: int = 20
    wide_spread: float = 0.001              # 0.1% — MARKED, never a reason to drop

    # -- GAP-PRICE-TRUST-1: is the pre-market price worth believing? -------- #
    #
    # The first full run produced 98 candidates and roughly 80 were junk: XEL +11%, LNT
    # +12% on no news, dozens of spreads at 40-57%. With no pre-market volume published
    # (see ``require_relative_volume``), ONE odd print reads as a gap and nothing
    # contradicts it.
    #
    # The spread is NOT the test. Measured on 2026-09-22: ALNY showed a 1.99% spread and
    # was junk — it opened 21% away from its pre-market price — while VKTX showed 7.28% and
    # was the day's most genuine mover. So the spread stays a configurable backstop at a
    # deliberately loose 10%, and the decisive tests are the two below.
    max_trusted_spread: float = 0.10
    #
    # MEASURED, on the twelve names the amendment named, against the live 2026-09-22 tape.
    # yfinance OMITS five-minute slots in which nothing traded (it does not forward-fill),
    # so the number of bars in a window IS the number of printed intervals, and tape
    # DENSITY is what separates a real pre-market move from a stray print:
    #
    #             bars in the final 30 min      bars in the 5h window
    #   junk      1, 1, 1, 1, 1, 2, 3           2 – 13
    #   genuine   6, 6, 6, 6, 6                 55 – 60
    #
    # A 30-minute window holds at most six five-minute bars, so a genuine mover printed in
    # EVERY slot of the final half hour and the junk printed in one to three. That is a
    # clean split with margin on both sides, and it is effectively a VOLUME PROXY — it
    # partially restores the leg this provider will not serve.
    #
    # (a) One print is not a price: BGC -23%, IRDM -25%, LKQ +26%, XEL +11%, LNT +12% were
    # all sparse tapes.
    min_premarket_prints: int = 2
    # (b) The last print must be CONFIRMED, which needs two things — enough prints around
    # it to confirm anything, and agreement with them.
    confirm_window_minutes: int = 30
    # The decisive one. Four of six slots, which clears the junk's worst (3) and sits below
    # every genuine name (6).
    min_confirm_prints: int = 4
    # NOTE, and a deliberate departure from the amendment's 1%: drift does NOT discriminate,
    # because a one-bar window agrees with itself perfectly. Five of the seven junk names
    # scored 0.000% drift and one scored 20.6%, while genuine VKTX scored 2.797% and ONON
    # 1.128% — so a 1% limit would have rejected two real movers and kept five stray prints.
    # It stays as a LOOSE backstop for an extreme last print inside an otherwise dense tape,
    # at a level no genuine name in the sample came near.
    max_confirm_drift: float = 0.05
    # Does a relative volume that CANNOT BE COMPUTED drop the name?
    #
    # False, and the reason is a fact about the data, probed on 2026-09-22: yfinance serves
    # pre-market PRICES but never pre-market VOLUME. Every 5-minute and 1-minute
    # extended-hours bar comes back with ``Volume == 0`` — confirmed on AMD and TSLA,
    # through both ``yf.download`` and ``Ticker.history``. The prices are real (AMD's last
    # pre-market print on 2026-09-21 was 583.89 and the 09:30 open was 583.94); the volume
    # simply is not published on this provider. See docs/GAP_LEDGER.md.
    #
    # So the ratio is a NOT-EVALUATED reading, and rule 3 governs: null is not false, and a
    # missing input may never act as a confirmed fail. Dropping a name because a number is
    # absent is exactly the bug class this repo has paid for most often. With this False the
    # name is KEPT and MARKED — the same treatment the brief gives a missing spread and a
    # missing headline — and the mark is on the row, in the report and in the Todoist task,
    # so a gap-only selection is never mistaken for a gap-and-volume one.
    #
    # A ratio that CAN be computed and comes in below ``min_relative_volume`` still FAILS
    # and still drops the name. The filter is in full force wherever the data exists; it
    # abstains only where the provider is silent. Set this True to require the reading (the
    # brief's literal wording), which on yfinance alone yields an empty list every day.
    require_relative_volume: bool = False

    # -- step 3, the reasons ---------------------------------------------- #
    news_lookback_hours: int = 18

    # -- fetching ---------------------------------------------------------- #
    chunk_size: int = 40                    # tickers per yfinance request
    chunk_pause_seconds: float = 1.0        # polite pacing between chunks

    # -- step 5, the scorecard --------------------------------------------- #
    min_days_to_score: int = 40             # below this the verdict is "not enough days"

    def as_record(self) -> dict:
        """The thresholds as flat strings, for the day's CSV."""
        return {
            "cfg_min_price": self.min_price,
            "cfg_min_avg_volume": self.min_avg_volume,
            "cfg_min_history_days": self.min_history_days,
            "cfg_min_abs_gap": self.min_abs_gap,
            "cfg_min_relative_volume": self.min_relative_volume,
            "cfg_require_relative_volume": str(self.require_relative_volume).lower(),
            "cfg_wide_spread": self.wide_spread,
            "cfg_max_trusted_spread": self.max_trusted_spread,
            "cfg_min_premarket_prints": self.min_premarket_prints,
            "cfg_min_confirm_prints": self.min_confirm_prints,
            "cfg_max_confirm_drift": self.max_confirm_drift,
            "cfg_news_lookback_hours": self.news_lookback_hours,
        }


DEFAULT_CONFIG = GapConfig()


def now_ny() -> datetime:
    """The current moment on the market's clock."""
    return datetime.now(NY)


def today_ny() -> date:
    """The market's calendar date, which is not always the machine's."""
    return now_ny().date()


def at_ny(day: date, moment: time) -> datetime:
    """A New-York-aware datetime for ``day`` at ``moment``."""
    return datetime.combine(day, moment, tzinfo=NY)
