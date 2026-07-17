# Graph Report - hft3-pr-vbt-hbt  (2026-06-18)

## Corpus Check
- 5227 files · ~2,681,982 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1173 nodes · 2849 edges · 57 communities (50 shown, 7 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 424 edges (avg confidence: 0.77)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `18e8676e`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]

## God Nodes (most connected - your core abstractions)
1. `Any` - 70 edges
2. `str` - 69 edges
3. `str` - 64 edges
4. `Any` - 64 edges
5. `compute_robustness_evidence()` - 49 edges
6. `str` - 42 edges
7. `compute_surface_stability()` - 41 edges
8. `holm_bh_correction()` - 33 edges
9. `_make_records()` - 31 edges
10. `_run_vectorbt_simulation()` - 30 edges

## Surprising Connections (you probably didn't know these)
- `test_replay_pass_without_source_lock_is_refused()` --calls--> `validate_replay_summary()`  [INFERRED]
  tests/backtest_pipeline/test_hftbacktest_realism_hbt0.py → packages/backtest_pipeline/src/hftbacktest_realism.py
- `test_replay_summary_certification_allowed_requires_non_accelerated_official_replay()` --calls--> `validate_replay_summary()`  [INFERRED]
  tests/backtest_pipeline/test_hftbacktest_realism_hbt0.py → packages/backtest_pipeline/src/hftbacktest_realism.py
- `_severity()` --references--> `str`  [EXTRACTED]
  packages/backtest_pipeline/src/ontology_gate.py → scripts/run_ontology_gate.py
- `bool` --uses--> `HftBacktestRealismArtifactError`  [INFERRED]
  tests/backtest_pipeline/test_hftbacktest_realism_hbt0.py → packages/backtest_pipeline/src/hftbacktest_realism.py
- `MonkeyPatch` --uses--> `HftBacktestRealismArtifactError`  [INFERRED]
  tests/backtest_pipeline/test_hftbacktest_realism_hbt0.py → packages/backtest_pipeline/src/hftbacktest_realism.py

## Communities (57 total, 7 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.11
Nodes (23): _build_filter_result(), _build_promoted_candidate_with_robustness(), _passing_wfc_cfg(), _passing_wfc_rows(), Tests for the VBT-4 robustness integration bridge.  Verifies that ``backtest_pip, WFC CONDITIONAL should count as fail, not pass., WFC ERROR should count as fail., WFC gate disabled in config → ERROR → fail. (+15 more)

### Community 1 - "Community 1"
Cohesion: 0.08
Nodes (16): Tests for planted_alpha_synthetic_control and adversarial_perturbation (R6).  Co, A strong edge with low perturbation should survive (adversarial_pass=True)., A weak edge with high perturbation should fail (adversarial_pass=False)., More perturbation -> lower (or equal) survival rate., perturbation_fraction=0 leaves the series untouched -> always survives         w, A negative observed mean -> perturbed_mean stays negative -> 0 survival., Same seed -> same result; independent of global RNG state., Required keys present with correct types. (+8 more)

### Community 2 - "Community 2"
Cohesion: 0.06
Nodes (33): _cell(), _flat_grid_2d(), Tests for the VBT-3 surface-stability producer.  Authority: ``docs/project/ROBUS, Build a grid cell with a performance value and trade count., A flat 2-D grid where every cell has the same performance., The producer output must be recognised as 'defined' by the adapter., TestAdapterValidatorCompatibility, TestCliffDistance (+25 more)

### Community 3 - "Community 3"
Cohesion: 0.06
Nodes (128): TestVectorbtAdapterIntegration, CandidateModel, Any, bool, float, int, ndarray, Path (+120 more)

### Community 4 - "Community 4"
Cohesion: 0.28
Nodes (24): Any, float, int, ndarray, str, _all_not_run_output(), _build_walk_forward_metrics(), _not_run_sentinel() (+16 more)

### Community 5 - "Community 5"
Cohesion: 0.07
Nodes (34): Acceptance Gate, Architecture Decision, code:text (feature_plane_status=feature_complete_pit_declared|scheduled), code:bash (bash scripts/run_hbt_realism_verify.sh), code:bash (bash scripts/run_vbt_hbt_handoff_verify.sh), code:text (ontology/literature/config), code:text (parameter_space_id), code:text (run_budget_id) (+26 more)

