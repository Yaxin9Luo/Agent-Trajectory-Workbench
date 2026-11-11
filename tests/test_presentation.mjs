import assert from "node:assert/strict";
import test from "node:test";
import {
  editDiff,
  firstDivergence,
  formatCount,
  formatDuration,
  graderAgreement,
  queueProgress,
  railCells,
  trackCells,
  editView,
  foldLines,
  foldText,
  lineDiff,
  terminalCards,
  toolSignature,
  claimConsistency,
  v4Status,
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

test("foldText folds one huge line by characters and many lines by lines", () => {
  assert.deepEqual(foldText("a\nb"), { folded: false, head: "a\nb", tail: "", hidden: 0, unit: "行" });
  const many = Array.from({ length: 100 }, (_, index) => "line " + index).join("\n");
  const byLines = foldText(many, { head: 5, tail: 3 });
  assert.equal(byLines.unit, "行");
  assert.equal(byLines.hidden, 92);
  assert.equal(byLines.head, "line 0\nline 1\nline 2\nline 3\nline 4");
  // One 50,000-character line: foldLines alone would show all of it.
  const huge = "x".repeat(50_000);
  assert.equal(foldLines(huge).folded, false);
  const byChars = foldText(huge);
  assert.equal(byChars.folded, true);
  assert.equal(byChars.unit, "字");
  assert.equal(byChars.head.length + byChars.tail.length + byChars.hidden, huge.length);
  assert.ok(byChars.head.length + byChars.tail.length <= 12_000);
  // A cut never leaves half of a surrogate pair.
  const emoji = foldText("😀".repeat(20_000));
  assert.equal(emoji.head.length % 2, 0);
  assert.equal(emoji.tail.length % 2, 0);
  assert.equal(emoji.head.length + emoji.tail.length + emoji.hidden, 40_000);
});

test("editView reads file changes from the input shape", () => {
  assert.deepEqual(editView({ file_path: "/a.py", old_string: "x = 1", new_string: "x = 2", replace_all: false }), {
    kind: "edit", path: "/a.py", hunks: [{ before: "x = 1", after: "x = 2" }],
  });
  assert.deepEqual(editView({ path: "b.py", command: "str_replace", old_str: "a", new_str: "b" }).hunks, [{ before: "a", after: "b" }]);
  assert.equal(editView({ file_path: "c.py", edits: [{ old_string: "a", new_string: "b" }, { old_string: "c", new_string: "d" }] }).hunks.length, 2);
  assert.deepEqual(editView({ file_path: "d.txt", content: "hello\n" }), { kind: "write", path: "d.txt", content: "hello\n" });
  assert.equal(editView("*** Begin Patch\n*** Update File: a\n-x\n+y\n*** End Patch").kind, "patch");
  assert.equal(editView({ input: "*** Begin Patch\n*** End Patch" }).kind, "patch");
  // Not file changes.
  assert.equal(editView({ command: "ls -la" }), null);
  assert.equal(editView({ file_path: "e.py" }), null);
  assert.equal(editView({ content: "no path" }), null);
  assert.equal(editView({ file_path: "f.py", edits: [{ old_string: "a" }] }), null);
  assert.equal(editView("echo hi"), null);
  assert.equal(editView(null), null);
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
  // An empty side has no lines, not one empty line.
  assert.deepEqual(lineDiff("", "x\ny"), [{ op: "+", text: "x" }, { op: "+", text: "y" }]);
  assert.deepEqual(lineDiff("a", ""), [{ op: "-", text: "a" }]);
  assert.deepEqual(lineDiff("", ""), []);
});

test("editDiff falls back when one huge line would bypass folding", () => {
  assert.deepEqual(editDiff("x = 1", "x = 2"), [{ op: "-", text: "x = 1" }, { op: "+", text: "x = 2" }]);
  // One line each, so the line limit alone would let it through.
  assert.equal(editDiff("a".repeat(7000), "b".repeat(7000)), null);
  assert.ok(editDiff("a".repeat(6000), "b".repeat(6000)));
});

test("formatCount never shows 1000k", () => {
  assert.equal(formatCount(9999), "9,999");
  assert.equal(formatCount(12_345), "12k");
  assert.equal(formatCount(999_499), "999k");
  assert.equal(formatCount(999_500), "1.0M");
  assert.equal(formatCount(999_999), "1.0M");
  assert.equal(formatCount(1_250_000), "1.3M");
});

test("v4 crop window keeps the box in view and clamps to the viewport", async () => {
  const { cropGeometry, groupByLayer, formatReward, parseViewport } = await import("../src/trajectory_workbench/web/presentation.mjs");
  assert.deepEqual(parseViewport("1366x768"), [1366, 768]);
  assert.deepEqual(parseViewport(undefined), [1920, 1080]);
  const small = cropGeometry([100, 200, 50, 20], "1920x1080", { margin: 40 });
  assert.equal(small.offsetX, -60);
  assert.equal(small.offsetY, -160);
  assert.deepEqual(small.box, { left: 40, top: 40, width: 50, height: 20 });
  assert.equal(small.backgroundWidth, 1920);
  const edge = cropGeometry([0, 0, 1920, 1080], "1920x1080", { maxWidth: 192, maxHeight: 400 });
  assert.equal(edge.width, 192);
  assert.equal(edge.offsetX, 0);
  assert.equal(edge.backgroundWidth, 192);
  assert.equal(edge.percent.width, 100);
  assert.equal(cropGeometry(["a"], "1920x1080"), null);
  assert.deepEqual(groupByLayer([{ layer: "L3" }, { layer: "L1" }, { layer: "X" }]).map((g) => g.layer), ["L1", "L3", "X"]);
  assert.deepEqual(groupByLayer([{ layer: "L1", id: "a", severity: "minor" }, { layer: "L1", id: "b", severity: "fatal" },
    { layer: "L1", id: "c", severity: "major" }, { layer: "L1", id: "d", severity: "fatal" }])[0].items.map((i) => i.id), ["b", "d", "c", "a"]);
  assert.equal(formatReward(null, false), "masked");
  assert.equal(formatReward(0, false), "0.00");
  assert.equal(formatReward(0.5, true), "masked");
});

test("a v4 reward stands for an outcome: masked unknown, tier 4 pass, 2-3 partial, lower fail", () => {
  assert.equal(v4Status(null), null);
  assert.equal(v4Status({ R: 0.76, tier: 4, masked: false }), "pass");
  assert.equal(v4Status({ R: 0.15, tier: 2 }), "partial");
  assert.equal(v4Status({ R: 0.03, tier: 1 }), "fail");
  assert.equal(v4Status({ R: 0, tier: 0 }), "fail");
  assert.equal(v4Status({ R: null, tier: 1, masked: true }), "unknown");
  // A list row carries the same reward as v4_* fields.
  assert.equal(v4Status({ v4_r: 0.6, v4_tier: 4, v4_masked: false }), "pass");
  assert.equal(v4Status({ v4_r: null, v4_tier: 1, v4_masked: true }), "unknown");
});

test("claim consistency compares the final claim with the outcome that counts", () => {
  assert.equal(claimConsistency("complete", "fail"), "overclaim");
  assert.equal(claimConsistency("complete", "pass"), null);
  assert.equal(claimConsistency("complete", "partial"), null);
  assert.equal(claimConsistency("failed", "pass"), "underclaim");
  assert.equal(claimConsistency(undefined, "fail"), null);
});
