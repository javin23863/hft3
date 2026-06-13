# Cockpit Build-Out — Commit & Next-Steps Review

Session date: 2026-06-13. Branch: `main`. All hashes below are pushed to `origin/main`.

This is a review ledger: every commit from this session, what it changed, why, how it
was verified, plus the honest record of one reverted misstep and the open next steps.
Scope discipline (per owner direction): **build out the dashboard + wire the existing
pipeline per the blueprints — no inventing pipeline code, no agent-run research.**

---

## 1. Commits (chronological)

| # | hash | subject | status |
|---|------|---------|--------|
| 1 | `e4c72b5` | Parallel/resumable sharding + hang-resistant fetch — event-tape downloader | ✅ live |
| 2 | `d164e27` | survivor_intake — mint CERTIFIED from a universe sweep (WS-B3) | ⛔ **reverted** |
| 3 | `aaa7573` | gauntlet_reader real-schema + nightly discovery wiring (WS-B3/B4) | ⛔ **reverted** |
| 4 | `9b2b2bd` | Revert of #3 | ✅ live |
| 5 | `4f5c8d6` | Revert of #2 | ✅ live |
| 6 | `46c8272` | Cockpit **W4** job launcher → existing job_runner/worker + live log streaming | ✅ live |
| 7 | `396f653` | Cockpit **live positions** + **service-deploy** hardening | ✅ live |
| 8 | `478f80a` | Cockpit review fixes: honest health, bounded+per-client rate-limit, normalized positions, headless service | ✅ live |

Net tree state = #1 + #6 + #7 + #8 (the WS-B work in #2/#3 was fully reverted; the tree is as if it never landed).

---

## 2. Live commits — detail

### `e4c72b5` — event-tape downloader hardening
- **Files:** `scripts/download_event_tape.py` (+151/−28).
- **What:** `--shard I/N` (disjoint modulo partition), `--start-idx N`, `.dbn` reuse (convert kept tape, no re-pay), transient-only retry, thread-watchdog timeout (databento ignores `socket.setdefaulttimeout`), convert-on-already-exists fallback.
- **Why:** download the full 7659-event MBO universe faster + crash/hang-resistant.
- **Verified:** completed the universe end-to-end (lake 47,886 → 63,804 NPZ); a 6→2-shard run; live monitors.

### `46c8272` — cockpit W4 job launcher (`control.py`, `worker.py`)
- **What:** `POST /api/control/job` enqueues into the **existing** `lifecycle_orchestrator.job_runner` (its enqueue lock, state machine, CHI404 host-gate, artifact capture); when `COCKPIT_CONTROL_EXEC=1` it kicks the **existing** `worker` (detached) with the shim `PYTHONPATH` + lake env. 5 jobs → real CLIs (`build_feature_store.py --rebuild`, `run_stage_a_screen.py`, `slow_tier_nightly.ps1`; `systemctl restart hft3-capture` for roll/restart on CHI404). `roll_now`/`capture_restart` flagged **disruptive** → require `params.ack_capture_gap=true`. `GET /api/control/job/{id}/logs` tails the per-job logfile **live**. `worker._default_handler` streams stdout+stderr to `runtime/lifecycle/jobs/logs/<id>.log` (`-u` for `.py`).
- **Why:** complete the cockpit's own documented **W4** control-plane step (the README + `control.py` markers) by reusing existing infrastructure, not a bespoke launcher.
- **Verified:** 46/46 (cockpit + orchestrator); queue round-trip probe; live-stream probe (logfile grew 1→6 mid-run).

### `396f653` — live positions + service deploy
- **Files:** `portfolio.py`, `main.py`, `paths.py`, new `scripts/register_cockpit_task.ps1`, new `configs/caddy/Caddyfile.example`.
- **What:** Portfolio reads the latest trade_manager session report (`positions.jsonl` + `pnl_timeseries.jsonl`) via the **existing observer reader** (`apps/observer.load_observer_view`); `paths.SESSIONS_ROOT = artifacts/sessions`. Dependency-free rate-limit middleware on `/api/*` (`/api/health` exempt, `/api/chat` tighter) + 4000-char chat cap. `register_cockpit_task.ps1` = restart-on-failure logon service. Caddy TLS reverse-proxy sample (XFF→view-only, streaming-friendly).
- **Verified:** 38/38; positions probe (no-session `[]` vs session-populated); rate-limit probe (429 over limit); schtask dry-run.

