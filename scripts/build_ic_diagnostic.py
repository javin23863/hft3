#!/usr/bin/env python
"""IC diagnostic — the gate for the event-alpha rebuild (PR-1).

Computes the repo's own hypothesis test (HYPOTHESIS_SPEC_TEMPLATE section 3)

    E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle

for every hypothesis model, per pre-registered horizon/threshold
(docs/hypotheses/HORIZON_MAP_PREREGISTERED.json), with the discipline the
EVENT_ALPHA_REBUILD_PLAN pre-commits:

- ONE MarketStatePipeline pass per tape evaluates ALL hypothesis adapters
  (each tape is one event x symbol window, so per-tape aggregation IS
  per-event aggregation — map-reduce with tiny worker payloads);
- inference on Confirmation years (2021-2022) ONLY; Discovery (2018-2020)
  supplies estimation extras (vol terciles, PR-3 barrier k); HOLDOUT (2023+)
  events are refused and receipted, enforced in code;
- errors two-way clustered (event x month); BH FDR q=0.10 over the primary
  family (one pre-registered H and s per model); DSR trials-deflation with
  num_trials = parameter sets ever evaluated for the model;
- spread-adjusted edge: E[signed move - half-spread | fired] — the bridge
  from mid-space IC to execution-space PnL that Pass A lacked;
- events with >20% fired-row censoring at H* are excluded (receipted);
- kill_list.json carries ONLY primary-family fields (schema-enforced);
  the exploratory grid lives in ic_report.json labeled exploratory.

Cross-asset lead-lag models abstain (signal == 0) without leader feature
legs; they receive verdict "no_verdict_leader_features_absent" — never a
fake pass/fail. Their first real test follows the PR-2 leader-lane unlock.

The pre-registered horizon map must be committed and clean in git; the
driver refuses to run otherwise (pre-registration is code, not convention).
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import math
import re
import statistics as pystats
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages"), str(REPO / "apps")]

SCHEMA_VERSION = "hft3_ic_diagnostic_v1"
KILL_LIST_SCHEMA = "hft3_ic_kill_list_v1"
DISCOVERY_YEARS = (2018, 2019, 2020)
CONFIRMATION_YEARS = (2021, 2022)
EXPLORATORY_HORIZONS_MS = [100, 250, 500, 1000, 3000, 5000, 15000, 60000]
BH_Q_PRIMARY = 0.10
MIN_EVENTS = 40
MIN_FIRED_ROWS_PER_EVENT = 5
MAX_CENSORING = 0.20
RESIDUAL_SLIPPAGE_TICKS = 0.5  # beyond the measured half-spread

# kill_list may carry ONLY these per-model keys (grader fix #4: no
# exploratory-grid field can leak into anything the PR-5 generator reads).
KILL_LIST_ALLOWED_FIELDS = frozenset(
    {
        "verdict",
        "h_star_ms",
        "threshold",
        "edge_ticks",
        "spread_adjusted_edge_ticks",
        "hurdle_fee_ticks",
        "pass_line_ticks",
        "p_raw",
        "bh_pass",
        "dsr",
        "n_events",
        "n_events_censor_excluded",
        "sigma_k_median",
        "alpha_class",
    }
)


def _event_year(event_id: str) -> int:
    m = re.search(r"_((?:19|20)\d{2})_\d{2}_\d{2}", str(event_id))
    if not m:
        raise SystemExit(f"unparseable_event_year:{event_id!r}")
    return int(m.group(1))


def _event_type(event_id: str) -> str:
    m = re.match(r"^(.*?)_(?:19|20)\d{2}_\d{2}_\d{2}", str(event_id))
    return m.group(1) if m else str(event_id)


def _month_key(event_id: str) -> str:
    m = re.search(r"_((?:19|20)\d{2})_(\d{2})_\d{2}", str(event_id))
    if not m:
        raise SystemExit(f"unparseable_event_month:{event_id!r}")
    return f"{m.group(1)}-{m.group(2)}"


def _require_committed_horizon_map(map_path: Path) -> str:
    """Pre-registration gate: map must be git-tracked and unmodified."""
    rel = map_path.resolve().relative_to(REPO.resolve()).as_posix()
    tracked = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--", rel],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if not tracked:
        raise SystemExit(f"horizon_map_not_committed:{rel}")
    dirty = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain", "--", rel],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if dirty:
        raise SystemExit(f"horizon_map_modified_uncommitted:{rel}")
    blob = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", f"HEAD:{rel}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return blob


def _load_units(manifest_path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Distinct prepared units from a campaign manifest; sealed-year events refused."""
    units: dict[str, dict[str, Any]] = {}
    excluded: set[str] = set()
    with manifest_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            npz = str(row.get("source_npz") or "")
            event_id = str(row.get("event_id") or "")
            if not npz or not event_id:
                continue
            # data_blocker rows reference unusable tapes (empty stubs,
            # prepare failures) — the IC pass reads raw tapes, so only
            # DATA-level blockers disqualify a unit. Semantic/pipeline
            # blockers gate execution, not tape validity.
            if str(row.get("blocker_code") or "").startswith("data_blocker:"):
                continue
            year = _event_year(event_id)
            if year not in DISCOVERY_YEARS + CONFIRMATION_YEARS:
                excluded.add(event_id)  # holdout seal (2023+) or pre-2018
                continue
            if npz not in units:
                units[npz] = {
                    "source_npz": npz,
                    "symbol": str(row.get("symbol") or ""),
                    "event_id": event_id,
                    "year": year,
                    "sensor_feature_npz": dict(row.get("sensor_feature_npz") or {}),
                }
            elif row.get("sensor_feature_npz"):
                units[npz]["sensor_feature_npz"].update(dict(row["sensor_feature_npz"]))
    return sorted(units.values(), key=lambda u: u["source_npz"]), sorted(excluded)


