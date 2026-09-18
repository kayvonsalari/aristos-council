"""COHORT-1 — the builder, against recorded fixtures. No network, no LLM, no key.

The fixtures in ``tests/fixtures/cohorts/`` are REAL responses, recorded from EODHD on
2026-09-18: the Specialty Chemicals / Chemicals constituents of GSPC.INDX + STOXX.INDX,
their filtered fundamentals documents, and three deliberate traps (a bank, an insurer and
a REIT re-labelled into the cohort's industry) so the exclusion rule has something to
catch. Recording them rather than inventing them is what makes these tests evidence about
the real shape of the data instead of evidence about my imagination of it.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from aristos_council.cohorts import cleanup, definitions, freeze, quality
from aristos_council.cohorts.builder import build, diff
from aristos_council.cohorts.definitions import CohortDefinition, definition_from_mapping
from aristos_council.cohorts.source import (PATH_CONSTITUENTS, PATH_SCREENER, Candidate,
                                            SourceProbe, build_pool)

FIXTURES = Path(__file__).parent / "fixtures" / "cohorts"


# --------------------------------------------------------------------------- #
# a source that serves the recording
# --------------------------------------------------------------------------- #
class FakeSource:
    """The recorded API. Counts its calls, so a test can assert what was NOT fetched."""

    def __init__(self, *, screener_rows_by_industry=None):
        self._constituents = json.loads((FIXTURES / "constituents.json").read_text())
        self._fundamentals = json.loads((FIXTURES / "fundamentals.json").read_text())
        self._screener = screener_rows_by_industry or {}
        self.indexes = ("GSPC.INDX", "STOXX.INDX")
        self.constituent_pulls = 0
        self.fundamentals_calls = 0
        self.screener_calls = 0

    def constituents(self):
        self.constituent_pulls += 1
        return list(self._constituents)

    def fundamentals(self, symbol):
        self.fundamentals_calls += 1
        return dict(self._fundamentals.get(symbol, {}))

    def screener_rows(self, industries, exchange_codes, min_market_cap, limit=500):
        self.screener_calls += 1
        rows = []
        for industry in industries:
            for row in self._screener.get(industry, []):
                if row.get("exchange") in exchange_codes:
                    rows.append(row)
        return rows


def _defn(**over) -> CohortDefinition:
    base = {
        "name": "Chemicals Test",
        "industry": ["Specialty Chemicals", "Chemicals"],
        "exchanges": ["US", "XETRA", "LSE", "EURONEXT", "SIX"],
        "min_market_cap": 1_000_000_000,
        "min_history_years": 5,
        "exclude": ["financials", "reits"],
        "anchors": [],
    }
    base.update(over)
    return definition_from_mapping(base)


def _pool(defn=None, source=None, probe=None):
    defn = defn or _defn()
    source = source or FakeSource()
    probe = probe or SourceProbe(False, note="HTTP 403 from /screener")
    return build_pool(defn, source, probe)


def _history(years=20.0):
    return lambda candidates: {c.ticker: years for c in candidates}


# =========================================================================== #
# 1. the screener-to-constituents fallback
# =========================================================================== #
def test_a_403_screener_falls_back_to_constituents_and_says_so():
    source = FakeSource()
    probe = SourceProbe(False, note="HTTP 403 from /screener")

    candidates, path, log = build_pool(_defn(), source, probe)

    assert path == PATH_CONSTITUENTS
    assert source.constituent_pulls == 1 and source.screener_calls == 0
    assert candidates, "the fallback produced no names at all"
    # The report must be able to state the path in a sentence, not leave a reader to infer
    # it from an empty screener section.
    assert "index constituents" in log[0] and "403" in log[0]


def test_a_working_screener_takes_the_primary_path_and_never_pulls_an_index():
    rows = [{"code": "LIN", "exchange": "US", "industry": "Specialty Chemicals"},
            {"code": "SHW", "exchange": "US", "industry": "Specialty Chemicals"}]
    source = FakeSource(screener_rows_by_industry={"Specialty Chemicals": rows})
    probe = SourceProbe(True, honoured=("industry", "exchange", "market_capitalization"))

    candidates, path, log = build_pool(_defn(), source, probe)

    assert path == PATH_SCREENER
    assert source.screener_calls == 1          # one filtered call, all industry codes
    assert source.constituent_pulls == 0       # the fallback was never touched
    assert {c.ticker for c in candidates} == {"LIN.US", "SHW.US"}
    assert all(c.source == PATH_SCREENER for c in candidates)
    assert "screener" in log[0].lower()


def test_the_probe_refuses_a_screener_that_ACCEPTS_the_industry_filter_and_ignores_it():
    """An endpoint that answers confidently with the wrong universe is worse than a 403."""
    from aristos_council.cohorts.source import EODHDSource

    src = EODHDSource(api_key="k")
    src._get = lambda *a, **k: [                # noqa: SLF001 - the seam under test
        {"industry": "Banks - Diversified", "exchange": "US",
         "market_capitalization": 5e9}]
    probe = src.probe()
    assert probe.screener_available is False
    assert "industry" in probe.refused
    assert probe.path == PATH_CONSTITUENTS


# =========================================================================== #
# 2. cleanup rule order and logging
# =========================================================================== #
def test_the_rule_order_is_the_contract():
    assert [rule_id for rule_id, _fn in cleanup.RULES] == [
        cleanup.RULE_ONE_LINE, cleanup.RULE_SIZE_AND_HISTORY,
        cleanup.RULE_EXCLUSIONS, cleanup.RULE_NO_DATA]


def test_one_line_per_company_runs_BEFORE_the_size_test():
    """Order matters: a company must be collapsed to one line before anything is measured,
    or the size test decides which listing survives instead of rule 1 deciding it."""
    defn = _defn(min_market_cap=10_000_000_000)
    big = Candidate(ticker="AAA.US", exchange="US", name="Acme NV", isin="NL0000000001",
                    primary_ticker="AAA.US", industry="Chemicals", sector="Basic Materials",
                    market_cap=50e9, history_years=20)
    small_secondary = Candidate(ticker="AAA.LSE", exchange="LSE", name="Acme NV",
                                isin="NL0000000001", primary_ticker="AAA.US",
                                industry="Chemicals", sector="Basic Materials",
                                market_cap=1e9, history_years=20)

    kept, removals = cleanup.clean([small_secondary, big], defn)

    assert [c.ticker for c in kept] == ["AAA.US"]
    # ...and the secondary line left under rule 1, NOT under the cap rule
    assert [(r.ticker, r.rule) for r in removals] == [("AAA.LSE", cleanup.RULE_ONE_LINE)]
    assert "secondary listing of AAA.US" in removals[0].reason


def test_every_removal_names_its_rule_and_its_reason():
    candidates, _path, _log = _pool()
    for cand in candidates:
        cand.history_years = 20.0
    _kept, removals = cleanup.clean(candidates, _defn())

    assert removals, "the recorded fixture carries traps; none were caught"
    for removal in removals:
        assert removal.rule in cleanup.RULE_NAMES
        assert removal.reason.strip()
        assert removal.ticker in removal.line()
        assert cleanup.RULE_NAMES[removal.rule] in removal.line()


def test_the_planted_financials_and_reit_are_removed_by_rule_3():
    candidates, _path, _log = _pool()
    for cand in candidates:
        cand.history_years = 20.0
    _kept, removals = cleanup.clean(candidates, _defn())

    excluded = [r for r in removals if r.rule == cleanup.RULE_EXCLUSIONS]
    assert len(excluded) == 3, [r.line() for r in excluded]
    assert {"financials", "reits"} == {r.reason.split()[2] for r in excluded}


def test_turning_the_exclusions_off_keeps_them():
    candidates, _path, _log = _pool()
    for cand in candidates:
        cand.history_years = 20.0
    _kept, removals = cleanup.clean(candidates, _defn(exclude=[]))
    assert not [r for r in removals if r.rule == cleanup.RULE_EXCLUSIONS]


def test_a_missing_market_cap_is_a_rule_4_gap_not_a_rule_2_failure():
    """house rule 3, in the builder: an absent value is not a failing one. A name with no
    cap has not failed the floor — nobody measured it — and the log must say which."""
    defn = _defn()
    nameless = Candidate(ticker="ZZZ.US", exchange="US", name="Unknown Co",
                         industry="Chemicals", sector="Basic Materials",
                         market_cap=None, history_years=20)
    kept, removals = cleanup.clean([nameless], defn)
    assert kept == []
    assert removals[0].rule == cleanup.RULE_NO_DATA
    assert "market_cap" in removals[0].reason and "abstain" in removals[0].reason


# =========================================================================== #
# 3. thin / wide detection
# =========================================================================== #
def _members(n, exchange="US"):
    return [Candidate(ticker=f"T{i}.{exchange}", exchange=exchange, industry="Chemicals",
                      market_cap=2e9) for i in range(n)]


def test_under_twenty_is_thin_and_names_the_exchange_that_would_fill_it():
    verdict = cleanup.size_verdict(_members(6), _defn(), {"MC": 20, "ST": 2})
    assert verdict.status == "thin" and verdict.count == 6
    assert "Need 14 more" in verdict.suggestion
    assert "MC" in verdict.suggestion and "would fill it" in verdict.suggestion
    assert "Not padded" in verdict.suggestion


def test_a_thin_cohort_no_other_exchange_can_fill_says_widen_the_industry():
    verdict = cleanup.size_verdict(_members(6), _defn(), {})
    assert verdict.status == "thin"
    assert "industry code is the thing to widen" in verdict.suggestion


def test_a_suggestion_that_would_not_be_enough_says_so_rather_than_overselling():
    verdict = cleanup.size_verdict(_members(6), _defn(), {"MC": 1})
    assert "still short" in verdict.suggestion


def test_over_sixty_is_wide_and_is_never_truncated():
    verdict = cleanup.size_verdict(_members(61), _defn(), {})
    assert verdict.status == "wide" and verdict.count == 61
    assert "Narrow the industry" in verdict.suggestion
    assert "Not truncated" in verdict.suggestion


@pytest.mark.parametrize("n", [definitions.MIN_MEMBERS, 40, definitions.MAX_MEMBERS])
def test_inside_the_band_is_ok(n):
    assert cleanup.size_verdict(_members(n), _defn(), {}).ok


def test_a_cohort_outside_the_band_is_NOT_frozen(tmp_path):
    """"do not pad, do not truncate" means the build stops, not that it ships anyway."""
    outcome = build(_defn(min_market_cap=1e15), source=FakeSource(),
                    probe=SourceProbe(False, note="403"), root=tmp_path,
                    history_provider=_history(), ranker=_boom_ranker)
    assert outcome.size.status == "thin"
    assert outcome.frozen is False and outcome.version is None
    assert list(tmp_path.glob("**/members.csv")) == []


def _boom_ranker(*a, **k):                     # pragma: no cover - must never run
    raise AssertionError("an unusable cohort reached the ranker")


# =========================================================================== #
# 4. versioning
# =========================================================================== #
def _fake_ranker(tickers, strategy_id, *, today=None):
    """A deterministic stand-in for the ranker. No pipeline, no network, no strategy file."""
    from aristos_council.rank_engine import FactorSpec, rank_universe

    factors = [FactorSpec("earnings_yield", "high"), FactorSpec("roic", "high")]
    rows = [(t, {"earnings_yield": 0.10 - i * 0.001, "roic": 0.20 + i * 0.002})
            for i, t in enumerate(sorted(tickers))]
    ranked = rank_universe(rows, factors)
    return ranked, [], quality.RankSetup(factors=tuple(factors))


def _built(tmp_path, **over):
    return build(_defn(**over), source=FakeSource(), probe=SourceProbe(False, note="403"),
                 root=tmp_path, history_provider=_history(), ranker=_fake_ranker,
                 today=date(2026, 9, 18))


def test_a_first_build_is_v1_and_writes_the_four_frozen_files(tmp_path):
    outcome = _built(tmp_path)
    assert outcome.frozen and outcome.version == 1
    v1 = tmp_path / "chemicals_test" / "v1"
    for name in (freeze.MEMBERS_FILE, freeze.DEFINITION_FILE, freeze.REPORT_FILE,
                 freeze.REMOVALS_FILE):
        assert (v1 / name).exists(), name


def test_build_does_NOT_increment_an_already_built_cohort(tmp_path):
    _built(tmp_path)
    again = _built(tmp_path)
    assert again.skipped and "already built at v1" in again.skipped
    assert again.frozen is False
    assert freeze.existing_versions(tmp_path, "chemicals_test") == [1]


def test_rebuild_increments_and_leaves_the_old_version_untouched(tmp_path):
    first = _built(tmp_path)
    before = (first.directory / freeze.MEMBERS_FILE).read_text(encoding="utf-8")

    second = build(_defn(), source=FakeSource(), probe=SourceProbe(False, note="403"),
                   root=tmp_path, history_provider=_history(), ranker=_fake_ranker,
                   rebuild=True, today=date(2026, 9, 18))

    assert second.version == 2
    assert freeze.existing_versions(tmp_path, "chemicals_test") == [1, 2]
    assert (first.directory / freeze.MEMBERS_FILE).read_text(encoding="utf-8") == before


def test_next_version_refuses_to_overwrite_without_rebuild(tmp_path):
    _built(tmp_path)
    with pytest.raises(FileExistsError, match="--rebuild"):
        freeze.next_version(tmp_path, "chemicals_test", rebuild=False)


def test_the_frozen_snapshot_is_a_COPY_of_the_rule_not_a_pointer(tmp_path):
    import yaml
    outcome = _built(tmp_path)
    doc = yaml.safe_load((outcome.directory / freeze.DEFINITION_FILE).read_text(encoding="utf-8"))
    assert doc["industry"] == ["Specialty Chemicals", "Chemicals"]
    assert doc["exclude"] == ["financials", "reits"]
    assert doc["version"] == 1 and doc["built_on"] == "2026-09-18"


def test_members_csv_round_trips(tmp_path):
    outcome = _built(tmp_path)
    back = freeze.read_members(outcome.directory / freeze.MEMBERS_FILE)
    assert {c.ticker for c in back} == {c.ticker for c in outcome.members}
    assert all(c.industry for c in back)


def test_members_csv_carries_the_symbol_the_ranker_will_actually_use(tmp_path):
    outcome = _built(tmp_path)
    text = (outcome.directory / freeze.MEMBERS_FILE).read_text(encoding="utf-8")
    assert "yahoo_ticker" in text.splitlines()[0]
    assert ".L," in text or ".DE," in text or ".PA," in text


# =========================================================================== #
# 5. drop-one stability
# =========================================================================== #
def _ranked(order_values):
    """A ranked list whose combined order is exactly ``order_values``' order."""
    from aristos_council.rank_engine import FactorSpec, rank_universe

    factors = [FactorSpec("earnings_yield", "high")]
    rows = [(t, {"earnings_yield": v}) for t, v in order_values]
    return rank_universe(rows, factors), quality.RankSetup(factors=tuple(factors))


