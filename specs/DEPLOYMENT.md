# DEPLOYMENT.md — Promotion Stamp → Live Sequence

Version: 2026-06-10. Authoritative for CHI404 bare-metal (Chicago colo, CME via Rithmic R|API+).
This document governs the chain from a GREEN certification stamp to an armed live session.
Research pipeline (ingest through stamp) is covered by PIPELINE.md; runtime behavior
of `hft3_engine` is covered by CHI404_RUNTIME.md.

---

## 1. Artifact Bundle

A bundle is the atomic unit of deployment. Nothing deploys without a complete, validated bundle.

### 1.1 Bundle Contents

| File | Description | Required |
|------|-------------|---------|
| `weights.bin` | C++ weights binary (magic `0x48465433`, 16-byte header + 1024 doubles; format per `packages/decision_engine/cpp/include/decision_runtime.hpp` `ModelHeader` and `packages/decision_engine/python/src/walk_forward.py` `export_weights_to_cpp()`) | Yes |
| `certification_stamp.json` | Stamp dict from `packages/hft3/validation/research_stamp.py` `build_certification_stamp()` | Yes |
| `manifest.json` | See §1.2 | Yes |

### 1.2 Manifest Schema

```json
{
  "schema_version": 1,
  "model_id": <uint32, matches weights header>,
  "run_id": "<walk-forward run_id from WalkForwardValidator>",
  "source_commit": "<git SHA of repo at promotion time>",
  "latency_ms_at_promotion": <float, value used in promotion replay>,
  "files": {
    "weights.bin":              "<sha256hex>",
    "certification_stamp.json": "<sha256hex>"
  },
  "promoted_at": "<ISO-8601 UTC>",
  "promoted_by": "<operator handle>"
}
```

### 1.3 Bundle Construction

Built **laptop-side** by one script from a GREEN, non-stale, `promotion_eligible=true`
certification stamp (source: `packages/hft3/validation/research_stamp.py`
`_resolve_promotion_label()` — label must be `PROMOTION_ELIGIBLE_FROM_BACKTESTER_SIDE`).

Pre-conditions the script enforces before writing the bundle:
- `stamp["status"] == "GREEN"`
- `stamp["promotion_eligible"] == true`
- `stamp["stale"] == false`
- `stamp["promotion_label"] == "PROMOTION_ELIGIBLE_FROM_BACKTESTER_SIDE"`
- Walk-forward OOS kill-gate passes on all four periods (Discovery 2018–2020,
  Confirmation 2021–2022, Holdout 2023–2024, Recent holdout 2025;
  per PIPELINE.md §5 and vault `Backtester Certification.md` T4 tier)
- SHA-256 of `weights.bin` computed and written to manifest
- `latency_ms_at_promotion` must be a measured value or an explicit `--latency-ms`
  argument; the UNMEASURED default is not permitted for promotion
  (cite: LATENCY.md §4, resolution step 4 raises ValueError when unmeasured)

Operator must not hand-edit any bundle file. Bundle is treated as immutable after construction.

---

## 2. Transfer and Versioning on CHI404

2.1 **Directory structure on CHI404:**
    ```
    /root/hft3/releases/
      <run_id>/
        weights.bin
        certification_stamp.json
        manifest.json
      current -> <run_id>/   (symlink)
    ```

2.2 Each deployment creates a new `releases/<run_id>/` directory. The `current` symlink
    is the atomic switch: updated only after §3 startup validation passes on the new bundle.

2.3 The previous bundle's directory is **retained** (never deleted automatically).
    Retention enables one-command rollback (§6) without re-transfer.

2.4 Transfer: `rsync` or `scp` from laptop to CHI404 staging directory; then move into
    `releases/<run_id>/` in one atomic `mv`. Never write directly into `releases/current/`.

---

## 3. Startup Validation on Box

Executed by `hft3_engine` at startup (CHI404_RUNTIME.md §8 steps 3–4) and independently
by the deployment script before updating the `current` symlink.

