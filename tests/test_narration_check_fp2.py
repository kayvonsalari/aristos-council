"""NARR-CHK-FP-2 — the closing-synthesis false positive.

Live incident (2026-08-10, universe_magic_formula_momentum_v1_narrator_20260810_1412.md):
GOOGL's closing paragraph enumerated three cautions and was stamped "contradicts rank
table" though every claim in it matched the table. Two mechanisms, both reproduced below
(the run artifact is not committed, so the paragraph here is a RECONSTRUCTION of the
reported shape — an enumeration plus a cross-name comparison — not a verbatim quote):

1. **multi-claim enumeration read as one blob** — "(1) … places it 4th of 21 (2)
   momentum_12m rank 2 out of 21 …" has no comma between the items, so the pre-fix
   comma-only clause split bound "4th" (the COMBINED position, item 1) to the momentum rank
   (item 2) and flagged 4 ≠ 2.
2. **a cross-name claim checked against the wrong row** — "the identically scored MSFT is
   5th on roic" is true of MSFT's row; checked against GOOGL's roic (6) it read as a
   contradiction.

Negative controls pin that per-claim checking did not buy the fix with blindness: a wrong
rank INSIDE the enumeration, a wrong cross-name rank, and a wrong cross-name verdict all
still stamp.
"""

from __future__ import annotations

from aristos_council.narration_check import check_narration

# GOOGL in the 21-name cohort under the flagship (roic / earnings_yield / momentum_12m):
# combined position 4, rank-sum 17. MSFT shares the rank-sum and got HOLD — the comparison
# the synthesis leaned on.
_MSFT_ROW = {"factors": {"roic": 5, "earnings_yield": 11, "momentum_12m": 1},
             "combined_position": 4, "score": 17.0, "verdict": "hold"}
_GOOGL = {"N": 21, "combined_position": 4, "ticker": "GOOGL", "score": 17.0,
          "factors": {"roic": 6, "earnings_yield": 9, "momentum_12m": 2},
          "peers": {"MSFT": _MSFT_ROW}}

# The reconstructed closing synthesis — every claim true of the table it names.
_SYNTHESIS = (
    "Three cautions temper the BUY: (1) GOOGL's combined rank-sum of 17 places it 4th of 21 "
    "(2) momentum_12m rank 2 out of 21 is doing the heavy lifting and momentum mean-reverts "
    "(3) the identically scored MSFT is 5th on roic and received HOLD."
)


def _stamped(narrative, table=None) -> bool:
    return bool(check_narration(narrative, table or _GOOGL))


# --------------------------------------------------------------------------- #
# The fixture: the whole paragraph must pass untouched
# --------------------------------------------------------------------------- #
def test_closing_synthesis_is_not_stamped():
    assert check_narration(_SYNTHESIS, _GOOGL) == []


def test_enumeration_items_are_separate_claims():
    # the blob mechanism in isolation: item 1's ordinal is about the COMBINED position,
    # item 2's citation about momentum — pre-fix they were paired.
    assert check_narration(
        "(1) the combined rank-sum of 17 places it 4th of 21 "
        "(2) momentum_12m rank 2 out of 21 carries it", _GOOGL) == []
    # roman and letter markers enumerate too
    assert check_narration(
        "(i) the combined rank-sum of 17 places it 4th of 21 "
        "(ii) momentum_12m rank 2 out of 21 carries it", _GOOGL) == []
    assert check_narration(
        "(a) the combined rank-sum of 17 places it 4th of 21 "
        "(b) momentum_12m rank 2 out of 21 carries it", _GOOGL) == []


def test_cross_name_claim_is_checked_against_the_named_row():
    # true of MSFT (roic 5) — and NOT of GOOGL (roic 6), which is what stamped it pre-fix
    assert check_narration("The identically scored MSFT is 5th on roic.", _GOOGL) == []
    # MSFT's own combined position and rank-sum, likewise
    assert check_narration("MSFT sits 4th of 21 on the combined rank-sum of 17.",
                           _GOOGL) == []


def test_cross_name_verdict_claim_that_matches_the_table_passes():
    assert check_narration("The identically scored MSFT received HOLD.", _GOOGL) == []


# --------------------------------------------------------------------------- #
# Negative controls — per-claim checking must not become blindness
# --------------------------------------------------------------------------- #
def test_a_wrong_rank_inside_the_enumeration_is_still_stamped():
    wrong = _SYNTHESIS.replace("places it 4th of 21", "places it 7th of 21")
    flags = check_narration(wrong, _GOOGL)
    assert len(flags) == 1 and "contradicts rank table" in flags[0]


def test_a_wrong_rank_in_a_later_enumeration_item_is_still_stamped():
    wrong = _SYNTHESIS.replace("momentum_12m rank 2 out of 21 is doing the heavy lifting",
                               "momentum_12m is the weakest leg of the three")
    # momentum_12m is rank 2 of 21 — not the worst — so the superlative contradicts it
    assert _stamped(wrong)