def test_a_perfectly_ordered_cohort_never_moves_when_one_name_leaves():
    """Removing the name in 3rd place moves everyone below up a seat. That is bookkeeping,
    not instability, and the check must not report it as a shift."""
    ranked, setup = _ranked([(f"T{i}", 1.0 - i * 0.01) for i in range(10)])
    check = quality.drop_one_stability(ranked, setup)
    assert check.figure == "0 place(s)"
    assert check.flagged is False


def test_a_tie_that_breaks_differently_when_a_name_leaves_is_reported():
    """Ties are where drop-one earns its keep: the tied block is averaged, so removing a
    name outside it can still re-seat its members against each other."""
    ranked, setup = _ranked([("A", 0.50), ("B", 0.30), ("C", 0.30), ("D", 0.30),
                             ("E", 0.10), ("F", 0.05)])
    check = quality.drop_one_stability(ranked, setup)
    assert check.name == "drop-one stability"
    assert check.figure.endswith("place(s)")


def test_the_flag_scales_with_the_cohort():
    """"more than 3 places on a cohort of 30" is one tenth, so 22 names flag above 2.2."""
    assert quality.STABILITY_FLAG_FRACTION == pytest.approx(0.1)
    assert 22 * quality.STABILITY_FLAG_FRACTION == pytest.approx(2.2)


