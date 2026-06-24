# Repository State

**Last updated:** 2026-06-23
**Purpose:** one current map for agents and operators before pre-VastAI VectorBT/HftBacktest work.

## Canonical Worktree

| Item | Current value |
| --- | --- |
| Canonical repo | `C:\Users\MSI\repos\hft3` |
| Legacy stub | `C:\Users\MSI\Documents\hft3` is redirect-only; do not run git, tests, scripts, or edits there |
| Remote | `https://github.com/javin23863/hft3` |
| Current branch | `codex/feature-plane-mvc-gates` |
| Current PR | `#13` |
| Heavy verification host | CHI404 |
| VastAI role | full paid-compute run only after the CHI404 smoke and ready gate are green |

## Current Pre-VastAI Track

Use this sequence as the live path:

1. Work and commit only from `C:\Users\MSI\repos\hft3`.
2. Run focused Python verification on CHI404.
3. Produce a small paid-compute smoke on CHI404 with real lake hashes and Rust VectorBT runtime proof.
4. Write `runtime/reports/paid_screen_ready_gate.json` with `ready_for_full_run: true`.
5. Deploy to the current VastAI instance with explicit SSH parameters. Do not rely on old host defaults.

Current CHI404 smoke checkout while PR #13 is in flight:

```text
/tmp/hft3-pr13-smoke-c041253f
```

The checkout is a temporary verification workspace. After a new commit lands, recreate or fast-forward a clean CHI404 checkout from the pushed branch before treating smoke receipts as final.

## Active Commands

Focused CHI404 verification:

```powershell
ssh chi404 'cd /tmp/hft3-pr13-smoke-c041253f && PATH=$PWD/.venv/bin:$PATH PYTHONPATH=$PWD:$PWD/packages pytest tests/test_vectorbt_adapter.py tests/test_paid_screen_matrix.py -q'
```

Vast deploy wrapper, after the ready gate is green:

```powershell
.\scripts\vast_deploy_and_verify.ps1 `
  -SshHost root@<current-vast-host> `
  -SshPort <current-vast-port> `
  -GitBranch codex/feature-plane-mvc-gates
```

The deploy wrapper must print `DEPLOY_CONTRACT_PASS`. It now requires an explicit host, port, and current branch or environment variables:

```text
VAST_SSH_HOST
VAST_SSH_PORT
HFT3_VAST_GIT_BRANCH or VBT_GIT_BRANCH
```

## Retired As Current Instructions

These may exist in historical notes, but they are not active instructions:

| Retired item | Current replacement |
| --- | --- |
| previous Vast workflow branch as active branch | `codex/feature-plane-mvc-gates` / PR #13 |
| hardcoded Vast SSH endpoints | explicit `VAST_SSH_HOST` and `VAST_SSH_PORT` for the current instance |
| previous v1 paid-screen runner | `scripts/run_vectorbt_paid_screen_v2.py` or `scripts/run_paid_screen.py` wrapper |
| workstation full-suite or paid-compute runs | CHI404 for smoke, VastAI for full paid-compute |

## Review Gates

Before claiming ready for VastAI:

1. VaultGate and repo `AGENTS.md` loaded.
2. Graph gate status recorded as `waived-by-owner-2026-06-16` until the owner re-enables it.
3. Focused CHI404 tests pass.
4. Small CHI404 paid-compute smoke passes with `failed_work_units == 0`.
5. Ready gate passes.
6. Plan-drift review runs before external GrepLoop/PR review.
7. PR review loop is green with no unresolved actionable comments.
