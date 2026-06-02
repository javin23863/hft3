"""Phase 11 hardening tests for the HFT3 certification registry.

Covers:
- test_atomic_registry_promotion: write atomicity under simulated crash
- test_registry_schema: validation rejects bad records
- test_hash_chain_continuity: SHA-256 chain links across records
- test_audit_log_preserves_history: append-only behavior
- test_legacy_migration: one-shot migration from single-JSON to JSONL
- test_rollback_on_validation_failure: invalid record leaves no partial write
- test_concurrent_writers: serialized via lock
- test_lock_timeout: lock acquisition times out cleanly
"""
from __future__ import annotations

import json
import multiprocessing
import time
from pathlib import Path

import pytest

from hft3.validation.certification_registry import (
    CertificationRecord,
    GENESIS_HASH,
    audit_log_path,
    backup_path,
    canonical_json,
    load_audit_log,
    load_registry,
    record_hash,
    registry_path,
    save_registry,
    validate_record,
)
from hft3.validation.registry_errors import (
    HashChainBroken,
    RegistryLockTimeout,
    RegistrySchemaError,
)


# ---------- atomicity ----------


def test_atomic_registry_promotion_writes_both_files(tmp_path: Path) -> None:
    reg = CertificationRecord(
        latest_certification_run_id="CERT-atom-1",
        latest_certification_commit="abc1234",
        latest_certification_status="GREEN",
    )
    out = save_registry(reg, tmp_path)
    assert out == registry_path(tmp_path)
    assert out.is_file()
    assert audit_log_path(tmp_path).is_file()
    loaded = load_registry(tmp_path)
    assert loaded.latest_certification_run_id == "CERT-atom-1"
    assert loaded.latest_certification_status == "GREEN"


