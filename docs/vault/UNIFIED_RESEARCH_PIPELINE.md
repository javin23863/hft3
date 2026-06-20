# Unified research pipeline (chronological order)

**Status:** canonical chronological spec for CME VectorBT → HftBacktest → workbench robustness → trade-manager lifecycle → CHI404 live/paper.

**Supersedes:** fragmented mental models across VBT paid screen, HBT realism, workbench robustness pack, autonomous promotion runner, and lifecycle registry. Does **not** replace lane-specific runbooks — it orders them.

**Authority chain:** vault `library/14 Model Lifecycle and Governance.md` · `library/13 Robust Backtesting and Multiple Testing.md` · [FEATURE_LITERATURE_TRACEABILITY_MATRIX.md](../project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md) · [RESEARCH_ENTRYPOINTS.md](RESEARCH_ENTRYPOINTS.md)

**Code registry:** `packages/backtest_pipeline/src/research_pipeline_stages.py` (`research_pipeline_stage_id` on artifacts)

---

## Chronological stage table

| Stage | Name | Vault | Literature | Code entrypoints | Input → output artifact | Trade manager / lifecycle hook |
|-------|------|-------|------------|------------------|-------------------------|--------------------------------|
| **0** | Ontology + literature grounding | `wiki/hot.md`, `Memory Stack.md`, `library/Ontology.md`, `library/System Implications.md`, `library/14 Model Lifecycle and Governance.md` | `Ultimate_Quantitative_Finance_Researcher.pdf`; [FEATURE_LITERATURE_TRACEABILITY_MATRIX.md](../project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md) | `scripts/vault_gate.ps1`, `scripts/vault_pre_edit.ps1`, `hft_campaign/ontology.py` | task query → `runtime/vault-gate/.last-vault-gate.json` | none (prep) |
| **1** | Feature families + VectorBT screen (paid/pilot) | `decisions/2026-06-17 Feature-complete research authority correction.md`, `pipelines/unified-research-pipeline.md` | [VECTORBT_SCREENING_ENGINE_SPEC.md](../project/VECTORBT_SCREENING_ENGINE_SPEC.md), [OPPORTUNITY_RESEARCH_SPEC.md](../project/OPPORTUNITY_RESEARCH_SPEC.md), `dev_instructions.pdf` | `run_paid_screen.py`, `run_pipeline.py --vectorbt`, `vectorbt_adapter.py`, `feature_plane.py`, `fs_v1_screen_path.py` | events.csv × registry × NPZ → `research_cards/pipeline_runs/<run_id>/screening_artifact.json` | optional `CANDIDATE→SCREENING` when `HFT3_PIPELINE_LIFECYCLE_ENROLL=1`; `research_card_links` annotate |
| **2** | Promoted aggregation | `library/13 Robust Backtesting and Multiple Testing.md` | `Ultimate_Quantitative_Finance_Researcher.pdf` (multiple testing); [ROBUSTNESS_TESTING_SPEC.md](../project/ROBUSTNESS_TESTING_SPEC.md) | `promotion_gate.py`, `apply_promotion_gates`, `recipe_hash_gate.py` | screening promoted[] → `PromotedCandidate` + `promoted_ids` | hypothesis_id → lifecycle slug; recipe_hash fail-closed handoff |
| **3** | HftBacktest execution realism | [HFTBACKTEST_LATENCY_ONTOLOGY.md](HFTBACKTEST_LATENCY_ONTOLOGY.md), `library/14 Model Lifecycle and Governance.md` | `chicago_cme_microstructure_mathematical_model.pdf`, [HFTBACKTEST_REALISM_ENGINE_SPEC.md](../project/HFTBACKTEST_REALISM_ENGINE_SPEC.md), `rithmic_trial_hftbacktest_pipeline_prompt.pdf` | `run_hftbacktest_realism.py`, `hftbacktest_realism.py`, `hft_campaign/` | screen-passed artifact → `research_cards/hftbacktest_realism/<run_id>/replay_summary.json` | order lifecycle audit; optional `SCREENING→GAUNTLET`; **does not run MC/full WF** |
| **4** | Workbench robustness pack | `library/13 Robust Backtesting and Multiple Testing.md` | `Ultimate_Quantitative_Finance_Researcher.pdf`; [BACKTESTER_CERTIFICATION.md](BACKTESTER_CERTIFICATION.md) | `apps/workbench/src/robustness/pack.py`, `python -m workbench run/campaign`, `run_autonomous.py::stage_robustness_and_wf` | HBT/campaign metrics → `research_cards/workbench_runs/<run_id>/`, `robustness_gates.json` | feeds envelope inputs at certify; autonomous MC/WF gates **scaffolded until workbench evidence observed** |
| **5** | Lifecycle enrollment + behavior tracking | `library/14 Model Lifecycle and Governance.md` | [MODEL_LIFECYCLE.md](../../specs/MODEL_LIFECYCLE.md) | `model_metrics/lifecycle.py`, `trade_manager/model_behavior.py`, `decay_detector.py`, `risk_layer.py`, `run_lifecycle_eval.py` | robustness + HBT artifacts → `runtime/lifecycle/model_lifecycle.json`, `envelopes/<id>.json` | `ModelBehaviorRuleEngine`; submit_gate on pre-trade path |
| **6** | Promotion / certification | `decisions/`, `validation/` | [VALIDATION_HONESTY.md](../VALIDATION_HONESTY.md), [REVIEWER_CHARTER.md](../REVIEWER_CHARTER.md), `hft3/validation/promotion_gate.py` | `certify.py`, `rearm.py`, `autonomy/gates.py` | frozen envelope + gates → certification stamp, `transitions.jsonl` | `CERTIFIED→SHADOW→LIVE`; defect-ledger fail-closed |
| **7** | Live/paper (CHI404 only) | [CHI404_CANONICAL_ENTRYPOINTS.md](CHI404_CANONICAL_ENTRYPOINTS.md), BLUEPRINT §4 | `chicago_cme_a_plus_production_implementation_prompt.pdf`, [rithmic_trial/README.md](../rithmic_trial/README.md) | `chi404_run_trial_live.sh`, `rithmic_trial/pipeline.py`, `trade_manager/execution_boundary.py` | LIVE + submit_gate pass → trial quarantine + order lifecycle logs | `execution_boundary`, `kill_switch`; decay routes via orchestrator |