### Community 6 - "Community 6"
Cohesion: 0.12
Nodes (11): Same seed -> same result; different seed -> (likely) different null., Different seeds produce (almost certainly) different null_mean., Fail-closed on empty / insufficient input., n_null_runs < 1 cannot form a distribution., n_obs=1 is degenerate but not empty; observed_mean is recoverable.         The s, Required keys present with correct types., TestNullBatteryDeterminism, TestNullBatteryGuards (+3 more)

### Community 7 - "Community 7"
Cohesion: 0.19
Nodes (18): Any, float, int, ndarray, str, bootstrap_ci(), cscv_pbo(), _deflated_sharpe_cdf_inline() (+10 more)

### Community 8 - "Community 8"
Cohesion: 0.16
Nodes (7): Holm adjusted p-values are analytically derivable; verify against formula., BH adjusted p-values are analytically derivable; verify against formula.      Re, Output positions must align with the original (unsorted) input., TestBHCorrectionHandMath, TestHolmCorrectionHandMath, holm_bh_correction(), Multiple-testing p-value correction for the Stage-B family.      Satisfies ROB

### Community 9 - "Community 9"
Cohesion: 0.12
Nodes (12): Same seed -> same result; independent of global RNG state., Different seeds produce (very likely) different planted_p_value., Required keys present with correct types., A strong observed edge should be detectable after planting alpha., A tiny alpha planted into a high-variance baseline is indistinguishable, Planting alpha must push the planted p_value below the baseline., n_planted larger than n_obs is clipped to n_obs (every position planted)., TestPlantedAlphaDetection (+4 more)

### Community 10 - "Community 10"
Cohesion: 0.15
Nodes (9): Fail-closed when decomposition fields missing., Old records with no fee decomposition → stress_data_available=False., No randomness: repeated calls produce identical results., Required keys present with correct types., TestLatencyDeterminism, TestLatencyGuards, TestLatencyOutputShape, latency_stress_for_cell() (+1 more)

### Community 11 - "Community 11"
Cohesion: 0.16
Nodes (11): _make_records(), Latency stress values are analytically derivable; verify against formula., With latency_ms_baseline=0, baseline_expectancy == base net mean., latency_cost_per_rt = (stress - baseline) * ticks_per_ms * tick_value_usd., stress_expectancy = gross - fee - latency_cost_per_rt., Doubling latency_ms_stress doubles the latency cost., baseline_expectancy = net mean (already embeds baseline latency);         stress, Return (expectancies, n_trades, fee_per_rt_list, tick_value_list). (+3 more)

### Community 12 - "Community 12"
Cohesion: 0.18
Nodes (8): Fail-closed when decomposition fields missing., Old records with no fee decomposition → stress_data_available=False., Required keys present with correct types., Guard (fail-closed) returns same key set with None values., TestSlippageGuards, TestSlippageOutputShape, R6 — Slippage stress test (post-hoc, no re-replay).      Satisfies ROBUSTNESS_, slippage_stress_for_cell()

### Community 13 - "Community 13"
Cohesion: 0.20
Nodes (6): Tests for holm_bh_correction and null_strategy_battery (R6 robustness layer).  C, BH (FDR) is more permissive than Holm (FWER) on the same family., alpha=0.0001 is below the smallest corrected p -> no rejections., The two producers compose: null battery gives per-cell p_values, then     Holm/B, TestBHHolmComparison, TestCrossProducerWorkflow

### Community 14 - "Community 14"
Cohesion: 0.20
Nodes (6): Varying per-event expectancies → stress values are means., Per-event tick_value may differ (different products)., Slippage stress values are analytically derivable; verify against formula., slip_xN: net_exp_at_m = gross - fee - tick_value * (m - 1.0).          With base, slip_p5t / slip_1t: gross - fee - tick_value * adder_ticks., TestSlippageHandMath

### Community 15 - "Community 15"
Cohesion: 0.20
Nodes (6): stress_pass (= slip_x2_pass) flips correctly at the boundary., gross - fee - tv > 0 → slip_x2_expectancy > 0 → stress_pass True., slip_x2_expectancy < 0 → stress_pass False., slip_x2_expectancy == 0 → NOT > 0 → stress_pass False., stress_pass must always equal slip_x2_pass., TestSlippagePassFail

