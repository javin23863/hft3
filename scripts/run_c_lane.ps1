# scripts/run_c_lane.ps1
# C-lane runner — Windows daily subset.
#
# This script runs the checks that are feasible on Windows/MinGW.
# It is the DAILY SUBSET; scripts/run_c_lane.sh is the AUTHORITATIVE FULL LANE
# (includes ASan+UBSan and TSan builds, which require a Linux toolchain).
#
# CORRECTNESS §2 rows covered: 1 (syntax/Wall/Wextra/Werror check only),
#                               2 (skipped — TSan requires Linux),
#                               3 (64-slot parity precondition + parity run),
#                               8 (test_decision_runtime_hardening.exe),
#                               12 (Wall/Wextra/Werror syntax compile).
#
# Exit code: nonzero on any failure.
#
# Usage:
#   scripts\run_c_lane.ps1 [-NpzPath <path-to-npz>]
#
# Environment overrides:
#   HFT3_NPZ_ROOT  — directory to search for NPZ when -NpzPath not given.

param(
    [string]$NpzPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$REPO = (Resolve-Path "$PSScriptRoot\..").Path
$GPLUS = "C:\Users\MSI\AppData\Local\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\mingw64\bin\g++.exe"
$BUILD = Join-Path $REPO "build"
$FAILURES = @()

function Log-Section([string]$msg) {
    Write-Host "`n=== $msg ===" -ForegroundColor Cyan
}

function Fail([string]$msg) {
    Write-Host "FAIL: $msg" -ForegroundColor Red
    $script:FAILURES += $msg
}

function Pass([string]$msg) {
    Write-Host "PASS: $msg" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Row 3 precondition: pyd module must exist before parity run
# ---------------------------------------------------------------------------
Log-Section "Precondition: pybind module present"
$PYD = Join-Path $BUILD "hft3_features_cpp.cp312-win_amd64.pyd"
if (-not (Test-Path $PYD)) {
    Fail "build/hft3_features_cpp.cp312-win_amd64.pyd not found — parity step cannot run (CORRECTNESS row 3)"
} else {
    Pass "pybind module present: $PYD"
}

# ---------------------------------------------------------------------------
# Row 3: 64-slot parity via verify_cpp_parity.py
# ---------------------------------------------------------------------------
Log-Section "Row 3: 64-slot parity (verify_cpp_parity.py)"
$npz = $NpzPath
if (-not $npz -and $env:HFT3_NPZ_ROOT) {
    $npz = Get-ChildItem -Path $env:HFT3_NPZ_ROOT -Filter "*.npz" -Recurse -ErrorAction SilentlyContinue |
           Select-Object -First 1 -ExpandProperty FullName
}
if ($npz -and (Test-Path $PYD)) {
    $env:PYTHONPATH = "C:\Users\MSI\AppData\Local\Programs\Python\Python312\Lib\site-packages;C:\Users\MSI\AppData\Roaming\Python\Python312\site-packages"
    $parityScript = Join-Path $REPO "scripts\verify_cpp_parity.py"
    & python -S $parityScript --npz $npz
    if ($LASTEXITCODE -ne 0) {
        Fail "verify_cpp_parity.py exited $LASTEXITCODE — slot mismatch or module absent"
    } else {
        Pass "64-slot parity confirmed"
    }
} elseif (-not (Test-Path $PYD)) {
    Fail "64-slot parity skipped — pyd absent (already listed above)"
} else {
    Write-Host "WARNING: No NPZ path provided and HFT3_NPZ_ROOT not set — parity check skipped" -ForegroundColor Yellow
    $FAILURES += "parity-skipped: no NPZ path (set HFT3_NPZ_ROOT or pass -NpzPath)"
}

# ---------------------------------------------------------------------------
# Row 8: test_decision_runtime_hardening.exe
# ---------------------------------------------------------------------------
Log-Section "Row 8: decision runtime hardening binary"
$HARDENING_EXE = Join-Path $BUILD "test_decision_runtime_hardening.exe"
if (Test-Path $HARDENING_EXE) {
    & $HARDENING_EXE
    if ($LASTEXITCODE -ne 0) {
        Fail "test_decision_runtime_hardening.exe exited $LASTEXITCODE"
    } else {
        Pass "test_decision_runtime_hardening.exe green"
    }
} else {
    Fail "test_decision_runtime_hardening.exe not found at $HARDENING_EXE"
}

# ---------------------------------------------------------------------------
# Row ? (safety injection): test_safety_failure_injection.exe
# NOTE: binary produced by a parallel agent — tolerate absence with explicit FAIL.
# ---------------------------------------------------------------------------
Log-Section "Safety failure injection binary"
$INJECTION_EXE = Join-Path $BUILD "test_safety_failure_injection.exe"
if (Test-Path $INJECTION_EXE) {
    & $INJECTION_EXE
    if ($LASTEXITCODE -ne 0) {
        Fail "test_safety_failure_injection.exe exited $LASTEXITCODE"
    } else {
        Pass "test_safety_failure_injection.exe green"
    }
} else {
    Fail "test_safety_failure_injection.exe NOT FOUND — binary pending parallel agent (explicit FAIL, not silent skip)"
}

# ---------------------------------------------------------------------------
# Rows 1/12: -Wall -Wextra -Werror syntax compile of first-party C++ TUs
# (no link; follows rithmic_gateway/tools/safety_poller_syntax_check.cpp pattern)
# ---------------------------------------------------------------------------
Log-Section "Rows 1/12: -Wall -Wextra -Werror syntax compile"
if (-not (Test-Path $GPLUS)) {
    Fail "g++ not found at expected MinGW path: $GPLUS"
} else {
    $INCLUDES = @(
        "-I", (Join-Path $REPO "rithmic_gateway\include"),
        "-I", (Join-Path $REPO "risk_engine\include"),
        "-I", (Join-Path $REPO "packages\decision_engine\cpp\include"),
        "-I", (Join-Path $REPO "packages\features_engine\cpp\include")
    )

    # TU list: hot-path first-party translation units (source only, no SDK link needed for syntax check).
    # rithmic_adapter.cpp includes RApiPlus.h (Rithmic vendor SDK, not available on this workstation);
    # c_api.cpp covers rithmic_gateway headers without the SDK dependency.
    # safety_poller_syntax_check.cpp is a standalone tool with a -Werror=comment issue (backslash in
    # comment); it is checked via its own cmake target.
    $TUS = @(
        "rithmic_gateway\src\c_api.cpp",
        "risk_engine\src\risk_manager.cpp",
        "packages\decision_engine\cpp\src\decision_runtime.cpp",
        "packages\features_engine\cpp\src\feature_extractor.cpp",
        "packages\features_engine\cpp\src\event_context.cpp",
        "packages\features_engine\cpp\src\regime_filter.cpp"
    )

    foreach ($tu in $TUS) {
        $fullTu = Join-Path $REPO $tu
        if (-not (Test-Path $fullTu)) {
            Fail "TU not found: $tu"
            continue
        }
        $gppArgs = @("-std=c++20", "-Wall", "-Wextra", "-Werror", "-fsyntax-only") + $INCLUDES + @($fullTu)
        $stderr = & $GPLUS @gppArgs 2>&1 | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | Out-String
        if ($LASTEXITCODE -ne 0) {
            Fail "-Wall -Wextra -Werror failed for $tu`n$stderr"
        } else {
            Pass "-Wall clean: $tu"
        }
    }
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host "`n=== C-LANE SUMMARY ===" -ForegroundColor Cyan
if ($FAILURES.Count -eq 0) {
    Write-Host "ALL CHECKS PASSED" -ForegroundColor Green
    exit 0
} else {
    Write-Host "FAILURES ($($FAILURES.Count)):" -ForegroundColor Red
    foreach ($f in $FAILURES) {
        Write-Host "  - $f" -ForegroundColor Red
    }
    exit 1
}
