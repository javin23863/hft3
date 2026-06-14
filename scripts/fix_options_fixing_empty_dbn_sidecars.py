#!/usr/bin/env python3
"""Create audited no-data proof sidecars for empty options fixing DBN files.

Ontology source: docs/cockpit/BUILDOUT_CORRECTNESS_CHECKLIST.md D-005.

Dry-run by default:
    python scripts/fix_options_fixing_empty_dbn_sidecars.py

Write sidecars only with:
    python scripts/fix_options_fixing_empty_dbn_sidecars.py --write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from numbers import Integral
from pathlib import Path
from typing import Any, Iterable

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from options_data.src.expiry_calendar import fixing_datetime_utc  # noqa: E402

DEFAULT_ROOT = Path(r"C:\hft3-lake\options\fixing_mbo")
DATASET = "GLBX.MDP3"
SYMBOL = "ES.v.0"
PROOF_SOURCE = "scripts/fix_options_fixing_empty_dbn_sidecars.py"
FIXING_WINDOW_BEFORE = timedelta(minutes=5)
FIXING_WINDOW_AFTER = timedelta(minutes=5)
FIXING_RE = re.compile(r"^ES_fixing_(trades_)?(\d{4}-\d{2}-\d{2})\.dbn\.zst$")


@dataclass(frozen=True)
class Candidate:
    path: Path
    expiry_date: date
    schema: str


class ProofError(RuntimeError):
    """Raised when an artifact cannot prove vendor no-data status."""


class SkipCandidate(RuntimeError):
    """Raised when a candidate does not need a no-data sidecar."""


def _sidecar_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.doctor.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dt_to_ns(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    utc = dt.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = utc - epoch
    return (
        delta.days * 86_400 * 1_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )


def _ns_to_iso(ns: int) -> str:
    seconds, remainder = divmod(ns, 1_000_000_000)
    dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
    if remainder % 1_000 == 0:
        return dt.replace(microsecond=remainder // 1_000).isoformat()
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{remainder:09d}+00:00"


def _metadata_value(metadata: Any, name: str) -> Any:
    if isinstance(metadata, dict):
        return metadata.get(name)
    return getattr(metadata, name, None)


def _value_name(value: Any) -> str:
    name = getattr(value, "value", value)
    return str(name)


def _normalize_schema(value: Any) -> str:
    raw = _value_name(value).lower()
    return raw.replace("_", "-")


def _metadata_symbols(metadata: Any) -> list[str]:
    raw = _metadata_value(metadata, "symbols")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, dict):
        values: list[str] = []
        for key, value in raw.items():
            values.append(str(key))
            if isinstance(value, (list, tuple, set)):
                values.extend(str(v) for v in value)
            elif value is not None:
                values.append(str(value))
        return values
    if isinstance(raw, Iterable):
        return [str(v) for v in raw]
    return [str(raw)]


def _metadata_ns(metadata: Any, name: str) -> int:
    value = _metadata_value(metadata, name)
    if value is None:
        raise ProofError(f"metadata {name} missing")
    if isinstance(value, datetime):
        return _dt_to_ns(value)
    if isinstance(value, Integral) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\d+", value):
        return int(value)
    raise ProofError(f"metadata {name} invalid: {value!r}")


def _load_existing_sidecar(sidecar: Path) -> dict[str, Any]:
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProofError(f"existing sidecar unreadable: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise ProofError("existing sidecar is not an object")
    return data


def _matching_no_data_sidecar(existing: dict[str, Any], rebuilt: dict[str, Any]) -> bool:
    keys = (
        "valid",
        "vendor_no_data_proof",
        "record_count",
        "schema",
        "size_bytes",
        "sha256",
        "dataset",
        "symbols",
        "expected_start_ns",
        "expected_end_ns",
        "metadata_start_ns",
        "metadata_end_ns",
        "source_artifact",
    )
    return all(existing.get(key) == rebuilt.get(key) for key in keys)


def _iter_candidates(root: Path) -> list[Candidate]:
    if not root.is_dir():
        return []
    candidates: list[Candidate] = []
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        match = FIXING_RE.match(path.name)
        if not match:
            continue
        candidates.append(
            Candidate(
                path=path,
                expiry_date=date.fromisoformat(match.group(2)),
                schema="trades" if match.group(1) else "mbo",
            )
        )
    return candidates


def _expected_window_ns(expiry_date: date) -> tuple[int, int, str, str]:
    fixing_utc = fixing_datetime_utc(expiry_date)
    start = fixing_utc - FIXING_WINDOW_BEFORE
    end = fixing_utc + FIXING_WINDOW_AFTER
    return _dt_to_ns(start), _dt_to_ns(end), start.isoformat(), end.isoformat()


def _open_store(path: Path) -> Any:
    try:
        import databento as db  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise ProofError(f"databento unavailable: {type(exc).__name__}") from exc
    try:
        return db.DBNStore.from_file(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ProofError(f"dbn open failed: {type(exc).__name__}: {exc}") from exc


def _verify_zero_records(store: Any) -> None:
    try:
        for _ in store:
            raise SkipCandidate("records present; no no-data sidecar needed")
    except SkipCandidate:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ProofError(f"dbn iteration failed: {type(exc).__name__}: {exc}") from exc


def build_sidecar_payload(candidate: Candidate, *, generated_at: datetime | None = None) -> dict[str, Any]:
    """Return a doctor sidecar payload after proving the DBN contains no records."""
    path = candidate.path
    if not path.is_file():
        raise ProofError("artifact missing")
    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        raise ProofError("artifact empty")

    store = _open_store(path)
    metadata = getattr(store, "metadata", None)
    if metadata is None:
        raise ProofError("metadata missing")

    dataset = _value_name(_metadata_value(metadata, "dataset"))
    if dataset != DATASET:
        raise ProofError(f"dataset {dataset!r} != {DATASET!r}")

    schema = _normalize_schema(_metadata_value(metadata, "schema"))
    if schema != candidate.schema:
        raise ProofError(f"schema {schema!r} != {candidate.schema!r}")

    symbols = _metadata_symbols(metadata)
    if SYMBOL not in symbols:
        raise ProofError(f"symbol {SYMBOL!r} missing from metadata symbols {symbols!r}")

    expected_start_ns, expected_end_ns, expected_start_iso, expected_end_iso = _expected_window_ns(candidate.expiry_date)
    metadata_start_ns = _metadata_ns(metadata, "start")
    metadata_end_ns = _metadata_ns(metadata, "end")
    if metadata_start_ns != expected_start_ns:
        raise ProofError(f"metadata start {metadata_start_ns} != expected {expected_start_ns}")
    if metadata_end_ns != expected_end_ns:
        raise ProofError(f"metadata end {metadata_end_ns} != expected {expected_end_ns}")

    _verify_zero_records(store)

    generated = generated_at or datetime.now(timezone.utc)
    return {
        "valid": True,
        "vendor_no_data_proof": True,
        "record_count": 0,
        "schema": candidate.schema,
        "size_bytes": size_bytes,
        "sha256": _sha256(path),
        "proof_source": PROOF_SOURCE,
        "dataset": DATASET,
        "symbols": [SYMBOL],
        "expected_start_ns": expected_start_ns,
        "expected_end_ns": expected_end_ns,
        "expected_start_utc": expected_start_iso,
        "expected_end_utc": expected_end_iso,
        "metadata_start_ns": metadata_start_ns,
        "metadata_end_ns": metadata_end_ns,
        "metadata_start_utc": _ns_to_iso(metadata_start_ns),
        "metadata_end_utc": _ns_to_iso(metadata_end_ns),
        "generated_at_utc": generated.astimezone(timezone.utc).isoformat(),
        "source_artifact": str(path.resolve(strict=False)),
    }


def process_root(root: Path, *, write: bool) -> tuple[int, int, int]:
    """Validate candidates under root; return (written_or_would_write, skipped, failed)."""
    ok_count = 0
    skipped = 0
    failed = 0
    for candidate in _iter_candidates(root):
        sidecar = _sidecar_path(candidate.path)
        if sidecar.exists():
            try:
                existing = _load_existing_sidecar(sidecar)
                if not existing.get("vendor_no_data_proof"):
                    skipped += 1
                    print(f"SKIP {candidate.path.name}: non-no-data sidecar already exists")
                    continue
                payload = build_sidecar_payload(candidate)
                if not _matching_no_data_sidecar(existing, payload):
                    raise ProofError("existing no-data sidecar does not match current DBN proof")
            except (ProofError, SkipCandidate) as exc:
                failed += 1
                print(f"FAIL {candidate.path.name}: existing sidecar stale or unproven: {exc}")
                continue
            skipped += 1
            print(f"SKIP {candidate.path.name}: existing no-data sidecar revalidated")
            continue
        try:
            payload = build_sidecar_payload(candidate)
        except SkipCandidate as exc:
            skipped += 1
            print(f"SKIP {candidate.path.name}: {exc}")
            continue
        except ProofError as exc:
            failed += 1
            print(f"FAIL {candidate.path.name}: {exc}")
            continue

        if write:
            sidecar.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"WROTE {sidecar}")
        else:
            print(f"DRY-RUN would write {sidecar}")
        ok_count += 1
    return ok_count, skipped, failed


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help=f"fixing DBN directory (default: {DEFAULT_ROOT})")
    parser.add_argument("--write", action="store_true", help="write .doctor.json sidecars; default is dry-run")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    ok_count, skipped, failed = process_root(args.root, write=bool(args.write))
    mode = "write" if args.write else "dry-run"
    print(f"SUMMARY mode={mode} root={args.root} ok={ok_count} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