def test_a_wrong_cross_name_rank_is_stamped():
    wrong = _SYNTHESIS.replace("MSFT is 5th on roic", "MSFT is 9th on roic")
    flags = check_narration(wrong, _GOOGL)
    assert len(flags) == 1 and "contradicts rank table" in flags[0]


def test_a_wrong_cross_name_verdict_is_stamped_and_names_the_peer():
    wrong = _SYNTHESIS.replace("received HOLD", "received BUY")
    flags = check_narration(wrong, _GOOGL)
    assert len(flags) == 1
    assert "MSFT" in flags[0] and "BUY" in flags[0] and "HOLD" in flags[0]
    assert "table is authoritative" in flags[0]


def test_a_hypothetical_verdict_is_not_a_claim():
    # "would have been" / a reference to the tier itself is not an assertion about MSFT
    assert check_narration("The identically scored MSFT would have been a BUY one rank up.",
                           _GOOGL) == []
    assert check_narration("MSFT sits just inside the HOLD tier.", _GOOGL) == []


def test_ambiguous_attribution_is_skipped_not_guessed():
    # both names in one clause: whose 5th? -> never checked against either row
    assert check_narration("GOOGL and MSFT are 5th on roic.", _GOOGL) == []


# --------------------------------------------------------------------------- #
# No peers supplied -> behavior identical to before (NARR-EVIDENCE-1 stays intact)
# --------------------------------------------------------------------------- #
_NO_PEERS = {k: v for k, v in _GOOGL.items() if k != "peers"}


def test_without_peers_a_cross_name_claim_is_read_as_the_narrated_name():
    # unchanged pre-fix behavior: with no cohort rows supplied there is nothing to resolve
    # against, so the claim is still checked against the narrated row (a known false
    # positive the caller fixes by supplying `peers` — the pipeline now does).
    assert _stamped("The identically scored MSFT is 5th on roic.", _NO_PEERS)


# --------------------------------------------------------------------------- #
# Wiring — the pipeline hands the cohort's other rows to the check
# --------------------------------------------------------------------------- #
def test_peer_rows_carry_each_live_names_position_verdict_and_factors():
    from aristos_council.pipeline import _peer_rows
    from aristos_council.rank_engine import RankedTicker

    msft = RankedTicker(ticker="MSFT", factor_ranks={"roic": 1.0}, factor_values={},
                        combined_rank=1.0, universe_size=2, verdict="hold")
    goog = RankedTicker(ticker="GOOGL", factor_ranks={"roic": 2.0}, factor_values={},
                        combined_rank=2.0, universe_size=2, verdict="buy")
    peers = _peer_rows([msft, goog], "GOOGL")
    assert set(peers) == {"MSFT"}                       # never the narrated name itself
    assert peers["MSFT"] == {"factors": {"roic": 1.0}, "combined_position": 1,
                             "score": 1.0, "verdict": "hold"}
    assert _peer_rows(None, "GOOGL") == {}              # no cohort -> no peers, as before


def test_annotate_narration_uses_the_cohort_for_cross_name_claims():
    from types import SimpleNamespace

    from aristos_council.pipeline import _annotate_narration
    from aristos_council.rank_engine import RankedTicker

    msft = RankedTicker(ticker="MSFT", factor_ranks={"roic": 1.0}, factor_values={},
                        combined_rank=1.0, universe_size=2, verdict="hold",
                        cohort_position=1)
    goog = RankedTicker(ticker="GOOGL", factor_ranks={"roic": 2.0}, factor_values={},
                        combined_rank=2.0, universe_size=2, verdict="buy",
                        cohort_position=2)
    honest = "MSFT is 1st on roic and received HOLD."
    rep = SimpleNamespace(decision=SimpleNamespace(rationale=honest))
    _annotate_narration(rep, goog, None, cohort=[msft, goog])
    assert rep.decision.rationale == honest              # true of MSFT's row -> no stamp

    wrong = "MSFT is 1st on roic and received BUY."
    rep2 = SimpleNamespace(decision=SimpleNamespace(rationale=wrong))
    _annotate_narration(rep2, goog, None, cohort=[msft, goog])
    assert "misstates MSFT's verdict" in rep2.decision.rationale
    assert rep2.decision.rationale.startswith(wrong)     # prose never rewritten


def test_opening_rank_line_stays_clean_with_and_without_peers():
    # NARR-EVIDENCE-1 must not regress: the honest opening line is never stamped.
    line = ("GOOGL ranks 4 out of 21 on a combined rank-sum of 17, with roic rank 6 of 21, "
            "earnings_yield rank 9 of 21 and momentum_12m rank 2 of 21.")
    assert check_narration(line, _GOOGL) == []
    assert check_narration(line, _NO_PEERS) == []
