"""Universe manifests — a declared, versioned INPUT to a rank run.

A verdict is only reproducible if its inputs are declared. A universe is one of those
inputs (rank verdicts are universe-relative — the same name ranks differently in a
different universe), so the standing lists live in ``universes/*.yaml`` as versioned
manifests, not as ad-hoc pasted tickers that vanish after the run.

Manifest shape:
    id:          '<name>_v<n>'   (must encode a version, like a strategy)
    description: one line
    tickers:     [AAPL, MSFT, ...]   (normalized + de-duped at load)
    created:     'YYYY-MM-DD'
    rationale:   one line — why this list

An AD-HOC ticker list (the Universe Run tab's custom textarea, a CLI ticker argument)
is recorded as ``adhoc:<hex8>`` — a stable hash of the sorted normalized tickers — so
two identical ad-hoc runs are linkable without pretending they were a named manifest.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from .data.adapter import normalize_ticker


class Universe(BaseModel):
    id: str
    # Friendly, user-facing name for dropdowns/captions (e.g. "Growth 40"). The `id`
    # stays the STABLE record key (never renamed); this is display-only. Optional —
    # falls back to the id when absent.
    display_name: str = ""
    # Optional one-line role caption shown under the selected entry (e.g. "scoreboard
    # universe — graded quarterly"). Display-only.
    role: str = ""
    description: str = ""
    tickers: list[str] = Field(min_length=1)
    created: str = ""
    rationale: str = ""
    # NOT a manifest field — never written to YAML. Set True at discovery time for a
    # personal list found under ``universes/local/`` (UNIED-1). Drives the "(local)" tag
    # in selectors; local lists are gitignored portfolio-class data, so they never ride a
    # commit by default.
    local: bool = False

    @field_validator("id")
    @classmethod
    def _id_versioned(cls, v: str) -> str:
        if not re.search(r"_v\d+$", v):
            raise ValueError(f"universe id '{v}' must encode a version, e.g. '..._v1'")
        return v

    @field_validator("tickers")
    @classmethod
    def _normalize(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for t in v:
            nt = normalize_ticker(t)
            if nt and nt not in seen:
                seen.add(nt)
                out.append(nt)
        if not out:
            raise ValueError("universe has no valid tickers")
        return out


def load_universe(path: str | Path) -> Universe:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"universe manifest not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"universe manifest {p} did not parse to a mapping")
    return Universe.model_validate(raw)


# Personal, gitignored lists live in this subdirectory of the universes dir (UNIED-1).
LOCAL_SUBDIR = "local"


def load_universe_by_id(universe_id: str, universes_dir: str | Path) -> Universe:
    """Resolve a manifest id to its ``<universes_dir>/<id>.yaml``, falling back to the
    ``local/`` subdirectory (UNIED-1 personal lists). A missing file is a CLEAR error
    naming the id and the searched directory (not a bare FileNotFound)."""
    d = Path(universes_dir)
    path = d / f"{universe_id}.yaml"
    is_local = False
    if not path.exists():
        local_path = d / LOCAL_SUBDIR / f"{universe_id}.yaml"
        if local_path.exists():
            path, is_local = local_path, True
        else:
            known = ", ".join(u.id for u in list_universes(d)) or "(none)"
            raise ValueError(f"unknown universe id '{universe_id}' — no {path.name} under "
                             f"{d}. Known manifests: {known}")
    u = load_universe(path)
    if u.id != universe_id:
        raise ValueError(f"universe file {path.name} declares id '{u.id}', not "
                         f"'{universe_id}' — the filename must match the id")
    u.local = is_local
    return u


def list_universes(universes_dir: str | Path) -> list[Universe]:
    """Every loadable manifest, id-sorted. Invalid YAMLs are skipped silently (the
    loader is the gatekeeper), consistent with strategy discovery. Personal lists under
    the ``local/`` subdirectory (UNIED-1) are discovered too and marked ``local=True`` so
    selectors can tag them '(local)'."""
    d = Path(universes_dir)
    if not d.exists():
        return []
    out: list[Universe] = []
    for p in sorted(d.glob("*.yaml")):
        try:
            out.append(load_universe(p))
        except Exception:
            continue
    local_dir = d / LOCAL_SUBDIR
    if local_dir.exists():
        for p in sorted(local_dir.glob("*.yaml")):
            try:
                u = load_universe(p)
            except Exception:
                continue
            u.local = True
            out.append(u)
    return sorted(out, key=lambda u: u.id)


def adhoc_universe_id(tickers: list[str]) -> str:
    """A stable id for an ad-hoc list: ``adhoc:<hex8>`` over the SORTED, normalized,
    de-duped tickers — so identical ad-hoc runs (regardless of paste order) link, while
    never masquerading as a named manifest."""
    norm = sorted({normalize_ticker(t) for t in tickers if normalize_ticker(t)})
    digest = hashlib.sha256(",".join(norm).encode("utf-8")).hexdigest()[:8]
    return f"adhoc:{digest}"
