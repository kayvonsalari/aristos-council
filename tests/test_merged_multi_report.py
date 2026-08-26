"""REPORT-2 — ONE merged report per run, however many lenses ran.

Ticking three extra lenses used to write FOUR standalone documents (one .md + one .html
per strategy), each repeating the same cohort, the same prices and the same valuation
band. Comparing lenses meant opening four files side by side, and the combined grid —
which is the thing worth reading — existed only on screen and was never written to disk.

This is a PRESENTATION and FILE-OUTPUT change, so two guards carry the weight:

1. VALUE PARITY — every verdict, rank and exclusion reason in the merged report is the
   one the per-strategy report carried for the same run.
2. RECORD PARITY — only the human-facing REPORT files merge. Each strategy's run stays
   individually frozen under ``runs/`` (so it replays), keeps its own membership manifest
   and member hash, and stays individually gradable on forward returns. Collapsing the
   record layer would be a doctrine breach, not a formatting choice.

Everything else pins the structure the spec asked for: one header, the rules PER lens,
one verdict table, the per-name facts stated ONCE, and per-lens detail only for what
genuinely differs.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from aristos_council.download_names import multi_universe_download_name
from aristos_council.export.report_html import (
    DISCLAIMER,
    DOCTRINE,
    multi_strategy_report_html,
    universe_report_html,
)
from aristos_council.pipeline import (
    VERDICT_TABLE_TITLE,
    exclusion_rows,
    multi_strategy_grid_rows,
    multi_summary_line,
    rules_applied,
    run_multi_strategy_pipeline,
    valuation_band_table,
)

# The established multi-lens fixture, reused rather than rebuilt: A/B are healthy, C
# fails the screened lens's ROIC floor (ranked by the unscreened ones), DEAD is a
# delisted shell — exactly the cross-lens disagreement a merged report has to render.
from tests.test_multi_strategy_run import (  # noqa: E402
    RAW,
    SCREENED,
    STRAT_DIR,
    TODAY,
    UNIVERSE,
    _Adapter,
)

MOMENTUM = "magic_formula_momentum_v1"
_RUN = datetime(2026, 8, 24, 11, 49, tzinfo=timezone.utc)


def _multi(ids, **kw):
    return run_multi_strategy_pipeline(UNIVERSE, ids, strategies_dir=STRAT_DIR,
                                       adapter=_Adapter(), today=TODAY, **kw)


@pytest.fixture(scope="module")
def three():
    return _multi([SCREENED, RAW, MOMENTUM])


def _markdown(multi_result, run_start=_RUN) -> str:
    pytest.importorskip("streamlit")
    import app
    return app._multi_strategy_markdown(multi_result, run_start)


# --------------------------------------------------------------------------- #
# 1. FILE OUTPUT — one run, one pair of files
# --------------------------------------------------------------------------- #
def test_a_three_lens_run_writes_exactly_two_files(three, tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    import app
    monkeypatch.setattr(app, "UNIVERSE_RUNS_DIR", tmp_path)

    md_path, html_path = app._persist_multi_strategy_run(three, _RUN, "Defensive Income 16")

    written = sorted(p.name for p in tmp_path.iterdir())
    assert len(written) == 2, written                     # NOT six
    assert md_path.suffix == ".md" and html_path.suffix == ".html"
    assert sorted(written) == sorted([md_path.name, html_path.name])
    assert md_path.read_bytes() and html_path.read_bytes()


def test_a_single_strategy_run_still_writes_its_own_two_files(tmp_path, monkeypatch):
    """UNCHANGED path: one strategy keeps one report, named for that strategy."""
    pytest.importorskip("streamlit")
    import app
    from aristos_council.pipeline import run_rank_pipeline

    monkeypatch.setattr(app, "UNIVERSE_RUNS_DIR", tmp_path)
    res = run_rank_pipeline(UNIVERSE, SCREENED, ranker_only=True,
                            strategies_dir=STRAT_DIR, adapter=_Adapter(), today=TODAY)
    md_path, html_path = app._persist_universe_run(res, _RUN, "Defensive Income 16")

    assert sorted(p.name for p in tmp_path.iterdir()) == \
        sorted([md_path.name, html_path.name])
    assert SCREENED in md_path.name and SCREENED in html_path.name
    assert "lenses" not in md_path.name                   # the single-run scheme, intact


def test_the_merged_filename_carries_the_cohort_the_lens_count_and_the_stamp():
    name = multi_universe_download_name(
        4, "ranker-only", _RUN, ext="html",
        universe_display_name="Defensive Income 16")
    assert name == "universe_defensive-income-16_4lenses_ranker_2026-08-24_1349.html"
    # an ad-hoc cohort with no named manifest OMITS the slug rather than inventing one
    assert multi_universe_download_name(2, "ranker-only", _RUN) == \
        "universe_2lenses_ranker_2026-08-24_1349.md"


def test_the_single_strategy_report_is_unchanged_in_shape(three):
    """The merged report is an ADDITION; a one-strategy run's document is untouched."""
    res = three.results[SCREENED]
    doc = universe_report_html(res, run_start=_RUN)
    for heading in ("Rules applied", "Ranked — the verdict of record",
                    "Where the numbers came from"):
        assert heading in doc
    assert VERDICT_TABLE_TITLE not in doc                 # no cross-lens table here
    assert "lenses" not in doc.split("</style>")[1][:400]


