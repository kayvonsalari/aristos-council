"""COHORT-1 — the four quality checks, plus the source summary.

A cohort that builds is not yet a cohort worth ranking. These checks ask four questions a
reader of the eventual report would ask first, and each answers in one figure and one
plain sentence:

  1. How much of this cohort could the lenses not read?      (abstention rate)
  2. Is it all sitting in one corner of its own history?      (band spread)
  3. Would one name leaving change the answer?                (drop-one stability)
  4. Where did the anchors land?                              (anchor check, no judgment)

Every function here is PURE: it takes a finished ranker-only result and returns numbers.
Nothing fetches, nothing ranks over the network, and no model is imported — drop-one
re-ranks in memory from the factor values the run already produced, so N+1 rankings cost
one fetch, not N+1.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..rank_engine import RankedTicker, rank_universe
from .source import PATH_CONSTITUENTS, PATH_SCREENER, PATH_YFINANCE, Candidate

ABSTENTION_FLAG = 0.15          # more than 15% unreadable
BAND_CONCENTRATION_FLAG = 0.60  # more than 60% in one band
# "> 3 places on a cohort of 30, scale proportionally" — i.e. one tenth of the cohort.
STABILITY_FLAG_FRACTION = 3.0 / 30.0

BAND_LABELS = ("0–20 (cheapest vs own past)", "20–40", "40–60", "60–80",
               "80–100 (dearest vs own past)")


@dataclass(frozen=True)
class RankSetup:
    """Everything ``rank_universe`` needs, carried together.

    Drop-one re-ranks 30-odd times. It must re-rank the way the RUN ranked — same factors,
    same cut, same k, same missing-mode — or the "instability" it reports is its own
    settings disagreeing with the pipeline's. Passing these as one object is what stops a
    later caller from supplying four of the five.
    """

    factors: tuple
    cut: str = "quintile"
    k: int = 6
    percentile: float = 0.2
    missing: str = "worst"

    def rerank(self, rows):
        return rank_universe(list(rows), list(self.factors), cut=self.cut, k=self.k,
                             percentile=self.percentile, missing=self.missing)


@dataclass(frozen=True)
class Check:
    """One check: a name, one figure, whether it is flagged, and a plain sentence."""

    name: str
    figure: str
    flagged: bool
    sentence: str
    detail: tuple[str, ...] = ()

    def line(self) -> str:
        return ("⚠ " if self.flagged else "") + self.sentence


# --------------------------------------------------------------------------- #
# 1. abstention rate
# --------------------------------------------------------------------------- #
def abstention_rate(ranked: list[RankedTicker], unrateable: list[tuple[str, str]],
                    total: int) -> Check:
    """Share of the cohort the ranker could not fully read.

    A name counts as an abstention when it produced NO verdict at all (it is UNRATEABLE —
    no data, delisted) or when it was ranked with a hole: at least one factor value came
    back None and had to be imputed or sent to the worst rank. Both are the same thing
    from a reader's seat — a row whose position rests on something the data did not say.
    Counting only the first would flatter the cohort; counting only the second would miss
    the names that never made the table.
    """
    total = total or (len(ranked) + len(unrateable))
    holes = [r for r in ranked if any(v is None for v in r.factor_values.values())]
    n_abstain = len(unrateable) + len(holes)
    rate = (n_abstain / total) if total else 0.0
    flagged = rate > ABSTENTION_FLAG
    detail = tuple(sorted([t for t, _ in unrateable] + [r.ticker for r in holes]))
    return Check(
        "abstention rate", f"{rate:.0%}", flagged,
        f"{rate:.0%} of the cohort abstained: {len(unrateable)} name(s) the ranker could "
        f"not read at all and {len(holes)} ranked with at least one factor missing, out "
        f"of {total}." + (f" Above the {ABSTENTION_FLAG:.0%} line." if flagged else ""),
        detail)


# --------------------------------------------------------------------------- #
# 2. band spread
# --------------------------------------------------------------------------- #
def band_spread(ranked: list[RankedTicker]) -> Check:
    """Share of names per valuation band, and whether one band holds the cohort.

    A cohort where four names in five sit in the dearest fifth of their own history is not
    a cohort of cheap and dear names to choose between — it is one bet, and the ranker
    will sort within it without ever saying so. Names whose band ABSTAINED are counted
    separately and never folded into a bucket; a fabricated 50th percentile is exactly the
    lie the band was built to refuse.
    """
    buckets = [0] * 5
    abstained = 0
    for r in ranked:
        band = getattr(r, "valuation_band", None)
        pct = getattr(band, "percentile", None) if band is not None else None
        if pct is None:
            abstained += 1
            continue
        buckets[min(int(pct // 20), 4)] += 1
    placed = sum(buckets)
    if not placed:
        return Check("band spread", "no bands", False,
                     f"No valuation band could be measured for any of the {len(ranked)} "
                     f"ranked names, so the spread says nothing here.")
    shares = [b / placed for b in buckets]
    top = max(range(5), key=lambda i: shares[i])
    flagged = shares[top] > BAND_CONCENTRATION_FLAG
    spread = ", ".join(f"{BAND_LABELS[i].split(' ')[0]}: {shares[i]:.0%}" for i in range(5))
    return Check(
        "band spread", f"{shares[top]:.0%} in {BAND_LABELS[top]}", flagged,
        f"Valuation bands across {placed} name(s) — {spread}"
        + (f"; {abstained} abstained." if abstained else ".")
        + (f" {shares[top]:.0%} sit in one band ({BAND_LABELS[top]}), above the "
           f"{BAND_CONCENTRATION_FLAG:.0%} line." if flagged else ""),
        tuple(f"{BAND_LABELS[i]}: {buckets[i]}" for i in range(5)))


# --------------------------------------------------------------------------- #
# 3. drop-one stability
# --------------------------------------------------------------------------- #
def _positions(ranked: list[RankedTicker]) -> dict[str, int]:
    """Ticker -> 1-based position among the KEPT names, best-first."""
    kept = [r for r in ranked if not r.excluded]
    kept.sort(key=lambda r: (r.combined_rank, r.ticker))
    return {r.ticker: i for i, r in enumerate(kept, 1)}


def drop_one_stability(ranked: list[RankedTicker], setup: RankSetup) -> Check:
    """Remove each name in turn, re-rank, and report the largest shift anyone suffers.

    The comparison is against the base order RESTRICTED to the surviving names, not
    against the raw base positions. Removing the name in 3rd place moves everyone below it
    up one seat as a matter of bookkeeping; that is not instability and must not be
    reported as if it were. What counts is a name changing places with ANOTHER name.

    Re-ranking happens in memory from ``factor_values`` the run already produced, so this
    costs no fetches. The threshold scales with the cohort: more than 3 places on 30 names
    is one tenth, so a 45-name cohort is flagged above 4.5.
    """
    kept = [r for r in ranked if not r.excluded]
    n = len(kept)
    if n < 3:
        return Check("drop-one stability", "n/a", False,
                     f"Only {n} ranked name(s) — drop-one says nothing at this size.")

    base = _positions(kept)
    order = sorted(base, key=lambda t: base[t])
    worst_shift, worst_note = 0, ""

    for removed in order:
        rows = [(r.ticker, dict(r.factor_values)) for r in kept if r.ticker != removed]
        reranked = setup.rerank(rows)
        after = _positions(reranked)
        # base order with `removed` taken out and the seats closed up
        expected = {t: i for i, t in enumerate(
            [t for t in order if t != removed], 1)}
        for ticker, seat in after.items():
            shift = abs(seat - expected.get(ticker, seat))
            if shift > worst_shift:
                worst_shift = shift
                worst_note = (f"{ticker} moved {shift} place(s) when {removed} was "
                              f"removed (seat {expected.get(ticker)} → {seat})")

    threshold = n * STABILITY_FLAG_FRACTION
    flagged = worst_shift > threshold
    return Check(
        "drop-one stability", f"{worst_shift} place(s)", flagged,
        f"Removing any one of the {n} ranked names moves no other name more than "
        f"{worst_shift} place(s)."
        + (f" {worst_note}." if worst_note else "")
        + (f" Above the {threshold:.1f}-place line for a cohort of {n}."
           if flagged else ""),
        (worst_note,) if worst_note else ())


# --------------------------------------------------------------------------- #
# 4. anchor check
# --------------------------------------------------------------------------- #
def anchor_check(ranked: list[RankedTicker], anchors: tuple[str, ...],
                 members: list[Candidate]) -> Check:
    """Where each anchor landed. No judgment — the figure IS the answer.

    An anchor that failed the rules is not in the cohort, and saying so is the point: it
    is the fastest way to find out that a rule does something its author did not expect.
    """
    if not anchors:
        return Check("anchor check", "none named", False,
                     "No anchors are named for this cohort.")
    by_ticker = {r.ticker: r for r in ranked}
    by_code = {r.ticker.split(".")[0].upper(): r for r in ranked}
    member_codes = {c.code.upper() for c in members}
    lines, positions = [], []
    for anchor in anchors:
        code = anchor.split(".")[0].upper()
        row = by_ticker.get(anchor) or by_code.get(code)
        if row is not None:
            seat = row.cohort_position or row.rank_position
            lines.append(f"{anchor}: {row.verdict.upper()} at position "
                         f"{seat if seat is not None else '?'} of {len(ranked)}")
            positions.append(str(seat))
        elif code in member_codes:
            lines.append(f"{anchor}: in the cohort but not ranked (no verdict this run)")
        else:
            lines.append(f"{anchor}: NOT in the cohort — it did not pass the rules")
    return Check("anchor check", "; ".join(positions) or "not ranked", False,
                 "Anchors — " + "; ".join(lines) + ".", tuple(lines))


# --------------------------------------------------------------------------- #
# source summary
# --------------------------------------------------------------------------- #
def source_summary(members: list[Candidate]) -> Check:
    """Counts from screener / constituents / yfinance-filled."""
    by_path = {PATH_SCREENER: 0, PATH_CONSTITUENTS: 0}
    filled_fields: dict[str, int] = {}
    for cand in members:
        by_path[cand.source] = by_path.get(cand.source, 0) + 1
        for field_name in cand.filled:
            filled_fields[field_name] = filled_fields.get(field_name, 0) + 1
    filled_total = sum(1 for c in members if c.filled)
    parts = [f"{n} from the {path}" for path, n in by_path.items() if n]
    fill = (f"; {filled_total} had a field filled from yfinance ("
            + ", ".join(f"{k} ×{v}" for k, v in sorted(filled_fields.items())) + ")"
            ) if filled_total else "; none needed a yfinance fill"
    return Check("source summary", f"{len(members)} member(s)", False,
                 f"Built from {', '.join(parts) or 'no source'}{fill}.",
                 tuple(f"{c.ticker}: {c.source}"
                       + (f" (+{'/'.join(c.filled)} from {PATH_YFINANCE})" if c.filled else "")
                       for c in members))


@dataclass
class QualityReport:
    cohort: str
    version: int
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def flags(self) -> list[Check]:
        return [c for c in self.checks if c.flagged]


def run_checks(*, cohort: str, version: int, ranked: list[RankedTicker],
               unrateable: list[tuple[str, str]], members: list[Candidate],
               setup: RankSetup, anchors: tuple[str, ...],
               notes: list[str] | None = None) -> QualityReport:
    """All four checks plus the source summary, in the order the report prints them."""
    return QualityReport(
        cohort=cohort, version=version,
        checks=[
            abstention_rate(ranked, unrateable, len(members)),
            band_spread(ranked),
            drop_one_stability(ranked, setup),
            anchor_check(ranked, anchors, members),
            source_summary(members),
        ],
        notes=list(notes or ()))
