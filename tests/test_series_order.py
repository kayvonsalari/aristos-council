"""FACTS-ORDER-1 — a series carries its order from one place, and nobody re-orders it.

Live, oil services narrator run 2026-09-18 18:34, TechnipFMC. The pack rendered

    Free Cash Flow Annual (oldest first) (4 periods, oldest first):
        $1.4bn · $679.4m · $467.8m · $194.2m

and the narrator wrote "the series runs from most recent to oldest in the raw evidence",
re-ordered it, and concluded "a sustained decline across all four visible periods". The
risk specialist made that its main risk; four of the five open questions rested on it.

The series RISES. The adapter builds ``free_cash_flow_annual`` NEWEST-FIRST and says so at
the point it is built, so $194.2m is the oldest figure and $1,447.4m the latest. The ARRAY
was right; the LABEL was wrong. The narrator was handed a bare list under a label that
contradicted it and guessed, which is the only thing it could do.

These tests use the real FTI figures, because a fabricated series would not have caught
what a real one did.
"""
from __future__ import annotations

import pytest

from aristos_council.data.adapter import Fundamentals
from aristos_council.reader_check import _pack_series, check_summary
from aristos_council.series_pack import (NO_PERIODS, ORDER_NEWEST_FIRST,
                                         ORDER_OLDEST_FIRST, PROVIDER_ORDER,
                                         direction_contradictions, pack_series,
                                         packed_ok, render_packed, series_direction)

# The real numbers, in the order the provider actually hands them over: newest first.
FTI_VALUES = [1_447_400_000.0, 679_400_000.0, 467_800_000.0, 194_200_000.0]
FTI_ENDS = ["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"]


def _fti(values=None, ends=None) -> Fundamentals:
    return Fundamentals(
        ticker="FTI", currency="USD",
        aligned_annual={"free_cash_flow": list(FTI_VALUES if values is None else values)},
        aligned_period_ends={"free_cash_flow": list(FTI_ENDS if ends is None else ends)})


def _packed(**kw):
    return pack_series(_fti(), "free_cash_flow_annual",
                       label="Free cash flow, annual", **kw)


# =========================================================================== #
# 1. the packer
# =========================================================================== #
def test_the_provider_order_is_recorded_once_and_is_newest_first():
    """The fact the whole item turns on, written down where a reader will find it."""
    assert PROVIDER_ORDER == ORDER_NEWEST_FIRST


def test_every_value_wears_its_year():
    packed = _packed()
    assert packed["years"] == ["FY2022", "FY2023", "FY2024", "FY2025"]
    assert packed["values"] == [194_200_000.0, 467_800_000.0, 679_400_000.0,
                                1_447_400_000.0]
    assert len(packed["years"]) == len(packed["values"])


def test_the_order_is_stated_and_honoured():
    oldest = _packed(order=ORDER_OLDEST_FIRST)
    newest = _packed(order=ORDER_NEWEST_FIRST)
    assert oldest["order"] == ORDER_OLDEST_FIRST
    assert oldest["years"][0] == "FY2022" and oldest["values"][0] == 194_200_000.0
    assert newest["years"][0] == "FY2025" and newest["values"][0] == 1_447_400_000.0


def test_the_rendered_line_says_the_order_AND_labels_every_figure():
    """Redundant on purpose: the 18:34 failure was a stated order contradicting an
    unstated one, with nothing to break the tie. Now the years break it."""
    line = render_packed(_packed())
    assert "oldest first" in line
    assert "FY2022 194,200,000" in line and "FY2025 1,447,400,000" in line


def test_a_series_without_years_ABSTAINS_rather_than_being_packed():
    bare = Fundamentals(ticker="X", free_cash_flow_annual=[1.0, 2.0, 3.0])
    packed = pack_series(bare, "free_cash_flow_annual")
    assert not packed_ok(packed)
    assert packed["note"] == NO_PERIODS
    assert packed["values"] == []


def test_a_series_whose_years_do_not_line_up_with_its_values_abstains():
    packed = pack_series(_fti(ends=["2025-12-31", "2024-12-31"]),
                         "free_cash_flow_annual")
    assert not packed_ok(packed) and packed["note"] == NO_PERIODS


def test_holes_are_dropped_and_their_years_go_with_them():
    packed = pack_series(_fti(values=[1_447_400_000.0, None, 467_800_000.0, 194_200_000.0]),
                         "free_cash_flow_annual")
    assert packed["years"] == ["FY2022", "FY2023", "FY2025"]
    assert len(packed["values"]) == 3


def test_the_order_is_taken_from_the_YEARS_not_from_the_list_position():
    """If a provider ever changes its mind about order, sorting by year is still right."""
    shuffled = pack_series(
        _fti(values=[467_800_000.0, 1_447_400_000.0, 194_200_000.0, 679_400_000.0],
             ends=["2023-12-31", "2025-12-31", "2022-12-31", "2024-12-31"]),
        "free_cash_flow_annual")
    assert shuffled["years"] == ["FY2022", "FY2023", "FY2024", "FY2025"]
    assert shuffled["values"][0] == 194_200_000.0


# =========================================================================== #
# 2. direction, recomputed
# =========================================================================== #
def test_the_real_FTI_series_RISES():
    """The fact the narrator got backwards."""
    assert series_direction(_packed()) == "up"


