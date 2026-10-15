"use client";

import { useEffect, useId, useRef, useState } from "react";
import type {
  IChartApi,
  ISeriesApi,
  SeriesMarker,
  UTCTimestamp,
} from "lightweight-charts";

import { decimalToChart, formatDecimal, formatUtcLabel, utcSeconds } from "@/lib/format";
import type { Bar, FillRow } from "@/lib/types";

interface CandleProps {
  bars: Bar[];
  fills: FillRow[];
  highlightTime?: string | null;
  onSelectTime?: (isoTime: string) => void;
}

export function CandleChart({ bars, fills, highlightTime, onSelectTime }: CandleProps) {
  const nodeRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const [hover, setHover] = useState<string>("Crosshair reads the synthetic bar under the pointer.");
  const [ready, setReady] = useState(0);
  const selectRef = useRef(onSelectTime);
  selectRef.current = onSelectTime;
  const labelId = useId();

  useEffect(() => {
    const node = nodeRef.current;
    if (!node || bars.length === 0) return;
    let active = true;
    let chart: IChartApi | null = null;
    void import("lightweight-charts").then((lc) => {
      if (!active || !nodeRef.current) return;
      chart = lc.createChart(node, {
        autoSize: true,
        layout: {
          background: { color: "#141716" },
          textColor: "#a39b8d",
          fontFamily: "IBM Plex Sans, sans-serif",
        },
        grid: {
          vertLines: { color: "rgba(243, 239, 230, 0.06)" },
          horzLines: { color: "rgba(243, 239, 230, 0.06)" },
        },
        rightPriceScale: { borderColor: "rgba(243, 239, 230, 0.12)" },
        timeScale: { borderColor: "rgba(243, 239, 230, 0.12)", timeVisible: true, secondsVisible: false },
        crosshair: { mode: lc.CrosshairMode.Normal },
        localization: { locale: "en-US" },
      });
      const candles = chart.addCandlestickSeries({
        upColor: "#3dbeb6",
        downColor: "#c4655a",
        borderUpColor: "#3dbeb6",
        borderDownColor: "#c4655a",
        wickUpColor: "#3dbeb6",
        wickDownColor: "#c4655a",
      });
      const volume = chart.addHistogramSeries({
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      });
      chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
      candles.setData(
        bars.map((bar) => ({
          time: utcSeconds(bar.time) as UTCTimestamp,
          open: decimalToChart(bar.open),
          high: decimalToChart(bar.high),
          low: decimalToChart(bar.low),
          close: decimalToChart(bar.close),
        })),
      );
      volume.setData(
        bars.map((bar) => ({
          time: utcSeconds(bar.time) as UTCTimestamp,
          value: decimalToChart(bar.volume),
          color: Number(bar.close) >= Number(bar.open) ? "rgba(61, 190, 182, 0.45)" : "rgba(196, 101, 90, 0.45)",
        })),
      );
      chart.timeScale().fitContent();
      chart.subscribeCrosshairMove((param) => {
        const point = param.seriesData.get(candles) as { open?: number; high?: number; low?: number; close?: number } | undefined;
        if (!param.time || !point || point.open === undefined || point.close === undefined) {
          setHover("Crosshair reads the synthetic bar under the pointer.");
          return;
        }
        const matched = bars.find((bar) => utcSeconds(bar.time) === Number(param.time));
        setHover(
          matched
            ? `${formatUtcLabel(matched.time)}  O ${formatDecimal(matched.open, 2)}  H ${formatDecimal(matched.high, 2)}  L ${formatDecimal(matched.low, 2)}  C ${formatDecimal(matched.close, 2)}  V ${formatDecimal(matched.volume, 0)}`
            : "Crosshair reads the synthetic bar under the pointer.",
        );
      });
      chart.subscribeClick((param) => {
        if (!param.time) return;
        const matched = bars.find((bar) => utcSeconds(bar.time) === Number(param.time));
        if (matched) selectRef.current?.(matched.time);
      });
      chartRef.current = chart;
      seriesRef.current = candles;
      setReady((value) => value + 1);
    });
    return () => {
      active = false;
      chart?.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [bars]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;
    const barTimes = new Set(bars.map((bar) => utcSeconds(bar.time)));
    const markers: SeriesMarker<UTCTimestamp>[] = fills
      .filter((fill) => barTimes.has(utcSeconds(fill.time)))
      .map((fill) => ({
        time: utcSeconds(fill.time) as UTCTimestamp,
        position: fill.side === "buy" ? "belowBar" : "aboveBar",
        color: fill.side === "buy" ? "#3dbeb6" : "#c4655a",
        shape: fill.side === "buy" ? "arrowUp" : "arrowDown",
        text: fill.side === "buy" ? "Buy" : "Sell",
      }));
    series.setMarkers(markers);
    if (highlightTime && barTimes.has(utcSeconds(highlightTime))) {
      const bar = bars.find((item) => utcSeconds(item.time) === utcSeconds(highlightTime));
      if (bar) chart.setCrosshairPosition(decimalToChart(bar.close), utcSeconds(bar.time) as UTCTimestamp, series);
    }
  }, [bars, fills, highlightTime, ready]);

  return (
    <div className="chart-frame panel">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span id={labelId} className="eyebrow">
          UTC hourly candles
        </span>
        <button className="button" type="button" onClick={() => chartRef.current?.timeScale().fitContent()}>
          Reset range
        </button>
      </div>
      <div ref={nodeRef} className="chart-surface" role="img" aria-labelledby={labelId} />
      <p className="hover-readout">{hover}</p>
    </div>
  );
}

export interface LineSeriesInput {
  name: string;
  color: string;
  points: { time: string; value: string }[];
}

export function LineChart({ series, label }: { series: LineSeriesInput[]; label: string }) {
  const nodeRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef(series);
  seriesRef.current = series;
  const signature = series
    .map((item) => `${item.name}:${item.points.length}:${item.points[0]?.time ?? ""}:${item.points.at(-1)?.value ?? ""}`)
    .join("|");

  useEffect(() => {
    const node = nodeRef.current;
    const usable = seriesRef.current.filter((item) => item.points.length > 0);
    if (!node || usable.length === 0) return;
    let active = true;
    let chart: IChartApi | null = null;
    void import("lightweight-charts").then((lc) => {
      if (!active || !nodeRef.current) return;
      chart = lc.createChart(node, {
        autoSize: true,
        layout: {
          background: { color: "#141716" },
          textColor: "#a39b8d",
          fontFamily: "IBM Plex Sans, sans-serif",
        },
        grid: {
          vertLines: { color: "rgba(243, 239, 230, 0.06)" },
          horzLines: { color: "rgba(243, 239, 230, 0.06)" },
        },
        rightPriceScale: { borderColor: "rgba(243, 239, 230, 0.12)" },
        timeScale: { borderColor: "rgba(243, 239, 230, 0.12)", timeVisible: true },
        localization: { locale: "en-US" },
      });
      for (const item of usable) {
        const line = chart.addLineSeries({ color: item.color, lineWidth: 2, title: item.name });
        line.setData(
          item.points.map((point) => ({
            time: utcSeconds(point.time) as UTCTimestamp,
            value: decimalToChart(point.value),
          })),
        );
      }
      chart.timeScale().fitContent();
      chartRef.current = chart;
    });
    return () => {
      active = false;
      chart?.remove();
      chartRef.current = null;
    };
  }, [signature]);

  return (
    <div className="chart-frame panel">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="eyebrow">{label}</span>
        <button className="button" type="button" onClick={() => chartRef.current?.timeScale().fitContent()}>
          Reset range
        </button>
      </div>
      <div ref={nodeRef} className="chart-surface" />
    </div>
  );
}
