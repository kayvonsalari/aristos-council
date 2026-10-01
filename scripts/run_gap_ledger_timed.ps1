# PAPER-TRADE-1 item 7 — wraps the EXISTING "Gap Ledger run" scheduled task's command line
# so its real start/end wall-clock time is known, without touching anything under
# src/aristos_council/gap_ledger/ (that package is frozen).
#
# Does exactly what the task's command line already did — run
# `python -m aristos_council.gap_ledger run`, appending its own report to
# data\local\gap_ledger_run.log — plus writes one START line and one END line (ISO-8601,
# elapsed minutes, exit code) to data\local\gap_ledger_run_timing.log around it, and exits
# with the SAME code Python exited with, so Task Scheduler's own "last result" still
# reflects the real outcome.
#
# Installed as the task's action by scripts/register_paper_trade_tasks.ps1 (run once by
# hand); nothing here runs on its own.

$ErrorActionPreference = "Stop"
Set-Location "C:\Users\kayvo\Documents\GitHub\aristos-council"

$pythonExe = "C:\Users\kayvo\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$timingLog = "data\local\gap_ledger_run_timing.log"
$runLog = "data\local\gap_ledger_run.log"

New-Item -ItemType Directory -Force -Path "data\local" | Out-Null

$start = Get-Date
Add-Content -Path $timingLog -Value ("START {0}" -f $start.ToString("o")) -Encoding utf8

& $pythonExe -m aristos_council.gap_ledger run *>> $runLog
$code = $LASTEXITCODE

$end = Get-Date
$minutes = [math]::Round(($end - $start).TotalMinutes, 1)
Add-Content -Path $timingLog -Value ("END   {0} (exit {1}, {2} min)" -f $end.ToString("o"), $code, $minutes) -Encoding utf8

exit $code