def test_direction_reads_oldest_against_newest_not_step_by_step():
    """A series that dips in the middle and ends higher has risen; calling that a decline
    is exactly the error under test."""
    dip = pack_series(_fti(values=[500.0, 100.0, 90.0, 200.0]), "free_cash_flow_annual",
                      label="Free cash flow, annual")
    assert series_direction(dip) == "up"          # FY2022 200 -> FY2025 500


def test_a_flat_series_is_flat_and_a_one_point_series_has_no_direction():
    flat = pack_series(_fti(values=[100.0, 100.0, 100.0, 100.0]),
                       "free_cash_flow_annual")
    assert series_direction(flat) == "flat"
    single = pack_series(_fti(values=[100.0, None, None, None]),
                         "free_cash_flow_annual")
    assert series_direction(single) == ""


# =========================================================================== #
# 3. the check: the live sentence is caught
# =========================================================================== #
def test_the_actual_prose_from_the_1834_run_is_caught():
    """Verbatim from the report, which is the only evidence that matters here."""
    live = ("The most material risk flag is the free cash flow, annual series: oldest to "
            "most recent, USD 1,447.4M, USD 679.4M, USD 467.8M, USD 194.2M — a sustained "
            "decline across all four visible periods.")
    assert direction_contradictions(live, [_packed()]) == ["Free cash flow, annual"]


def test_prose_that_agrees_with_the_numbers_passes():
    good = "Free cash flow, annual rose steadily across the four periods."
    assert direction_contradictions(good, [_packed()]) == []


def test_prose_saying_nothing_about_direction_passes():
    quiet = "Free cash flow, annual is reported for four periods."
    assert direction_contradictions(quiet, [_packed()]) == []


def test_prose_describing_the_PATH_is_not_a_contradiction():
    """"fell in 2023 before rising" describes the shape, it does not assert a trend. A
    check that flagged it would be a style rule, and this is a factual one."""
    both = ("Free cash flow, annual fell early in the window before rising to its "
            "highest level.")
    assert direction_contradictions(both, [_packed()]) == []


def test_a_direction_word_far_from_the_series_name_is_not_read_as_a_claim_about_it():
    far = ("Free cash flow, annual is reported for four periods. " + ("Filler. " * 40)
           + "Margins declined.")
    assert direction_contradictions(far, [_packed()]) == []


def test_an_abstained_series_is_never_contradicted():
    bare = pack_series(Fundamentals(ticker="X"), "free_cash_flow_annual",
                       label="Free cash flow, annual")
    assert direction_contradictions("free cash flow, annual declined", [bare]) == []


# =========================================================================== #
# 4. the check WITHHOLDS
# =========================================================================== #
def _summary(text: str) -> dict:
    """A complete summary carrying ``text`` — the real field names, read from the module
    rather than copied, so a rename cannot leave this test asserting nothing."""
    from aristos_council.reader_check import _FIELDS

    fields = list(_FIELDS)
    return {f: (text if f == fields[1] else "Nothing else to report.") for f in fields}


def _pack_with_series():
    return {"series": [_packed()]}


def test_a_contradicting_direction_WITHHOLDS_the_summary():
    check = check_summary(_summary("Free cash flow, annual shows a sustained decline."),
                          _pack_with_series())
    assert not check.ok
    assert "direction contradicts the series: Free cash flow, annual" in check.reason


def test_an_agreeing_direction_publishes():
    check = check_summary(_summary("Free cash flow, annual rose over the window."),
                          _pack_with_series())
    assert "direction contradicts" not in (check.reason or "")


def test_the_series_is_found_wherever_the_pack_put_it():
    """The reader's pack and the narrator's pack nest differently, and a check that only
    looked in one place would silently stop checking the other."""
    nested = {"company": {"facts": {"cash": [_packed()]}}}
    assert len(_pack_series(nested)) == 1
    assert _pack_series({}) == []


def test_the_prompt_rule_is_on_every_agent():
    """One line, in the SHARED rules, so a specialist cannot be told something the
    narrator is not."""
    from aristos_council.agents.prompts import HARD_RULES

    assert "SERIES ORDER" in HARD_RULES
    assert "NEVER re-order a series" in HARD_RULES


def test_the_evidence_block_hands_over_the_packed_series_not_a_bare_list():
    from aristos_council.agents.nodes import _scoped_fundamentals

    scoped = _scoped_fundamentals(_fti(), {"ticker", "free_cash_flow_annual"})
    packed = scoped["free_cash_flow_annual"]
    assert isinstance(packed, dict), "a bare list is what the narrator had to guess about"
    assert packed["years"][0] == "FY2022"
    assert packed["values"][0] == 194_200_000.0


def test_an_unlabelled_series_reaches_the_narrator_as_an_abstention():
    from aristos_council.agents.nodes import _scoped_fundamentals

    scoped = _scoped_fundamentals(Fundamentals(ticker="X",
                                               free_cash_flow_annual=[1.0, 2.0]),
                                  {"ticker", "free_cash_flow_annual"})
    assert scoped["free_cash_flow_annual"]["note"] == NO_PERIODS
    assert "values" not in scoped["free_cash_flow_annual"]
