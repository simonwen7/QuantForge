export type StrategyName = "momentum" | "mean_reversion";
export type ScenarioName = "uptrend" | "downtrend" | "sideways" | "shock" | "gap";
export type CostName = "zero" | "baseline" | "pessimistic_2x";

export interface BacktestRequest {
  strategy: StrategyName;
  lookback_hours: number;
  scenario: ScenarioName;
  seed: number;
  bar_count: number;
  cost: CostName;
  entry_threshold: string | null;
}

export interface Bar {
  time: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
}

export interface Metric {
  name: string;
  unit: string;
  value: string | null;
  value_hex: string | null;
  reason: string | null;
  observation_count: number;
  coverage: string;
}

export interface EquityPoint {
  time: string;
  equity: string;
  drawdown: string;
}

export interface FillRow {
  time: string;
  side: string;
  execution_price: string;
  posted_fee: string;
  order_id: string;
}

export interface Policy {
  name: string;
  tied_to: string;
  run_id: string;
  fill_count: number;
  posted_fees: string;
  hourly_grid_complete: boolean;
  cost: { fee_rate: string; slippage_rate: string };
  metrics: Metric[];
  equity: EquityPoint[];
  fills?: FillRow[];
}

export interface SignalRow {
  action: string;
  available_at: string;
  open_time: string;
  feature_name: string;
  feature: string | null;
  reason: string | null;
}

export interface TraceStep {
  index: number;
  time: string;
  phase: number;
  name: string;
  order_id: string | null;
  signal: SignalRow | null;
  order: {
    order_id: string;
    side: string;
    state: string;
    due_time: string;
    submitted_at: string;
    reason: string | null;
  } | null;
  fill: {
    fill_id: string;
    order_id: string;
    side: string;
    execution_price: string;
    posted_fee: string;
    quantity: string;
    time: string;
  } | null;
  ledger: {
    cash_after: string;
    btc_after: string;
    cash_delta: string;
    fee: string;
  } | null;
  equity: {
    equity: string;
    cash: string;
    btc: string;
    price: string;
    price_kind: string;
  } | null;
  stale: {
    cash: string;
    btc: string;
    last_price: string;
    last_price_time: string;
  } | null;
  open: string | null;
  complete: {
    open: string;
    high: string;
    low: string;
    close: string;
    volume: string;
  } | null;
}

export interface MarketGap {
  previous_open_time: string;
  next_open_time: string;
}

export interface BacktestResponse {
  data_origin: "synthetic_unverified";
  statement: string;
  experiment_id: string;
  config_id: string;
  protocol_binding: string;
  protocol_approved: false;
  strategy: StrategyName;
  lookback_hours: number;
  entry_threshold: string | null;
  latency_bars: 1;
  cost: { name: string; fee_rate: string; slippage_rate: string };
  scenario: {
    name: ScenarioName;
    seed: number;
    bar_count: number;
    scenario_id: string;
    generator_version: string;
  };
  engine_run_ids: Record<string, string>;
  market_gaps: MarketGap[];
  bars: Bar[];
  signals: SignalRow[];
  policies: Policy[];
  trace: TraceStep[];
}

export interface CompareResponse {
  data_origin: "synthetic_unverified";
  statement: string;
  experiment_id: string;
  protocol_binding: string;
  protocol_approved: false;
  lookback_hours: number;
  entry_threshold: string;
  scenario: BacktestResponse["scenario"];
  policies: Policy[];
}

export interface ResearchBlock {
  protocol_approved: boolean;
  empirical_acceptance_enabled: boolean;
  activation_enabled: boolean;
  empirical_table_allowed: boolean;
  train: string;
  validation: string;
  holdout: string;
  requested_scope: string;
  protocol_id: string;
  preflight_id: string;
  access_defect: string | null;
  engine_version: string;
  analytics_version: string;
}

export function isBacktestResponse(value: unknown): value is BacktestResponse {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    record.data_origin === "synthetic_unverified" &&
    typeof record.experiment_id === "string" &&
    Array.isArray(record.bars) &&
    Array.isArray(record.policies) &&
    Array.isArray(record.trace)
  );
}
