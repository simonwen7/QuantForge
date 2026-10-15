import type { Metric } from "./types";

const RATIO_NAMES = new Set(["sharpe_ratio", "sortino_ratio", "annualized_volatility"]);

export function formatDecimal(value: string, digits = 2): string {
  const negative = value.startsWith("-");
  const raw = negative ? value.slice(1) : value;
  const [whole, fraction = ""] = raw.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const shown = (fraction + "0".repeat(digits)).slice(0, digits);
  return `${negative ? "−" : ""}${grouped}${digits > 0 ? `.${shown}` : ""}`;
}

export function hexFloatToNumber(hex: string): number {
  const match = /^([+-]?)0x([0-9a-f]+)\.?([0-9a-f]*)p([+-]?\d+)$/i.exec(hex.trim());
  if (!match) return Number.NaN;
  const sign = match[1] === "-" ? -1 : 1;
  const fraction = match[3] ?? "";
  const significand = Number.parseInt(`${match[2]}${fraction}`, 16);
  const exponent = Number(match[4]) - 4 * fraction.length;
  const value = sign * significand * 2 ** exponent;
  return Number.isFinite(value) ? value : Number.NaN;
}

export function decimalToChart(value: string): number {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    throw new Error("chart coordinate is not finite");
  }
  return number;
}

export function utcSeconds(iso: string): number {
  const millis = Date.parse(iso);
  if (!Number.isFinite(millis)) {
    throw new Error("timestamp is not usable on the chart");
  }
  return Math.floor(millis / 1000);
}

export function formatUtcLabel(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${date.toISOString().replace(".000Z", "Z").replace("T", " ")} UTC`;
}

export interface MetricDisplay {
  state: "value" | "undefined";
  text: string;
  detail: string | null;
  reason: string | null;
}

export function metricDisplay(metric: Pick<Metric, "name" | "value" | "value_hex" | "reason">): MetricDisplay {
  if (metric.value !== null) {
    const digits = metric.name === "total_return" || metric.name === "maximum_drawdown" ? 4 : 6;
    return {
      state: "value",
      text: formatDecimal(metric.value, digits),
      detail: metric.value,
      reason: null,
    };
  }
  if (metric.value_hex !== null) {
    const number = hexFloatToNumber(metric.value_hex);
    const digits = RATIO_NAMES.has(metric.name) ? 3 : 4;
    return {
      state: "value",
      text: Number.isFinite(number) ? number.toFixed(digits) : metric.value_hex,
      detail: metric.value_hex,
      reason: null,
    };
  }
  return {
    state: "undefined",
    text: "Undefined",
    detail: null,
    reason: metric.reason,
  };
}

export function reasonLabel(reason: string): string {
  return reason.replaceAll("_", " ");
}

export function phaseLabel(phase: number): string {
  const names: Record<number, string> = {
    1: "Bar open",
    2: "Fill due",
    3: "Bar complete",
    4: "Signal availability",
    5: "Order submission",
    6: "Portfolio valuation",
  };
  return names[phase] ?? `Phase ${phase}`;
}
