import assert from "node:assert/strict";
import test from "node:test";
import { formatDuration, terminalCards } from "../src/trajectory_workbench/web/presentation.mjs";

test("missing duration is distinct from measured zero", () => {
  for (const value of [undefined, null, NaN, Infinity]) {
    assert.equal(formatDuration(value), "—");
  }
  assert.equal(formatDuration(0), "0ms");
  assert.equal(formatDuration(1234), "1.2s");
});

const run = {
  runtime: {
    process_terminal_reason: "timeout", exit_code: 143,
    classification: "agent_timeout", failure_message: "Agent wall limit reached",
  },
  metrics: { max_offset_ms: 60000 },
  artifact_states: [],
};

test("timeout and absent artifact cannot appear completed or existing", () => {
  const cards = terminalCards(run);
  assert.equal(cards[0].title, "Agent timeout");
  assert.equal(cards[0].state, "bad");
  assert.match(cards[0].detail, /exit 143/);
  assert.equal(cards[1].title, "no artifact recorded");
  assert.equal(cards[1].detail, "—");
  assert.equal(cards[2].title, "Runtime agent_timeout");
  assert.equal(cards[2].state, "bad");
});

test("normal process completion and artifact history do not imply Runtime success", () => {
  const cards = terminalCards({
    ...run,
    runtime: { process_terminal_reason: "completed", exit_code: 0, classification: "artifact_invalid" },
    artifact_states: [{ size_bytes: 2048, artifact_sha256: "1234567890abcdef" }],
  });
  assert.equal(cards[0].title, "Agent completed");
  assert.equal(cards[0].state, "ok");
  assert.equal(cards[1].title, "artifact recorded");
  assert.equal(cards[1].detail, "2.0 KB · 1234567890…");
  assert.equal(cards[2].state, "bad");
});

test("missing terminal evidence stays unknown", () => {
  const cards = terminalCards({ ...run, runtime: {} });
  assert.equal(cards[0].title, "Agent unknown");
  assert.equal(cards[0].state, "warn");
  assert.equal(cards[2].title, "Runtime unknown");
  assert.equal(cards[2].state, "warn");
});

test("Runtime completion is shown as successful independently of process failure", () => {
  const cards = terminalCards({
    ...run, runtime: { process_terminal_reason: "completed", exit_code: 1, classification: "completed" },
  });
  assert.equal(cards[0].title, "Agent failed");
  assert.equal(cards[0].state, "bad");
  assert.equal(cards[2].state, "ok");
});