---

## Flow (strict order)

```mermaid
flowchart LR
  S0[Stage 0 Ontology] --> S1[Stage 1 VBT Screen]
  S1 --> S2[Stage 2 Promote]
  S2 --> S3[Stage 3 HBT Realism]
  S3 --> S4[Stage 4 Workbench Robustness]
  S4 --> S5[Stage 5 Lifecycle + Trade Manager]
  S5 --> S6[Stage 6 Certify / Promote]
  S6 --> S7[Stage 7 CHI404 Live/Paper]
```

**Retired / historical only:** `run_event_replay.py`, bare `run_event_universe --rescan` on Vast without screening artifact, M6 Stage A as VectorBT prerequisite. See [RESEARCH_ENTRYPOINTS.md](RESEARCH_ENTRYPOINTS.md) §1a.

---

## Honest gaps (what is NOT run where)

| Layer | Runs at stage | Does NOT run |
|-------|---------------|--------------|
| VectorBT paid/pilot screen | 1–2 | Full Monte Carlo; full walk-forward fold matrix (pilot: `pilot_summary_only`, reason `full_walk_forward_fold_matrix_not_run_in_vbt2_pilot`); official HftBacktest |
| HftBacktest realism | 3 | Broad VectorBT rescreen; workbench Bonferroni pack; autonomous promotion MC/WF unless workbench summary wired |
| Workbench robustness pack | 4 | VectorBT lightweight `_simulate_walk_forward`; live orders |
| Autonomous promotion runner | 4–6 (gates defined) | MC/WF gates pass only when workbench evidence observed — otherwise `observed_value=None`, `pass_fail=False` (honest scaffold) |
| Trade manager / lifecycle | 5–7 | Does not replace Stage 1–4 evidence; enforces behavior on LIVE path via submit_gate |

---

## Artifact stamping

Terminal artifacts include:

- `research_pipeline_stage_id` — e.g. `stage_1_vectorbt_screen`
- `research_pipeline_ontology_doc` — this file
- `research_pipeline_lifecycle_state` — expected registry state at handoff
- `research_pipeline_trade_manager_hook` — human-readable hook description

Lifecycle annotation (existing models only, no state change): `record_lifecycle_pipeline_handoff()`.

Optional enrollment (`HFT3_PIPELINE_LIFECYCLE_ENROLL=1`): `maybe_enroll_lifecycle_transition()` for Stage 1→SCREENING and Stage 3→GAUNTLET.

---

## Cross-links

| Doc | Role |
|-----|------|
| [RESEARCH_ENTRYPOINTS.md](RESEARCH_ENTRYPOINTS.md) | CLI commands per stage |
| [VBT_PAID_SCREEN_RUNBOOK.md](../project/VBT_PAID_SCREEN_RUNBOOK.md) | Stage 1 operational phases A–E |
| [MODEL_LIFECYCLE.md](../../specs/MODEL_LIFECYCLE.md) | Lifecycle states + autonomy rails |
| [HFTBACKTEST_REALISM_ENGINE_SPEC.md](../project/HFTBACKTEST_REALISM_ENGINE_SPEC.md) | Stage 3 contract |
| [WALK_FORWARD_CAMPAIGNS.md](../workbench/WALK_FORWARD_CAMPAIGNS.md) | Stage 4 campaigns |

---

## Verification

```bash
python -m pytest tests/backtest_pipeline/test_research_pipeline_stages.py -q
python -m pytest tests/test_model_lifecycle.py tests/backtest_pipeline/test_hftbacktest_realism_hbt0.py -q
```

Full scope-green: `scripts/run_agent_verify.ps1` (180s cap).
