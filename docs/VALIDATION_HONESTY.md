# Validation honesty (hft3 repo-wide)

Every agent handoff in this repo must report verification status honestly. Applies to **all** packages, lanes, infra, scripts, and research paths.

Cross-links: [AGENTS.md](../AGENTS.md), [docs/AGENTIC_ENGINEERING.md](AGENTIC_ENGINEERING.md), [scripts/check_handoff_status.py](../scripts/check_handoff_status.py), [.cursor/rules/validation-honesty.mdc](../.cursor/rules/validation-honesty.mdc).

## Required status block

Every handoff must include this block. **Paste the last 5–20 lines of verify output** (or gate script tail) in `verify-run` when not waived.

```
merge-ready:     yes | no
scope-green:     yes | no | not-run
scope:           <touched path prefix or lane name>
verify-run:      <full command> → exit <code>; <summary tail> | WAIVED (user: …) | not-run
plan-drift:      pass | fail | not-run
data-mode:       fixture | production | live | mixed | n/a
pr-ai-review:    run | unavailable(no-pr|no-connector|not-authenticated) | waived-by-user
review-surface:  <PR/MR/CL URL or id>; head=<sha>; split-needed yes|no | none(blocked: <reason>) | none(waived-by-user: <reason>)
known-gaps:      <list> | none | unverified (verify waived)
```

**Rules:**

- `merge-ready: yes` requires `scope-green: yes`, verify-run showing **exit 0**, `plan-drift: pass`, reviewer merge-ready, and either graph rebuilt when graph gates are active or `graph-gate: waived-by-owner` reported while the temporary waiver is active.
- For PR AI, `merge-ready: yes` requires either `pr-ai-review: run` with a current-head PR/MR/CL review surface using `head=<sha>`, or `pr-ai-review: waived-by-user` with `review-surface: none(waived-by-user: <reason>)`.
- `unavailable(no-pr|no-connector|not-authenticated)` means the workflow is blocked; it is not a normal successful GrepLoop state.
- `known-gaps: none` requires **scope-green: yes** and **no open items** in any lane addendum below.
- User waived verify → `known-gaps: unverified (verify waived)` (never `none` or `none declared`).

Example (partial work):

```
merge-ready:     no
scope-green:     no (5 failed / 39 in tests/test_crypto_lane/)
scope:           packages/crypto_lane/
verify-run:      python -m pytest tests/test_crypto_lane/ -q → exit 1; FAILED test_smoke_all_candidates.py
plan-drift:      fail
data-mode:       fixture
pr-ai-review:    unavailable(no-pr)
review-surface:  none(blocked: tests failed before PR surface)
known-gaps:      θ convention audit — see packages/crypto_lane/docs/VALIDATION_HONESTY.md
```

Example (user waived verify):

```
merge-ready:     no
scope-green:     not-run
scope:           workbench/src/run/campaign_runner.py
verify-run:      WAIVED (user: code-only pass)
plan-drift:      not-run
data-mode:       n/a
pr-ai-review:    unavailable(no-pr)
review-surface:  none(blocked: user waived verify before PR surface)
known-gaps:      unverified (verify waived)
```

### Cursor / plan todos

Cursor todos only support `pending`, `completed`, `cancelled`. When verify is waived or not run, keep verify todos **`pending`** (do not use `completed`). Document waiver in the handoff block; optional note: `waived-not-verified` in prose.

## Scope-green verify gates

Run the **full scope** below for the area touched. One-file / targeted pytest is **smoke-only**.

| If you touched | Scope-green command (minimum) |
|----------------|----------------------------|
| `packages/crypto_lane/` | `python -m pytest tests/test_crypto_lane/ -q` |
| `packages/equities_lane/` | `python -m pytest tests/test_equities_lane/ -q` |
| `workbench/` | `python -m pytest tests/test_workbench/ -q` |
| `packages/economic_event_universe/` | `python -m pytest tests/test_economic_event_universe/ -q` |
| `packages/research_pipeline/`, `scripts/run_pipeline.py` | `python -m pytest tests/test_research_pipeline.py -q` |
| `data_layer/`, packet schemas | `python -m pytest tests/test_data_layer/ -q` |
| `data_system/` (Databento, events, ingest) | `python -m pytest tests/test_run_event_replay.py tests/test_research_pipeline.py -q` (+ widen if diff touches more) |
| `data_system/rithmic_trial/` | `python -m pytest tests/test_rithmic_trial_pipeline.py tests/test_rithmic_topology_guards.py -q` |
| `options_lane/`, parity | `python -m pytest tests/test_parity_ingest.py tests/test_parity_backtest.py tests/test_parity_engine.py -q` |
| `features_engine/`, `backtest_pipeline/`, `decision_engine/` | `python -m pytest tests/test_feature_parity.py tests/test_regime_pipeline.py tests/test_run_event_replay.py tests/test_replay_clock_order_timestamps.py -q` |
| `scripts/run_event_replay.py`, macro replay | `python -m pytest tests/test_run_event_replay.py tests/test_replay_must_emit_order_intents.py -q` |
| `tests/test_hfc3/` scope | `python -m pytest tests/test_hfc3/ -q` |
| `rithmic_gateway/` | `python -m pytest tests/test_rithmic_topology_guards.py tests/test_execution_interface_parity.py -q` |
| `infrastructure/chi404/`, CHI404 scripts | `python -m pytest tests/test_chi404_canonical_guardrails.py tests/test_chi404_baseline_spec.py tests/test_chi404_memory_upgrade.py -q` **and** `validate_pass_criteria.py` on real log dir when claiming PASS |
| C++ feature hot path | build `hft_feature_golden` + `python -m pytest tests/test_cpp_feature_golden.py -q` |
| `graphify-out/` + code graph tooling | when graph gates are active: `graphify update .` or `scripts/graphify_rebuild.ps1` (exit 0); while owner-waived: report `graph-gate: waived-by-owner` |
| Repo-wide / ambiguous | `python -m pytest -q` or `scripts/run_agent_verify.ps1` (paste summary) |

