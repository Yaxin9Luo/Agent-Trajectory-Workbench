import { formatDuration, formatBytes, shortSha, terminalCards } from "./presentation.mjs";
import { compareRuns, getAnalysis, getArtifactDiffs, getErrors, getMessages, getRun, importRun, listRuns, runFileUrl } from "./api.js";

const state = {
  runs: [],
  activeId: null,
  summary: null,
  analysis: null,
  errors: null,
  artifactDiffs: null,
  compareIds: [],
  compareResult: null,
  roles: new Set(["assistant", "tool", "user", "result"]),
  tool: "",
  search: "",
  offset: 0,
  limit: 200,
  messageTotal: 0,
};

const elements = {
  empty: document.querySelector("#empty-state"),
  runView: document.querySelector("#run-view"),
  runList: document.querySelector("#run-list"),
  importForm: document.querySelector("#import-form"),
  runPath: document.querySelector("#run-path"),
  runLabel: document.querySelector("#run-label"),
  importStatus: document.querySelector("#import-status"),
  refreshRuns: document.querySelector("#refresh-runs"),
  runAdapter: document.querySelector("#run-adapter"),
  runTitle: document.querySelector("#run-title"),
  runSource: document.querySelector("#run-source"),
  runtimeChip: document.querySelector("#runtime-chip"),
  metrics: document.querySelector("#metrics"),
  timeline: document.querySelector("#timeline"),
  timelineDuration: document.querySelector("#timeline-duration"),
  toolFilter: document.querySelector("#tool-filter"),
  search: document.querySelector("#message-search"),
  messageCount: document.querySelector("#message-count"),
  filterStatus: document.querySelector("#filter-status"),
  messages: document.querySelector("#messages"),
  previousPage: document.querySelector("#previous-page"),
  nextPage: document.querySelector("#next-page"),
  pageStatus: document.querySelector("#page-status"),
  terminalFlow: document.querySelector("#terminal-flow"),
  toolSummary: document.querySelector("#tool-summary"),
  toolBars: document.querySelector("#tool-bars"),
  artifactFacts: document.querySelector("#artifact-facts"),
  artifactChart: document.querySelector("#artifact-chart"),
  artifactStates: document.querySelector("#artifact-states"),
  workbenchList: document.querySelector("#workbench-list"),
  analysisList: document.querySelector("#analysis-list"),
  analysisStatus: document.querySelector("#analysis-status"),
  errorsList: document.querySelector("#errors-list"),
  errorsStatus: document.querySelector("#errors-status"),
  diffList: document.querySelector("#diff-list"),
  diffStatus: document.querySelector("#diff-status"),
  compareList: document.querySelector("#compare-list"),
  compareStatus: document.querySelector("#compare-status"),
  compareA: document.querySelector("#compare-a"),
  compareB: document.querySelector("#compare-b"),
  compareGo: document.querySelector("#compare-go"),
  imageModal: document.querySelector("#image-modal"),
};

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

function formatTime(milliseconds) {
  const total = Math.max(0, Number(milliseconds || 0));
  const minutes = Math.floor(total / 60000);
  const seconds = Math.floor((total % 60000) / 1000);
  return minutes + ":" + String(seconds).padStart(2, "0");
}

function metric(label, value, note, alert = false) {
  const item = node("div", "metric" + (alert ? " alert" : ""));
  item.append(node("label", "", label), node("b", "", value), node("small", "", note));
  return item;
}

async function refreshLibrary(preferredId = state.activeId) {
  const payload = await listRuns();
  state.runs = payload.runs;
  renderLibrary();
  const selected = state.runs.find((run) => run.id === preferredId && run.available)
    || state.runs.find((run) => run.available);
  if (selected) await selectRun(selected.id);
  else showEmpty();
}

function renderLibrary() {
  elements.runList.replaceChildren();
  if (!state.runs.length) {
    elements.runList.append(node("div", "empty-message", "还没有导入 run"));
    return;
  }
  state.runs.forEach((run) => {
    const button = node("button", "run-item");
    button.type = "button";
    if (run.id === state.activeId) button.classList.add("active");
    if (!run.available) button.classList.add("unavailable");
    const title = node("strong", "", run.label);
    const path = node("span", "", run.path);
    const availability = node("i");
    title.prepend(availability);
    button.append(title, path);
    button.disabled = !run.available;
    button.addEventListener("click", () => selectRun(run.id));
    elements.runList.append(button);
  });
}

