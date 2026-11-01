"""VOTE-REASON-1 (2026-10-31 sweep): the Reason column said only "ranked among its peers" for every vote.
It now names the factors that drove the rank: the worst and the best factor rank, in one clause."""
from types import SimpleNamespace

from aristos_council.company_report import driver_clause


def _result(factor_ranks, size=27, imputed=()):
    row = SimpleNamespace(ticker="JPM", excluded=False, factor_ranks=factor_ranks,
                          universe_size=size, imputed_factors=list(imputed))
    return SimpleNamespace(ranked=[row])


def test_jpm_financials_names_the_worst_and_best_factor():
    r = _result({"price_to_book": 24, "return_on_equity": 14})
    assert driver_clause(r, "JPM") == "price-to-book 24th of 27 against return on equity 14th of 27"


def test_one_factor_reads_alone_and_imputed_factors_are_not_drivers():
    assert driver_clause(_result({"epv_margin_of_safety": 19}, 28), "JPM").endswith("19th of 28")
    r = _result({"price_to_book": 24, "return_on_equity": 14}, imputed=["price_to_book"])
    assert "price-to-book" not in driver_clause(r, "JPM")


def test_nothing_on_file_is_empty_never_an_error():
    assert driver_clause(SimpleNamespace(ranked=[]), "JPM") == ""
    assert driver_clause(None, "JPM") == ""


def test_the_table_reason_uses_the_clause_and_keeps_the_factor_note():
    from aristos_council.company_story import _row_reason
    v = SimpleNamespace(status="ranked", cap_reason="", factor_note=" · ranked on 2 of 3 factors",
                        driver_note="earnings yield 19th of 28 against momentum 4th of 28", votes=True)
    assert _row_reason(v) == "earnings yield 19th of 28 against momentum 4th of 28; ranked on 2 of 3 factors"
    v.driver_note = ""
    v.factor_note = ""
    assert _row_reason(v) == "ranked among its peers"
