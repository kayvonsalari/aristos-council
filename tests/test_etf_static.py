"""ETF-STATIC-1 — the dated, committed static layer for slow ETF fields.

The adapter fills ETF factor fields the vendor doesn't serve (expense ratio, fund size,
distribution yield) from a committed CSV, for ETF-kind names ONLY. Four disciplines are
pinned here:

- **static fill + tag renders** — a missing/implausible vendor field is filled from
  static and its factor source discloses the ``static: <as_of>, <source>`` receipt, which
  the report wraps as ``[static: <as_of>, <source>]`` (the FX-receipt convention).
- **vendor precedence** — a present, plausible vendor value always wins; static is not
  read for that field.
- **staleness abstains** — an entry older than 90 days is NOT served; the field is
  withheld and the "static data stale — refresh required" note is surfaced.
- **a stock never reads the static layer** — a non-ETF kind short-circuits before any
  static read, even when a matching row exists.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceHistory,
)
from aristos_council.etf_static import (
    DEFAULT_STATIC_PATH,
    STALE_NOTE,
    StaticRow,
    apply_static_fill,
    is_stale,
    load_static,
)
from aristos_council.factors import (
    FactorInputs,
    compute_factor_outcomes,
    gather_factor_inputs,
)

TODAY = date(2026, 7, 15)

# A fresh (well within 90 days) and a stale (>90 days) row for the same fund shape.
FRESH = StaticRow(ticker="SCHD", expense_ratio=0.06, fund_size=6.0e10,
                  distribution_yield=0.035, share_class="dist", domicile="US",
                  source="Schwab factsheet", as_of="2026-06-01")
STALE = StaticRow(ticker="SCHD", expense_ratio=0.06, fund_size=6.0e10,
                  distribution_yield=0.035, share_class="dist", domicile="US",
                  source="Schwab factsheet", as_of="2026-01-01")

ETF_FIELDS = ["expense_ratio", "fund_size", "distribution_yield"]


def _etf(**kw) -> Fundamentals:
    """An ETF-kind Fundamentals shell — the vendor serves quote_type but (by default) not
    the slow factor fields."""
    return Fundamentals(ticker="SCHD", quote_type="ETF", **kw)


# --------------------------------------------------------------------------- #
# static fill + tag renders
# --------------------------------------------------------------------------- #
def test_static_fills_fields_the_vendor_omits():
    f, fill = apply_static_fill(_etf(), kind="etf", row=FRESH, today=TODAY)
    assert f.net_expense_ratio == 0.06
    assert f.total_assets == 6.0e10
    assert f.dividend_yield == 0.035
    assert set(fill.filled) == {"net_expense_ratio", "total_assets", "dividend_yield"}


def test_static_fill_source_tag_renders():
    f, fill = apply_static_fill(_etf(), kind="etf", row=FRESH, today=TODAY)
    fi = FactorInputs(ticker="SCHD", fundamentals=f, static=fill)
    outcomes = compute_factor_outcomes(fi, ETF_FIELDS)
    # value came from static, and the source is the provenance receipt.
    val, src = outcomes["expense_ratio"]
    assert val == 0.06
    assert src == "static: 2026-06-01, Schwab factsheet"
    # the report wraps a factor source as [<source>] (company_check convention) — exactly
    # the [static: <as_of>, <source>] tag the spec asks for.
    assert f"[{src}]" == "[static: 2026-06-01, Schwab factsheet]"
    assert outcomes["fund_size"][1] == "static: 2026-06-01, Schwab factsheet"
    assert outcomes["distribution_yield"][1] == "static: 2026-06-01, Schwab factsheet"


# --------------------------------------------------------------------------- #
# vendor precedence
# --------------------------------------------------------------------------- #
def test_vendor_value_wins_where_present_and_plausible():
    # vendor serves a plausible expense ratio + fund size; static must NOT override them.
    f, fill = apply_static_fill(
        _etf(net_expense_ratio=0.09, total_assets=5.0e10),
        kind="etf", row=FRESH, today=TODAY)
    assert f.net_expense_ratio == 0.09          # vendor kept
    assert f.total_assets == 5.0e10             # vendor kept
    assert f.dividend_yield == 0.035            # vendor omitted -> static filled
    assert "net_expense_ratio" not in fill.filled
    assert "total_assets" not in fill.filled
    assert fill.filled["dividend_yield"] == "static: 2026-06-01, Schwab factsheet"


def test_implausible_vendor_value_yields_to_static():
    # a non-positive expense ratio / fund size can't be real -> static fills them.
    f, fill = apply_static_fill(
        _etf(net_expense_ratio=0.0, total_assets=-1.0, dividend_yield=1.5),
        kind="etf", row=FRESH, today=TODAY)
    assert f.net_expense_ratio == 0.06
    assert f.total_assets == 6.0e10
    assert f.dividend_yield == 0.035            # 1.5 (>100%) is implausible -> static
    assert set(fill.filled) == {"net_expense_ratio", "total_assets", "dividend_yield"}


def test_vendor_precedence_source_is_computed_not_static():
    f, fill = apply_static_fill(
        _etf(net_expense_ratio=0.09), kind="etf", row=FRESH, today=TODAY)
    fi = FactorInputs(ticker="SCHD", fundamentals=f, static=fill)
    assert compute_factor_outcomes(fi, ["expense_ratio"])["expense_ratio"][1] == "computed"


# --------------------------------------------------------------------------- #
# staleness abstains
# --------------------------------------------------------------------------- #
def test_stale_entry_is_not_served():
    f, fill = apply_static_fill(_etf(), kind="etf", row=STALE, today=TODAY)
    assert f.net_expense_ratio is None          # NOT filled from stale data
    assert f.total_assets is None
    assert f.dividend_yield is None
    assert fill.filled == {}
    assert fill.stale["net_expense_ratio"] == STALE_NOTE


def test_stale_source_surfaces_the_refresh_note():
    f, fill = apply_static_fill(_etf(), kind="etf", row=STALE, today=TODAY)
    fi = FactorInputs(ticker="SCHD", fundamentals=f, static=fill)
    val, src = compute_factor_outcomes(fi, ["expense_ratio"])["expense_ratio"]
    assert val is None
    assert src == STALE_NOTE


def test_is_stale_boundary_and_unparseable_date():
    ninety = STALE.__class__(**{**STALE.__dict__, "as_of": "2026-04-16"})  # exactly 90d
    assert is_stale(ninety, TODAY) is False
    older = STALE.__class__(**{**STALE.__dict__, "as_of": "2026-04-15"})   # 91 days
    assert is_stale(older, TODAY) is True
    bad = STALE.__class__(**{**STALE.__dict__, "as_of": "not-a-date"})
    assert is_stale(bad, TODAY) is True         # unverifiable freshness can't be trusted


# --------------------------------------------------------------------------- #
# a stock-kind name never reads the static layer
# --------------------------------------------------------------------------- #
def test_stock_kind_is_untouched_even_with_a_matching_row():
    stock = Fundamentals(ticker="SCHD", quote_type="EQUITY")     # wrong kind, same symbol
    f, fill = apply_static_fill(stock, kind="equity", row=FRESH, today=TODAY)
    assert f is stock                            # returned unchanged
    assert fill.filled == {} and fill.stale == {}


def test_missing_row_is_untouched():
    f, fill = apply_static_fill(_etf(), kind="etf", row=None, today=TODAY)
    assert f.net_expense_ratio is None
    assert not fill.touched


# --------------------------------------------------------------------------- #
# gather_factor_inputs integration + a fake adapter
# --------------------------------------------------------------------------- #
class _FakeAdapter(MarketDataAdapter):
    name = "fake"

    def __init__(self, fundamentals):
        self._f = fundamentals

    def get_fundamentals(self, ticker):
        return self._f

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_gather_fills_etf_from_injected_static_rows():
    adapter = _FakeAdapter(_etf())
    fi = gather_factor_inputs(adapter, "SCHD", today=TODAY,
                              static_rows={"SCHD": FRESH})
    assert fi.fundamentals.net_expense_ratio == 0.06
    assert compute_factor_outcomes(fi, ["fund_size"])["fund_size"][1] == \
        "static: 2026-06-01, Schwab factsheet"


def test_gather_leaves_stock_untouched():
    adapter = _FakeAdapter(Fundamentals(ticker="SCHD", quote_type="EQUITY"))
    fi = gather_factor_inputs(adapter, "SCHD", today=TODAY,
                              static_rows={"SCHD": FRESH})
    assert fi.fundamentals.net_expense_ratio is None
    assert fi.static is not None and not fi.static.touched


# --------------------------------------------------------------------------- #
# the committed seed file
# --------------------------------------------------------------------------- #
def test_default_path_points_at_repo_data_dir():
    assert DEFAULT_STATIC_PATH == \
        Path(__file__).resolve().parents[1] / "data" / "etf_static.csv"


def test_committed_seed_has_only_the_example_row():
    rows = load_static(DEFAULT_STATIC_PATH)
    # header-and-example-row only: real rows are human-verified and supplied separately,
    # so no real fund is touched and every existing run replays byte-identically.
    assert set(rows) == {"EXMPL"}
    ex = rows["EXMPL"]
    assert ex.share_class == "dist" and ex.domicile == "US"
    assert ex.expense_ratio == 0.06 and ex.distribution_yield == 0.035


def test_load_static_skips_comments_and_tolerates_missing_file(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text(
        "# a comment paragraph\n"
        "#\n"
        "ticker,expense_ratio,fund_size,distribution_yield,share_class,domicile,source,as_of\n"
        "vwrl,0.22,1.0e9,0.02,acc,IE,Vanguard,2026-05-01\n",
        encoding="utf-8")
    rows = load_static(p)
    assert set(rows) == {"VWRL"}                 # ticker upper-cased
    assert rows["VWRL"].fund_size == 1.0e9
    assert load_static(tmp_path / "nope.csv") == {}   # missing file -> {}
