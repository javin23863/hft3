# Bounded agent verification: T0 + registry + workbench (excludes slow catalog-event e2e).
# Not a substitute for T2 replay certification — see docs/vault/BACKTESTER_CERTIFICATION.md
# Policy: docs/ai/SHELL_EXECUTION.md
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$env:PYTHONPATH = "packages"

$Wrapper = Join-Path $RepoRoot 'tools/shell/run_with_timeout.ps1'
$BudgetSec = 180

function Invoke-TimedPython {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [int]$TimeoutSec = $BudgetSec
    )

    $Command = @('python') + $Arguments
    & $Wrapper -TimeoutSec $TimeoutSec -Label $Label -Command $Command
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Invoke-TimedPython -TimeoutSec $BudgetSec -Label 'agent-verify-preamble' -Arguments @(
    '-m', 'economic_event_universe.cli', 'validate'
)

Invoke-TimedPython -TimeoutSec $BudgetSec -Label 'agent-verify-core' -Arguments @(
    '-m', 'pytest',
    'tests/backtester_validation/fast',
    'tests/test_model_registry_slugs.py',
    'tests/test_economic_event_universe/',
    '-q', '--tb=no'
)

$WorkbenchDir = Join-Path $RepoRoot 'tests/test_workbench'
$WorkbenchTests = Get-ChildItem -LiteralPath $WorkbenchDir -Filter 'test_*.py' |
    Where-Object { $_.Name -ne 'test_catalog_event_e2e.py' } |
    Sort-Object Name

foreach ($TestFile in $WorkbenchTests) {
    $RelativePath = $TestFile.FullName.Substring($RepoRoot.Length).TrimStart([char[]]@('\', '/'))
    Invoke-TimedPython -TimeoutSec $BudgetSec -Label "agent-verify-workbench:$($TestFile.BaseName)" -Arguments @(
        '-m', 'pytest',
        $RelativePath,
        '-q', '--tb=no'
    )
}

$HandoffFile = $env:HANDOFF_STATUS_FILE
if ($HandoffFile -and (Test-Path -LiteralPath $HandoffFile)) {
  Invoke-TimedPython -TimeoutSec 30 -Label 'handoff-status' -Arguments @(
      'scripts/check_handoff_status.py', $HandoffFile, '--require'
  )
}
exit 0
