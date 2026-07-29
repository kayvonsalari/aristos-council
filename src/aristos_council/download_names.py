"""Download filename scheme (ITEM 6).

The old `universe_<strategy>.md` name collides across runs and modes (the browser then
appends "(4)", "(5)"…). These helpers build unique, self-describing names that carry the
strategy id, the run MODE, and the run-start timestamp (Europe/Berlin), so a folder of
downloads sorts and reads cleanly. Pure functions — unit-tested without Streamlit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Timestamps are captured in UTC at run start; the filename shows Europe/Berlin (the UI's
# display zone), matching every other user-facing time in the app.
_BERLIN = ZoneInfo("Europe/Berlin")

# Executed council-mode -> the short tag used in filenames. ranker | narrator | council.
_MODE_TAG = {
    "ranker-only": "ranker", "ranker": "ranker",
    "narrator": "narrator",
    "second_opinion": "council", "council": "council",
}


def mode_tag(council_mode: str) -> str:
    """Map an executed council mode to its filename tag (ranker | narrator | council)."""
    return _MODE_TAG.get(council_mode, council_mode)


def _stamp(run_start: datetime) -> str:
    """Run-start as ``YYYY-MM-DD_HHMM`` in Europe/Berlin. A naive datetime is treated as
    UTC (that's how run-start is captured)."""
    if run_start.tzinfo is None:
        run_start = run_start.replace(tzinfo=timezone.utc)
    return run_start.astimezone(_BERLIN).strftime("%Y-%m-%d_%H%M")


def universe_download_name(strategy_id: str, council_mode: str,
                           run_start: datetime, *, ext: str = "md") -> str:
    """The universe run's download name. A COHORT file: it carries the strategy id, the
    mode and the stamp — never a ticker (no single name owns it). ``ext`` selects the
    export (``md`` = the canonical markdown record, ``html`` = the presentation export);
    the default keeps the existing name byte-identical."""
    return f"universe_{strategy_id}_{mode_tag(council_mode)}_{_stamp(run_start)}.{ext}"


def company_check_download_name(ticker: str, strategy_id: str,
                                run_start: datetime, *, ext: str = "txt") -> str:
    """The Company Check download name. A SINGLE-NAME file, so the ticker is REQUIRED and
    always present. ``ext`` selects the export (``txt`` = the canonical text record,
    ``html`` = the presentation export); the default keeps the existing name
    byte-identical."""
    return f"company_check_{ticker}_{strategy_id}_{_stamp(run_start)}.{ext}"


def universe_html_download_name(strategy_id: str, council_mode: str,
                                run_start: datetime) -> str:
    """The universe run's SELF-CONTAINED HTML export name (REPORT-HTML-1) — the same
    scheme and stamp as the markdown, ``.html`` extension, no ticker."""
    return universe_download_name(strategy_id, council_mode, run_start, ext="html")


def company_check_html_download_name(ticker: str, strategy_id: str,
                                     run_start: datetime) -> str:
    """The Company Check SELF-CONTAINED HTML export name (REPORT-HTML-1) — the same
    scheme and stamp as the text report, ``.html`` extension, ticker required."""
    return company_check_download_name(ticker, strategy_id, run_start, ext="html")
