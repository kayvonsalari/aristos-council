"""SUMMARY-COUNT-1 - the summary's counts and lens-with-a-reason claims against the lens table.
Anchored to the live Ford summary (2026-10-06): "six because ... no operating profit" over a table
showing five."""
from aristos_council.reader_check import check_summary
from aristos_council.summary_counts import count_problems

OP = "no operating profit (fiscal year to Dec 2025)"
FORD_PACK = {
    "company": {"name": "Ford Motor Company (F)"},
    "lenses": [{"name": n, "votes": v} for n, v in [
        ("Value + Momentum", True), ("Defensive Income", True), ("Cyclical Income", True),
        ("Earnings Power Value", True), ("Financials", True), ("Forensic", False),
        ("Growth", True), ("Magic Formula RAW", True), ("Quality", True)]],
    "agreement": {
        "available": True, "lenses_that_voted": 0, "lenses_that_did_not_apply": 8,
        "check_lenses_that_do_not_vote": 1, "buy_votes": 0, "buy_lenses": [], "hold_lenses": [],
        "sell_lenses": [], "buy_lenses_to_name": [],
        "does_not_apply": [
            f"Value + Momentum ({OP})",
            "Defensive Income (0 consecutive years of dividend increases; the rule requires at least 10)",
            f"Cyclical Income ({OP})", f"Earnings Power Value ({OP})",
            "Financials (not for this sector (Consumer Cyclical))",
            "Growth (revenue grew 5.7% a year over the trailing 3 years; the rule requires at least 10%)",
            f"Magic Formula RAW ({OP})", f"Quality ({OP})"]},
}
FORD_SENTENCE = ("All eight voting tests excluded Ford on their own rules: six because Ford reported "
                 "no operating profit in the fiscal year to December 2025, one because Ford is not a "
                 "bank or insurer, and one because revenue grew 5.7% a year over the trailing three "
                 "years, below the 10% the Growth test requires.")


def test_the_ford_sentence_is_a_count_mismatch():
    problems = count_problems(FORD_SENTENCE, FORD_PACK)
    assert any("six because" in p and "shows 5" in p for p in problems)


def test_the_corrected_sentence_passes():
    truthful = ("All eight voting tests excluded Ford: five because Ford reported no operating "
                "profit, one because Ford is not a bank or insurer, one because revenue grew 5.7% "
                "a year, and one because it has no record of dividend increases.")
    assert count_problems(truthful, FORD_PACK) == []


def test_a_lens_given_the_wrong_reason_is_a_mismatch():
    text = "Defensive Income did not apply because Ford had no operating profit."
    assert any("Defensive Income" in p for p in count_problems(text, FORD_PACK))
    right = "Defensive Income did not apply because it has no dividend record."
    assert count_problems(right, FORD_PACK) == []


def test_a_list_of_lenses_shares_the_reason_that_follows():
    ok = "Value + Momentum, Cyclical Income and Quality failed on operating profit."
    assert count_problems(ok, FORD_PACK) == []
    bad = "Value + Momentum, Defensive Income and Quality failed on operating profit."
    assert any("Defensive Income" in p for p in count_problems(bad, FORD_PACK))


def test_a_count_of_lenses_the_table_does_not_hold_is_a_mismatch():
    assert count_problems("Seven lenses voted.", FORD_PACK)
    assert count_problems("Nine tests ran in total: eight vote and one does not vote.",
                          FORD_PACK) == []


def test_a_pack_without_a_lens_table_is_not_checked():
    assert count_problems(FORD_SENTENCE, {"agreement": {"available": False}}) == []
    assert count_problems(FORD_SENTENCE, {}) == []


def test_check_summary_withholds_with_the_reason():
    fields = {"asked": "Ford was compared with 15 peers.", "happened": FORD_SENTENCE,
              "survived": "x", "doubt": "x", "cannot_say": "x"}
    check = check_summary(fields, FORD_PACK)
    assert not check.ok
    assert "count mismatch" in check.withheld_line
