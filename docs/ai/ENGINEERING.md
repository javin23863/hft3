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
Spec → GraphPre → Plan → Delegate → Review → Verify → GraphPost
```

- **GraphPre:** `graphify query` before locating code.
- **Review:** dual-pass per [REVIEWER_CHARTER.md](../REVIEWER_CHARTER.md).
- **Verify:** bounded pytest + domain gates — [SHELL_EXECUTION.md](SHELL_EXECUTION.md) (timeouts mandatory).
- **GraphPost:** rebuild `graphify-out/` after code edits.

## Research invariants (non-negotiable)

- Filtration \(F_t\): no lookahead in features or signals.
- Event-time ordering for replay and backtest.
- Walk-forward discipline for promotion (see BLUEPRINT).
- CHI404 topology for live/paper paths.

See [AGENTIC_ENGINEERING.md](../AGENTIC_ENGINEERING.md) for full workflow diagrams.