When multiple scopes change, each must be scope-green or listed as failed/waived.

Entrypoints: [docs/vault/RESEARCH_ENTRYPOINTS.md](vault/RESEARCH_ENTRYPOINTS.md).

## Enforcement

Optional CI / agent verify (when `HANDOFF_STATUS_FILE` is set):

```bash
python scripts/check_handoff_status.py "$HANDOFF_STATUS_FILE" --require
```

`scripts/run_agent_verify.ps1` / `.sh` run this check when `HANDOFF_STATUS_FILE` points to a handoff block file.

## User-waived verify

If the user says **"don't test"** or **"code only"**:

- `verify-run: WAIVED (user: …)` and **`merge-ready: no`**
- Verify todos stay **`pending`**
- No "done", "shipped", or "all todos complete" for verify-gated work

## data-mode honesty

| Mode | Meaning |
|------|---------|
| `fixture` | Bundled fixtures, `fixture_connector`, synthetic logs, dry-run |
| `production` | Trusted lake populated from real ingest — empty lake = config-only |
| `live` | CHI404 colo capture / paper — not workstation-only |
| `mixed` | State which paths are real vs fixture |
| `n/a` | Docs-only, no data path |

Crypto CI default: `validation_mode: fixture` in backtest YAML until `data/crypto/normalized/` is populated.

## Measurement and gate honesty

| Claim | Requires |
|-------|----------|
| CHI404 PASS | `validate_pass_criteria.py` + `PASS_FAIL.txt` on **real** log dir |
| Colo latency authority | CHI404 `latency_summary.json` from probes |
| Rithmic trial live validated | CHI404 capture + replay artifacts |
| Live venue RTT | Measured ping/pong artifact (`source` not `synthetic_calibrated:*`) |
| Workbench latency viable | C++ CHI404 distributions per [LATENCY_ARCHITECTURE.md](workbench/LATENCY_ARCHITECTURE.md) |
| Backtester certified | T0–T4 per [BACKTESTER_CERTIFICATION.md](vault/BACKTESTER_CERTIFICATION.md) |

## Spec vs implementation

Spec docs state **target semantics**, not completed code. Each lane addendum lists **known gaps**. Do not claim e2e compliance while addendum items remain open.

| Lane / area | Known-gaps addendum |
|-------------|---------------------|
| Crypto PIT | [packages/crypto_lane/docs/VALIDATION_HONESTY.md](../packages/crypto_lane/docs/VALIDATION_HONESTY.md) |
| Workbench | [docs/workbench/VALIDATION_ADDENDUM.md](workbench/VALIDATION_ADDENDUM.md) |
| Equities | [packages/equities_lane/docs/VALIDATION_ADDENDUM.md](../packages/equities_lane/docs/VALIDATION_ADDENDUM.md) |
| Rithmic trial | [docs/rithmic_trial/VALIDATION_ADDENDUM.md](rithmic_trial/VALIDATION_ADDENDUM.md) |
| CHI404 infra | [docs/chi404/VALIDATION_ADDENDUM.md](chi404/VALIDATION_ADDENDUM.md) |

## Forbidden phrases (when scope-green is no, verify waived, or gaps remain)

- "all todos complete" / "all plan todos complete"
- "merge-ready: yes" / "shipped" / "shipped per plan"
- "A+ implementation" / "implemented" (without status block)
- "real-data wired" (empty production lake)
- "CHI404 PASS" / "colo validated" (without validate script on real logs)
- "live probe succeeded" / "WebSocket RTT probe" (for `synthetic_calibrated:*`)
- "PIT-complete" / "e2e compliant" (open addendum gaps)

**Allowed:** "code written", "spec updated", "smoke-only pass (N/N on `<file>`)", "blocked on …".
