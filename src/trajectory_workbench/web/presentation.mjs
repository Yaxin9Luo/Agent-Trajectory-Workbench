export function formatDuration(milliseconds) {
  if (milliseconds == null || !Number.isFinite(Number(milliseconds))) return "—";
  const total = Number(milliseconds);
  if (total < 1000) return Math.round(total) + "ms";
  if (total < 60000) return (total / 1000).toFixed(total < 10000 ? 1 : 0) + "s";
  if (total < 3600000) return Math.floor(total / 60000) + "m " + Math.round((total % 60000) / 1000) + "s";
  return Math.floor(total / 3600000) + "h " + Math.round((total % 3600000) / 60000) + "m";
}

export function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return value + " B";
  if (value < 1024 * 1024) return (value / 1024).toFixed(1) + " KB";
  return (value / 1024 / 1024).toFixed(1) + " MB";
}

export function formatCount(value) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  if (number >= 1e6) return (number / 1e6).toFixed(1) + "M";
  if (number >= 1e4) return (number / 1e3).toFixed(0) + "k";
  return number.toLocaleString();
}

export function percent(value, digits = 0) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return (Number(value) * 100).toFixed(digits) + "%";
}

export function shortSha(value) {
  return value ? String(value).slice(0, 10) + "…" : "—";
}

export function formatClock(milliseconds) {
  if (milliseconds == null) return "";
  const total = Math.max(0, Number(milliseconds || 0));
  const hours = Math.floor(total / 3600000);
  const minutes = Math.floor((total % 3600000) / 60000);
  const seconds = Math.floor((total % 60000) / 1000);
  const tail = String(minutes).padStart(hours ? 2 : 1, "0") + ":" + String(seconds).padStart(2, "0");
  return hours ? hours + ":" + tail : tail;
}

export const OUTCOME_LABELS = {
  pass: "评分通过",
  partial: "部分通过",
  fail: "评分失败",
  error: "未完成/报错",
  unknown: "无评分",
};

export const HUMAN_LABELS = { pass: "确实完成", partial: "部分完成", fail: "没完成", unsure: "说不准" };

export const PHASE_LABELS = {
  explore: "探索",
  plan: "规划",
  implement: "实现",
  verify: "验证",
  debug: "调试",
  report: "汇报",
  other: "其他",
};

export const LAYER_LABELS = { base: "内置工具", mcp: "MCP", skill: "Skill", subagent: "子代理", hook: "Hook", instruction: "Instruction" };

export const WORK_LABELS = { required: "必需", fix: "修复", polish: "打磨", support: "辅助", unclear: "不明" };
export const ROLE_LABELS = { main: "主轨迹", segment: "上下文分段", subagent: "子代理" };

export function outcomeTone(status) {
  return { pass: "ok", partial: "warn", fail: "bad", error: "bad" }[status] || "muted";
}

/** How the reviewer's verdict relates to the grader's. */
export function graderAgreement(grader, human) {
  if (!human || human === "unsure" || !grader || grader === "unknown") return null;
  if (grader === "pass" && (human === "fail" || human === "partial")) return { key: "inflated", label: "评分虚高：评分器判过，人工判不过" };
  if ((grader === "fail" || grader === "error") && human === "pass") return { key: "deflated", label: "评分误杀：评分器判失败，人工判完成" };
  if (grader === human || (grader === "error" && human === "fail")) return { key: "agree", label: "与评分器一致" };
  return { key: "partial", label: "与评分器部分一致" };
}

export function terminalCards(run) {
  const runtime = run.runtime;
  const finalState = run.artifact_states.at(-1);
  const reason = runtime.process_terminal_reason || "unknown";
  const processCompleted = reason === "completed" && Number(runtime.exit_code) === 0;
  const processFailed = runtime.exit_code != null && Number(runtime.exit_code) !== 0;
  const processLabel = reason === "completed" && processFailed ? "failed" : reason;
  const classification = runtime.classification || "unknown";
  return [
    {
      title: "Agent " + processLabel,
      detail: "exit " + String(runtime.exit_code ?? "—") + " · " + formatDuration(run.metrics.max_offset_ms),
      state: processCompleted ? "ok" : (processFailed || !["unknown", "completed"].includes(reason) ? "bad" : "warn"),
    },
    {
      title: finalState ? "artifact recorded" : "no artifact recorded",
      detail: finalState ? formatBytes(finalState.size_bytes) + " · " + shortSha(finalState.artifact_sha256) : "—",
      state: "warn",
    },
    {
      title: "Runtime " + classification,
      detail: runtime.failure_message || runtime.agent_result_status || "—",
      state: classification === "completed" ? "ok" : (classification === "unknown" ? "warn" : "bad"),
    },
  ];
}

