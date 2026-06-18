# universe_M6_full Ã¢â‚¬â€ Vast watch protocol

**Instance:** Vast `41383988` Ã¢â‚¬â€ `ssh -o ConnectTimeout=15 -p 23988 root@ssh1.vast.ai`  
**Tmux session (when running):** `universe_M6_full`  
**Primary log:** `/root/hft3/repo/runtime/universe_M6_full_20260618T043132Z.log`  
**Output:** `/root/hft3/repo/research_cards/universe_M6_full/`  
**Launch stamp (2026-06-18):** workers=254, lake `/data/npz`, commit `e9c72b8a`, queue ~285840 units (~224334 rescan skips expected).

## Quick health (run from workstation)

```powershell
ssh -o ConnectTimeout=15 -p 23988 root@ssh1.vast.ai "bash /root/hft3/repo/runtime/monitor/watch_universe_M6_full.sh"
```

Or one-shot inline:

```powershell
ssh -o ConnectTimeout=15 -p 23988 root@ssh1.vast.ai "pgrep -af scripts/run_event_universe || echo DEAD; tmux has-session -t universe_M6_full 2>&1; tail -n 5 /root/hft3/repo/runtime/universe_M6_full_20260618T043132Z.log; wc -c /root/hft3/repo/research_cards/universe_M6_full/unit_results.jsonl; find /root/hft3/repo/research_cards/universe_M6_full -type f | wc -l"
```

Copy `watch_universe_M6_full.sh` to the instance after editing locally, or keep a copy under repo `runtime/monitor/` and `scp` when the remote tree is synced.

## What each check means

| Signal | GREEN | YELLOW | RED |
|--------|-------|--------|-----|
| `pgrep -af scripts/run_event_universe` | Main python parent present | Only stale/zombie workers | No process; tmux gone |
| `unit_results.jsonl` size | Grows between passes | Flat <30 min while process alive | 0 bytes after pool start |
| Log tail | Progress / unit completion lines | OpenBLAS thread warnings sporadic | `BlockingIOError`, `Traceback`, repeated `pthread_create failed` |
| Worker count | Stable vs launch (254) | Drift >10% | Pool never created |
| NPZ root | `/data/npz` ~63k+ index | Ã¢â‚¬â€ | Wrong path or empty lake |
| Artifacts dir | File count + `du` rising | Only `unit_results.context.json` | No new cards after hours |

## Red flags (2026-06-18 incident pattern)

1. **Process spawn exhaustion:** `OpenBLAS blas_thread_init: pthread_create failed` followed by `BlockingIOError: [Errno 11] Resource temporarily unavailable` at `ctx.Pool(processes=254)` Ã¢â‚¬â€ run exits before any unit completes; `unit_results.jsonl` stays **0 bytes**.
2. **False Ã¢â‚¬Å“skipÃ¢â‚¬Â comfort:** `skipped: 224334` on the `Work units:` line is **rescan dedupe**, not completion Ã¢â‚¬â€ pair with `remaining:` and jsonl growth.
3. **All-hypothesis FAIL:** With zero completed units, do not interpret hypothesis FAIL rates; see `specs/CORRECTNESS.md` M6 masking note for prop-slot dead hypotheses (HYP 20, 30, 32, 35, 36, 38).
4. **Wrong commit / lake:** Launch line must show `npz=/data/npz` and expected short SHA; lake index ~63602 entries.

## Recommended relaunch (after RED spawn failure)

Do **not** use 254 workers until thread budget is bounded. Example:

```bash
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
cd /root/hft3/repo
ts=$(date -u +%Y%m%dT%H%M%SZ)
log=runtime/universe_M6_full_${ts}.log
tmux new-session -d -s universe_M6_full "python -u scripts/run_event_universe.py ... --workers 32 2>&1 | tee $log"
```

Tune `--workers` (32Ã¢â‚¬â€œ64) until `pgrep -c python` stabilizes and jsonl grows. Update **Primary log** path in this doc after relaunch.

## Ongoing monitor (every 5Ã¢â‚¬â€œ10 min)

Windows Task Scheduler or manual loop (max 12 iterations = ~1 hr):

```powershell
for ($i=0; $i -lt 12; $i++) {
  ssh -o ConnectTimeout=15 -p 23988 root@ssh1.vast.ai "bash /root/hft3/repo/runtime/monitor/watch_universe_M6_full.sh"
  Start-Sleep -Seconds 300
}
```

## Cockpit / UI gap

Workbench cockpit backend has **no** built-in ingest for remote Vast SSH log tail as of this watch setup. **Wire cockpit** subagent would need: SSH poll Ã¢â€ â€™ parse `Work units` / jsonl line count Ã¢â€ â€™ push status API. Monitoring is CLI-only until then.

## Monitor session log

| UTC | Status | Notes |
|-----|--------|-------|
| 2026-06-18 ~04:34Ã¢â‚¬â€œ04:39 | **RED** | No `run_event_universe` parent; tmux absent; log frozen 1957 lines; crash `BlockingIOError` at Pool; jsonl 0 B; 2 artifact files only. |

## Cockpit workstation mirror (read-only tracking)

Cockpit reads repo artifacts only; it does not SSH to Vast. To stop a **stalled local 14-worker** false read, mirror Vast outputs into `C:\Users\MSI\repos\hft3`:

```powershell
scp -o ConnectTimeout=15 -P 23988 root@ssh1.vast.ai:/root/hft3/repo/runtime/universe_M6_full_20260618T043132Z.log C:\Users\MSI\repos\hft3\runtime\universe_M6_vast.log
scp -o ConnectTimeout=15 -P 23988 root@ssh1.vast.ai:/root/hft3/repo/research_cards/universe_M6_full/unit_results.context.json C:\Users\MSI\repos\hft3\research_cards\universe_M6_full\unit_results.context.json
```

**Host label:** Pipeline uses checkpoint `cli_args.workers` (>=64 => `host_kind=rented`) and the newest `runtime/universe_M6*.log` by mtime. Prefer `universe_M6_vast.log` so the Vast mirror wins over stale local logs.

**Override (optional):** In the cockpit launch script / shell before `uvicorn`, set `$env:HFT3_UNIVERSE_SWEEP_HOST = "vast"` so host shows as external even if workers in checkpoint are wrong.

**Refresh:** Re-run the `scp` log line (or `ssh ... tail` append) every few minutes while the remote job is active; `state=running` requires log mtime <300s and `remaining>0`. Hard-refresh the Pipeline view in the browser after sync.

**2026-06-18 sync:** Log ~174 KiB and checkpoint copied; log tail shows pool `BlockingIOError` Ã¢â‚¬â€ see red flags above; do not kill the instance from this mirror step alone.
