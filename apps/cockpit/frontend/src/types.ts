// Loose zone payload shapes — mirror the backend aggregators. Kept permissive
// (index signatures) so the UI tolerates additive backend fields.

export type Status = "ok" | "running" | "stale" | "fail" | "missing" | "unknown";
export type Health = "green" | "amber" | "red";
export type Severity = "info" | "warn" | "crit";

export interface Stage {
  id: string;
  label: string;
  status: Status;
  [k: string]: unknown;
}

export interface PipelineZone {
  zone: "pipeline";
  generated_utc: string;
  health: Health;
  latency_evidence?: PipelineLatencyEvidence;
  stages: Stage[];
}

export interface PipelineLatencyEvidence {
  status: Status;
  detail?: string;
  ack_p99_us?: number | null;
  m6_band_ms?: number | null;
  offensive_engine_us?: number | null;
  offensive_baseline_tick_to_send_us?: number | null;
  offensive_baseline_decision_to_send_us?: number | null;
  offensive_latest_decision_to_send_p50_us?: number | null;
  offensive_latest_decision_to_send_p99_us?: number | null;
  defensive_cancel_to_send_us?: number | null;
  defensive_cancel_ack_status?: string;
  live_readiness_status?: Status;
  [k: string]: unknown;
}

export interface ControlTrackedJob {
  job_id?: string;
  name?: string;
  host?: string;
  state?: string;
}

export interface ControlStatus {
  exec_enabled: boolean;
  execution_mode: string;
  jobs: Record<string, string>;
  tracked_jobs: ControlTrackedJob[];
  note?: string;
}

export interface ControlJobLog {
  job_id: string;
  name?: string;
  host?: string;
  state?: string;
  command?: unknown;
  executed?: boolean;
  returncode?: number | null;
  note?: string | null;
  error?: string | null;
  log_lines: number;
  log_tail: string;
}

export interface PortfolioZone {
  zone: "portfolio";
  generated_utc: string;
  health: Health;
  live_session: boolean;
  execution_mode: string;
  banner: string | null;
  positions: unknown[];
  pnl: { net_pnl: number | null; realized: number | null; unrealized: number | null; expected_shortfall_5pct: number | null };
  adverse_selection_ticks: Record<string, number> | null;
  fills: { total: number; recent: Record<string, string>[] };
}

export interface ModelRow {
  id: number;
  name: string;
  family: string;
  structurally_dead: boolean;
  status: string;
  total_trades: number;
  n_event_types: number;
  n_events?: number;
  n_events_with_vix?: number;
  vix_coverage_pct?: number | null;
  mean_expectancy_usd: number | null;
  worst_event_tail_usd: number | null;
}

export interface ModelVixCoverage {
  status: string;
  cell_event_observations: number;
  cell_event_observations_with_vix: number;
  cells_with_vix: number;
  invalid_cells?: number;
  coverage_pct: number | null;
  note: string;
  authority_sources?: Array<Record<string, string>>;
}

export interface ModelsZone {
  zone: "models";
  generated_utc: string;
  health: Health;
  registry_total: number;
  funnel: Record<string, number>;
  silent_zero: { count: number; hypotheses: { id: number; name: string }[]; note: string };
  vix_coverage?: ModelVixCoverage;
  rows: ModelRow[];
}

export interface OptionsZone {
  zone: "options";
  generated_utc: string;
  health: Health;
  lane: string;
  model_id_prefix: string;
  phase: string;
  research_backtest_status: string;
  research_backtest_detail?: string;
  execution_status: string;
  research_only: boolean;
  data_readiness: Record<string, unknown>;
  defect_ledger: Record<string, unknown>;
  context_feature_coverage: Record<string, unknown>;
  standalone_model_evidence: Record<string, unknown>;
  legacy_options_fixture_evidence: Record<string, unknown>;
  shadow_live_status: string;
  shadow_live_blockers: string[];
  controls: Record<string, unknown>;
  authority_sources?: string[];
}

export interface SystemZone {
  zone: "system";
  generated_utc: string;
  health: Health;
  latency: Record<string, unknown>;
  slow_tier: Record<string, unknown>;
  certification: Record<string, unknown>;
  databento: Record<string, unknown>;
  capture: Record<string, unknown>;
  execution: Record<string, unknown>;
  lanes?: Record<string, unknown>;
}

export interface Alert {
  id: string;
  severity: Severity;
  source: string;
  message: string;
  ts: string | null;
}

export interface AlertsZone {
  zone: "alerts";
  generated_utc: string;
  health: Health;
  count: number;
  alerts: Alert[];
}

export interface LifecycleRow {
  id: string;
  hypothesis_id: number | null;
  symbol: string | null;
  state: string;
  dot: string;
  since: string | null;
  route: string | null;
  demotion_reason: string | null;
  last_revalidation: string | null;
  envelope_id: string | null;
}

export interface LifecycleZone {
  zone: "lifecycle";
  generated_utc: string;
  health: Health;
  total_models: number;
  funnel: Record<string, number>;
  live: number;
  rows: LifecycleRow[];
  registered: boolean;
  note: string | null;
}

export interface AutonomyZone {
  zone: "autonomy";
  generated_utc: string;
  health: Health;
  available: boolean;
  master_enabled?: boolean;
  env_enabled?: boolean;
  file_enabled?: boolean;
  kill_engaged?: boolean;
  can_arm_live?: boolean;
  actions?: Record<string, boolean>;
  rearm_allow_live?: boolean;
  breaker?: { frozen: boolean; reason: string; frozen_at: string };
  audit_chain_ok?: boolean;
  jobs?: Record<string, number>;
  recent_audit?: Array<Record<string, unknown>>;
  error?: string;
}

export interface Zones {
  pipeline?: PipelineZone;
  portfolio?: PortfolioZone;
  models?: ModelsZone;
  options?: OptionsZone;
  lifecycle?: LifecycleZone;
  autonomy?: AutonomyZone;
  system?: SystemZone;
  alerts?: AlertsZone;
}