/** Build the step-rail cell model: one entry per step with colour key and markers. */
export function railCells(stepIndex, { jevSteps = [], flaggedSteps = new Set(), turningStep = null } = {}) {
  const phaseByStep = new Map(jevSteps.filter((item) => item.phase).map((item) => [item.step, item.phase]));
  return stepIndex.map((item) => ({
    step: item.step,
    kind: phaseByStep.get(item.step) || (item.layer ? "layer-" + item.layer : item.role),
    error: Boolean(item.error),
    flagged: flaggedSteps.has(item.step),
    turning: turningStep === item.step,
  }));
}

/**
 * Per-step context and clock cells, aligned with the step rail.
 * `ratio` is the prompt size relative to the context window (or the peak when the window is
 * unknown); `duration_ms` is the time until the next step that has a timestamp.
 */
export function trackCells(stepIndex, contextWindow = null) {
  const sizes = stepIndex.map((item) => (typeof item.context === "number" ? item.context : null));
  const peak = Math.max(0, ...sizes.filter((value) => value != null));
  const scale = Math.max(contextWindow || 0, peak) || 1;
  const nextStamp = new Array(stepIndex.length).fill(null);
  let upcoming = null;
  for (let index = stepIndex.length - 1; index >= 0; index -= 1) {
    nextStamp[index] = upcoming;
    if (stepIndex[index].t != null) upcoming = stepIndex[index].t;
  }
  return stepIndex.map((item, index) => {
    const duration = item.t != null && nextStamp[index] != null ? Math.max(0, nextStamp[index] - item.t) : null;
    let heat = "none";
    if (duration != null) heat = duration < 10_000 ? "fast" : duration < 60_000 ? "normal" : duration < 300_000 ? "slow" : "stall";
    return {
      step: item.step,
      context: sizes[index],
      ratio: sizes[index] == null ? 0 : Math.min(1, sizes[index] / scale),
      measured: Boolean(item.context_measured),
      compaction: Boolean(item.compaction),
      duration_ms: duration,
      heat,
    };
  });
}

export function queueProgress(queue) {
  const size = queue?.items?.length || 0;
  const done = (queue?.items || []).filter((item) => item.done).length;
  return { size, done, ratio: size ? done / size : 0 };
}

/** A tool call reduced to what matters for alignment: tool name plus the file it targets. */
export function toolSignature(tool) {
  const input = tool.input && typeof tool.input === "object" ? tool.input : {};
  const path = input.file_path || input.path || input.notebook_path || "";
  const base = String(path).split("/").filter(Boolean).pop() || "";
  return base ? tool.name + ":" + base : tool.name;
}

/**
 * First point where two transcripts' tool-call sequences differ. Returns the step on each
 * side plus how many calls matched before it, or null when one is a prefix of the other.
 */
export function firstDivergence(leftMessages, rightMessages) {
  const flatten = (messages) =>
    messages.flatMap((message) => (message.tools || []).map((tool) => ({ step: message.step, signature: toolSignature(tool) })));
  const left = flatten(leftMessages);
  const right = flatten(rightMessages);
  const length = Math.min(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    if (left[index].signature !== right[index].signature) {
      return {
        left: left[index].step,
        right: right[index].step,
        leftLabel: left[index].signature,
        rightLabel: right[index].signature,
        shared: index,
      };
    }
  }
  if (left.length !== right.length && length > 0) {
    const longer = left.length > right.length ? left : right;
    const shorter = left.length > right.length ? right : left;
    const next = longer[length];
    const last = shorter[length - 1];
    const leftLonger = longer === left;
    return {
      left: leftLonger ? next.step : last.step,
      right: leftLonger ? last.step : next.step,
      leftLabel: leftLonger ? next.signature : "（结束）",
      rightLabel: leftLonger ? "（结束）" : next.signature,
      shared: length,
    };
  }
  return null;
}

/** Head and tail of a long output; `hidden` counts the lines left out in between. */
export function foldLines(text, head = 40, tail = 20) {
  const lines = String(text ?? "").split("\n");
  if (lines.length <= head + tail + 10) return { folded: false, head: lines, tail: [], hidden: 0 };
  return { folded: true, head: lines.slice(0, head), tail: lines.slice(-tail), hidden: lines.length - head - tail };
}

/**
 * Line diff (longest common subsequence) of two outputs, as [{op: "=" | "-" | "+", text}].
 * Returns null when either side is too long to compare line by line.
 */
export function lineDiff(before, after, limit = 800) {
  const a = String(before ?? "").split("\n");
  const b = String(after ?? "").split("\n");
  if (a.length > limit || b.length > limit) return null;
  const table = Array.from({ length: a.length + 1 }, () => new Uint16Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const out = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ op: "=", text: a[i] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      out.push({ op: "-", text: a[i] });
      i += 1;
    } else {
      out.push({ op: "+", text: b[j] });
      j += 1;
    }
  }
  while (i < a.length) out.push({ op: "-", text: a[i++] });
  while (j < b.length) out.push({ op: "+", text: b[j++] });
  return out;
}
