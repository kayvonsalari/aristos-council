"""DEFENCE-COHORT-1 - the Europe + US aerospace and defence cohort is a RULE (no member is written down), and the
optional one-line thesis rides along into the frozen definition, the report and the registered list."""
from pathlib import Path

import yaml

from aristos_council.cohorts.definitions import definition_from_mapping, load_definitions

DEFS = Path(__file__).resolve().parents[1] / "data" / "cohort_definitions.yaml"
NAME = "Industrials - Aerospace & Defense (Europe + US)"


def _defn():
    return next(d for d in load_definitions(DEFS) if d.name == NAME)


def test_the_cohort_is_a_rule_over_europe_and_the_us_with_the_5bn_floor():
    d = _defn()
    assert d.industry == ("Aerospace & Defense",) and d.min_market_cap_usd == 5_000_000_000
    assert "ALL" not in d.exchanges and {"US", "XETRA", "LSE", "EURONEXT", "MILAN"} <= set(d.exchanges)
    for not_europe in ("HONGKONG", "KOREA", "TAIWAN", "AUSTRALIA", "TSX"):
        assert not_europe not in d.exchanges
    assert {"US", "XETRA", "LSE", "PA", "MI", "ST", "OL", "SW"} <= set(d.exchange_codes)
    assert "SA" not in d.exchange_codes


def test_no_member_is_hand_picked_only_two_anchors_the_loader_allows():
    d = _defn()
    assert len(d.anchors) <= 2 and set(d.anchors) <= {"RHM.XETRA", "LDO.MI"}
    raw = yaml.safe_load(DEFS.read_text(encoding="utf-8"))["cohorts"]
    entry = next(e for e in raw if e["name"] == NAME)
    assert not {"members", "tickers", "include"} & set(entry)


def test_the_thesis_is_one_plain_line():
    t = _defn().thesis
    assert t.startswith("European and US defence and aerospace companies") and "\n" not in t
    assert definition_from_mapping({"name": "x", "industry": ["Aerospace & Defense"], "exchanges": ["US"],
                                    "min_market_cap_usd": 5e9, "min_history_years": 5}).thesis == ""


def test_the_thesis_reaches_the_frozen_definition_and_the_report(tmp_path):
    from datetime import date

    from aristos_council.cohorts.freeze import write_definition_snapshot
    from aristos_council.cohorts.report import render_report
    from aristos_council.cohorts.cleanup import SizeVerdict
    d = _defn()
    path = write_definition_snapshot(tmp_path / "definition.yaml", d, version=1, built_on=date(2026, 10, 15), source_path="x")
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["thesis"] == d.thesis
    text = render_report(defn=d, version=1, built_on=date(2026, 10, 15), members=[], removals=[],
                         size=SizeVerdict("ok", 40), quality=None, source_log=[])
    assert f"**Thesis:** {d.thesis}" in text
