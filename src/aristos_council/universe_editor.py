"""Universe editor — build, clone, and save custom ticker lists (UNIED-1).

Pure, Streamlit-free core behind Council Station's Universe Editor. Two write paths:

- **Run once** (no file): the parsed tickers go through the existing AD-HOC pipeline
  path (``universe_id=None`` -> ``adhoc:<hex8>``), so an editor run is fingerprinted and
  linkable exactly like a Custom paste — the ad-hoc path is REUSED, never forked.
- **Save**: writes ``universes/local/<id>.yaml`` (created date + rationale). The ``local/``
  directory carries its own ``.gitignore`` (``*`` + ``!.gitignore``) so personal lists —
  portfolio-class data — can never ride a commit by default (the 2026-07-09 incident
  class, closed STRUCTURALLY, not by a reviewer remembering).

Guardrails:
- A universe whose id appears in the graded/scoreboard set (the snapshot CSV) is
  CLONE-ONLY: editing it in place would silently rewrite what a past verdict was graded
  against, so a save under a graded id is refused with "graded — clone to modify".
- A saved id must not collide with ANY existing universe id (top-level or local).
- Ticker validation is LAZY (house convention): unresolvable tickers surface as
  UNRATEABLE in the run, never blocking a save.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .data.adapter import normalize_ticker
from .universe import LOCAL_SUBDIR, Universe, list_universes

# The .gitignore dropped into universes/local/ on first use. ``*`` ignores every file in
# the directory; ``!.gitignore`` re-includes THIS file so the ignore rule itself travels
# with the repo (the structural guarantee ships; it is not a per-clone chore).
_GITIGNORE_BODY = (
    "# Personal universe lists — portfolio-class data (UNIED-1). Never committed by\n"
    "# default: the 2026-07-09 incident class, closed structurally. Remove a name here\n"
    "# only if you deliberately intend it to be public.\n"
    "*\n"
    "!.gitignore\n"
)


def parse_ticker_lines(text: str) -> list[str]:
    """Editor text -> normalized, de-duped, ordered tickers. One per line, but tolerant of
    spaces/commas on a line; ``#`` starts a comment (whole-line or trailing). Blank and
    comment-only lines are dropped. This is a SUPERSET of the Custom-paste parser (which
    has no comments), so feeding the result to ``adhoc_universe_id`` gives the SAME
    fingerprint the Custom path would — the ad-hoc run path is unchanged."""
    seen: set[str] = set()
    out: list[str] = []
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0]                 # strip a trailing/whole-line comment
        for tok in line.replace(",", " ").split():
            nt = normalize_ticker(tok)
            if nt and nt not in seen:
                seen.add(nt)
                out.append(nt)
    return out


def suggest_clone_id(base_id: str, existing_ids) -> str:
    """A non-colliding version-bumped id for a clone: ``foo_v1`` -> the next free
    ``foo_v<n>``. A base id with no ``_v<n>`` suffix gets ``<id>_v1``. The result still
    encodes a version (so ``Universe`` accepts it) and avoids every existing id."""
    m = re.match(r"^(.*)_v(\d+)$", base_id)
    stem, n = (m.group(1), int(m.group(2))) if m else (base_id, 0)
    existing = set(existing_ids)
    n += 1
    while f"{stem}_v{n}" in existing:
        n += 1
    return f"{stem}_v{n}"


def graded_universe_ids(snapshots_csv: str | Path) -> set[str]:
    """The graded/scoreboard set — universe ids that appear in the prospective-scoreboard
    snapshot CSV. A graded universe is a frozen, pre-registered input to a forward-return
    test, so it is CLONE-ONLY in the editor. Missing/empty CSV -> empty set."""
    from .scoreboard import read_rows

    p = Path(snapshots_csv)
    if not p.exists():
        return set()
    return {r.universe_id for r in read_rows(p) if r.universe_id}


def existing_universe_ids(universes_dir: str | Path) -> set[str]:
    """Every universe id currently discoverable (top-level manifests + local lists) — the
    collision set a new save must avoid."""
    return {u.id for u in list_universes(universes_dir)}


def local_dir(universes_dir: str | Path) -> Path:
    return Path(universes_dir) / LOCAL_SUBDIR


def ensure_local_dir(universes_dir: str | Path) -> Path:
    """Create ``universes/local/`` (if absent) with its ``.gitignore`` inside. Idempotent:
    an existing ``.gitignore`` is left untouched. Returns the directory path."""
    d = local_dir(universes_dir)
    d.mkdir(parents=True, exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text(_GITIGNORE_BODY, encoding="utf-8")
    return d


def _dump_manifest_yaml(u: Universe, *, created: str) -> str:
    """Serialize a validated manifest to YAML in the house field order. ``local`` is never
    written (it is a discovery-time flag, not a manifest field)."""
    data: dict = {"id": u.id}
    if u.display_name:
        data["display_name"] = u.display_name
    if u.role:
        data["role"] = u.role
    if u.description:
        data["description"] = u.description
    data["created"] = created
    if u.rationale:
        data["rationale"] = u.rationale
    data["tickers"] = list(u.tickers)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def save_local_universe(universes_dir: str | Path, *, id: str, tickers: list[str],
                        created: str, display_name: str = "", rationale: str = "",
                        description: str = "", role: str = "",
                        graded_ids: set[str] | frozenset[str] | None = None) -> Path:
    """Validate and write a personal list to ``universes/local/<id>.yaml``.

    Raises ``ValueError`` (nothing written) when:
    - ``id`` is in ``graded_ids`` — graded, clone-only ("graded — clone to modify");
    - ``id`` collides with an existing universe id (top-level or local);
    - the manifest is invalid (id must encode ``_v<n>``, at least one valid ticker) — the
      ``Universe`` model is the single validator, reused so the file stays loadable.

    Ticker validation is LAZY by design: normalization drops empty tokens, but a
    syntactically-valid symbol that no provider can resolve is NOT rejected here — it
    surfaces as UNRATEABLE in the run (house convention), never blocking the save."""
    universes_dir = Path(universes_dir)
    graded = set(graded_ids or ())

    if id in graded:
        raise ValueError(
            f"universe '{id}' is graded — clone to modify. A graded universe is a frozen "
            "scoreboard input; save your edits under a new id instead.")

    # The Universe model is the one validator (id versioning, ticker normalize/dedupe) —
    # reused so a saved file is guaranteed loadable by the same path everything else uses.
    u = Universe(id=id, display_name=display_name.strip(), role=role.strip(),
                 description=description.strip(), tickers=list(tickers),
                 created=created, rationale=rationale.strip())

    if u.id in existing_universe_ids(universes_dir):
        raise ValueError(
            f"universe id '{u.id}' already exists — pick a new id (saved ids must be "
            "unique; clone into a fresh version like '..._v2').")

    d = ensure_local_dir(universes_dir)
    path = d / f"{u.id}.yaml"
    path.write_text(_dump_manifest_yaml(u, created=created), encoding="utf-8")
    return path
