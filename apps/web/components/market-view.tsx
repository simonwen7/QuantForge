"use client";

import { useRouter } from "next/navigation";

import { useExperiment } from "@/lib/experiment";
import { metricDisplay } from "@/lib/format";
import { stepForBarTime } from "@/lib/replay";
import { CandleChart } from "./charts";
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

export function MarketView() {
  const router = useRouter();
  const { setReplayIndex } = useExperiment();

  return (
    <ExperimentGate>
      {(session) => {
        const policy = primaryPolicy(session);
        const latest = [...session.signals].reverse().find((signal) => signal.action !== "no_signal");
        return (
          <>
            <div className="stage">
              <CandleChart
                bars={session.bars}
                fills={policy.fills ?? []}
                onSelectTime={(iso) => {
                  const index = stepForBarTime(session.trace, iso);
                  if (index >= 0) {
                    setReplayIndex(index);
                    router.push("/replay");
                  }
                }}
              />
              <div>
                <RunBar session={session} />
                <div className="panel context" style={{ marginTop: 12 }}>
                  <h2>Signal</h2>
                  {latest ? (
                    <p>
                      {latest.action} · {latest.feature_name} {latest.feature ?? "—"}
                      <br />
                      <span className="note">Available {latest.available_at}</span>
                    </p>
                  ) : (
                    <p className="note">No directional signal in this path.</p>
                  )}
                  <p className="note">
                    Markers are engine fills. Orders that did not fill are not drawn.
                  </p>
                </div>
              </div>
            </div>
            <div className="metric-strip">
              {policy.metrics.map((metric) => (
                <MetricCard key={metric.name} metric={metric} label={LABELS[metric.name] ?? metric.name} />
              ))}
              <article className="metric">
                <span>Posted fees</span>
                <b>{metricDisplay({ name: "fee", value: policy.posted_fees, value_hex: null, reason: null }).text}</b>
                <small>{policy.fill_count} fills · {policy.cost.fee_rate} fee</small>
              </article>
            </div>
          </>
        );
      }}
    </ExperimentGate>
  );
}
