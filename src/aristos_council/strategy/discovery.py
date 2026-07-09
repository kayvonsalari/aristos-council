"""Strategy discovery — classify the strategy YAMLs by SHAPE, so each surface lists
only what it can run.

Three kinds live under ``strategies/``:

- **rank**  — has a ``factors:`` list (the v2 rank engine drives it over a UNIVERSE).
  Runnable from Council Station's Universe Run tab. Loaded by ``load_rank_strategy``.
- **council** — has ``criteria:`` and is a standalone SINGLE-TICKER strategy
  (dividend_aristocrats_v1, growth_v1). Runnable from the single-ticker page.
- **lens** — also ``criteria:``, but referenced by some rank strategy's
  ``council_screen_strategy`` (the same-philosophy screen the council judges a ranked
  pick against). NOT a standalone strategy — hidden from both dropdowns.

The lens set is DERIVED (the union of rank strategies' ``council_screen_strategy``),
never hardcoded — add a rank strategy that points at a new screen and that screen is
classified as a lens automatically. Invalid YAMLs are skipped silently (the loaders
are the gatekeepers), exactly as the UI did before.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .loader import load_strategy
from .rank_loader import load_rank_strategy


@dataclass(frozen=True)
class StrategyInfo:
    id: str
    name: str
    path: Path
    kind: str          # "rank" | "council" | "lens"
    # Presentation (Sprint 4C): the friendly dropdown label (falls back to name), and
    # whether the config is UI-hidden (ui: hidden — legacy, not listed by default).
    display_name: str = ""
    hidden: bool = False

    @property
    def label(self) -> str:
        return self.display_name or self.name


def _raw_mapping(path: Path) -> dict | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def discover_strategies(strategies_dir: str | Path) -> list[StrategyInfo]:
    """Every loadable strategy YAML classified by shape, id-sorted.

    Shape decides KIND: ``factors:`` -> rank; otherwise ``criteria:`` -> council or
    lens (lens iff referenced by a rank strategy's ``council_screen_strategy``)."""
    d = Path(strategies_dir)
    rank: list[StrategyInfo] = []
    criteria: list[tuple[Path, str, str, str, bool]] = []   # (path,id,name,display,hidden)
    lens_ids: set[str] = set()

    for p in sorted(d.glob("*.yaml")):
        raw = _raw_mapping(p)
        if raw is None:
            continue
        if "factors" in raw:
            try:
                s = load_rank_strategy(p)
            except Exception:
                continue                            # invalid rank YAML -> skip
            rank.append(StrategyInfo(s.id, s.name, p, "rank",
                                     display_name=getattr(s, "display_name", ""),
                                     hidden=getattr(s, "ui", "") == "hidden"))
            if s.council_screen_strategy:
                lens_ids.add(s.council_screen_strategy)
        elif "criteria" in raw:
            try:
                s = load_strategy(p)
            except Exception:
                continue                            # invalid screen YAML -> skip
            criteria.append((p, s.id, s.name, getattr(s, "display_name", ""),
                             getattr(s, "ui", "") == "hidden"))

    out = list(rank)
    for path, sid, name, display, hidden in criteria:
        out.append(StrategyInfo(sid, name, path,
                                "lens" if sid in lens_ids else "council",
                                display_name=display, hidden=hidden))
    out.sort(key=lambda si: si.id)
    return out


def visible_rank_strategies(strategies_dir: str | Path) -> list[StrategyInfo]:
    """RANK strategies that are NOT ui-hidden — the default dropdown set (Sprint 4C)."""
    return [s for s in rank_strategies(strategies_dir) if not s.hidden]


def rank_strategies(strategies_dir: str | Path) -> list[StrategyInfo]:
    """RANK strategies — the Universe Run tab's dropdown."""
    return [s for s in discover_strategies(strategies_dir) if s.kind == "rank"]


def council_strategies(strategies_dir: str | Path) -> list[StrategyInfo]:
    """COUNCIL (single-ticker) strategies — the single-ticker page's dropdown."""
    return [s for s in discover_strategies(strategies_dir) if s.kind == "council"]


def lens_strategy_ids(strategies_dir: str | Path) -> set[str]:
    """The DERIVED set of council-lens screen ids (hidden from both dropdowns)."""
    return {s.id for s in discover_strategies(strategies_dir) if s.kind == "lens"}
