"""COHORT-1 CLI — ``python -m aristos_council.cohorts <build|check|diff>``.

The brief writes this as ``python -m aristos.cohorts``; this repo's package is
``aristos_council`` and there is no ``aristos``, so the command is spelled to match the
package rather than a second import path being invented to match the command.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .builder import (DEFAULT_DEFINITIONS, DEFAULT_ROOT, DEFAULT_STRATEGY, build, check,
                      diff)
from .definitions import DefinitionError, find_definition, load_definitions
from .source import EODHDSource, SourceError

DEFAULT_UNIVERSES = Path("universes")


def _say(message: str) -> None:
    """Print, on a console that may not be able to spell what we want to say.

    A Windows terminal defaults to cp1252 and dies on the flag glyph. Losing a character
    is acceptable; losing the run because of a character is not.
    """
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", "") or "ascii"
        print(message.encode(encoding, "replace").decode(encoding, "replace"), flush=True)


def _load(args) -> list:
    return load_definitions(args.definitions)


def _selected(args, defs: list) -> list:
    if getattr(args, "all", False):
        return defs
    return [find_definition(defs, args.name)]


def _source_and_probe(args):
    """Build the client and PROBE it once, at startup, logging what it honours."""
    source = EODHDSource()
    probe = source.probe()
    _say(probe.sentence())
    return source, probe


def cmd_build(args) -> int:
    defs = _load(args)
    selected = _selected(args, defs)
    source, probe = _source_and_probe(args)
    failures = 0
    for defn in selected:
        outcome = build(defn, source=source, probe=probe, root=args.root,
                        universes_dir=(None if args.no_register else args.universes_dir),
                        rebuild=args.rebuild, strategy_id=args.strategy, progress=_say)
        _say(outcome.summary())
        if outcome.universe_path:
            _say(f"  registered as a local stock list: {outcome.universe_path}")
        elif outcome.registration_error:
            _say(f"  NOT registered as a UI list: {outcome.registration_error}")
        if outcome.quality:
            for flag in outcome.quality.flags:
                _say(f"  ⚠ {flag.name}: {flag.figure}")
        if not outcome.frozen and not outcome.skipped:
            failures += 1
    return 1 if failures else 0


def cmd_check(args) -> int:
    defs = _load(args)
    for defn in _selected(args, defs):
        _report, text = check(defn, root=args.root, strategy_id=args.strategy,
                              progress=_say)
        _say(text)
    return 0


def cmd_diff(args) -> int:
    defs = _load(args)
    source, probe = _source_and_probe(args)
    for defn in _selected(args, defs):
        text, _added, _removed = diff(defn, source=source, probe=probe, root=args.root,
                                      progress=_say)
        _say(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m aristos_council.cohorts",
        description="Build, check and diff rule-defined cohorts. No LLM is ever called.")
    parser.add_argument("--definitions", default=str(DEFAULT_DEFINITIONS),
                        help=f"definition file (default {DEFAULT_DEFINITIONS})")
    parser.add_argument("--root", default=str(DEFAULT_ROOT),
                        help=f"where frozen cohorts live (default {DEFAULT_ROOT})")
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY,
                        help=f"rank strategy for the quality report (default {DEFAULT_STRATEGY})")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_selector(sp, *, allow_all: bool):
        group = sp.add_mutually_exclusive_group(required=True)
        group.add_argument("--name", help="cohort name or slug")
        if allow_all:
            group.add_argument("--all", action="store_true", help="every definition")

    p_build = sub.add_parser("build", help="build cohorts that are not built yet")
    add_selector(p_build, allow_all=True)
    p_build.add_argument("--rebuild", action="store_true",
                         help="cut the NEXT version of an already-built cohort")
    p_build.add_argument("--universes-dir", default=str(DEFAULT_UNIVERSES),
                         help="where to register the cohort as a local stock list")
    p_build.add_argument("--no-register", action="store_true",
                         help="build and freeze, but do not register a UI list")
    p_build.set_defaults(func=cmd_build)

    p_check = sub.add_parser("check", help="quality report only, no rebuild")
    add_selector(p_check, allow_all=True)
    p_check.set_defaults(func=cmd_check)

    p_diff = sub.add_parser("diff", help="what a rebuild would add or remove")
    add_selector(p_diff, allow_all=True)
    p_diff.set_defaults(func=cmd_diff)
    return parser


def _load_env() -> None:
    """Pick up EODHD_API_KEY from a local .env, the way app.py does at start.

    Guarded: python-dotenv is a ``ui`` extra, and the key may already be exported. Under
    pytest this is a no-op by design — TEST-ISOLATION-1 silences load_dotenv for the whole
    suite, so a test can never build a cohort against the owner's real key.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:                                          # UTF-8 where the console allows it
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    _load_env()
    try:
        return args.func(args)
    except (DefinitionError, SourceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":                        # pragma: no cover
    raise SystemExit(main())
