# Fable mindset — load this first (every agent session)

**Permanent state of being.** Read this note before any task, search, script, or edit.  
Canonical repo: `C:\Users\MSI\repos\hft3`. Full Fable reference: `C:\Users\MSI\.codex\skills\fable-mindset\references\Fable_Mindset_public.md`.

---

## 1. Fable loop (always)

1. **Ground** — read real artifacts, logs, and vault notes; never assume from memory.
2. **Reason** — name the clock, metric, and authority before acting.
3. **Act** — deliberate batches; no mass-blast on live accounts.
4. **Observe** — capture what changed (counts, units, paths).
5. **Re-evaluate** — if units or authority were wrong, stop and fix before continuing.
6. **Read exact regions** — before edits, read the file lines you will touch.
7. **Verify** — real checks (probe output, file counts, pytest), not narrative.
8. **Recover** — diagnose from evidence; retry with a corrected model.
9. **Report truthfully** — separate measured vs inferred vs blocked.

---

## 2. When the operator says **“latency test”**

They mean the **CHI404 native C++ placement probe** with **offensive and defensive clocks in microseconds (`_us`)** — **not** a generic order-ack sweep in milliseconds.

| Operator phrase | Means | Does **not** mean |
|-----------------|-------|-------------------|
| **latency test** | `rithmic_latency_probe` → read **`tick_to_send_us`** (offensive) and **`cancel_to_send_us`** / **`cancel_to_ack_us`** (defensive) from summary JSON | `chi404_run_*_latency_sweep.sh` p99 **ms** ack campaign alone |
| **offensive latency** | Market event → SDK send: `tick_to_decision_us`, `tick_to_send_trigger_us`, **`tick_to_send_us`**, `rithmic_send_call_us` | Engine loop ns from `latency_truth.json` (different clock) |
| **defensive latency** | Cancel/replace path: **`cancel_to_send_us`**, `cancel_to_ack_us`, replace_* | Risk/defensive **models** in workbench (different subsystem) |
| **ack / wire / backtest latency** | Round trip: **`send_to_ack_us`** / **`new_send_to_ack_us`** (ms distribution for replay) | Placement speed — never substitute for `tick_to_send_us` |
| **HftBacktest backtest latency** | Three components: **feed**, **order-entry**, **order-response**; regimes Fast/Normal/Stress/Extreme; prefer `IntpOrderLatency` samples | Single scalar `constant_order_latency(ms, ms)` or collapsed `live_order_ack_p99_ms` |

**Primary KPI (placement speed):** `tick_to_send_us` in **µs**.  
**Backtest ontology:** [HFTBACKTEST_LATENCY_ONTOLOGY.md](HFTBACKTEST_LATENCY_ONTOLOGY.md) — load before any realism or HftBacktest replay work.  
**Spec:** [docs/LATENCY_BASELINE.md](../LATENCY_BASELINE.md) · **Runbook:** [CHI404_CANONICAL_ENTRYPOINTS.md](CHI404_CANONICAL_ENTRYPOINTS.md) · **Paper baseline:** `reports/latency_baselines/current_baseline.json` · **Live baseline (R01 Chicago, 2026-06-18):** `reports/latency_baselines/live_r01_chicago_baseline.json` · **Live capability:** `runtime/latency_reports/live_placement_capability.json`.

**Authority path:** R|API+ C++ adapter (in-process) → `rithmic_latency_probe` → `data/latency_baselines/…/*.jsonl` + `reports/latency_baselines/<run_id>_summary.json` (`hot_path_language=c++`, `wrapper=none`).

**Report sections to read after a run:**

- **Offensive placement speed** — `tick_to_send_us`, `decision_to_send_us`, …
- **Defensive actions** — `cancel_to_send_us`, `cancel_to_ack_us`, …
- **Round trip acknowledgment** — `send_to_ack_us` (separate; for backtest/replay band only)

---

## 3. Run shape (latency test on CHI404)

```bash
cd /root/hft3/repo
cmake --build build --target rithmic_latency_probe --config Release
set -a; . /root/hft3/.env; set +a

# Offensive + defensive in one probe (MD-primed, cancel after ack)
export RITHMIC_PROBE_SKIP_MD=0
export RITHMIC_PROBE_CANCEL_AFTER_ACK=1
export RITHMIC_PROBE_ORDER_COUNT=30          # live: keep ≤25 unless operator approves
export RITHMIC_PROBE_ORDER_INTERVAL_US=2000000
./build/rithmic_gateway/rithmic_latency_probe
```

Read **`reports/latency_baselines/<run_id>_summary.json`** — offensive/defensive blocks are in **µs**.

**Bulk ack campaigns** (`chi404_run_paper_latency_sweep.sh`, `chi404_run_live_latency_sweep.sh`) exist for **backtest `send_to_ack` authority (ms)** only. Run them only when the operator asks for **ack/replay latency**, not when they say **latency test**.

Live safety: [RITHMIC_LIVE_CONNECTION.md](RITHMIC_LIVE_CONNECTION.md).

---

## 4. Repo vault read order (after this note)

| # | Note | When |
|---|------|------|
| 1 | **This file** | Every session start |
| 2 | [HFTBACKTEST_LATENCY_ONTOLOGY.md](HFTBACKTEST_LATENCY_ONTOLOGY.md) | Backtest / realism / component latency |
| 3 | [RITHMIC_LIVE_CONNECTION.md](RITHMIC_LIVE_CONNECTION.md) | CHI404 / Rithmic / live |
| 4 | [CHI404_CANONICAL_ENTRYPOINTS.md](CHI404_CANONICAL_ENTRYPOINTS.md) | Probes, capture, forbidden paths |
| 5 | [RESEARCH_ENTRYPOINTS.md](RESEARCH_ENTRYPOINTS.md) | Pipeline script order |
| 6 | Task-specific note | CPI, data lake, certification, … |

Obsidian vault (declarative memory): `C:\Users\MSI\Desktop\Obsidian Vault From VPS\hft3\` — read `wiki/hot.md` after this repo vault gate.

---

## 5. Hard rejects (latency)

- Do **not** report `send_to_ack_us` p99 **ms** when asked for a **latency test**.
- Do **not** use paper ack ms as internal engine µs without explicit conversion and separate labels.
- Do **not** invent probes, log inject, or Python ctypes for placement-speed authority.
- Do **not** conflate `specs/LATENCY.md` replay injection (ms band) with `tick_to_send_us` (µs placement).