function showEmpty() {
  state.activeId = null;
  state.summary = null;
  state.analysis = null;
  state.errors = null;
  state.artifactDiffs = null;
  state.compareResult = null;
  elements.empty.classList.remove("hidden");
  elements.runView.classList.add("hidden");
  renderLibrary();
}

async function selectRun(runId) {
  state.activeId = runId;
  state.analysis = null;
  state.errors = null;
  state.artifactDiffs = null;
  state.compareResult = null;
  state.offset = 0;
  elements.errorsStatus.textContent = "尚未加载";
  elements.diffStatus.textContent = "尚未加载";
  renderLibrary();
  elements.empty.classList.add("hidden");
  elements.runView.classList.remove("hidden");
  elements.runTitle.textContent = "读取 records…";
  try {
    state.summary = await getRun(runId);
    renderSummary();
    await refreshMessages();
  } catch (error) {
    elements.runTitle.textContent = "读取失败";
    elements.runSource.textContent = error.message;
  }
}

function renderSummary() {
  const run = state.summary;
  const metrics = run.metrics;
  const runtime = run.runtime;
  const finalState = run.artifact_states.at(-1) || {};
  elements.runAdapter.textContent = run.adapter_id + " · attempt " + run.attempt_id;
  elements.runTitle.textContent = run.label || run.title;
  elements.runSource.textContent = run.source_path;
  elements.runtimeChip.textContent = runtime.classification || "completed";
  elements.metrics.replaceChildren(
    metric("Agent wall time", formatDuration(metrics.max_offset_ms), "最后一条 trajectory record"),
    metric("Tool calls", String(metrics.tool_calls), metrics.tool_kinds + " 类工具"),
    metric("Messages", String(metrics.message_count), "不含 token 计量行"),
    metric("Artifact states", String(metrics.artifact_state_count), "按 SHA 去重"),
    metric("Workbench", String(metrics.workbench_calls), "模型主动调用"),
    metric("Runtime", String(runtime.classification || "completed").toUpperCase(), formatBytes(finalState.size_bytes), runtime.classification !== "completed")
  );
  renderTimeline();
  renderToolFilter();
  renderTerminal();
  renderToolBars();
  renderArtifacts();
  renderWorkbench();
  renderAnalysis().catch(() => {});
  renderErrors().catch(() => {});
  renderArtifactDiffs().catch(() => {});
  renderCompare().catch(() => {});
}

function renderTimeline() {
  const run = state.summary;
  const duration = Math.max(1, Number(run.metrics.max_offset_ms));
  elements.timelineDuration.textContent = formatDuration(duration);
  elements.timeline.replaceChildren();

  const ruler = node("div", "ruler");
  [0, .25, .5, .75, 1].forEach((ratio) => {
    const tick = node("span", "tick");
    tick.style.left = ratio * 100 + "%";
    tick.append(node("span", "", formatTime(duration * ratio)));
    ruler.append(tick);
  });
  elements.timeline.append(ruler);

  const laneSpecs = [
    ["message", "Assistant / User", "#3f5fa8"],
    ["tool", "Tool calls", "#53755e"],
    ["artifact", "artifact.html", "#7659a6"],
    ["workbench", "Slides Workbench", "#26768b"],
    ["runtime", "Runtime", "#b94b47"],
  ];
  laneSpecs.forEach(([kind, label, color]) => {
    const lane = node("div", "lane");
    lane.append(node("div", "lane-label", label));
    const track = node("div", "lane-track");
    const items = kind === "workbench"
      ? run.timeline.filter((item) => item.kind === "tool" && item.label === "Slides Workbench")
      : run.timeline.filter((item) => item.kind === kind);
    items.forEach((item) => {
      const mark = node("button", "timeline-mark");
      mark.type = "button";
      mark.style.left = Math.min(99.7, Math.max(.3, Number(item.offset_ms || 0) / duration * 100)) + "%";
      mark.style.setProperty("--mark", color);
      if (kind === "workbench") mark.classList.add("round");
      if (kind === "runtime") mark.classList.add("fail");
      mark.dataset.tip = formatTime(item.offset_ms) + " · " + item.label;
      mark.setAttribute("aria-label", mark.dataset.tip);
      if (item.message_id) {
        mark.addEventListener("click", () => focusMessage(item.message_id, mark));
      }
      track.append(mark);
    });
    lane.append(track);
    elements.timeline.append(lane);
  });
}

