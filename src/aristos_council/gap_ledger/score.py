"""GAP-LEDGER-1 step 4c — did the move carry on, and did it carry on MORE than the control?

One question, asked at three checkpoints (10:00 ET, 11:30 ET, the close):

    from the OPEN, did the price keep going in the gap's direction?

From the open, not from the previous close. The gap itself is already realized by the time
the bell rings — nobody in this record traded it — so "did the gap continue" as measured
from the previous close would count the gap twice and would score positive on a name that
opened up 8% and fell all morning. Measured from the open it answers the only question the
list can actually raise: having seen the pre-market move, did the rest of the session go
the same way?

    continued  ==  sign(checkpoint price - open)  ==  sign(gap)

A flat reading (checkpoint exactly equal to the open) is NOT a continuation. It is counted
in the denominator and not in the numerator, which is the conservative reading — the one
that cannot flatter the screen.

Both groups are scored identically, and the answer is always a PAIR. A candidate rate on
its own is a fact about the market that month; the difference against names that passed the
liquidity filter but not the gap-and-volume filter is the only part that is about the
screen. Below ``min_days_to_score`` days the verdict is "not enough days" and no rate is
presented as a finding, because a fortnight of mornings is a mood, not evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from statistics import mean, median
from typing import Iterable, Optional, Sequence

from .config import DEFAULT_CONFIG, GapConfig
from .ledger import GROUP_BASELINE, GROUP_CANDIDATE, LedgerRow
from .outcomes import SPY_COLUMN, directional_move, relative_to_market, signal_moves
from .verify import SOURCE_IBKR

# The checkpoints, in the order they happen. The close is included as the third: it is
# logged for every name anyway, and "did it hold all day" is the question the two morning
# checkpoints cannot answer.
CHECKPOINT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("10:00 ET", "price_1000"),
    ("11:30 ET", "price_1130"),
    ("the close", "close_price"),
)


def continued(row: LedgerRow, column: str) -> Optional[bool]:
    """Did ``row`` carry on in the gap's direction at ``column``?

    None means NOT SCOREABLE — no direction, no open, or no checkpoint price. Three-valued
    on purpose: a missing checkpoint is not a failure to continue, and counting it as one
    would penalise exactly the thin, hard-to-fill names.
    """
    price = getattr(row, column, None)
    if row.direction == 0 or row.open_price is None or price is None:
        return None
    return (price - row.open_price) * row.direction > 0


@dataclass(frozen=True)
class MoveStats:
    """A set of directional moves, summarised. ``n == 0`` means nothing was scoreable."""

    n: int = 0
    mean: Optional[float] = None
    median: Optional[float] = None
    positive: int = 0

    def sentence(self) -> str:
        if self.n == 0:
            return "no scoreable names"
        return (f"n={self.n}: mean {self.mean * 100:+.2f}%, median {self.median * 100:+.2f}%, "
                f"{self.positive} of {self.n} up")


def move_stats(values: Iterable[Optional[float]]) -> MoveStats:
    """Mean, median and how many were positive. A missing value is skipped, never a 0."""
    kept = [v for v in values if v is not None]
    if not kept:
        return MoveStats()
    return MoveStats(n=len(kept), mean=mean(kept), median=median(kept),
                     positive=sum(1 for v in kept if v > 0))


@dataclass(frozen=True)
class GroupRate:
    """How often one group carried on at one checkpoint."""

    scored: int = 0
    carried: int = 0

    @property
    def rate(self) -> Optional[float]:
        """The fraction, or None when nothing was scoreable. Never 0.0 for "no data"."""
        return None if self.scored == 0 else self.carried / self.scored

    def sentence(self) -> str:
        if self.scored == 0:
            return "no scoreable names"
        return f"{self.carried}/{self.scored} ({self.rate * 100:.0f}%)"


@dataclass(frozen=True)
class CheckpointScore:
    """One checkpoint, both groups."""

    label: str
    candidates: GroupRate
    baseline: GroupRate
    # GAP-MARKET-BENCH-1 — how far each group MOVED from the open in the gap's direction (raw),
    # and how far beyond the market (SPY) it moved over the same span (relative).
    candidate_move: MoveStats = field(default_factory=MoveStats)
    baseline_move: MoveStats = field(default_factory=MoveStats)
    candidate_vs_spy: MoveStats = field(default_factory=MoveStats)
    baseline_vs_spy: MoveStats = field(default_factory=MoveStats)

    @property
    def edge(self) -> Optional[float]:
        """Candidate rate minus control rate, or None when either side has nothing."""
        a, b = self.candidates.rate, self.baseline.rate
        return None if a is None or b is None else a - b

    def market_lines(self) -> list[str]:
        """Two lines: the raw move, and the move beyond the market, candidates then control."""
        def pair(mine, theirs) -> str:
            return f"candidates {mine.sentence()}; control {theirs.sentence()}"
        return [f"{self.label} raw move from the open: "
                + pair(self.candidate_move, self.baseline_move),
                f"{self.label} beyond SPY: "
                + pair(self.candidate_vs_spy, self.baseline_vs_spy)]

    def sentence(self) -> str:
        line = (f"{self.label}: candidates {self.candidates.sentence()}, "
                f"baseline {self.baseline.sentence()}")
        if self.edge is not None:
            line += f" — edge {self.edge * 100:+.0f} points"
        return line


@dataclass(frozen=True)
class Scorecard:
    """The whole record, scored. ``verdict`` is the plain-English answer."""

    days_logged: int = 0
    days_scored: int = 0
    candidates: int = 0
    baseline: int = 0
    checkpoints: tuple[CheckpointScore, ...] = ()
    enough_days: bool = False
    min_days: int = DEFAULT_CONFIG.min_days_to_score
    first_day: Optional[date] = None
    last_day: Optional[date] = None
    # GAP-EARLY-CHECKPOINT-1 — acting at the first signal against acting at the open.
    early: Optional["EarlyScore"] = None

    @property
    def verdict(self) -> str:
        if not self.enough_days:
            return (f"Not enough days: {self.days_scored} scored of "
                    f"{self.min_days} needed. No rate here is a finding yet.")
        if not self.checkpoints:
            return ("Nothing scoreable yet — days are logged but no outcome has been "
                    "filled in. Run `outcomes` for those days.")
        ahead = [c for c in self.checkpoints if (c.edge or 0) > 0]
        behind = [c for c in self.checkpoints if (c.edge or 0) < 0]
        if len(ahead) == len(self.checkpoints):
            head = ("The screen's names carried on more often than the control group at "
                    "every checkpoint.")
        elif not ahead:
            head = ("The screen's names did NOT carry on more often than the control "
                    "group at any checkpoint.")
        else:
            head = (f"Mixed: the screen's names led the control group at "
                    f"{len(ahead)} of {len(self.checkpoints)} checkpoints and trailed at "
                    f"{len(behind)}.")
        return (f"{head} {self.days_scored} scored days"
                + (f", {self.first_day} to {self.last_day}." if self.first_day else "."))

    def lines(self) -> list[str]:
        out = [f"Days logged: {self.days_logged}; days with filled outcomes: "
               f"{self.days_scored}.",
               f"Names scored: {self.candidates} candidates, {self.baseline} baseline."]
        out += [f"  {c.sentence()}" for c in self.checkpoints]
        out.append(self.verdict)
        market = [line for c in self.checkpoints for line in c.market_lines()]
        if market:
            out += ["", "Against the market (SPY), in the gap's direction, from the open:"]
            out += [f"  {line}" for line in market]
        if self.early is not None:
            out.append("")
            out += self.early.lines()
        return out


# --------------------------------------------------------------------------- #
# GAP-EARLY-CHECKPOINT-1 — is acting earlier in the pre-market worth anything?
# --------------------------------------------------------------------------- #
# The exits each entry is measured to. The close is the one both share, so it is the comparison.
_SIGNAL_EXITS = ("to 09:00", "to the open", "to the close")
_OPEN_EXITS = (("to 10:00", "price_1000"), ("to 11:30", "price_1130"),
               ("to the close", "close_price"))
CLOSE_LABEL = "to the close"


@dataclass(frozen=True)
class EarlyScore:
    """Acting at the first signal against acting at the open, on the SAME names.

    Only IBKR-verified candidates can have a first signal, so this is a smaller population than
    the scorecard above it, and both entries are measured over the names that have BOTH a first
    signal and an open and close — a paired comparison, so a difference is about the entry time
    and not about which names happened to be in each side.
    """

    verified: int = 0                 # IBKR-verified candidates
    signalled: int = 0                # ...of which a first signal was found
    paired: int = 0                   # ...of which the open and close are filled in too
    days: int = 0
    min_days: int = DEFAULT_CONFIG.min_days_to_score
    at_signal: tuple = ()             # ((label, MoveStats), ...)
    at_open: tuple = ()
    first_day: Optional[date] = None
    last_day: Optional[date] = None

    @property
    def enough_days(self) -> bool:
        return self.days >= self.min_days

    @staticmethod
    def _close(entries: tuple) -> MoveStats:
        return next((stats for label, stats in entries if label == CLOSE_LABEL), MoveStats())

    @property
    def verdict(self) -> str:
        if not self.enough_days:
            return (f"Not enough days: {self.days} with a first signal of {self.min_days} "
                    f"needed. No figure here is a finding yet.")
        early, late = self._close(self.at_signal), self._close(self.at_open)
        if early.mean is None or late.mean is None:
            return "Nothing scoreable yet — no name has a first signal with an open and close."
        diff = (early.mean - late.mean) * 100
        worth = "was worth" if diff > 0 else "was NOT worth"
        return (f"Acting at the first signal {worth} more than acting at the open: a mean "
                f"{early.mean * 100:+.2f}% to the close against {late.mean * 100:+.2f}% "
                f"({diff:+.2f} points), {self.paired} names over {self.days} days.")

    def lines(self) -> list[str]:
        out = ["Acting at the first signal vs acting at the open "
               "(IBKR-verified candidates only, in the gap's direction):",
               f"  {self.verified} verified, {self.signalled} with a first signal, "
               f"{self.paired} with an open and close as well — {self.days} of "
               f"{self.min_days} trading days."]
        out += [f"  first signal {label}: {stats.sentence()}" for label, stats in self.at_signal]
        out += [f"  the open {label}: {stats.sentence()}" for label, stats in self.at_open]
        out.append(self.verdict)
        return out


def early_score(candidates: Sequence[tuple], *,
                config: GapConfig = DEFAULT_CONFIG) -> Optional[EarlyScore]:
    """The early-checkpoint comparison over ``(day, row)`` pairs, or None when no candidate was
    IBKR-verified at all (an old ledger, or a run of yfinance-only days) — the section is then
    simply absent rather than a block of zeros that reads as a finding."""
    verified = [(d, r) for d, r in candidates if r.source == SOURCE_IBKR]
    if not verified:
        return None
    signalled = [(d, r) for d, r in verified if r.first_signal_price is not None]
    paired = [(d, r) for d, r in signalled
              if r.open_price is not None and r.close_price is not None and r.direction != 0]
    at_signal = tuple((label, move_stats(signal_moves(r)[i] for _d, r in paired))
                      for i, label in enumerate(_SIGNAL_EXITS))
    at_open = tuple((label, move_stats(
        directional_move(r.open_price, getattr(r, column), r.direction) for _d, r in paired))
        for label, column in _OPEN_EXITS)
    day_set = sorted({d for d, _r in paired})
    return EarlyScore(verified=len(verified), signalled=len(signalled), paired=len(paired),
                      days=len(day_set), min_days=config.min_days_to_score,
                      at_signal=at_signal, at_open=at_open,
                      first_day=day_set[0] if day_set else None,
                      last_day=day_set[-1] if day_set else None)


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #
def _rate(rows: Iterable[LedgerRow], column: str) -> GroupRate:
    scored = carried = 0
    for row in rows:
        answer = continued(row, column)
        if answer is None:
            continue
        scored += 1
        carried += 1 if answer else 0
    return GroupRate(scored=scored, carried=carried)


def _moves(rows: Iterable[LedgerRow], column: str) -> MoveStats:
    """Each row's move from the open to ``column`` in the gap's direction, summarised. Computed
    from the prices, not read from the stored ``move_*`` column, so a CSV filled before that
    column existed scores exactly the same."""
    return move_stats(directional_move(r.open_price, getattr(r, column), r.direction)
                      for r in rows)


def _vs_market(rows: Iterable[LedgerRow], column: str) -> MoveStats:
    """The same, minus SPY's move over the same span (in the gap's direction). A row on a day
    with no SPY reading is skipped for THIS number only, never counted as a zero."""
    return move_stats(relative_to_market(
        directional_move(r.open_price, getattr(r, column), r.direction),
        getattr(r, SPY_COLUMN[column]), r.direction) for r in rows)


def score(days: dict[date, Sequence[LedgerRow]], *,
          config: GapConfig = DEFAULT_CONFIG) -> Scorecard:
    """Score every logged day. ``days`` maps a market date to that day's rows."""
    candidates = [r for rows in days.values() for r in rows if r.group == GROUP_CANDIDATE]
    baseline = [r for rows in days.values() for r in rows if r.group == GROUP_BASELINE]

    scored_days = sorted(day for day, rows in days.items()
                         if any(continued(r, column) is not None
                                for r in rows for _, column in CHECKPOINT_COLUMNS))
    checkpoints = tuple(CheckpointScore(
        label=label, candidates=_rate(candidates, column), baseline=_rate(baseline, column),
        candidate_move=_moves(candidates, column), baseline_move=_moves(baseline, column),
        candidate_vs_spy=_vs_market(candidates, column),
        baseline_vs_spy=_vs_market(baseline, column))
                        for label, column in CHECKPOINT_COLUMNS
                        if _rate(candidates, column).scored
                        or _rate(baseline, column).scored)
    early = early_score([(day, r) for day, rows in days.items() for r in rows
                         if r.group == GROUP_CANDIDATE], config=config)
    return Scorecard(days_logged=len(days), days_scored=len(scored_days),
                     candidates=len(candidates), baseline=len(baseline),
                     checkpoints=checkpoints,
                     enough_days=len(scored_days) >= config.min_days_to_score,
                     min_days=config.min_days_to_score,
                     first_day=scored_days[0] if scored_days else None,
                     last_day=scored_days[-1] if scored_days else None, early=early)
