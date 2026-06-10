"""tests/test_engine_replay_determinism.py

Determinism test for hft3_engine REPLAY mode (CHI404_RUNTIME.md §7.4 / §10).

Verifies:
  1. export_replay_feed.py produces a deterministic feed from the real NPZ.
  2. hft3_engine REPLAY runs twice on that feed with identical weights.
  3. Both decision log files are byte-identical.
  4. Both logs are non-trivial (> MIN_LOG_RECORDS records × 40 bytes).

The binary is auto-built if missing (pattern: tests/test_decision_runtime_hardening.py).

Environment:
    HFT3_NPZ_ROOT — must point to a directory containing the target NPZ file.
                    Defaults used: MES.v.0_UNEMPLOYMENT_CLAIMS_2026_06_04_TIGHT_mbo.npz

Setup:
    1. export_replay_feed.py called with --npz <target> --out <temp feed>
    2. A valid weights file is written via export_weights_to_cpp logic.
    3. hft3_engine run twice with same feed + weights → logs compared byte-by-byte.
"""
from __future__ import annotations

import hashlib
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO    = Path(__file__).resolve().parents[1]
_BUILD   = _REPO / "build"
_ENGINE  = _BUILD / "hft3_engine.exe"
_EXPORT  = _REPO / "scripts" / "export_replay_feed.py"

_NPZ_ROOT = os.environ.get("HFT3_NPZ_ROOT", "")
_TARGET_NPZ_NAME = os.environ.get(
    "HFT3_NPZ_NAME",
    "MES.v.0_UNEMPLOYMENT_CLAIMS_2026_06_04_TIGHT_mbo.npz",
)

_GPLUS = (
    "C:/Users/MSI/AppData/Local/Microsoft/WinGet/Packages/"
    "BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe/"
    "mingw64/bin/g++.exe"
)

_MIN_LOG_RECORDS = 10  # minimum non-trivial record count in each log
_LOG_RECORD_SIZE = 48  # bytes per LogRecord (spec says 40; 6×uint64_t = 48 for alignment)

# ---------------------------------------------------------------------------
# Python executable helper (avoids bare "python" stall)
# ---------------------------------------------------------------------------
_PYTHON_EXE = sys.executable


