"""COHORT-1 — the cohort builder never imports a model. Asserted, not asserted-in-prose.

The builder's whole claim is that a cohort is MECHANICAL: a rule, a source, four removals,
four checks. The moment a model can be reached from this package, that claim needs an
asterisk. So the import graph is a test.

This runs in a SUBPROCESS on purpose. By the time the rest of the suite has run, langchain
and langgraph are already in ``sys.modules`` because other modules legitimately import
them, and an in-process check would pass no matter what this package did.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# A model can be reached through any of these. langchain_core arrives with langgraph, so
# it is named too — the test is about what this package DRAGS IN, not about what is
# installed.
FORBIDDEN = ("langchain", "langchain_core", "langchain_anthropic", "langgraph",
             "anthropic", "openai")


def _import_probe(statement: str) -> set[str]:
    """Run ``statement`` in a fresh interpreter; return the forbidden roots it imported."""
    code = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {str(ROOT / "src")!r})
        {statement}
        roots = {{name.split(".")[0] for name in sys.modules}}
        print(json.dumps(sorted(roots & set({list(FORBIDDEN)!r}))))
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=ROOT, timeout=180)
    assert out.returncode == 0, out.stderr
    import json
    return set(json.loads(out.stdout.strip().splitlines()[-1]))


def test_importing_the_cohorts_package_imports_no_model_library():
    assert _import_probe("import aristos_council.cohorts") == set()


@pytest.mark.parametrize("module", [
    "aristos_council.cohorts.builder",
    "aristos_council.cohorts.definitions",
    "aristos_council.cohorts.source",
    "aristos_council.cohorts.cleanup",
    "aristos_council.cohorts.quality",
    "aristos_council.cohorts.freeze",
    "aristos_council.cohorts.report",
    "aristos_council.cohorts.symbols",
    "aristos_council.cohorts.__main__",
])
def test_no_single_cohorts_module_pulls_in_a_model_library(module):
    """Each module on its own, because a package ``__init__`` that imports four of five
    would let the fifth carry an LLM import unnoticed."""
    assert _import_probe(f"import {module}") == set()


def test_the_cli_can_be_parsed_without_a_model_library_present():
    """Building the parser is what ``--help`` does, and it must not need the world."""
    assert _import_probe(
        "import aristos_council.cohorts.__main__ as m; m.build_parser()") == set()


def test_no_cohorts_source_file_names_a_model_library_even_in_a_lazy_import():
    """A lazy import inside a function would slip past the import probe. Grep for it.

    The pipeline IS imported lazily by ``default_ranker`` — that is deliberate and is how
    the ranker-only quality run happens — but the pipeline is a deterministic entry point,
    and nothing in this package may name a model library itself.
    """
    offenders = []
    for path in sorted((ROOT / "src" / "aristos_council" / "cohorts").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            if any(stripped.startswith((f"import {m}", f"from {m}")) for m in FORBIDDEN):
                offenders.append(f"{path.name}: {stripped}")
    assert offenders == []


def test_the_ranker_the_quality_report_uses_is_asked_for_ranker_only():
    """The one place the pipeline is reached, it is reached with the LLM switched off.

    A source-level assertion rather than a behavioural one, because behaviourally proving
    it would mean running the real pipeline — which is exactly the network call the rest of
    the suite forbids.
    """
    source = (ROOT / "src" / "aristos_council" / "cohorts" / "builder.py").read_text(
        encoding="utf-8")
    call = source.split("run_rank_pipeline(", 1)[1].split(")", 1)[0]
    assert "ranker_only=True" in call
    assert 'council_mode="ranker-only"' in call
    assert "runners" not in call               # never hands the pipeline a runner set
