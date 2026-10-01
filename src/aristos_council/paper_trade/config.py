"""PAPER-TRADE-1 — every constant this package uses, in one place, independent of
``aristos_council.gap_ledger.config`` (deliberately NOT imported from there — see the
package docstring).

Time is New York time throughout, for the same reason Gap Ledger's own config states: a
pre-market/opening-auction tool is a statement about a session, and a session has one clock.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# -- the Gateway ------------------------------------------------------------- #
# PAPER ONLY. 4001 is the LIVE Gateway and this package must never place an order there —
# ibkr_paper.PaperIBKR.connect refuses outright if the resolved port is this one, whatever
# the caller passed or set in the environment.
LIVE_PORT_FORBIDDEN = 4001
DEFAULT_PORT = 4002
DEFAULT_HOST = "127.0.0.1"
# Distinct from Gap Ledger's own IBKR_CLIENT_ID (17, see gap_ledger/ibkr.py) so the two
# packages' sessions can never collide if somehow run at the same moment.
DEFAULT_CLIENT_ID = 27
# IBKR's own prefix for a paper account id (e.g. "DU1234567"); a live account starts with
# "U". Checked on every connection before any order can be placed.
PAPER_ACCOUNT_PREFIX = "DU"

# -- direction, one spelling used everywhere in this package -------------------- #
LONG = "long"                       # gap up -> buy
SHORT = "short"                     # gap down -> sell short

# -- where things live -------------------------------------------------------- #
# READ-ONLY. Gap Ledger's own output; this package reads it and writes nothing there, ever.
GAP_LEDGER_ROOT = Path("data/local/gap_ledger")
# Everything THIS package writes. Gitignored (see .gitignore) — local, machine-specific,
# never personal-holdings data (ticker picks only, the same convention Gap Ledger's own
# record keeps).
DEFAULT_ROOT = Path("data/local/paper_trade")
STOP_FILE_NAME = "STOP"

# -- timing -------------------------------------------------------------------- #
# The opening-auction (MOO/LOO) cutoff is ~09:28 ET — too early for Gap Ledger's own run
# (which starts at 09:00 ET and is not reliably done by then). Owner's ruling: enter
# shortly after the open instead, and treat the resulting slippage as a headline number
# rather than pretend it does not exist.
ENTER_TARGET = time(9, 32)
# If today's CSV is not there yet at the target, keep checking up to this time, then trade
# at whatever time it actually showed up (recorded), or skip the day with a reason past it.
ENTER_DEADLINE = time(9, 40)
ENTER_POLL_SECONDS = 15.0

# NYSE's own MOC cutoff is 15:50 ET (no cancel/reduce after 15:45 ET) — submit comfortably
# before it.
EXIT_TARGET = time(15, 40)

# After the 16:00 ET close, enough margin for MOC fills and executions to settle on IBKR's
# side before ``record`` goes looking for them.
RECORD_AFTER = time(16, 10)

# -- sizing and order shape ----------------------------------------------------- #
POSITION_USD = 10_000.0
# A "marketable limit": far enough through the current quote that it should execute like a
# market order against visible size, without being a bare market order that can print at an
# arbitrarily bad price in a thin name.
MARKETABLE_PAD = 0.01
UNFILLED_CANCEL_SECONDS = 120.0
FILL_POLL_SECONDS = 2.0


def now_ny() -> datetime:
    return datetime.now(NY)


def today_ny() -> date:
    return now_ny().date()


def at_ny(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment, tzinfo=NY)


def root_dir(root: Path | str | None = None) -> Path:
    return Path(root) if root is not None else DEFAULT_ROOT


def stop_file(root: Path | str | None = None) -> Path:
    return root_dir(root) / STOP_FILE_NAME


def stopped(root: Path | str | None = None) -> bool:
    """The kill switch (PAPER-TRADE-1 hard rule): True if ``STOP`` exists under the output
    root. Checked before every order ``enter``/``exit`` might place."""
    return stop_file(root).exists()


@dataclass(frozen=True)
class Paths:
    """Every path one day's run touches, derived once so the four commands can never
    disagree about where a file lives."""

    root: Path
    day: date

    @property
    def gap_ledger_csv(self) -> Path:
        return GAP_LEDGER_ROOT / f"{self.day.isoformat()}.csv"

    @property
    def entries_json(self) -> Path:
        return self.root / f"{self.day.isoformat()}_entries.json"

    @property
    def exits_json(self) -> Path:
        return self.root / f"{self.day.isoformat()}_exits.json"

    @property
    def record_csv(self) -> Path:
        return self.root / f"{self.day.isoformat()}.csv"


def paths_for(day: date, root: Path | str | None = None) -> Paths:
    return Paths(root=root_dir(root), day=day)