### Community 16 - "Community 16"
Cohesion: 0.20
Nodes (6): Hand-verified mathematical expectations., Cells with strong positive expectancy should survive 10% perturbation., Cells with explicitly zero mean should fail under perturbation.          The add, Negative expectancy should always fail., Zero perturbation fraction should preserve the original sign., TestParameterPerturbationHandMath

### Community 17 - "Community 17"
Cohesion: 0.22
Nodes (5): stress_pass (= stress_expectancy > 0) flips correctly., Large latency → stress_expectancy < 0 → stress_pass False., stress_expectancy == 0 → NOT > 0 → stress_pass False., latency_ms_stress == latency_ms_baseline → stress == baseline., TestLatencyPassFail

### Community 19 - "Community 19"
Cohesion: 0.29
Nodes (4): Symmetric / zero-mean expectancies -> high p_value -> fail., A random zero-centred sample should have a high p_value., The null distribution is centred near zero regardless of the data., TestNullBatteryNoEdge

### Community 20 - "Community 20"
Cohesion: 0.33
Nodes (4): No-edge expectancies should not produce a false planted_pass via the     baselin, The demeaned baseline of any series has mean exactly 0, so the null         batt, Across varied inputs the demeaned baseline (mean==0) yields a high         basel, TestPlantedAlphaNoEdge

### Community 21 - "Community 21"
Cohesion: 0.20
Nodes (6): Pass/fail boundary behavior., Pass when stability_score >= min_stability_score., Fail when stability_score < min_stability_score (via zero-mean series)., Behavior at exact boundary depends on floating point., min_stability_score echoed in output., TestParameterPerturbationPassFail

