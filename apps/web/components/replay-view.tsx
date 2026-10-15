"use client";

import { useEffect, useState } from "react";

import { useExperiment } from "@/lib/experiment";
import { formatDecimal, formatUtcLabel, phaseLabel } from "@/lib/format";
import { advancePlayhead, clampPlayhead } from "@/lib/replay";
import type { Bar, FillRow, TraceStep } from "@/lib/types";
import { CandleChart } from "./charts";
import { ExperimentGate } from "./gate";
import { primaryPolicy } from "./run-bar";

const SPEEDS = [1, 2, 4];

export function ReplayView() {
  const { replayIndex, setReplayIndex } = useExperiment();
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);

  return (
    <ExperimentGate>
      {(session) => {
        const index = clampPlayhead(replayIndex, session.trace.length);
        const step = session.trace[index];
        const policy = primaryPolicy(session);
        return (
          <ReplayStage
            bars={session.bars}
            fills={policy.fills ?? []}
            trace={session.trace}
            index={index}
            step={step}
            playing={playing}
            speed={speed}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onSpeed={setSpeed}
            onIndex={(next) => {
              setPlaying(false);
              setReplayIndex(clampPlayhead(next, session.trace.length));
            }}
            onTick={() => {
              const moved = advancePlayhead(index, session.trace.length);
              setReplayIndex(moved.index);
              if (moved.finished) setPlaying(false);
            }}
          />
        );
      }}
    </ExperimentGate>
  );
}

function ReplayStage({
  bars,
  fills,
  trace,
  index,
  step,
  playing,
  speed,
  onPlay,
  onPause,
  onSpeed,
  onIndex,
  onTick,
}: {
  bars: Bar[];
  fills: FillRow[];
  trace: TraceStep[];
  index: number;
  step: TraceStep | undefined;
  playing: boolean;
  speed: number;
  onPlay: () => void;
  onPause: () => void;
  onSpeed: (speed: number) => void;
  onIndex: (index: number) => void;
  onTick: () => void;
}) {
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(onTick, 700 / speed);
    return () => window.clearInterval(timer);
  }, [playing, speed, onTick]);

  if (!step) return <p className="empty">This run has no engine events.</p>;

  return (
    <div className="replay-layout">
      <div>
        <CandleChart bars={bars} fills={fills} highlightTime={step.time} onSelectTime={() => undefined} />
        <div className="panel" style={{ marginTop: 12, padding: 12 }}>
          <div className="replay-controls">
            <button className="button" type="button" onClick={() => onIndex(0)}>
              Restart
            </button>
            <button className="button" type="button" onClick={() => onIndex(index - 1)}>
              Previous
            </button>
            {playing ? (
              <button className="button button-primary" type="button" onClick={onPause}>
                Pause
              </button>
            ) : (
              <button className="button button-primary" type="button" onClick={onPlay} disabled={index >= trace.length - 1}>
                Play
              </button>
            )}
            <button className="button" type="button" onClick={() => onIndex(index + 1)}>
              Next
            </button>
            <label className="field" style={{ width: 120 }}>
              Speed
              <select value={speed} onChange={(event) => onSpeed(Number(event.target.value))}>
                {SPEEDS.map((item) => (
                  <option key={item} value={item}>
                    {item}×
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="field" style={{ marginTop: 10 }}>
            Event {index + 1} of {trace.length}
            <input
              className="range"
              type="range"
              min={0}
              max={Math.max(trace.length - 1, 0)}
              value={index}
              onChange={(event) => onIndex(Number(event.target.value))}
            />
          </label>
        </div>
        <div className="event-list panel" style={{ marginTop: 12 }}>
          {trace.map((item) => (
            <button
              key={item.index}
              type="button"
              aria-current={item.index === index}
              onClick={() => onIndex(item.index)}
            >
              <span className="mono">{item.index}</span>
              <span>
                {item.name}
                <br />
                <span className="note">{formatUtcLabel(item.time)}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
      <aside className="panel context">
        <h2>{phaseLabel(step.phase)}</h2>
        <p className="mono">{step.name}</p>
        <p className="note">{formatUtcLabel(step.time)}</p>
        <div className="detail-grid">
          <div>
            <span>Open</span>
            <b>{step.open ? formatDecimal(step.open, 2) : "—"}</b>
          </div>
          <div>
            <span>Signal</span>
            <b>{step.signal ? step.signal.action : "—"}</b>
          </div>
          <div>
            <span>Order</span>
            <b>{step.order ? `${step.order.side} ${step.order.state}` : "—"}</b>
          </div>
          <div>
            <span>Fill price</span>
            <b>{step.fill ? formatDecimal(step.fill.execution_price, 2) : "—"}</b>
          </div>
          <div>
            <span>Fee</span>
            <b>{step.ledger ? formatDecimal(step.ledger.fee, 8) : "—"}</b>
          </div>
          <div>
            <span>Cash</span>
            <b>{step.ledger ? formatDecimal(step.ledger.cash_after, 4) : "—"}</b>
          </div>
          <div>
            <span>BTC</span>
            <b>{step.ledger ? formatDecimal(step.ledger.btc_after, 8) : "—"}</b>
          </div>
          <div>
            <span>Equity</span>
            <b>{step.equity ? formatDecimal(step.equity.equity, 4) : "—"}</b>
          </div>
        </div>
        {step.order_id ? <p className="note">Order {step.order_id}</p> : null}
        {step.stale ? <p className="note">Stale mark from {formatUtcLabel(step.stale.last_price_time)}</p> : null}
        {step.complete ? <p className="note">Completed close {formatDecimal(step.complete.close, 2)}</p> : null}
      </aside>
    </div>
  );
}
