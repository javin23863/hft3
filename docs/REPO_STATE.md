# Repository State

**Last updated:** 2026-07-02
**Purpose:** one current map for agents and operators before touching the repo,
workbench, or paid Vast HBT campaign.

## Canonical Worktree

| Item | Current value |
| --- | --- |
| Canonical repo | `C:\Users\MSI\repos\hft3` |
| Legacy stub | `C:\Users\MSI\Documents\hft3` is redirect-only; do not run git, tests, scripts, or edits there |
| Remote | `https://github.com/javin23863/hft3` |
| Current branch | `codex/research-pipeline-run-contract`; recorded upstream is gone |
| Merge readiness | not merge-ready; local worktree is dirty and includes user/runtime WIP |
| Heavy verification host | CHI404 for smoke/transfer checks; Vast only for the active paid HBT campaign |
| VastAI role | original paid HBT campaign is resumed on instance `42609000` |

## Current Worktree State

Do not reset or clean the tree. A fresh status check on 2026-07-02 showed
modified generated graph files, structural-model code/test WIP, runtime reports,
and untracked Vast/runtime receipts. Treat these as existing WIP unless the owner
explicitly asks for cleanup.

Known dirty areas include:

- `graphify-out/.graphify_labels.json`
- `graphify-out/GRAPH_REPORT.md`
- `packages/features_engine/src/structural_models/model_09_quantum_spread.py`
- `tests/structural_models/test_pdf_models_integration.py`
- `runtime/reports/`
- `runtime/vast_receipts/`

## Active Paid Vast HBT Campaign

Canonical paid host is Vast instance `42609000`, not the tandem/candidate
instance `43298099` and not a new rental. Live check at
`2026-07-01T19:38Z` showed only `42609000` active, with
`actual_status=running`, `cur_state=running`, `intended_status=running`, CPU
about `84%`, and `instance.totalHour=$0.6988888889/hr`.

Campaign identity:

- Campaign root: `/data/hbt_vast_20260629_587e7f2`
- Manifest: `/data/hbt_vast_20260629_587e7f2/campaign_parameter_surface.jsonl`
- Output root:
  `/data/hbt_vast_20260629_587e7f2/hbt_full_parameter_surface_runs_078fa690`
- Launcher start: `2026-07-01T15:29:59Z`
- Source commit on runner: `a6424fc1095cff9c5eff23ceef948b6841df0518`
- Workers: `217`
- `max_tasks_per_child=256`
- Resume mode: same manifest/output root, `--resume`, no manual filter

Current issue:

Monitor reports `3,123,439 / 3,950,895` row receipts, `827,456` remaining,
`phase=progress_stalled`, and `rows_per_second_last_interval=0.0`. Watchdog is
still `status=watching`; tmux run/monitor sessions are present and manifest scan
is advancing at about `13.08%` with `seconds_since_manifest_advance=0`. The
post-latest-resume log audit verdict is `post_resume_clean` with zero
post-marker `Traceback`, `BrokenProcessPool`, `numpy.trapz`, and
`QUANTUM_SPREAD_DEFENSE` counts.

Interpretation: this is paid resume catch-up/manifest scanning before new row
receipts appear. It is not currently a crash, successful completion, proof of an
extra active instance, or permission to start a second paid box.

## Workbench State

Workbench is a local/offline diagnostics and robustness lane, not the active
paid HBT campaign runner. Current workbench notes that describe a direct
`44 HYP + 7 PDF` or "51-model" universe are legacy/direct-workbench language.
For the active paid HBT campaign, the canonical HBT identity is the registry
slug universe from `packages/features_engine/config/model_registry.yaml`,
including hypothesis, structural, and reinforcement-learning policy/proxy
entries. Missing HBT order adapters are blockers, not permission to skip models
or treat local workbench output as tradability evidence.

Local diagnostic code started during the tandem/candidate investigation is
unfinished WIP and was not deployed as the paid-campaign plan.

## Active Status Commands

```powershell
git status --short --branch
vastai show instances-v1 --raw
vastai ssh-url 42609000
ssh -p 36849 root@211.21.106.81 "cd /data/hbt_vast_20260629_587e7f2 && jq . hbt_full_parameter_surface_078fa690.monitor_status.json && jq . hbt_full_parameter_surface_078fa690.watchdog_status.json"
```

Use the current `vastai ssh-url 42609000` result before any SSH action; direct
port `36849` was valid at the last check but Vast ports can change across stops
and starts.

## Retired As Current Instructions

These may exist in historical notes, but they are not active instructions:

| Retired item | Current replacement |
| --- | --- |
| PR #13 / `codex/feature-plane-mvc-gates` as active repo state | current branch is `codex/research-pipeline-run-contract`, with upstream gone |
| "pre-VastAI only" instructions | active paid HBT campaign already resumed on `42609000` |
| hardcoded old Vast endpoints | check `vastai ssh-url 42609000` before SSH |
| candidate/tandem `43298099` as canonical | `42609000` is canonical; tandem artifacts are isolated diagnostics |
| workbench "51-model" wording as paid HBT identity | registry-backed HBT slug universe is authoritative for the paid campaign |

## Review Gates

Before claiming any repo or campaign state is resolved:

1. VaultGate and repo `AGENTS.md` loaded.
2. Graph gate status recorded as `waived-by-owner-2026-06-16` until the owner re-enables it.
3. Current Vast instance list checked.
4. Campaign monitor/watchdog files checked from the active host.
5. Dirty worktree reviewed without reverting user/runtime WIP.
6. Any paid-compute change is explicitly tied to the owner-approved canonical
   host/run plan.
7. Focused tests or smoke checks run only for the code path being changed.
