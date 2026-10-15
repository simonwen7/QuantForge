"use client";

import { useEffect, useState } from "react";

import { ApiError, getResearchBlock } from "@/lib/api";
import type { ResearchBlock } from "@/lib/types";
import { ErrorBlock, LoadingBlock } from "./ui";

const NODES = [
  ["Scenario", "Deterministic June 2022 bars. Identity includes the generator version."],
  ["Strategies", "Momentum and mean reversion see only completed closes."],
  ["Engine", "Open, due fill, completed bar, signal, order, then valuation."],
  ["Ledger", "Decimal cash and BTC. Fees post with the fill."],
  ["Analytics", "M7 Sharpe, Sortino, volatility, and drawdown."],
  ["API", "Decimals stay strings. Undefined metrics keep a reason."],
  ["Terminal", "Charts convert strings to coordinates for display only."],
  ["Identity", "Scenario, engine run, and experiment ids stay distinct."],
];

export function SystemView() {
  const [block, setBlock] = useState<ResearchBlock | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  function load() {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void getResearchBlock(controller.signal)
      .then((next) => {
        setBlock(next);
        setLoading(false);
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof ApiError ? reason.message : "The research-block route failed.");
        setLoading(false);
      });
    return () => controller.abort();
  }

  useEffect(() => load(), []);

  return (
    <div>
      <div className="architecture">
        {NODES.map(([title, copy]) => (
          <article key={title} className="arch-node">
            <strong>{title}</strong>
            <span className="note">{copy}</span>
          </article>
        ))}
      </div>
      <section className="panel context" style={{ marginTop: 12 }}>
        <h2>Causal timing</h2>
        <p>
          A candle is usable after it completes. The engine waits one more hour before a fill. The trace is signal,
          order, fill, ledger, then equity. A missing hour stays missing.
        </p>
        <p className="note">
          This terminal replays synthetic_unverified paths. It does not read an exchange, a 2024 validation file, or
          the 2025 holdout.
        </p>
      </section>
      <section style={{ marginTop: 12 }}>
        {loading && !block ? <LoadingBlock label="Asking the blocked-study route." /> : null}
        {error ? <ErrorBlock message={error} onRetry={load} /> : null}
        {block ? (
          <div className="table-wrap panel">
            <table>
              <tbody>
                <tr>
                  <th>M9 protocol</th>
                  <td>{block.protocol_approved ? "approved" : "unapproved"}</td>
                </tr>
                <tr>
                  <th>Empirical acceptance</th>
                  <td>{block.empirical_acceptance_enabled ? "enabled" : "disabled"}</td>
                </tr>
                <tr>
                  <th>Empirical activation</th>
                  <td>{block.activation_enabled ? "enabled" : "disabled"}</td>
                </tr>
                <tr>
                  <th>Train</th>
                  <td>{block.train}</td>
                </tr>
                <tr>
                  <th>2024 validation</th>
                  <td>{block.validation}</td>
                </tr>
                <tr>
                  <th>2025 holdout</th>
                  <td>{block.holdout}</td>
                </tr>
                <tr>
                  <th>Requested scope</th>
                  <td>{block.requested_scope}</td>
                </tr>
                <tr>
                  <th>Access</th>
                  <td>{block.access_defect ?? "no defect string"}</td>
                </tr>
                <tr>
                  <th>Real-market licensing</th>
                  <td>unresolved</td>
                </tr>
                <tr>
                  <th>Protocol id</th>
                  <td className="mono">{block.protocol_id}</td>
                </tr>
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}