### Community 22 - "Community 22"
Cohesion: 0.33
Nodes (3): Tests for slippage_stress_for_cell and latency_stress_for_cell (R6).  Covers (pe, Higher slippage stress = lower (or equal) expectancy., TestSlippageOrdering

### Community 26 - "Community 26"
Cohesion: 0.40
Nodes (3): No randomness: repeated calls produce identical results., Result does not depend on global numpy RNG state., TestSlippageDeterminism

### Community 27 - "Community 27"
Cohesion: 0.09
Nodes (16): Tests for parameter_perturbation producer (§10 line 286, gap matrix #3)., Determinism guarantees., Output schema validation., All required keys present on success., Guard keys present even on failure., Input fractions echoed in output., Default fractions [0.10, 0.25] used when None., Stability score equals mean of fraction survival rates. (+8 more)

### Community 28 - "Community 28"
Cohesion: 0.20
Nodes (7): _full_passing_input(), Complete robustness input where all gates pass.      Includes stress decompositi, TestAllGatesPass, TestDeterminism, TestOutputShape, compute_robustness_evidence(), Given robustness raw input data, call producers and return artifact fields.

### Community 29 - "Community 29"
Cohesion: 0.07
Nodes (103): test_replay_summary_accelerated_mode_fails_closed(), Any, bool, float, int, Path, str, _append_metric_discrepancy() (+95 more)

### Community 30 - "Community 30"
Cohesion: 0.14
Nodes (13): _failing_pbo_matrix(), _passing_cscv_matrix(), _passing_expectancies(), Random CSCV matrix → PBO >= 0.5 (fail)., Only DSR data (no PBO matrix) → DSR runs, PBO stays not_run., DSR data also triggers bootstrap_ci., Only CSCV matrix → PBO runs, DSR stays not_run., Per-event expectancies with high SNR → DSR + all §10 producers pass. (+5 more)

### Community 31 - "Community 31"
Cohesion: 0.23
Nodes (23): Any, _all_families_consumed(), build_data_scope_skip_manifest(), build_feature_plane_payload(), build_feature_usage_manifest(), _canonical_json(), classify_feature_plane_status(), compute_feature_usage_manifest_hash() (+15 more)

### Community 32 - "Community 32"
Cohesion: 0.29
Nodes (4): Malformed per_event_expectancies → DSR fail-closed with reason., Malformed cscv_matrix → PBO fail-closed., When a producer fails, the result includes a reason string., TestProducerErrorHandling

### Community 33 - "Community 33"
Cohesion: 0.40
Nodes (4): _failing_dsr_expectancies(), Per-event expectancies with very low SNR → DSR fails., TestDSRFails, float

### Community 35 - "Community 35"
Cohesion: 0.29
Nodes (11): native_probe_latency_fields(), passing_section10_evidence_maps(), Shared HftBacktest realism test fixtures (§10 evidence + native hot-path pins)., Top-level screening artifact with hash for HBT handoff tests., Latency artifact fields pointing at hash-backed CHI404 native probe evidence., §10 robustness maps that pass staleness (from robustness_bridge golden input)., Promoted screening row with replay-eligibility fields + §10 evidence., replay_eligible_promoted_candidate() (+3 more)

### Community 36 - "Community 36"
Cohesion: 0.09
Nodes (23): TestCitationTracer, bool, Path, _applicable_invariants(), _check_vendor_lock(), CitationResult, DriftResult, _list_paper_ids() (+15 more)

### Community 37 - "Community 37"
Cohesion: 0.06
Nodes (30): 0. Fable Mindset (MANDATORY — load before any gate action), 0A. The Fable Loop (run every gate cycle, in order, no skipping), 0B. Why the Fable loop matters for financial code, 0C. Fable gate entry checklist (must be confirmed before Step 1), 10. Summary, 1. Purpose, 2. Ontology Model (Palantir-style: data + actions + logic), 2A. Data (Nouns) (+22 more)

### Community 38 - "Community 38"
Cohesion: 0.17
Nodes (8): If Fable checklist fails, gate must reject regardless of other results., TestGateDecision, TestScopeHonesty, check_scope_honesty(), gate_decision(), Enforce scope honesty (spec GATE_RULES §8).      - Subset pytest ≠ scope-green, Aggregate all results and emit PASS or REJECT with reasons.      Any red finding, ScopeHonestyResult

### Community 39 - "Community 39"
Cohesion: 0.18
Nodes (9): TestFableEntryChecklist, TestRunGate, FableChecklist, GateVerdict, Run the full gate pipeline and return the aggregate verdict.      Convenience en, Result of the 5-checkbox Fable entry checklist (spec §0C)., Validate the 5 mandatory Fable entry checkboxes.      If any checkbox is false t, run_gate() (+1 more)

### Community 40 - "Community 40"
Cohesion: 0.15
Nodes (8): Docs area has no applicable invariants — all should be na/pass., Unknown area defaults to full B1-B8 per charter., TestInvariantChecker, int, check_invariants(), InvariantCheck, InvariantResult, Apply B1-B8 for a code area, citing authority for each check.      ``invariant_r

### Community 41 - "Community 41"
Cohesion: 0.18
Nodes (7): Drift patterns can be flagged via structured artifact mapping., Patterns can be passed directly (for tests/direct flagging)., TestDriftGuard, check_drift(), _detect_drift_patterns(), Detect the 7 drift patterns from prose/artifact text.      Deterministic keyword, Check for the 7 drift patterns from the 2026-06-17 decision.      Either ``text`

### Community 42 - "Community 42"
Cohesion: 0.27
Nodes (5): Artifact claiming feature_complete without consumption proof fails., TestArtifactValidation, ArtifactResult, Validate a screening/feature-plane artifact schema.      Delegates to :func:`val, validate_artifact_schema()

### Community 43 - "Community 43"
Cohesion: 0.16
Nodes (7): Tests for the Ontology Gate Agent (ontology_gate.py).  Every test exercises a re, Pilot scope is allowed to use numba engine., Minimal valid screening artifact for schema validation tests., TestToolUsageChecker, valid_artifact(), check_tool_usage(), Verify a VectorBT or HftBacktest API call site matches official signatures.

### Community 44 - "Community 44"
Cohesion: 0.29
Nodes (5): _bar_stub_payload(), _bar_stub_payload_raw(), Tests for VectorBT feature-plane contract enforcement., TestFeaturePlaneValidation, TestFeatureUsageManifest

### Community 45 - "Community 45"
Cohesion: 0.15
Nodes (38): _screening_artifact(), test_hbt0_cli_writes_fail_closed_artifacts(), test_hbt0_code_does_not_name_retired_replay_entrypoints(), test_hbt0_derives_rust_requirement_from_broad_screening_scope(), test_hbt0_missing_screening_artifact_path_still_writes_fail_closed_summary(), test_hbt0_refuses_malformed_nonterminal_screening_artifact(), test_hbt0_refuses_missing_terminal_screening_hash(), test_hbt0_refuses_required_non_rust_screening_artifact() (+30 more)

### Community 46 - "Community 46"
Cohesion: 0.22
Nodes (33): _event_row(), hbt_contract(), _hftbacktest_validate_event_order_importable(), _load_hftbacktest_contract(), _make_events(), TRADE_EVENT is L3-compatible (round-3 P1); trade + ADD is valid L3 MBO., test_validate_hftbacktest_data_path_rejects_missing_data_array(), test_validate_hftbacktest_event_array_accepts_trade_plus_l3_events() (+25 more)

### Community 47 - "Community 47"
Cohesion: 0.14
Nodes (19): BacktestAsset, HashMapMarketDepthBacktest, bool, float, int, ndarray, str, _apply_constant_latency() (+11 more)

### Community 48 - "Community 48"
Cohesion: 0.41
Nodes (15): evaluate_gate(), _hash_fields(), _load_json(), main(), Any, bool, int, Path (+7 more)

### Community 49 - "Community 49"
Cohesion: 0.41
Nodes (11): _load_ready_gate(), _load_units(), main(), _parse_pipeline_stdout(), Any, bool, int, Path (+3 more)

### Community 50 - "Community 50"
Cohesion: 0.05
Nodes (37): actual_verdict, issues, severity, valid, clean, detected_patterns, severity, expected_until (+29 more)

### Community 51 - "Community 51"
Cohesion: 0.33
Nodes (8): ArgumentParser, _build_parser(), _load_json(), main(), _parse_fable(), _parse_invariants(), int, Accept GROUNDED-style or grounded-style keys.

### Community 52 - "Community 52"
Cohesion: 0.18
Nodes (10): code:powershell ($env:HFT3_VAULT_ROOT = "$env:USERPROFILE\Desktop\Obsidian Va), code:block2 ([ONTOLOGY]), code:block3 (invariant: B1=pass,B2=pass,B3=pass,B4=pass,B5=pass,B6=na,B7=), code:powershell (python scripts/run_ontology_gate.py --fable-json runtime/rep), Current posture (REJECT until fixed), Ontology Gate — Vast M6 pipeline path, Required citation block (handoff / PR), Required pipeline order (canonical) (+2 more)

### Community 53 - "Community 53"
Cohesion: 0.33
Nodes (5): all_rejected, authority, claims_audited, generated_at_utc, results

### Community 54 - "Community 54"
Cohesion: 0.67
Nodes (3): _audit_one(), main(), int

## Knowledge Gaps
- **100 isolated node(s):** `code:powershell ($env:HFT3_VAULT_ROOT = "$env:USERPROFILE\Desktop\Obsidian Va)`, `Required pipeline order (canonical)`, `code:block2 ([ONTOLOGY])`, `Vault authority`, `code:block3 (invariant: B1=pass,B2=pass,B3=pass,B4=pass,B5=pass,B6=na,B7=)` (+95 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `compute_robustness_evidence()` connect `Community 28` to `Community 32`, `Community 33`, `Community 34`, `Community 0`, `Community 4`, `Community 3`, `Community 30`?**
  _High betweenness centrality (0.346) - this node is a cross-community bridge._
- **Why does `_normalise_promoted_screening_row()` connect `Community 3` to `Community 0`, `Community 28`?**
  _High betweenness centrality (0.323) - this node is a cross-community bridge._
- **Why does `_validate_screening_artifact_hash()` connect `Community 29` to `Community 3`, `Community 31`?**
  _High betweenness centrality (0.225) - this node is a cross-community bridge._
- **Are the 31 inferred relationships involving `compute_robustness_evidence()` (e.g. with `.test_all_status_fields_pass()` and `.test_bootstrap_ci_has_pass_status()`) actually correct?**
  _`compute_robustness_evidence()` has 31 INFERRED edges - model-reasoned connections that need verification._
- **What connects `code:powershell ($env:HFT3_VAULT_ROOT = "$env:USERPROFILE\Desktop\Obsidian Va)`, `Required pipeline order (canonical)`, `code:block2 ([ONTOLOGY])` to the rest of the system?**
  _332 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.11264367816091954 - nodes in this community are weakly interconnected._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.08095238095238096 - nodes in this community are weakly interconnected._