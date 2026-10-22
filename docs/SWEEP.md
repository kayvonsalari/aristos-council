# SWEEP-1: the awkward-company round, in one command

```
python -m aristos_council.sweep run [--set awkward] [--judges | --no-judges] [--council]
                                    [--workers N] [--out DIR]
```

It runs the real pipeline with the real adapters, exactly what the Analyse tab runs, over a named set of
companies and lists, writes every report the app would let you download, and then runs the REPORT-SWEEP-1
checks over the Markdown. Nobody has to watch it.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--set` | `awkward` | A set in `data/sweep_sets.yaml` (tracked). |
| `--judges` / `--no-judges` | on | The blind judge panel on every company page. Real model calls on three providers. |
| `--council` | off | The council opinion on every company page. An entry with `council: true` runs it regardless. |
| `--workers` | 3 | Company entries that run at once. List entries always run afterwards, one at a time. |
| `--out` | `data/local/sweep/<YYYY-MM-DD_HHMM>/` | Output folder (git-ignored). |

The exit code is non-zero only if **every** entry failed. Findings never fail a sweep.

## The awkward set

Company pages, with **every visible stock lens ticked** (the voting lenses and Forensic): KO.US (with the
council opinion), F.US, VKTX.US, NVCR.US, 1211.HK, JPM.US, MSFT.US. Then lists: the Diversified Mining built
cohort, a pasted NVDA, MSFT, AAPL, a pasted six-name oil list (XOM, CVX, SHEL.L, TTE.PA, EQNR.OL, CNQ.TO),
and a one-name list (KO). The set file says how to add an entry, or a set. A built cohort must already be
built: the sweep never starts a market-index or cohort build, and it uses the day-cache and the existing
retry/rate-limit wrappers.

Two things differ from a click in the browser, so a sweep leaves your records alone: the judge panel's log
rows go to `<entry>/judge_log/` instead of `data/local/judges/`, and nothing asks you to confirm spend.

## What it writes

```
<out>/manifest.json      git commit, start/end, flags, per-entry status, model calls, approximate cost
<out>/summary.md         one table per entry, then every finding
<out>/<entry>/report.md  report.txt  report.html     the three downloads
<out>/<entry>/judge.json   (company pages, with --judges)   council.txt   (with the council opinion)
<out>/<entry>/error.txt    (only when the entry failed: the traceback)
```

The manifest is rewritten after every entry, so a sweep that is interrupted still leaves one
(`"finished": false`). A list's `report.txt` is the combined grid text; the app offers Markdown and HTML for
lists.

## Cost

| Flag | Model calls per company page | About |
| --- | --- | --- |
| neither | 0 | free (data only) |
| `--judges` | 3 judges x 2 calls, plus a reading call when the panel disagrees | $0.10-0.20 |
| council | about 6, mostly the cheap tier | measured and recorded |

The judge cost in the manifest is calls x a fixed approximate price (the panel does not meter its calls); the
council cost is the meter's. A full default run (seven company pages, judges on, council for KO) is on the
order of $1.

## Reading summary.md

One table per entry: verdict of record (the agreement line; for a list, how many names have a BUY), the lens
votes (BUY / HOLD / SELL among the lenses that voted), the judge split, whether a council text is present, the
finding count. Then **Findings**: a count per rule, and for each entry its findings as
entry | section | file:line | rule | detail, or "no findings" stated explicitly. A failed entry says so and
points at its `error.txt`.

## The checks

Over every Markdown report: internal ids or strategy slugs in reader text; `$-` money; inconsistent unit scale
within one series (a table row whose columns are years or quarters; a column of different companies may mix
$m and $bn); singular/plural slips ("1 names", "3 company", "(s)"); unmatched `**` or `_`; diagnostic column
names in reader text; truncated company names ("The Coca-Cola", "Johnson &", a trailing "..."); a repeated
paragraph (the same paragraph under two headings, or the same sentence in two lens notes); a ticked lens
absent from every table. Over the structured results: the same lens or check stated two ways, SELL votes
missing from the agreement, BUY with no SELL at list sizes of 2 or more (under 5 names a lens has no SELL
rule at all). Over the judge panel: a field that starts with a verdict token ("HOLD:"), and any error,
invalid or incomplete judge row with its reason. The rules that already existed live in `report_sweep.py`
and are called, not copied; the sweep adds location (entry, section, line).

A finding is a report, not a fix: the sweep changes no report.
