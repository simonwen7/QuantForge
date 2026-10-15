"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ApiError, postBacktest, postCompare } from "./api";
import { DEFAULT_REQUEST } from "./request";
import { isBacktestResponse, type BacktestRequest, type BacktestResponse, type CompareResponse } from "./types";

const SESSION_KEY = "quantforge.experiment.v1";
const COMPARE_KEY = "quantforge.compare.v1";

type Status = "loading" | "ready" | "error" | "empty";

interface ExperimentContextValue {
  session: BacktestResponse | null;
  status: Status;
  error: string | null;
  replayIndex: number;
  setReplayIndex: (index: number) => void;
  comparison: CompareResponse | null;
  compareStatus: Status;
  compareError: string | null;
  run: (input: BacktestRequest) => void;
  loadDefault: () => void;
  clear: () => void;
  runComparison: (input: BacktestRequest) => void;
}

const ExperimentContext = createContext<ExperimentContextValue | null>(null);

function readStoredSession(): BacktestResponse | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return isBacktestResponse(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function ExperimentProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<BacktestResponse | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [error, setError] = useState<string | null>(null);
  const [replayIndex, setReplayIndex] = useState(0);
  const [comparison, setComparison] = useState<CompareResponse | null>(null);
  const [compareStatus, setCompareStatus] = useState<Status>("empty");
  const [compareError, setCompareError] = useState<string | null>(null);
  const runToken = useRef(0);
  const compareToken = useRef(0);
  const booted = useRef(false);
  const sessionRef = useRef<BacktestResponse | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  sessionRef.current = session;

  const run = useCallback((input: BacktestRequest) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const token = ++runToken.current;
    setStatus("loading");
    setError(null);
    void postBacktest(input, controller.signal)
      .then((next) => {
        if (token !== runToken.current) return;
        setSession(next);
        setReplayIndex(0);
        setComparison(null);
        setCompareStatus("empty");
        setCompareError(null);
        setStatus("ready");
        try {
          sessionStorage.setItem(SESSION_KEY, JSON.stringify(next));
          sessionStorage.removeItem(COMPARE_KEY);
        } catch {
          setError("The result is on screen, but this browser did not store it for the next visit.");
        }
      })
      .catch((reason: unknown) => {
        if (token !== runToken.current) return;
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        const message = reason instanceof ApiError ? reason.message : "The backtest request failed.";
        setError(message);
        setStatus(sessionRef.current ? "ready" : "error");
      });
  }, []);

  const loadDefault = useCallback(() => {
    run(DEFAULT_REQUEST);
  }, [run]);

  const clear = useCallback(() => {
    abortRef.current?.abort();
    runToken.current += 1;
    compareToken.current += 1;
    setSession(null);
    setComparison(null);
    setReplayIndex(0);
    setError(null);
    setCompareError(null);
    setStatus("empty");
    setCompareStatus("empty");
    sessionStorage.removeItem(SESSION_KEY);
    sessionStorage.removeItem(COMPARE_KEY);
  }, []);

  const runComparison = useCallback((input: BacktestRequest) => {
    const token = ++compareToken.current;
    setCompareStatus("loading");
    setCompareError(null);
    void postCompare(input)
      .then((next) => {
        if (token !== compareToken.current) return;
        setComparison(next);
        setCompareStatus("ready");
        try {
          sessionStorage.setItem(COMPARE_KEY, JSON.stringify(next));
        } catch {
          setCompareError("The comparison is on screen, but this browser did not store it.");
        }
      })
      .catch((reason: unknown) => {
        if (token !== compareToken.current) return;
        const message = reason instanceof ApiError ? reason.message : "The comparison request failed.";
        setCompareError(message);
        setCompareStatus("error");
      });
  }, []);

  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    const stored = readStoredSession();
    if (stored) {
      setSession(stored);
      setStatus("ready");
      return;
    }
    loadDefault();
  }, [loadDefault]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const value = useMemo(
    () => ({
      session,
      status,
      error,
      replayIndex,
      setReplayIndex,
      comparison,
      compareStatus,
      compareError,
      run,
      loadDefault,
      clear,
      runComparison,
    }),
    [
      session,
      status,
      error,
      replayIndex,
      comparison,
      compareStatus,
      compareError,
      run,
      loadDefault,
      clear,
      runComparison,
    ],
  );

  return <ExperimentContext.Provider value={value}>{children}</ExperimentContext.Provider>;
}

export function useExperiment(): ExperimentContextValue {
  const value = useContext(ExperimentContext);
  if (value === null) throw new Error("ExperimentProvider is missing");
  return value;
}