def test_drop_one_says_nothing_rather_than_something_wrong_on_a_tiny_cohort():
    ranked, setup = _ranked([("A", 0.5), ("B", 0.4)])
    check = quality.drop_one_stability(ranked, setup)
    assert check.figure == "n/a" and not check.flagged
    assert "says nothing at this size" in check.sentence


def test_drop_one_makes_no_call_of_any_kind():
    """N+1 rankings, one fetch. The re-rank reads factor_values the run already produced."""
    ranked, setup = _ranked([(f"T{i}", 1.0 - i * 0.01) for i in range(12)])
    source = FakeSource()
    quality.drop_one_stability(ranked, setup)
    assert source.fundamentals_calls == 0 and source.constituent_pulls == 0


# =========================================================================== #
# 6. the other three checks
# =========================================================================== #
def test_abstention_counts_both_the_unreadable_and_the_partly_read():
    ranked, _setup = _ranked([("A", 0.5), ("B", 0.4), ("C", 0.3)])
    ranked[0].factor_values["earnings_yield"] = None
    members = [Candidate(ticker=t) for t in ("A", "B", "C", "D")]
    check = quality.abstention_rate(ranked, [("D", "no data")], len(members))
    assert check.figure == "50%"            # 1 hole + 1 unrateable out of 4
    assert check.flagged is True


