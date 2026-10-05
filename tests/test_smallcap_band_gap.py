"""SMALLCAP-BAND-GAP-1 - a company under $5bn whose industry's tested range starts at or above
$5bn is ranked against the size-matched same-industry peers (the owner's option a), and a lens
that divides by operating profit does not apply to a company that has none (PROFIT GUARD).

Seen live: NVCR.US ($1.9bn): banner "$5bn-$5bn ... no band"; VKTX ($3.4bn, biotech cohort floor
$10bn): banner printed backwards as "$10bn-$5bn". In both, every lens said "too few to rank (only 1
company here)" while the Peers section listed 40 size-matched peers "not in this run"."""
from __future__ import annotations

import pytest

from aristos_council.company_report import (SIZE_MATCHED_LINE, format_company_report,
                                            run_company_report, vote_table_lines)
from aristos_council.export.report_html import company_report_html
from aristos_council.market_index import SOURCE_EODHD_LISTING, IndexRow
from aristos_council.operating_profit import (NO_OPERATING_PROFIT_REASON, has_no_operating_profit,
                                              latest_operating_profit)
from tests.test_company_report import (RAW, STRAT_DIR, TODAY, UNIV_DIR, _no_news, _SmallcapAdapter,
                                       _Store)


def _bio(ticker, code, cap):
    return IndexRow(
        ticker=ticker, yahoo_ticker=code, name=f"{code} Therapeutics", exchange="US", market="US",
        currency="USD", sector="Healthcare", industry="Biotechnology",
        gics_sector="Health Care", gics_industry="Biotechnology", gics_subindustry="Biotechnology",
        market_cap=cap, market_cap_usd=cap, market_cap_usd_source="computed",
        primary_ticker=ticker, isin=f"XX{abs(hash(ticker)) % 10**10:010d}", fetched_at="2026-09-26",
        source=SOURCE_EODHD_LISTING)


def _vkt_run(tmp_path, lens_ids=(RAW,), **kw):
    # The subject is "CO" and the peers P00.. because the shared fake adapter manufactures
    # fundamentals from those names.
    rows = [_bio("CO.US", "CO", 3.4e9)] + [_bio(f"P{i:02d}.US", f"P{i:02d}", 3.0e9 + i * 1e8)
                                           for i in range(13)]
    kw.setdefault("news_fetcher", _no_news)
    return run_company_report(
        "CO", list(lens_ids), adapter=_SmallcapAdapter(), strategies_dir=STRAT_DIR,
        universes_dir=UNIV_DIR, runs_dir=tmp_path / "runs", today=TODAY, store=_Store(rows),
        include_small=True, save=False, **kw)


def test_a_company_in_a_cohort_that_starts_above_5bn_is_ranked_against_size_matched_peers(tmp_path):
    report = _vkt_run(tmp_path)
    assert report.outside_tested_range is True and report.size_matched_peers is True
    vote = report.votes[0]
    assert vote.status == "ranked" and vote.cohort_size > 3        # not "only 1 company here"
    assert len(report.universe) > 1


def test_no_badge_and_no_track_record_for_the_size_matched_run(tmp_path):
    report = _vkt_run(tmp_path)
    assert all(v.badge is None for v in report.votes)
    assert report.cohort_slug is None and report.track_record_caption == ""
    assert "Track record" not in company_report_html(report)


def test_the_label_is_the_owners_sentence_on_the_header_and_every_vote(tmp_path):
    report = _vkt_run(tmp_path)
    assert SIZE_MATCHED_LINE == ("Compared with similar-sized companies in its industry; outside "
                                 "the tested range, no track record applies.")
    text = format_company_report(report)
    assert text.count(SIZE_MATCHED_LINE) >= 1 + len(report.votes)
    assert SIZE_MATCHED_LINE in company_report_html(report)
    for line in vote_table_lines(report)[1:]:
        assert SIZE_MATCHED_LINE in line


def test_a_backwards_or_empty_band_is_never_printed(tmp_path):
    report = _vkt_run(tmp_path)
    text = format_company_report(report) + company_report_html(report)
    assert "$10bn-$5bn" not in text and "$5bn-$5bn" not in text
    assert "Small-company peer band" not in text
    # the reason is stated in words instead
    assert "Its industry's tested range starts at $10bn, above this company." in text


def test_the_peers_table_shows_the_lens_ranks_of_the_size_matched_peers(tmp_path):
    from aristos_council.peer_table import peer_rows, rank_columns
    report = _vkt_run(tmp_path)
    cols = rank_columns(report)
    rows = peer_rows(report.peer_group, cols, "CO")
    assert rows[0].is_company
    peers = [r for r in rows if not r.is_company]
    assert peers
    cells = [v for r in peers for _h, v in r.ranks]
    assert not any(v == "not in this run" for v in cells)       # every peer was in this run
    assert all(isinstance(v, (int, float)) for v in cells)       # and carries a real rank


# --- PROFIT GUARD ---------------------------------------------------------------------------- #
class _F:
    def __init__(self, op=None, ebit=None):
        self.operating_income, self.ebit = op, ebit


@pytest.mark.parametrize("op,ebit,expected", [
    ([-5.0, 3.0], None, True), ([0.0], None, True), ([4.0, -2.0], None, False),
    (None, [-1.0], True), (None, [2.0], False), (None, None, False), ([], [], False)])
def test_only_a_confirmed_zero_or_negative_latest_profit_gates(op, ebit, expected):
    assert has_no_operating_profit(_F(op, ebit)) is expected


def test_unknown_profit_is_not_a_failure():
    assert latest_operating_profit(_F()) is None and has_no_operating_profit(None) is False


def test_the_five_lenses_are_guarded_and_others_are_not():
    from aristos_council.operating_profit import lens_requires_operating_profit as req
    from aristos_council.strategy.rank_loader import load_rank_strategy
    for sid in ("magic_formula_raw_v1", "magic_formula_momentum_v1", "epv_v1", "quality_v1",
                "cyclical_income_v1"):
        assert req(load_rank_strategy(STRAT_DIR / f"{sid}.yaml")), sid
    for sid in ("growth_garp_v2", "financials_v1", "forensic_v1", "conservative_plus_v1"):
        assert not req(load_rank_strategy(STRAT_DIR / f"{sid}.yaml")), sid


def test_a_loss_maker_does_not_apply_instead_of_ranking_on_negative_numbers(tmp_path):
    """The guard sits in the rank stage, so it holds for a normal company page too."""
    from tests.test_company_report import _Adapter, _row

    rows = [_row("CO.US", "CO", name="Company Co")] + [_row(f"P{i:02d}.US", f"P{i:02d}")
                                                       for i in range(13)]
    report = run_company_report(
        "CO", [RAW], adapter=_Adapter(company_ebit=-400.0), strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR,
        runs_dir=tmp_path / "runs", today=TODAY, store=_Store(rows), save=False,
        news_fetcher=_no_news)
    vote = report.votes[0]
    assert vote.status == "excluded"
    assert vote.result() == f"does not apply - {NO_OPERATING_PROFIT_REASON} (latest fiscal year)"
    assert report.agreement.n_not_applying == 1 and report.agreement.n_voted == 0
