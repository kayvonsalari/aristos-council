"""B2 CORRUPTION scoring — does the production fact-checker actually stamp a corrupted
claim the narrator repeats? Symmetric to NARR-CHK-FP-1 (which fixed the checker's false
POSITIVE rate): this measures its true-POSITIVE rate.

The checker (``narration_check.check_narration``) is a pure function of ``(narrative,
table)`` — it never touches a ``RankedTicker``. Critically, it must be called against the
TRUE table here, never the corrupted one the narrator was actually shown: calling it
against the corrupted table would find nothing (the narrator was truthful to a lie, so
nothing in the pack contradicts what it says), which measures nothing. ``true_table``
builds the SAME shape ``pipeline._annotate_narration`` does, from the fund's real
``RankedTicker`` (``conditions.ConditionInputs.true_ticker``).
"""

from __future__ import annotations

from aristos_council.narration_check import check_narration
from aristos_council.rank_engine import RankedTicker


def true_table(true_ticker: RankedTicker, *, boundary_tie: dict | None = None) -> dict:
    """The SAME table shape ``pipeline._annotate_narration`` builds (post-NARR-EVIDENCE-1:
    ``cohort_position``, matching what the ranked table actually displays), built from the
    fund's REAL ``RankedTicker`` — independent of whatever the narrator was actually shown."""
    return {"N": true_ticker.universe_size, "combined_position": true_ticker.cohort_position,
            "factors": dict(true_ticker.factor_ranks), "ticker": true_ticker.ticker,
            "score": true_ticker.combined_rank, "boundary_tie": boundary_tie or {}}


def check_against_truth(narrative: str, true_ticker: RankedTicker, *,
                        boundary_tie: dict | None = None) -> list[str]:
    """The checker's annotations for ``narrative`` against ``true_ticker``'s REAL table —
    what an honest audit would flag, independent of whatever pack actually produced the
    narrative. Empty list means: nothing the narrator said contradicts the truth (either
    because it was truthful, or because the checker has a genuine gap — see B2's absolute-
    value corruption, which the checker structurally cannot catch; documented, not hidden)."""
    return check_narration(narrative, true_table(true_ticker, boundary_tie=boundary_tie))


def table_from_ticker_snapshot(snapshot: dict) -> dict:
    """The SAME table shape, built from a saved ``RankedTicker`` snapshot dict (as written
    to a raw-output JSON file by ``runner.py`` — ``dataclasses.asdict(RankedTicker)``)
    rather than a live object. Lets ``analysis.py`` rebuild the true/narrator table purely
    from disk, without re-running anything."""
    return {"N": snapshot.get("universe_size"),
            "combined_position": snapshot.get("cohort_position"),
            "factors": dict(snapshot.get("factor_ranks") or {}),
            "ticker": snapshot.get("ticker"),
            "score": snapshot.get("combined_rank"),
            "boundary_tie": {}}
