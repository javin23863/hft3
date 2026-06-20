# Greptile 3-PR split execution — 2026-06-20

## Greptile-only policy (assignment §23)

| Reviewer | Satisfies PR GrepLoop? |
|----------|------------------------|
| **Greptile** (`@greptileai`, ≤100 changed files per PR) | **Yes — only this counts** |
| `@codex review` / `request-codex-review` GitHub Action | **No** |
| ChatGPT-Codex-Connector | **No** |
| Agent / cavecrew self-review | **No** (separate gate) |

Canonical doc: [docs/ai/GREPLOOP.md](../../docs/ai/GREPLOOP.md)

Trigger **one PR at a time**: `@greptileai` on PR-A first; wait for 0 actionable before B, then C.

---

## PR stack

| PR | URL | Branch | Base | Head SHA | Files (PR diff) | vs `main` | Greptile | merge-ready |
|----|-----|--------|------|----------|-----------------|-----------|----------|-------------|
| **A** | https://github.com/javin23863/hft3/pull/8 | `cursor/autoresearch-gate-chain-pr-a` | `main` | `84ca400d` | **52** | 52 | `@greptileai` posted 2026-06-20 — **pending re-review** | **no** |
| **B** | https://github.com/javin23863/hft3/pull/9 | `cursor/autoresearch-pr-b-paid-screen` | PR-A branch | `26d57b98` | **77** | 123 (cumulative) | **wait for A** — do not trigger yet | **no** |
| **C** | https://github.com/javin23863/hft3/pull/10 | `cursor/autoresearch-pr-c-phases-5-7` | PR-B branch | `521b3502` | **8** | 131 (cumulative) | **wait for B** | **no** |

**Review first:** PR-A (#8). Stacked diffs keep each Greptile surface ≤80 files (≤100 hard limit).

---

## PR-A Greptile fixes verified (`d2a6909a`)

| Finding | Location | Fix |
|---------|----------|-----|
| P1 resume NameError | `generation_loop.py:745-746` | `candidate.metadata` (was `c.metadata`) — `9ed376db` |
| P1 pre-HFT false negative | `generation_gate_chain.py:264-267` | `stopped_at_gate is None` → True — `9ed376db` |
| P2 staleness vs failed_check_count | `generation_gate_producers.py:74,476-480` | in `_STATISTICAL_REQUIRED_CHECKS` — `9ed376db` |
| P2 unused parent_params | `elite_refinement.py` | removed — `9ed376db` |
| P2 private `_` imports | `generation_gate_producers.py` | public aliases in `vectorbt_adapter.py` — `d2a6909a` |

**verify-run:** `pytest tests/research_pipeline/ -q` → **210 passed**, exit 0 (`.venv`, 2026-06-20)

**PR #8 comment:** re-review request with line refs posted; user resolves threads manually if bot does not auto-close.

---

## PR #7 handling

- **Status:** OPEN — **not closed** by agent
- **Comment posted:** superseded by #8 / #9 / #10; recommend close (147 files > Greptile 100-file block)
- Link: https://github.com/javin23863/hft3/pull/7#issuecomment-4756599387

---

## Scope notes

- **PR-A:** Gate chain Phases 0–4 + Greptile-only GrepLoop doc + `run_paid_screen.py` wrapper
- **PR-B:** Paid-screen v2 + Vast deploy contract (file-scope from `cursor/vast-vbt-workflow`)
- **PR-C:** Phase 6/7 tests, three-gen acceptance, WF adapter contract tests, split plan doc

---

## Validation honesty

```text
merge-ready: no (all three PRs)
scope-green: PR-A research_pipeline pytest only (210 pass); PR-B/C verify not run this session
verify-run: PR-A — pytest tests/research_pipeline/ -q exit 0, 210 passed
data-mode: offline
known-gaps: Greptile re-review pending on #8; #9/#10 await stacked merge + Greptile; PR #7 still open
pr-ai-review: @greptileai triggered on #8 only; Codex Action advisory only
```

**merge-ready yes** only after Greptile posts 0 actionable on each PR head in order A → B → C.