# --------------------------------------------------------------------------- #
# 2. VALUE PARITY — the core guard
# --------------------------------------------------------------------------- #
def test_every_verdict_rank_and_exclusion_matches_the_per_strategy_reports(three):
    """The merged report must contain exactly the values the three separate reports did."""
    rows, head = multi_strategy_grid_rows(three)
    by_name = {row["Name"]: row for row in rows}
    columns = dict(zip(three.strategy_ids, head[1:1 + len(three.strategy_ids)]))

    for sid in three.strategy_ids:
        res = three.results[sid]
        # every RANKED name: its position and verdict, exactly as its own run recorded
        from aristos_council.rank_engine import cohort_positions
        positions = cohort_positions(res.ranked)
        for r in res.ranked:
            cell = by_name[res.names.get(r.ticker, r.ticker) if False
                           else _display(three, r.ticker)][columns[sid]]
            pos, _tied = positions[r.ticker]
            assert cell == f"#{pos} of {len(res.ranked)} · {r.verdict.upper()}"
        # every EXCLUDED name: the same plain sentence its own report carries
        for row in exclusion_rows(res):
            cell = by_name[row["name"]][columns[sid]]
            assert cell == f"excluded — {row['sentence']}"
        # every no-data name reads on its own axis
        for ticker, _why in res.unrateable:
            assert by_name[_display(three, ticker)][columns[sid]] == "no data"


def _display(multi_result, ticker: str) -> str:
    return next(row.display for row in multi_result.rows if row.ticker == ticker)


def _visible(doc: str) -> str:
    """The document's visible text, whitespace removed — the HTML splits a ranked cell
    into two spans so it can colour the verdict, so a raw-markup match would compare the
    TAGS rather than the data."""
    import html as _html
    import re
    body = doc.split("</style>", 1)[-1]
    return re.sub(r"\s+", "", _html.unescape(re.sub(r"<[^>]+>", "\n", body)))


def _squash(text: str) -> str:
    import re
    return re.sub(r"\s+", "", text.replace("*", "").replace("`", ""))


def test_the_merged_surfaces_carry_the_same_cells(three):
    """markdown and HTML render the SAME cells — one builder, no drift."""
    md = _squash(_markdown(three))
    doc = _visible(multi_strategy_report_html(three, run_start=_RUN))
    rows, head = multi_strategy_grid_rows(three)
    for row in rows:
        for column in head:
            cell = _squash(row[column])
            if cell in ("—", ""):
                continue
            assert cell in md, (column, cell)
            assert cell in doc, (column, cell)


# --------------------------------------------------------------------------- #
# 3. RECORD PARITY — the doctrine guardrail
# --------------------------------------------------------------------------- #
def test_each_strategys_run_stays_individually_frozen_and_replayable(tmp_path):
    """Merging the REPORTS must not collapse the RECORD layer: every lens is still
    frozen on its own under runs/, so each remains individually replayable."""
    runs = tmp_path / "runs"
    multi = _multi([SCREENED, RAW], freeze_dir=runs)

    run_ids = [multi.results[sid].meta["run_id"] for sid in multi.strategy_ids]
    assert all(run_ids), run_ids                          # one frozen record per lens
    assert len(set(run_ids)) == len(run_ids)              # distinct records
    assert sorted(p.name for p in runs.iterdir()) == sorted(run_ids)

    # ...and each still replays on its own, offline, to the same verdicts.
    from aristos_council.pipeline import run_rank_pipeline
    for sid, run_id in zip(multi.strategy_ids, run_ids):
        replayed = run_rank_pipeline(
            UNIVERSE, sid, ranker_only=True, strategies_dir=STRAT_DIR,
            today=TODAY, freeze_dir=runs, replay_run_id=run_id)
        assert [(r.ticker, r.verdict) for r in replayed.ranked] == \
            [(r.ticker, r.verdict) for r in multi.results[sid].ranked]


