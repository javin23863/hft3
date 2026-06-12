export interface ModelConstruction {
  class_name: string;
  thesis: string;
  how_it_works: string;
  features: string[];
  event_gates: string[];
  regime_gates: string[];
  cross_assets: string[];
  citations: string[];
  evaluate_source: string;
  defect_note?: string;
}

export interface StageACell {
  event_type: string;
  band_ms: number;
  n_events: number;
  total_trades: number;
  mean_expectancy_usd: number | null;
  mean_win_rate: number | null;
  p5_expectancy_tail_usd: number | null;
}

export interface ModelDetail {
  id: number;
  name: string;
  family: string;
  structurally_dead: boolean;
  generated_utc: string;
  error?: string;
  construction: ModelConstruction | null;
  results: {
    stage_a_cells: StageACell[];
    n_event_types: number;
    total_trades: number;
    card: Record<string, unknown> | null;
  };
}
