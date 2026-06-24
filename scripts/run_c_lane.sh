#!/usr/bin/env bash
# scripts/run_c_lane.sh
# C-lane runner — Linux/CHI404 AUTHORITATIVE FULL LANE.
#
# This is the canonical, full C-lane script that includes:
#   - -Wall -Wextra -Werror syntax compile of all first-party C++ TUs
#   - ASan+UBSan build and run of test binaries  (CORRECTNESS row 1)
#   - TSan build and run of concurrency tests     (CORRECTNESS row 2)
#   - 64-slot parity via verify_cpp_parity.py     (CORRECTNESS row 3)
#   - test_decision_runtime_hardening binary       (CORRECTNESS row 8)
#   - test_safety_failure_injection binary         (pending parallel agent)
#
# scripts/run_c_lane.ps1 is the Windows DAILY SUBSET — it cannot run
# ASan/UBSan or TSan because those require a Linux toolchain (MinGW does not
# support -fsanitize=address/-fsanitize=thread at time of writing).
#
# Usage:
#   bash scripts/run_c_lane.sh [--npz <path-to-npz>]
#
# Environment overrides:
#   HFT3_NPZ_ROOT  — directory to search for NPZ when --npz not given.
#   BUILD_DIR      — override the default ./build directory.
#   PYTHON_BIN     — Python interpreter used for pybind discovery/parity.
#
# Exit code: nonzero on any failure.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-$REPO/build}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
FAILURES=()
NPZ_PATH=""

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --npz) NPZ_PATH="$2"; shift 2 ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

