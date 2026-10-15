import assert from "node:assert/strict";
import test from "node:test";

import { metricDisplay, hexFloatToNumber } from "../lib/format.ts";
import { apiOrigin, proxyTarget, proxyUrl } from "../lib/proxy.ts";
import { backtestPayload, DEFAULT_REQUEST, validateBacktest } from "../lib/request.ts";
import { advancePlayhead, clampPlayhead, fillStepIndex } from "../lib/replay.ts";

test("default request has no prices, paths, or latency", () => {
  const body = backtestPayload(DEFAULT_REQUEST);
  assert.deepEqual(Object.keys(body).sort(), [
    "bar_count",
    "cost",
    "lookback_hours",
    "scenario",
    "seed",
    "strategy",
  ]);
  assert.equal(body.strategy, "momentum");
  assert.equal(body.lookback_hours, 24);
  assert.equal(body.cost, "baseline");
  assert.equal(validateBacktest(DEFAULT_REQUEST), null);
});

test("momentum rejects a client threshold before the request is sent", () => {
  const message = validateBacktest({ ...DEFAULT_REQUEST, entry_threshold: "0.02" });
  assert.match(message ?? "", /threshold/);
});

test("undefined metrics stay undefined", () => {
  const display = metricDisplay({
    name: "sharpe_ratio",
    value: null,
    value_hex: null,
    reason: "zero_dispersion",
  });
  assert.equal(display.state, "undefined");
  assert.equal(display.text, "Undefined");
  assert.notEqual(display.text, "0.00");
});

test("python float hex is only a display conversion", () => {
  assert.equal(hexFloatToNumber("0x1.8000000000000p+0"), 1.5);
  assert.equal(hexFloatToNumber("-0x1.0000000000000p+1"), -2);
});

test("production does not invent a localhost origin", () => {
  assert.equal(apiOrigin(undefined, "production"), null);
  assert.equal(apiOrigin(undefined, "development"), "http://127.0.0.1:8000");
  assert.equal(proxyTarget("https://api.example", "backtest"), "https://api.example/api/backtest");
  assert.equal(proxyTarget("https://api.example", "prices"), null);
  assert.equal(
    proxyUrl("https://api.example", "research-block", "?source_path=/tmp/x"),
    "https://api.example/api/research-block?source_path=/tmp/x",
  );
});

test("replay stops on the last engine event", () => {
  assert.equal(clampPlayhead(9, 4), 3);
  assert.equal(clampPlayhead(-1, 4), 0);
  assert.deepEqual(advancePlayhead(3, 4), { index: 3, finished: true });
  assert.equal(
    fillStepIndex(
      [
        { index: 0, time: "t", phase: 5, name: "submit", order_id: "a", signal: null, order: null, fill: null, ledger: null, equity: null, stale: null, open: null, complete: null },
        {
          index: 1,
          time: "t2",
          phase: 2,
          name: "fill",
          order_id: "a",
          signal: null,
          order: null,
          fill: {
            fill_id: "f",
            order_id: "a",
            side: "buy",
            execution_price: "1",
            posted_fee: "0",
            quantity: "1",
            time: "t2",
          },
          ledger: null,
          equity: null,
          stale: null,
          open: null,
          complete: null,
        },
      ],
      "a",
    ),
    1,
  );
});
