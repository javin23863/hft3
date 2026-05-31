# LLM migration checklist (packet-strict)

**Fix / verify against:** [PACKET_STRICT_FIX_CHECKLIST.md](PACKET_STRICT_FIX_CHECKLIST.md) (authoritative open items)  
**Plan:** [packet-strict_llm_fix](../../.cursor/plans/packet-strict_llm_fix_16432bbd.plan.md)  
**Contract:** [PACKET_LLM_CONTRACT.md](PACKET_LLM_CONTRACT.md)

---

## Status summary (honest)

| Phase | Claimed | Grader verdict |
|-------|---------|----------------|
| Schemas + validate.py | done | done — re-verify invariants |
| AAR packet_runner | done | mostly done — P0-2, P0-4, P1-1, P1-2 remain |
| Pipeline packet-strict | done | **not done** — P0-1 (dead `run_llm_on_pipeline_request`) |
| Docs / Hawkish purge | done | done in active docs |
| Tests | done | partial — missing schema-on-write + pipeline LLM tests |
| Reviewer + spike | in progress | **not done** |

**Merge-ready: no** until [PACKET_STRICT_FIX_CHECKLIST.md](PACKET_STRICT_FIX_CHECKLIST.md) P0 section is complete.

---

## Quick grep gates

```powershell
# Must be empty (active code + docs; exclude .cursor/plans if archived)
grep -ri hawkish packages/ scripts/ docs/ tests/

# Pipeline packet runner must be called (after P0-1 fix)
grep -r run_llm_on_pipeline_request packages/ scripts/

# Dead code check (before fix: only packet_runner.py)
grep -r run_llm_on_pipeline_request packages/ scripts/ tests/
```

---

## Shipped file index (for reviewers)

| Area | Files |
|------|-------|
| Schemas | `packages/data_layer/packet/schema_*.json`, `runtime/schemas/` |
| Validation | `packages/data_layer/packet/validate.py` |
| LLM | `packages/data_layer/llm/packet_runner.py`, `ollama_client.py`, `prompts.py` |
| AAR | `packages/data_layer/pipeline/after_action.py` |
| Pipeline | `packages/research_pipeline/packets.py`, `scripts/run_pipeline.py` |
| UI | `apps/workbench/ui/analyst_panel.py`, `flow_state.py`, `campaign_runner.py` |
| Sync | `scripts/sync_runtime_schemas.ps1` |
| Tests | `tests/test_data_layer/test_packet_*.py`, `tests/test_research_pipeline.py` |

Tick boxes in **PACKET_STRICT_FIX_CHECKLIST.md** as fixes land; update this summary when merge-ready gates pass.
