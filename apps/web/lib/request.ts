import type { BacktestRequest, CompareResponse, CostName, ScenarioName, StrategyName } from "./types";

export const STRATEGIES: readonly StrategyName[] = ["momentum", "mean_reversion"];
export const SCENARIOS: readonly ScenarioName[] = [
  "uptrend",
  "downtrend",
  "sideways",
  "shock",
  "gap",
];
export const COSTS: readonly CostName[] = ["zero", "baseline", "pessimistic_2x"];

export const COST_RATES: Record<CostName, { fee: string; slippage: string }> = {
  zero: { fee: "0", slippage: "0" },
  baseline: { fee: "0.001", slippage: "0.0005" },
  pessimistic_2x: { fee: "0.002", slippage: "0.001" },
};

export const DEFAULT_REQUEST: BacktestRequest = {
  strategy: "momentum",
  lookback_hours: 24,
  scenario: "uptrend",
  seed: 7,
  bar_count: 96,
  cost: "baseline",
  entry_threshold: null,
};

export const LIMITS = {
  minBars: 48,
  maxBars: 168,
  compareMaxBars: 120,
  minSeed: 0,
  maxSeed: 10000,
  minLookback: 1,
  maxLookback: 48,
  maxThreshold: "0.5",
};

export function validateBacktest(input: BacktestRequest): string | null {
  if (!STRATEGIES.includes(input.strategy)) return "Choose momentum or mean reversion.";
  if (!SCENARIOS.includes(input.scenario)) return "Choose a supported synthetic scenario.";
  if (!COSTS.includes(input.cost)) return "Choose a supported cost preset.";
  if (!Number.isInteger(input.lookback_hours) || input.lookback_hours < 1 || input.lookback_hours > 48) {
    return "Lookback must be an integer from 1 to 48 hours.";
  }
  if (!Number.isInteger(input.seed) || input.seed < 0 || input.seed > 10000) {
    return "Seed must be an integer from 0 to 10,000.";
  }
  if (!Number.isInteger(input.bar_count) || input.bar_count < 48 || input.bar_count > 168) {
    return "Bar count must be an integer from 48 to 168.";
  }
  if (input.bar_count < input.lookback_hours + 8) {
    return "Bar count must leave at least 8 bars after the lookback.";
  }
  if (input.strategy === "momentum" && input.entry_threshold !== null) {
    return "Momentum does not take an entry threshold.";
  }
  if (input.strategy === "mean_reversion" && input.entry_threshold !== null) {
    if (!/^\d+(\.\d+)?$/.test(input.entry_threshold)) {
      return "Threshold must be a positive decimal.";
    }
    const numeric = Number(input.entry_threshold);
    if (!Number.isFinite(numeric) || numeric <= 0 || numeric > 0.5) {
      return "Threshold must be greater than 0 and at most 0.5.";
    }
  }
  return null;
}

export function backtestPayload(input: BacktestRequest): Record<string, unknown> {
  const body: Record<string, unknown> = {
    strategy: input.strategy,
    lookback_hours: input.lookback_hours,
    scenario: input.scenario,
    seed: input.seed,
    bar_count: input.bar_count,
    cost: input.cost,
  };
  if (input.strategy === "mean_reversion" && input.entry_threshold !== null) {
    body.entry_threshold = input.entry_threshold;
  }
  return body;
}

export function comparePayload(input: BacktestRequest): Record<string, unknown> | null {
  if (input.bar_count > LIMITS.compareMaxBars) return null;
  const body: Record<string, unknown> = {
    lookback_hours: input.lookback_hours,
    scenario: input.scenario,
    seed: input.seed,
    bar_count: input.bar_count,
  };
  if (input.strategy === "mean_reversion" && input.entry_threshold !== null) {
    body.entry_threshold = input.entry_threshold;
  }
  return body;
}

export function isCompareResponse(value: unknown): value is CompareResponse {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return record.data_origin === "synthetic_unverified" && Array.isArray(record.policies);
}
