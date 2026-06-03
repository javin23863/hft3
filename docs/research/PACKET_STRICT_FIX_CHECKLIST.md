# Packet-strict LLM — fix checklist

Use this to **verify** shipped work and **close gaps** from grader review (2026-05-31).  
Plan: [packet-strict_llm_fix](../../.cursor/plans/packet-strict_llm_fix_16432bbd.plan.md) · Contract: [PACKET_LLM_CONTRACT.md](PACKET_LLM_CONTRACT.md)

**Legend:** `[ ]` open · `[x]` verified · `[-]` optional / best-effort

---

## Merge-ready gates (all must pass)

| Gate | Command / check | Status |
|------|-----------------|--------|
| No Hawkish in active tree | `grep -ri hawkish packages/ scripts/ docs/ tests/` → empty | [x] |
| jsonschema installable on fresh setup | `pip install -e .` or requirements include `jsonschema>=4.20` | [x] |
| Mock AAR pytest | `pytest tests/test_data_layer/test_packet_*.py tests/test_data_layer/test_after_action.py -q -m "not slow"` | [x] |
| Pipeline pytest | `pytest tests/test_research_pipeline.py -q` | [x] |
| Cert fast gate | `pytest tests/backtester_validation/fast -q` | [x] |
| Runtime schemas in sync | `powershell -File scripts/sync_runtime_schemas.ps1` then `test_sync_runtime_schemas.py` | [x] |
| Dual-pass reviewer | cavecrew-reviewer Pass A+B on full diff; **0 red** | [ ] |
| graphify post-edit | `graphify update .` | [x] |

---

## P0 — Blockers (financial / contract correctness)

### P0-1 Pipeline LLM is not packet-strict today

**Problem:** `run_llm_on_pipeline_request()` has **zero callers**. Hypothesis parse uses loose `generate_json()` fence parsing.

| Task | File(s) | Status |
|------|---------|--------|
| Wire NL hypothesis stage through `run_llm_on_hypothesis_request` | `hypothesis_parser.py`, `packet_runner.py` | [x] |
| Deprecate loose `generate_json()` when `pipeline_request` + `repo_root` provided | `hypothesis_parser.py`, `research_pipeline/llm.py` | [x] |
| Test: mock GPT-5.5 hypothesis response validates | `tests/test_research_pipeline.py` | [x] |

**Verify:** `grep -r run_llm_on_hypothesis_request packages/` shows caller in `hypothesis_parser.py`.

---

### P0-2 Every persisted response must validate

**Problem:** `_finalize_aar_response()` mutates invalid objects but may still write schema-invalid JSON to disk.

| Task | File(s) | Status |
|------|---------|--------|
| On out-validation fail: rebuild via `_base_aar_response` / `_ensure_aar_response` | `packet_runner.py` | [x] |
| After write: assert `validate_aar_packet_out()` empty in `after_action.py` | `after_action.py` | [x] |
| Pipeline error envelopes pass `validate_pipeline_response()` | `packet_runner.py` | [x] |
| Test: skip + schema_reject paths produce valid response JSON | `tests/test_data_layer/test_packet_runner.py` | [x] |

**Verify:** No code path writes `after_action_response.json` without passing `validate_aar_packet_out`.

---

### P0-3 jsonschema in all install paths

**Problem:** Only `pyproject.toml` pins `jsonschema`; GETTING_STARTED / workbench reqs omit it.

| Task | File(s) | Status |
|------|---------|--------|
| Add `jsonschema>=4.20` to workbench requirements + `pip install -e .` in GETTING_STARTED | done | [x] |

**Verify:** Clean venv + documented install → `import jsonschema` works before after-action.

---

### P0-4 OpenFoundry gate must fail closed

**Problem:** `validate_connector()` returns dict; rarely raises. Duplicate call in packet builder.

| Task | File(s) | Status |
|------|---------|--------|
| Add `assert_connector_valid(result)` | `openfoundry_bridge.py` | [x] |
| Call before LLM via `_connector_gate` | `packet_runner.py` | [x] |
| Test: pending lock → `skipped_connector` | `tests/test_data_layer/test_after_action.py` | [x] |

**Verify:** Invalid `VENDOR.lock` (`openfoundry=pending`) blocks LLM in integration test.

---

## P1 — High severity (consistency / audit trail)

### P1-1 Disk packet vs LLM input (Option B audit)

**Problem:** `after_action_packet.json` written before `similar_prior_runs`; LLM gets envelope wrapper not in schema.

| Task | File(s) | Status |
|------|---------|--------|
| Attach `similar_prior_runs` before writing `after_action_packet.json` | `after_action.py` | [x] |
| Document envelope in `PACKET_LLM_CONTRACT.md` | docs | [x] |

**Verify:** `after_action_packet.json` on disk matches `microstructure_aar_packet` field sent to LLM (except envelope-only fields).

---

### P1-2 Promotion clamp vs latency packet fields

**Problem:** LLM can recommend promote when `lane_pass` / WFC / robustness false.

| Task | File(s) | Status |
|------|---------|--------|
| Extend `_clamp_promote_recommendation` for lane_pass / robustness / wfc | `packet_runner.py` | [x] |
| Test: `lane_pass: false` clamps recommendation | `tests/test_data_layer/test_packet_runner.py` | [x] |

