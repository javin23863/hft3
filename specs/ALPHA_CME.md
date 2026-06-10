# ALPHA_CME.md — CME Live-Alpha Campaign Milestones

Version: 2026-06-10.

**TIME-BOUND CAMPAIGN DOCUMENT — NOT A DURABLE CONTRACT.**
This document expires when M10 (live arm) is reached and superseded by the
post-launch operations record. Milestone definitions, scope, and kill criteria
may be revised only by an explicit versioned edit to this file before M10.

---

## 1. Scope

This document tracks the engineering, measurement, and research milestones
required to arm a live CME MES/ES strategy on CHI404 (Chicago colo, Rithmic
R|API+). Governing specs are cited per row; this document adds no new behavioral
contracts — it references and sequences existing ones.

---

## 2. Milestone Register

| ID | Milestone | Machine | Governing spec | Status | Depends |
|----|-----------|---------|---------------|--------|---------|
| M0 | **pybind 64-slot parity** — Python ↔ C++ `hft3_features_cpp` parity on real lake NPZ across all 64 slots. DONE for slots 0–40, 50–63. Regime slots 41–49 differ because the Python pipeline calls `RegimeFilter` outside the extractor while C++ integrates it; pipeline-level integration is in flight. | Laptop | CORRECTNESS.md §2 row 3 | **DONE (partial — regime integration in flight)** | — |
| M1 | **`decision_runtime` hardening** — `evaluate_actions` writes all 10 slots every call; slots 3–9 receive `NEG_INFINITY_SENTINEL`; header validation tests pass; CORRECTNESS.md §3 defect (a) cleared. | Laptop | CHI404_RUNTIME.md §4; CORRECTNESS.md §3 defect a | **DONE** | — |
| M2 | **C-lane CI** — sanitizer suite (ASan/UBSan/TSan), failure injection tests, and submission gate tests pass on every C++ commit. CORRECTNESS.md §3 defects (c) and (d) cleared. | Laptop | CORRECTNESS.md §2 rows 1-2, 6-7 | **DONE** | M1 |
| M3 | **`hft3_engine` consumer loop, REPLAY-mode-first** — the fused hot loop (CHI404_RUNTIME.md §3) processes NPZ-derived event streams end-to-end in REPLAY mode; decision log produced. Gate: determinism check on lake NPZ (row 4 of CORRECTNESS.md §2). | Laptop → CHI404 | CHI404_RUNTIME.md §3; CORRECTNESS.md §2 row 4 | **DONE** (`hft3_engine` REPLAY mode: determinism gate byte-identical decision logs on real lake NPZ, ~45k events/s; live instantiation at M8). | M0, M1, M2 |
| M4 | **Real waterfall timestamps** — `PaperLatencyDaemon` records real monotonic callback timestamps; `shadow_synthetic: true` probe eliminated from authoritative summaries; CORRECTNESS.md §3 defect (b) cleared. | CHI404 | CORRECTNESS.md §2 row 10; LATENCY.md §9 | **OPEN** | — |
| M5 | **≥1000-paired-sample order-ack campaign** — `paper_order_latency.measured = true` in `runtime/latency_reports/latency_summary.json`; `order_ack_p99_ms` populated from ≥1000 paired submit→ack samples. Unblocks resolver rung 2 (LATENCY.md §4). Gate: `measured = true`. | CHI404 | LATENCY.md §9; LATENCY.md §4 resolution rung 2 | **OPEN** | M4 |
| M6 | **Universe sweep at measured p99** — walk-forward sweep across all symbols and event windows at the M5-measured order-ack p99; Holm correction applied across survivors. Gate: survivors list non-empty. | Laptop | PIPELINE.md §5; LATENCY.md §8 | **OPEN** | M5 |
| M7 | **First survivor T2 → T4 → GREEN stamp → bundle** — one surviving hypothesis completes T2 full certification, T4 champion gate, receives GREEN `promotion_eligible` stamp, and is packed into a deployment bundle (DEPLOYMENT.md §1). | Laptop | DEPLOYMENT.md §1-3; vault `Backtester Certification.md` T2/T4 | **OPEN** | M6 |
| M8 | **Deployment automation + deploy to CHI404** — bundle transferred, startup validation passes (DEPLOYMENT.md §3), `current` symlink set. CORRECTNESS.md §3 defects (e) and (f) cleared. | Both | DEPLOYMENT.md §1-3; CORRECTNESS.md §3 defects e, f | **OPEN** | M3, M7 |
| M9 | **Paper-shadow SIM over 2026-01-01 → 2026-06-10** — deployed bundle replayed through all 2026 event windows in REPLAY/PAPER mode at measured p99 (DEPLOYMENT.md §4). Gate: positive net expectancy on 2026 window; zero code-attributable safety halts; determinism spot-check passes. 2026 NPZ coverage required. | CHI404 | CHI404_RUNTIME.md §10; DEPLOYMENT.md §4 | **OPEN** | M8, 2026 NPZ coverage |
| M10 | **Live arm** — pre-arm checklist complete, kill-switch drill passed, ARM entry appended to audit log, `hft3_engine` started in LIVE mode. Defect ledger MUST be empty (CORRECTNESS.md §3; DEPLOYMENT.md §5). | CHI404 | DEPLOYMENT.md §5; CORRECTNESS.md §3 | **OPEN** | M9, empty defect ledger |