def _run_python_S(script: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        "C:\\Users\\MSI\\AppData\\Local\\Programs\\Python\\Python312\\Lib\\site-packages;"
        "C:\\Users\\MSI\\AppData\\Roaming\\Python\\Python312\\site-packages"
    )
    return subprocess.run(
        [_PYTHON_EXE, "-S", script, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


# ---------------------------------------------------------------------------
# Auto-build hft3_engine if missing
# ---------------------------------------------------------------------------
def _try_build_engine() -> bool:
    """Attempt to compile hft3_engine.exe with standalone g++ invocation.
    Returns True on success."""
    gpp = Path(_GPLUS)
    if not gpp.exists():
        return False

    includes = [
        f"-I{_REPO / 'rithmic_gateway' / 'include'}",
        f"-I{_REPO / 'risk_engine' / 'include'}",
        f"-I{_REPO / 'packages' / 'decision_engine' / 'cpp' / 'include'}",
        f"-I{_REPO / 'packages' / 'features_engine' / 'cpp' / 'include'}",
        f"-I{_REPO / 'engine' / 'include'}",
    ]
    sources = [
        str(_REPO / "risk_engine" / "src" / "risk_manager.cpp"),
        str(_REPO / "packages" / "decision_engine" / "cpp" / "src" / "decision_runtime.cpp"),
        str(_REPO / "packages" / "features_engine" / "cpp" / "src" / "feature_extractor.cpp"),
        str(_REPO / "packages" / "features_engine" / "cpp" / "src" / "event_context.cpp"),
        str(_REPO / "packages" / "features_engine" / "cpp" / "src" / "regime_filter.cpp"),
        str(_REPO / "engine" / "src" / "engine_config.cpp"),
        str(_REPO / "engine" / "src" / "hft3_engine_main.cpp"),
    ]
    out = str(_ENGINE)
    _BUILD.mkdir(exist_ok=True)
    result = subprocess.run(
        [str(gpp), "-std=c++20", "-O2", *includes, *sources, "-o", out],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"[build stderr]\n{result.stderr}", file=sys.stderr)
    return result.returncode == 0 and Path(out).exists()


def _get_engine() -> Path:
    if not _ENGINE.exists():
        built = _try_build_engine()
        if not built or not _ENGINE.exists():
            pytest.skip(
                f"hft3_engine binary not found at {_ENGINE} and auto-build failed. "
                "Run: cmake --build build or standalone g++ per engine/src/hft3_engine_main.cpp"
            )
    return _ENGINE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_weights(path: str, feature_count: int = 64) -> None:
    """Write a valid weights binary (deterministic, known weights)."""
    magic = 0x48465433
    version = 1
    model_id = 1
    header = struct.pack("<IIII", magic, version, model_id, feature_count)
    # Fixed weights: alternating 0.01 / -0.01 for first feature_count, rest 0.
    weights = []
    for i in range(1024):
        if i < feature_count:
            weights.append(0.01 if i % 2 == 0 else -0.01)
        else:
            weights.append(0.0)
    body = struct.pack(f"<{len(weights)}d", *weights)
    with open(path, "wb") as f:
        f.write(header + body)


def _find_target_npz() -> str:
    """Locate the target NPZ; skip test if not found."""
    if not _NPZ_ROOT:
        pytest.skip(
            "HFT3_NPZ_ROOT not set — cannot run determinism test. "
            f"Set HFT3_NPZ_ROOT to directory containing {_TARGET_NPZ_NAME}"
        )
    p = Path(_NPZ_ROOT) / _TARGET_NPZ_NAME
    if not p.exists():
        pytest.skip(f"Target NPZ not found: {p}")
    return str(p)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def feed_path(tmp_path_factory):
    """Export the target NPZ to a flat binary feed once per test module."""
    npz = _find_target_npz()
    tmp = tmp_path_factory.mktemp("replay")
    feed = str(tmp / "replay_feed.bin")

    result = _run_python_S(str(_EXPORT), "--npz", npz, "--out", feed)
    if result.returncode != 0:
        pytest.fail(
            f"export_replay_feed.py failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    if result.stdout:
        print(result.stdout)

    assert Path(feed).exists(), "export_replay_feed.py produced no output file"
    feed_size = Path(feed).stat().st_size
    assert feed_size > 0, "feed file is empty"
    assert feed_size % 34 == 0, f"feed file size {feed_size} not divisible by 34"

    n_records = feed_size // 34
    print(f"[determinism] Feed: {n_records} records ({feed_size} bytes)")
    return feed


@pytest.fixture(scope="module")
def weights_path(tmp_path_factory):
    """Write a fixed-weight model file once per test module."""
    tmp = tmp_path_factory.mktemp("weights")
    w = str(tmp / "model.bin")
    _write_weights(w, feature_count=64)
    return w


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_feed_export_deterministic(tmp_path):
    """Same NPZ → same feed bytes (export is deterministic)."""
    npz = _find_target_npz()
    feed1 = str(tmp_path / "feed1.bin")
    feed2 = str(tmp_path / "feed2.bin")

    for out in (feed1, feed2):
        r = _run_python_S(str(_EXPORT), "--npz", npz, "--out", out)
        assert r.returncode == 0, f"export failed:\n{r.stderr}"

    h1 = _sha256(feed1)
    h2 = _sha256(feed2)
    assert h1 == h2, f"Feed export not deterministic: {h1} vs {h2}"


def test_replay_logs_byte_identical(feed_path, weights_path, tmp_path):
    """Two REPLAY runs → byte-identical decision logs (§7.4 / §10)."""
    engine = _get_engine()

    log1 = str(tmp_path / "run1.log")
    log2 = str(tmp_path / "run2.log")

    # Minimal config file.
    cfg = str(tmp_path / "replay.cfg")
    with open(cfg, "w") as f:
        f.write(
            "mode=REPLAY\n"
            "warmup_events=50\n"
            "mbo_batch_size=64\n"
            "max_position=100\n"
            "daily_loss_limit=-1000000.0\n"
            "max_order_rate_per_sec=100\n"
            "max_cancel_rate_per_sec=100\n"
            "latency_ns=500\n"
        )

    for log_out in (log1, log2):
        result = subprocess.run(
            [
                str(engine),
                "--mode", "REPLAY",
                "--config", cfg,
                "--weights", weights_path,
                "--feed", feed_path,
                "--log", log_out,
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        assert result.returncode == 0, (
            f"hft3_engine exited {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    # Both logs must exist.
    assert Path(log1).exists(), "Run 1 produced no log file"
    assert Path(log2).exists(), "Run 2 produced no log file"

    size1 = Path(log1).stat().st_size
    size2 = Path(log2).stat().st_size

    assert size1 == size2, f"Log sizes differ: {size1} vs {size2}"
    assert size1 > 0, "Log file is empty"
    assert size1 % _LOG_RECORD_SIZE == 0, (
        f"Log size {size1} not divisible by {_LOG_RECORD_SIZE}"
    )

    n_records = size1 // _LOG_RECORD_SIZE
    print(f"[determinism] Log records per run: {n_records}")
    assert n_records >= _MIN_LOG_RECORDS, (
        f"Non-trivial check failed: only {n_records} records (need >= {_MIN_LOG_RECORDS})"
    )

    h1 = _sha256(log1)
    h2 = _sha256(log2)
    assert h1 == h2, (
        f"Decision logs are NOT byte-identical!\n"
        f"run1 SHA256: {h1}\nrun2 SHA256: {h2}\n"
        f"Log size: {size1} bytes = {n_records} records"
    )

    print(f"[determinism] PASS: {n_records} records, SHA256={h1[:16]}...")


def test_replay_throughput(feed_path, weights_path, tmp_path):
    """Run REPLAY and report events/sec; assert > 1000 events/s (sanity floor)."""
    import time

    engine = _get_engine()
    cfg = str(tmp_path / "replay_tput.cfg")
    with open(cfg, "w") as f:
        f.write(
            "mode=REPLAY\n"
            "warmup_events=50\n"
            "mbo_batch_size=64\n"
            "max_position=100\n"
            "daily_loss_limit=-1000000.0\n"
            "max_order_rate_per_sec=100\n"
            "max_cancel_rate_per_sec=100\n"
            "latency_ns=500\n"
        )
    log_path = str(tmp_path / "tput.log")

    t0 = time.perf_counter()
    result = subprocess.run(
        [
            str(engine),
            "--mode", "REPLAY",
            "--config", cfg,
            "--weights", weights_path,
            "--feed", feed_path,
            "--log", log_path,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    elapsed = time.perf_counter() - t0

    assert result.returncode == 0, (
        f"hft3_engine exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    # Parse throughput from stdout.
    tput_line = [l for l in result.stdout.splitlines() if "events/s" in l]
    if tput_line:
        print(f"[throughput] {tput_line[-1].strip()}")

    # Feed size in records.
    feed_size = Path(feed_path).stat().st_size
    n_records = feed_size // 34
    tput = n_records / elapsed if elapsed > 0 else 0.0
    print(f"[throughput] {n_records} events in {elapsed:.3f}s = {tput:.0f} events/s")

    assert tput > 1000, f"Throughput too low: {tput:.0f} events/s"