**Verify:** Pass B cites BLUEPRINT §4 / LATENCY_ARCHITECTURE promotion rules.

---

### P1-3 Pipeline telemetry honesty

**Problem:** `--no-llm` dry/full run sets `llm_status: ok`.

| Task | File(s) | Status |
|------|---------|--------|
| `skipped_no_llm` in pipeline response schema + CLI | `schema_pipeline_response_v1.json`, `run_pipeline.py` | [x] |

**Verify:** `--dry-run --no-llm` → response packet does not claim GPT-5.5 success.

---

### P1-4 Cloud JSON mode spike (Phase 0)

| Task | File(s) | Status |
|------|---------|--------|
| Add OpenAI-compatible JSON-mode spike (GPT-5.5, CPI-sized fixture) | new script | [ ] |
| Log parse rate, elapsed_s, context size estimate | script output / docs | [ ] |
| Document spike results in checklist or PACKET_LLM_CONTRACT | docs | [ ] |

**Verify:** Script runs (skip if GPT-5.5 endpoint is unconfigured) without crashing.

---

### P1-5 Output token budget vs Option B

| Task | File(s) | Status |
|------|---------|--------|
| Raise output-token budget for AAR or env `HFT3_AAR_NUM_PREDICT` | `openai_compatible_client.py`, `.env.example` | [ ] |
| Test with fixture at upper trade count (or document known limit) | tests / docs | [ ] |

---

### P1-6 Unused / dead API cleanup

| Task | File(s) | Status |
|------|---------|--------|
| Use or remove `deploy_best(..., request=)` | `deployment.py`, `run_pipeline.py` | [ ] |
| Remove unused `request` param if packets written only in CLI | `deployment.py` | [ ] |

---

## P2 — Completeness (gates, UI, paths)

### P2-1 Hybrid / full pipeline gates

| Task | File(s) | Status |
|------|---------|--------|
| Assert `after_action_response.json` in hybrid gate | `run_hybrid_pipeline_gate.py` | [ ] |
| Assert in full pipeline gate | `run_full_pipeline_gate.py` | [ ] |
| Test sync script | `tests/test_data_layer/test_sync_runtime_schemas.py` | [x] |

---

### P2-2 Analyst chat (deferred lane)

| Task | File(s) | Status |
|------|---------|--------|
| Either: schema-strict analyst I/O **or** document chat as non-contract in PACKET_LLM_CONTRACT | `analyst_panel.py`, docs | [ ] |
| Replace truncated `_compact_context` with response packet primary | `analyst_panel.py` | [ ] |

---

### P2-3 Path unification

| Task | File(s) | Status |
|------|---------|--------|
| Document `research_cards/` vs `artifacts/research_cards/` for pipeline runs | `RUNTIME_CONTRACT.md` | [ ] |
| Optional: write pipeline runs under `artifacts/research_cards/pipeline_runs/` | `run_pipeline.py`, `paths.py` | [ ] |

---

### P2-4 Live smoke artifacts

| Task | File(s) | Status |
|------|---------|--------|
| Regenerate `AAR_SMOKE_TEST` with GPT-5.5 + valid `after_action_response.json` | `artifacts/research_cards/workbench_runs/AAR_SMOKE_TEST/` | [-] |
| Update `after_action_meta.json` model field | smoke dir | [-] |

---

### P2-5 Stale plan hygiene

| Task | File(s) | Status |
|------|---------|--------|
| Strip Hawkish body from superseded plan (keep pointer only) | `.cursor/plans/hft_after-action_llm_layer_*.plan.md` | [ ] |
| Mark `gemma_llm_unification` plan superseded | `.cursor/plans/` | [ ] |

---

## Already shipped (re-verify, do not re-implement blindly)

| Item | Verify command |
|------|----------------|
| AAR uses `packet_runner` | `grep run_llm_on_aar_packet packages/data_layer/pipeline/after_action.py` |
| Canonical response artifact | `grep after_action_response campaign_runner.py flow_state.py` |
| GPT-5.5 default | `grep gpt-5.5 packages/data_layer/llm/openai_compatible_client.py` |
| Research pipeline default | `grep DEFAULT_RESEARCH_MODEL packages/research_pipeline/llm.py` |
| Cross-field invariants | `pytest tests/test_data_layer/test_packet_schemas.py::test_aar_packet_in_fixture` |
| Hybrid latency_authority string | `grep workstation_replay packages/backtest_pipeline/src/pipeline_aar_artifacts.py` |

---

## Suggested fix order

1. P0-3 (deps) → P0-2 (validate on write) → P0-4 (OpenFoundry) → P0-1 (pipeline wire-up)  
2. P1-1, P1-2, P1-3 in parallel  
3. P1-4 spike + P1-5 token budget  
4. P2 gates + tests → reviewer → graphify → live smoke (optional)

---

## Sign-off

| Role | Name | Date | Notes |
|------|------|------|-------|
| Implementer | | | |
| Reviewer Pass A | | | |
| Reviewer Pass B (BLUEPRINT/PDF cites) | | | |
| Merge-ready | **no** — pending reviewer + graphify; P1-4 spike optional | | |