def test_each_strategy_keeps_its_own_membership_manifest(three):
    for sid in three.strategy_ids:
        m = three.results[sid].meta
        assert m["universe_members"] == list(UNIVERSE)
        assert m["universe_member_hash"]
        assert m["rank_strategy_id"] == sid               # gradable on its own


# --------------------------------------------------------------------------- #
# 4. THE MERGED REPORT'S STRUCTURE
# --------------------------------------------------------------------------- #
def test_the_header_and_footer_appear_exactly_once(three):
    doc = multi_strategy_report_html(three, run_start=_RUN)
    assert doc.count("<header") == 1 and doc.count("<footer") == 1
    assert doc.count(DOCTRINE) == 1 and doc.count(DISCLAIMER) == 1
    md = _markdown(three)
    assert md.count("# Universe run — ") == 1
    assert md.count(DOCTRINE) == 1 and md.count(DISCLAIMER) == 1


def test_the_header_names_the_cohort_and_every_lens(three):
    md = _markdown(three)
    doc = multi_strategy_report_html(three, run_start=_RUN)
    for surface in (md, doc):
        assert "3 lenses" in surface
        for sid in three.strategy_ids:                    # ids present, muted
            assert sid in surface
        for label in three.strategy_names.values():       # human names lead
            assert label in surface


def test_the_rules_applied_block_has_one_sub_block_per_lens(three):
    md = _markdown(three)
    doc = multi_strategy_report_html(three, run_start=_RUN)
    for sid in three.strategy_ids:
        block = rules_applied(three.results[sid])
        if block is None or not block.rules:
            continue
        for rule in block.rules:
            assert rule.label in md and rule.threshold_phrase in md
            assert rule.label in doc and rule.threshold_phrase in doc
    # each lens is HEADED, so a reader can tell whose rules these are
    for label in three.strategy_names.values():
        assert f"### {label}" in md or f"### {label} (" in md


def test_the_shared_price_and_valuation_section_appears_exactly_once():
    banded = _multi([SCREENED, RAW], with_valuation_band=True)
    table = valuation_band_table(banded.results[SCREENED])
    assert table is not None
    md = _markdown(banded)
    doc = multi_strategy_report_html(banded, run_start=_RUN)
    assert md.count(f"## {table.title}") == 1
    assert doc.count(f"<h2>{table.title}</h2>") == 1
    # ...and each NAME appears in it once, not once per lens (a price can legitimately
    # equal that name's 12-month high, so the row is the unit, not a bare number).
    # ...and inside THAT section each name has exactly one row — the per-name facts are
    # stated once for the run, not once per lens.
    section = md.split(f"## {table.title}", 1)[1].split(chr(10) + "## ", 1)[0]
    for row in table.rows:
        assert section.count(f"| **{row['Name']}** |") == 1


def test_the_verdict_table_appears_once_with_one_column_per_lens(three):
    rows, head = multi_strategy_grid_rows(three)
    assert head[0] == "Name"
    # GRID-COLS-1: Name plus ONE column per lens, and nothing else. The Rank-sum and
    # Graded-by columns are gone \u2014 incomparable on most rows of a real cohort, and
    # graded-by was already legible from the cells themselves.
    assert len(head) == 1 + len(three.strategy_ids)
    assert "Rank-sum" not in head and "Graded by" not in head
    md = _markdown(three)
    assert md.count(f"## {VERDICT_TABLE_TITLE}") == 1
    assert "\u2021" not in md          # the footnote marker has nothing left to mark