### `478f80a` — review fixes (grader pass)
- **What:** Portfolio **honest health** — surface unreadable session artifacts (AMBER, not silent empty), RED on engaged kill switch, AMBER on stale live session (>600s); add `session_age_s`/`kill_switch`/`notes`. **Normalize** positions to current `[{symbol, quantity}]`. Rate-limit **memory-bounded** (`_RL_MAX_KEYS` + prune) and **per-client** behind a trusted proxy (`COCKPIT_TRUST_PROXY` → leftmost XFF). `-Headless` registers a true boot service (AtStartup + S4U).
- **Verified:** 38/38; probes confirm amber-on-malformed, red-on-killswitch, normalized positions, per-client limiting, headless dry-run.

---

## 3. Reverted misstep (transparency)

`d164e27` + `aaa7573` invented `survivor_intake.py` + edited `gauntlet_reader.py` to mint
CERTIFIED models from agent-run sweeps. This **overstepped** — it duplicated/repointed the
real pipeline (the 26-phase autonomous runner; robustness already lives inside it) and had
the runtime LLM doing research. Per owner direction it was **fully reverted** (`9b2b2bd`,
`4f5c8d6`); the scratch sweep outputs were deleted and the production registry was never
written. Lesson recorded: follow `specs/PIPELINE.md` / `docs/hft3_autonomous_pipeline_runbook.md`;
do not invent pipeline code or run research.

---

## 4. Cockpit (WS-C) status

| Item | State |
|---|---|
| W4 job launcher (backend) | ✅ `46c8272` |
| Live positions | ✅ `396f653` + `478f80a` |
| Service deploy (rate-limit, schtask, Caddy) | ✅ `396f653` + `478f80a` |
| **Frontend control UI (C2)** | ⬜ not started — React buttons for the 5 W4 jobs (+ `ack_capture_gap` on disruptive), a live log viewer polling `/api/control/job/{id}/logs`, autonomy stop/unfreeze |
| Backend unit tests for new paths (C5) | verified via probes; no new test files added (per "no testing" directive) |

---

## 5. Next steps / open items

**Cockpit (build-out, in scope):**
1. Frontend control UI (C2) — drive the W4 backend end-to-end.
2. (Optional) widen the existing cockpit test suite to cover W4 launcher + portfolio-health + rate-limit paths.

**Data lake (WS-A) — ran this session, not all committed (data ops, not code):**
- A1 catalog rebuild **done** (`manifest.json` → 63,804). 
- A2 B2 sync of the new tape **stopped mid-`npz`** — resumable: `scripts/sync_lake_b2.ps1 -Stage all`.
- A3 restore drill (`b2_restore_drill.py --n 200`), A4 promote `HFT3_NPZ_ROOT`/etc to **Machine** scope (else SYSTEM tasks re-resolve to the empty repo path), A5 `data_doctor.py` 0-FAIL — **pending**.

**Autonomous pipeline (WS-B) — corrected scope:**
- The real pipeline is the **26-phase autonomous runner** (`hft3-research.py` / `hft3.research.run_autonomous`), currently **scaffolded** (WorkbenchEngine backtest/robustness not wired → defaults QUARANTINE per the runbook §17). Build-out = wire the scaffolded seams **per the blueprint**, not new discovery code.

**Repo/CI (WS-D), LIVE-arm (WS-E):** not started; WS-E stays OFF (Rithmic R|API entitlement externally blocked).

---

## 6. How to review

- `git log --oneline 9db382e..HEAD` — the commit stack above.
- `git show <hash>` — per-commit diff.
- Tests: `python -m pytest apps/cockpit/backend/tests -q` (shim `PYTHONPATH`).
- Live behavior: `scripts/run_cockpit.ps1` → `http://127.0.0.1:8080`; control endpoints are local-origin only.
