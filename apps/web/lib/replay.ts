import type { TraceStep } from "./types";

export function clampPlayhead(index: number, length: number): number {
  if (length <= 0) return 0;
  if (index < 0) return 0;
  if (index >= length) return length - 1;
  return index;
}

export function fillStepIndex(trace: readonly TraceStep[], orderId: string): number {
  return trace.findIndex((step) => step.name === "fill" && step.fill?.order_id === orderId);
}

export function stepForBarTime(trace: readonly TraceStep[], isoTime: string): number {
  const seconds = Math.floor(Date.parse(isoTime) / 1000);
  return trace.findIndex((step) => {
    if (step.name !== "fill" || step.fill === null) return false;
    return Math.floor(Date.parse(step.fill.time) / 1000) === seconds;
  });
}

export function advancePlayhead(index: number, length: number): { index: number; finished: boolean } {
  if (length <= 0) return { index: 0, finished: true };
  if (index >= length - 1) return { index: length - 1, finished: true };
  return { index: index + 1, finished: false };
}
