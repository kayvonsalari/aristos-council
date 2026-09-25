"""COHORT-3 finish - size corrections, and flagging every correction a cohort list carries.

The owner's rule: flag, never hide. Each affected company appears once with a symbol, a legend at
the bottom says what each symbol means for that company, and a company excluded for its size is
listed as excluded, with its reason. Nothing here reaches the network or the real index.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from aristos_council.cohorts import builder
from aristos_council.cohorts.builder import build, format_plan, plan
from aristos_council.cohorts.cleanup import clean
from aristos_council.cohorts.flags import (LEGEND, SYM_IDENTITY, SYM_LABEL, SYM_MERGED, SYM_SIZE,
                                           excluded_for, excluded_note, flags_for, legend_lines,
                                           symbols)
from aristos_council.cohorts.freeze import MEMBERS_FILE, read_members
from aristos_council.cohorts.source import build_pool_from_index
from aristos_council.market_index import (SIZE_EXCLUDE, SIZE_SET, IdentityAlias, LabelOverride,
                                          MarketIndexError, SizeCorrection, apply_size_corrections,
                                          clean_pool, load_identity_aliases, load_size_corrections,
                                          peers)
from tests.test_cohort_index import _defn, _many, _row, _sw, _tickers
from tests.test_cohorts import _fake_ranker, _history

ROOT = Path(__file__).resolve().parents[1]
QUHUO = SizeCorrection("QH.US", SIZE_EXCLUDE, "2026-09-25", "price x ordinary shares, 136x revenue")


def _quhuo():
    return _sw("QH.US", "Quhuo Limited American Depository Shares", "Application Software",
               cap_bn=343.3)


# =========================================================================== #
# size corrections: the file
# =========================================================================== #
@pytest.mark.parametrize("body, needle", [
    ("corrections:\n  - ticker: A.US\n    action: exclude\n    date: 2026-09-25\n", "reason"),
    ("corrections:\n  - ticker: A.US\n    action: exclude\n    reason: r\n", "date"),
    ("corrections:\n  - action: exclude\n    date: 2026-09-25\n    reason: r\n", "ticker"),
    ("corrections:\n  - ticker: A.US\n    date: 2026-09-25\n    reason: r\n", "action"),
    ("corrections:\n  - {ticker: A.US, action: delete, date: 2026-09-25, reason: r}\n",
     "'exclude' or 'set'"),
    ("corrections:\n  - {ticker: A.US, action: set, date: 2026-09-25, reason: r}\n",
     "needs a positive market_cap_usd"),
    ("corrections:\n  - {ticker: A.US, action: set, market_cap_usd: -5, date: 2026-09-25, "
     "reason: r}\n", "needs a positive market_cap_usd"),
    ("corrections:\n  - {ticker: A.US, action: exclude, market_cap_usd: 5, date: 2026-09-25, "
     "reason: r}\n", "takes no market_cap_usd"),
    ("corrections:\n  - {ticker: A.US, action: exclude, date: 2026-09-25, reason: r}\n"
     "  - {ticker: a.us, action: exclude, date: 2026-09-25, reason: r}\n", "repeats"),
    ("corrections: nope\n", "must be a list"),
])
def test_a_malformed_size_corrections_file_is_an_error_not_a_silent_no_op(tmp_path, body, needle):
    bad = tmp_path / "s.yaml"
    bad.write_text(body, encoding="utf-8")
    with pytest.raises(MarketIndexError, match=needle):
        load_size_corrections(bad)


def test_a_missing_size_corrections_file_is_none_and_a_good_one_parses(tmp_path):
    assert load_size_corrections(tmp_path / "nothing.yaml") == []
    good = tmp_path / "s.yaml"
    good.write_text("corrections:\n  - {ticker: a.us, action: set, market_cap_usd: 1.2e9, "
                    "date: 2026-09-25, reason: 'the   evidence'}\n", encoding="utf-8")
    (c,) = load_size_corrections(good)
    assert (c.ticker, c.action, c.market_cap_usd, c.reason) == ("A.US", SIZE_SET, 1.2e9,
                                                                "the evidence")


def test_the_shipped_file_excludes_quhuo_with_evidence_from_two_independent_sources():
    shipped = {c.ticker: c for c in load_size_corrections()}
    quhuo = shipped["QH.US"]
    assert quhuo.action == SIZE_EXCLUDE and quhuo.date == "2026-09-25"
    for needle in ("343.3bn", "350.4bn", "Yahoo", "ORDINARY shares", "136 times revenue"):
        assert needle in quhuo.reason, needle
    assert quhuo.market_cap_usd is None       # a figure with no correct value is not guessed


# =========================================================================== #
# size corrections: what they do
# =========================================================================== #
def test_exclude_keeps_a_company_out_of_the_pool_and_says_so():
    pool = clean_pool([_quhuo(), *_many(3)], size_corrections=[QUHUO])
    assert "QH.US" not in {r.ticker for r in pool.rows}
    (gone,) = pool.size_excluded
    assert (gone.ticker, gone.kind, gone.date) == ("QH.US", "size correction", "2026-09-25")
    assert gone.reported_usd == 343.3e9 and "136x revenue" in gone.reason
    assert any("size correction: excluded" in line for line in pool.lines())


def test_set_replaces_the_figure_tags_it_and_keeps_the_row():
    fix = SizeCorrection("X1.US", SIZE_SET, "2026-09-25", "a verified figure", 2.0e9)
    row = _row("X1.US", "Some Co", cap_bn=90.0)
    (out,), applied = apply_size_corrections([row], [fix])
    assert out.market_cap_usd == 2.0e9 and "corrected" in out.market_cap_usd_source
    assert row.market_cap_usd == 90e9                                  # the input is untouched
    assert applied["X1.US"][0] == 90e9
    pool = clean_pool([row], size_corrections=[fix])
    assert [r.market_cap_usd for r in pool.rows] == [2.0e9]
    assert pool.size_corrected["X1.US"][0] == 90e9 and not pool.size_excluded


def test_corrections_are_not_applied_to_a_table_handed_in_directly_by_default():
    """Quhuo is a real key: a fabricated table must not meet a real correction by accident."""
    assert "QH.US" in {r.ticker for r in clean_pool([_quhuo()]).rows}


def test_peers_refuses_an_excluded_company_as_a_peer_and_as_a_subject():
    rows = [_sw("SUBJ.US", "Subject Software", "Application Software", cap_bn=300.0), _quhuo(),
            *[_sw(f"P{i:02d}.US", f"Peer {i}", "Application Software", cap_bn=300.0 + i)
              for i in range(13)]]
    group = peers("SUBJ.US", rows=rows, size_corrections=[QUHUO], aliases=[], overrides=[])
    assert group.available and "QH.US" not in [m.ticker for m in group.members]
    refused = peers("QH.US", rows=rows, size_corrections=[QUHUO], aliases=[], overrides=[])
    assert not refused.available
    assert any("excluded by a size correction" in r and "136x revenue" in r
               for r in refused.reasons)


# =========================================================================== #
# an identity alias that says "this line is its own company"
# =========================================================================== #
def _adeia_shaped():
    """The provider gives Adeia the PrimaryTicker of Xperi, a separate company."""
    adeia = _sw("ADEA.US", "ADEIA CORP", "Application Software", cap_bn=2.74)
    xperi = _sw("XPER.US", "Xperi Corp", "Application Software", cap_bn=0.27)
    adeia.primary_ticker, xperi.primary_ticker = "XPER.US", "XPER.US"
    return adeia, xperi


def test_a_wrong_primary_folds_two_companies_into_one_and_a_self_alias_undoes_it():
    adeia, xperi = _adeia_shaped()
    assert len(clean_pool([adeia, xperi], aliases=[]).rows) < 3
    fix = IdentityAlias("ADEA.US", "ADEA.US", "2026-09-25", "a different company")
    pool = clean_pool([adeia, xperi], aliases=[fix])
    assert {r.ticker for r in pool.rows} == {"ADEA.US", "XPER.US"}
    assert not pool.merged                 # a self-alias joined nothing, so it is nobody's evidence
    (flag,) = flags_for("ADEA.US", pool)
    assert flag.symbol == SYM_IDENTITY
    assert "XPER.US" in flag.note and "different company" in flag.note


def test_the_shipped_self_alias_is_loaded_and_documented():
    shipped = {a.ticker: a for a in load_identity_aliases()}
    assert shipped["ADEA.US"].primary == "ADEA.US" and "SELF-alias" in shipped["ADEA.US"].reason


# =========================================================================== #
# the flags
# =========================================================================== #
def test_the_legend_defines_all_four_symbols_in_the_owners_words():
    text = dict(LEGEND)
    assert text[SYM_MERGED].startswith("counted once, also listed as <other line(s)>; evidence:")
    assert text[SYM_LABEL].startswith("industry label corrected; from <old> to <new>;")
    assert text[SYM_SIZE].startswith("size corrected or excluded; reported <figure>;")
    assert (SYM_MERGED, SYM_LABEL, SYM_SIZE, SYM_IDENTITY) == ("†", "‡", "§", "¶")


def test_a_line_joined_by_an_alias_is_flagged_with_the_reason_from_the_alias_file():
    home = _row("BP.LSE", "BP PLC", cap_bn=115.6, isin="GB0007980591")
    pref = _row("BP-A.LSE", "BP p.l.c", cap_bn=146.8, isin="GB0001385250")
    alias = IdentityAlias("BP-A.LSE", "BP.LSE", "2026-09-25", "BP's 8% cumulative preference")
    pool = clean_pool([home, pref], aliases=[alias])
    (flag,) = flags_for("BP.LSE", pool)
    assert flag.symbol == SYM_MERGED
    assert flag.note == ("counted once, also listed as BP-A.LSE (BP p.l.c); evidence: BP's 8% "
                         "cumulative preference")


def test_a_line_joined_by_the_name_and_size_link_is_flagged_with_that_evidence():
    a = _row("ZS.US", "Zscaler Inc", cap_bn=32.2)
    b = _row("ZSC.XETRA", "ZSCALER INC. DL-,001", cap_bn=32.0, market="XETRA")
    pool = clean_pool([a, b])
    (flag,) = flags_for(pool.rows[0].ticker, pool)
    assert "same reduced company name and USD market caps within 25%" in flag.note
    assert flag.symbol == SYM_MERGED


def test_a_hong_kong_rmb_counter_and_a_korean_preference_line_are_flagged_under_their_ordinary_line():
    hkd = _row("1211.HK", "Sunrise Motor Company Limited", cap_bn=93.4)
    rmb = _row("81211.HK", "Sunrise Motor Company Limited", cap_bn=130.1)
    ordinary = _row("005930.KO", "Samsung Sample Co Ltd", cap_bn=1376.0, market="KO")
    pref = _row("005935.KO", "Samsung Sample Co Pref", cap_bn=1065.4, market="KO")
    pool = clean_pool([hkd, rmb, ordinary, pref])
    (hk,) = flags_for("1211.HK", pool)
    (ko,) = flags_for("005930.KO", pool)
    assert "81211.HK" in hk.note and "80000-89999" in hk.note
    assert "005935.KO" in ko.note and "trailing 5, 7 or 9" in ko.note


def test_a_line_joined_only_by_the_providers_own_handles_is_not_a_correction():
    home = _row("AMD.US", "Advanced Micro Devices", cap_bn=900.0, isin="US0079031078")
    cross = _row("AMD.XETRA", "Advanced Micro Devices", cap_bn=899.0, primary="AMD.US",
                 isin="US0079031078", market="XETRA")
    pool = clean_pool([home, cross])
    assert len(pool.rows) == 1 and not pool.merged and flags_for(pool.rows[0].ticker, pool) == ()


def test_a_corrected_label_is_flagged_with_the_old_label_the_new_one_the_reason_and_the_date():
    mu = _row("MU.US", "Sample Memory Inc", sub="Semiconductor Materials & Equipment")
    fix = LabelOverride("MU.US", "Semiconductors", "2026-09-25", "it makes memory chips")
    (flag,) = flags_for("MU.US", clean_pool([mu], overrides=[fix]))
    assert flag.symbol == SYM_LABEL
    assert flag.note == ("industry label corrected; from Semiconductor Materials & Equipment to "
                         "Semiconductors; it makes memory chips, 2026-09-25")


def test_a_corrected_size_is_flagged_with_the_reported_figure():
    fix = SizeCorrection("X1.US", SIZE_SET, "2026-09-25", "a verified figure", 2.0e9)
    pool = clean_pool([_row("X1.US", "Some Co", cap_bn=90.0)], size_corrections=[fix])
    (flag,) = flags_for("X1.US", pool)
    assert flag.symbol == SYM_SIZE
    assert flag.note == "size corrected; reported $90.0bn; a verified figure, 2026-09-25"


def test_a_company_touched_twice_carries_both_symbols_and_appears_once():
    mu = _row("MU.US", "Sample Memory Inc", sub="Semiconductor Materials & Equipment", cap_bn=90.0)
    ads = _row("MUS.XETRA", "SAMPLE MEMORY INC. DL-,001", cap_bn=90.0, market="XETRA")
    fix = LabelOverride("MU.US", "Semiconductors", "2026-09-25", "memory chips")
    pool = clean_pool([mu, ads], overrides=[fix])
    assert len(pool.rows) == 1
    assert symbols(flags_for(pool.rows[0].ticker, pool)) == SYM_MERGED + SYM_LABEL


# =========================================================================== #
# excluded companies: listed, with the reason, under the cohorts they belong to
# =========================================================================== #
def test_an_excluded_company_is_listed_under_the_cohort_it_would_have_been_in_and_only_there():
    pool = clean_pool([_quhuo(), *_many(3)], size_corrections=[QUHUO])
    software = _defn(name="Software", industry=["Software - Application"],
                     gics_subindustry=["Application Software"])
    steel = _defn(name="Steel", industry=["Steel"])
    payments = _defn(name="Payments", industry=["Software - Application"],
                     gics_subindustry=["Transaction & Payment Processing Services"])
    assert [e.ticker for e in excluded_for(software, pool)] == ["QH.US"]
    assert excluded_for(steel, pool) == [] and excluded_for(payments, pool) == []


def test_an_excluded_note_gives_the_figure_the_reason_and_the_date():
    pool = clean_pool([_quhuo()], size_corrections=[QUHUO])
    note = excluded_note(pool.size_excluded[0])
    assert note.startswith("§ QH.US (Quhuo Limited American Depository Shares): reported "
                           "$343.3bn; excluded;")
    assert "136x revenue" in note and note.endswith("2026-09-25")


def test_an_automatic_size_check_refusal_says_where_the_company_remains():
    lines = [_row("VWS.CO", "Wind Co", cap_bn=31.5, isin="A", primary="VWS.CO"),
             _row("VWSB.XETRA", "Wind Co", cap_bn=31.0, isin="A", primary="VWS.CO", market="XETRA"),
             _row("VWDRY.US", "Wind Co", cap_bn=5.2, isin="A", primary="VWS.CO")]
    pool = clean_pool(lines)
    (gone,) = pool.size_excluded
    assert gone.ticker == "VWDRY.US" and gone.kind == "size check"
    assert gone.remains_as == ("VWS.CO",)
    assert "the company remains in the pool as VWS.CO" in excluded_note(gone)


# =========================================================================== #
# the legend, the report, members.csv and the plan
# =========================================================================== #
def _flagged_cohort_pool():
    rows = ([_sw(f"APP{i:02d}.US", f"App Co {i}", "Application Software", cap_bn=5 + i * 0.1)
             for i in range(24)]
            + [_sw("ZS.US", "Zscaler Inc", "Application Software", cap_bn=32.2),
               _sw("ZSC.XETRA", "ZSCALER INC. DL-,001", "Application Software", cap_bn=32.0,
                   market="XETRA"),
               _quhuo()])
    return clean_pool(rows, size_corrections=[QUHUO])


def _software():
    return _defn(name="Software Test", industry=["Software - Application"],
                 gics_subindustry=["Application Software"])


def test_the_legend_lists_each_flagged_company_then_the_excluded_ones():
    pool = _flagged_cohort_pool()
    defn = _software()
    members, _ = clean(build_pool_from_index(defn, pool)[0], defn)
    lines = legend_lines(members, excluded_for(defn, pool))
    text = "\n".join(lines)
    assert lines[0].startswith("Symbols: ")
    assert "ZS.US †  Zscaler Inc" in text and "also listed as ZSC.XETRA" in text
    assert text.index("Excluded, not silently dropped:") > text.index("ZS.US")
    assert "QH.US" in text.split("Excluded, not silently dropped:")[1]
    assert legend_lines([], []) == []              # nothing corrected, nothing excluded: no legend


def test_the_frozen_report_carries_the_symbols_and_ends_with_the_legend(tmp_path):
    outcome = build(_software(), root=tmp_path, index_pool=_flagged_cohort_pool(),
                    history_provider=_history(), ranker=_fake_ranker, today=date(2026, 9, 25))
    report = (outcome.directory / "report.md").read_text(encoding="utf-8")
    assert "| ZS.US † |" in report                          # the symbol beside the company
    head, legend = report.split("## Corrections and exclusions")
    assert "Excluded, not silently dropped:" in legend and "QH.US" in legend
    assert "also listed as ZSC.XETRA" in legend
    assert "## Members" in head and "## Removed" in head          # the legend is at the bottom
    assert [q.ticker for q in outcome.excluded] == ["QH.US"]


def test_a_cohort_with_no_corrections_says_so_at_the_bottom(tmp_path):
    outcome = build(_defn(), root=tmp_path, index_pool=clean_pool(_many(25)),
                    history_provider=_history(), ranker=_fake_ranker, today=date(2026, 9, 25))
    report = (outcome.directory / "report.md").read_text(encoding="utf-8")
    assert "No member of this cohort carries a correction flag" in report


def test_members_csv_records_the_symbols_and_reads_them_back(tmp_path):
    outcome = build(_software(), root=tmp_path, index_pool=_flagged_cohort_pool(),
                    history_provider=_history(), ranker=_fake_ranker, today=date(2026, 9, 25))
    path = outcome.directory / MEMBERS_FILE
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert {r["ticker"]: r["flags"] for r in rows}["ZS.US"] == "†"
    assert sum(1 for r in rows if r["flags"]) == 1
    assert {m.ticker: symbols(m.flags) for m in read_members(path)}["ZS.US"] == "†"


def test_the_plan_shows_symbols_the_legend_and_the_excluded_company():
    defn = _software()
    (entry,) = plan([defn], _flagged_cohort_pool(), root=Path("no/such"))
    text = format_plan([entry])
    assert "1 of 24 member(s) carry a correction symbol" in text or "of 25 member(s)" in text
    assert "Excluded, not silently dropped:" in text and "QH.US" in text
    full = format_plan([entry], all_members=True)
    assert "members:" in full and "ZS.US †" in full and "top 5:" not in full
    assert "also listed as ZSC.XETRA" in full


def test_a_cohort_with_nothing_to_flag_prints_no_legend_in_the_plan():
    (entry,) = plan([_defn()], clean_pool(_many(25)), root=Path("no/such"))
    text = format_plan([entry])
    assert "legend:" not in text and "flagged:" not in text


# =========================================================================== #
# history is measured only for the names that survive the other rules
# =========================================================================== #
def test_history_is_measured_only_for_the_survivors_and_the_result_is_unchanged(tmp_path):
    """12,829 candidates and 2,289 survivors on the live index: a request per name saved."""
    rows = _many(25, cap_bn=5.0) + _many(30, cap_bn=0.2, start=100)         # 30 under the floor
    pool = clean_pool(rows)
    defn = _defn()
    asked: list = []

    def provider(candidates):
        asked.append([c.ticker for c in candidates])
        return {c.ticker: 10.0 for c in candidates}

    outcome = build(defn, root=tmp_path, index_pool=pool, history_provider=provider,
                    ranker=_fake_ranker, today=date(2026, 9, 25))
    assert len(asked) == 1 and len(asked[0]) == 25                          # not 55
    assert not any(t.startswith("S1") for t in asked[0])                    # none of the small ones

    # ...and identical to measuring history for everyone first, as the old order did
    everyone = build_pool_from_index(defn, pool)[0]
    for cand in everyone:
        cand.history_years = 10.0
    reference, _ = clean(everyone, defn)
    assert _tickers(outcome.members) == _tickers(reference)


def test_a_short_history_still_removes_a_survivor_with_its_reason(tmp_path):
    pool = clean_pool(_many(25))
    short = "S000.US"

    def provider(candidates):
        return {c.ticker: (1.5 if c.ticker == short else 10.0) for c in candidates}

    outcome = build(_defn(), root=tmp_path, index_pool=pool, history_provider=provider,
                    ranker=_fake_ranker, today=date(2026, 9, 25))
    assert short not in _tickers(outcome.members)
    assert any(r.ticker == short and "1.5y of history is under the 5y floor" in r.reason
               for r in outcome.removals)


# =========================================================================== #
# nothing personal
# =========================================================================== #
def test_no_tracked_correction_file_names_a_watch_list():
    for name in ("size_corrections.yaml", "identity_aliases.yaml", "label_overrides.yaml",
                 "cohort_definitions.yaml"):
        text = (ROOT / "data" / name).read_text(encoding="utf-8").lower()
        assert "watchlist" not in text and "portfolio" not in text, name
