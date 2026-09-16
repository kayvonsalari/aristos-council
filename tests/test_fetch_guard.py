"""FETCH-GUARD-1 — a run that measured nothing must say so.

On 2026-09-16 a helper bug made every fundamentals fetch raise. The pipeline caught each one
as a fetch failure, exactly as designed, and produced a report that looked entirely normal:
134 names "graded", 0 ranked, 0 excluded, a verdict grid full of ties. No individual
behaviour was wrong. The failure was that NOTHING SAID the run had measured nothing, so the
only thing standing between that document and a reader trusting it was someone noticing the
numbers were absurd.

The guard blocks nothing — a half-fetched run is still evidence of something. It stops the
document reading like an ordinary one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aristos_council.pipeline import (FETCH_GUARD_SHARE, fetch_guard, fetch_guard_line,
                                      multi_summary_line)


@dataclass
class _Row:
    ticker: str
    verdict: str = "hold"
    excluded: bool = False


@dataclass
class _Res:
    ranked: list = field(default_factory=list)
    excluded: list = field(default_factory=list)
    fetch_errors: list = field(default_factory=list)


@dataclass
class _Multi:
    strategy_ids: list
    results: dict
    meta: dict = field(default_factory=dict)
    rows: list = field(default_factory=list)
    strategy_names: dict = field(default_factory=dict)


def _multi(*, ranked=0, excluded=0, errors=0, total=4):
    res = _Res(ranked=[_Row(f"R{i}") for i in range(ranked)],
               excluded=[(f"E{i}", "screen: x") for i in range(excluded)],
               fetch_errors=[(f"F{i}", "fetch error: timeout") for i in range(errors)])
    return _Multi(["a_v1"], {"a_v1": res}, {"universe_size": total})


# --------------------------------------------------------------------------- #
# The two triggers
# --------------------------------------------------------------------------- #
def test_three_of_four_fetch_errors_fires():
    guard = fetch_guard(_multi(ranked=1, errors=3, total=4))
    assert guard == {"failed": 3, "total": 4}
    assert fetch_guard_line(guard) == (
        "Data fetch failed for 3 of 4 names; this run measured almost nothing. Check the "
        "provider and the cache before reading any verdict.")


def test_one_of_four_does_not_fire():
    assert fetch_guard(_multi(ranked=3, errors=1, total=4)) == {}
    assert fetch_guard_line({}) == ""


def test_exactly_half_does_not_fire():
    """The trigger is MORE than half, so an even split is not enough on its own."""
    assert FETCH_GUARD_SHARE == 0.5
    assert fetch_guard(_multi(ranked=2, errors=2, total=4)) == {}


def test_a_run_that_ranked_and_excluded_NOTHING_fires_even_with_no_fetch_errors():
    """The shape the 2026-09-16 bug actually produced: the fetch 'succeeded' into an empty
    object, so nothing was ever recorded as a fetch error and every name fell through every
    gate untested. Counting fetch errors alone would have missed it entirely."""
    guard = fetch_guard(_multi(ranked=0, excluded=0, errors=0, total=134))
    assert guard == {"failed": 134, "total": 134}


def test_a_normal_run_is_silent():
    assert fetch_guard(_multi(ranked=3, excluded=1, total=4)) == {}


def test_a_run_that_excluded_everything_is_NOT_flagged():
    """Excluding every name is a RESULT — a strict screen on a bad cohort — and reads
    nothing like a run that could not measure. Only ranked AND excluded both zero fires."""
    assert fetch_guard(_multi(ranked=0, excluded=4, total=4)) == {}


def test_an_empty_run_is_not_flagged():
    assert fetch_guard(_Multi([], {}, {"universe_size": 0})) == {}


# --------------------------------------------------------------------------- #
# Where it shows
# --------------------------------------------------------------------------- #
def test_the_summary_line_is_PREFIXED_not_appended():
    """A reader who stops after the first clause must not be told a normal-looking summary
    of a run that measured nothing."""
    m = _multi(ranked=0, excluded=0, total=4)
    m.meta["fetch_guard"] = {"failed": 4, "total": 4}
    m.rows = []
    line = multi_summary_line(m)
    assert line.startswith("⚠ Data fetch failed for 4 of 4 names")
    assert "measured almost nothing" in line


def test_the_summary_line_is_unchanged_on_a_healthy_run():
    m = _multi(ranked=3, excluded=1, total=4)
    assert not multi_summary_line(m).startswith("⚠")
