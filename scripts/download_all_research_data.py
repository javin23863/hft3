#!/usr/bin/env python3
"""
Orchestrate full research data download (macro imbalance + equities decadal lane).

Phase A (default): audit → fill imbalance gaps → estimate-decadal → exit awaiting confirmation.
Phase B (--confirm-pull): pull-decadal --resume --pull-options → options-only retry → final audit.

OPRA chains are pulled via equities_lane pull-decadal, not download_imbalance_research_data.py.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

_DECADAL_CFG = _REPO / "packages/equities_lane/config/decadal_runners.yaml"
_CRYPTO_BT_CFG = _REPO / "backtests/configs/crypto_hypotheses/h1_basis_compression.yaml"


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print(f"\n>>> {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=_REPO, check=check)


def _load_audit_module():
    import importlib.util

    path = _REPO / "scripts/audit_all_research_data.py"
    spec = importlib.util.spec_from_file_location("audit_all_research_data", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _audit() -> dict[str, Any]:
    mod = _load_audit_module()
    report = mod.audit_report()
    report["_has_imbalance_gaps"] = mod.has_imbalance_gaps(report)
    return report


def _fill_imbalance_if_needed(report: dict[str, Any], max_cost: float) -> None:
    if not report.get("_has_imbalance_gaps"):
        print("=== Imbalance gaps: none — skip download_imbalance_research_data ===", flush=True)
        return
    _run(
        [
            sys.executable,
            str(_REPO / "scripts/download_imbalance_research_data.py"),
            "--all",
            f"--max-cost-usd={max_cost}",
        ]
    )


def _estimate_decadal() -> dict[str, Any]:
    from equities_lane.src.ingest.decadal_pull import estimate_catalog_cost

    rows = estimate_catalog_cost(_DECADAL_CFG)
    total = sum(r.get("total_cost_usd") or 0 for r in rows)
    mbo = sum(r.get("mbo_cost_usd") or 0 for r in rows)
    daily = sum(r.get("daily_cost_usd") or 0 for r in rows)
    options = sum(r.get("options_cost_usd") or 0 for r in rows)
    payload = {
        "estimates": rows,
        "total_cost_usd": total,
        "mbo_cost_usd": mbo,
        "daily_cost_usd": daily,
        "options_cost_usd": options,
    }
    print(json.dumps(payload, indent=2), flush=True)
    return payload


def _pull_decadal(*, options_only: bool = False) -> int:
    cmd = [
        sys.executable,
        "-m",
        "equities_lane.pipeline",
        "pull-decadal",
        "--decadal-config",
        str(_DECADAL_CFG),
        "--resume",
        "--override-hard-limit",
        "--override-operating-cap",
    ]
    if options_only:
        cmd.append("--options-only")
    else:
        cmd.append("--pull-options")
    proc = _run(cmd, check=False)
    return proc.returncode


def _sessions_needing_options_retry(report: dict[str, Any]) -> list[str]:
    failed = report.get("options_failed") or []
    missing = report.get("options_missing") or []
    ids = []
    for item in failed:
        if isinstance(item, dict):
            ids.append(item["session_id"])
    ids.extend(missing)
    return sorted(set(ids))


def _options_retry_per_session(session_ids: list[str]) -> None:
    for sid in session_ids:
        print(f"\n=== options-only retry: {sid} ===", flush=True)
        _run(
            [
                sys.executable,
                "-m",
                "equities_lane.pipeline",
                "pull-decadal",
                "--decadal-config",
                str(_DECADAL_CFG),
                "--session-id",
                sid,
                "--resume",
                "--options-only",
                "--override-hard-limit",
                "--override-operating-cap",
            ],
            check=False,
        )


def _crypto_date_range() -> tuple[str, str]:
    import yaml

    if _CRYPTO_BT_CFG.is_file():
        cfg = yaml.safe_load(_CRYPTO_BT_CFG.read_text(encoding="utf-8"))
        dr = cfg.get("date_range") or {}
        return str(dr.get("start", "2024-01-01")), str(dr.get("end", "2024-12-31"))
    return "2024-01-01", "2024-12-31"


def _run_crypto_phase(
    *,
    replace_synthetic: bool = False,
    allow_degraded: bool = True,
) -> dict[str, Any]:
    from crypto_lane.src.config.env_loader import ensure_crypto_env
    from crypto_lane.src.ingest.l3_preflight import preflight_l3_gaps
    from crypto_lane.src.ingest.mempool_preflight import preflight_mempool_gaps
    from crypto_lane.src.ingest.node_remote_sync import sync_chi404_btc_node_artifacts

    ensure_crypto_env()
    start, end = _crypto_date_range()
    steps: dict[str, Any] = {
        "chi404_node_sync": sync_chi404_btc_node_artifacts(),
        "preflight": preflight_l3_gaps(start=start, end=end),
        "mempool_preflight": preflight_mempool_gaps(start=start, end=end),
    }
    purge_safe = bool(steps["preflight"].get("purge_safe"))
    if replace_synthetic and not purge_safe:
        steps["replace_synthetic_skipped"] = steps["preflight"].get("purge_block_reason")
        replace_synthetic = False
    _run(
        [
            sys.executable,
            "-m",
            "crypto_lane.pipeline",
            "pull-gold",
            "--start",
            start,
            "--end",
            end,
        ],
        check=False,
    )
    if not steps["mempool_preflight"].get("mempool_ready"):
        _run(
            [
                sys.executable,
                "-m",
                "crypto_lane.pipeline",
                "pull-gold",
                "--start",
                start,
                "--end",
                end,
                "--sources",
                "mempool",
            ],
            check=False,
        )
        steps["mempool_preflight_after_pull"] = preflight_mempool_gaps(start=start, end=end)
    fill_cmd = [
        sys.executable,
        "-m",
        "crypto_lane.pipeline",
        "fill-l3-gaps",
        "--start",
        start,
        "--end",
        end,
    ]
    if replace_synthetic:
        fill_cmd.append("--replace-synthetic")
    if allow_degraded:
        fill_cmd.append("--allow-degraded")
    proc = _run(fill_cmd, check=False)
    steps["fill_l3_exit_code"] = proc.returncode
    _run(
        [
            sys.executable,
            "-m",
            "crypto_lane.pipeline",
            "normalize",
            "--start",
            start,
            "--end",
            end,
        ],
        check=False,
    )
    mp_pf = steps.get("mempool_preflight_after_pull") or steps["mempool_preflight"]
    if not mp_pf.get("mempool_ready"):
        if mp_pf.get("btc_node_synced"):
            try:
                from crypto_lane.src.ingest.mempool_pull import backfill_blockspace_from_node

                steps["blockspace_written"] = backfill_blockspace_from_node(
                    start=start, end=end, step_hours=1
                )
                steps["mempool_preflight_after_blockspace"] = preflight_mempool_gaps(
                    start=start, end=end
                )
            except Exception as exc:
                steps["blockspace_error"] = str(exc)
        else:
            steps["blockspace_skipped"] = "mempool gaps remain; btc node not synced or status unknown"
            steps["mempool_preflight_degraded"] = preflight_mempool_gaps(
                start=start,
                end=end,
                allow_degraded_mempool=True,
            )
    mod = _load_audit_module()
    steps["crypto_audit"] = {
        k: v
        for k, v in mod.audit_report().items()
        if k.startswith("crypto_") or k == "lanes_ready"
    }
    return steps


def _write_status(status: dict[str, Any]) -> Path:
    out = _REPO / "runtime/data_audits/research_data_status.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(f"\nWrote {out}", flush=True)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Download all research data (orchestrated)")
    p.add_argument(
        "--confirm-pull",
        action="store_true",
        help="Phase B: run pull-decadal after estimate (requires explicit approval)",
    )
    p.add_argument("--max-cost-usd", type=float, default=200.0)
    p.add_argument("--skip-imbalance-download", action="store_true")
    p.add_argument(
        "--skip-crypto-download",
        action="store_true",
        help="Skip Phase C crypto lane pull (B2/Vision/normalize)",
    )
    p.add_argument(
        "--replace-synthetic-crypto",
        action="store_true",
        help="Phase C: purge synthetic bookticker only when preflight says B2 can replace",
    )
    p.add_argument(
        "--no-degraded-crypto",
        action="store_true",
        help="Phase C: do not fill remaining bookticker gaps from perp klines",
    )
    args = p.parse_args()

    status: dict[str, Any] = {"phase": "A" if not args.confirm_pull else "B"}

    report = _audit()
    status["audit_initial"] = {k: v for k, v in report.items() if not k.startswith("_")}

    if not args.skip_imbalance_download:
        _fill_imbalance_if_needed(report, args.max_cost_usd)
        report = _audit()
        status["audit_after_imbalance"] = {k: v for k, v in report.items() if not k.startswith("_")}

    estimate = _estimate_decadal()
    status["estimate"] = estimate

    if not args.skip_crypto_download:
        print("\n=== Phase C: crypto lane (no Databento spend) ===", flush=True)
        status["crypto_phase"] = _run_crypto_phase(
            replace_synthetic=args.replace_synthetic_crypto,
            allow_degraded=not args.no_degraded_crypto,
        )
        report = _audit()
        status["audit_after_crypto"] = {k: v for k, v in report.items() if not k.startswith("_")}

    if not args.confirm_pull:
        status["awaiting_confirmation"] = True
        status["message"] = (
            "Estimate complete. Re-run with --confirm-pull (or download_all_research_data.ps1 -ConfirmPull) "
            "after approving Databento spend."
        )
        _write_status(status)
        print("\n=== AWAITING CONFIRMATION — no pull-decadal run ===", flush=True)
        return 0

    status["awaiting_confirmation"] = False
    status["phase"] = "B"
    rc = _pull_decadal(options_only=False)
    status["pull_decadal_exit_code"] = rc

    report = _audit()
    retry_ids = _sessions_needing_options_retry(report)
    if retry_ids:
        print(f"\n=== Options retry for {len(retry_ids)} session(s) ===", flush=True)
        _options_retry_per_session(retry_ids)
        status["options_retry_sessions"] = retry_ids

    mod = _load_audit_module()
    final = mod.audit_report()
    status["audit_final"] = final
    status["ready"] = final.get("ready", False)
    _write_status(status)

    gaps_path = _REPO / "runtime/data_audits/research_data_gaps.json"
    gaps_path.write_text(json.dumps(final, indent=2), encoding="utf-8")

    if not final.get("ready"):
        print("\n=== GAPS REMAIN (see research_data_gaps.json) ===", flush=True)
        return 1
    print("\n=== ALL RESEARCH DATA AUDITS GREEN ===", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
