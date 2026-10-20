"""JUDGE-PANEL-1 - the panel's configuration: which models, read from ``data/judges.yaml``."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = _ROOT / "data" / "judges.yaml"
PROMPT_PATH = Path(__file__).resolve().parent / "judge_prompt_v2.md"
PROMPT_VERSION = "judge_prompt_v2"

FAMILIES = ("anthropic", "openai", "google")
DEFAULT_CALLS_PER_JUDGE = 2


@dataclass(frozen=True)
class JudgeSpec:
    family: str          # anthropic | openai | google
    label: str           # what the page calls it
    model: str
    key_env: str

    def has_key(self, environ=None) -> bool:
        env = os.environ if environ is None else environ
        return bool((env.get(self.key_env) or "").strip())

    def key(self, environ=None) -> str:
        env = os.environ if environ is None else environ
        return (env.get(self.key_env) or "").strip()


@dataclass(frozen=True)
class PanelConfig:
    judges: tuple
    calls_per_judge: int = DEFAULT_CALLS_PER_JUDGE

    def runnable(self, environ=None) -> tuple:
        return tuple(j for j in self.judges if j.has_key(environ))

    def expected_calls(self, environ=None) -> int:
        """The model calls a run will make: runnable judges x calls each (a malformed reply is
        retried once, so the true number can be higher; it is never lower)."""
        return len(self.runnable(environ)) * self.calls_per_judge


def load_config(path: Optional[Path] = None) -> PanelConfig:
    import yaml

    doc = yaml.safe_load(Path(path or CONFIG_PATH).read_text(encoding="utf-8")) or {}
    judges = []
    for entry in doc.get("judges") or []:
        family = str(entry.get("family") or "").strip().lower()
        if family not in FAMILIES:
            raise ValueError(f"judges.yaml: family must be one of {', '.join(FAMILIES)}, got {family!r}")
        model = str(entry.get("model") or "").strip()
        if not model:
            raise ValueError(f"judges.yaml: the {family} judge has no model")
        judges.append(JudgeSpec(family=family, label=str(entry.get("label") or family).strip(),
                                model=model, key_env=str(entry.get("key_env") or "").strip()))
    if len({j.family for j in judges}) != len(judges):
        raise ValueError("judges.yaml: one judge per model family")
    calls = int(doc.get("calls_per_judge") or DEFAULT_CALLS_PER_JUDGE)
    return PanelConfig(judges=tuple(judges), calls_per_judge=max(1, calls))


def prompt_text() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")
