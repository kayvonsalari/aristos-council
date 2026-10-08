"""B24-E8 - the council narrator is given each lens's actual factors and the company's rank on each, and
told to explain a rank only from them. (EL.PA: "Magic Formula RAW - why" blamed only return on capital and
ignored its third factor, the -46% 12-month move; "Cyclical Income - why" invented "within cyclical,
capital-intensive businesses". Prompt and evidence only: no vote, rank or number changes.)"""
from types import SimpleNamespace

from aristos_council.agents.nodes import _cross_lens_block
from aristos_council.company_report import _council_cross_lens_verdicts
from tests.test_company_report import RAW, SCREENED, _run


def test_a_ranked_lens_carries_every_one_of_its_factors_with_the_companys_rank(tmp_path):
    report = _run([RAW, SCREENED], tmp_path=tmp_path)
    ranked = [v for v in report.votes if v.ranked]
    assert ranked
    for v in ranked:
        assert v.factors, v.label
        for line in v.factors:
            assert ", rank " in line and " of " in line
    raw = next(v for v in report.votes if v.strategy_id == RAW)
    names = " | ".join(raw.factors).lower()
    # Magic Formula RAW ranks on earnings yield, return on capital AND 12-month price momentum
    assert len(raw.factors) == 3 and "momentum" in names and "return on invested capital" in names


def test_an_excluded_lens_carries_no_factors(tmp_path):
    report = _run([RAW, SCREENED], tmp_path=tmp_path)
    rows = _council_cross_lens_verdicts(report.votes)
    for r, v in zip(rows, report.votes):
        assert ("factors" in r) is bool(v.status == "ranked" and v.factors)


def test_the_narrator_block_lists_each_lens_factors_and_the_explain_only_from_them_rule():
    rows = [{"lens": "Magic Formula RAW", "cell": "SELL - 20th of 21", "votes": True, "factors": [
        "Return on invested capital: 5.1%, rank 18 of 21", "Earnings yield (EBIT/EV): 3.0%, rank 15 of 21",
        "12-month price momentum: -46.0%, rank 21 of 21"]},
        {"lens": "Cyclical Income", "cell": "BUY - 1st of 6", "votes": True, "factors": [
            "Dividend yield: 2.0%, rank 2 of 6"]}]
    block = _cross_lens_block(SimpleNamespace(cross_lens_verdicts=rows, cross_lens_reasons=[]))
    assert "factor: 12-month price momentum: -46.0%, rank 21 of 21" in block
    assert block.index("Magic Formula RAW") < block.index("factor: Return on invested capital") < \
        block.index("Cyclical Income")
    assert "EXPLAIN A LENS'S RANK ONLY FROM THE FACTORS LISTED UNDER THAT LENS" in block
    assert "within cyclical, capital-intensive businesses" in block        # named as the thing NOT to do
    assert "not just the first two" in block


def test_a_run_without_factors_has_the_block_byte_unchanged():
    rows = [{"lens": "Quality", "cell": "HOLD - 3rd of 10", "votes": True}]
    block = _cross_lens_block(SimpleNamespace(cross_lens_verdicts=rows, cross_lens_reasons=[]))
    assert "factor:" not in block and "EXPLAIN A LENS'S RANK" not in block
