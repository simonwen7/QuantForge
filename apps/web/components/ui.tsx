"use client";

import type { ReactNode } from "react";

import { metricDisplay, reasonLabel } from "@/lib/format";
import type { Metric } from "@/lib/types";

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "synthetic" | "tide" | "loss";
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Hint({ label }: { label: string }) {
  return (
    <span className="hint" tabIndex={0}>
      <span className="hint-mark" aria-hidden="true">
        i
      </span>
      <span className="hint-bubble" role="tooltip">
        {label}
      </span>
    </span>
  );
}

export function MetricCard({ metric, label }: { metric: Metric; label: string }) {
  const display = metricDisplay(metric);
  return (
    <article className={`metric${display.state === "undefined" ? " undefined" : ""}`}>
      <span>{label}</span>
      <b title={display.detail ?? undefined}>{display.text}</b>
      <small>
        {display.state === "undefined"
          ? reasonLabel(display.reason ?? "unspecified")
          : `${metric.observation_count} observations · ${metric.coverage}`}
      </small>
    </article>
  );
}

export function LoadingBlock({ label }: { label: string }) {
  return (
    <div className="skeleton-card" role="status" aria-live="polite">
      <p>{label}</p>
      <div className="skeleton chart-skeleton" />
      <div className="skeleton" />
      <div className="skeleton" />
    </div>
  );
}

export function ErrorBlock({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="empty" role="alert">
      <h2>The research API did not return a result</h2>
      <p>{message}</p>
      <button className="button button-primary" type="button" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

export function EmptyBlock({ onLoad }: { onLoad: () => void }) {
  return (
    <div className="empty">
      <h2>No experiment in this session</h2>
      <p>Run the default synthetic momentum path, or configure one in the lab.</p>
      <button className="button button-primary" type="button" onClick={onLoad}>
        Load default experiment
      </button>
    </div>
  );
}
