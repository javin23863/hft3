# Greptile 3-PR split execution — 2026-06-20

## Greptile-only policy (assignment §23)

| Reviewer | Satisfies PR GrepLoop? |
|----------|------------------------|
| **Greptile** (`@greptileai`, ≤100 changed files per PR) | **Yes — only this counts** |
| `@codex review` / `request-codex-review` GitHub Action | **No** |
| ChatGPT-Codex-Connector | **No** |
| Agent / cavecrew self-review | **No** (separate gate) |

Canonical doc: [docs/ai/GREPLOOP.md](../../docs/ai/GREPLOOP.md)

Trigger **one PR at a time**: `@greptileai` on PR-A first; wait for ≥4/5 + 0 actionable before B, then C.

---

## PR stack (updated 2026-06-21)

| PR | URL | Branch | Base | Status | Greptile |
|----|-----|--------|------|--------|----------|
| **A** | https://github.com/javin23863/hft3/pull/8 | `cursor/autoresearch-gate-chain-pr-a` | `main` | **MERGED** (`fb00aa25` squash lineage) | merged despite incomplete Greptile; **not** a valid waive-by-merge pass |
| **B** | https://github.com/javin23863/hft3/pull/9 | `cursor/autoresearch-pr-b-paid-screen` | PR-A branch | **MERGED** (`fb00aa25` into PR-A) | **waived-by-owner-20260620** (5 iter exhausted; empty review on `a3c0cc1e`) |
| **C** | https://github.com/javin23863/hft3/pull/10 | `cursor/autoresearch-pr-c-phases-5-7` | PR-B branch | OPEN — `751321d1` Greptile 3/5; WFC/cached-cert diagnostic follow-up locally fixed and pending push/rerun | Current actionable fixed locally: default robustness WFC matrix-row hydration; cached HFT failing-cert diagnostics; prior acceptance `to_dict`, declared-cert cache, and Phase 6 local-fixture fixes remain covered |
| **#7** | https://github.com/javin23863/hft3/pull/7 | `cursor/vast-vbt-workflow` | `main` | **CLOSED** superseded | n/a |

---

## Next plan step

1. ~~Merge PR-A~~ **done** 2026-06-20T05:56:40Z
2. ~~Greptile loop PR-B (#9)~~ **waived + merged** 2026-06-20T08:57:24Z — log: [greptile_pr9_loop_20260620.md](./greptile_pr9_loop_20260620.md)
3. **PR-C (#10) final gate** — current post-`751321d1` WFC/cached-cert diagnostic fix batch → push → Greptile last
4. Phase 10 checklist **blocked** until PR-C current-head gate resolves or owner explicitly waives/substitutes it

---

## Scope notes

- **PR-A:** Gate chain Phases 0–4 + Greptile-only GrepLoop doc + `run_paid_screen.py` wrapper
- **PR-B:** Paid-screen v2 + Vast deploy contract (file-scope from `cursor/vast-vbt-workflow`)
- **PR-C:** Phase 6/7 tests, three-gen acceptance, WF adapter contract tests, split plan doc

---

## Validation honesty

```text
merge-ready: no (post-`751321d1` WFC/cached-cert diagnostic fix batch pending push/rerun)
scope-green: yes for PR-C current WFC/cached-cert diagnostic Greptile fix batch
verify-run: Vast AI `/root/hft3/pr10-followup-vast` exit 0 — editable install passed; workbench console script from `/tmp` passed; setup/WFC/UI slice 45 passed; broad vectorbt/latency 370 passed; runner callback/direct-handle/lifecycle follow-up 7 passed, 2 warnings; forbidden private/accessor grep clean. Local runner callback/direct-handle/lifecycle follow-up: 7 passed, 2 warnings; forbidden private/accessor grep clean; workbench console script from temp cwd passed. `dd96da89` Greptile fix batch: focused issue slice local+Vast 2 passed; local generation loop 13 passed; local paid-screen batch/performance 68 passed; Vast touched suite 81 passed, 23 warnings; Vast research+backtest 584 passed / 3 skipped; Vast paid-screen gap 368 passed; Banach reviewer 0🔴0🟡. `03cf84aa` Greptile fix batch: focused generation-loop 2 passed; focused HBT realism 1 passed; touched generation-loop 14 passed; touched HBT realism 13 passed; Heisenberg reviewer 0🔴0🟡. `22b3b9ae` Greptile fix batch: Phase 7 acceptance `--strict-markers` 1 passed; Phase 7 acceptance normal 1 passed; editable install rebuilt successfully; installed workbench console script from temp cwd passed; Russell reviewer 0🔴0🟡. Current `751321d1` Greptile WFC/cached-cert diagnostic fix batch: focused issue slice 4 passed; generation loop + gate integration 41 passed; scoped research+backtest 589 passed, 40 warnings; paid-screen gap 367 passed, 3 skipped, 27 warnings; `git diff --check` clean; Kuhn reviewer 0 red / 0 yellow.
data-mode: offline + live GitHub API
known-gaps: post-`751321d1` WFC/cached-cert diagnostic fix batch pending push + Greptile rerun
pr-greptile-review: PR-B waived-by-owner-20260620; PR-C pending rerun after current WFC/cached-cert diagnostic fix batch
```
