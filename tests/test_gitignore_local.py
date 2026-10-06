"""LOCAL-IGNORE (Batch 20 C12): data/local/ is ignored whole, except the one file the repo tracks there.

``data/local/docs_offline/`` showed as untracked because only named subfolders were ignored; anything
else dropped under data/local/ (personal or machine-written) could be ``git add -A``-ed by accident."""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not (ROOT / ".git").exists(), reason="needs the git checkout")

# every file the repo deliberately tracks under data/local/ - add one here only on purpose
TRACKED_ON_PURPOSE = {"data/local/cohorts/definitions.yaml"}


def _ignored(path: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT).returncode == 0


@pytest.mark.parametrize("path", [
    "data/local/docs_offline/anything.html", "data/local/notes.txt", "data/local/new_tool/x.csv",
    "data/local/cohorts/watch.yaml", "data/local/cohorts/some_cohort/members.csv",
    "data/local/market_index/index.parquet", "data/local/paper_trade_enter.log"])
def test_anything_under_data_local_is_ignored(path):
    assert _ignored(path)


def test_the_cohort_rule_file_stays_trackable():
    assert not _ignored("data/local/cohorts/definitions.yaml")


def test_the_only_tracked_files_under_data_local_are_the_deliberate_ones():
    out = subprocess.run(["git", "ls-files", "data/local"], cwd=ROOT, capture_output=True, text=True)
    assert set(out.stdout.split()) == TRACKED_ON_PURPOSE
