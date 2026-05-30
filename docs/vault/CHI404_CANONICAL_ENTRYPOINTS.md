# CHI404 canonical entrypoints (agents: read before any CHI404 / R|Trader work)

**Graph first:** `graphify query "CHI404 R|Trader deploy paper latency"` or read this doc.  
**Do not** invent host-side log inject, workstation round-trips, or parallel orchestrators.

## Topology (non-negotiable)

Per [BLUEPRINT.md §4](../../BLUEPRINT.md#4-live-architecture): live/paper capture and order measurement run on **CHI404 bare metal** only.  
Path: **R|Trader Windows VM (paper) → SMB → `/root/hft3/rtrader_watch` → `RTraderBridgeConnector` → daemon / capture**.

## One-time / recovery deploy (VM + sidecar)

Run on **CHI404** after sync; requires `VM_ADMIN_PASSWORD` in `/root/hft3/.env`.

```bash
cd /root/hft3/repo
bash scripts/chi404_finish_rtrader.sh          # SMB + VM + bridge + headless (full lane)
# or, if VM already exists:
bash scripts/chi404_vm_deploy.sh               # headless + SMB remap + log path + restart + status
```

| Step | Script | Purpose |
|------|--------|---------|
| SMB share | `infrastructure/chi404/10_rtrader_smb_share.sh` | Host watch dir |
| VM create | `infrastructure/chi404/11_rtrader_windows_vm.sh` | KVM guest (Desktop Experience) |
| Headless autostart | `scripts/chi404_vm_apply_headless.py` | Upload PS1 + scheduled tasks |
| SMB symlink | `scripts/chi404_vm_remap_smb.py` / `C:\chi404_vm_map_smb.ps1` | `Documents\Rithmic` → UNC |
| Log path | `scripts/chi404_vm_fix_log_path.py` | Merge `.bak`, restart R\|Trader |
| Restart chain | `scripts/chi404_vm_restart_rtrader.py` | MapSMB → trader → login → subscribe |
| Linux bridge | `scripts/chi404_setup_vm_bridge.sh` | Capture service + watch dirs |
| Health | `scripts/chi404_vm_status_check.py` | Process, SMB, session JSON |

**Commits:** `adb1b26`, `844b987`, `4e1f750`, `f35f0e6`, `70fef9d`, `ad1080e` (memory).

## Live capture (real market + order logs)

```bash
bash scripts/chi404_run_trial_live.sh          # live gate → capture → process → replay
```

Requires **growing** `.log` / `.cur.txt` on SMB from **R|Trader**, not script writes.

## Paper order submit→ack latency (≥1,000 pairs)

**Forbidden:** `Add-Content`, host `f.write` order lines, `SWEEP-*` synthetic order IDs, TCP :65000 as ack.

```bash
# 1. VM session healthy (cur.txt growing, live gate PASS)
bash scripts/chi404_vm_live_gate.sh

# 2. Full sweep (live gate → daemon → VM UI orders → promote → latency_summary)
bash scripts/chi404_run_paper_latency_sweep.sh
```

VM UI orders run **inside interactive session** via `scripts/chi404_trigger_vm_paper_sweep.py` → `chi404_vm_run_interactive.py` (not raw WinRM `Start-Process` for UI).

| Artifact | Path |
|----------|------|
| Raw audit | `runtime/paper_latency/raw/<run_id>/records.ndjson` |
| Trial reports | `reports/rithmic_trial/<date>/` |
| Summary | `runtime/latency_reports/latency_summary.json` |

Refresh probe summary:

```bash
python3 scripts/latency_probe/summarize_latency.py --run-id <probe_run_id> --include-trial-appendix
```

## Deprecated / forbidden paths

| Path | Why forbidden |
|------|----------------|
| `scripts/deprecated/chi404_*host*sweep*` | Host-side synthetic log inject |
| `scripts/deprecated/chi404_*fast_market*` | Host-side synthetic log inject |
| `scripts/deprecated/chi404_run_paper_sweep_direct.sh` | Skips live gate; session bypass |
| Workstation capture / log-push | BLUEPRINT §4 |
| `chi404_vm_paper_order_sweep.ps1` with `Add-Content` | Fake orders — blocked by pytest |

## Agent checklist (before editing CHI404 scripts)

1. `scripts/graphify_gate.ps1 -Query "..."` (or `graphify_gate.sh` on CHI404)
2. Read this doc + [docs/rithmic_trial/README.md](../rithmic_trial/README.md) + [CHI404_VM_BUGS.md](../rithmic_trial/CHI404_VM_BUGS.md)
3. Prefer extending **existing** deploy chain — do not add parallel orchestrators
4. `pytest tests/test_chi404_canonical_guardrails.py` after changes
