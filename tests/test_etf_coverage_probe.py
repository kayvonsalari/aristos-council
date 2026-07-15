"""ETF-1 ITEM 1 — the field-coverage probe's decision logic (pure, offline).

The fetching lives in examples/etf_coverage_probe.py (needs network); the ≥80% rule
lives in aristos_council.tools.etf_coverage and is tested here with mocked rows.
"""

from aristos_council.tools.etf_coverage import (
    COVERAGE_THRESHOLD,
    MIN_12M_CLOSES,
    PROBE_FIELDS,
    coverage_decision,
    price_history_present,
    value_present,
)


def _row(**kw):
    base = dict(net_expense_ratio=0.04, total_assets=1e10, dividend_yield=0.03,
                quote_type="ETF", price_history_12m=251)
    base.update(kw)
    return base


def test_value_present_semantics():
    assert value_present(0.0) is True          # a real 0% expense ratio is PRESENT
    assert value_present(None) is False
    assert value_present("ETF") is True
    assert value_present("") is False
    assert value_present("  ") is False


def test_price_history_present_floor():
    assert price_history_present(MIN_12M_CLOSES) is True
    assert price_history_present(MIN_12M_CLOSES - 1) is False
    assert price_history_present(251) is True


def test_all_fields_in_at_full_coverage():
    rows = [_row() for _ in range(10)]
    cov = {c.field: c for c in coverage_decision(rows)}
    assert set(cov) == set(PROBE_FIELDS)
    for c in cov.values():
        assert c.present == 10 and c.total == 10
        assert c.decision == "IN"
        assert c.fraction == 1.0


def test_field_dropped_below_threshold():
    # 3/10 lines missing the expense ratio -> 70% < 80% -> OUT; the rest stay IN.
    rows = [_row() for _ in range(7)] + [_row(net_expense_ratio=None) for _ in range(3)]
    cov = {c.field: c for c in coverage_decision(rows)}
    assert cov["net_expense_ratio"].decision == "OUT"
    assert cov["net_expense_ratio"].present == 7
    assert cov["total_assets"].decision == "IN"


def test_exactly_80_percent_is_in():
    # boundary: 8/10 present == 0.80 == threshold -> IN (>=)
    rows = [_row() for _ in range(8)] + [_row(total_assets=None) for _ in range(2)]
    cov = {c.field: c for c in coverage_decision(rows)}
    assert cov["total_assets"].fraction == COVERAGE_THRESHOLD
    assert cov["total_assets"].decision == "IN"


def test_truncated_price_history_reads_as_absent():
    rows = [_row() for _ in range(8)] + [_row(price_history_12m=50) for _ in range(2)]
    cov = {c.field: c for c in coverage_decision(rows)}
    assert cov["price_history_12m"].present == 8
    assert cov["price_history_12m"].decision == "IN"     # 80% clears


def test_empty_universe_is_all_out():
    cov = coverage_decision([])
    assert all(c.decision == "OUT" and c.total == 0 for c in cov)
