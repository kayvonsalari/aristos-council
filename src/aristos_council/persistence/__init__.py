"""Persistence layer — IO at the edge, so the graph stays pure.

The council graph never reads or writes disk; the verdict log is loaded before
`invoke` and appended after it (see pipeline.py and company_report.py). This keeps every
graph node deterministic and replayable.
"""