log_section() { echo; echo "=== $1 ==="; }
pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1" >&2; FAILURES+=("$1"); }
find_pybind_so() {
    find "$BUILD_DIR" -maxdepth 2 -type f -name 'hft3_features_cpp*.so' | sort | head -n 1
}
pybind11_cmake_arg() {
    if [[ -n "${HFT3_PYBIND11_DIR:-}" ]]; then
        printf '%s\n' "-Dpybind11_DIR=$HFT3_PYBIND11_DIR"
        return 0
    fi

    local cmake_dir
    if cmake_dir="$("$PYTHON_BIN" -m pybind11 --cmakedir 2>/dev/null)" && [[ -n "$cmake_dir" ]]; then
        printf '%s\n' "-Dpybind11_DIR=$cmake_dir"
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Precondition: pybind module (Linux .so)
# ---------------------------------------------------------------------------
log_section "Precondition: build pybind module"
mkdir -p "$BUILD_DIR"
PYD=""
PYBIND_ARG=""
PYBIND_ARGS=()
if PYBIND_ARG="$(pybind11_cmake_arg)"; then
    PYBIND_ARGS=("$PYBIND_ARG")
    pass "pybind11 CMake dir resolved: ${PYBIND_ARG#-Dpybind11_DIR=}"
else
    fail "pybind11 CMake dir unavailable — install pybind11 or set HFT3_PYBIND11_DIR"
fi
if cmake -B "$BUILD_DIR" -S "$REPO" -DCMAKE_BUILD_TYPE=Release \
         -DPython3_EXECUTABLE="$PYTHON_BIN" "${PYBIND_ARGS[@]}" -Wno-dev 2>&1; then
    if cmake --build "$BUILD_DIR" --target hft3_features_cpp 2>&1; then
        PYD="$(find_pybind_so)"
    else
        fail "pybind module build target failed — parity step cannot run (CORRECTNESS row 3)"
    fi
else
    fail "pybind module configure failed — parity step cannot run (CORRECTNESS row 3)"
fi

if [[ -z "$PYD" || ! -f "$PYD" ]]; then
    fail "pybind module hft3_features_cpp*.so not found under $BUILD_DIR — parity step cannot run (CORRECTNESS row 3)"
else
    pass "pybind module present: $PYD"
fi

# ---------------------------------------------------------------------------
# Row 3: 64-slot parity
# ---------------------------------------------------------------------------
log_section "Row 3: 64-slot parity (verify_cpp_parity.py)"
if [[ -z "$NPZ_PATH" && -n "${HFT3_NPZ_ROOT:-}" ]]; then
    NPZ_PATH="$(find "$HFT3_NPZ_ROOT" -name "*.npz" | head -1)"
fi
if [[ -n "$NPZ_PATH" && -f "$PYD" ]]; then
    HFT3_FEATURES_CPP_BUILD_DIR="$BUILD_DIR" "$PYTHON_BIN" "$REPO/scripts/verify_cpp_parity.py" --npz "$NPZ_PATH" \
        && pass "64-slot parity confirmed" \
        || fail "verify_cpp_parity.py non-zero exit — slot mismatch or module absent"
elif [[ ! -f "$PYD" ]]; then
    fail "64-slot parity skipped — pyd absent (already listed above)"
else
    echo "WARNING: No NPZ path provided and HFT3_NPZ_ROOT not set — parity check skipped" >&2
    FAILURES+=("parity-skipped: no NPZ path (set HFT3_NPZ_ROOT or pass --npz)")
fi

# ---------------------------------------------------------------------------
# Row 1/12: -Wall -Wextra -Werror syntax compile
# ---------------------------------------------------------------------------
log_section "Rows 1/12: -Wall -Wextra -Werror syntax compile"
INCLUDES=(
    "-I$REPO/rithmic_gateway/include"
    "-I$REPO/risk_engine/include"
    "-I$REPO/packages/decision_engine/cpp/include"
    "-I$REPO/packages/features_engine/cpp/include"
    "-I$REPO/engine/include"
    "-I$REPO/rithmic_gateway/RApiPlus/13.7.0.0/include"
)
# rithmic_adapter.cpp includes RApiPlus.h (Rithmic vendor SDK); c_api.cpp covers rithmic_gateway
# headers without the SDK dependency.  safety_poller_syntax_check.cpp is a standalone tool with a
# backslash-in-comment that triggers -Werror=comment; it is checked via its own cmake target.
TUS=(
    "rithmic_gateway/src/c_api.cpp"
    "risk_engine/src/risk_manager.cpp"
    "packages/decision_engine/cpp/src/decision_runtime.cpp"
    "packages/features_engine/cpp/src/feature_extractor.cpp"
    "packages/features_engine/cpp/src/event_context.cpp"
    "packages/features_engine/cpp/src/regime_filter.cpp"
    "engine/src/engine_config.cpp"
    "engine/src/hft3_engine_main.cpp"
)
for tu in "${TUS[@]}"; do
    full="$REPO/$tu"
    if [[ ! -f "$full" ]]; then
        fail "TU not found: $tu"
        continue
    fi
    if g++ -std=c++20 -Wall -Wextra -Werror -fsyntax-only "${INCLUDES[@]}" "$full" 2>&1; then
        pass "-Wall clean: $tu"
    else
        fail "-Wall -Wextra -Werror failed for $tu"
    fi
done

# ---------------------------------------------------------------------------
# Row 1: ASan + UBSan build + run
# ---------------------------------------------------------------------------
log_section "Row 1: ASan + UBSan build"
ASAN_BUILD="$BUILD_DIR/asan_build"
mkdir -p "$ASAN_BUILD"
if cmake -B "$ASAN_BUILD" -S "$REPO" -DCMAKE_BUILD_TYPE=Asan -DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++ \
         -DPython3_EXECUTABLE="$PYTHON_BIN" "${PYBIND_ARGS[@]}" \
         -DCMAKE_CXX_FLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer" \
         -DCMAKE_EXE_LINKER_FLAGS="-fsanitize=address,undefined" \
         -Wno-dev 2>&1; then
    if cmake --build "$ASAN_BUILD" --target test_decision_runtime_hardening test_safety_failure_injection 2>&1; then
        ASAN_BIN="$ASAN_BUILD/test_decision_runtime_hardening"
        if [[ -f "$ASAN_BIN" ]]; then
            ASAN_SYMBOLIZER_PATH="$(which llvm-symbolizer 2>/dev/null || true)" \
            ASAN_OPTIONS="halt_on_error=1:detect_leaks=1" \
            UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1" \
              "$ASAN_BIN" && pass "ASan+UBSan: test_decision_runtime_hardening clean" \
                          || fail "ASan+UBSan: test_decision_runtime_hardening reported errors"
        else
            fail "ASan+UBSan: test_decision_runtime_hardening binary not built"
        fi
    else
        fail "ASan+UBSan: cmake build failed for test_decision_runtime_hardening"
    fi
else
    fail "ASan+UBSan: cmake configure failed"
fi

# Safety injection — ASan variant (tolerate absence)
INJECTION_ASAN="$ASAN_BUILD/test_safety_failure_injection"
if [[ -f "$INJECTION_ASAN" ]]; then
    ASAN_OPTIONS="halt_on_error=1:detect_leaks=1" \
    UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1" \
      "$INJECTION_ASAN" && pass "ASan+UBSan: test_safety_failure_injection clean" \
                        || fail "ASan+UBSan: test_safety_failure_injection reported errors"
else
    fail "ASan+UBSan: test_safety_failure_injection NOT FOUND — binary pending parallel agent (explicit FAIL)"
fi

# ---------------------------------------------------------------------------
# Row 2: TSan build + run
# ---------------------------------------------------------------------------
log_section "Row 2: TSan build"
TSAN_BUILD="$BUILD_DIR/tsan_build"
mkdir -p "$TSAN_BUILD"
if cmake -B "$TSAN_BUILD" -S "$REPO" -DCMAKE_BUILD_TYPE=Tsan -DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++ \
         -DPython3_EXECUTABLE="$PYTHON_BIN" "${PYBIND_ARGS[@]}" \
         -DCMAKE_CXX_FLAGS="-fsanitize=thread -fno-omit-frame-pointer" \
         -DCMAKE_EXE_LINKER_FLAGS="-fsanitize=thread" \
         -Wno-dev 2>&1; then
    # TSan targets per CORRECTNESS row 2: spsc_queue_stress, risk_manager_atomic_stress, safety_poller_concurrent
    for tsan_target in spsc_queue_stress risk_manager_atomic_stress safety_poller_concurrent; do
        if cmake --build "$TSAN_BUILD" --target "$tsan_target" 2>&1; then
            BIN="$TSAN_BUILD/$tsan_target"
            if [[ -f "$BIN" ]]; then
                TSAN_OPTIONS="halt_on_error=1" "$BIN" \
                    && pass "TSan: $tsan_target clean" \
                    || fail "TSan: $tsan_target reported data-race errors"
            else
                fail "TSan: $tsan_target binary not found after build"
            fi
        else
            fail "TSan: cmake build failed for $tsan_target"
        fi
    done
else
    fail "TSan: cmake configure failed"
fi

# ---------------------------------------------------------------------------
# Release gate binaries
# ---------------------------------------------------------------------------
log_section "Release gate binaries"
if cmake --build "$BUILD_DIR" \
        --target test_decision_runtime_hardening test_safety_failure_injection test_engine_loop hft3_engine \
        2>&1; then
    pass "release gate binaries built"
else
    fail "release gate binary build failed"
fi

# ---------------------------------------------------------------------------
# Row 8: test_decision_runtime_hardening (release binary)
# ---------------------------------------------------------------------------
log_section "Row 8: decision runtime hardening binary (release)"
HARDENING_EXE="$BUILD_DIR/test_decision_runtime_hardening"
if [[ -f "$HARDENING_EXE" ]]; then
    "$HARDENING_EXE" && pass "test_decision_runtime_hardening green" \
                     || fail "test_decision_runtime_hardening exited non-zero"
else
    fail "test_decision_runtime_hardening not found at $HARDENING_EXE"
fi

# ---------------------------------------------------------------------------
# Safety failure injection binary (release)
# NOTE: produced by a parallel agent — tolerate absence with explicit FAIL.
# ---------------------------------------------------------------------------
log_section "Safety failure injection binary (release)"
INJECTION_EXE="$BUILD_DIR/test_safety_failure_injection"
if [[ -f "$INJECTION_EXE" ]]; then
    "$INJECTION_EXE" && pass "test_safety_failure_injection green" \
                     || fail "test_safety_failure_injection exited non-zero"
else
    fail "test_safety_failure_injection NOT FOUND — binary pending parallel agent (explicit FAIL, not silent skip)"
fi

# ---------------------------------------------------------------------------
# Engine loop gate binary (release)
# ---------------------------------------------------------------------------
log_section "Engine loop gate binary (release)"
ENGINE_LOOP_EXE="$BUILD_DIR/test_engine_loop"
if [[ -f "$ENGINE_LOOP_EXE" ]]; then
    "$ENGINE_LOOP_EXE" && pass "test_engine_loop green" \
                       || fail "test_engine_loop exited non-zero"
else
    fail "test_engine_loop not found at $ENGINE_LOOP_EXE"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo
echo "=== C-LANE SUMMARY ==="
if [[ ${#FAILURES[@]} -eq 0 ]]; then
    echo "ALL CHECKS PASSED"
    exit 0
else
    echo "FAILURES (${#FAILURES[@]}):" >&2
    for f in "${FAILURES[@]}"; do
        echo "  - $f" >&2
    done
    exit 1
fi
