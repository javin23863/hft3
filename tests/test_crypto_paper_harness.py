"""C8 paper harness tests — CRYPTO_LIVE.md §5, LATENCY.md §10.3."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from execution import safety
from execution.adapters.crypto_broker import (
    CryptoPaperBrokerAdapter,
    CryptoTransportError,
    LatencySample,
)
from execution.crypto_paper_harness import (
    MIN_AUTHORITATIVE_SAMPLES,
    FIRST_BATCH_TARGET,
    build_latency_summary,
    write_latency_summary,
    CryptoPaperHarness,
)
from execution.interfaces import new_intent_id


# ---------------------------------------------------------------------------
# Fake clock (same approach as test_crypto_latency_capture.py)
# ---------------------------------------------------------------------------

class _FakeClock:
    """Fake perf_counter_ns: each call advances by step_ns."""

    def __init__(self, start: int = 1_000_000_000, step_ns: int = 2_000_000) -> None:
        self._val = start
        self._step = step_ns

    def __call__(self) -> int:
        v = self._val
        self._val += self._step
        return v


# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------

class FakeTransport:
    def __init__(self) -> None:
        self.call_count = 0

    def order_new(self, body: dict):
        self.call_count += 1
        cid = body.get("cid", 0)
        return [0, "SUCCESS", None, None, [[None, None, cid, None, None]]]

    def order_cancel(self, body: dict):
        return [0, "SUCCESS"]

    def cancel_all(self):
        return [0, "SUCCESS"]

    def open_orders(self):
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter(transport):
    return CryptoPaperBrokerAdapter(
        run_id="test", transport=transport, risk_check=lambda i: True
    )


def _accepted_sample(latency_ms: float = 10.0) -> LatencySample:
    base = 1_000_000_000
    delta = int(latency_ms * 1e6)
    return LatencySample(
        intent_id=new_intent_id(),
        cid=1,
        submit_ns=base,
        ack_ns=base + delta,
        accepted=True,
        shadow_synthetic=False,
    )


def _make_samples(n: int, latency_ms: float = 10.0, **overrides) -> list[LatencySample]:
    samples = []
    for i in range(n):
        base = 1_000_000_000 + i * 1_000_000_000
        delta = int(latency_ms * 1e6)
        s = LatencySample(
            intent_id=new_intent_id(),
            cid=i,
            submit_ns=base,
            ack_ns=base + delta,
            accepted=overrides.get("accepted", True),
            shadow_synthetic=overrides.get("shadow_synthetic", False),
        )
        samples.append(s)
    return samples


# ---------------------------------------------------------------------------
# Test 1: run_batch records n samples, all accepted
# ---------------------------------------------------------------------------

def test_run_batch_records_n_samples(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    clock = _FakeClock(start=1_000_000_000, step_ns=2_000_000)
    import execution.adapters.crypto_broker as cb_mod
    monkeypatch.setattr(cb_mod.time, "perf_counter_ns", clock)

    transport = FakeTransport()
    adapter = _make_adapter(transport)
    safety.reset_counters()

    harness = CryptoPaperHarness(adapter, run_id="t1")
    samples = harness.run_batch(10, base_price=60_000.0)

    assert len(samples) == 10
    assert all(s.accepted for s in samples)
    assert all(not s.shadow_synthetic for s in samples)
    assert all(s.ack_ns > s.submit_ns for s in samples)
    assert transport.call_count == 10


# ---------------------------------------------------------------------------
# Test 2: summary not measured below 1000 samples
# ---------------------------------------------------------------------------

def test_summary_not_measured_below_1000():
    samples = _make_samples(100)
    summary = build_latency_summary(samples, run_id="t2")

    assert summary["paper_order_latency"]["measured"] is False
    assert summary["paper_order_latency"]["paired_count"] == 100
    assert summary["first_batch_complete"] is True
    assert summary["total_samples"] == 100


# ---------------------------------------------------------------------------
# Test 3: summary measured at 1000
# ---------------------------------------------------------------------------

def test_summary_measured_at_1000():
    # Use two distinct latency values: 999 samples at 10 ms, 1 at 100 ms.
    # p99 nearest-rank: ceil(99/100 * 1000) = 990th value = 10.0 ms.
    samples = _make_samples(999, latency_ms=10.0) + _make_samples(1, latency_ms=100.0)
    assert len(samples) == 1000

    summary = build_latency_summary(samples, run_id="t3")

    assert summary["paper_order_latency"]["measured"] is True
    assert summary["paper_order_latency"]["authoritative"] is True
    assert summary["paper_order_latency"]["paired_count"] == 1000
    # p99 of [10.0 x 999, 100.0 x 1]: rank = ceil(0.99*1000)=990 → 10.0 ms
    assert summary["order_ack_p99_ms"] == pytest.approx(10.0)
    assert summary["shadow_synthetic_present"] is False


# ---------------------------------------------------------------------------
# Test 4: shadow_synthetic blocks measured even at 1000 samples
# ---------------------------------------------------------------------------

def test_shadow_synthetic_blocks_measured():
    # All 1000 samples are shadow_synthetic=True → none pass the accepted_real filter
    # → paired_count=0, measured=False, shadow_synthetic_present=True.
    samples = _make_samples(1000, shadow_synthetic=True)

    summary = build_latency_summary(samples, run_id="t4")

    assert summary["shadow_synthetic_present"] is True
    assert summary["paper_order_latency"]["measured"] is False
    assert summary["paper_order_latency"]["paired_count"] == 0


# ---------------------------------------------------------------------------
# Test 5: rejected samples excluded from p99
# ---------------------------------------------------------------------------

def test_rejected_excluded_from_p99():
    accepted = _make_samples(80, latency_ms=10.0, accepted=True)
    rejected = _make_samples(20, latency_ms=999.0, accepted=False)
    samples = accepted + rejected

    summary = build_latency_summary(samples, run_id="t5")

    assert summary["paper_order_latency"]["paired_count"] == 80
    assert summary["rejected_or_unaccepted"] == 20
    assert summary["total_samples"] == 100
    # p99 drawn only from 80 accepted — max is 10.0, not 999.0
    assert summary["order_ack_p99_ms"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Test 6: write and resolve round-trip integration
# ---------------------------------------------------------------------------

def test_write_and_resolve_roundtrip(tmp_path):
    from backtest_pipeline.src.crypto_latency import resolve_crypto_replay_latency_ms

    # Build 1000 accepted samples with known p99-eligible latency
    # 990 at 5.0 ms, 10 at 50.0 ms → p99 nearest-rank at ceil(0.99*1000)=990 → 5.0 ms
    samples = _make_samples(990, latency_ms=5.0) + _make_samples(10, latency_ms=50.0)
    summary = build_latency_summary(samples, run_id="t6")
    assert summary["paper_order_latency"]["measured"] is True

    out = tmp_path / "latency_summary.json"
    write_latency_summary(summary, out)

    ms, source = resolve_crypto_replay_latency_ms(summary_path=out)
    assert ms == pytest.approx(5.0)
    assert "authoritative" in source


# ---------------------------------------------------------------------------
# Test 7: write format — ends with newline, keys sorted
# ---------------------------------------------------------------------------

def test_write_format(tmp_path):
    samples = _make_samples(5)
    summary = build_latency_summary(samples, run_id="t7")
    out = tmp_path / "sub" / "latency_summary.json"

    write_latency_summary(summary, out)

    raw = out.read_text(encoding="utf-8")
    assert raw.endswith("\n"), "file must end with newline (repo convention)"

    parsed = json.loads(raw)
    keys = list(parsed.keys())
    assert keys == sorted(keys), "top-level keys must be sorted"


# ---------------------------------------------------------------------------
# Test 8: CLI simulated exit 0
# ---------------------------------------------------------------------------

def test_cli_simulated_exit0():
    script = Path(__file__).resolve().parents[1] / "scripts" / "crypto_paper_harness.py"
    packages_root = Path(__file__).resolve().parents[1] / "packages"

    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(packages_root)
        + os.pathsep
        + str(packages_root / "backtest_pipeline" / "src")
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )
    env["EXECUTION_MODE"] = "PAPER"
    env["LIVE_KILL_SWITCH"] = "armed"

    result = subprocess.run(
        [sys.executable, str(script), "--simulated", "--n", "20", "--base-price", "60000"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"exit {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Parse the JSON line on stdout
    stdout_line = result.stdout.strip()
    data = json.loads(stdout_line)

    assert data["simulated"] is True
    assert data["measured"] is False
    assert data["total_samples"] == 20


# ---------------------------------------------------------------------------
# Test 9: CLI simulated n=1000 cannot be consumed as authoritative
# ---------------------------------------------------------------------------

def test_cli_simulated_n1000_not_authoritative(tmp_path):
    from backtest_pipeline.src.crypto_latency import resolve_crypto_replay_latency_ms

    script = Path(__file__).resolve().parents[1] / "scripts" / "crypto_paper_harness.py"
    packages_root = Path(__file__).resolve().parents[1] / "packages"
    summary_file = tmp_path / "latency_summary.json"

    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(packages_root)
        + os.pathsep
        + str(packages_root / "backtest_pipeline" / "src")
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )
    env["EXECUTION_MODE"] = "PAPER"
    env["LIVE_KILL_SWITCH"] = "armed"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--simulated",
            "--n", "1000",
            "--base-price", "60000",
            "--summary-path", str(summary_file),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert result.returncode == 0, (
        f"exit {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    assert summary_file.is_file(), "summary file must be written"
    summary = json.loads(summary_file.read_text(encoding="utf-8"))

    assert summary["paper_order_latency"]["measured"] is False, (
        "simulated run must not be marked measured in the written file"
    )

    with pytest.raises(ValueError, match="UNMEASURED"):
        resolve_crypto_replay_latency_ms(summary_path=summary_file)