**M2 caveat**: `scripts/run_c_lane.ps1` ALL CHECKS PASSED on workstation; sanitizer full lane `scripts/run_c_lane.sh` pending first CHI404 run.

---

## 3. Parallel-Chain Execution

Two chains may run concurrently; they join at M8:

```
Engineering chain:   M0 → M1 → M2 → M3 ──────────────────────────────┐
                                                                       ↓
                                                                       M8 → M9 → M10
                                                                       ↑
Measurement chain:   M4 → M5 → Research chain: M6 → M7 ──────────────┘
```

M4 and M5 (waterfall + order-ack measurement) are **independent of M1–M3** and
must start immediately — measurement requires CHI404 connection time that cannot
be recovered. While engineering works on M1–M3 on the laptop, M4/M5 run on
CHI404. Research (M6–M7) begins as soon as M5 delivers a measured p99.

---

## 4. 2026 Data Embargo (Binding)

**Research sweeps, fitting, selection, and hyperparameter search must NEVER read
2026-01-01 → 2026-06-10 data. Promotion happens blind to 2026. The first touch
of any 2026 market data is the deployed bundle running in REPLAY or PAPER mode
on CHI404 (M9).**

This rule is verbatim from DEPLOYMENT.md §4.2 and is repeated here for
campaign clarity. Violation consequences:

- Shadow results for the affected bundle are **invalid**.
- A mandatory `DEFECT` entry must be appended to
  `runtime/validation/defect_ledger.jsonl` (append-only).
- M9 cannot pass for that bundle; a new bundle without the leak must be
  promoted.

Enforcement: the walk-forward period table is fixed (Discovery 2018–2020,
Confirmation 2021–2022, Holdout 2023–2024, Recent holdout 2025). Any sweep
runner that reads NPZ dated ≥ 2026-01-01 during M6–M7 constitutes a violation.

---

## 5. Kill Criteria

Any of the following conditions halts the campaign at the current milestone and
requires root-cause resolution before proceeding:

1. **Net expectancy ≤ 0** in any walk-forward OOS period during M6–M7 sweep.
   This is the primary kill gate: a hypothesis that cannot clear positive
   expectancy in any OOS window is not viable.
2. **Determinism failure** during M3 or M9: two REPLAY runs on identical inputs
   produce non-identical decision logs.
3. **Defect ledger non-empty** at M10: any OPEN item in CORRECTNESS.md §3
   blocks live arm.
4. **2026 embargo violation** during M6–M7: shadow results invalid; campaign
   stalls at M7 until a clean bundle is produced.
5. **Shadow acceptance criteria failure** at M9 (DEPLOYMENT.md §4.4): negative
   expectancy, code-attributable safety halts, or fill/slippage out of
   walk-forward envelope.