def _hypothesis_model_ids(horizon_map: Mapping[str, Any]) -> list[str]:
    from backtest_pipeline.src.hftbacktest_only_pipeline import _canonical_signal_adapter

    out = []
    for mid in sorted(horizon_map):
        try:
            kind, _adapter, _cls = _canonical_signal_adapter(mid)
        except Exception:
            continue
        if kind == "hypothesis":
            out.append(mid)
    return out


def _extract_signal_frame(
    unit: Mapping[str, Any], model_ids: list[str]
) -> "tuple[Any, float]":
    """ONE MarketStatePipeline pass evaluating ALL hypothesis adapters.

    Returns (frame, tick_size); frame columns: timestamp_ns, mid_price,
    spread_ticks, realized_vol_state, sig__<model_id>... Parity contract:
    sig__<m> must equal build_meta_training_set._signal_and_features(m,...)
    ["primary_signal"] — tested in tests/research_pipeline.
    """
    import pandas as pd

    from backtest_pipeline.src.hftbacktest_only_pipeline import _canonical_signal_adapter
    from backtest_pipeline.src.instrument_specs import resolve_instrument_spec
    from features_engine.src.features.npz_feed import iter_mbo_events, load_npz_events
    from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline

    symbol_root = str(unit["symbol"]).split(".")[0].upper()
    spec = resolve_instrument_spec(symbol_root)
    tick_size = float(spec.tick_size)

    adapters = []
    for mid in model_ids:
        _kind, adapter, _cls = _canonical_signal_adapter(mid)
        adapters.append((mid, adapter))

    sensor_adapter = None
    sensor_npz = (unit.get("sensor_feature_npz") or {}).get("VIX", "")
    if sensor_npz:
        from replay.sensor_feature_adapter import PrecomputedFeatureAdapter

        sensor_adapter = PrecomputedFeatureAdapter(sensor_npz)

    raw_events = load_npz_events(unit["source_npz"])
    pipeline = MarketStatePipeline(tick_size=tick_size, latency_ms=0.1)

    records: dict[str, list[float]] = {
        "timestamp_ns": [], "mid_price": [], "spread_ticks": [],
        "realized_vol_state": [],
    }
    for mid_id in model_ids:
        records[f"sig__{mid_id}"] = []

    for event in iter_mbo_events(raw_events):
        state = pipeline.process_event(event)
        if sensor_adapter is not None:
            sensor_adapter.sync_to_timestamp(int(event.timestamp_ns))
            sensor_features = sensor_adapter.current_features() or {}
            state.cross_asset_features["VIX"] = sensor_features
        feats = state.primary_features
        records["timestamp_ns"].append(int(event.timestamp_ns))
        records["mid_price"].append(float(feats.get("mid_price", float("nan"))))
        records["spread_ticks"].append(float(feats.get("spread", float("nan"))) / tick_size)
        records["realized_vol_state"].append(float(feats.get("realized_vol_state", float("nan"))))
        for mid_id, adapter in adapters:
            try:
                records[f"sig__{mid_id}"].append(float(adapter.evaluate(state)))
            except Exception:
                records[f"sig__{mid_id}"].append(float("nan"))

    return pd.DataFrame(records), tick_size


