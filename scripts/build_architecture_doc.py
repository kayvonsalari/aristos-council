"""ARCH-DOC-2 — regenerate the lens and badge tables of docs/aristos-architecture.html.

The page used to restate each lens's question and each badge's meaning in its own words, and
drifted (Value + Momentum's 12% capital rule and Growth's three rules were missing; the badge
table named the backtest's internal verdicts instead of the app's five labels). Those two
tables are now GENERATED from the same text the app shows:

* each lens row  <- the strategy's own ``asks`` caption (``demo_surface.lens_caption``), in the
  picker's order, split into stock and ETF lenses exactly as the app's Stocks / ETFs switch does;
* each badge row <- ``backtest.BADGE_MEANINGS`` (the five labels the app shows).

    python -m scripts.build_architecture_doc          # rewrite the page in place
    python -m scripts.build_architecture_doc --check  # exit 1 if the page is out of date

``tests/test_architecture_doc.py`` runs the --check, so the page cannot drift again.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "aristos-architecture.html"
STRATEGIES_DIR = ROOT / "strategies"

LENSES_RE = re.compile(r"(<!-- GEN:LENSES -->).*?(<!-- /GEN:LENSES -->)", re.S)
BADGES_RE = re.compile(r"(<!-- GEN:BADGES -->).*?(<!-- /GEN:BADGES -->)", re.S)

# badge label -> the CSS class the page styles it with
_BADGE_CLASS = {"proven here": "b-proven", "promising here": "b-luck",
                "no edge shown here": "b-notproven", "worked against you here": "b-against",
                "untested here": "b-insufficient"}


def _visible_lenses():
    from aristos_council.strategy.discovery import rank_strategies
    from aristos_council.strategy.picker import strategy_choices
    from aristos_council.strategy.rank_loader import load_rank_strategy

    loaded = []
    for info in rank_strategies(STRATEGIES_DIR):
        try:
            loaded.append(load_rank_strategy(info.path))
        except Exception:                                   # noqa: BLE001 - as the app does
            continue
    return [c.strategy for c in strategy_choices(loaded, show_validation=False)]


def _lens_name(strategy) -> str:
    from aristos_council.demo_surface import strategy_label
    return html.escape(strategy_label(strategy))


def lens_tables_html() -> str:
    from aristos_council.demo_surface import is_etf_lens, lens_caption, strategy_role

    lenses = _visible_lenses()
    stocks = [s for s in lenses if not is_etf_lens(s)]
    etfs = [s for s in lenses if is_etf_lens(s)]

    def row(s) -> str:
        note = ""
        if (getattr(s, "kind", "selector") or "selector") == "check":
            note = ' <span class="note">(a check, never a vote — see below)</span>'
        elif strategy_role(s) == "flagship":
            note = ' <span class="note">(the flagship)</span>'
        return (f"  <tr><td><strong>{_lens_name(s)}</strong>{note}</td>\n"
                f"      <td>{html.escape(lens_caption(s))}</td></tr>")

    def table(head: str, group) -> str:
        return (f"<table>\n  <tr><th>{head}</th><th>What it asks</th></tr>\n"
                + "\n".join(row(s) for s in group) + "\n</table>")

    return "\n" + table("Lens", stocks) + "\n" + table("ETF lens", etfs) + "\n"


def badge_table_html() -> str:
    from aristos_council.backtest import BADGE_MEANINGS

    rows = [f'  <tr><td><span class="badge {_BADGE_CLASS.get(label, "b-notproven")}">'
            f"{html.escape(label)}</span></td>\n      <td>{html.escape(meaning)}</td></tr>"
            for label, meaning in BADGE_MEANINGS.items()]
    return "\n<table>\n  <tr><th>Badge</th><th>Meaning</th></tr>\n" + "\n".join(rows) + "\n</table>\n"


def render(page: str) -> str:
    for pattern, body in ((LENSES_RE, lens_tables_html()), (BADGES_RE, badge_table_html())):
        if not pattern.search(page):
            raise SystemExit(f"marker missing for {pattern.pattern[:30]}")
        page = pattern.sub(lambda m, b=body: m.group(1) + b + m.group(2), page, count=1)
    return page


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    current = DOC.read_text(encoding="utf-8")
    fresh = render(current)
    if args.check:
        if fresh != current:
            print("docs/aristos-architecture.html is out of date: "
                  "run python -m scripts.build_architecture_doc")
            return 1
        return 0
    DOC.write_text(fresh, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
