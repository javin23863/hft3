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

## PR stack (updated after PR-A merge)

| PR | URL | Branch | Base | Status | Greptile |
|----|-----|--------|------|--------|----------|
| **A** | https://github.com/javin23863/hft3/pull/8 | `cursor/autoresearch-gate-chain-pr-a` | `main` | **MERGED** (`8f551b31`, head `88fab454`) | **5/5** Greptile SUCCESS |
| **B** | https://github.com/javin23863/hft3/pull/9 | `cursor/autoresearch-pr-b-paid-screen` | PR-A branch | OPEN — Phase 9 **STOP** (5 iter, no ≥4/5) | head `a3c0cc1e`; 4 P2 inline; **no merge** |
| **C** | https://github.com/javin23863/hft3/pull/10 | `cursor/autoresearch-pr-c-phases-5-7` | PR-B branch | OPEN — **blocked** until B ≥4/5 | not triggered |
| **#7** | https://github.com/javin23863/hft3/pull/7 | `cursor/vast-vbt-workflow` | `main` | **CLOSED** superseded | n/a |

---

## Next plan step

1. ~~Merge PR-A~~ **done** 2026-06-20T05:56:40Z
2. **Greptile loop PR-B (#9)** — cavecrew-reviewer → scoped pytest → `@greptileai` (max 5 iterations)
3. After B ≥4/5 → Greptile on PR-C (#10) → Phase 10 checklist

Log: [greptile_pr9_loop_20260620.md](./greptile_pr9_loop_20260620.md)