def _process_unit(task: tuple[dict[str, Any], list[str], Mapping[str, Any]]) -> dict[str, Any]:
    """Worker: one pipeline pass -> per-model per-event aggregates (tiny payload)."""
    unit, model_ids, hmap = task
    import numpy as np
    import pandas as pd

    from decision_engine.python.src.targets import build_labels_frame

    symbol_root = str(unit["symbol"]).split(".")[0].upper()
    try:
        raw_frame, tick_size = _extract_signal_frame(unit, model_ids)
    except Exception as exc:  # fail-soft per unit: receipt, never kill the run
        return {"event_id": unit["event_id"], "symbol": symbol_root,
                "year": unit["year"], "n_rows": 0,
                "skipped": f"tape_load_failed:{type(exc).__name__}",
                "models": {}}
    ts_list = raw_frame["timestamp_ns"].tolist()
    mids = raw_frame["mid_price"].tolist()
    spread_ticks_arr = raw_frame["spread_ticks"].to_numpy(dtype=np.float64)
    vols = raw_frame["realized_vol_state"].tolist()
    sigs = {mid_id: raw_frame[f"sig__{mid_id}"].tolist() for mid_id in model_ids}

    n = len(ts_list)
    if n < 50:
        return {"event_id": unit["event_id"], "symbol": symbol_root,
                "year": unit["year"], "n_rows": n, "skipped": "too_few_rows",
                "models": {}}

    frame = pd.DataFrame({"timestamp_ns": ts_list, "mid_price": mids})
    horizons = sorted({int(hmap[m]["horizon_ms"]) for m in model_ids} | set(EXPLORATORY_HORIZONS_MS))
    frame = build_labels_frame(frame, tick_size=tick_size, horizons_ms=horizons)

    spread_ticks = spread_ticks_arr
    sigma_step = float(np.nanstd(np.diff(np.asarray(mids, dtype=np.float64)) / tick_size))
    vol_med = float(np.nanmedian(np.asarray(vols, dtype=np.float64)))

    out_models: dict[str, Any] = {}
    for mid_id in model_ids:
        sig = np.asarray(sigs[mid_id], dtype=np.float64)
        threshold = float(hmap[mid_id]["threshold"] or 0.0)
        h_star = int(hmap[mid_id]["horizon_ms"])
        fired = np.abs(sig) > threshold
        n_fired = int(np.nansum(fired))
        entry: dict[str, Any] = {"n_fired": n_fired}
        if n_fired >= MIN_FIRED_ROWS_PER_EVENT:
            sign = np.sign(sig)
            per_h: dict[str, float] = {}
            for h in horizons:
                ret = frame[f"y_return_{h}ms"].to_numpy(dtype=np.float64)
                signed = np.where(fired, sign * ret, np.nan)
                per_h[str(h)] = float(np.nanmean(signed)) if np.isfinite(signed).any() else float("nan")
            entry["mean_signed_by_h"] = per_h
            ret_star = frame[f"y_return_{h_star}ms"].to_numpy(dtype=np.float64)
            censored = int(np.sum(fired & ~np.isfinite(ret_star)))
            entry["censoring_rate"] = censored / n_fired
            entry["mean_half_spread_ticks"] = float(np.nanmean(spread_ticks[fired]) / 2.0)
            ok = fired & np.isfinite(ret_star) & np.isfinite(sig)
            if int(ok.sum()) >= 10:
                s_r = pd.Series(sig[ok]).rank()
                r_r = pd.Series(ret_star[ok]).rank()
                entry["ic_h_star"] = float(s_r.corr(r_r))
        out_models[mid_id] = entry

    return {
        "event_id": unit["event_id"], "symbol": symbol_root, "year": unit["year"],
        "event_type": _event_type(unit["event_id"]), "month": _month_key(unit["event_id"]),
        "n_rows": n, "sigma_step_ticks": sigma_step, "vol_median": vol_med,
        "tick_size": tick_size, "models": out_models,
    }


