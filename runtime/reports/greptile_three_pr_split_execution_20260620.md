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

## PR stack (updated 2026-06-20T08:57Z)

| PR | URL | Branch | Base | Status | Greptile |
|----|-----|--------|------|--------|----------|
| **A** | https://github.com/javin23863/hft3/pull/8 | `cursor/autoresearch-gate-chain-pr-a` | `main` | **MERGED** (`fb00aa25` squash lineage) | **waived-by-merge** (last 3/5 on head) |
| **B** | https://github.com/javin23863/hft3/pull/9 | `cursor/autoresearch-pr-b-paid-screen` | PR-A branch | **MERGED** (`fb00aa25` into PR-A) | **waived-by-owner-20260620** (5 iter exhausted; empty review on `a3c0cc1e`) |
| **C** | https://github.com/javin23863/hft3/pull/10 | `cursor/autoresearch-pr-c-phases-5-7` | PR-B branch | OPEN — Phase 9 Greptile in progress | pending |
| **#7** | https://github.com/javin23863/hft3/pull/7 | `cursor/vast-vbt-workflow` | `main` | **CLOSED** superseded | n/a |

---

## Next plan step

1. ~~Merge PR-A~~ **done** 2026-06-20T05:56:40Z
2. ~~Greptile loop PR-B (#9)~~ **waived + merged** 2026-06-20T08:57:24Z — log: [greptile_pr9_loop_20260620.md](./greptile_pr9_loop_20260620.md)
3. **Greptile loop PR-C (#10)** — cavecrew-reviewer → scoped pytest → `@greptileai` (max 5 iterations)
4. Phase 10 checklist **blocked** until PR-C Greptile resolves or waives

---

## Scope notes

- **PR-A:** Gate chain Phases 0–4 + Greptile-only GrepLoop doc + `run_paid_screen.py` wrapper
- **PR-B:** Paid-screen v2 + Vast deploy contract (file-scope from `cursor/vast-vbt-workflow`)
- **PR-C:** Phase 6/7 tests, three-gen acceptance, WF adapter contract tests, split plan doc

---

## Validation honesty

```text
merge-ready: no (PR-C pending Greptile)
scope-green: PR-B tests/research_pipeline/ 213 passed on a3c0cc1e; PR-C verify pending this pass
verify-run: PR-B — pytest tests/research_pipeline/ -q exit 0, 213 passed
data-mode: offline + live GitHub API
known-gaps: PR-C rebase onto PR-B head; Greptile not started on #10 until rebase push
pr-greptile-review: PR-B waived-by-owner-20260620; PR-C pending
```
