from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "fix_options_fixing_empty_dbn_sidecars.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("fix_options_fixing_empty_dbn_sidecars", _SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Metadata:
    def __init__(self, *, dataset: str, schema: str, symbols: list[str], start: int, end: int) -> None:
        self.dataset = dataset
        self.schema = schema
        self.symbols = symbols
        self.start = start
        self.end = end


class _Store:
    def __init__(self, metadata: _Metadata, records: list[object] | None = None) -> None:
        self.metadata = metadata
        self._records = records or []

    def __iter__(self):
        return iter(self._records)


def _install_fake_databento(monkeypatch: pytest.MonkeyPatch, stores: dict[str, _Store]) -> None:
    class _DBNStore:
        @staticmethod
        def from_file(path: str) -> _Store:
            return stores[path]

    monkeypatch.setitem(sys.modules, "databento", types.SimpleNamespace(DBNStore=_DBNStore))


def _candidate(module, tmp_path: Path, name: str = "ES_fixing_2026-01-05.dbn.zst"):
    path = tmp_path / name
    path.write_bytes(b"dbn-bytes")
    match = module.FIXING_RE.match(path.name)
    assert match is not None
    return module.Candidate(
        path=path,
        expiry_date=date.fromisoformat(match.group(2)),
        schema="trades" if match.group(1) else "mbo",
    )


def _window(module, expiry: date) -> tuple[int, int]:
    start_ns, end_ns, _, _ = module._expected_window_ns(expiry)
    return start_ns, end_ns


def test_dry_run_validates_but_does_not_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _load_module()
    candidate = _candidate(module, tmp_path)
    start_ns, end_ns = _window(module, candidate.expiry_date)
    _install_fake_databento(
        monkeypatch,
        {
            str(candidate.path): _Store(
                _Metadata(
                    dataset="GLBX.MDP3",
                    schema="mbo",
                    symbols=["ES.v.0"],
                    start=start_ns,
                    end=end_ns,
                )
            )
        },
    )

    ok_count, skipped, failed = module.process_root(tmp_path, write=False)

    assert (ok_count, skipped, failed) == (1, 0, 0)
    assert not candidate.path.with_name(f"{candidate.path.name}.doctor.json").exists()


def test_write_creates_data_doctor_compatible_no_data_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    candidate = _candidate(module, tmp_path, "ES_fixing_trades_2026-01-05.dbn.zst")
    start_ns, end_ns = _window(module, candidate.expiry_date)
    _install_fake_databento(
        monkeypatch,
        {
            str(candidate.path): _Store(
                _Metadata(
                    dataset="GLBX.MDP3",
                    schema="trades",
                    symbols=["ES.v.0"],
                    start=start_ns,
                    end=end_ns,
                )
            )
        },
    )

    ok_count, skipped, failed = module.process_root(tmp_path, write=True)

    assert (ok_count, skipped, failed) == (1, 0, 0)
    sidecar = candidate.path.with_name(f"{candidate.path.name}.doctor.json")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["valid"] is True
    assert payload["vendor_no_data_proof"] is True
    assert payload["record_count"] == 0
    assert payload["schema"] == "trades"
    assert payload["dataset"] == "GLBX.MDP3"
    assert payload["symbols"] == ["ES.v.0"]
    assert payload["size_bytes"] == len(b"dbn-bytes")
    assert len(payload["sha256"]) == 64
    assert payload["expected_start_ns"] == start_ns
    assert payload["expected_end_ns"] == end_ns
    assert payload["metadata_start_ns"] == start_ns
    assert payload["metadata_end_ns"] == end_ns
    assert payload["proof_source"] == module.PROOF_SOURCE
    assert payload["source_artifact"].endswith(candidate.path.name)

    scripts_path = str(_REPO / "scripts")
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    import data_doctor  # noqa: PLC0415

    assert data_doctor._valid_dbn_sidecar(candidate.path, payload, expected_schema="trades") == (
        True,
        "doctor sidecar ok",
    )


@pytest.mark.parametrize(
    ("metadata_kwargs", "records", "reason"),
    [
        ({"dataset": "XNAS.ITCH", "schema": "mbo", "symbols": ["ES.v.0"]}, [], "dataset"),
        ({"dataset": "GLBX.MDP3", "schema": "trades", "symbols": ["ES.v.0"]}, [], "schema"),
        ({"dataset": "GLBX.MDP3", "schema": "mbo", "symbols": ["NQ.v.0"]}, [], "symbol"),
    ],
)
def test_rejects_unproven_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    metadata_kwargs: dict[str, object],
    records: list[object],
    reason: str,
) -> None:
    module = _load_module()
    candidate = _candidate(module, tmp_path)
    start_ns, end_ns = _window(module, candidate.expiry_date)
    _install_fake_databento(
        monkeypatch,
        {
            str(candidate.path): _Store(
                _Metadata(start=start_ns, end=end_ns, **metadata_kwargs),
                records=records,
            )
        },
    )

    with pytest.raises(module.ProofError, match=reason):
        module.build_sidecar_payload(candidate)
    assert not candidate.path.with_name(f"{candidate.path.name}.doctor.json").exists()


