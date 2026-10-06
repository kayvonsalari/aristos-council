# PAPER-TRADE-1 item 7 + GAP-STATUS-1 — the "Gap Ledger run" scheduled task's wrapper.
#
# 1. Runs `python -m aristos_council.gap_ledger run` with a WATCHDOG: if it has not finished
#    after $TimeoutMinutes it is killed (with its children) instead of being left to the task's
#    one-hour limit, which kills this script too and leaves no trace. Output is appended to
#    data\local\gap_ledger_run.log as UTF-8 and unbuffered, so a killed run's last line survives.
# 2. Writes one START and one END line (ISO-8601, elapsed minutes, exit code) to
#    data\local\gap_ledger_run_timing.log.
# 3. ALWAYS then posts one short status task to the Todoist project "Gap Ledger" (ran / failed,
#    candidates, IBKR verified or UNAVAILABLE, the reason, and any missed days) — see
#    src/aristos_council/gap_ledger/status.py. A run that hangs or crashes can no longer look like
#    a quiet day.
#
# Nothing here changes how Gap Ledger screens or scores. Exits with the screen process's own code
# (124 when the watchdog fired), so Task Scheduler's "last result" still reflects the outcome.
#
# Why --backfill-max 0 (2026-10-06): GAP-BACKFILL-1's catch-up ran BEFORE the live scan and
# tried to rebuild 2026-09-23 through ~1,300 sequential IBKR history requests, which IB's pacing
# limit (60 per 10 minutes) makes a ~3.5 hour job; the task's one-hour limit killed it every day
# from 09-30, so the live scan never started. Catch-up can still be run by hand:
#     python -m aristos_council.gap_ledger catch-up --dry-run
# Remove "--backfill-max 0" below to restore the automatic catch-up once that is fixed.
#
# Installed as the task's action by scripts/register_paper_trade_tasks.ps1.

param(
    [double]$TimeoutMinutes = 50,
    [string]$RunArgs = "run --backfill-max 0",
    [string]$StatusExtraArgs = ""
)

$ErrorActionPreference = "Stop"
Set-Location "C:\Users\kayvo\Documents\GitHub\aristos-council"

$pythonExe = "C:\Users\kayvo\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$timingLog = "data\local\gap_ledger_run_timing.log"
$runLog = "data\local\gap_ledger_run.log"

New-Item -ItemType Directory -Force -Path "data\local" | Out-Null
if (-not (Test-Path $runLog)) { New-Item -ItemType File -Path $runLog | Out-Null }

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

function Invoke-Python([string]$Arguments, [double]$Minutes) {
    # cmd.exe does the redirect, so the log gets the process's own UTF-8 bytes (PowerShell's
    # *>> would re-encode them as UTF-16 and garble the file).
    $cmdLine = "/c `"`"$pythonExe`" -m aristos_council.gap_ledger $Arguments >> `"$runLog`" 2>&1`""
    $p = Start-Process -FilePath "cmd.exe" -ArgumentList $cmdLine -PassThru -WindowStyle Hidden
    $null = $p.Handle                       # keeps ExitCode readable after exit
    if ($p.WaitForExit([int]($Minutes * 60 * 1000))) {
        return @{ Code = $p.ExitCode; TimedOut = $false }
    }
    & taskkill.exe /PID $p.Id /T /F | Out-Null
    return @{ Code = 124; TimedOut = $true }
}

$start = Get-Date
$offset = (Get-Item $runLog).Length
Add-Content -Path $timingLog -Value ("START {0}" -f $start.ToString("o")) -Encoding utf8

$result = Invoke-Python $RunArgs $TimeoutMinutes
$code = $result.Code

$end = Get-Date
$minutes = [math]::Round(($end - $start).TotalMinutes, 1)
$note = if ($result.TimedOut) { ", TIMED OUT" } else { "" }
Add-Content -Path $timingLog -Value ("END   {0} (exit {1}, {2} min{3})" -f $end.ToString("o"), $code, $minutes, $note) -Encoding utf8

# The status task. Its own failure must never change the screen's exit code.
try {
    $statusArgs = "status --exit-code $code --log `"$runLog`" --log-offset $offset"
    if ($result.TimedOut) { $statusArgs += " --timed-out --timeout-minutes $TimeoutMinutes" }
    if ($StatusExtraArgs) { $statusArgs += " $StatusExtraArgs" }
    $null = Invoke-Python $statusArgs 5
} catch {
    Add-Content -Path $runLog -Value ("status task failed: {0}" -f $_.Exception.Message) -Encoding utf8
}

exit $code
