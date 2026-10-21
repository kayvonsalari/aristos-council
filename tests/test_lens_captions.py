"""LENS-CAPTIONS-1 - a lens's caption must name every gate that can keep a company out of it.

The Growth caption read "sales up at least 10% a year, and not overpaying for that growth", but
growth_v2's screen also excludes on return on invested capital below 12% - AstraZeneca (10.5%) was
"screened out" by a rule the caption never mentioned. A reader choosing a lens by its caption was
choosing blind.

The GATING criteria of a lens are what its YAML says can exclude a name before it is ranked: the
criteria of its prefilter screen, the size floor (``min_market_cap``), the sector exclusions and the
sector scope. Each has a plain-words keyword (and, where it has a threshold, that threshold in words);
the caption must carry them. A NEW gating criterion with no keyword fails this test until someone
says how it reads in plain words - that is the point.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

STRATEGIES = Path(__file__).resolve().parents[1] / "strategies"


def _load(strategy_id: str) -> dict:
    return yaml.safe_load((STRATEGIES / f"{strategy_id}.yaml").read_text(encoding="utf-8"))


def _rank_lenses() -> list[dict]:
    out = []
    for path in sorted(STRATEGIES.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and "factors" in doc and doc.get("ui") != "hidden":
            out.append(doc)
    return out


# criterion -> the words (any one) that name it in a caption
KEYWORDS = {
    "min_revenue_cagr": ("sales", "revenue"),
    "min_roic": ("capital",),
    "max_peg_ratio": ("overpaying", "growth rate"),
    "min_market_cap": ("worth at least",),
    "min_dividend_yield": ("yield",),
    "max_payout_ratio_fcf": ("free cash flow",),
    "min_price_momentum": ("share price not down", "no recent fall"),
    "min_dividend_streak": ("raised for",),
    "max_debt_to_market_cap": ("debt",),
    "max_dividend_cuts": ("not cut",),
    "max_payout_ratio": ("payout", "paid out"),
    "exclude_sectors": ("not a bank", "not banks"),
    "include_sectors": ("banks", "financial"),
}
# criterion -> the threshold as a reader says it (given the YAML's number), or "" when none is stated
THRESHOLD_WORDS = {
    "min_revenue_cagr": lambda v: f"{v:.0%}",
    "min_roic": lambda v: f"{v:.0%}",
    "min_market_cap": lambda v: f"${v / 1e9:g}bn",
    "min_dividend_yield": lambda v: f"{v:.1%}".replace(".0%", "%"),
    "max_payout_ratio_fcf": lambda v: f"{v:.0%}",
    "min_price_momentum": lambda v: f"{abs(v):.0%}" if v else "",
    "min_dividend_streak": lambda v: f"{v:g} years",
    "max_dividend_cuts": lambda v: "five years" if v == 5 else f"{v:g} years",
    "max_peg_ratio": lambda v: "twice" if v == 2 else "",
    "max_debt_to_market_cap": lambda v: "",
}


def gating_criteria(lens: dict) -> list[tuple[str, object]]:
    """``[(criterion, threshold), ...]`` that can exclude a name from ``lens`` before it is ranked."""
    gates: list[tuple[str, object]] = []
    screen_id = lens.get("council_screen_strategy")
    if screen_id and lens.get("prefilter_screen"):
        for crit in _load(screen_id).get("criteria", []):
            gates.append((crit["name"], crit.get("threshold")))
    if lens.get("min_market_cap") is not None and not any(n == "min_market_cap" for n, _ in gates):
        gates.append(("min_market_cap", float(lens["min_market_cap"])))   # YAML reads 5.0e9 as text
    if lens.get("exclude_sectors"):
        gates.append(("exclude_sectors", None))
    if lens.get("include_sectors"):
        gates.append(("include_sectors", None))
    if lens.get("max_payout_ratio") is not None:
        gates.append(("max_payout_ratio", float(lens["max_payout_ratio"])))
    return gates


def missing_gates(caption: str, lens: dict) -> list[str]:
    """The gates ``caption`` fails to name in plain words (and, where it has one, the threshold)."""
    text = (caption or "").lower()
    missing = []
    for name, threshold in gating_criteria(lens):
        words = KEYWORDS.get(name)
        assert words, f"gating criterion {name!r} has no plain-words keyword in this test: add one"
        if not any(w in text for w in words):
            missing.append(name)
            continue
        expected = THRESHOLD_WORDS.get(name)
        if expected is not None and threshold is not None:
            said = expected(threshold)
            if said and said.lower() not in text:
                missing.append(f"{name} ({said})")
    return missing


@pytest.mark.parametrize("lens", _rank_lenses(), ids=lambda d: d["id"])
def test_every_lens_caption_names_every_gate_that_can_exclude_a_company(lens):
    caption = (lens.get("asks") or "").strip()
    assert caption, f"{lens['id']} has no caption"
    assert missing_gates(caption, lens) == [], (lens["id"], caption)


def test_the_old_growth_caption_would_have_failed_on_the_roic_rule():
    old = ("Growing fast at a fair price: sales up at least 10% a year, and not overpaying for "
           "that growth.")
    missing = missing_gates(old, _load("growth_garp_v2"))
    assert "min_roic" in missing[0] or any(m.startswith("min_roic") for m in missing)
    assert any(m.startswith("min_market_cap") for m in missing)      # the $5bn floor too


def test_the_growth_caption_names_the_twelve_percent_rule():
    caption = _load("growth_garp_v2")["asks"]
    assert "earning at least 12% on its capital" in " ".join(caption.split())


def test_every_gating_criterion_in_the_shipped_lenses_has_a_keyword():
    """A new gate cannot be added silently: it must be given words here, and so a caption."""
    for lens in _rank_lenses():
        for name, _threshold in gating_criteria(lens):
            assert name in KEYWORDS, (lens["id"], name)


def test_a_lens_with_no_gates_needs_no_gate_words():
    for lens in _rank_lenses():
        if not gating_criteria(lens):
            assert lens.get("asks")            # e.g. the ETF lenses: a caption, and nothing to add
