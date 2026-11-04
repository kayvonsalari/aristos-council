"""JUDGE-PANEL-1 - the panel's configuration: which models, read from ``data/judges.yaml``."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = _ROOT / "data" / "judges.yaml"
PROMPT_PATH = Path(__file__).resolve().parent / "judge_prompt_v3.md"
PROMPT_VERSION = "judge_prompt_v3"
# ABSOLUTE-PAGE-1 (docs/DESIGN_absolute.md section k): the question the judges answer. A record carries the
# name of the question it answered; a record with none (every row written before 2026-11-04) answers the OLD
# question (BUY, HOLD or SELL) and is never mixed into the new agreement figure.
QUESTION_VERSION = "ten_year_hurdle_v1"
OLD_QUESTION_VERSION = "buy_hold_sell_v2"
QUESTION_START_DATE = "2026-11-04"      # the 12-month, 30-name bar of docs/JUDGE_PANEL.md restarts on this date
QUESTION_TEXT = ("Using only the evidence pack and anything you know about the company, which you must label, would "
                 "a person who buys this company at today's price and holds it for ten years more likely than not "
                 "earn more than the ten-year government bond yield of the company's currency plus 5 points a year? "
                 "Answer YES, NO or ABSTAIN, with a confidence and the one fact that decided it.")

FAMILIES = ("anthropic", "openai", "google")
DEFAULT_CALLS_PER_JUDGE = 2
DEFAULT_TIMEOUT_SECONDS = 90      # only when data/judges.yaml omits it; the setting lives in the yaml


@dataclass(frozen=True)
class JudgeSpec:
    family: str          # anthropic | openai | google
    label: str           # what the page calls it
    model: str
    key_env: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS   # per-call client timeout (data/judges.yaml)
    thinking_level: str = ""     # Gemini only: "low" etc.; "" leaves the provider default

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
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    def runnable(self, environ=None) -> tuple:
        return tuple(j for j in self.judges if j.has_key(environ))

    def expected_calls(self, environ=None) -> int:
        """The model calls a run will make: runnable judges x calls each (a malformed reply is
        retried once, so the true number can be higher; it is never lower)."""
        return len(self.runnable(environ)) * self.calls_per_judge


def load_config(path: Optional[Path] = None) -> PanelConfig:
    import yaml

    doc = yaml.safe_load(Path(path or CONFIG_PATH).read_text(encoding="utf-8")) or {}
    timeout = max(1, int(doc.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS))
    judges = []
    for entry in doc.get("judges") or []:
        family = str(entry.get("family") or "").strip().lower()
        if family not in FAMILIES:
            raise ValueError(f"judges.yaml: family must be one of {', '.join(FAMILIES)}, got {family!r}")
        model = str(entry.get("model") or "").strip()
        if not model:
            raise ValueError(f"judges.yaml: the {family} judge has no model")
        judges.append(JudgeSpec(family=family, label=str(entry.get("label") or family).strip(),
                                model=model, key_env=str(entry.get("key_env") or "").strip(),
                                timeout_seconds=max(1, int(entry.get("timeout_seconds") or timeout)),
                                thinking_level=str(entry.get("thinking_level") or "").strip().lower()))
    if len({j.family for j in judges}) != len(judges):
        raise ValueError("judges.yaml: one judge per model family")
    calls = int(doc.get("calls_per_judge") or DEFAULT_CALLS_PER_JUDGE)
    return PanelConfig(judges=tuple(judges), calls_per_judge=max(1, calls), timeout_seconds=timeout)


def question_of(record) -> str:
    """The question a saved panel record (a dict) or a log row answered: its ``question``, or - for every record
    written before 4 Nov 2026, which has none - the OLD question (BUY, HOLD or SELL)."""
    got = record.get("question") if isinstance(record, dict) else getattr(record, "question", "")
    return got or OLD_QUESTION_VERSION


def prompt_text() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")
