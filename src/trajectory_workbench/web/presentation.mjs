import { t } from "./i18n.mjs";

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
  pass: t("评分通过"),
  partial: t("部分通过"),
  fail: t("评分失败"),
  error: t("未完成/报错"),
  unknown: t("无评分"),
};

export const HUMAN_LABELS = { pass: t("确实完成"), partial: t("部分完成"), fail: t("没完成"), unsure: t("说不准") };

export const PHASE_LABELS = {
  explore: t("探索"),
  plan: t("规划"),
  implement: t("实现"),
  verify: t("验证"),
  debug: t("调试"),
  report: t("汇报"),
  other: t("其他"),
};

export const LAYER_LABELS = { base: t("内置工具"), mcp: "MCP", skill: "Skill", subagent: t("子代理"), hook: "Hook", instruction: "Instruction" };

export const WORK_LABELS = { required: t("必需"), fix: t("修复"), polish: t("打磨"), support: t("辅助"), unclear: t("不明") };
export const ROLE_LABELS = { main: t("主轨迹"), segment: t("上下文分段"), subagent: t("子代理") };

export function outcomeTone(status) {
  return { pass: "ok", partial: "warn", fail: "bad", error: "bad" }[status] || "muted";
}

/** How the reviewer's verdict relates to the grader's. */
export function graderAgreement(grader, human) {
  if (!human || human === "unsure" || !grader || grader === "unknown") return null;
  if (grader === "pass" && (human === "fail" || human === "partial")) return { key: "inflated", label: t("评分虚高：评分器判过，人工判不过") };
  if ((grader === "fail" || grader === "error") && human === "pass") return { key: "deflated", label: t("评分误杀：评分器判失败，人工判完成") };
  if (grader === human || (grader === "error" && human === "fail")) return { key: "agree", label: t("与评分器一致") };
  return { key: "partial", label: t("与评分器部分一致") };
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
      leftLabel: leftLonger ? next.signature : t("（结束）"),
      rightLabel: leftLonger ? t("（结束）") : next.signature,
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

/** Cut `text` at `end` without splitting a surrogate pair. */
function sliceHead(text, end) {
  const code = text.charCodeAt(end - 1);
  return text.slice(0, code >= 0xd800 && code <= 0xdbff ? end - 1 : end);
}

function sliceTail(text, count) {
  const start = text.length - count;
  const code = text.charCodeAt(start);
  return text.slice(code >= 0xdc00 && code <= 0xdfff ? start + 1 : start);
}

/**
 * Head and tail of a long text as strings. Folds by lines first; when the kept part is still
 * too long (one huge line: minified JSON, base64, one-line HTML) it folds by characters.
 * `unit` says what `hidden` counts: "行" or "字".
 */
export function foldText(text, { head = 40, tail = 20, maxChars = 12000, headChars = 6000, tailChars = 3000 } = {}) {
  const value = String(text ?? "");
  const lines = foldLines(value, head, tail);
  const headText = lines.head.join("\n");
  const tailText = lines.tail.join("\n");
  if (headText.length + tailText.length <= maxChars) {
    return lines.folded
      ? { folded: true, head: headText, tail: tailText, hidden: lines.hidden, unit: "行" }
      : { folded: false, head: value, tail: "", hidden: 0, unit: "行" };
  }
  const keptHead = sliceHead(value, headChars);
  const keptTail = sliceTail(value, tailChars);
  return { folded: true, head: keptHead, tail: keptTail, hidden: value.length - keptHead.length - keptTail.length, unit: "字" };
}

const PATCH_START = /^\s*\*\*\* Begin Patch/;

/**
 * What a file-changing call does, read from the shape of its input so it works across harnesses:
 *   {kind: "edit", path, hunks: [{before, after}]}   old/new string pairs (Edit, MultiEdit, str_replace)
 *   {kind: "write", path, content}                   whole-file content (Write, create)
 *   {kind: "patch", text}                            apply_patch text
 * Returns null for any other input.
 */
export function editView(input) {
  if (typeof input === "string") return PATCH_START.test(input) ? { kind: "patch", text: input } : null;
  if (!input || typeof input !== "object" || Array.isArray(input)) return null;
  for (const key of ["input", "patch", "command"]) {
    if (typeof input[key] === "string" && PATCH_START.test(input[key])) return { kind: "patch", text: input[key] };
  }
  const path = [input.file_path, input.path, input.notebook_path].find((value) => typeof value === "string") || "";
  const pair = (item) => {
    if (!item || typeof item !== "object") return null;
    const before = item.old_string ?? item.old_str;
    const after = item.new_string ?? item.new_str;
    return typeof before === "string" && typeof after === "string" ? { before, after } : null;
  };
  if (Array.isArray(input.edits)) {
    const hunks = input.edits.map(pair);
    if (hunks.length && hunks.every(Boolean)) return { kind: "edit", path, hunks };
  }
  const single = pair(input);
  if (single) return { kind: "edit", path, hunks: [single] };
  const content = input.content ?? input.file_text;
  if (path && typeof content === "string") return { kind: "write", path, content };
  return null;
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

// ---- v4 reward ------------------------------------------------------------------------

export const V4_LAYERS = ["L0", "L1", "L2", "L3"];

/** Viewport "1920x1080" → [1920, 1080]; anything else falls back to 1920x1080. */
export function parseViewport(text) {
  const match = /^(\d+)x(\d+)$/.exec(String(text || ""));
  return match ? [Number(match[1]), Number(match[2])] : [1920, 1080];
}

/**
 * Where to cut an evidence box out of a still. Box and viewport are CSS px of the
 * screenshot viewport; the still may be any resolution, so the result is in display px
 * for a background sized to the whole viewport. Returns the crop window (box plus margin,
 * clamped to the viewport), the scale, and the box inside the window.
 */
export function cropGeometry(box, viewport, { margin = 40, maxWidth = 360, maxHeight = 220 } = {}) {
  const [vw, vh] = parseViewport(viewport);
  const [x, y, w, hgt] = (box || []).map(Number);
  if (![x, y, w, hgt].every(Number.isFinite)) return null;
  const left = Math.max(0, Math.min(vw, x - margin));
  const top = Math.max(0, Math.min(vh, y - margin));
  const right = Math.max(left + 1, Math.min(vw, x + w + margin));
  const bottom = Math.max(top + 1, Math.min(vh, y + hgt + margin));
  const scale = Math.min(1, maxWidth / (right - left), maxHeight / (bottom - top));
  return {
    width: Math.round((right - left) * scale),
    height: Math.round((bottom - top) * scale),
    backgroundWidth: Math.round(vw * scale),
    backgroundHeight: Math.round(vh * scale),
    offsetX: -Math.round(left * scale) || 0,
    offsetY: -Math.round(top * scale) || 0,
    box: {
      left: Math.round((x - left) * scale),
      top: Math.round((y - top) * scale),
      width: Math.max(1, Math.round(w * scale)),
      height: Math.max(1, Math.round(hgt * scale)),
    },
    // The same box on a whole-slide thumbnail, in percent.
    percent: { left: (x / vw) * 100, top: (y / vh) * 100, width: (w / vw) * 100, height: (hgt / vh) * 100 },
  };
}

/** Failed items grouped by layer, L0–L3 first, unknown layers after. */
export function groupByLayer(items) {
  const groups = new Map(V4_LAYERS.map((layer) => [layer, []]));
  for (const item of items || []) {
    const layer = item.layer || "?";
    if (!groups.has(layer)) groups.set(layer, []);
    groups.get(layer).push(item);
  }
  // worst first inside a layer: the item that sets the tier leads (stable for equal severity)
  const rank = (item) => V4_SEVERITY_RANK[item.severity] ?? 9;
  return [...groups]
    .filter(([, list]) => list.length)
    .map(([layer, list]) => ({ layer, items: list.map((item, i) => [item, i]).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(([item]) => item) }));
}

const V4_SEVERITY_RANK = { invalid: 0, fatal: 1, major: 2, severe: 3, normal: 4, minor: 5 };

/** Short text for a reward: R with two decimals, or "masked" (masked is not zero). */
export function formatReward(r, masked) {
  if (masked || r === null || r === undefined || !Number.isFinite(Number(r))) return "masked";
  return Number(r).toFixed(2);
}