async function focusMessage(messageId, mark) {
  let target = document.getElementById(messageId);
  if (!target) {
    state.roles = new Set(["assistant", "tool", "user", "system", "result"]);
    state.tool = "";
    state.search = "";
    state.offset = 0;
    syncFilterControls();
    await refreshMessages();
    target = document.getElementById(messageId);
  }
  if (!target) return;
  document.querySelectorAll(".timeline-mark.active").forEach((item) => item.classList.remove("active"));
  mark.classList.add("active");
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  target.classList.remove("flash");
  void target.offsetWidth;
  target.classList.add("flash");
}

function renderToolFilter() {
  elements.toolFilter.replaceChildren(new Option("全部工具", ""));
  state.summary.tool_catalog.forEach((tool) => {
    const status = tool.observed
      ? tool.call_count + " 次调用"
      : (tool.available_in_session ? "session 可用 · 0 次调用" : "manifest 声明 · 0 次调用");
    elements.toolFilter.append(new Option(tool.name + " · " + status, tool.name));
  });
  elements.toolFilter.value = state.tool;
}

function activeMessageRoles() {
  const roles = new Set(state.roles);
  if (state.tool) {
    roles.add("assistant");
    roles.add("tool");
  }
  return [...roles];
}

async function refreshMessages() {
  if (!state.activeId) return;
  elements.messages.replaceChildren(node("div", "empty-message", "读取消息…"));
  const payload = await getMessages(state.activeId, {
    roles: activeMessageRoles(),
    tool: state.tool,
    search: state.search,
    offset: state.offset,
    limit: state.limit,
  });
  state.messageTotal = payload.total;
  renderMessages(payload.items);
  const first = payload.total ? payload.offset + 1 : 0;
  const last = Math.min(payload.total, payload.offset + payload.items.length);
  elements.messageCount.textContent = payload.total + " 条语义消息";
  elements.filterStatus.textContent = state.summary.metrics.thinking_token_events.toLocaleString() + " 条 thinking_tokens 计量事件已折叠";
  elements.pageStatus.textContent = first + "–" + last + " / " + payload.total;
  elements.previousPage.disabled = payload.offset <= 0;
  elements.nextPage.disabled = payload.offset + payload.limit >= payload.total;
}

function renderMessages(messages) {
  elements.messages.replaceChildren();
  const prompt = state.summary?.model_prompt;
  if (prompt?.scope === "moh_model_prompt_v1") {
    const panel = node("section", "tool-box model-prompt");
    panel.append(node("h3", "", "MoH 输入记录 · 独立于原生轨迹"));
    panel.append(node("p", "", "来源：" + prompt.source + "。System 是 MoH 追加内容，不包含 Claude Code 的完整原生 system；本地记录一致不代表已捕获完整 HTTP 请求。"));
    [["system", "MoH system 扩展"], ["user", "MoH user 输入"]].forEach(([role, label]) => {
      const part = prompt[role];
      const status = part.status === "records_match"
        ? "记录与摘要一致"
        : (part.status === "mismatch" ? "不可验证：记录不一致" : "未验证：记录或摘要缺失");
      const detail = detailBlock(label + " · " + status, part.text);
      detail.append(node("p", "", part.record_path + " · 内容 SHA256 " + part.sha256));
      panel.append(detail);
    });
    elements.messages.append(panel);
  }
  if (!messages.length) {
    elements.messages.append(node("div", "empty-message", "没有符合当前筛选的消息"));
    return;
  }
  messages.forEach((message) => {
    const article = node("article", "message " + message.role);
    article.id = message.id;
    article.append(node("span", "message-index", "#" + message.line_number));
    const head = node("div", "message-head");
    head.append(node("span", "message-role", message.role), node("span", "message-time", formatTime(message.offset_ms)));
    article.append(head);
    if (message.text) {
      const text = node("div", "message-text");
      text.textContent = message.text;
      article.append(text);
    }
    if (message.thinking) {
      const detail = node("details", "thinking-detail");
      detail.append(node("summary", "", "Thinking · 展开"));
      const thinking = node("div", "thinking-text");
      thinking.textContent = message.thinking;
      detail.append(thinking);
      article.append(detail);
    }
    message.tools.forEach((tool) => article.append(renderTool(tool)));
    if (message.native_result) {
      const detail = detailBlock("Native result · modelUsage", JSON.stringify(message.native_result, null, 2));
      detail.className = "native-result-detail";
      article.append(detail);
    }
    elements.messages.append(article);
  });
}

