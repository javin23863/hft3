#!/usr/bin/env python
"""Toxicity-gate A/B report from EXISTING campaign receipts (PR-1, zero replay).

The hbt_stagec3_a326db8f envelope carried 195 base + 195 ``_tox`` parameter
sets (identical params plus toxicity_max_vpin / toxicity_block_regime). This
script recomputes each set's parameter_hash with the manifest's own
``_parameter_hash``, pairs base<->_tox by source_candidate_id, and reports the
paired per-model economics diff from a per-run economics JSONL
(pass_a_runs.jsonl.gz — one row per completed run, carrying parameter_hash).

VERDICT SCOPE (grader fix #7): results are labeled
``expression-v1-conditional`` — the A/B was measured under the v1 passive
expression whose exits were 99.2% timeouts. A null here kills nothing and a
positive proves little; the BINDING toxicity decision re-runs as paired arms
under the v2 expression in PR-5.
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics as pystats
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages"), str(REPO / "apps")]

SCHEMA_VERSION = "hft3_tox_ab_report_v1"


def _load_sets(envelope_path: Path) -> list[dict[str, Any]]:
    data = json.loads(envelope_path.read_text(encoding="utf-8"))
    sets_ = data if isinstance(data, list) else data.get("parameter_sets") or []
    if not sets_:
        raise SystemExit(f"envelope_empty:{envelope_path}")
    return sets_


def _hash_sets(sets_: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """parameter_hash -> {model, candidate_id, is_tox, base_key}."""
    from backtest_pipeline.src.hftbacktest_only_campaign_manifest import _parameter_hash

    out: dict[str, dict[str, Any]] = {}
    for s in sets_:
        model = str(s.get("canonical_model_id") or "")
        cand = str(s.get("source_candidate_id") or "")
        params = s.get("strategy_params") or {}
        h = _parameter_hash(
            canonical_model_id=model,
            parameter_family=str(s.get("parameter_family") or ""),
            strategy_params=params,
        )
        is_tox = cand.endswith("_tox")
        out[h] = {
            "model": model,
            "candidate_id": cand,
            "is_tox": is_tox,
            "base_key": cand[:-4] if is_tox else cand,
        }
    return out


def _iter_runs(runs_path: Path):
    opener = gzip.open if runs_path.suffix == ".gz" else open
    with opener(runs_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Toxicity A/B from existing receipts")
    parser.add_argument("--envelope", type=Path,
                        default=REPO / "runtime" / "stagec1" / "envelope_rt_tox_ab.json")
    parser.add_argument("--runs-jsonl", type=Path, required=True,
                        help="per-run economics JSONL(.gz) carrying parameter_hash "
                             "(e.g. box_pull/.../pass_a_runs.jsonl.gz)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--hash-prefix-len", type=int, default=0,
                        help="if runs carry truncated hashes, match on this prefix length")
    args = parser.parse_args(argv)

    sets_ = _load_sets(args.envelope)
    by_hash = _hash_sets(sets_)
    prefix = int(args.hash_prefix_len)
    lookup = (
        {h[:prefix]: v for h, v in by_hash.items()} if prefix else dict(by_hash)
    )
    if prefix and len(lookup) != len(by_hash):
        raise SystemExit(f"hash_prefix_collision:len={prefix}")

    # accumulate per (model, base_key, arm): pnl sums over matched runs
    acc: dict[tuple[str, str, bool], dict[str, float]] = defaultdict(
        lambda: {"runs": 0, "realized": 0.0, "net": 0.0, "fills": 0, "orders": 0}
    )
    matched = unmatched = 0
    for run in _iter_runs(args.runs_jsonl):
        h = str(run.get("parameter_hash") or "")
        key = h[:prefix] if prefix else h
        meta = lookup.get(key)
        if meta is None:
            unmatched += 1
            continue
        matched += 1
        a = acc[(meta["model"], meta["base_key"], meta["is_tox"])]
        a["runs"] += 1
        a["realized"] += float(run.get("realized_closed_trade_pnl") or 0.0)
        a["net"] += float(run.get("net_pnl") or 0.0)
        a["fills"] += int(run.get("fills_count") or 0)
        a["orders"] += int(run.get("orders_submitted") or 0)
    if matched == 0:
        raise SystemExit("no_runs_matched_envelope_hashes:check --hash-prefix-len")

    # pair arms per (model, base_key)
    per_model_diffs: dict[str, list[dict[str, float]]] = defaultdict(list)
    unpaired = 0
    keys = {(m, b) for (m, b, _t) in acc}
    for m, b in sorted(keys):
        base = acc.get((m, b, False))
        tox = acc.get((m, b, True))
        if not base or not tox or not base["runs"] or not tox["runs"]:
            unpaired += 1
            continue
        per_model_diffs[m].append({
            "base_runs": base["runs"], "tox_runs": tox["runs"],
            "d_realized_per_run": tox["realized"] / tox["runs"] - base["realized"] / base["runs"],
            "d_net_per_run": tox["net"] / tox["runs"] - base["net"] / base["runs"],
            "d_fill_rate": (tox["fills"] / max(1, tox["orders"]))
                           - (base["fills"] / max(1, base["orders"])),
        })

    models_out: dict[str, Any] = {}
    for m, diffs in sorted(per_model_diffs.items()):
        d_real = [d["d_realized_per_run"] for d in diffs]
        d_net = [d["d_net_per_run"] for d in diffs]
        models_out[m] = {
            "pairs": len(diffs),
            "mean_d_realized_per_run": round(pystats.fmean(d_real), 4),
            "mean_d_net_per_run": round(pystats.fmean(d_net), 4),
            "median_d_net_per_run": round(pystats.median(d_net), 4),
            "mean_d_fill_rate": round(pystats.fmean(d["d_fill_rate"] for d in diffs), 4),
        }

    report = {
        "schema_version": SCHEMA_VERSION,
        "verdict_scope": "expression-v1-conditional",
        "scope_note": (
            "Measured under the v1 passive expression (99.2% timeout exits). "
            "A null kills nothing; a positive proves little. Binding toxicity "
            "decision re-runs as paired arms under expression v2 in PR-5."
        ),
        "envelope": str(args.envelope),
        "runs_jsonl": str(args.runs_jsonl),
        "sets_hashed": len(by_hash),
        "runs_matched": matched,
        "runs_unmatched": unmatched,
        "unpaired_arms": unpaired,
        "models": models_out,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps({"models": len(models_out), "runs_matched": matched,
                      "runs_unmatched": unmatched}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