def test_positive_record_artifact_is_skipped_not_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    candidate = _candidate(module, tmp_path)
    start_ns, end_ns = _window(module, candidate.expiry_date)
    _install_fake_databento(
        monkeypatch,
        {
            str(candidate.path): _Store(
                _Metadata(
                    dataset="GLBX.MDP3",
                    schema="mbo",
                    symbols=["ES.v.0"],
                    start=start_ns,
                    end=end_ns,
                ),
                records=[object()],
            )
        },
    )

    ok_count, skipped, failed = module.process_root(tmp_path, write=True)

    assert (ok_count, skipped, failed) == (0, 1, 0)
    assert not candidate.path.with_name(f"{candidate.path.name}.doctor.json").exists()


def test_existing_sidecar_is_not_overwritten(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    candidate = _candidate(module, tmp_path)
    sidecar = candidate.path.with_name(f"{candidate.path.name}.doctor.json")
    sidecar.write_text('{"valid": true}\n', encoding="utf-8")
    start_ns, end_ns = _window(module, candidate.expiry_date)
    _install_fake_databento(
        monkeypatch,
        {
            str(candidate.path): _Store(
                _Metadata(
                    dataset="GLBX.MDP3",
                    schema="mbo",
                    symbols=["ES.v.0"],
                    start=start_ns,
                    end=end_ns,
                )
            )
        },
    )

    ok_count, skipped, failed = module.process_root(tmp_path, write=True)

    assert (ok_count, skipped, failed) == (0, 1, 0)
    assert sidecar.read_text(encoding="utf-8") == '{"valid": true}\n'


def test_existing_no_data_sidecar_is_revalidated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    candidate = _candidate(module, tmp_path)
    start_ns, end_ns = _window(module, candidate.expiry_date)
    _install_fake_databento(
        monkeypatch,
        {
            str(candidate.path): _Store(
                _Metadata(
                    dataset="GLBX.MDP3",
                    schema="mbo",
                    symbols=["ES.v.0"],
                    start=start_ns,
                    end=end_ns,
                )
            )
        },
    )
    payload = module.build_sidecar_payload(candidate)
    sidecar = candidate.path.with_name(f"{candidate.path.name}.doctor.json")
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    ok_count, skipped, failed = module.process_root(tmp_path, write=True)

    assert (ok_count, skipped, failed) == (0, 1, 0)


def test_existing_stale_no_data_sidecar_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    candidate = _candidate(module, tmp_path)
    start_ns, end_ns = _window(module, candidate.expiry_date)
    _install_fake_databento(
        monkeypatch,
        {
            str(candidate.path): _Store(
                _Metadata(
                    dataset="GLBX.MDP3",
                    schema="mbo",
                    symbols=["ES.v.0"],
                    start=start_ns,
                    end=end_ns,
                ),
                records=[object()],
            )
        },
    )
    sidecar = candidate.path.with_name(f"{candidate.path.name}.doctor.json")
    sidecar.write_text(
        json.dumps({"valid": True, "vendor_no_data_proof": True}),
        encoding="utf-8",
    )

    ok_count, skipped, failed = module.process_root(tmp_path, write=True)

    assert (ok_count, skipped, failed) == (0, 0, 1)
    assert json.loads(sidecar.read_text(encoding="utf-8")) == {
        "valid": True,
        "vendor_no_data_proof": True,
    }


def test_rejects_wrong_fixing_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _load_module()
    candidate = _candidate(module, tmp_path)
    start_ns, end_ns = _window(module, candidate.expiry_date)
    _install_fake_databento(
        monkeypatch,
        {
            str(candidate.path): _Store(
                _Metadata(
                    dataset="GLBX.MDP3",
                    schema="mbo",
                    symbols=["ES.v.0"],
                    start=start_ns + 1,
                    end=end_ns,
                )
            )
        },
    )

    with pytest.raises(module.ProofError, match="metadata start"):
        module.build_sidecar_payload(candidate)


def test_rejects_float_nanosecond_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _load_module()
    candidate = _candidate(module, tmp_path)
    start_ns, end_ns = _window(module, candidate.expiry_date)
    _install_fake_databento(
        monkeypatch,
        {
            str(candidate.path): _Store(
                _Metadata(
                    dataset="GLBX.MDP3",
                    schema="mbo",
                    symbols=["ES.v.0"],
                    start=float(start_ns),
                    end=end_ns,
                )
            )
        },
    )

    with pytest.raises(module.ProofError, match="metadata start invalid"):
        module.build_sidecar_payload(candidate)


def test_payload_uses_utc_generated_timestamp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _load_module()
    candidate = _candidate(module, tmp_path)
    start_ns, end_ns = _window(module, candidate.expiry_date)
    _install_fake_databento(
        monkeypatch,
        {
            str(candidate.path): _Store(
                _Metadata(
                    dataset="GLBX.MDP3",
                    schema="mbo",
                    symbols=["ES.v.0"],
                    start=start_ns,
                    end=end_ns,
                )
            )
        },
    )

    payload = module.build_sidecar_payload(
        candidate,
        generated_at=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
    )

    assert payload["generated_at_utc"] == "2026-06-14T12:00:00+00:00"


def test_ns_to_iso_preserves_sub_microsecond_remainder() -> None:
    module = _load_module()

    assert module._ns_to_iso(1_000_000_123) == "1970-01-01T00:00:01.000000123+00:00"