function renderTool(tool) {
  const box = node("div", "tool-box");
  const head = node("div", "tool-head");
  const identity = node("div", "tool-identity");
  identity.append(node("strong", "", tool.name));
  if (tool.raw_name && tool.raw_name !== tool.name) {
    identity.append(node("code", "tool-raw-name", tool.raw_name));
  }
  const resultState = tool.result
    ? (tool.result.is_error ? "error" : "result")
    : "no result";
  const status = node("div", "tool-call-status " + (tool.result?.is_error ? "error" : ""));
  status.append(node("span", "", resultState), node("code", "", tool.id));
  head.append(identity, status);
  box.append(head);
  box.append(detailBlock("Input", JSON.stringify(tool.input, null, 2)));
  box.append(detailBlock(tool.result?.is_error ? "Result · error" : "Result", tool.result ? tool.result.text : "No matching tool_result record"));
  appendToolImages(box, tool.result?.images || [], tool.name + " result");
  return box;
}

function appendToolImages(target, images, label) {
  if (!images.length) return;
  const gallery = node("div", "tool-images");
  images.forEach((source, index) => {
    const image = node("img");
    image.loading = "lazy";
    image.alt = label + " image #" + (index + 1);
    image.src = "data:" + source.media_type + ";base64," + source.data;
    image.addEventListener("click", () => openImage(image));
    gallery.append(image);
  });
  target.append(gallery);
}

function detailBlock(label, content) {
  const details = node("details");
  details.append(node("summary", "", label));
  const pre = node("pre");
  pre.textContent = content || "";
  details.append(pre);
  return details;
}

function renderTerminal() {
  const cards = terminalCards(state.summary).map((card) => terminalNode(card.title, card.detail, card.state));
  elements.terminalFlow.replaceChildren(
    cards[0], node("div", "terminal-arrow", "→"),
    cards[1], node("div", "terminal-arrow", "→"), cards[2]
  );
}

function terminalNode(title, detail, stateName) {
  const item = node("div", "terminal-node " + stateName);
  item.append(node("strong", "", title), node("span", "", detail));
  return item;
}

function renderToolBars() {
  const entries = [...state.summary.tool_catalog].sort((a, b) => b.call_count - a.call_count || a.name.localeCompare(b.name));
  const max = entries.length ? Math.max(1, entries[0].call_count) : 1;
  const observed = entries.filter((tool) => tool.observed).length;
  const available = entries.filter((tool) => tool.available_in_session).length;
  const declared = entries.filter((tool) => tool.declared).length;
  const surfaces = state.summary.native_tool_surfaces.length
    ? " · native surface: " + state.summary.native_tool_surfaces.join(", ") + "（未枚举）"
    : "";
  elements.toolSummary.textContent = available + " 个 session 可用 · " + observed + " 个实调 · " + declared + " 个 manifest 声明" + surfaces;
  elements.toolBars.replaceChildren();
  entries.forEach((tool) => {
    const row = node("div", "tool-bar");
    const label = node("div", "tool-label");
    label.append(node("strong", "", tool.name));
    const states = [];
    if (tool.observed) states.push("轨迹实调 × " + tool.call_count);
    if (tool.available_in_session) states.push("session 可用");
    if (tool.declared) states.push("manifest 已声明");
    const stateText = states.join(" · ") + (tool.observed ? "" : " · 未调用");
    label.append(node("span", "", stateText));
    const rawNames = tool.raw_names.filter((name) => name !== tool.name);
    if (rawNames.length) label.append(node("code", "", rawNames.join(" · ")));
    row.append(label);
    const track = node("div", "tool-track");
    const fill = node("div", "tool-fill");
    fill.style.width = tool.call_count / max * 100 + "%";
    track.append(fill);
    row.append(track, node("span", "tool-value", String(tool.call_count)));
    elements.toolBars.append(row);
  });
}

