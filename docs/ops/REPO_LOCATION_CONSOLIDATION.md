# MANDATORY ONTOLOGY GATE: Before every interaction in this project, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent codebases, pipelines, models, or methodology outside that authority.

# Repo Location Consolidation

Date: 2026-06-15.

Canonical working tree:

```text
C:\Users\MSI\repos\hft3
```

This is the only active hft3 working tree for Codex, cockpit, workbench, CHI404 launchers, tests, and future commits. The Obsidian vault also names this path as the repo authority in `wiki/hot.md`, `Home.md`, and `Memory Stack.md`.

Detailed clone-retirement evidence: [Repo Clone Inventory 2026-06-15](REPO_CLONE_INVENTORY_20260615.md).

## Current Launcher State

These desktop launchers must point to the canonical tree:

| Launcher | Canonical target |
|---|---|
| `HFT3 Cockpit.lnk` | `C:\Users\MSI\repos\hft3\scripts\cockpit_launch.ps1` |
| `HFT3 Workbench.lnk` | `C:\Users\MSI\repos\hft3\scripts\launch_workbench.ps1` |
| `CHI404 HFT Server.lnk` | `C:\Users\MSI\repos\hft3\scripts\launch_chi404.ps1` |
| `CHI404 RTrader VM VNC.lnk` | `C:\Users\MSI\repos\hft3\scripts\launch_chi404_vm_vnc.ps1` |

`scripts/launch_chi404_vm_vnc.ps1 -InstallDesktopShortcut` regenerates the VNC launcher from the canonical repo. The stale copy in `C:\Users\MSI\Documents\GitHub\hft3` redirects to the canonical script when present.

## Legacy Paths

Do not run repo work from these paths unless a migration plan explicitly says so:

| Path | Current role | Required handling |
|---|---|---|
| `C:\Users\MSI\Documents\hft3` | Legacy Codex entry stub, no `.git` directory | Keep as redirect warning until it can be replaced by a junction to canonical. Do not run git/tests/scripts here. |
| `C:\Users\MSI\Documents\GitHub\hft3` | Dirty standalone stale clone | Quarantine first. Inventory branch, commits, dirty files, generated data, and unique artifacts before copying or deleting anything. |
| `C:\Users\MSI\Documents\opencode\hft3` | Mostly clean standalone clone with local artifacts | Preserve or reject `perf/event-driven-quoting`, `runtime/universe_logs/`, local `data/`, `artifacts/`, Rithmic SDK/vendor material, `graphify-out/`, and secure env files before retirement. |
| `C:\Users\MSI\repos\hft3-baseline` | Linked worktree / baseline view | Review dirty `graphify-out/*`; keep only if still needed for comparison. |
| `C:\Users\MSI\repos\hft3-eqopt` | Linked worktree for `eqopt/live-probe` | Keep until the equities/options probe branch is closed or explicitly migrated. |
| `C:\Users\MSI\repos\hft3\.claude\worktrees\options-slice1b` | Linked worktree for prior options slice | Close or archive only after confirming the branch contents are merged, pushed, or intentionally rejected. |

## Consolidation Order

1. Keep `C:\Users\MSI\repos\hft3` active throughout.
2. Quarantine and inventory `C:\Users\MSI\Documents\GitHub\hft3`.
3. Preserve or reject unique artifacts from `C:\Users\MSI\Documents\opencode\hft3`.
4. Review `hft3-baseline` dirty graph files and decide whether the worktree still has value.
5. Keep `hft3-eqopt` until its branch is accepted, rejected, or moved to the lane repo.
6. When no process has `C:\Users\MSI\Documents\hft3` open, replace the non-git stub with a junction to `C:\Users\MSI\repos\hft3`.

No destructive delete, move, reset, or cleanup is allowed against any alternate clone until the inventory proves its contents are duplicated, obsolete, or intentionally rejected.
