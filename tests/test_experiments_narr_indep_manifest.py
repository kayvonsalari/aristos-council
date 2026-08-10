"""NARR-INDEP-TEST harness — manifest.py (append-only JSONL run log)."""

from __future__ import annotations

from experiments.narr_indep_test.manifest import ManifestRow, append_row, read_manifest


def test_read_manifest_empty_when_file_absent(tmp_path):
    assert read_manifest(tmp_path / "nope.jsonl") == []


def test_append_row_then_read_round_trips(tmp_path):
    path = tmp_path / "manifest.jsonl"
    row = ManifestRow(run_id="A_VUSA.AS_buy_rep1", experiment="A",
                      condition_id="A_VUSA.AS_buy", fund_ticker="VUSA.AS", rep=1,
                      note="truthful", models={"decision": {"model": "x", "temperature": 0.0}},
                      finished_at="2026-08-10T00:00:00+00:00",
                      raw_output_path="raw/A_VUSA.AS_buy_rep1.json", ok=True)
    append_row(row, path)
    rows = read_manifest(path)
    assert len(rows) == 1
    assert rows[0]["run_id"] == "A_VUSA.AS_buy_rep1"
    assert rows[0]["ok"] is True
    assert rows[0]["models"]["decision"]["model"] == "x"


def test_append_row_is_append_only_not_overwriting(tmp_path):
    path = tmp_path / "manifest.jsonl"
    for i in range(1, 4):
        append_row(ManifestRow(run_id=f"run{i}", experiment="A", condition_id="c",
                               fund_ticker="X", rep=i, note=""), path)
    rows = read_manifest(path)
    assert [r["run_id"] for r in rows] == ["run1", "run2", "run3"]


def test_a_partial_run_manifest_survives_a_later_resume(tmp_path):
    path = tmp_path / "manifest.jsonl"
    append_row(ManifestRow(run_id="run1", experiment="A", condition_id="c",
                           fund_ticker="X", rep=1, note="", ok=True), path)
    # simulate a fresh process resuming — it must see the prior row, not start blank.
    assert len(read_manifest(path)) == 1
    append_row(ManifestRow(run_id="run2", experiment="A", condition_id="c",
                           fund_ticker="X", rep=2, note="", ok=False,
                           error="RuntimeError: boom"), path)
    rows = read_manifest(path)
    assert len(rows) == 2
    assert rows[1]["ok"] is False and "boom" in rows[1]["error"]