function renderArtifacts() {
  const run = state.summary;
  const states = run.artifact_states;
  const first = states[0] || {};
  const last = states.at(-1) || {};
  elements.artifactFacts.replaceChildren(
    artifactFact(String(states.length), "唯一状态"),
    artifactFact(formatTime(first.received_offset_ms), "首次落盘"),
    artifactFact(formatBytes(last.size_bytes), "最终大小")
  );
  renderArtifactChart(states, run.metrics.max_offset_ms);
  elements.artifactStates.replaceChildren();
  states.forEach((item) => {
    const row = node("div", "artifact-state");
    row.append(
      node("span", "", formatTime(item.received_offset_ms)),
      node("span", "", shortSha(item.artifact_sha256)),
      node("span", "", formatBytes(item.size_bytes))
    );
    row.title = item.artifact_sha256;
    elements.artifactStates.append(row);
  });
}

function artifactFact(value, label) {
  const item = node("div", "artifact-fact");
  item.append(node("strong", "", value), node("span", "", label));
  return item;
}

function renderArtifactChart(states, duration) {
  const svg = elements.artifactChart;
  svg.replaceChildren();
  if (!states.length) return;
  const sizes = states.map((item) => item.size_bytes);
  const min = Math.min(...sizes);
  const max = Math.max(...sizes);
  const range = Math.max(1, max - min);
  const points = states.map((item) => {
    const x = 7 + item.received_offset_ms / Math.max(1, duration) * 346;
    const y = 56 - (item.size_bytes - min) / range * 44;
    return [x, y];
  });
  const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  polyline.setAttribute("points", points.map((point) => point.join(",")).join(" "));
  polyline.setAttribute("fill", "none");
  polyline.setAttribute("stroke", "#7659a6");
  polyline.setAttribute("stroke-width", "2");
  polyline.setAttribute("vector-effect", "non-scaling-stroke");
  svg.append(polyline);
  points.forEach(([x, y]) => {
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("cx", x);
    circle.setAttribute("cy", y);
    circle.setAttribute("r", "2.5");
    circle.setAttribute("fill", "#7659a6");
    svg.append(circle);
  });
}

function renderWorkbench() {
  elements.workbenchList.replaceChildren();
  const calls = state.summary.workbench;
  if (!calls.length) {
    elements.workbenchList.append(node("div", "empty-message", "这次运行没有调用 Slides Workbench"));
    return;
  }
  calls.forEach((call, index) => {
    const observation = call.observation || {};
    const card = node("article", "workbench-card");
    const head = node("div", "workbench-head");
    head.append(node("strong", "", "Workbench #" + (index + 1)), node("span", "", formatTime(call.offset_ms) + " · " + (observation.status || "unparsed")));
    card.append(head);
    if (observation.error_code || observation.message) {
      card.append(node("div", "workbench-raw", [observation.error_code, observation.message].filter(Boolean).join(" · ")));
    }
    const images = state.summary.tools.find((tool) => tool.id === call.tool_id)?.result?.images || [];
    if (images.length) {
      appendToolImages(card, images, "Workbench #" + (index + 1));
    } else if (call.contact_sheet_relative_path) {
      const image = node("img");
      image.loading = "lazy";
      image.alt = "Workbench contact sheet #" + (index + 1);
      image.src = runFileUrl(state.activeId, call.contact_sheet_relative_path);
      image.addEventListener("click", () => openImage(image));
      card.append(image);
    }
    if (call.observation) {
      const diagnostics = observation.diagnostics || {};
      const meta = node("div", "workbench-meta");
      meta.append(
        workbenchFact(formatDuration(observation.timings_ms?.total), "工具耗时"),
        workbenchFact(String(diagnostics.returned_count ?? "—") + " / " + String(diagnostics.total_count ?? "—"), "返回 / 总诊断"),
        workbenchFact(shortSha(observation.artifact_sha256), "artifact SHA")
      );
      card.append(meta);
    } else {
      card.append(node("div", "workbench-raw", call.raw_result || "Workbench result 无法解析"));
    }
    elements.workbenchList.append(card);
  });
}

function workbenchFact(value, label) {
  const item = node("div");
  item.append(node("strong", "", value), document.createTextNode(label));
  return item;
}

const ANALYSIS_LABELS = {
  none: "无错误",
  recoverable: "可恢复",
  persistent: "持续未恢复",
  fatal: "致命",
  validation: "输入校验",
  tool_misuse: "工具误用",
  environment: "环境缺失",
  logic: "逻辑缺陷",
  external: "外部服务",
};

function choiceLabel(choice) {
  return ANALYSIS_LABELS[choice] || choice;
}

function percent(value) {
  return Math.round((Number(value) || 0) * 100) + "%";
}

