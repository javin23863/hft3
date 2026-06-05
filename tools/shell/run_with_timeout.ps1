# Run a command with a hard wall-clock timeout. Kills process tree on exceed (Windows).
param(
    [Parameter(Mandatory = $true)]
    [int]$TimeoutSec,
    [string]$Label = "job",
    [Parameter(Position = 0)]
    [string]$Executable,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = 'Stop'
$Command = @()
if ($Executable) {
    $Command += $Executable
}
if ($Arguments) {
    $Command += $Arguments
}
if ($Command -and $Command.Count -gt 0 -and $Command[0] -eq '--') {
    $Command = @($Command | Select-Object -Skip 1)
}
if (-not $Command -or $Command.Count -eq 0) {
    Write-Error "Usage: & run_with_timeout.ps1 -TimeoutSec N [-Label name] [--] command...; with powershell -File, omit --."
    exit 2
}

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $RepoRoot

function Get-ProcessTreeIds {
    param([int]$RootProcessId)

    $ids = @($RootProcessId)
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$RootProcessId" -ErrorAction SilentlyContinue)
    foreach ($child in $children) {
        $ids += Get-ProcessTreeIds -RootProcessId $child.ProcessId
    }
    return $ids
}

function Wait-ProcessesGone {
    param(
        [int[]]$ProcessIds,
        [int]$TimeoutMs = 5000
    )

    $deadline = (Get-Date).AddMilliseconds($TimeoutMs)
    do {
        $alive = @($ProcessIds | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
        if ($alive.Count -eq 0) {
            return $true
        }
        Start-Sleep -Milliseconds 100
    } while ((Get-Date) -lt $deadline)

    return $false
}

function Stop-ProcessTree {
    param([int]$RootProcessId)

    $treeIds = @(Get-ProcessTreeIds -RootProcessId $RootProcessId | Select-Object -Unique)
    foreach ($processId in @($treeIds | Sort-Object -Descending)) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }

    if (Wait-ProcessesGone -ProcessIds $treeIds -TimeoutMs 5000) {
        return $true
    }

    foreach ($processId in $treeIds) {
        if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
            & taskkill.exe /PID $processId /T /F | Out-Null
        }
    }

    return (Wait-ProcessesGone -ProcessIds $treeIds -TimeoutMs 5000)
}

$exe = $Command[0]
$argList = @()
if ($Command.Count -gt 1) {
    $argList = $Command[1..($Command.Count - 1)]
}

Write-Host "[$Label] start (budget ${TimeoutSec}s): $exe $($argList -join ' ')"
$started = Get-Date

$p = Start-Process -FilePath $exe -ArgumentList $argList -NoNewWindow -PassThru
$timeoutMs = $TimeoutSec * 1000
$finished = $p.WaitForExit($timeoutMs)

if (-not $finished) {
    [Console]::Error.WriteLine("[$Label] TIMEOUT after ${TimeoutSec}s - stopping PID $($p.Id) (tree)")
    $stopped = Stop-ProcessTree -RootProcessId $p.Id
    if (-not $stopped) {
        [Console]::Error.WriteLine("[$Label] cleanup failed: PID $($p.Id) tree still has live processes")
    }
    exit 124
}

$elapsed = [int]((Get-Date) - $started).TotalSeconds
$p.Refresh()
$code = if ($null -ne $p.ExitCode) { $p.ExitCode } else { 0 }
if ($code -ne 0) {
    [void](Stop-ProcessTree -RootProcessId $p.Id)
}
Write-Host "[$Label] done in ${elapsed}s (exit $code)"
exit $code