def _envelope_trials_per_model(envelope_path: Path) -> dict[str, int]:
    if not envelope_path.is_file():
        return {}
    data = json.loads(envelope_path.read_text(encoding="utf-8"))
    sets_ = data if isinstance(data, list) else data.get("parameter_sets") or []
    counts: collections.Counter[str] = collections.Counter()
    for s in sets_:
        mid = str(s.get("canonical_model_id") or "")
        if mid:
            counts[mid] += 1
    return dict(counts)


def _hurdle_referenced_test(edges, half_spreads, pass_line, event_ids, months):
    """Clustered t of H0 "edge does not clear the cost hurdle" (one-sided).

    The pre-registered claim is E[move|signal] > hurdle
    (HYPOTHESIS_SPEC_TEMPLATE section 3), so inference runs on the per-event
    NET series ``edge_i - half_spread_i - pass_line``, not on raw edge vs
    zero — a zero-referenced test would let a significant-but-below-hurdle
    model pass on its point estimate alone (Greptile P1, PR #75 round 5).
    Returns (t_stat, p_one_sided, dof); alternative is strictly net > 0.
    """
    import numpy as np

    from research_pipeline.ic_stats import clustered_t_two_way

    net = (
        np.asarray(edges, dtype=np.float64)
        - np.asarray(half_spreads, dtype=np.float64)
        - float(pass_line)
    )
    t_stat, p_two, dof = clustered_t_two_way(net, event_ids, months)
    if not np.isfinite(t_stat):
        return t_stat, p_two, dof
    p_one = p_two / 2.0 if t_stat > 0 else 1.0 - p_two / 2.0
    return t_stat, p_one, dof


def _bh_over_primary_family(model_ids, report_models, *, q: float) -> list[bool]:
    """BH mask over the FULL pre-registered primary family.

    No-verdict models (insufficient_events, leader-feature gaps — no
    ``p_raw``) enter as NaN so ``bh_reject`` counts them in the denominator
    as p=1.0 non-rejections. Filtering them out before the call would shrink
    the family and weaken the multiple-testing penalty for the survivors
    (Greptile P1, PR #75 round 4 — the driver previously rebuilt the family
    from only models that produced a p-value).
    """
    from research_pipeline.ic_stats import bh_reject

    family_p = [report_models[m].get("p_raw", float("nan")) for m in model_ids]
    return bh_reject(family_p, q=q)


