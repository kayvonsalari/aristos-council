"""Universe editor (UNIED-1) — build/clone/save custom ticker lists.

Pure, network-free tests of the editor core: comment-tolerant parsing, the graded-id
clone-only guardrail, id-collision refusal, the gitignored local dir, and the
clone-edit-save round trip through the same loaders everything else uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aristos_council.universe import (
    adhoc_universe_id,
    list_universes,
    load_universe_by_id,
)
from aristos_council.universe_editor import (
    ensure_local_dir,
    existing_universe_ids,
    graded_universe_ids,
    parse_ticker_lines,
    save_local_universe,
    suggest_clone_id,
)

REPO = Path(__file__).resolve().parents[1]
UNIVERSES_DIR = REPO / "universes"


def _write_manifest(dir_: Path, tickers: list[str], *, uid: str) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"{uid}.yaml").write_text(
        f"id: {uid}\ndescription: test\ncreated: '2026-07-05'\nrationale: test\n"
        "tickers:\n" + "".join(f"  - {t}\n" for t in tickers), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Parsing — one per line, comments allowed
# --------------------------------------------------------------------------- #
def test_parse_ticker_lines_handles_comments_blanks_and_normalizes():
    raw = ("AAPL\n"
           "msft  # anchor position\n"
           "\n"
           "# --- energy sleeve ---\n"
           "  xom ,  cvx  \n"
           "brk.b\n"
           "AAPL\n")                       # duplicate -> dropped
    assert parse_ticker_lines(raw) == ["AAPL", "MSFT", "XOM", "CVX", "BRK.B"]


def test_parse_ticker_lines_empty_and_comment_only():
    assert parse_ticker_lines("") == []
    assert parse_ticker_lines("# just a note\n\n   \n") == []


def test_parse_matches_adhoc_fingerprint_of_plain_list():
    # The ad-hoc RUN path is unchanged: a commented editor list fingerprints identically
    # to the same tickers pasted plainly (comments/order/case are irrelevant to the id).
    raw = "AAPL\nMSFT  # note\n# heading\ngoogl\n"
    parsed = parse_ticker_lines(raw)
    assert adhoc_universe_id(parsed) == adhoc_universe_id(["GOOGL", "aapl", "msft"])


# --------------------------------------------------------------------------- #
# Graded-id clone-only guardrail
# --------------------------------------------------------------------------- #
def test_graded_universe_ids_reads_snapshot_csv(tmp_path):
    csv_path = tmp_path / "verdict_consensus.csv"
    csv_path.write_text(
        "snapshot_date,strategy,universe_id,ticker,aristos_verdict,combined_rank,price,"
        "street_mean,n_analysts,target_mean,notes\n"
        "2026-07-05,magic_formula_v1,graded_uni_v1,AAPL,BUY,1.0,100,1.5,30,120,\n"
        "2026-07-05,magic_formula_v1,,ZZ,UNRATEABLE,,,,,,no data\n",  # blank id ignored
        encoding="utf-8")
    assert graded_universe_ids(csv_path) == {"graded_uni_v1"}


def test_graded_universe_ids_missing_csv_is_empty(tmp_path):
    assert graded_universe_ids(tmp_path / "nope.csv") == set()


def test_save_refuses_a_graded_id_in_place(tmp_path):
    _write_manifest(tmp_path, ["AAPL", "MSFT"], uid="graded_uni_v1")
    with pytest.raises(ValueError, match="graded — clone to modify"):
        save_local_universe(tmp_path, id="graded_uni_v1", tickers=["AAPL", "MSFT", "GOOGL"],
                            created="2026-07-15", graded_ids={"graded_uni_v1"})
    # nothing written to local/
    assert not (tmp_path / "local" / "graded_uni_v1.yaml").exists()


# --------------------------------------------------------------------------- #
# Id-collision refusal
# --------------------------------------------------------------------------- #
def test_save_refuses_id_collision_with_existing(tmp_path):
    _write_manifest(tmp_path, ["AAPL"], uid="mine_v1")
    with pytest.raises(ValueError, match="already exists"):
        save_local_universe(tmp_path, id="mine_v1", tickers=["MSFT"], created="2026-07-15")


def test_save_rejects_unversioned_id(tmp_path):
    with pytest.raises(Exception, match="must encode a version"):
        save_local_universe(tmp_path, id="no_version", tickers=["AAPL"],
                            created="2026-07-15")


def test_save_rejects_empty_ticker_list(tmp_path):
    with pytest.raises(Exception):
        save_local_universe(tmp_path, id="empty_v1", tickers=["", "   "],
                            created="2026-07-15")


# --------------------------------------------------------------------------- #
# Local dir is gitignored (structural, closed at first use)
# --------------------------------------------------------------------------- #
def test_ensure_local_dir_creates_gitignore(tmp_path):
    d = ensure_local_dir(tmp_path)
    assert d == tmp_path / "local"
    gi = (d / ".gitignore").read_text(encoding="utf-8")
    assert "*" in gi and "!.gitignore" in gi


def test_ensure_local_dir_does_not_clobber_existing_gitignore(tmp_path):
    d = tmp_path / "local"
    d.mkdir()
    (d / ".gitignore").write_text("custom\n", encoding="utf-8")
    ensure_local_dir(tmp_path)
    assert (d / ".gitignore").read_text(encoding="utf-8") == "custom\n"


def test_committed_local_dir_is_gitignored():
    # The repo ships universes/local/.gitignore so the guarantee exists from day one.
    gi = UNIVERSES_DIR / "local" / ".gitignore"
    assert gi.exists()
    body = gi.read_text(encoding="utf-8")
    assert "*" in body and "!.gitignore" in body


# --------------------------------------------------------------------------- #
# Clone-edit-save round trip
# --------------------------------------------------------------------------- #
def test_clone_edit_save_round_trip(tmp_path):
    _write_manifest(tmp_path, ["AAPL", "MSFT", "GOOGL"], uid="seed_v1")

    # clone: read the base, edit the list (drop GOOGL, add TSLA + a commented line)
    base = load_universe_by_id("seed_v1", tmp_path)
    edited_text = "\n".join(base.tickers[:2]) + "\nTSLA  # new add\n# note\n"
    new_id = suggest_clone_id(base.id, existing_universe_ids(tmp_path))
    assert new_id == "seed_v2"

    path = save_local_universe(
        tmp_path, id=new_id, tickers=parse_ticker_lines(edited_text),
        created="2026-07-15", display_name="My Clone", rationale="testing the editor")
    assert path == tmp_path / "local" / "seed_v2.yaml"

    # round trip: it loads back through the standard loader, tagged local
    reloaded = load_universe_by_id("seed_v2", tmp_path)
    assert reloaded.tickers == ["AAPL", "MSFT", "TSLA"]
    assert reloaded.display_name == "My Clone"
    assert reloaded.rationale == "testing the editor"
    assert reloaded.created == "2026-07-15"
    assert reloaded.local is True
    # the base is untouched (clone-only never mutates the original)
    assert load_universe_by_id("seed_v1", tmp_path).tickers == ["AAPL", "MSFT", "GOOGL"]


def test_save_is_not_written_to_disk_before_validation(tmp_path):
    # a validation failure must leave local/ with no orphan file
    with pytest.raises(Exception):
        save_local_universe(tmp_path, id="bad_id_no_version", tickers=["AAPL"],
                            created="2026-07-15")
    assert not (tmp_path / "local" / "bad_id_no_version.yaml").exists()


# --------------------------------------------------------------------------- #
# Discovery — a saved local universe is discovered and tagged
# --------------------------------------------------------------------------- #
def test_saved_local_universe_is_discovered_and_flagged(tmp_path):
    _write_manifest(tmp_path, ["AAPL"], uid="graded_top_v1")   # a top-level manifest
    save_local_universe(tmp_path, id="mylist_v1", tickers=["MSFT", "NVDA"],
                        created="2026-07-15", display_name="My List")

    found = {u.id: u for u in list_universes(tmp_path)}
    assert "graded_top_v1" in found and "mylist_v1" in found
    assert found["graded_top_v1"].local is False
    assert found["mylist_v1"].local is True
    # existing_universe_ids sees both (so a second save can't collide with either)
    assert {"graded_top_v1", "mylist_v1"} <= existing_universe_ids(tmp_path)


def test_local_universe_label_is_tagged():
    from aristos_council.demo_surface import universe_label
    from aristos_council.universe import Universe

    u = Universe(id="mylist_v1", display_name="My List", tickers=["AAPL"], local=True)
    assert universe_label(u) == "My List (local)"
    plain = Universe(id="mylist_v1", display_name="My List", tickers=["AAPL"])
    assert universe_label(plain) == "My List"