function bar(value, max) {
  const track = node("div", "bar");
  const fill = node("div", "bar-fill");
  const ratio = max > 0 ? Math.min(1, Math.max(0, (Number(value) || 0) / max)) : 0;
  fill.style.width = ratio * 100 + "%";
  track.append(fill);
  return track;
}

function analysisRow(label, value) {
  const row = node("div", "analysis-row");
  row.append(node("span", "analysis-label", label), value);
  return row;
}

async function renderAnalysis() {
  const runId = state.activeId;
  if (!runId) return;
  elements.analysisStatus.textContent = "分析中…";
  try {
    const result = await getAnalysis(runId);
    if (runId !== state.activeId) return;
    state.analysis = result;
    renderAnalysisResult(result);
  } catch (error) {
    elements.analysisStatus.textContent = "分析失败：" + error.message;
  }
}

function renderAnalysisResult(result) {
  const run = result.run || {};
  const health = run.health_score || null;
  const needsHuman = run.needs_human || null;
  const severity = run.error_severity || null;
  const failure = (result.errors || {}).failure_category || null;
  const toolErrorCount = Number(result.tool_error_count || 0);
  elements.analysisStatus.textContent = "Jev · TypeOne 结构化判断";
  elements.analysisList.replaceChildren();

  if (health && health.score !== null && health.score !== undefined) {
    const value = node("div", "analysis-value");
    value.append(node("strong", "", Number(health.score).toFixed(1) + " / 10"));
    value.append(bar(health.confidence == null ? 0 : health.confidence, 1));
    value.append(node("span", "analysis-note", "置信度 " + percent(health.confidence)));
    elements.analysisList.append(analysisRow("健康分", value));
  }
  if (needsHuman && needsHuman.probability !== null && needsHuman.probability !== undefined) {
    const probability = Number(needsHuman.probability) || 0;
    const value = node("div", "analysis-value");
    value.append(node("strong", "", percent(probability)));
    value.append(bar(probability, 1));
    value.append(node("span", "analysis-note", probability >= 0.5 ? "倾向需要人工介入" : "倾向无需人工介入"));
    elements.analysisList.append(analysisRow("需要人工介入", value));
  }
  if (severity && severity.choice) {
    const value = node("div", "analysis-value");
    value.append(node("strong", "", choiceLabel(severity.choice)));
    value.append(bar(severity.confidence == null ? 0 : severity.confidence, 1));
    value.append(node("span", "analysis-note", "置信度 " + percent(severity.confidence)));
    elements.analysisList.append(analysisRow("最严重错误", value));
  }
  if (toolErrorCount > 0 && failure && failure.choice) {
    const value = node("div", "analysis-value");
    value.append(node("strong", "", choiceLabel(failure.choice)));
    value.append(bar(failure.confidence == null ? 0 : failure.confidence, 1));
    value.append(node("span", "analysis-note", "置信度 " + percent(failure.confidence) + " · " + toolErrorCount + " 个工具错误"));
    elements.analysisList.append(analysisRow("失败类别", value));
  }
  if (!elements.analysisList.children.length) {
    elements.analysisList.append(node("div", "empty-message", "该 run 暂无可用的语义分析结果"));
  }
}

async function renderErrors() {
  const runId = state.activeId;
  if (!runId) return;
  elements.errorsStatus.textContent = "读取中…";
  try {
    const result = await getErrors(runId);
    if (runId !== state.activeId) return;
    state.errors = result;
    renderErrorsResult(result);
  } catch (error) {
    elements.errorsStatus.textContent = "读取失败：" + error.message;
  }
}

function renderErrorsResult(result) {
  elements.errorsList.replaceChildren();
  const rate = result.recovery_rate == null ? "—" : percent(result.recovery_rate);
  elements.errorsStatus.textContent =
    result.error_count + " 个错误 · 重试 " + result.retry_count + " 次 · 恢复率 " + rate;
  if (!result.errors.length) {
    elements.errorsList.append(node("div", "empty-message", "这次运行没有工具错误"));
    return;
  }
  const byName = node("div", "errors-byname");
  Object.entries(result.by_name || {}).forEach(([name, count]) => {
    const chip = node("span", "errors-chip");
    chip.append(node("strong", "", name), node("span", "", "× " + count));
    byName.append(chip);
  });
  elements.errorsList.append(byName);
  result.errors.forEach((item, index) => {
    const row = node("div", "errors-row");
    const head = node("div", "errors-head");
    head.append(node("strong", "", "#" + (index + 1) + " " + item.name));
    head.append(node("span", "", formatTime(item.offset_ms)));
    row.append(head);
    const text = String(item.result_text || "").trim();
    const snippet = text ? (text.length > 160 ? text.slice(0, 160) + "…" : text) : "（无结果文本）";
    row.append(node("div", "errors-text", snippet));
    const recovery = node("div", "errors-recovery " + (item.recovery ? "ok" : "bad"));
    if (item.recovery) {
      recovery.append(
        node("span", "", "✓ 已恢复"),
        node("code", "", item.recovery.tool_id),
        node("span", "", "+" + formatTime(item.recovery.offset_ms - item.offset_ms) + " · 之后第 " + item.recovery.attempts_later + " 次调用")
      );
    } else {
      recovery.append(node("span", "", "✗ 未恢复"));
    }
    row.append(recovery);
    elements.errorsList.append(row);
  });
}

