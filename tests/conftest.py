"""Repo-root on sys.path (pyproject's pythonpath only adds src/) so tests can import the
experiments/ package (NARR-INDEP-TEST) without turning it into an installed dependency."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
