# Shell execution — time-bounded runs (agents + humans)

**Problem:** Background pytest, SSH, and subprocess jobs that never exit waste machine resources and hide failures. Agents must treat **no output within budget** as **hung**, stop the job, and report blocked status.

**Canonical for:** Cursor agents, `shell` subagent, CI-adjacent local verify. Cross-ref: [AGENTS.md](../../AGENTS.md) · [ENGINEERING.md](ENGINEERING.md) · [.cursor/rules/shell-execution-timeouts.mdc](../../.cursor/rules/shell-execution-timeouts.mdc)

---

## Rules (non-negotiable for agents)

1. **Every shell command has a time budget** — pick from the table below before running; never “background and poll forever.”
2. **No progress = hung** — if wall time exceeds **2× expected** with no new stdout/stderr, **stop** the process and diagnose.
3. **Never leave orphan processes** — on timeout or user abort, kill the job tree (pytest workers, `replay-sample`, SSH wait loops, duplicate Streamlit).
4. **Prefer bounded scripts** in this repo over raw long commands (see [Bounded verify commands](#bounded-verify-commands)).
5. **Report blocked honestly** — state: command, budget, elapsed, last log lines, and what unblocks (fix path, skip test, run on CHI404).

### Forbidden patterns

| Pattern | Why |
|---------|-----|
| `ssh chi404 'while ! grep …; do sleep 30; done'` without `-o ConnectTimeout` and a max wall clock | Can run overnight |
| Full `pytest tests/` in background with repeated `Await` polls | Hangs hide inside slow/integration tests |
| Spawning Streamlit/workbench without tracking PID/port | Orphans on old import paths |
| Claiming “tests pass” without captured exit code + summary line | Unverifiable |

---

## Time budgets

| Workload | Expected | Hard stop | Notes |
|----------|----------|-----------|-------|
| T0 fast gate | ≤30s | **90s** | `tests/backtester_validation/fast` |
| Registry + workbench (excl. CPI e2e) | ≤90s | **180s** | `scripts/run_agent_verify.ps1` |
| Single test file | ≤30s | **120s** | Add `--timeout=120` for integration |
| Full `pytest tests/` | ≤5min | **600s** | Run only when requested; exclude known slow tests |
| `test_cpi_e2e` / full replay | minutes | **900s** | Explicit user approval |
| Graphify wiki index | ≤10s | **60s** | `python tools/graphify/build_wiki_index.py` |
| Graphify AST rebuild | ≤3min | **300s** | `scripts/graphify_rebuild.ps1` |
| SSH one-shot (grep, tail, status) | ≤5s | **60s** | Always `-o ConnectTimeout=15` |
| CHI404 remote poll (sweep/log tail) | varies | **900s** | One tail + status; no infinite wait loops |
| Rithmic `replay-sample` smoke | ≤30s | **120s** | Subprocess; kill on exceed |

---

## Hung detection

```
start = now()
run command with hard stop (script or timeout wrapper)
if exit code = timeout:
  report BLOCKED: hung
  include last 20 lines of output
if elapsed > 2 * expected AND no output delta for 60s:
  treat as hung even if process alive — stop and report
```

Agents using Cursor **background shells** must set `block_until_ms` to the **hard stop** (not unbounded), then read terminal file once. Do not poll more than **3 times** without new output.

---

## Bounded verify commands

From repo root (Windows):

```powershell
# Preferred agent/human gate (~3 min cap; T0 + registry + workbench, not full T2 replay)
powershell -File scripts/run_agent_verify.ps1

# Generic wrapper (call with & — not -File … --)
& tools/shell/run_with_timeout.ps1 -TimeoutSec 90 -Label "t0-fast" -- python -m pytest tests/backtester_validation/fast -q
```

Linux / CHI404 / macOS:

```bash
bash scripts/run_agent_verify.sh
bash tools/shell/run_with_timeout.sh 90 t0-fast -- python -m pytest tests/backtester_validation/fast -q
```

---

## SSH (CHI404)

```bash
ssh -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 chi404 'command'
```

- **One-shot diagnostics** only from the dev workstation.
- Long sweeps run **on CHI404** via `nohup` + log file; workstation pulls **one** `tail`/`grep`, not an open-ended wait loop.
- See [BLUEPRINT.md](../../BLUEPRINT.md) §4 and [docs/rithmic_trial/README.md](../rithmic_trial/README.md).

---

## Cleanup after abort

```powershell
# Inspect (repo root)
Get-CimInstance Win32_Process -Filter "name='python.exe'" |
  Select-Object ProcessId, CommandLine

# Stop stale agent pytest/streamlit (adjust PIDs after inspect)
Stop-Process -Id <pid> -Force
```

Document what was killed in the agent thread when cleaning up after a hung run.

---

## Related entrypoints

| Doc | Purpose |
|-----|---------|
| [ONBOARDING.md](ONBOARDING.md) step 4 | Verification table with bounded commands |
| [CONTRIBUTING.md](../../CONTRIBUTING.md) | PR checks |
| [docs/vault/BACKTESTER_CERTIFICATION.md](../vault/BACKTESTER_CERTIFICATION.md) | T0–T4 tiers |