def test_atomic_registry_promotion_no_partial_state_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the temp-file rename fails, neither the legacy file nor the audit
    log should be touched."""
    from hft3.validation import certification_registry as cr

    reg = CertificationRecord(latest_certification_status="GREEN")
    save_registry(reg, tmp_path)
    pre_legacy = registry_path(tmp_path).read_text(encoding="utf-8")
    pre_audit = audit_log_path(tmp_path).read_text(encoding="utf-8")

    def boom(src: str, dst: str) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(cr.os, "replace", boom)
    with pytest.raises(OSError):
        save_registry(
            CertificationRecord(latest_certification_status="RED"),
            tmp_path,
        )
    assert registry_path(tmp_path).read_text(encoding="utf-8") == pre_legacy
    assert audit_log_path(tmp_path).read_text(encoding="utf-8") == pre_audit


# ---------- schema validation ----------


def test_registry_schema_accepts_valid_record() -> None:
    validate_record(CertificationRecord(latest_certification_status="GREEN").to_dict())


@pytest.mark.parametrize(
    "field,bad_value,reason",
    [
        ("latest_certification_status", "PURPLE", "status enum"),
        ("latest_certification_run_id", "BAD-id", "run id prefix"),
        ("latest_certification_commit", "xyz!@#", "commit hex"),
        ("latest_certification_timestamp", "not-a-date", "timestamp iso"),
        ("covered_modules", "not-a-list", "list type"),
        ("covered_modules", [1, 2], "list element type"),
        ("covered_latency_bands", ["a"], "numeric list"),
    ],
)
def test_registry_schema_rejects_bad_fields(field: str, bad_value, reason: str) -> None:
    rec = CertificationRecord().to_dict()
    rec[field] = bad_value
    with pytest.raises(RegistrySchemaError):
        validate_record(rec)


# ---------- hash chain ----------


def test_hash_chain_continuity_across_writes(tmp_path: Path) -> None:
    save_registry(
        CertificationRecord(latest_certification_run_id="CERT-1", latest_certification_status="GREEN"),
        tmp_path,
    )
    save_registry(
        CertificationRecord(latest_certification_run_id="CERT-2", latest_certification_status="YELLOW"),
        tmp_path,
    )
    save_registry(
        CertificationRecord(latest_certification_run_id="CERT-3", latest_certification_status="RED"),
        tmp_path,
    )
    records = load_audit_log(tmp_path)
    assert [r["record_seq"] for r in records] == [1, 2, 3]
    assert records[0]["prev_hash"] == GENESIS_HASH
    for prev, cur in zip(records, records[1:]):
        assert cur["prev_hash"] == prev["self_hash"]
    for r in records:
        assert r["self_hash"] == record_hash(r)


def test_hash_chain_detects_tampering(tmp_path: Path) -> None:
    save_registry(
        CertificationRecord(latest_certification_run_id="CERT-a", latest_certification_status="GREEN"),
        tmp_path,
    )
    save_registry(
        CertificationRecord(latest_certification_run_id="CERT-b", latest_certification_status="YELLOW"),
        tmp_path,
    )
    path = audit_log_path(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["latest_certification_status"] = "RED"
    lines[0] = json.dumps(rec, sort_keys=True, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(HashChainBroken):
        load_audit_log(tmp_path)


# ---------- audit log / history ----------


def test_audit_log_preserves_history(tmp_path: Path) -> None:
    save_registry(
        CertificationRecord(latest_certification_run_id="CERT-1", latest_certification_status="GREEN"),
        tmp_path,
    )
    save_registry(
        CertificationRecord(latest_certification_run_id="CERT-2", latest_certification_status="YELLOW"),
        tmp_path,
    )
    save_registry(
        CertificationRecord(latest_certification_run_id="CERT-3", latest_certification_status="RED"),
        tmp_path,
    )
    records = load_audit_log(tmp_path)
    assert len(records) == 3
    assert [r["latest_certification_run_id"] for r in records] == [
        "CERT-1",
        "CERT-2",
        "CERT-3",
    ]
    # load_registry returns the latest
    latest = load_registry(tmp_path)
    assert latest.latest_certification_run_id == "CERT-3"
    assert latest.latest_certification_status == "RED"


# ---------- legacy migration ----------


def test_legacy_migration_one_shot(tmp_path: Path) -> None:
    legacy = registry_path(tmp_path)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        json.dumps(
            CertificationRecord(
                latest_certification_run_id="CERT-legacy",
                latest_certification_status="GREEN",
            ).to_dict(),
            indent=2,
        ),
        encoding="utf-8",
    )
    rec = load_registry(tmp_path)
    assert rec.latest_certification_run_id == "CERT-legacy"
    assert audit_log_path(tmp_path).is_file()
    assert not legacy.exists(), "legacy file should be moved aside after migration"
    moved_files = list(tmp_path.glob("runtime/validation/certification_registry.json.legacy-*"))
    assert len(moved_files) == 1
    assert load_audit_log(tmp_path)[0]["record_seq"] == 1
    assert load_audit_log(tmp_path)[0]["prev_hash"] == GENESIS_HASH


# ---------- rollback on validation failure ----------


def test_rollback_on_validation_failure(tmp_path: Path) -> None:
    save_registry(
        CertificationRecord(latest_certification_run_id="CERT-ok", latest_certification_status="GREEN"),
        tmp_path,
    )
    pre_legacy = registry_path(tmp_path).read_text(encoding="utf-8")
    pre_audit = audit_log_path(tmp_path).read_text(encoding="utf-8")
    bad = CertificationRecord()
    bad_payload = bad.to_dict()
    bad_payload["latest_certification_status"] = "PURPLE"
    with pytest.raises(RegistrySchemaError):
        from hft3.validation.certification_registry import _append_audit_line, validate_record

        validate_record(bad_payload)
        _append_audit_line(audit_log_path(tmp_path), bad_payload)
    assert registry_path(tmp_path).read_text(encoding="utf-8") == pre_legacy
    assert audit_log_path(tmp_path).read_text(encoding="utf-8") == pre_audit


# ---------- concurrent writers ----------


def _worker_append(worker_id: int, root_str: str, count: int) -> None:
    from hft3.validation.certification_registry import save_registry, CertificationRecord

    root = Path(root_str)
    for i in range(count):
        save_registry(
            CertificationRecord(
                latest_certification_run_id=f"CERT-w{worker_id}-{i}",
                latest_certification_status="GREEN",
            ),
            root,
        )


def test_concurrent_writers_serialize(tmp_path: Path) -> None:
    workers = 3
    per_worker = 4
    procs = [
        multiprocessing.Process(
            target=_worker_append,
            args=(i, str(tmp_path), per_worker),
        )
        for i in range(workers)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert not p.is_alive(), f"worker hung: {p.pid}"
    records = load_audit_log(tmp_path)
    assert len(records) == workers * per_worker
    seqs = [r["record_seq"] for r in records]
    assert seqs == list(range(1, workers * per_worker + 1))
    prev = GENESIS_HASH
    for r in records:
        assert r["prev_hash"] == prev
        assert r["self_hash"] == record_hash(r)
        prev = r["self_hash"]


# ---------- lock timeout ----------


def test_lock_timeout_raises_cleanly(tmp_path: Path) -> None:
    from hft3.validation.certification_registry import _RegistryLock

    lock_file = tmp_path / "lock.test"
    held = _RegistryLock(lock_file, timeout_sec=2.0)
    held.__enter__()
    try:
        contender = _RegistryLock(lock_file, timeout_sec=0.2)
        t0 = time.monotonic()
        with pytest.raises(RegistryLockTimeout):
            contender.__enter__()
        elapsed = time.monotonic() - t0
        assert 0.15 <= elapsed < 1.5, f"timeout should be ~0.2s, got {elapsed:.2f}s"
    finally:
        held.__exit__(None, None, None)


# ---------- canonical_json stability ----------


def test_canonical_json_is_deterministic() -> None:
    a = canonical_json({"b": 1, "a": 2, "c": [3, 2, 1]})
    b = canonical_json({"c": [3, 2, 1], "a": 2, "b": 1})
    assert a == b


# ---------- backup file written ----------


def test_backup_written_before_rotate(tmp_path: Path) -> None:
    save_registry(
        CertificationRecord(latest_certification_run_id="CERT-1", latest_certification_status="GREEN"),
        tmp_path,
    )
    save_registry(
        CertificationRecord(latest_certification_run_id="CERT-2", latest_certification_status="YELLOW"),
        tmp_path,
    )
    bak = backup_path(tmp_path)
    assert bak.is_file()
    bak_data = json.loads(bak.read_text(encoding="utf-8"))
    assert bak_data["latest_certification_run_id"] == "CERT-1"
