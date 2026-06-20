# Greptile zero-tolerance reset — 2026-06-20 (updated)

**Branch:** `cursor/autoresearch-pr-c-phases-5-7` (PR #10)  
**Stack:** PR #8 MERGED · PR #9 MERGED · PR #10 OPEN  
**Policy:** owner zero-tolerance — unlimited iterations until 0 P1 + 0 P2 + 0 🔴 + 0 🟡 + scoped pytest green  

### Gate-order compliance

| Check | Result |
|-------|--------|
| `85eb27bd` push | pytest green **before** push; cavecrew on 85eb27bd diff **NOT RUN** (violation) |
| Remediation batch | cavecrew dual-pass on fix diff → **0🔴 0🟡** before push |
| Prior `078cecae` | validator migration + cockpit eligibility tests (already on remote) |
| Greptile | ping **after** remediation push only |

---

## Session heads

| SHA | Note |
|-----|------|
| `85eb27bd` | gen2 recipe + cscv structure_ran — pushed without cavecrew |
| `a3433804` | screening validator migration |
| `078cecae` | cavecrew batch (validator + cockpit tests) |
| **pending** | holdout fail-closed + replay pairing + recipe delta fixes |

---

## verify-run (this session)

```text
.\.venv\Scripts\python.exe -m pytest tests/research_pipeline/ tests/backtest_pipeline/ -q
exit 0 — 570 passed, 41 warnings in ~394s
```

### Fixes in remediation batch (uncommitted → push)

| Finding | Fix |
|---------|-----|
| Gate 4 missing holdout | `holdout_evaluate_only_missing:{name}` when configured band absent |
| Synthetic robustness pass | removed `robustness_passed` → dsr/pbo/cscv pass injection |
| Recipe dimension gen2 | require hash/recipe inequality (no label-only OR) |
| Alien replay fallback | `_latest_paired_replay_artifact` fail-closed on pair miss |
| vectorbt_screen OK | requires `replay_eligibility_status==eligible` |
| structure_ran gate test | `test_statistical_gate_rejects_structure_ran_cscv_status` |
| Phase 7 honesty | `@pytest.mark.fixture_dry_run` + mode assert |

---

## Validation honesty

```text
merge-ready: no (Greptile pending after remediation push)
scope-green: yes (research_pipeline + backtest_pipeline 570/570 pass, exit 0)
scope: tests/research_pipeline/ + tests/backtest_pipeline/
verify-run: exit 0 — 570 passed in ~394s (.venv, 2026-06-20)
data-mode: offline pytest + GitHub API poll
known-gaps: Greptile bot pending on new head; Phase 10 blocked
finding-count: cavecrew 0🔴 0🟡 on remediation diff; Greptile actionable TBD
```
