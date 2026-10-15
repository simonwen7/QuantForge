"use client";

import type { ReactNode } from "react";

import { useExperiment } from "@/lib/experiment";
import type { BacktestResponse } from "@/lib/types";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "./ui";

export function ExperimentGate({
  children,
}: {
  children: (session: BacktestResponse) => ReactNode;
}) {
  const { session, status, error, loadDefault } = useExperiment();
  if (!session && status === "loading") {
    return <LoadingBlock label="Running the default synthetic momentum experiment." />;
  }
  if (!session && status === "error") {
    return <ErrorBlock message={error ?? "The API is unavailable."} onRetry={loadDefault} />;
  }
  if (!session) return <EmptyBlock onLoad={loadDefault} />;
  return (
    <>
      {error ? (
        <div className="banner" role="alert">
          {error}
        </div>
      ) : null}
      {status === "loading" ? <p className="note">Running the requested experiment.</p> : null}
      {children(session)}
    </>
  );
}