def test_a_name_ranked_by_some_lenses_and_excluded_by_others_renders_in_every_cell(three):
    """C fails the screened lens's ROIC floor and is ranked by the two unscreened ones."""
    rows, head = multi_strategy_grid_rows(three)
    columns = dict(zip(three.strategy_ids, head[1:1 + len(three.strategy_ids)]))
    c = next(row for row in rows if row["Name"].endswith("C") or row["Name"] == "C")

    # the two SCREENED lenses exclude it, naming the rule in REPORT-1 plain English...
    for sid in (SCREENED, MOMENTUM):
        assert c[columns[sid]].startswith("excluded — ")
        assert "return on invested capital" in c[columns[sid]]
        assert "the rule requires at least" in c[columns[sid]]
    # ...while the canonical no-screen lens RANKS it — the disagreement the grid exists
    # to show, readable in one row instead of across three files.
    assert c[columns[RAW]].startswith("#") and "·" in c[columns[RAW]]
    # GRID-COLS-1: "graded by fewer lenses" is now read from the ROW \u2014 two excluded
    # cells and one ranked one \u2014 rather than from a count column.
    assert sum(1 for sid in three.strategy_ids
               if c[columns[sid]].startswith("#")) == 1


def test_an_unrateable_name_reads_no_data_and_keeps_its_reason_per_lens(three):
    rows, head = multi_strategy_grid_rows(three)
    columns = dict(zip(three.strategy_ids, head[1:1 + len(three.strategy_ids)]))
    dead = next(row for row in rows if row["Name"] == "DEAD")

    assert all(dead[columns[sid]] == "no data" for sid in three.strategy_ids)
    assert set(dead) == {"Name", *columns.values()}      # no count columns left
    # the REASON is not lost — it is in each lens's own detail section
    md = _markdown(three)
    doc = multi_strategy_report_html(three, run_start=_RUN)
    for sid in three.strategy_ids:
        reason = dict(three.results[sid].unrateable)["DEAD"]
        assert reason in md
        assert reason in doc


def test_the_row_order_is_the_combined_grids_own(three):
    rows, _head = multi_strategy_grid_rows(three)
    assert [row["Name"] for row in rows] == [row.display for row in three.rows]


def test_the_rank_sum_still_computed_even_though_it_is_no_longer_rendered(three):
    """GRID-COLS-1 removed the COLUMN, not the computation. rank_sum and graded stay on
    the row, stay in the record layer, and still decide the order \u2014 so the ordering
    this report depends on cannot quietly stop working because a column was dropped."""
    for row in three.rows:
        assert hasattr(row, "rank_sum") and hasattr(row, "graded")
    ranked = [r for r in three.rows if r.rank_sum is not None]
    assert ranked, "the fixture should rank something"
    assert any(not r.comparable for r in ranked), (
        "the fixture should still contain a partially-graded name")

    # ...and the rendered order is the order those numbers produced
    rows, _head = multi_strategy_grid_rows(three)
    assert [row["Name"] for row in rows] == [row.display for row in three.rows]


# --------------------------------------------------------------------------- #
# 5. THE SUMMARY LINE
# --------------------------------------------------------------------------- #
def test_the_summary_line_counts_lenses_names_and_the_cross_lens_agreements(three):
    line = multi_summary_line(three)
    assert line.startswith(f"{len(three.strategy_ids)} lenses × "
                           f"{three.meta['universe_size']} names — ")
    ranked_any = sum(1 for row in three.rows if row.graded)
    assert f"{ranked_any} of {three.meta['universe_size']} ranked by at least one" in line


def test_a_zero_clause_is_omitted_rather_than_printed(three):
    """No name in this fixture is excluded by EVERY lens, so that clause must not appear
    as "0 excluded by every lens"."""
    excluded_all = sum(1 for row in three.rows
                       if all(row.cells[s].status == "excluded"
                              for s in three.strategy_ids))
    line = multi_summary_line(three)
    if excluded_all == 0:
        assert "excluded by every lens" not in line
    assert " 0 " not in line


def test_one_lens_reads_as_a_lens_not_lenses():
    assert multi_summary_line(_multi([SCREENED])).startswith("1 lens × ")


# --------------------------------------------------------------------------- #
# 6. Narration is untouched
# --------------------------------------------------------------------------- #
def test_a_multi_lens_run_stays_deterministic_and_unnarrated(three):
    for sid in three.strategy_ids:
        res = three.results[sid]
        assert res.meta["ranker_only"] is True
        assert res.narratives == {}
    assert three.meta["council_mode"] == "ranker-only"
    assert "Narration" not in multi_strategy_report_html(three, run_start=_RUN)
