"""Fund Profile (FUND-PROFILE-1) — the renamed single-name profile and its three new
transparency rules.

The failure this feature replaces: "Company Check" compared a name against a frozen
reference run whose members were INVISIBLE to the reader and often from the wrong category
entirely — a clean-energy UCITS fund shown losing to US tech trackers, with no explanation.
So the tests here pin, in order:

1. **The cohort is listed in full.** A name inside the frozen run gets every member by
   ticker + name with rank and score, itself included, neighbours first.
2. **A rank is SCOPED.** A name outside a matching-sector cohort gets exactly one fit
   warning, NO ordinal ranks — and the medians anyway.
3. **Missing data abstains.** No declared sector -> no sector rank, no crash, no guess.
4. **The median is a historical fact.** It comes from the frozen run's STORED values: a
   live adapter serving completely different numbers cannot move it.
5. **The rename is complete** on every user-visible surface, with the internal
   ``company_check`` ids kept importable so nothing silently broke.
6. **Universe outputs are untouched** — this was a single-name presentation change.

Deterministic throughout: fake adapters, no network, no LLM.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from aristos_council.data.adapter import Fundamentals, MarketDataAdapter, PriceBar, PriceHistory
from aristos_council.fund_profile import (
    Identity,
    cohort_fit,
    cohort_median,
    detect_asset_kind,
    fee_display,
    fit_warning,
    format_fund_profile,
    fund_size_display,
    identity_rows,
    median,
    run_fund_profile,
    strategies_for_asset_kind,
)

ROOT = Path(__file__).resolve().parents[1]
STRAT_DIR = ROOT / "strategies"
UNIV_DIR = ROOT / "universes"
TODAY = date(2026, 6, 30)

_FIN_TICKERS = ["JPM", "BAC", "WFC", "C", "GS", "MS", "USB", "PNC", "SCHW", "BLK",
                "AXP", "V", "MA", "COF", "MET", "AIG"]          # financials_16_v1


def _rising(n=260, base=100.0, step=0.002):
    closes = [base * (1 + step * i) for i in range(n)]
    return PriceHistory(ticker="X", bars=[
        PriceBar(day=date(2026, 1, 1), open=c, high=c, low=c, close=c,
                 adj_close=c, volume=10) for c in closes])


class _FinAdapter(MarketDataAdapter):
    """The 16 financials of financials_16_v1, shaped for a deterministic P/B + ROE +
    momentum ranking (the same shape test_company_check froze). GS is worst on both legs so
    it ranks strictly LAST. ``pb_offset`` shifts EVERY price-to-book — used to prove the
    cohort median comes from the FROZEN record and not from a live fetch."""

    name = "fake"

    def __init__(self, *, pb_offset: float = 0.0, extra=None):
        self._pb_offset = pb_offset
        self._extra = extra or {}

    def get_fundamentals(self, ticker):
        if ticker in self._extra:
            return self._extra[ticker]
        i = _FIN_TICKERS.index(ticker) if ticker in _FIN_TICKERS else 99
        if ticker == "GS":
            pb, roe = 5.0, 0.02                     # worst on both -> ranked last
        else:
            pb, roe = 1.0 + 0.1 * i, 0.30 - 0.005 * i
        return Fundamentals(
            ticker=ticker, company_name=f"{ticker} Corp", market_cap=5e10,
            sector="Financial Services", price_to_book=pb + self._pb_offset,
            return_on_equity=roe)

    def get_price_history(self, ticker, *, start, end):
        return _rising()

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _freeze(runs_dir, adapter=None):
    """Freeze ONE financials_v1 run over financials_16_v1 into ``runs_dir``."""
    from aristos_council.pipeline import run_rank_pipeline
    return run_rank_pipeline(
        None, "financials_v1", universe_id="financials_16_v1", universes_dir=UNIV_DIR,
        strategies_dir=STRAT_DIR, ranker_only=True, adapter=adapter or _FinAdapter(),
        today=TODAY, freeze_dir=runs_dir)


def _profile(ticker, runs_dir, *, adapter=None, reference="financials_16_v1"):
    return run_fund_profile(
        ticker, "financials_v1", reference, adapter=adapter or _FinAdapter(),
        strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR, runs_dir=runs_dir,
        today=TODAY, static_rows={})


# The two outsiders: same sector as the cohort (a bank not on the list) and a different
# sector entirely (the category error this feature exists to catch).
_TFC = Fundamentals(ticker="TFC", company_name="Truist Financial", market_cap=5e10,
                    sector="Financial Services", price_to_book=1.05,
                    return_on_equity=0.11)
_AAPL = Fundamentals(ticker="AAPL", company_name="Apple Inc", market_cap=3e12,
                     sector="Technology", price_to_book=45.0, return_on_equity=1.4)
_NOSEC = Fundamentals(ticker="NOSEC", company_name="No Sector Corp", market_cap=5e10,
                      price_to_book=2.0, return_on_equity=0.15)


# --------------------------------------------------------------------------- #
# 1 — the cohort is never invisible (rule 3)
# --------------------------------------------------------------------------- #
def test_member_report_lists_every_cohort_member_including_itself(tmp_path):
    runs = tmp_path / "runs"
    _freeze(runs)
    r = _profile("GS", runs)

    assert r.cohort_fit == "member"
    assert len(r.cohort_members) == r.reference_cohort_n == 16
    assert {m.ticker for m in r.cohort_members} == set(_FIN_TICKERS)   # itself included
    assert any(m.is_profiled and m.ticker == "GS" for m in r.cohort_members)
    # every row carries a rank AND a score — the two facts rule 3 requires
    assert all(m.position is not None and m.score for m in r.cohort_members)

    text = format_fund_profile(r)
    assert "REFERENCE COHORT" in text
    for t in _FIN_TICKERS:                       # by ticker...
        assert t in text
    assert "JPM Corp" in text                    # ...and by name
    # the run is identified (id + date) and the record is pointed at
    assert r.reference_run_id in text and (r.reference_run_date or "") in text
    assert "full table in the run record" in text


def test_cohort_ordering_puts_the_profiled_name_and_its_neighbours_first(tmp_path):
    runs = tmp_path / "runs"
    _freeze(runs)
    r = _profile("GS", runs)
    # GS ranks last (16 of 16), so its ±2 window is the final three rows of the ranking —
    # and they lead the table.
    window = [m for m in r.cohort_members if m.neighbour]
    assert [m.position for m in window] == [14, 15, 16]
    assert r.cohort_members[:3] == window        # neighbours first, in rank order
    assert not any(m.neighbour for m in r.cohort_members[3:])
    assert "neighbours first" in r.cohort_note


def test_cohort_is_listed_even_when_the_name_is_not_a_member(tmp_path):
    # Rule 3 is unconditional: "whenever a reference run is used". An outsider still sees
    # exactly who it is being compared against.
    runs = tmp_path / "runs"
    _freeze(runs)
    r = _profile("AAPL", runs, adapter=_FinAdapter(extra={"AAPL": _AAPL}))
    assert len(r.cohort_members) == 16
    assert not any(m.is_profiled for m in r.cohort_members)
    assert not any(m.neighbour for m in r.cohort_members)
    assert r.cohort_note.startswith("full cohort in rank order")


# --------------------------------------------------------------------------- #
# 2 — a rank is SCOPED (rule 4)
# --------------------------------------------------------------------------- #
def test_outsider_from_another_sector_gets_a_warning_no_ranks_and_medians(tmp_path):
    runs = tmp_path / "runs"
    _freeze(runs)
    r = _profile("AAPL", runs, adapter=_FinAdapter(extra={"AAPL": _AAPL}))

    assert r.cohort_fit == "mismatch"
    assert r.ranks_shown is False
    # ONE plain-English sentence, naming BOTH sides of the mismatch.
    assert r.fit_warning is not None
    assert r.fit_warning.count(" — ") == 1
    assert "Financials 16" in r.fit_warning          # the group, by its friendly name
    assert "Financial Services" in r.fit_warning     # its declared sector
    assert "Technology" in r.fit_warning             # this name's sector
    assert "not a ranking" in r.fit_warning

    # NO ordinal rank phrasing survives...
    for fc in r.factors:
        assert fc.rank_shown is False
        assert "ahead of" not in fc.context and "below all" not in fc.context
    # ...but the medians are still there (rule 5 says so explicitly).
    assert any(fc.median is not None for fc in r.factors)

    text = format_fund_profile(r)
    assert f"FIT: {r.fit_warning}" in text
    assert "cohort median" in text
    assert "ahead of" not in text and "below all" not in text


def test_same_sector_outsider_keeps_its_ranks(tmp_path):
    # A US bank that simply isn't on the list IS a sector match, so positions are honest.
    runs = tmp_path / "runs"
    _freeze(runs)
    r = _profile("TFC", runs, adapter=_FinAdapter(extra={"TFC": _TFC}))
    assert r.cohort_fit == "sector_match"
    assert r.ranks_shown is True
    assert r.fit_warning is None
    assert any("ahead of" in fc.context or "below all" in fc.context for fc in r.factors)


def test_member_of_the_run_always_ranks_regardless_of_sector(tmp_path):
    # Membership is its own confirmation: the recorded rank IS a fact about this name.
    runs = tmp_path / "runs"
    _freeze(runs)
    odd = Fundamentals(ticker="GS", company_name="GS Corp", market_cap=5e10,
                       sector="Technology", price_to_book=5.0, return_on_equity=0.02)
    r = _profile("GS", runs, adapter=_FinAdapter(extra={"GS": odd}))
    assert r.cohort_fit == "member" and r.ranks_shown is True and r.fit_warning is None


def test_cohort_fit_is_confirmed_only():
    assert cohort_fit(is_member=True, name_sector=None, cohort_sector=None) == "member"
    assert cohort_fit(is_member=False, name_sector="Energy",
                      cohort_sector="energy") == "sector_match"      # case-insensitive
    assert cohort_fit(is_member=False, name_sector="Energy",
                      cohort_sector="Utilities") == "mismatch"
    assert cohort_fit(is_member=False, name_sector=None,
                      cohort_sector="Energy") == "name_sector_unknown"
    assert cohort_fit(is_member=False, name_sector="Energy",
                      cohort_sector=None) == "cohort_sector_unknown"


def test_fit_warning_is_exactly_one_sentence_and_none_on_a_fit():
    for fit in ("member", "sector_match", "none"):
        assert fit_warning(fit, ticker="X", name_sector="A", cohort_label="L",
                           cohort_sector="A") is None
    for fit in ("mismatch", "name_sector_unknown", "cohort_sector_unknown"):
        w = fit_warning(fit, ticker="X", name_sector="A", cohort_label="L",
                        cohort_sector="B")
        assert w and w.endswith(".") and w.count(".") == 1       # ONE sentence


# --------------------------------------------------------------------------- #
# 3 — missing sector abstains (rule 2/4): no rank, no crash, no guess
# --------------------------------------------------------------------------- #
def test_missing_sector_shows_no_rank_and_does_not_crash(tmp_path):
    runs = tmp_path / "runs"
    _freeze(runs)
    r = _profile("NOSEC", runs, adapter=_FinAdapter(extra={"NOSEC": _NOSEC}))
    assert r.sector is None
    assert r.cohort_fit == "name_sector_unknown"
    assert r.ranks_shown is False
    assert "no assigned sector" in (r.fit_warning or "")
    text = format_fund_profile(r)                       # renders fully, no exception
    assert "sector:" in text and "not assigned" in text
    assert "cohort median" in text                      # medians survive the abstention


def test_declared_manifest_sector_beats_the_vendor_and_names_its_source(tmp_path):
    # An ETF has no vendor sector at all, so the manifest declaration is the only source —
    # and the profile says where it came from.
    from aristos_council.universe import declared_instrument_sectors
    decls = declared_instrument_sectors(UNIV_DIR)
    assert decls["EQQQ.L"].sector == "UCITS Growth ETFs"
    assert "universe manifest" in decls["EQQQ.L"].source
    assert decls["VHYL.L"].sector == "UCITS Dividend ETFs"
    # A ticker nobody declares stays undeclared — never back-filled from its neighbours.
    assert "AAPL" not in decls


def test_conflicting_declarations_abstain_rather_than_pick_a_winner(tmp_path):
    from aristos_council.universe import declared_instrument_sectors
    d = tmp_path / "universes"
    d.mkdir()
    for n, sector in (("a_v1", "Clean Energy"), ("b_v1", "US Growth ETFs")):
        (d / f"{n}.yaml").write_text(
            f"id: {n}\ncreated: '2026-07-30'\ntickers:\n  - INRG.L\n"
            f"instrument_sectors:\n  INRG.L: {sector}\n", encoding="utf-8")
    decl = declared_instrument_sectors(d)["INRG.L"]
    assert decl.sector is None                              # omit, never invent
    assert "conflicting" in decl.source and "a_v1" in decl.source and "b_v1" in decl.source


# --------------------------------------------------------------------------- #
# 4 — the median is a historical fact (rule 5)
# --------------------------------------------------------------------------- #
def test_median_comes_from_the_stored_run_values_not_a_fresh_fetch(tmp_path):
    runs = tmp_path / "runs"
    _freeze(runs)                                   # frozen P/B: 1.0…2.5 with GS at 5.0
    # The LIVE adapter now serves P/B shifted by +100 for every name. If the median were
    # recomputed from a fresh fetch it would move; it must not.
    r = _profile("GS", runs, adapter=_FinAdapter(pb_offset=100.0))
    pb = next(fc for fc in r.factors if fc.factor == "price_to_book")
    assert pb.median == pytest.approx(1.85)         # (1.8 + 1.9) / 2 over the 16 stored
    assert pb.median_n == 16
    assert pb.value == pytest.approx(105.0)         # the PROFILED name is fetched live
    assert f"(run {r.reference_run_date})" in format_fund_profile(r)


def test_median_is_labelled_with_the_run_date(tmp_path):
    runs = tmp_path / "runs"
    _freeze(runs)
    r = _profile("GS", runs)
    text = format_fund_profile(r)
    for fc in r.factors:
        if fc.median is not None:
            assert (f"over {fc.median_n} stored value" in text
                    and f"(run {r.reference_run_date})" in text)


def test_median_drops_missing_values_and_never_counts_them_as_zero():
    assert median([]) is None
    assert median([None, None]) is None
    assert median([3.0, None, 1.0]) == 2.0                # median of the PRESENT values
    assert median([1.0, 2.0, 3.0, 4.0]) == 2.5            # even count averages the middle


def test_absolute_money_medians_are_withheld_until_currencies_are_stated():
    # DATA-HYGIENE-1 sequencing: a fund-size median over a cohort whose currencies the run
    # record does not state would MIX currencies. A wrong number is worse than none.
    med, n, note = cohort_median("fund_size", [1e9, 2e9, 3e9])
    assert med is None and n == 3
    assert "mix currencies" in note and "DATA-HYGIENE-1" in note
    # Ratios and returns are currency-invariant, so they compute as normal.
    assert cohort_median("expense_ratio", [0.1, 0.3, 0.2])[0] == pytest.approx(0.2)
    assert cohort_median("momentum_12m", [0.1, 0.5])[0] == pytest.approx(0.3)


def test_no_reference_run_means_no_median_and_the_old_honest_note(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()                                    # nothing frozen
    r = _profile("GS", runs)
    assert r.cohort_members == [] and r.fit_warning is None
    assert all(fc.median is None and not fc.median_note for fc in r.factors)
    text = format_fund_profile(r)
    assert "no reference run available" in text
    assert "cohort median" not in text
    assert "REFERENCE COHORT" not in text


# --------------------------------------------------------------------------- #
# 5 — the identity header (rule 6)
# --------------------------------------------------------------------------- #
def test_identity_header_states_every_field_or_its_absence(tmp_path):
    runs = tmp_path / "runs"
    _freeze(runs)
    r = _profile("GS", runs)
    rows = {row.label: row for row in identity_rows(r.identity)}
    assert rows["name"].value == "GS Corp (GS)"
    assert rows["ticker"].value == "GS"
    assert rows["ISIN"].value == "not known"            # absent, stated — never invented
    assert rows["sector"].value == "Financial Services"
    assert rows["sector"].source == "vendor: sector"
    # A stock has no fee and no fund size, so those rows are omitted rather than dashed.
    assert "fee (expense ratio)" not in rows
    text = format_fund_profile(r)
    assert "IDENTITY:" in text and "ISIN:" in text


def test_identity_reads_isin_and_sector_from_the_dated_static_layer(tmp_path):
    from aristos_council.etf_static import StaticRow

    runs = tmp_path / "runs"
    runs.mkdir()
    etf = Fundamentals(ticker="XYZ.L", company_name="Example UCITS ETF",
                       market_cap=1e10, quote_type="ETF", currency="GBP")
    row = StaticRow(ticker="XYZ.L", expense_ratio=0.22, fund_size=1.5e9,
                    distribution_yield=0.03, share_class="dist", domicile="IE",
                    source="iShares factsheet", as_of=TODAY.isoformat(),
                    sector="UCITS Index Tracker ETFs", isin="IE00EXAMPLE0")
    r = run_fund_profile(
        "XYZ.L", "financials_v1", "", adapter=_FinAdapter(extra={"XYZ.L": etf}),
        strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR, runs_dir=runs,
        today=TODAY, static_rows={"XYZ.L": row})
    rows = {x.label: x for x in identity_rows(r.identity)}
    assert rows["ISIN"].value == "IE00EXAMPLE0"
    assert "static:" in rows["ISIN"].source                     # dated provenance receipt
    assert rows["asset kind"].value == "ETF"
    assert rows["sector"].value == "UCITS Index Tracker ETFs"
    assert r.sector_source.startswith("static:")
    # the fund fields are shown for an ETF, with the currency question answered honestly
    assert "0.22% per year" in rows["fee (expense ratio)"].value
    assert "currency-invariant" in rows["fee (expense ratio)"].value
    assert "1,500,000,000" in rows["fund size"].value


def test_a_stale_static_row_serves_no_sector_and_no_isin():
    from aristos_council.etf_static import STALE_NOTE, StaticRow, static_descriptive
    old = StaticRow(ticker="X.L", expense_ratio=0.1, fund_size=1e9,
                    distribution_yield=0.02, share_class="dist", domicile="IE",
                    source="factsheet", as_of="2020-01-01",
                    sector="UCITS Growth ETFs", isin="IE00STALE000")
    desc = static_descriptive(old, TODAY)
    assert desc.sector is None and desc.isin is None
    assert desc.stale_note == STALE_NOTE            # never served silently


def test_money_amounts_never_render_without_saying_what_currency():
    assert fund_size_display(None, "EUR") == "not available"
    assert fund_size_display(1e9, "EUR") == "1,000,000,000 EUR"
    assert "currency not stated" in fund_size_display(1e9, None)
    # a static-layer value is in the FUND's reporting currency, which the row doesn't record
    out = fund_size_display(1e9, "GBP", "static: 2026-07-21, factsheet")
    assert "reporting currency not recorded" in out and "GBP" in out
    assert fee_display(None) == "not available"
    assert "currency-invariant" in fee_display(0.3)


def test_identity_rows_are_empty_without_an_identity():
    assert identity_rows(None) == []
    rows = identity_rows(Identity(ticker="X"))
    assert {r.label for r in rows} == {"name", "ticker", "ISIN", "asset kind", "sector"}
    assert [r.value for r in rows if r.label == "asset kind"] == ["not detected"]


def test_unrateable_name_still_gets_an_identity_and_no_verdict(tmp_path):
    class _Dead(MarketDataAdapter):
        name = "fake"

        def get_fundamentals(self, ticker):
            raise RuntimeError("no data")

        def get_price_history(self, ticker, *, start, end):
            raise RuntimeError("no timezone found, symbol may be delisted")

        def get_dividend_history(self, ticker, *, start, end):
            return []

    runs = tmp_path / "runs"
    runs.mkdir()
    r = run_fund_profile("PARA", "financials_v1", "", adapter=_Dead(),
                         strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR,
                         runs_dir=runs, today=TODAY, static_rows={})
    assert r.unrateable and r.identity is not None
    text = format_fund_profile(r)
    assert "IDENTITY:" in text and "UNRATEABLE" in text
    assert "NO VERDICT" in text
    assert not any(f"Verdict: {v}" in text for v in ("BUY", "HOLD", "SELL"))


# --------------------------------------------------------------------------- #
# 6 — asset-kind scoping for the selector (rule 7)
# --------------------------------------------------------------------------- #
class _Kind:
    def __init__(self, id, kinds):
        self.id = id
        self.asset_kinds = kinds


_STOCK = _Kind("magic_formula_v1", ["equity"])
_ETF = _Kind("etf_dividend_v1", ["etf"])
_ANY = _Kind("raw_v1", [])


def test_selector_scopes_strategies_to_the_detected_asset_kind():
    assert [s.id for s in strategies_for_asset_kind([_STOCK, _ETF, _ANY], "etf")] == \
        ["etf_dividend_v1", "raw_v1"]
    assert [s.id for s in strategies_for_asset_kind([_STOCK, _ETF, _ANY], "equity")] == \
        ["magic_formula_v1", "raw_v1"]


def test_detection_failure_widens_to_the_full_list_never_guesses():
    every = [_STOCK, _ETF, _ANY]
    assert strategies_for_asset_kind(every, None) == every
    assert strategies_for_asset_kind(every, "") == every
    # a kind nothing admits would empty the dropdown — a wide list beats a dead one
    assert strategies_for_asset_kind([_STOCK, _ETF], "cryptocurrency") == [_STOCK, _ETF]


def test_detect_asset_kind_never_raises():
    class _Boom(MarketDataAdapter):
        name = "fake"

        def get_fundamentals(self, ticker):
            raise RuntimeError("429")

        def get_price_history(self, ticker, *, start, end):
            return _rising()

        def get_dividend_history(self, ticker, *, start, end):
            return []

    assert detect_asset_kind(_Boom(), "MU") is None
    assert detect_asset_kind(_FinAdapter(), "") is None
    etf = Fundamentals(ticker="QQQ", quote_type="ETF")
    assert detect_asset_kind(_FinAdapter(extra={"QQQ": etf}), "QQQ") == "etf"
    assert detect_asset_kind(_FinAdapter(), "JPM") is None      # no quoteType -> unknown


# --------------------------------------------------------------------------- #
# 7 — the rename is complete, and the internal ids still import
# --------------------------------------------------------------------------- #
# Files allowed to mention the OLD display name: the new module (which documents the
# rename) and the deprecated alias shim (whose whole job is to explain it).
_RENAME_EXEMPT = {"src/aristos_council/fund_profile.py",
                  "src/aristos_council/company_check.py"}


def _code_files():
    """Every shipped code surface the rename must cover. Docs (README / docs/*) are
    handled by the DOCS PROPOSED duty in CLAUDE.md, so they are deliberately out of scope
    for this fixture — it guards the CODE."""
    files = [ROOT / "app.py", ROOT / "acceptance_check.py"]
    files += sorted((ROOT / "src").rglob("*.py"))
    files += sorted((ROOT / "examples").rglob("*.py"))
    return files


def test_no_user_visible_company_check_string_survives_in_code():
    offenders = []
    for p in _code_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel in _RENAME_EXEMPT:
            continue
        if "Company Check" in p.read_text(encoding="utf-8"):
            offenders.append(rel)
    assert offenders == []


def test_the_new_name_is_what_the_reader_sees():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '"Fund Profile"' in app                       # the tab label
    assert "Fund Profile — single-name profile" in app   # the header
    assert "Run fund profile" in app                     # the button
    assert "Company Check" not in app
    # the CLI moved with the feature name
    assert (ROOT / "examples" / "fund_profile.py").exists()
    assert not (ROOT / "examples" / "company_check.py").exists()


def test_old_internal_ids_still_import_and_are_the_same_objects():
    # Snapshots and external callers keep working — the aliases are not copies.
    from aristos_council import company_check as legacy
    from aristos_council import fund_profile as new
    assert legacy.run_company_check is new.run_fund_profile
    assert legacy.format_company_check is new.format_fund_profile
    assert legacy.CompanyCheckResult is new.FundProfileResult
    from aristos_council.export.report_html import company_check_html, fund_profile_html
    assert company_check_html is fund_profile_html


def test_report_titles_and_html_carry_the_new_name(tmp_path):
    from aristos_council.export.report_html import fund_profile_html
    runs = tmp_path / "runs"
    _freeze(runs)
    r = _profile("GS", runs)
    text = format_fund_profile(r)
    assert text.startswith("Fund Profile — GS Corp (GS)")
    doc = fund_profile_html(r)
    assert "<title>Fund Profile — GS Corp (GS)</title>" in doc
    assert "fund profile" in doc and "Company Check" not in doc
    # the new sections travel into the shareable export too
    assert "Reference cohort" in doc and "Identity" in doc


# --------------------------------------------------------------------------- #
# 8 — universe outputs are untouched (hard constraint)
# --------------------------------------------------------------------------- #
def test_universe_outputs_are_byte_identical_across_the_rename(tmp_path):
    """FUND-PROFILE-1 changed a SINGLE-NAME presentation surface. The cohort report — the
    universe run's ``.md`` download and the CLI ``.txt`` — must come out byte-for-byte as
    before, including after a Fund Profile has read the same frozen run."""
    from aristos_council.pipeline import format_cli_report

    runs = tmp_path / "runs"
    result = _freeze(runs)
    cli_before = format_cli_report(result)

    pytest.importorskip("streamlit")          # the .md download builder lives in app.py
    import app
    md_before = app._universe_markdown(result)

    _profile("GS", runs)                      # replays the frozen run for cohort context

    assert format_cli_report(result) == cli_before
    assert app._universe_markdown(result) == md_before
    # and the cohort report never grew a single-name section
    for out in (md_before, cli_before):
        assert "Fund Profile" not in out and "REFERENCE COHORT" not in out
        assert "IDENTITY:" not in out
