# Repository state (canonical consolidation)

**Last updated:** 2026-06-18 (cleanup pass — pristine trees)  
**Purpose:** One truthful map of where to work, what landed on `main`, and how to verify a clean tree. Humans and agents read this before assuming branch or path.

---

## Canonical working copy

| Item | Value |
|------|--------|
| **Path** | `C:\Users\MSI\repos\hft3` |
| **Remote** | `https://github.com/javin23863/hft3` |
| **Default branch** | `main` |
| **Active branch (canonical)** | `main` |
| **HEAD (canonical `main`)** | `dbfa9942` — *docs(repo): REPO_STATE + canonical path pointers* (tracks `origin/main`) |

Do **not** treat `C:\Users\MSI\Documents\New project` or other hft3-looking folders as canonical without reading [Secondary workspaces](#secondary-workspaces) below.

---

## Chronological trunk (GitHub `main`)

Recent merges on the authoritative remote (read top-down):

| When (UTC) | Commit | What |
|------------|--------|------|
| 2026-06-18 | `dbfa9942` | **REPO_STATE** on `main` — canonical path pointers in AGENTS/CLAUDE |
| 2026-06-18 | `080254c3` | **PR #6 merged** — HftBacktest three-component latency taxonomy, probe/regime wiring, CC component ingest |
| 2026-06-17 | `6fe3e8c2` | PR #4 merged — Codex PR review workflow |
| 2026-06-17 | `fbd26a6b` | PR #2 merged — AI coding delegate workflow (earlier iteration) |

Latency feature commits on `main` (included in `080254c3`):

- `135f9135` — ingest CC summaries into `latency_truth` bands
- `3a90ff5c` — unblock CC latency campaigns for live Rithmic 01
- `1a2900c2` — align regime JSON with realism contract
- `a8275eb2` — unblock PR #6 tests and screening hash imports
- `9d712f89` — Add HftBacktest three-component latency taxonomy and wiring

---

## Consolidation actions (2026-06-18)

| Action | Detail |
|--------|--------|
| Stashed local WIP | On canonical repo, **159** uncommitted files (pipeline cleanup + latency local artifacts) → `git stash list` → `stash@{0}: pre-consolidation-2026-06-18: pipeline cleanup + latency local WIP` |
| Merged PR #6 | `gh pr merge 6 --merge` → merge commit `080254c3` |
| Reset stale local `main` | Local `main` had diverged (`780a161c`, 3 commits) from remote; reset to `origin/main` after merge (no force-push) |
| Deleted local branch | `feat/hftbacktest-three-component-latency` (fully merged) |
| **Not merged** | Dirty / unreviewed work from `chore/repo-cleanup-and-data-fill` and New project workspace — see below |
| Cleanup pass (same day) | Canonical: stashed **27** modified files on `main` → `stash@{0}: pre-cleanup-2026-06-18: local main WIP (latency/docs/cockpit, 27 files)`. Workspace: `main` @ `dbfa9942`, removed untracked `artifacts/runs/`, `data/npz/`, `runtime/data_audits/`, `runtime/reports/`, `workbench_relaunch.log` (plus ignored cache via `git clean -fdX`). **No remote branches deleted.** |


---

## Branch map — before → after (local canonical repo)

### Before

| Branch | Role | Notes |
|--------|------|--------|
| `main` (local) | Stale | `780a161c` — behind/ahead of remote |
| `feat/hftbacktest-three-component-latency` | PR #6 | Clean at `135f9135` but **159** dirty working-tree files |
| Many topic branches | Lane work | See [Remote branches kept](#remote-branches-kept-intentionally) |

### After (local `C:\Users\MSI\repos\hft3`)

| Branch | Status |
|--------|--------|
| `main` | **Active** — tracks `origin/main` at `dbfa9942`, clean working tree |
| `feat/hftbacktest-three-component-latency` | **Removed locally** (merged); still on `origin` until remote prune |

**Local branches still present (not deleted):**

| Branch | Merged into `main`? |
|--------|---------------------|
| `codex/ai-delegate-workflow` | Yes |
| `codex/research-plan-authority-fix` | Unknown / worktree |
| `codex/vbt-hbt-handoff` | **No** — PR #3 open |
| `crypto/c0-venue-rtt` | Yes |
| `eqopt/live-probe` | **No** |
| `options/cockpit-integration` | **No** |
| `options/ws1-slice1` | Yes |
| `pr5-codex` | **No** |
| `spec/crypto-production-set` | Yes |

---

## Remote branches kept intentionally

Remote branches were **not** deleted during consolidation (no force-push, no remote prune). Review before deleting on GitHub:

```
origin/chore/repo-cleanup-and-data-fill
origin/codex/vbt-hbt-handoff          # PR #3
origin/codex/workbench-runtime-sync
origin/crypto/c0-venue-rtt
origin/cursor/fix-codex-pr-review-main-d160   # PR #5 draft
origin/cursor/gate-8-research-clock-d160
origin/cursor/hbt-fixtures-workflow-d160
origin/cursor/hftbacktest-vendor-lock-d160
origin/cursor/stress-decomposition-length-d160
origin/cursor/vbt-post-gate-playbook-d160
origin/eqopt/live-probe
origin/feat/hftbacktest-three-component-latency   # merged — safe to delete after confirm
origin/feat/mbo-release-lane
origin/mandatory-vectorbt-idea-crypto-lane
origin/options/cockpit-integration
origin/options/ws1-slice1
origin/perf/event-driven-quoting
origin/runner-seed-resolver
origin/stocks-lane-restored
origin/chi404/wip-l3-sim-fills
```

**Open pull requests (after PR #6 merge):**

| PR | State | Branch |
|----|-------|--------|
| #6 | **MERGED** | `feat/hftbacktest-three-component-latency` |
| #5 | DRAFT | `cursor/fix-codex-pr-review-main-d160` |
| #3 | OPEN | `codex/vbt-hbt-handoff` |

---

## Secondary workspaces

### `C:\Users\MSI\Documents\New project`

| Item | State (after cleanup pass) |
|------|---------------------------|
| Remote | Same (`javin23863/hft3`) |
| Branch | `main` (matches `origin/main` @ `dbfa9942`) |
| Local branch kept | `chore/repo-cleanup-and-data-fill` still exists locally — **not merged** |
| Working tree | **Clean** (`git status -sb` shows no M/D/??) |
| Stash | `stash@{0}: On chore/repo-cleanup-and-data-fill: pre-push stash` |
| Relationship | Cursor workspace clone; lane work on `chore/repo-cleanup-and-data-fill` remains on remote — triage via PR before merge |

**Policy:** Do not merge or cherry-pick from this tree without explicit triage. Use `C:\Users\MSI\repos\hft3` on `main` for testing and new work. Reconcile `chore/repo-cleanup-and-data-fill` via a future reviewed PR if those commits are still wanted.

### Stashes on canonical repo

| Stash | Contents |
|-------|----------|
| `stash@{0}` | `pre-cleanup-2026-06-18: local main WIP (latency/docs/cockpit, 27 files)` |
| `stash@{1}` | `pre-consolidation-2026-06-18: pipeline cleanup + latency local WIP` (from feat branch) |
| `stash@{2}` | `On main: wip-all-local` (older) |

Inspect newest WIP: `git stash show -p stash@{0} --stat`

**Remote branches merged into `origin/main` (safe to delete on GitHub after human confirm):** `origin/feat/hftbacktest-three-component-latency`, `origin/crypto/c0-venue-rtt`, `origin/options/ws1-slice1`, `origin/mandatory-vectorbt-idea-crypto-lane`, `origin/perf/event-driven-quoting`, `chi404/pc-sync`.

---

## Verify clean state

Run from `C:\Users\MSI\repos\hft3`:

```powershell
cd C:\Users\MSI\repos\hft3
git fetch origin
git checkout main
git status -sb          # expect: ## main...origin/main  (no M/D/??)
git branch -a           # branch inventory
git log --oneline -5    # expect dbfa9942 at top
git stash list          # WIP stashes if any
```

Expected clean `git status`: no modified, deleted, or untracked files on `main` after consolidation commits.

**Agent verify (bounded):** requires Python deps (e.g. `pandas`); if collection fails with `ModuleNotFoundError`, activate the project venv first.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_agent_verify.ps1
```

---

## Start-here doc order

1. This file — path, branch, merge truth  
2. [docs/human/GETTING_STARTED.md](human/GETTING_STARTED.md) — end-to-end human onboarding  
3. [docs/human/DOC_INDEX.md](human/DOC_INDEX.md) — full doc chronology  
4. [docs/ai/ONBOARDING.md](ai/ONBOARDING.md) — graph-first agent path  
5. [AGENTS.md](../AGENTS.md) — delegation charter (pointer only)

---

## CHI404 remote

Canonical repo also has remote `chi404` → `chi404:/root/hft3/repo` for colo sync. Workstation consolidation does not change CHI404 deploy path; sync via `bash scripts/sync_chi404_repo.sh` after `main` updates.
