# Run a command with a hard wall-clock timeout. Kills process tree on exceed (Windows).
param(
    [Parameter(Mandatory = $true)]
    [int]$TimeoutSec,
    [string]$Label = "job",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Command
)

$ErrorActionPreference = 'Stop'
if (-not $Command -or $Command.Count -eq 0) {
    Write-Error "Usage: run_with_timeout.ps1 -TimeoutSec N [-Label name] -- command..."
    exit 2
}

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $RepoRoot

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
    Write-Error "[$Label] TIMEOUT after ${TimeoutSec}s - stopping PID $($p.Id) (tree)"
    & taskkill.exe /PID $p.Id /T /F 2>$null | Out-Null
    exit 124
}

$elapsed = [int]((Get-Date) - $started).TotalSeconds
$p.Refresh()
$code = if ($null -ne $p.ExitCode) { $p.ExitCode } else { 0 }
Write-Host "[$Label] done in ${elapsed}s (exit $code)"
exit $code
