"""LENS-EXPAND-1b — "would have ranked": where a company WOULD sit on a lens that does not apply.

A lens's entry rules (a screen floor such as Growth's "revenue growth at least 10%") can exclude a
company. The exclusion is a verdict of record and stays exactly as it was. This module adds one
extra, clearly subordinate fact beside it: if the lens had ranked the company on its OWN ranking
factors against the same peer group, which position would it have taken?

What it is NOT, enforced by shape rather than by wording:

- not a vote: it is a ``WouldRank`` on a ``LensVote`` whose status stays "excluded"; the agreement
  counts read ``status``, never this.
- not a verdict: it carries a position and a cohort size and nothing else - no BUY / HOLD / SELL.
- not a track record: badges are attached only to ranked votes.
- not a council input as a verdict: the council's facts pack carries it in its own ``would_rank``
  field as the text "would rank ...", and the narration check flags any other use.

Pure and deterministic: factor values in, a position out. The peer group is every name that
reached the lens's entry rules (it passed the lens's scope gates - asset kind, size floor, sector)
whether or not it then passed them, so the comparison is against the whole group, never against
only the survivors of a rule that has just excluded the company.
"""

from __future__ import annotations

from aristos_council.plurals import plural

from dataclasses import dataclass
from typing import Optional

from .rank_engine import MIN_RANKABLE_COHORT, FactorSpec, cohort_positions, rank_universe
from .tools.valuation_band import ordinal

NOT_A_VOTE = "Not a vote."
UNAVAILABLE_PREFIX = "would-rank not available"


@dataclass(frozen=True)
class WouldRank:
    """The shadow reading for ONE company on ONE lens that excluded it."""

    available: bool
    position: Optional[int] = None
    cohort_size: int = 0
    reason: str = ""                  # why it is not available (plain words); "" when available

    @property
    def text(self) -> str:
        """The sentence a surface prints beside the exclusion reason."""
        if self.available:
            return (f"on its measures it would rank {ordinal(self.position)} of "
                    f"{self.cohort_size}. {NOT_A_VOTE}")
        return f"{UNAVAILABLE_PREFIX}: {self.reason}"

    def as_dict(self) -> dict:
        return {"available": self.available, "position": self.position,
                "cohort_size": self.cohort_size, "reason": self.reason, "text": self.text}


def _plain(source: str) -> str:
    """The reason inside a factor source tag ("abstained: gross profit unavailable" -> the part
    after the colon)."""
    return source.split(":", 1)[1].strip() if ":" in (source or "") else (source or "").strip()


def would_rank(strategy, ranked, pool: dict, ticker: str) -> Optional[WouldRank]:
    """Where ``ticker`` would rank on ``strategy``'s factors among the peer group, or None when
    the company is not an entry-rule exclusion (nothing to say).

    ``ranked`` is the lens's live ranked names (``RankedTicker``); ``pool`` maps every OTHER name
    that reached the entry rules and was excluded by them to ``(factor_values, factor_sources)``.
    The company must itself be in ``pool`` - that is what "its entry rules excluded it" means here;
    a company excluded by a scope gate (a fund in a stock lens, below the size floor, a bank in a
    lens that leaves banks out) is not in it and gets no reading, because the lens does not measure
    that kind of company at all.

    Not available - never a guess - when any of the company's own factors cannot be computed (the
    reason names which) or when the peer group is smaller than a rank can be called a comparison."""
    entry = pool.get(ticker)
    if entry is None:
        return None
    values, sources = entry
    names = [f.name for f in strategy.factors]
    missing = [n for n in names if values.get(n) is None]
    if missing:
        why = "; ".join(f"{n.replace('_', ' ')} - {_plain(sources.get(n, '')) or 'not computed'}"
                        for n in missing)
        return WouldRank(False, reason=why)
    rows = [(r.ticker, dict(r.factor_values)) for r in ranked if not r.excluded]
    rows += [(t, dict(v)) for t, (v, _) in pool.items()]
    specs = [FactorSpec(f.name, f.direction, f.missing) for f in strategy.factors]
    result = rank_universe(rows, specs, cut=strategy.cut, k=strategy.k,
                           percentile=strategy.percentile, missing=strategy.missing)
    live = [r for r in result if not r.excluded]
    positions = cohort_positions(live)
    if ticker not in positions or len(live) < MIN_RANKABLE_COHORT:
        return WouldRank(False, cohort_size=len(live),
                         reason=f"only {plural(len(live), 'company', 'companies')} could be compared "
                                f"(needs {MIN_RANKABLE_COHORT})")
    return WouldRank(True, position=positions[ticker][0], cohort_size=len(live))
