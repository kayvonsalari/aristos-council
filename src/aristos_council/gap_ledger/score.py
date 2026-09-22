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

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional, Sequence

from .config import DEFAULT_CONFIG, GapConfig
from .ledger import GROUP_BASELINE, GROUP_CANDIDATE, LedgerRow

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

    @property
    def edge(self) -> Optional[float]:
        """Candidate rate minus control rate, or None when either side has nothing."""
        a, b = self.candidates.rate, self.baseline.rate
        return None if a is None or b is None else a - b

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
        return out


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


def score(days: dict[date, Sequence[LedgerRow]], *,
          config: GapConfig = DEFAULT_CONFIG) -> Scorecard:
    """Score every logged day. ``days`` maps a market date to that day's rows."""
    candidates = [r for rows in days.values() for r in rows if r.group == GROUP_CANDIDATE]
    baseline = [r for rows in days.values() for r in rows if r.group == GROUP_BASELINE]

    scored_days = sorted(day for day, rows in days.items()
                         if any(continued(r, column) is not None
                                for r in rows for _, column in CHECKPOINT_COLUMNS))
    checkpoints = tuple(CheckpointScore(label=label,
                                        candidates=_rate(candidates, column),
                                        baseline=_rate(baseline, column))
                        for label, column in CHECKPOINT_COLUMNS
                        if _rate(candidates, column).scored
                        or _rate(baseline, column).scored)
    return Scorecard(days_logged=len(days), days_scored=len(scored_days),
                     candidates=len(candidates), baseline=len(baseline),
                     checkpoints=checkpoints,
                     enough_days=len(scored_days) >= config.min_days_to_score,
                     min_days=config.min_days_to_score,
                     first_day=scored_days[0] if scored_days else None,
                     last_day=scored_days[-1] if scored_days else None)
