"use client";

import { useState } from "react";

import { formatUtcLabel } from "@/lib/format";
import type { BacktestResponse } from "@/lib/types";

export function primaryPolicy(session: BacktestResponse) {
  return session.policies.find((item) => item.name === session.strategy) ?? session.policies[0];
}

export function RunBar({ session }: { session: BacktestResponse }) {
  const [copied, setCopied] = useState(false);

  async function copyId() {
    await navigator.clipboard.writeText(session.experiment_id);
    setCopied(true);
  }

  function exportJson() {
    const blob = new Blob([JSON.stringify(session, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `quantforge-${session.experiment_id.slice(0, 12)}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="context panel">
      <div className="row">
        <span className="badge badge-synthetic">SYNTHETIC</span>
        <span className="badge">{session.protocol_binding === "frozen_rule_parameters" ? "FROZEN PARAMS" : "EXPLORATORY"}</span>
      </div>
      <div>
        <h2>Provenance</h2>
        <p className="note">{session.scenario.name} · seed {session.scenario.seed} · {session.scenario.bar_count} bars</p>
        <p className="note">Origin {session.data_origin}</p>
      </div>
      <div className="id-line">
        <span className="eyebrow">Experiment</span>
        <code>{session.experiment_id}</code>
      </div>
      <div className="id-line">
        <span className="eyebrow">Scenario</span>
        <code>{session.scenario.scenario_id}</code>
      </div>
      <div className="id-line">
        <span className="eyebrow">Engine run</span>
        <code>{primaryPolicy(session).run_id}</code>
      </div>
      <p className="note">
        Latency is fixed at {session.latency_bars} hour. The clock starts {formatUtcLabel(session.bars[0]?.time ?? "")}.
      </p>
      <p className="note">
        {session.market_gaps.length === 0
          ? "No missing hour in this path."
          : `${session.market_gaps.length} missing hour detected by the engine.`}
      </p>
      <div className="actions">
        <button className="button" type="button" onClick={() => void copyId()}>
          {copied ? "Copied" : "Copy experiment ID"}
        </button>
        <button className="button" type="button" onClick={exportJson}>
          Export JSON
        </button>
      </div>
      <p className="statement">{session.statement}</p>
    </div>
  );
}