def _sharpe_and_dsr(edges, n_trials: int) -> tuple[float, float]:
    """Per-event Sharpe of the edge series + Bailey-Lopez de Prado deflation.

    deflated_sharpe_ratio takes the OBSERVED SHARPE (float) plus n_obs and
    n_trials keywords (Greptile P1 on PR #75 — the previous call passed the
    raw edge list positionally and crashed at the MIN_EVENTS branch).
    """
    from research_pipeline.statistics import deflated_sharpe_ratio, sharpe_ratio

    values = [float(v) for v in edges]
    if len(values) < 2:
        return float("nan"), float("nan")
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    if var < 1e-18:
        # (Near-)constant edges: no Sharpe is defined. Exact zero variance
        # raises inside sharpe_ratio; float-noise variance yields astronomic
        # Sharpe and DSR=1.0 (a garbage auto-pass). Both degrade to NaN.
        return float("nan"), float("nan")
    try:
        sr = sharpe_ratio(values)
    except ValueError:
        return float("nan"), float("nan")
    if sr != sr:
        return float("nan"), float("nan")
    dsr = deflated_sharpe_ratio(sr, n_obs=len(values), n_trials=max(1, int(n_trials)))
    return sr, dsr


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="IC diagnostic (PR-1 gate)")
    parser.add_argument("--campaign-manifest", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--horizon-map", type=Path,
                        default=REPO / "docs" / "hypotheses" / "HORIZON_MAP_PREREGISTERED.json")
    parser.add_argument("--envelope", type=Path,
                        default=REPO / "runtime" / "stagec1" / "envelope_rt_tox_ab.json")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--max-tapes", type=int, default=0, help="0 = all (smoke runs cap this)")
    args = parser.parse_args(argv)

    import numpy as np
    import pandas as pd

    from backtest_pipeline.src.fee_model import FeeModel
    from backtest_pipeline.src.instrument_specs import resolve_instrument_spec
    from research_pipeline.ic_stats import clustered_t_two_way, hurdle_ticks
    from research_pipeline.statistics import deflated_sharpe_ratio, sharpe_ratio

    map_blob = _require_committed_horizon_map(args.horizon_map)
    hmap = json.loads(args.horizon_map.read_text(encoding="utf-8"))["models"]

    units, holdout_excluded = _load_units(args.campaign_manifest)
    if args.max_tapes:
        units = units[: args.max_tapes]
    model_ids = _hypothesis_model_ids(hmap)
    if not units or not model_ids:
        raise SystemExit(f"nothing_to_do:units={len(units)}:models={len(model_ids)}")

    tasks = [(u, model_ids, hmap) for u in units]
    results: list[dict[str, Any]] = []
    if args.workers <= 1:
        for t in tasks:
            results.append(_process_unit(t))
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
            for res in pool.map(_process_unit, tasks, chunksize=4):
                results.append(res)

    trials = _envelope_trials_per_model(args.envelope)

    # ---- reduce: per-model event tables -> verdicts -------------------------
    kill_list: dict[str, dict[str, Any]] = {}
    report_models: dict[str, Any] = {}
    for mid_id in model_ids:
        h_star = int(hmap[mid_id]["horizon_ms"])
        threshold = float(hmap[mid_id]["threshold"] or 0.0)
        rows = []
        for r in results:
            entry = (r.get("models") or {}).get(mid_id) or {}
            if "mean_signed_by_h" not in entry:
                continue
            rows.append({
                "event_id": r["event_id"], "month": r["month"], "year": r["year"],
                "event_type": r["event_type"], "symbol": r["symbol"],
                "edge": entry["mean_signed_by_h"].get(str(h_star), float("nan")),
                "by_h": entry["mean_signed_by_h"],
                "half_spread": entry.get("mean_half_spread_ticks", float("nan")),
                "censoring": entry.get("censoring_rate", 1.0),
                "ic": entry.get("ic_h_star", float("nan")),
                "sigma_step": r["sigma_step_ticks"], "vol_median": r["vol_median"],
                "tick_size": r["tick_size"],
            })
        table = pd.DataFrame(rows)
        censor_excluded = 0
        if len(table):
            keep = table["censoring"] <= MAX_CENSORING
            censor_excluded = int((~keep).sum())
            table = table[keep]
        conf = table[table["year"].isin(CONFIRMATION_YEARS)] if len(table) else table
        disc = table[table["year"].isin(DISCOVERY_YEARS)] if len(table) else table

        entry: dict[str, Any] = {
            "h_star_ms": h_star, "threshold": threshold,
            "n_events": int(len(conf)), "n_events_censor_excluded": censor_excluded,
        }
        # hurdle from the modal traded symbol among contributing events
        if len(conf):
            sym = conf["symbol"].mode().iloc[0]
            spec = resolve_instrument_spec(sym)
            fee = FeeModel(product=sym).get_fee_per_contract()
            hurdle = hurdle_ticks(fee_per_side=fee, contract_multiplier=spec.contract_multiplier,
                                  tick_size=spec.tick_size, slippage_ticks=0.0)
            entry["hurdle_fee_ticks"] = round(hurdle["fee_ticks"], 4)
        else:
            entry["hurdle_fee_ticks"] = float("nan")

        # The inference floor counts events with a finite NET term (finite
        # edge AND finite half-spread): clustered_t_two_way silently drops
        # non-finite rows, so gating on edge alone could pass MIN_EVENTS
        # while the effective test N falls below it.
        edges = conf["edge"].to_numpy(dtype=np.float64) if len(conf) else np.array([])
        half_spreads = (
            conf["half_spread"].to_numpy(dtype=np.float64) if len(conf) else np.array([])
        )
        n_inference = int((np.isfinite(edges) & np.isfinite(half_spreads)).sum())
        if len(conf) >= MIN_EVENTS and n_inference >= MIN_EVENTS:
            edge = float(np.nanmean(edges))
            half_spread = float(np.nanmean(half_spreads))
            spread_adj = edge - half_spread
            pass_line = entry["hurdle_fee_ticks"] + RESIDUAL_SLIPPAGE_TICKS
            t_stat, p_raw, dof = _hurdle_referenced_test(
                edges, half_spreads, pass_line, conf["event_id"], conf["month"]
            )
            entry["n_events_inference"] = n_inference
            finite_edges = edges[np.isfinite(edges)]
            sr, dsr = _sharpe_and_dsr(finite_edges, max(1, trials.get(mid_id, 1)))
            entry.update({
                "edge_ticks": round(edge, 4),
                "spread_adjusted_edge_ticks": round(spread_adj, 4),
                "pass_line_ticks": round(pass_line, 4),
                "t_stat": round(t_stat, 3), "p_raw": p_raw, "dof": dof,
                "sharpe_per_event": round(sr, 4) if np.isfinite(sr) else None,
                "dsr": round(dsr, 4) if np.isfinite(dsr) else None,
            })
        else:
            entry["verdict_reason"] = (
                "no_verdict_leader_features_absent"
                if not len(table) and hmap[mid_id].get("horizon_ms") and mid_id.endswith(("LEAD_LAG", "SNAPBACK", "MACRO_IMPULSE", "RETAIL_LAG"))
                else "insufficient_events"
            )

        # Discovery extras: PR-3 barrier k + frozen vol terciles
        if len(disc):
            with np.errstate(invalid="ignore", divide="ignore"):
                k_vals = np.abs(disc["edge"].to_numpy(dtype=np.float64)) / (
                    disc["sigma_step"].to_numpy(dtype=np.float64)
                    * np.sqrt(max(1.0, h_star / 1000.0))
                )
            k_vals = k_vals[np.isfinite(k_vals)]
            entry["sigma_k_median"] = round(float(np.median(k_vals)), 4) if len(k_vals) else None
            vols = disc["vol_median"].to_numpy(dtype=np.float64)
            vols = vols[np.isfinite(vols)]
            if len(vols) >= 9:
                entry["vol_terciles_frozen"] = [
                    round(float(np.quantile(vols, 1 / 3)), 6),
                    round(float(np.quantile(vols, 2 / 3)), 6),
                ]
        report_models[mid_id] = entry

    mask = _bh_over_primary_family(model_ids, report_models, q=BH_Q_PRIMARY)
    for m, rejected_null in zip(model_ids, mask):
        if "p_raw" in report_models[m]:
            report_models[m]["bh_pass"] = bool(rejected_null)

    from backtest_pipeline.src.model_execution_contracts import model_execution_contract
    for m in model_ids:
        e = report_models[m]
        try:
            role = model_execution_contract(m).execution_role
        except Exception:
            role = "unknown"
        alpha_class = "momentum" if "primary_alpha" in role else role
        verdict = "no_verdict"
        if "p_raw" in e:
            _edge = e.get("spread_adjusted_edge_ticks")
            _edge_val = float(_edge) if _edge is not None else float("-inf")
            passed = bool(e.get("bh_pass")) and _edge_val > float(e["pass_line_ticks"])
            verdict = "pass" if passed else "fail"
        elif e.get("verdict_reason"):
            verdict = e["verdict_reason"]
        kill_entry = {
            "verdict": verdict,
            "h_star_ms": e["h_star_ms"],
            "threshold": e["threshold"],
            "edge_ticks": e.get("edge_ticks"),
            "spread_adjusted_edge_ticks": e.get("spread_adjusted_edge_ticks"),
            "hurdle_fee_ticks": e.get("hurdle_fee_ticks"),
            "pass_line_ticks": e.get("pass_line_ticks"),
            "p_raw": e.get("p_raw"),
            "bh_pass": e.get("bh_pass"),
            "dsr": e.get("dsr"),
            "n_events": e["n_events"],
            "n_events_censor_excluded": e["n_events_censor_excluded"],
            "sigma_k_median": e.get("sigma_k_median"),
            "alpha_class": alpha_class,
        }
        illegal = set(kill_entry) - KILL_LIST_ALLOWED_FIELDS
        if illegal:
            raise SystemExit(f"kill_list_schema_violation:{sorted(illegal)}")
        kill_list[m] = kill_entry

    # exploratory grid (report only, never in kill_list)
    exploratory: dict[str, Any] = {"label": "exploratory", "note":
                                   "never used for promotion; BH q=0.05 within-grid applies downstream"}
    for m in model_ids:
        rows = []
        for r in results:
            entry = (r.get("models") or {}).get(m) or {}
            if "mean_signed_by_h" in entry and r["year"] in CONFIRMATION_YEARS:
                rows.append({"event_type": r["event_type"], "by_h": entry["mean_signed_by_h"]})
        agg: dict[str, dict[str, list[float]]] = collections.defaultdict(lambda: collections.defaultdict(list))
        for row in rows:
            for h, v in row["by_h"].items():
                if math.isfinite(v):  # excludes NaN AND inf
                    agg[row["event_type"]][h].append(v)
        exploratory[m] = {
            etype: {h: round(pystats.fmean(vs), 4) for h, vs in hs.items() if vs}
            for etype, hs in agg.items()
        }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "campaign_manifest": str(args.campaign_manifest),
        "horizon_map_blob": map_blob,
        "split": {"discovery": DISCOVERY_YEARS, "confirmation": CONFIRMATION_YEARS,
                  "holdout_sealed": "2023+"},
        "holdout_excluded_events": holdout_excluded,
        "units_processed": len(units),
        # No silent caps: every unit that produced no aggregates is itemized.
        "units_skipped": collections.Counter(
            str(r["skipped"]) for r in results if r.get("skipped")
        ),
        "models": report_models,
        "bh_q_primary": BH_Q_PRIMARY,
        "min_events": MIN_EVENTS,
        "max_censoring": MAX_CENSORING,
        "residual_slippage_ticks": RESIDUAL_SLIPPAGE_TICKS,
        "exploratory": exploratory,
    }
    (args.out_dir / "ic_report.json").write_text(
        json.dumps(receipt, indent=1, sort_keys=True, default=str), encoding="utf-8")
    (args.out_dir / "kill_list.json").write_text(
        json.dumps({"schema_version": KILL_LIST_SCHEMA, "horizon_map_blob": map_blob,
                    "models": kill_list}, indent=1, sort_keys=True, default=str),
        encoding="utf-8")

    summary = {
        "units": len(units), "holdout_excluded": len(holdout_excluded),
        "models": len(model_ids),
        "pass": sorted(m for m, v in kill_list.items() if v["verdict"] == "pass"),
        "fail": sum(1 for v in kill_list.values() if v["verdict"] == "fail"),
        "no_verdict": sorted(m for m, v in kill_list.items()
                             if v["verdict"] not in ("pass", "fail")),
    }
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
