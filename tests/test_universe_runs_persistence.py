"""Tests for universe-run auto-persistence (UI-FIX-1) — persistence/universe_runs.py.

Two paid narrated runs existed only in Streamlit session state and were destroyed by a
restart before download. ``save_universe_run`` is the pure disk-write half of the fix:
given the SAME bytes the UI's download buttons already serve, write them to a directory
(creating it if needed) and hand back the paths — no rendering, no session state, so it
can never drift from what a download button produces.
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.persistence.universe_runs import save_universe_run


def test_save_universe_run_writes_both_files_byte_identical(tmp_path):
    out_dir = tmp_path / "universe_runs"
    md_bytes = b"# a markdown run\n"
    html_bytes = b"<html>a run</html>"
    md_path, html_path = save_universe_run(
        md_bytes, html_bytes, md_name="universe_x_v1_narrator_2026-08-05_1200.md",
        html_name="universe_x_v1_narrator_2026-08-05_1200.html", out_dir=out_dir)
    assert md_path == out_dir / "universe_x_v1_narrator_2026-08-05_1200.md"
    assert html_path == out_dir / "universe_x_v1_narrator_2026-08-05_1200.html"
    assert md_path.read_bytes() == md_bytes
    assert html_path.read_bytes() == html_bytes


def test_save_universe_run_creates_the_directory(tmp_path):
    out_dir = tmp_path / "does" / "not" / "exist" / "yet"
    assert not out_dir.exists()
    save_universe_run(b"x", b"y", md_name="a.md", html_name="a.html", out_dir=out_dir)
    assert out_dir.is_dir()


def test_save_universe_run_accepts_a_string_out_dir(tmp_path):
    out_dir = str(tmp_path / "universe_runs")
    md_path, _ = save_universe_run(b"x", b"y", md_name="a.md", html_name="a.html",
                                    out_dir=out_dir)
    assert isinstance(md_path, Path)
    assert md_path.exists()
