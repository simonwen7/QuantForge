import { backtestPayload, comparePayload, isCompareResponse } from "./request";
import {
  isBacktestResponse,
  type BacktestRequest,
  type BacktestResponse,
  type CompareResponse,
  type ResearchBlock,
} from "./types";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function readDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return "The request was rejected.";
  } catch {
    return "The API returned an unreadable error.";
  }
  return `Request failed (${response.status}).`;
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  return fetch(path, {
    ...init,
    headers: {
      accept: "application/json",
      ...(init?.body ? { "content-type": "application/json" } : {}),
      ...init?.headers,
    },
  });
}

export async function postBacktest(input: BacktestRequest, signal?: AbortSignal): Promise<BacktestResponse> {
  const response = await send("/api/backtest", {
    method: "POST",
    body: JSON.stringify(backtestPayload(input)),
    signal,
  });
  if (!response.ok) throw new ApiError(response.status, await readDetail(response));
  const payload: unknown = await response.json();
  if (!isBacktestResponse(payload)) throw new ApiError(502, "The API response was incomplete.");
  return payload;
}

export async function postCompare(input: BacktestRequest, signal?: AbortSignal): Promise<CompareResponse> {
  const body = comparePayload(input);
  if (body === null) {
    throw new ApiError(422, "Comparison accepts at most 120 bars. Lower the bar count in the lab.");
  }
  const response = await send("/api/compare", {
    method: "POST",
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw new ApiError(response.status, await readDetail(response));
  const payload: unknown = await response.json();
  if (!isCompareResponse(payload)) throw new ApiError(502, "The comparison response was incomplete.");
  return payload;
}

export async function getResearchBlock(signal?: AbortSignal): Promise<ResearchBlock> {
  const response = await send("/api/research-block", { signal });
  if (!response.ok) throw new ApiError(response.status, await readDetail(response));
  return (await response.json()) as ResearchBlock;
}
