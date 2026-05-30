# Grader verification — pytest for workbench + structural models (not run by desktop shortcut).
$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $RepoRoot
$env:PYTHONPATH = $RepoRoot

$checklist = Join-Path $RepoRoot 'docs/workbench/GRADER_CHECKLIST.md'

Write-Host 'Running workbench grader pytest suite...' -ForegroundColor Cyan
& python -m pytest tests/test_workbench/ tests/structural_models/ -q --tb=short
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host "pytest failed (exit $exitCode). See grader checklist: $checklist" -ForegroundColor Red
    exit $exitCode
}

Write-Host 'pytest passed.' -ForegroundColor Green
exit 0