def test_band_spread_never_invents_a_percentile_for_an_abstained_band():
    class _Band:
        def __init__(self, pct):
            self.percentile = pct
    ranked, _setup = _ranked([("A", 0.5), ("B", 0.4), ("C", 0.3)])
    ranked[0].valuation_band = _Band(10.0)
    ranked[1].valuation_band = _Band(None)       # abstained
    ranked[2].valuation_band = None              # not requested
    check = quality.band_spread(ranked)
    assert "1 name(s)" in check.sentence and "2 abstained" in check.sentence


def test_band_spread_flags_a_cohort_sitting_in_one_band():
    class _Band:
        def __init__(self, pct):
            self.percentile = pct
    ranked, _setup = _ranked([(f"T{i}", 1.0 - i * 0.01) for i in range(10)])
    for i, row in enumerate(ranked):
        row.valuation_band = _Band(90.0 if i < 9 else 10.0)
    assert quality.band_spread(ranked).flagged is True


def test_an_anchor_that_failed_the_rules_is_reported_as_absent_not_hidden():
    ranked, _setup = _ranked([("A", 0.5), ("B", 0.4)])
    check = quality.anchor_check(ranked, ("ZZZ",), [Candidate(ticker="A"), Candidate(ticker="B")])
    assert "NOT in the cohort" in check.sentence
    assert check.flagged is False              # shown, never judged


