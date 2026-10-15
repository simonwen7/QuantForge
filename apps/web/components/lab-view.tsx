"use client";

import { useEffect, useState } from "react";

import { useExperiment } from "@/lib/experiment";
import { COST_RATES, COSTS, DEFAULT_REQUEST, SCENARIOS, STRATEGIES, validateBacktest } from "@/lib/request";
import type { BacktestRequest } from "@/lib/types";
import { ExperimentGate } from "./gate";
import { RunBar } from "./run-bar";
import { Hint } from "./ui";

const SCENARIO_HELP: Record<string, string> = {
  uptrend: "Positive drift with pullbacks. Prices are generated, not observed.",
  downtrend: "Negative drift with rebounds on the same June 2022 clock.",
  sideways: "An oscillation large enough for the mean-reversion rule to act.",
  shock: "A short window of wider swings, then a quieter path.",
  gap: "One hourly open is omitted. The engine records the gap and does not interpolate it.",
};

export function LabView() {
  const { session, run, clear, status } = useExperiment();
  const [form, setForm] = useState<BacktestRequest>(DEFAULT_REQUEST);
  const [localError, setLocalError] = useState<string | null>(null);
  const busy = status === "loading";

  useEffect(() => {
    if (!session) return;
    setForm({
      strategy: session.strategy,
      lookback_hours: session.lookback_hours,
      scenario: session.scenario.name,
      seed: session.scenario.seed,
      bar_count: session.scenario.bar_count,
      cost: session.cost.name as BacktestRequest["cost"],
      entry_threshold: session.entry_threshold,
    });
  }, [session]);

  function update<K extends keyof BacktestRequest>(key: K, value: BacktestRequest[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function submit() {
    const next: BacktestRequest = {
      ...form,
      entry_threshold: form.strategy === "momentum" ? null : form.entry_threshold,
    };
    const message = validateBacktest(next);
    setLocalError(message);
    if (message) return;
    run(next);
  }

  return (
    <ExperimentGate>
      {(session) => (
        <div className="stage">
          <form
            className="panel context"
            onSubmit={(event) => {
              event.preventDefault();
              submit();
            }}
          >
            <h2>Configuration</h2>
            <div className="control-grid">
              <div className="field">
                <label htmlFor="strategy">Strategy</label>
                <select
                  id="strategy"
                  value={form.strategy}
                  onChange={(event) => update("strategy", event.target.value as BacktestRequest["strategy"])}
                >
                  {STRATEGIES.map((item) => (
                    <option key={item} value={item}>
                      {item === "momentum" ? "Momentum" : "Mean reversion"}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="lookback">
                  Lookback hours
                  <Hint label="Frozen research default is 24. Other values are exploratory and do not change the M9 protocol." />
                </label>
                <input
                  id="lookback"
                  type="number"
                  min={1}
                  max={48}
                  value={form.lookback_hours}
                  onChange={(event) => update("lookback_hours", Number(event.target.value))}
                />
              </div>
              <div className="field">
                <label htmlFor="threshold">
                  Mean-reversion threshold
                  <Hint label="Frozen default is 0.02. Momentum ignores this field." />
                </label>
                <input
                  id="threshold"
                  inputMode="decimal"
                  disabled={form.strategy !== "mean_reversion"}
                  placeholder="0.02"
                  value={form.entry_threshold ?? ""}
                  onChange={(event) => update("entry_threshold", event.target.value === "" ? null : event.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="scenario">
                  Scenario
                  <Hint label={SCENARIO_HELP[form.scenario]} />
                </label>
                <select
                  id="scenario"
                  value={form.scenario}
                  onChange={(event) => update("scenario", event.target.value as BacktestRequest["scenario"])}
                >
                  {SCENARIOS.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="seed">Seed</label>
                <input
                  id="seed"
                  type="number"
                  min={0}
                  max={10000}
                  value={form.seed}
                  onChange={(event) => update("seed", Number(event.target.value))}
                />
              </div>
              <div className="field">
                <label htmlFor="bars">Bar count</label>
                <input
                  id="bars"
                  type="number"
                  min={48}
                  max={168}
                  value={form.bar_count}
                  onChange={(event) => update("bar_count", Number(event.target.value))}
                />
              </div>
              <div className="field">
                <label htmlFor="cost">
                  Cost preset
                  <Hint label="zero is 0/0, baseline is 0.001 fee and 0.0005 slippage, pessimistic_2x doubles both." />
                </label>
                <select
                  id="cost"
                  value={form.cost}
                  onChange={(event) => update("cost", event.target.value as BacktestRequest["cost"])}
                >
                  {COSTS.map((item) => (
                    <option key={item} value={item}>
                      {item} · fee {COST_RATES[item].fee}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <p className="note">Execution latency is one hour. It is not a selectable setting.</p>
            <p className="note">
              {form.lookback_hours === 24 && (form.strategy === "momentum" || form.entry_threshold === null || form.entry_threshold === "0.02")
                ? "These parameter values match the frozen rule. That does not approve the M9 protocol."
                : "Exploratory parameters. protocol_binding will be exploratory."}
            </p>
            {localError ? <p role="alert">{localError}</p> : null}
            <div className="actions">
              <button className="button button-primary" type="submit" disabled={busy}>
                {busy ? "Running" : "Run backtest"}
              </button>
              <button
                className="button"
                type="button"
                onClick={() => {
                  setForm(DEFAULT_REQUEST);
                  setLocalError(null);
                }}
              >
                Reset to frozen preset
              </button>
              <button className="button" type="button" onClick={clear}>
                Clear experiment
              </button>
            </div>
          </form>
          <RunBar session={session} />
        </div>
      )}
    </ExperimentGate>
  );
}