async function renderArtifactDiffs() {
  const runId = state.activeId;
  if (!runId) return;
  elements.diffStatus.textContent = "读取中…";
  try {
    const result = await getArtifactDiffs(runId);
    if (runId !== state.activeId) return;
    state.artifactDiffs = result;
    renderDiffsResult(result);
  } catch (error) {
    elements.diffStatus.textContent = "读取失败：" + error.message;
  }
}

function renderDiffsResult(result) {
  elements.diffList.replaceChildren();
  elements.diffStatus.textContent = result.state_count + " 个 artifact 状态";
  if (!result.diffs.length) {
    elements.diffList.append(node("div", "empty-message", "artifact.html 内容没有变化"));
    return;
  }
  result.diffs.forEach((diff) => {
    const row = node("div", "diff-row");
    const head = node("div", "diff-head");
    head.append(node("span", "", formatTime(diff.from_offset_ms) + " → " + formatTime(diff.to_offset_ms)));
    head.append(node("code", "", diff.from_sha + " → " + diff.to_sha));
    const delta = (diff.to_size || 0) - (diff.from_size || 0);
    head.append(node("span", "diff-size " + (delta >= 0 ? "more" : "less"), (delta >= 0 ? "+" : "−") + formatBytes(Math.abs(delta))));
    row.append(head);
    if (diff.unavailable) {
      row.append(node("div", "diff-unavailable", "内容不可读"));
    } else {
      row.append(node("div", "diff-lines", "+" + diff.added + " 行 / −" + diff.removed + " 行"));
    }
    elements.diffList.append(row);
  });
}

async function renderCompare() {
  const options = state.runs.filter((run) => run.available);
  [elements.compareA, elements.compareB].forEach((select, index) => {
    select.replaceChildren();
    options.forEach((run) => {
      const option = node("option", "", run.label);
      option.value = run.id;
      select.append(option);
    });
    if (options[index]) select.value = options[index].id;
  });
  elements.compareGo.disabled = options.length < 2;
}

function renderCompareResult(result) {
  const comparison = result.comparison;
  elements.compareList.replaceChildren();
  if (!comparison) {
    elements.compareStatus.textContent = "至少需要两个 run 才能对比";
    return;
  }
  const ids = Object.keys(comparison.metrics);
  elements.compareStatus.textContent = result.run_count + " 个 run 并排对比 · 绿色为更优";

  const table = node("table", "compare-table");
  const headRow = node("tr");
  headRow.append(node("th", "", "指标"));
  ids.forEach((id) => headRow.append(node("th", "", comparison.metrics[id].label)));
  table.append(headRow);

  const rows = [
    ["Runtime", (entry) => String(entry.runtime || "unknown")],
    ["工具调用", (entry) => String(entry.metrics.tool_calls ?? 0)],
    ["消息数", (entry) => String(entry.metrics.message_count ?? 0)],
    ["Artifact 状态", (entry) => String(entry.metrics.artifact_state_count ?? 0)],
    ["总费用", (entry) => entry.metrics.total_cost_usd == null ? "—" : "$" + Number(entry.metrics.total_cost_usd).toFixed(4)],
    ["工具错误", (entry) => String(entry.error_count ?? 0)],
  ];
  rows.forEach(([label, read]) => {
    const values = ids.map((id) => read(comparison.metrics[id]));
    const numeric = values.map((value) => Number(value.replace(/[^0-9.\-]/g, "")) || 0);
    const best = values.every((value) => !value.startsWith("$"))
      ? (label === "工具错误" ? Math.min(...numeric) : Math.max(...numeric))
      : -1;
    const row = node("tr");
    row.append(node("td", "compare-label", label));
    values.forEach((value, index) => {
      row.append(node("td", index === best && ids.length > 1 ? "compare-best" : "", value));
    });
    table.append(row);
  });
  elements.compareList.append(table);

  const toolEntries = Object.entries(comparison.tool_diff || {});
  if (toolEntries.length) {
    const toolsBox = node("div", "compare-tools");
    toolsBox.append(node("div", "compare-tools-title", "工具使用差异（" + comparison.shared_tools.length + " 个共用工具）"));
    toolEntries.forEach(([tool, counts]) => {
      const row = node("div", "compare-tool-row");
      row.append(node("strong", "", tool));
      ids.forEach((id, index) => {
        const value = Number(counts[id]) || 0;
        const other = Number(counts[ids[1 - index]]) || 0;
        const tone = value > other ? "compare-more" : (value < other ? "compare-less" : "");
        row.append(node("span", tone, String(value)));
      });
      toolsBox.append(row);
    });
    elements.compareList.append(toolsBox);
  }
}