| Check | Mechanism | Failure behavior |
|-------|-----------|-----------------|
| Re-hash `weights.bin` | SHA-256 vs `manifest.json files.weights.bin` | Refuse deploy; do not update symlink |
| Re-hash `certification_stamp.json` | SHA-256 vs manifest | Refuse deploy |
| Header validation | Read 16-byte header; assert `magic == 0x48465433`; assert `feature_count ≤ 64` | Refuse deploy |
| Stamp eligibility re-check | Assert `promotion_eligible == true`, `status == "GREEN"`, `stale == false` | Refuse deploy |
| Model ID consistency | `manifest.model_id == header.model_id` | Refuse deploy |
| Latency value present | `manifest.latency_ms_at_promotion > 0` | Refuse deploy |

Any mismatch produces a human-readable error, appends a REJECTED entry to the audit
log (§7), and exits non-zero. The `current` symlink is never updated on failure.

---

## 4. Paper-Shadow SIM Protocol (Binding)

This section is a **binding contract**. Deviation from the embargo rule constitutes a
research integrity defect and invalidates shadow results.

### 4.1 Shadow Window

The 2026-01-01 → 2026-06-10 period is reserved **exclusively** for shadow evaluation
of deployed bundles. It lies entirely beyond all walk-forward periods:

| Period | Dates | Role |
|--------|-------|------|
| Discovery | 2018–2020 | Train (internal OOS gate at last 33%) |
| Confirmation | 2021–2022 | OOS gate 1 |
| Holdout | 2023–2024 | OOS gate 2 |
| Recent holdout | 2025 | OOS gate 3 (source: PIPELINE.md §5) |
| **Shadow** | **2026-01-01 → 2026-06-10** | **Deployed bundle only; no fitting** |

### 4.2 Embargo Rule

Research sweeps, fitting, selection, and hyperparameter search must **never read 2026
data**. Promotion happens blind to 2026. The first touch of any 2026 market data is
the deployed bundle running in REPLAY or PAPER mode on CHI404.

Violation = shadow invalid + mandatory entry in defect ledger
(`runtime/validation/defect_ledger.jsonl`, append-only).

### 4.3 Shadow Execution

The deployed bundle is replayed through ALL event windows in the 2026-01-01 →
2026-06-10 shadow period using `hft3_engine` in REPLAY or PAPER mode at the measured
order-ack p99 (or `latency_ms_at_promotion` from the bundle manifest when p99
is not yet measured; see LATENCY.md §4).

Engine runs the **same hot loop** as live (CHI404_RUNTIME.md §3 loop contract).
Safety monitors run in audit-only mode in REPLAY (PIPELINE.md §8).

### 4.4 Acceptance Criteria

All four conditions must hold before proceeding to §5 live arm:

| Criterion | Pass condition |
|-----------|---------------|
| Net expectancy | Positive net expectancy on the 2026 shadow window (net of fills, slippage, costs) |
| Safety halts | Zero code-attributable safety halts (safety halts caused by infrastructure faults are investigated separately and do not auto-pass) |
| Determinism | Spot-check: re-run at least one event window with identical inputs; decision log must be byte-identical to first run |
| Fill / slippage | Realized fill rate and slippage within envelope predicted by replay (tolerance: ±2σ of distribution from walk-forward periods) |

Failure of any criterion: document in defect ledger; do not arm live; diagnose before
re-running shadow.

---

## 5. Live Arm

Live arm is a **deliberate manual step**. It is never a default or an automated
consequence of shadow passing.

### 5.1 Pre-Arm Checklist

| Item | Action | Source |
|------|--------|--------|
| LIVE_* env contract | Set `LIVE_MAX_ORDER_SIZE`, `LIVE_DAILY_LOSS_LIMIT`, `LIVE_KILL_SWITCH`, `LIVE_RISK_ENABLED` in CHI404 env | `packages/execution/safety.py` `assert_live_config()` |
| Kill-switch fire drill | Under live connection (paper session), send kill-switch signal; verify `hft3_engine` halts and cancel-all is submitted within 1 s | CHI404_RUNTIME.md §9 shutdown sequence |
| Minimum size | Confirm `LIVE_MAX_ORDER_SIZE` ≥ 1 contract (minimum viable) | Operator judgment |
| Daily loss limit | Confirm `LIVE_DAILY_LOSS_LIMIT` is set to a value consistent with account risk policy | `risk_engine/include/risk_manager.hpp` `RiskLimits.daily_loss_limit` |
| All pre-arm items passed | Append ARM event to audit log (§7) with operator handle and timestamp | Binding |