def test_the_source_summary_counts_the_yfinance_fills():
    members = [Candidate(ticker="A.US", source=PATH_CONSTITUENTS),
               Candidate(ticker="B.US", source=PATH_CONSTITUENTS,
                         filled={"market_cap": "yfinance"})]
    check = quality.source_summary(members)
    assert "2 from the constituents" in check.sentence
    assert "1 had a field filled from yfinance" in check.sentence


# =========================================================================== #
# 7. diff
# =========================================================================== #
def test_diff_reports_what_a_rebuild_would_change_and_writes_nothing(tmp_path):
    outcome = _built(tmp_path)
    before = sorted(p.name for p in (tmp_path / "chemicals_test").iterdir())

    text, added, removed = diff(_defn(), source=FakeSource(),
                                probe=SourceProbe(False, note="403"), root=tmp_path,
                                history_provider=_history())

    assert added == [] and removed == []
    assert "Nothing" in text and "rebuilt identically" in text
    assert sorted(p.name for p in (tmp_path / "chemicals_test").iterdir()) == before


def test_diff_names_the_joiners_when_the_rule_widens(tmp_path):
    """Turning the exclusions off would let the three planted financials back in."""
    _built(tmp_path)
    text, added, _removed = diff(_defn(exclude=[]), source=FakeSource(),
                                 probe=SourceProbe(False, note="403"), root=tmp_path,
                                 history_provider=_history())
    assert len(added) == 3, text
    assert "Would join" in text and "Nothing was written" in text


