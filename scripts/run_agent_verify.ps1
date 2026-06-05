# Bounded agent verification: T0 + registry + workbench (excludes slow catalog-event e2e).
# Not a substitute for T2 replay certification — see docs/vault/BACKTESTER_CERTIFICATION.md
# Policy: docs/ai/SHELL_EXECUTION.md
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$env:PYTHONPATH = "packages"

$Wrapper = Join-Path $RepoRoot 'tools/shell/run_with_timeout.ps1'
$BudgetSec = 180

$PyArgs = @(
    '-m', 'pytest',
    'tests/backtester_validation/fast',
    'tests/test_model_registry_slugs.py',
    'tests/test_workbench/',
    'tests/test_economic_event_universe/',
    '--ignore=tests/test_workbench/test_catalog_event_e2e.py',
    '-q', '--tb=no'
)

& $Wrapper -TimeoutSec $BudgetSec -Label 'agent-verify-preamble' -- python -m economic_event_universe.cli validate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Wrapper -TimeoutSec $BudgetSec -Label 'agent-verify' -- python @PyArgs
$verifyExit = $LASTEXITCODE
if ($verifyExit -ne 0) { exit $verifyExit }

$HandoffFile = $env:HANDOFF_STATUS_FILE
if ($HandoffFile -and (Test-Path -LiteralPath $HandoffFile)) {
  & $Wrapper -TimeoutSec 30 -Label 'handoff-status' -- python scripts/check_handoff_status.py $HandoffFile --require
  exit $LASTEXITCODE
}
exit 0