### 5.2 Arm Procedure

1. Verify `current` symlink points to the bundle that passed §4 shadow.
2. Set LIVE_* env vars on CHI404.
3. Run kill-switch fire drill (item above).
4. Append ARM entry to audit log.
5. Start `hft3_engine` in LIVE mode.
6. Monitor for first N events; confirm no safety halts, position tracked correctly.

---

## 6. Rollback

One-command rollback to the previous bundle.

### 6.1 Rollback Procedure

```bash
# 1. Halt the running engine (sends SIGTERM; engine follows CHI404_RUNTIME.md §9)
systemctl stop hft3-engine.service

# 2. Identify previous bundle
ls /root/hft3/releases/   # list versioned directories

# 3. Switch symlink atomically
ln -sfn /root/hft3/releases/<previous_run_id> /root/hft3/releases/current

# 4. Re-run startup validation on previous bundle (§3 checks)
hft3_engine --validate-only

# 5. Restart
systemctl start hft3-engine.service
```

### 6.2 Rollback Constraints

- Rollback is only valid if the previous bundle passes all §3 startup validation checks
  after symlink switch.
- Append ROLLBACK entry to audit log (§7) with operator handle, from_run_id,
  to_run_id, timestamp, and reason.
- Log files from the aborted session are retained; never deleted.

### 6.3 Log Retention

All session logs (`/root/hft3/logs/`), decision logs, and SPSC ring drain outputs
are retained indefinitely. Disk space policy: operator responsibility; rotate compressed
archives to off-box storage before disk exceeds 80% utilization.

---

## 7. Audit Log

Every deployment event is appended to a JSONL hash-chain file at
`runtime/validation/deployment_audit.jsonl`, mirroring the certification registry
style (`packages/hft3/validation/certification_registry.py`
`CertificationRecord`; SHA-256 hash chain, append-only).

### 7.1 Event Types

| Event type | When | Required fields |
|-----------|------|-----------------|
| `BUNDLE_BUILT` | Bundle constructed laptop-side | `run_id`, `model_id`, `source_commit`, `latency_ms_at_promotion`, `sha256_weights`, `sha256_stamp`, `promoted_by`, `ts` |
| `TRANSFER_COMPLETE` | `rsync`/`scp` complete | `run_id`, `destination`, `ts` |
| `VALIDATION_PASS` | §3 all checks pass | `run_id`, `ts` |
| `VALIDATION_REJECTED` | §3 any check fails | `run_id`, `failure_reason`, `ts` |
| `SHADOW_START` | §4 shadow run begins | `run_id`, `shadow_window_start`, `shadow_window_end`, `ts` |
| `SHADOW_PASS` | §4 all criteria pass | `run_id`, `net_expectancy`, `halts`, `determinism_pass`, `fill_slippage_pass`, `ts` |
| `SHADOW_FAIL` | §4 any criterion fails | `run_id`, `failed_criteria`, `defect_ledger_entry_id`, `ts` |
| `ARM` | §5 live arm | `run_id`, `operator`, `live_max_order_size`, `live_daily_loss_limit`, `kill_switch_drill_passed`, `ts` |
| `ROLLBACK` | §6 rollback | `from_run_id`, `to_run_id`, `operator`, `reason`, `ts` |
| `DEFECT` | Any integrity violation | `run_id`, `defect_type`, `description`, `ts` |

### 7.2 Hash Chain

Each record includes `prev_hash: sha256(previous_record_bytes)` (empty string for
the first record). Records must be appended atomically (file lock held during write).
The chain must be verifiable offline; tampering detection is the chain's purpose.
