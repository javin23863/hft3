# Ontology Gate — Vast M6 pipeline path

Status: enforcement reference for `scripts/validate_vast_m6_ontology_gate.py`.

## Current posture (REJECT until fixed)

Bare `run_event_universe.py --rescan` on Vast **without** a VectorBT screening artifact is **REJECT** by the Ontology Gate Agent:

- Missing `screening_artifact.json` schema validation
- Drift: treating full HftBacktest rescan as VectorBT-first pipeline evidence
- Scope honesty: progress claims without verify exit code + output tail

Run the check:

```powershell
$env:HFT3_VAULT_ROOT = "$env:USERPROFILE\Desktop\Obsidian Vault From VPS\hft3"
$env:PYTHONPATH = "$PWD;$PWD\packages"
python scripts/validate_vast_m6_ontology_gate.py
```

Output: `runtime/reports/ontology_gate_vast_m6_validation.json` (expect `actual_verdict: REJECT`).

## Required pipeline order (canonical)

1. **VectorBT paid screen** — `scripts/run_vectorbt_paid_screen.py` / Vast launch scripts per `docs/project/VBT_PAID_SCREEN_RUNBOOK.md`
2. **Screening artifact** — `screening_artifact.json` passing `validate_screening_artifact` + `feature_plane_validation_errors`
3. **Stage A survivors** (when applicable) — `stage_a_survivors.json`
4. **HftBacktest realism** — `run_event_universe.py --from-stage-a` with latency band `6.255764` ms (M5 authority), not bare `--rescan` full matrix

## Required citation block (handoff / PR)

```
[ONTOLOGY]
paper: none
spec: VECTORBT_SCREENING_ENGINE_SPEC.md::Screening Artifact Contract
spec: OPPORTUNITY_RESEARCH_SPEC.md
tool_doc: Portfolio.from_signals::1.0.0
invariant: B1=pass,B2=pass,B3=pass,B4=pass,B5=pass,B6=na,B7=pass,B8=na
artifact: screening_artifact.json validated
feature_plane: scheduled_event_only | incomplete_feature_plane | feature_complete_pit_declared
```

## Vault authority

- `decisions/2026-06-17 Feature-complete research authority correction.md` — no parallel `VBT_RESEARCH_PRODUCT_SCOPE.md` authority
- `wiki/hot.md` — VectorBT rust engine required for broad paid scope

## Unblocks when

1. A valid `screening_artifact.json` passes `validate_artifact_schema`.
2. Handoff includes invariant results for applicable B-checks, for example:

```
invariant: B1=pass,B2=pass,B3=pass,B4=pass,B5=pass,B6=na,B7=pass,B8=na
```

3. CLI returns PASS:

```powershell
python scripts/run_ontology_gate.py --fable-json runtime/reports/fable_checklist.json --artifact path/to/screening_artifact.json --area backtest_pipeline --invariant-results "B1=pass,B2=pass,B3=pass,B4=pass,B5=pass,B6=na,B7=pass,B8=na"
```
