"use client";

import { useState } from "react";

import { useExperiment } from "@/lib/experiment";
import { metricDisplay } from "@/lib/format";
import { COSTS, COST_RATES, LIMITS } from "@/lib/request";
import type { BacktestRequest, CostName, Policy } from "@/lib/types";
import { LineChart } from "./charts";
import { ExperimentGate } from "./gate";
import { RunBar } from "./run-bar";

const COLORS = ["#3dbeb6", "#e7d7a1", "#a39b8d", "#c4655a", "#8aa2c8", "#d7c4a3"];

function costOf(policy: Policy): CostName | null {
  return (
    COSTS.find(
      (name) =>
        COST_RATES[name].fee === policy.cost.fee_rate &&
        COST_RATES[name].slippage === policy.cost.slippage_rate,
    ) ?? null
  );
}

function requestFromSession(session: {
  strategy: BacktestRequest["strategy"];
  lookback_hours: number;
  entry_threshold: string | null;
  scenario: { name: BacktestRequest["scenario"]; seed: number; bar_count: number };
  cost: { name: string };
}): BacktestRequest {
  return {
    strategy: session.strategy,
    lookback_hours: session.lookback_hours,
    scenario: session.scenario.name,
    seed: session.scenario.seed,
    bar_count: session.scenario.bar_count,
    cost: session.cost.name as CostName,
    entry_threshold: session.entry_threshold,
  };
}

export function CompareView() {
  const { comparison, compareStatus, compareError, runComparison } = useExperiment();
  const [cost, setCost] = useState<CostName>("baseline");

  return (
    <ExperimentGate>
      {(session) => {
        const request = requestFromSession(session);
        const tooWide = request.bar_count > LIMITS.compareMaxBars;
        const plotted = comparison?.policies.filter((policy) => costOf(policy) === cost) ?? [];
        const series = plotted.map((policy, index) => ({
          name: `${policy.tied_to} ${policy.name}`,
          color: COLORS[index % COLORS.length],
          points: policy.equity.map((point) => ({ time: point.time, value: point.equity })),
        }));
        return (
          <div>
            <div className="actions" style={{ marginBottom: 12 }}>
              <button
                className="button button-primary"
                type="button"
                disabled={tooWide || compareStatus === "loading"}
                onClick={() => runComparison(request)}
              >
                {compareStatus === "loading" ? "Comparing" : "Run comparison"}
              </button>
              {COSTS.map((item) => (
                <button
                  key={item}
                  className="button"
                  type="button"
                  aria-pressed={cost === item}
                  onClick={() => setCost(item)}
                >
                  {item}
                </button>
              ))}
            </div>
            {tooWide ? (
              <p className="note">Comparison accepts at most 120 bars. Lower the bar count in the lab.</p>
            ) : (
              <p className="note">
                Same scenario, seed, and clock. Every policy and cost stays visible. Nothing here is ranked or recommended.
              </p>
            )}
            {compareError ? (
              <div className="banner" role="alert">
                {compareError}
              </div>
            ) : null}
            {comparison ? (
              <>
                <LineChart series={series} label={`Equity at ${cost}. UTC. Not ranked.`} />
                <div className="row" style={{ marginTop: 8 }}>
                  {series.map((item) => (
                    <span key={item.name} className="note">
                      <span
                        style={{
                          display: "inline-block",
                          width: 8,
                          height: 8,
                          marginRight: 6,
                          background: item.color,
                        }}
                      />
                      {item.name}
                    </span>
                  ))}
                </div>
                <div className="table-wrap panel" style={{ marginTop: 12 }}>
                  <table>
                    <caption className="note" style={{ captionSide: "bottom", textAlign: "left", padding: 10 }}>
                      Experiment {comparison.experiment_id}. Scenario {comparison.scenario.scenario_id}.
                    </caption>
                    <thead>
                      <tr>
                        <th>Tied to</th>
                        <th>Policy</th>
                        <th>Cost</th>
                        <th>Total return</th>
                        <th>Sharpe</th>
                        <th>Fees</th>
                        <th>Fills</th>
                        <th>Run</th>
                      </tr>
                    </thead>
                    <tbody>
                      {comparison.policies.map((policy) => {
                        const total = policy.metrics.find((metric) => metric.name === "total_return");
                        const sharpe = policy.metrics.find((metric) => metric.name === "sharpe_ratio");
                        return (
                          <tr key={`${policy.tied_to}-${policy.name}-${policy.run_id}`}>
                            <td>{policy.tied_to}</td>
                            <td>{policy.name}</td>
                            <td>{costOf(policy) ?? "cost"}</td>
                            <td>{total ? metricDisplay(total).text : "Undefined"}</td>
                            <td>
                              {sharpe
                                ? sharpe.reason
                                  ? `Undefined · ${sharpe.reason.replaceAll("_", " ")}`
                                  : metricDisplay(sharpe).text
                                : "Undefined"}
                            </td>
                            <td>{policy.posted_fees}</td>
                            <td>{policy.fill_count}</td>
                            <td className="mono">{policy.run_id.slice(0, 12)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            ) : compareStatus === "loading" ? (
              <p className="note">Calculating every policy and cost preset.</p>
            ) : (
              <div className="empty">
                <h2>No comparison yet</h2>
                <p>The table is filled only after the Python comparison route returns.</p>
              </div>
            )}
            <div style={{ marginTop: 12 }}>
              <RunBar session={session} />
            </div>
          </div>
        );
      }}
    </ExperimentGate>
  );
}
