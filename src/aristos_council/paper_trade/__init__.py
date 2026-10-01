"""PAPER-TRADE-1 — paper-trades Gap Ledger's daily candidates in the owner's IBKR PAPER
account, to measure real-world execution alongside Gap Ledger's own scorecard.

A SEPARATE tool, the same way Gap Ledger itself is a separate tool living in this repo:

- Gap Ledger is FROZEN. This package never imports it and never writes to it — it reads
  ``data/local/gap_ledger/YYYY-MM-DD.csv`` read-only, nothing else.
- It is independent even at the code level: its own NYSE holiday calendar, its own IBKR
  connection, its own New York clock constant — none of it shared with
  ``aristos_council.gap_ledger``, so a change here can never touch Gap Ledger's frozen
  behaviour and a test of one package can never load the other.
- Orders only ever go to the PAPER Gateway (port 4002). Every connection this package makes
  is refused before it can place an order unless the connected account id starts with "DU"
  (IBKR's own paper-account prefix) — see ``ibkr_paper.PaperIBKR.connect``.
- A kill switch: ``data/local/paper_trade/STOP`` — if that file exists, ``enter`` and
  ``exit`` place nothing and say so.

The CLI (``python -m aristos_council.paper_trade enter|exit|record|report``) and each
module's own docstring are the current documentation; a standalone docs/PAPER_TRADE.md
(mirroring docs/GAP_LEDGER.md) is proposed but not written — see the done-report's "DOCS
PROPOSED" note (documentation duty, CLAUDE.md).
"""
