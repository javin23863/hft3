# HFT3 imbalance traceability

| Requirement | Implementation | Tests | Artifact |
|-------------|----------------|-------|------------|
| Data inventory | `scripts/build_imbalance_inventory.py`, `docs/hft3_imbalance_inventory.md` | `test_imbalance_inventory_exists` | `runtime/data_audits/hft3_imbalance_inventory.json` |
| Schema classification | `packages/features_engine/src/imbalance/classification.py`, `packages/data_system/src/schema_resolver.py` | `test_mbp10_not_labeled_l3`, `test_no_silent_schema_downgrade` | `true_vs_proxy_classification.json` |
| Book imbalance L1–L10 | `packages/features_engine/src/imbalance/book.py` | `test_book_imbalance_from_mbo`, `test_book_imbalance_from_mbp10` | `book_imbalance_summary.json` |
| True vs proxy OFI | `packages/features_engine/src/imbalance/order_flow.py` | `test_order_flow_true_vs_proxy_labeling`, `test_no_ofi_when_source_insufficient` | `order_flow_imbalance_summary.json` |
| Auction family | `packages/features_engine/src/imbalance/auction.py` | `test_auction_imbalance_separate_family`, `test_auction_imbalance_window_alignment` | `auction_imbalance_summary.json` |
| MBP-10 ingest | `packages/data_system/src/databento_client.py` (`download_mbp10_window`) | `test_book_imbalance_from_mbp10` | manifest.parquet `schema=mbp-10` |
| Feature registry | `packages/features_engine/config/imbalance_features.yaml`, `registry.py` | `test_imbalance_feature_lineage` | `imbalance_feature_manifest.json` |
| Cross-asset lineage | `packages/features_engine/src/imbalance/normalize.py` | `test_options_contract_lineage`, `test_futures_roll_metadata` | `imbalance_lineage.json` |
| Options eligibility | `packages/options_lane/src/imbalance_eligibility.py` | `test_options_liquidity_eligibility` | — |
| Eight-mode ablation (real replay) | `packages/features_engine/src/imbalance/ablation.py`, `apps/workbench/src/imbalance/replay_runner.py`, `packages/backtest_pipeline/src/replay_matrix.py` | `test_ablation_wrapper_changes_signal`, `test_best_ablation_verdict` | `imbalance_ablation_results.json` |
| Shared OrderBook | `packages/features_engine/src/pipeline/market_state_pipeline.py`, `imbalance/engine.py` | `test_shared_book_no_double_apply` | — |
| MBP-10 replay | `packages/features_engine/src/imbalance/mbp_replay.py` | `test_mbp10_replay_fixture` | `tests/fixtures/imbalance_mbp10_sample.ndjson` |
| Auction normalize | `packages/equities_lane/src/ingest/normalize_auction_imbalance.py` | `test_auction_imbalance_window_alignment` | `tests/fixtures/imbalance_auction_sample.ndjson` |
| Options eligibility | `packages/options_lane/src/parity_engine.py` | `test_options_liquidity_eligibility` | — |
| max_contract_trade_imbalance | `packages/features_engine/src/features/mbo_features.py` | via MBO pipeline | FeatureIndex slot 34 |
| Quality / leakage | `packages/features_engine/src/imbalance/quality.py` | `test_imbalance_no_lookahead` | `imbalance_quality_checks.json` |
| Latency budget | `runtime/validation/feature_latency_budget.json` | `test_imbalance_latency_budget` | `imbalance_latency_budget.json` |
| Promotion gate | `packages/hft3/validation/research_stamp.py` | `test_reject_complexity_without_contribution` | certification stamp |
| Pipeline integration | `packages/features_engine/src/pipeline/market_state_pipeline.py` | `test_book_imbalance_from_mbo` | run `imbalance/` dir |
| Workbench artifacts | `apps/workbench/src/imbalance/artifacts.py` | workbench run integration | `imbalance/*` |
