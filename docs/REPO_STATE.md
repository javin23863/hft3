# Repository state (canonical consolidation)

**Last updated:** 2026-06-20 (Vast pipeline Plan v3 — single deploy path)
**Purpose:** One truthful map of where to work, what landed on `main`, and how to verify a clean tree. Humans and agents read this before assuming branch or path.

---

## Canonical working copy

| Item | Value |
|------|--------|
| **Path (production verify)** | `C:\Users\MSI\repos\hft3` |
| **Path (this Cursor workspace)** | `C:\Users\MSI\Documents\New project` |
| **Remote** | `https://github.com/javin23863/hft3` |
| **Default branch** | `main` |
| **HEAD (`origin/main`)** | `6aa89c7e` — *fix(vbt): run paid screen units on Vast* |

Do **not** treat alternate hft3-looking folders as canonical without reading [Secondary workspaces](#secondary-workspaces-cursor) below.

---

## Chronological trunk (GitHub `main`)

Read top-down (newest first):

| When (UTC) | Commit | What |
|------------|--------|------|
| 2026-06-18 | `6aa89c7e` | Vast paid-screen unit execution on `main` |
| 2026-06-18 | `29884418` | Smoke: wire `--ready-gate-file` for multi-worker paid screen |
| 2026-06-18 | `9c9ba522` | Phase 9 feature-family paid-screen readiness gate |
| 2026-06-18 | `080254c3` | **PR #6 merged** — HftBacktest three-component latency taxonomy |
| 2026-06-18 | `dbfa9942` | REPO_STATE + canonical path pointers |
| 2026-06-17 | `6fe3e8c2` | PR #4 merged — Codex PR review workflow |
| 2026-06-17 | `63cc8abe` | **PR #3 merged** — VBT+HBT handoff pipeline |

---

## Active feature line (not on `main` yet)

| Branch | Tip | Role | vs `main` |
|--------|-----|------|-----------|
| **`cursor/vast-vbt-workflow`** | `f27fefcc` | Vast VectorBT paid-screen orchestrator fixes, repro gates, launch scripts | **+23 commits** ahead of `main` |

Merge via reviewed PR when ready gate + scope are green. Do not treat this branch as `main` until merged.

---

## Local branch inventory (this workspace, 2026-06-19)

| Branch | Merged into `origin/main`? | Action |
|--------|---------------------------|--------|
| `main` | — | Tracks `origin/main` @ `6aa89c7e` |
| `cursor/vast-vbt-workflow` | **No** (active) | **Current** — Vast/VBT incident fixes |
| `chore/repo-cleanup-and-data-fill` | **No** | Lane WIP — triage via PR before merge |
| `cursor/fix-codex-pr-review-main-d160` | **No** | Draft **PR #5** |
| ~~`codex/workbench-runtime-sync`~~ | — | **Removed locally** 2026-06-19 (still on `origin/`; recover with `git checkout -b … origin/…`) |

**Stashes**

| Stash | Contents |
|-------|----------|
| `stash@{0}` | `pre-repo-cleanup-2026-06-19: vast-vbt drift WIP` on `cursor/vast-vbt-workflow` |
| `stash@{1}` | `pre-push stash` on `chore/repo-cleanup-and-data-fill` |

---

## Remote branches — merged vs open

### Merged into `origin/main` (candidates for remote delete after human confirm)

```
origin/codex/vbt-hbt-handoff
origin/crypto/c0-venue-rtt
origin/cursor/gate-8-research-clock-d160
origin/cursor/hbt-fixtures-workflow-d160
origin/cursor/hftbacktest-vendor-lock-d160
origin/cursor/stress-decomposition-length-d160
origin/cursor/vbt-post-gate-playbook-d160
origin/feat/hftbacktest-three-component-latency
origin/mandatory-vectorbt-idea-crypto-lane
origin/options/ws1-slice1
origin/perf/event-driven-quoting
```

### Not merged (keep until PR or explicit retire)

```
origin/chore/repo-cleanup-and-data-fill
origin/cursor/fix-codex-pr-review-main-d160   # PR #5 draft
origin/cursor/vast-vbt-workflow               # active VBT work
origin/chi404/wip-l3-sim-fills
origin/codex/workbench-runtime-sync
origin/eqopt/live-probe
origin/feat/mbo-release-lane
origin/options/cockpit-integration
origin/runner-seed-resolver
origin/stocks-lane-restored
```

**Open pull requests (GitHub):**

| PR | State | Branch |
|----|-------|--------|
| #6 | **MERGED** | `feat/hftbacktest-three-component-latency` |
| #5 | DRAFT | `cursor/fix-codex-pr-review-main-d160` |
| #3 | **MERGED** | `codex/vbt-hbt-handoff` |

---

## Cleanup pass (2026-06-19, this workspace)

| Action | Detail |
|--------|--------|
| Stashed VBT drift WIP | `stash@{0}` — declaration, monitors, launch script edits |
| Archived incident debris | `_rebase_stash/vast_vbt_incident_20260619/` — agent one-off `runtime/_*` scripts, NPZ sync scratch, autoresearch cards, drift doc draft |
| `.gitignore` | Ignore `runtime/_*.py|sh|ps1|bat`, `paid_screen_scratch/`, large `vbt_full_units.jsonl`, `_rebase_stash/`, `research_cards/autoresearch/**` |
| Deleted local branch | `codex/workbench-runtime-sync` (duplicate of remote; not on `main`) |
| **Not done** | Remote branch deletes on GitHub (requires owner confirm); merge `cursor/vast-vbt-workflow` |

---

## Secondary workspaces (Cursor)

### `C:\Users\MSI\Documents\New project`

| Item | State (after 2026-06-19 cleanup) |
|------|----------------------------------|
| Branch | `cursor/vast-vbt-workflow` @ `f27fefcc` |
| Working tree | Clean except `.gitignore` update (commit when ready) |
| Relationship | Same remote as canonical; use for VBT/Vast lane until PR merges |

**Policy:** Heavy compute on Vast, not this workstation. Restore WIP from `stash@{0}` when resuming VBT work.

### `C:\Users\MSI\repos\hft3`

Canonical path for production verify and new `main`-based work per [AGENTS.md](../AGENTS.md).

---

## Vast VectorBT deploy (Plan v3)

| Item | Value |
|------|--------|
| **Only deploy path** | `scripts/vast_deploy_and_verify.ps1` → must print `DEPLOY_CONTRACT_PASS` |
| **Review stack before commit** | `cavecrew-reviewer` → `python scripts/run_ontology_gate.py` → `python scripts/run_plan_drift_review.py --completed-phase <id>` → pytest |
| **Retired launch scripts** | `runtime/vast_*.sh` → `runtime/_deprecated_vast_incident_20260619/` |
| **NPZ filter** | `VBT_REQUIRE_RUNNABLE_NPZ=1` (default) + `--require-runnable-npz` on unit generator |
| **Abort policy** | `abort_on_failed_units: true` in declaration + `--abort-on-failed-units` on v2 orchestrator |
| **Gate hashes** | `events_csv_hash` / `lake_manifest_hash` in `runtime/reports/paid_screen_ready_gate.json` — sync `manifest.parquet` to Vast `/data/npz/manifest.parquet` |
| **Vast manifest env** | On Vast host set `HFT3_MANIFEST_PATH=/data/npz/manifest.parquet` (parquet hash matches gate `pilot_hashes.lake_manifest_hash`; not `manifest.json`) |

---

## Verify clean state

```powershell
cd "C:\Users\MSI\Documents\New project"
git fetch origin --prune
git status -sb          # expect clean or only intentional edits
git branch -vv          # branch inventory
git log --oneline -5 cursor/vast-vbt-workflow
git stash list
```

Expected after cleanup: no untracked `runtime/_*.py` flood; incident artifacts under `_rebase_stash/` (gitignored).

---

## Start-here doc order

1. This file — path, branch, merge truth
2. [docs/human/GETTING_STARTED.md](human/GETTING_STARTED.md)
3. [docs/human/DOC_INDEX.md](human/DOC_INDEX.md) — full doc chronology
4. [docs/ai/ONBOARDING.md](ai/ONBOARDING.md)
5. [AGENTS.md](../AGENTS.md)