function openImage(image) {
  const modalImage = elements.imageModal.querySelector("img");
  modalImage.src = image.src;
  modalImage.alt = image.alt;
  elements.imageModal.classList.remove("hidden");
}

function closeImage() {
  elements.imageModal.classList.add("hidden");
}

function syncFilterControls() {
  document.querySelectorAll("[data-role]").forEach((button) => {
    const role = button.dataset.role;
    const pressed = role === "assistant"
      ? state.roles.has("assistant") && state.roles.has("tool")
      : state.roles.has(role);
    button.setAttribute("aria-pressed", String(pressed));
  });
  elements.toolFilter.value = state.tool;
  elements.search.value = state.search;
}

elements.importForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = elements.importForm.querySelector("button[type=submit]");
  submit.disabled = true;
  elements.importStatus.classList.remove("error");
  elements.importStatus.textContent = "验证并登记路径…";
  try {
    const entry = await importRun(elements.runPath.value.trim(), elements.runLabel.value.trim());
    elements.importStatus.textContent = "已登记；源文件仍在原目录";
    elements.runPath.value = "";
    elements.runLabel.value = "";
    await refreshLibrary(entry.id);
  } catch (error) {
    elements.importStatus.classList.add("error");
    elements.importStatus.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

elements.refreshRuns.addEventListener("click", () => refreshLibrary());
elements.compareGo.addEventListener("click", async () => {
  const a = elements.compareA.value;
  const b = elements.compareB.value;
  if (!a || !b || a === b) {
    elements.compareStatus.textContent = "请选择两个不同的 run";
    elements.compareList.replaceChildren();
    return;
  }
  elements.compareStatus.textContent = "对比中…";
  try {
    const result = await compareRuns([a, b]);
    state.compareResult = result;
    renderCompareResult(result);
  } catch (error) {
    elements.compareStatus.textContent = "对比失败：" + error.message;
  }
});
document.querySelectorAll("[data-role]").forEach((button) => {
  button.addEventListener("click", async () => {
    const role = button.dataset.role;
    const roles = role === "assistant" ? ["assistant", "tool"] : [role];
    const enabled = button.getAttribute("aria-pressed") !== "true";
    roles.forEach((item) => enabled ? state.roles.add(item) : state.roles.delete(item));
    button.setAttribute("aria-pressed", String(enabled));
    state.offset = 0;
    await refreshMessages();
  });
});
elements.toolFilter.addEventListener("change", async () => {
  state.tool = elements.toolFilter.value;
  state.offset = 0;
  await refreshMessages();
});

let searchTimer;
elements.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    state.search = elements.search.value.trim();
    state.offset = 0;
    await refreshMessages();
  }, 180);
});

elements.previousPage.addEventListener("click", async () => {
  state.offset = Math.max(0, state.offset - state.limit);
  await refreshMessages();
});
elements.nextPage.addEventListener("click", async () => {
  state.offset += state.limit;
  await refreshMessages();
});
elements.imageModal.querySelector("button").addEventListener("click", closeImage);
elements.imageModal.addEventListener("click", (event) => {
  if (event.target === elements.imageModal) closeImage();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeImage();
});

refreshLibrary().catch((error) => {
  elements.importStatus.classList.add("error");
  elements.importStatus.textContent = error.message;
});
