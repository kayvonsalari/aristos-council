# PAPER-TRADE-1 item 6/7/8 — registers the three paper-trade Windows tasks and rewires the
# EXISTING "Gap Ledger run" task to go through the timing wrapper (scripts/
# run_gap_ledger_timed.ps1), which does not touch anything under gap_ledger/ itself.
#
# Idempotent: re-running this replaces each task's action/trigger in place rather than
# creating a duplicate. Run it by hand, once, after a change here — it is not itself
# scheduled.
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_paper_trade_tasks.ps1

$ErrorActionPreference = "Stop"

$repo = "C:\Users\kayvo\Documents\GitHub\aristos-council"
$python = "C:\Users\kayvo\AppData\Local\Python\pythoncore-3.14-64\python.exe"
# Monday-Friday, the same DaysOfWeek value the three existing Gap Ledger tasks use.
$weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

function Register-PyTask {
    param($Name, $Time, $Module, $LogFile)
    $action = New-ScheduledTaskAction -Execute "cmd.exe" `
        -Argument "/c cd /d $repo && `"$python`" -m $Module >> data\local\$LogFile 2>&1"
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $Time
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
        -StartWhenAvailable
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger `
        -Settings $settings -Force | Out-Null
}

# -- the three new tasks (PAPER-TRADE-1 item 6) ------------------------------ #
Register-PyTask -Name "Paper Trade enter" -Time "15:20" `
    -Module "aristos_council.paper_trade enter" -LogFile "paper_trade_enter.log"
Register-PyTask -Name "Paper Trade exit" -Time "21:30" `
    -Module "aristos_council.paper_trade exit" -LogFile "paper_trade_exit.log"
Register-PyTask -Name "Paper Trade record" -Time "22:20" `
    -Module "aristos_council.paper_trade record" -LogFile "paper_trade_record.log"

# -- rewire the EXISTING "Gap Ledger run" task (item 7) ----------------------- #
# Same trigger/settings as before (15:00 Berlin, weekdays, 1-hour limit, start-when-
# available for GAP-BACKFILL-1's catch-up) — only the ACTION changes, to go through the
# timing wrapper instead of calling Python directly. gap_ledger/ itself is untouched.
$existing = Get-ScheduledTask -TaskName "Gap Ledger run"
$timedAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$repo\scripts\run_gap_ledger_timed.ps1`""
Set-ScheduledTask -TaskName "Gap Ledger run" -Action $timedAction `
    -Trigger $existing.Triggers -Settings $existing.Settings | Out-Null

# -- show what is actually registered now ------------------------------------- #
Write-Output ""
Write-Output "Registered / updated tasks:"
foreach ($name in @("Paper Trade enter", "Paper Trade exit", "Paper Trade record", "Gap Ledger run")) {
    $info = Get-ScheduledTaskInfo -TaskName $name
    $task = Get-ScheduledTask -TaskName $name
    Write-Output ("  {0,-20} next run {1}  ({2})" -f $name, $info.NextRunTime, $task.State)
}
