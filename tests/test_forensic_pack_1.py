"""BATCH 19B B11/B12 - the council's facts pack explains a "doubted" mark and dates its accounts;
the narration checks no longer read a denial or an exclusion as a verdict or a vote."""
from __future__ import annotations

from types import SimpleNamespace

from aristos_council.abs_readings import accounts_context
from aristos_council.agents.nodes import _company_facts_block
from aristos_council.company_report import LensVote, _check_components, _council_company_facts
from aristos_council.narration_check import check_would_rank
from aristos_council.narration_schema import _asserted_verdict_words
from aristos_council.rank_engine import RankedTicker


def _ranked(ticker="NVCR", imputed=()):
    return RankedTicker(
        ticker=ticker,
        factor_ranks={"accrual_ratio": 30.0, "altman_z": 12.0, "piotroski_f_score": 28.0},
        factor_values={"accrual_ratio": 0.142, "altman_z": 2.5, "piotroski_f_score": None},
        combined_rank=70.0, universe_size=41, verdict="sell", imputed_factors=list(imputed))


def test_check_components_name_each_factor_value_and_rank():
    lines = _check_components(SimpleNamespace(ranked=[_ranked(imputed=["piotroski_f_score"])]),
                              "NVCR")
    assert len(lines) == 3
    assert lines[0].startswith("Accrual ratio: 14.2%, rank 30 of 41")
    assert "Altman Z-Score: 2.5, rank 12 of 41" in lines[1]
    assert "no value on file" in lines[2] and "average rank" in lines[2]


def test_a_company_the_lens_did_not_rank_has_no_components():
    assert _check_components(SimpleNamespace(ranked=[]), "NVCR") == ()


def test_accounts_context_dates_the_accounts_and_never_invents_a_filing_date():
    f = SimpleNamespace(financial_currency="USD", currency="USD",
                        aligned_annual={"operating_income": [-5.0]},
                        aligned_period_ends={"operating_income": ["2025-12-31"]},
                        period_ends={}, operating_income=[-5.0], ebit=[])
    ctx = accounts_context(f)
    assert ctx["currency"] == "USD"
    assert "fiscal year to Dec 2025" in ctx["basis"]
    assert "filing date is not in the data" in ctx["basis"]
    undated = accounts_context(SimpleNamespace(financial_currency="", currency="HKD"))
    assert "period end date is not in the data" in undated["basis"]
    assert accounts_context(None) == {}


def _report(vote, accounts):
    check = SimpleNamespace(debt_and_cash=None, growth_record=None, analyst_trend=None,
                            peer_group=None, valuation_band=None, price_and_cash=None,
                            accounts=accounts)
    return SimpleNamespace(check=check, votes=[vote])


def test_the_council_block_carries_components_currency_and_basis():
    vote = LensVote(strategy_id="forensic_v1", label="Forensic", kind="check", status="ranked",
                    verdict="sell", position=30, cohort_size=41,
                    components=("Accrual ratio: 14.2%, rank 30 of 41",))
    accounts = {"currency": "USD", "listing_currency": "USD",
                "basis": "annual accounts for the fiscal year to Dec 2025 (the filing date is "
                         "not in the data)"}
    facts = _council_company_facts(_report(vote, accounts))
    assert facts["accounts"]["currency"] == "USD"
    assert facts["check_components"][0]["lens"] == "Forensic"
    block = _company_facts_block(SimpleNamespace(company_facts_block=facts))
    assert "fiscal year to Dec 2025" in block
    assert "in USD" in block
    assert "Forensic check (marks, never votes)" in block
    assert "Accrual ratio: 14.2%, rank 30 of 41" in block


def test_a_missing_account_currency_is_said_not_guessed():
    block = _company_facts_block(SimpleNamespace(company_facts_block={
        "accounts": {"currency": "", "listing_currency": "HKD", "basis": "x"}}))
    assert "not stated by the source" in block


# ---- B12: the two live false positives ---------------------------------------------------------
VKTX = ("Because Forensic is a CHECK lens, this placement is a cleanliness mark only - it does "
        "not constitute a vote and does not produce a buy, hold, or sell verdict.")
NVCR = ("NovoCure is excluded from every voting lens: five lenses exclude it for having no "
        "operating profit, the Defensive Income lens excludes it for a 0% dividend yield against "
        "a 1.5% floor, and the Growth lens excludes it for trailing 3-year revenue growth of 8.0%.")
_ROWS = [{"lens": "Defensive Income", "would_rank": "it would rank 31st of 41. Not a vote.",
          "would_rank_position": 31, "would_rank_of": 41}]


def test_vktx_denial_of_a_verdict_is_not_giving_forensic_a_verdict():
    assert _asserted_verdict_words(VKTX) == set()
    assert _asserted_verdict_words(VKTX.replace("buy, hold, or sell", "BUY, HOLD, or SELL")) == set()


def test_nvcr_exclusion_sentence_is_not_giving_defensive_income_a_vote():
    assert check_would_rank(NVCR, _ROWS) == []
    assert check_would_rank("Defensive Income excludes it for a 0% yield.", _ROWS) == []


def test_genuine_violations_still_flag():
    assert _asserted_verdict_words("Forensic's clean read keeps this closer to a HOLD.") == {"hold"}
    assert _asserted_verdict_words("Forensic does not apply, but gave it a BUY.") == {"buy"}
    for s in ("Defensive Income rates it BUY.", "Defensive Income votes for the name.",
              "Defensive Income gives a HOLD here."):
        assert len(check_would_rank(s, _ROWS)) == 1, s
