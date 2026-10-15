"use client";

import { formatDecimal } from "@/lib/format";
import type { Policy } from "@/lib/types";
import { LineChart } from "./charts";
import { ExperimentGate } from "./gate";
import { primaryPolicy, RunBar } from "./run-bar";
import { MetricCard } from "./ui";

const LABELS: Record<string, string> = {
  total_return: "Total return",
  maximum_drawdown: "Max drawdown",
  annualized_volatility: "Ann. volatility",
  sharpe_ratio: "Sharpe",
  sortino_ratio: "Sortino",
};

function policyLine(policy: Policy, color: string) {
  return {
    name: policy.name,
    color,
    points: policy.equity.map((point) => ({ time: point.time, value: point.equity })),
  };
}

export function PerformanceView() {
  return (
    <ExperimentGate>
      {(session) => {
        const primary = primaryPolicy(session);
        const cash = session.policies.find((item) => item.name === "cash");
        const hold = session.policies.find((item) => item.name === "buy_and_hold");
        const series = [
          policyLine(primary, "#3dbeb6"),
          ...(cash ? [policyLine(cash, "#a39b8d")] : []),
          ...(hold ? [policyLine(hold, "#e7d7a1")] : []),
        ];
        const drawdown = [
          {
            name: "drawdown",
            color: "#c4655a",
            points: primary.equity.map((point) => ({ time: point.time, value: point.drawdown })),
          },
        ];
        return (
          <div className="stage">
            <div>
              <LineChart series={series} label="Equity, matching the strategy entry clock" />
              <div style={{ height: 12 }} />
              <LineChart series={drawdown} label="Drawdown from the running peak" />
              <div className="metric-strip">
                {primary.metrics.map((metric) => (
                  <MetricCard key={metric.name} metric={metric} label={LABELS[metric.name] ?? metric.name} />
                ))}
              </div>
              <div className="table-wrap panel" style={{ marginTop: 12 }}>
                <table>
                  <thead>
                    <tr>
                      <th>Policy</th>
                      <th>Fills</th>
                      <th>Posted fees</th>
                      <th>Hourly grid</th>
                      <th>Run ID</th>
                    </tr>
                  </thead>
                  <tbody>
                    {session.policies.map((policy) => (
                      <tr key={policy.run_id}>
                        <td>{policy.name}</td>
                        <td>{policy.fill_count}</td>
                        <td>{formatDecimal(policy.posted_fees, 6)}</td>
                        <td>{policy.hourly_grid_complete ? "complete" : "incomplete"}</td>
                        <td className="mono">{policy.run_id.slice(0, 16)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="note">
                Cost {session.cost.name}: fee {session.cost.fee_rate}, slippage {session.cost.slippage_rate}. Figures
                come from the Python analytics module.
              </p>
            </div>
            <RunBar session={session} />
          </div>
        );
      }}
    </ExperimentGate>
  );
}
