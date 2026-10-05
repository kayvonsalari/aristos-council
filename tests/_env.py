"""Facts about the machine a test runs on, for tests that drive the Streamlit app.

``tests/test_app.py``-style tests start the real app and expect the owner's own saved ticker lists
(``universes/local/*.yaml``, git-ignored) to be offered. A fresh checkout / CI has none, the Run
button is then disabled and the test cannot proceed - so such tests skip with this reason there.
"""
from pathlib import Path

import pytest

_LOCAL = Path(__file__).resolve().parents[1] / "universes" / "local"
HAS_LOCAL_SAVED_LISTS = _LOCAL.is_dir() and any(_LOCAL.glob("*.yaml"))
needs_local_saved_list = pytest.mark.skipif(
    not HAS_LOCAL_SAVED_LISTS,
    reason="needs a saved ticker list under universes/local (the owner's own, git-ignored); "
           "absent on a fresh checkout and on CI")
