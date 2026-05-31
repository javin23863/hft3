# After-Action Report for CME MBO Backtests

## Summary of Results
The backtest run, identified as `AAR_SMOKE_TEST`, has been evaluated against the established symbolic invariants. The results indicate that all obligations were met without any violations.

### Symbolic Result
{
  "passed": true,
  "obligations": [
    "trade[0]: market_data_exchange_ts_ns <= market_data_receive_ts_ns",
    "trade[0]: decision_end_ts_ns >= market_data_receive_ts_ns",
    "trade[0]: fill_ts_ns >= order_send_ts_ns when fill present",
    "trade[0]: tick_to_ack_us ≅ feed + decision + send + ack"
  ],
  "violations": []
}

## Similar Prior Runs
The following runs have similar characteristics:

[
  {
    "breakeven_us": 250.0,
    "event_context": "CPI_TIGHT",
    "event_id": "CPI_2024_09_11_TIGHT",
    "id": "run:run_ok",
    "lane_pass": false,
    "latency_lane": "sub_10ms",
    "model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
    "net_pnl": -181.77,
    "promote_candidate": false,
    "run_id": "run_ok",
    "type": "backtest-run"
  },
  {
    "breakeven_us": 250.0,
    "event_context": "CPI_TIGHT",
    "event_id": "CPI_2024_09_11_TIGHT",
    "id": "run:run_test",
    "lane_pass": false,
    "latency_lane": "sub_10ms",
    "model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
    "net_pnl": -181.77,
    "promote_candidate": false,
    "run_id": "run_test",
    "type": "backtest-run"
  },
  {
    "breakeven_us": 250.0,
    "event_context": "CPI_TIGHT",
    "event_id": "CPI_2024_09_11_TIGHT",
    "id": "run:AAR_SMOKE_TEST",
    "lane_pass": false,
    "latency_lane": "sub_10ms",
    "model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
    "net_pnl": -181.77,
    "promote_candidate": false,
    "run_id": "AAR_SMOKE_TEST",
    "type": "backtest-run"
  }
]

## PDF Citation Index
The following fields have corresponding citations in the provided PDFs:

[
  {
    "field": "event_context",
    "pdf": "chicago_cme_microstructure_mathematical_model.pdf",
    "section": "Event-time filtration",
    "present_on_disk": true
  },
  {
    "field": "per_trade_audit",
    "pdf": "chicago_cme_microstructure_mathematical_model.pdf",
    "section": "Timestamp discipline",
    "present_on_disk": true
  },
  {
    "field": "latency_authority",
    "pdf": "chicago_cme_a_plus_production_implementation_prompt.pdf",
    "section": "Gateway latency",
    "present_on_disk": true
  },
  {
    "field": "injection_sweep",
    "pdf": "chicago_cme_a_plus_production_implementation_prompt.pdf",
    "section": "Injection sweep",
    "present_on_disk": true
  },
  {
    "field": "simulation_fidelity",
    "pdf": "chicago_cme_microstructure_a_plus_developer_handoff.pdf",
    "section": "Replay fidelity",
    "present_on_disk": true
  },
  {
    "field": "rithmic_trial_quarantine",
    "pdf": "rithmic_trial_hftbacktest_pipeline_prompt.pdf",
    "section": "Trial lane",
    "present_on_disk": true
  },
  {
    "field": "walk_forward_validation",
    "pdf": "Ultimate_Quantitative_Finance_Researcher.pdf",
    "section": "Validation",
    "present_on_disk": true
  },
  {
    "field": "structural_models",
    "pdf": "algorithmic_trading_strategy_development.pdf",
    "section": "Ch. structural",
    "present_on_disk": true
  },
  {
    "field": "framework_extensions",
    "pdf": "hft_framework_developer_prompt.pdf",
    "section": "Framework prompt",
    "present_on_disk": true
  }
]

## Packet Summary (Full Audit in Artifact JSON)
The summary of the run is as follows:

{
  "run_id": "AAR_SMOKE_TEST",
  "event_context": {
    "event_id": "CPI_2024_09_11_TIGHT",
    "event_state": "CPI_TIGHT",
    "event_state_heuristic": true,
    "data_sufficient": true,
    "catalog_years": 12.0,
    "symbol": null
  },
  "latency_authority": {
    "authority": "cpp_measured",
    "measured_production_p99_us": 1000.0,
    "breakeven_us": 250.0,
    "latency_profitability_buffer_us": -750.0,
    "lane_required": "sub_10ms",
    "lane_measured": "sub_10ms",
    "lane_pass": false,
    "survives_cpp_execution_delay": false,
    "promote_candidate": false,
    "robustness_passed": true,
    "cpp_hot_path_runtime_us": 850.0,
    "python_research_runtime_us": 12500.0,
    "python_research_runtime_authoritative": false,
    "net_pnl": -181.77
  },
  "injection_sweep": {
    "0": -161.77,
    "50": -165.0,
    "100": -170.0,
    "250": -175.0,
    "500": -181.77,
    "1000": -190.0,
    "2000": -210.0,
    "5000": -240.0,
    "10000": -260.0,
    "25000": -280.0,
    "50000": -300.0,
    "100000": -320.0,
    "250000": -350.0,
    "1000000": -400.0
  },
  "simulation_fidelity": {
    "cpp_replay_available": false,
    "matching_config": "workbench\\src\\sim\\matching_config.yaml",
    "queue_tracker_status": "stub_or_unverified"
  },
  "predictions_vs_outcomes": {
    "signal_raw": null,
    "signal_adjusted": null,
    "trades_vetoed_by_defense": 0,
    "adverse_selection_ticks": null,
    "signal_fill_direction_aligned_count": 1,
    "signal_fill_direction_aligned_ratio": 1.0
  },
  "per_trade_audit_summary": [
    {
      "side": "BUY",
      "tick_to_ack_us": 40.0,
      "feed_delay_us": 10.0,
      "decision_compute_us": 10.0,
      "decision_to_send_us": 10.0,
      "send_to_ack_us": 10.0,
      "net_pnl_contribution": -10.0,
      "signal": 0.85
    }
  ],
  "composition_trace": null,
  "skip_reasons": [],
  "pdf_citations_complete": true
}

## Edge Proposals (JSON Block)
[
  {
    "from": "AAR_SMOKE_TEST",
    "to": "CPI_2024_09_11_TIGHT",
    "relation": "backtest_run",
    "scope": "discovery_only"
  }
]

---

**End of Report**