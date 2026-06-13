from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "download_event_tape.py"
    spec = importlib.util.spec_from_file_location("download_event_tape", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


det = _load_module()


def _identity(symbols: list[str]) -> dict:
    return det._request_identity(
        event_id="E_TEST",
        symbols=symbols,
        start_utc=datetime(2024, 1, 2, 14, 0, tzinfo=timezone.utc),
        end_utc=datetime(2024, 1, 2, 14, 5, tzinfo=timezone.utc),
    )


def _dbn(path: Path) -> Path:
    path.write_bytes(b"x" * (det.SHELL_BYTES + 1))
    return path


def test_dbn_reuse_requires_exact_request_identity(tmp_path: Path) -> None:
    dbn = _dbn(tmp_path / "E_TEST_mbo.dbn.zst")
    exact = _identity(["ES.n.0", "NQ.n.0"])
    det._write_identity(dbn, exact)

    assert det._can_reuse_dbn(dbn, exact)
    assert not det._can_reuse_dbn(dbn, _identity(["ES.n.0"]))
    assert not det._can_reuse_dbn(dbn, _identity(["ES.n.0", "NQ.n.0", "YM.n.0"]))


def test_dbn_reuse_rejects_missing_sidecar_and_modified_file(tmp_path: Path) -> None:
    dbn = _dbn(tmp_path / "E_TEST_mbo.dbn.zst")
    exact = _identity(["ES.n.0"])

    assert not det._can_reuse_dbn(dbn, exact)

    det._write_identity(dbn, exact)
    dbn.write_bytes(b"y" * (det.SHELL_BYTES + 2))

    assert not det._can_reuse_dbn(dbn, exact)


def test_partial_conversion_keeps_dbn_and_sidecar(tmp_path: Path) -> None:
    dbn = _dbn(tmp_path / "E_TEST_mbo.dbn.zst")
    request = _identity(["ES.n.0", "NQ.n.0"])
    det._write_identity(dbn, request)

    det._cleanup_dbn_after_conversion(
        dbn,
        keep_dbn=False,
        requested_symbols=request["requested_symbols"],
        converted_symbols=["ES.n.0"],
    )

    assert dbn.is_file()
    assert det._identity_path(dbn).is_file()


def test_abandoned_timeout_is_not_retried() -> None:
    calls = 0

    def boom():
        nonlocal calls
        calls += 1
        raise TimeoutError("call timed out after 1s (abandoned)")

    with pytest.raises(TimeoutError):
        det._with_retry(boom, tries=4, base=0.0)

    assert calls == 1


def test_non_abandoned_transient_timeout_can_retry() -> None:
    calls = 0

    def flaky():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("read timed out")
        return "ok"

    assert det._with_retry(flaky, tries=2, base=0.0) == "ok"
    assert calls == 2


def test_budget_ledger_aggregates_across_shards(tmp_path: Path) -> None:
    ledger = tmp_path / "event_tape_budget_ledger.jsonl"
    first = _identity(["ES.n.0"])
    second = _identity(["NQ.n.0"])

    r1 = det._reserve_budget(
        ledger,
        first,
        event_id="E_TEST",
        cost_usd=4.0,
        cost_cap_usd=6.0,
        shard="0/2",
    )
    r2 = det._reserve_budget(
        ledger,
        second,
        event_id="E_TEST",
        cost_usd=3.0,
        cost_cap_usd=6.0,
        shard="1/2",
    )

    assert r1["allowed"] is True
    assert r2["allowed"] is False
    assert r2["running_usd"] == 4.0
    assert r2["after_usd"] == 7.0


def test_budget_duplicate_requires_reconciliation_not_new_spend(tmp_path: Path) -> None:
    ledger = tmp_path / "event_tape_budget_ledger.jsonl"
    request = _identity(["ES.n.0"])

    first = det._reserve_budget(
        ledger,
        request,
        event_id="E_TEST",
        cost_usd=2.0,
        cost_cap_usd=6.0,
        shard="0/2",
    )
    duplicate = det._reserve_budget(
        ledger,
        request,
        event_id="E_TEST",
        cost_usd=2.0,
        cost_cap_usd=6.0,
        shard="1/2",
    )

    assert first["allowed"] is True
    assert duplicate == {"allowed": True, "duplicate": True, "running_usd": 2.0, "after_usd": 2.0}
    assert det._budget_attempt_recorded(ledger, request)


def test_budget_paid_superset_blocks_subset_reissue(tmp_path: Path) -> None:
    ledger = tmp_path / "event_tape_budget_ledger.jsonl"
    superset = _identity(["ES.n.0", "NQ.n.0"])
    subset = _identity(["NQ.n.0"])

    first = det._reserve_budget(
        ledger,
        superset,
        event_id="E_TEST",
        cost_usd=5.0,
        cost_cap_usd=10.0,
        shard="0/2",
    )
    before = ledger.read_text(encoding="utf-8")
    duplicate = det._reserve_budget(
        ledger,
        subset,
        event_id="E_TEST",
        cost_usd=2.0,
        cost_cap_usd=10.0,
        shard="1/2",
    )

    assert first["allowed"] is True
    assert det._budget_attempt_recorded(ledger, subset)
    assert duplicate == {"allowed": True, "duplicate": True, "running_usd": 5.0, "after_usd": 5.0}
    assert ledger.read_text(encoding="utf-8") == before


def test_budget_paid_subset_blocks_superset_reissue(tmp_path: Path) -> None:
    ledger = tmp_path / "event_tape_budget_ledger.jsonl"
    subset = _identity(["ES.n.0"])
    superset = _identity(["ES.n.0", "NQ.n.0"])

    first = det._reserve_budget(
        ledger,
        subset,
        event_id="E_TEST",
        cost_usd=2.0,
        cost_cap_usd=10.0,
        shard="0/2",
    )
    before = ledger.read_text(encoding="utf-8")
    conflict = det._reserve_budget(
        ledger,
        superset,
        event_id="E_TEST",
        cost_usd=5.0,
        cost_cap_usd=10.0,
        shard="1/2",
    )

    assert first["allowed"] is True
    assert det._budget_attempt_recorded(ledger, superset)
    assert conflict["allowed"] is False
    assert conflict["reason"] == "paid_symbol_overlap"
    assert conflict["conflict"]["overlap_symbols"] == ["ES.n.0"]
    assert ledger.read_text(encoding="utf-8") == before


def test_budget_paid_partial_overlap_blocks_reissue(tmp_path: Path) -> None:
    ledger = tmp_path / "event_tape_budget_ledger.jsonl"
    first_request = _identity(["ES.n.0", "NQ.n.0"])
    overlapping = _identity(["NQ.n.0", "YM.n.0"])

    first = det._reserve_budget(
        ledger,
        first_request,
        event_id="E_TEST",
        cost_usd=5.0,
        cost_cap_usd=12.0,
        shard="0/2",
    )
    before = ledger.read_text(encoding="utf-8")
    conflict = det._reserve_budget(
        ledger,
        overlapping,
        event_id="E_TEST",
        cost_usd=5.0,
        cost_cap_usd=12.0,
        shard="1/2",
    )

    assert first["allowed"] is True
    assert conflict["allowed"] is False
    assert conflict["reason"] == "paid_symbol_overlap"
    assert conflict["conflict"]["overlap_symbols"] == ["NQ.n.0"]
    assert ledger.read_text(encoding="utf-8") == before


def test_budget_attempt_recorded_fails_closed_on_corrupt_jsonl(tmp_path: Path) -> None:
    ledger = tmp_path / "event_tape_budget_ledger.jsonl"
    ledger.write_text('{"request_key":"ok","cost_usd":1.0}\n{not-json}\n', encoding="utf-8")
    before = ledger.read_text(encoding="utf-8")

    with pytest.raises(det.BudgetLedgerCorruptionError):
        det._budget_attempt_recorded(ledger, _identity(["ES.n.0"]))

    assert ledger.read_text(encoding="utf-8") == before


def test_reserve_budget_fails_closed_on_corrupt_jsonl_and_does_not_append(tmp_path: Path) -> None:
    ledger = tmp_path / "event_tape_budget_ledger.jsonl"
    ledger.write_text("{not-json}\n", encoding="utf-8")
    before = ledger.read_text(encoding="utf-8")

    with pytest.raises(det.BudgetLedgerCorruptionError):
        det._reserve_budget(
            ledger,
            _identity(["ES.n.0"]),
            event_id="E_TEST",
            cost_usd=2.0,
            cost_cap_usd=6.0,
            shard="0/2",
        )

    assert ledger.read_text(encoding="utf-8") == before
