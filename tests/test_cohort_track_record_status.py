"""BACKTEST-RULE-1: `cohorts status` names frozen cohorts that have no backtest rows."""
from aristos_council.cohorts.__main__ import main
from aristos_council.cohorts.track_record import format_missing, missing_track_records

LENSES = ("lens_a", "lens_b")


def _freeze(root, slug):
    d = root / slug / "v1"
    d.mkdir(parents=True)
    (d / "members.csv").write_text("ticker\nAAA\n", encoding="utf-8")


def _tree(tmp_path):
    cohorts, bt = tmp_path / "cohorts", tmp_path / "backtests"
    _freeze(cohorts, "has_rows")
    _freeze(cohorts, "no_rows")
    _freeze(cohorts, "partial_rows")
    (cohorts / "unfrozen" / "v1").mkdir(parents=True)          # no members file: ignored
    for slug, lenses in (("has_rows", LENSES), ("partial_rows", LENSES[:1])):
        (bt / slug).mkdir(parents=True)
        for lens in lenses:
            (bt / slug / f"{lens}.csv").write_text("x\n", encoding="utf-8")
    return cohorts, bt


def test_only_cohorts_missing_rows_are_listed(tmp_path):
    cohorts, bt = _tree(tmp_path)
    assert missing_track_records(cohorts, bt) == [
        ("no_rows", list(LENSES)), ("partial_rows", ["lens_b"])]


def test_cli_prints_the_plain_line_and_clears_when_rows_are_added(tmp_path, capsys):
    cohorts, bt = _tree(tmp_path)
    assert main(["--root", str(cohorts), "status", "--backtests", str(bt)]) == 0
    out = capsys.readouterr().out
    assert "no_rows: no track record yet for all 2 voting lenses" in out
    assert "partial_rows: no track record yet for lens_b" in out
    assert "has_rows" not in out
    (bt / "no_rows").mkdir()
    for lens in LENSES:
        (bt / "no_rows" / f"{lens}.csv").write_text("x\n", encoding="utf-8")
    (bt / "partial_rows" / "lens_b.csv").write_text("x\n", encoding="utf-8")
    main(["--root", str(cohorts), "status", "--backtests", str(bt)])
    assert "every frozen cohort has a backtest row" in capsys.readouterr().out


def test_empty_is_a_clean_message():
    assert "every frozen cohort" in format_missing([])
