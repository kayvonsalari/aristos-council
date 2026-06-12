"""Strategy versioning — "edit as a new version", never mutate a published file.

The single hard rule (mirrors the YAML's own header comment): a strategy file,
once published, is immutable. Recorded verdicts and run reports reference their
``strategy_id``; rewriting the file behind that id would silently change what a
historical decision claims it was made under. So editing a strategy ALWAYS
produces a new ``<id>_v<n+1>.yaml`` and ``save_strategy`` refuses to overwrite.

Validation happens up front, through the same pydantic contract the loader
enforces: ``make_new_version`` constructs (and thus validates) a ``Strategy``
before anything reaches disk, and ``save_strategy`` refuses to write over an
existing file. An out-of-range edit fails as a ``ValidationError`` at build
time, not as a corrupt file on disk.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .loader import Strategy


def bump_version(strategy: Strategy) -> tuple[str, int]:
    """The (id, version) for the next version of ``strategy``.

    The id convention enforced by the loader is ``<base>_v<N>``, so the new id is
    the base with the incremented number re-appended.
    """
    base = strategy.id.rsplit("_v", 1)[0]
    new_version = strategy.version + 1
    return f"{base}_v{new_version}", new_version


def make_new_version(strategy: Strategy, updates: dict | None = None) -> Strategy:
    """Build the next version of ``strategy`` with ``updates`` applied.

    ``updates`` is a (possibly nested) mapping over the strategy fields, e.g.::

        {"criteria": {"min_dividend_yield": 0.03}, "veto": {"min_confidence": 0.7}}

    Top-level sections (``criteria``/``policy``/``veto``) are merged key-by-key so
    a caller can change one threshold without restating the rest. The id and
    version are owned by the bump and cannot be set through ``updates``.

    Raises ``pydantic.ValidationError`` if any edited value is out of range —
    BEFORE any file is written.
    """
    data = strategy.model_dump(mode="json")
    new_id, new_version = bump_version(strategy)
    data["id"] = new_id
    data["version"] = new_version

    updates = dict(updates or {})
    # the bump owns identity — never let an edit hijack it
    updates.pop("id", None)
    updates.pop("version", None)
    for key, val in updates.items():
        if isinstance(val, dict) and isinstance(data.get(key), dict):
            data[key] = {**data[key], **val}
        else:
            data[key] = val

    return Strategy.model_validate(data)


def save_strategy(strategy: Strategy, strategies_dir: str | Path) -> Path:
    """Write ``strategy`` to ``<strategies_dir>/<id>.yaml`` as a new file.

    Refuses (``FileExistsError``) to write over an existing file — the
    immutability guarantee. Re-validates the serialised form through the loader's
    contract before touching disk, so a saved file is always loadable.
    """
    path = Path(strategies_dir) / f"{strategy.id}.yaml"
    if path.exists():
        raise FileExistsError(
            f"strategy file already exists, refusing to overwrite: {path}. "
            "Published strategies are immutable; bump to a new version."
        )

    text = yaml.safe_dump(strategy.model_dump(mode="json"), sort_keys=False)
    # belt-and-braces: prove the serialised text re-validates before it lands
    Strategy.model_validate(yaml.safe_load(text))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