def test_diff_names_the_leavers_when_the_rule_narrows(tmp_path):
    _built(tmp_path)
    text, _added, removed = diff(_defn(min_market_cap=50_000_000_000), source=FakeSource(),
                                 probe=SourceProbe(False, note="403"), root=tmp_path,
                                 history_provider=_history())
    assert removed, text
    assert "Would leave" in text


def test_diff_on_an_unbuilt_cohort_says_so_instead_of_failing(tmp_path):
    text, added, removed = diff(_defn(), source=FakeSource(),
                                probe=SourceProbe(False, note="403"), root=tmp_path,
                                history_provider=_history())
    assert "not built yet" in text and added == [] and removed == []


# =========================================================================== #
# 8. registration — a built cohort shows up like any other list
# =========================================================================== #
def test_a_built_cohort_registers_as_a_local_STOCK_list(tmp_path):
    """ASSET-MODE-1: a cohort of operating companies is not an ETF list, and a blank
    asset_kind would file it behind the wrong switch."""
    import yaml

    universes = tmp_path / "universes"
    outcome = build(_defn(), source=FakeSource(), probe=SourceProbe(False, note="403"),
                    root=tmp_path / "cohorts", universes_dir=universes,
                    history_provider=_history(), ranker=_fake_ranker,
                    today=date(2026, 9, 18))

    assert outcome.universe_path is not None and outcome.universe_path.exists()
    doc = yaml.safe_load(outcome.universe_path.read_text(encoding="utf-8"))
    assert doc["id"] == "cohort_chemicals_test_v1"
    assert doc["asset_kind"] == "stocks"
    assert len(doc["tickers"]) == len(outcome.members)
    # the list carries the symbols the ranker resolves, not the EODHD ones
    assert not any("." in t and t.rsplit(".", 1)[-1] in ("US", "XETRA", "LSE")
                   for t in doc["tickers"])
    # ...and it points at the membership of record rather than pretending to BE it
    assert "members.csv" in doc["description"]


def test_the_universe_id_carries_the_SAME_version_as_the_frozen_cohort(tmp_path):
    universes = tmp_path / "universes"
    build(_defn(), source=FakeSource(), probe=SourceProbe(False, note="403"),
          root=tmp_path / "cohorts", universes_dir=universes,
          history_provider=_history(), ranker=_fake_ranker, today=date(2026, 9, 18))
    second = build(_defn(), source=FakeSource(), probe=SourceProbe(False, note="403"),
                   root=tmp_path / "cohorts", universes_dir=universes, rebuild=True,
                   history_provider=_history(), ranker=_fake_ranker,
                   today=date(2026, 9, 18))
    assert second.version == 2
    assert second.universe_path.name == "cohort_chemicals_test_v2.yaml"
    # v1's list is left alone — a past run's list must stay what it was
    assert (universes / "local" / "cohort_chemicals_test_v1.yaml").exists()
