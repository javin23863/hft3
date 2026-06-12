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
  stages: Stage[];
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
  mean_expectancy_usd: number | null;
  worst_event_tail_usd: number | null;
}

export interface ModelsZone {
  zone: "models";
  generated_utc: string;
  health: Health;
  registry_total: number;
  funnel: Record<string, number>;
  silent_zero: { count: number; hypotheses: { id: number; name: string }[]; note: string };
  rows: ModelRow[];
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
  lifecycle?: LifecycleZone;
  autonomy?: AutonomyZone;
  system?: SystemZone;
  alerts?: AlertsZone;
}
