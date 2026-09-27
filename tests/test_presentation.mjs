import assert from "node:assert/strict";
import test from "node:test";
import {
  firstDivergence,
  formatDuration,
  graderAgreement,
  queueProgress,
  railCells,
  trackCells,
  foldLines,
  lineDiff,
  terminalCards,
  toolSignature,
} from "../src/trajectory_workbench/web/presentation.mjs";

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

test("grader agreement names the direction of a disagreement", () => {
  assert.equal(graderAgreement("pass", "fail").key, "inflated");
  assert.equal(graderAgreement("pass", "partial").key, "inflated");
  assert.equal(graderAgreement("fail", "pass").key, "deflated");
  assert.equal(graderAgreement("error", "fail").key, "agree");
  assert.equal(graderAgreement("unknown", "pass"), null);
  assert.equal(graderAgreement("pass", "unsure"), null);
});

test("rail cells prefer Jev phases and keep error and turning markers", () => {
  const cells = railCells(
    [
      { step: 1, role: "user" },
      { step: 2, role: "tool", error: true },
      { step: 3, role: "system", layer: "hook" },
    ],
    { jevSteps: [{ step: 2, phase: "debug" }], flaggedSteps: new Set([3]), turningStep: 2 }
  );
  assert.deepEqual(cells.map((cell) => cell.kind), ["user", "debug", "layer-hook"]);
  assert.equal(cells[1].error, true);
  assert.equal(cells[1].turning, true);
  assert.equal(cells[2].flagged, true);
});

test("first divergence aligns tool calls by tool and target file", () => {
  const call = (step, name, input = {}) => ({ step, tools: [{ name, input }] });
  const left = [call(2, "Read", { file_path: "/a/brief.md" }), call(3, "Bash", { command: "ls" }), call(5, "Write", { file_path: "/a/deck.html" })];
  const right = [call(2, "Read", { file_path: "/b/brief.md" }), call(4, "Bash", { command: "pwd" }), call(6, "Edit", { file_path: "/b/deck.html" })];
  assert.equal(toolSignature(left[0].tools[0]), "Read:brief.md");
  assert.deepEqual(firstDivergence(left, right), { left: 5, right: 6, leftLabel: "Write:deck.html", rightLabel: "Edit:deck.html", shared: 2 });
  assert.equal(firstDivergence(left, left), null);
  const shorter = firstDivergence(left.slice(0, 2), left);
  assert.equal(shorter.rightLabel, "Write:deck.html");
  assert.equal(shorter.leftLabel, "（结束）");
});

test("queue progress counts done items", () => {
  assert.deepEqual(queueProgress({ items: [{ done: true }, { done: false }] }), { size: 2, done: 1, ratio: 0.5 });
  assert.deepEqual(queueProgress(null), { size: 0, done: 0, ratio: 0 });
});

test("trackCells scales context to the window and times each step until the next stamp", () => {
  const cells = trackCells(
    [
      { step: 1, context: 1000, context_measured: true, t: 0 },
      { step: 2, context: 50000, t: null },
      { step: 3, context: 2000, compaction: true, t: 400_000 },
      { step: 4, context: null, t: 405_000 },
    ],
    100000
  );
  assert.deepEqual(cells.map((cell) => cell.ratio), [0.01, 0.5, 0.02, 0]);
  assert.deepEqual(cells.map((cell) => cell.heat), ["stall", "none", "fast", "none"]);
  assert.equal(cells[0].duration_ms, 400_000);
  assert.equal(cells[0].measured, true);
  assert.equal(cells[2].compaction, true);
  // Without a known window the peak is the scale.
  assert.equal(trackCells([{ step: 1, context: 10 }, { step: 2, context: 20 }])[1].ratio, 1);
});

test("foldLines keeps head and tail of long outputs only", () => {
  assert.equal(foldLines("a\nb").folded, false);
  const long = Array.from({ length: 100 }, (_, index) => "line " + index).join("\n");
  const folded = foldLines(long, 5, 3);
  assert.equal(folded.folded, true);
  assert.deepEqual(folded.head, ["line 0", "line 1", "line 2", "line 3", "line 4"]);
  assert.deepEqual(folded.tail, ["line 97", "line 98", "line 99"]);
  assert.equal(folded.hidden, 92);
});

test("lineDiff marks removed and added lines", () => {
  const diff = lineDiff("PASS a\nFAIL b\nsummary", "PASS a\nPASS b\nsummary");
  assert.deepEqual(diff, [
    { op: "=", text: "PASS a" },
    { op: "-", text: "FAIL b" },
    { op: "+", text: "PASS b" },
    { op: "=", text: "summary" },
  ]);
  assert.equal(lineDiff("x\n".repeat(900), "y"), null);
});
