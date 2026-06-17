# Engineering principles (Karpathy agentic)

Canonical coding style for all human and AI contributors. [AGENTS.md](../../AGENTS.md) references this document.

## 1. Think before coding

- State assumptions explicitly.
- Surface tradeoffs; ask when uncertain — do not silently guess.
- Convert vague goals into verifiable success criteria.

## 2. Simplicity first

- Minimum code that solves the problem.
- No speculative abstractions, extra config, or unrequested features.
- If a senior engineer would call it overcomplicated, simplify.

## 3. Surgical changes

- Edit only what the task requires.
- Match existing naming, types, and style.
- Every changed line traces to the request.

## 4. Goal-driven execution

- Prefer "write a failing test, then make it pass" over "make it work."
- Loop Spec → Plan → Code → Verify until criteria met or blocked.
- Do not claim done without test output evidence.

## Agent workflow (mandatory)

```
Spec -> Plan -> Delegate -> Code -> Local Preflight -> Review -> Verify -> PR GrepLoop
```

- **GraphPre/GraphPost:** `waived-by-owner-2026-06-16`; do not run graphify commands until the owner lifts the temporary waiver.
- **Local preflight:** mandatory bounded task-specific `rg` loop after every repo edit; Codex self-review is not enough; see [GREPLOOP.md](GREPLOOP.md).
- **PR GrepLoop:** Greptile-backed PR/MR/CL loop only. A local `rg` pass is not GrepLoop.
- **AI coding delegate:** when token pressure is high, use [CODING_DELEGATE.md](CODING_DELEGATE.md) to draft code from exact supplied context only; Codex still reviews, applies, verifies, and runs PR GrepLoop.
- **Review:** dual-pass per [REVIEWER_CHARTER.md](../REVIEWER_CHARTER.md).
- **Verify:** bounded pytest + domain gates — [SHELL_EXECUTION.md](SHELL_EXECUTION.md) (timeouts mandatory).

## Research invariants (non-negotiable)

- Filtration \(F_t\): no lookahead in features or signals.
- Event-time ordering for replay and backtest.
- Walk-forward discipline for promotion (see BLUEPRINT).
- CHI404 topology for live/paper paths.

See [AGENTIC_ENGINEERING.md](../AGENTIC_ENGINEERING.md) for full workflow diagrams.
