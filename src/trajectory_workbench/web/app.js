import { getMessages, getRun, importRun, listRuns, runFileUrl } from "./api.js";

const state = {
  runs: [],
  activeId: null,
  summary: null,
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

function formatDuration(milliseconds) {
  const total = Number(milliseconds || 0);
  if (total < 1000) return Math.round(total) + "ms";
  if (total < 60000) return (total / 1000).toFixed(total < 10000 ? 1 : 0) + "s";
  return Math.floor(total / 60000) + "m " + Math.round((total % 60000) / 1000) + "s";
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return value + " B";
  if (value < 1024 * 1024) return (value / 1024).toFixed(1) + " KB";
  return (value / 1024 / 1024).toFixed(1) + " MB";
}

function shortSha(value) {
  return value ? String(value).slice(0, 10) + "…" : "—";
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
  elements.empty.classList.remove("hidden");
  elements.runView.classList.add("hidden");
  renderLibrary();
}

async function selectRun(runId) {
  state.activeId = runId;
  state.offset = 0;
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
  Object.keys(state.summary.metrics.tool_counts).sort().forEach((name) => {
    elements.toolFilter.append(new Option(name, name));
  });
  elements.toolFilter.value = state.tool;
}

function activeMessageRoles() {
  const roles = new Set(state.roles);
  if (state.tool) roles.add("tool");
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
    elements.messages.append(article);
  });
}

function renderTool(tool) {
  const box = node("div", "tool-box");
  const head = node("div", "tool-head");
  head.append(node("strong", "", tool.name), node("span", "", tool.id));
  box.append(head);
  box.append(detailBlock("Input", JSON.stringify(tool.input, null, 2)));
  box.append(detailBlock("Result", tool.result ? tool.result.text : "No matching tool_result record"));
  return box;
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
  const run = state.summary;
  const runtime = run.runtime;
  const finalState = run.artifact_states.at(-1) || {};
  elements.terminalFlow.replaceChildren(
    terminalNode("Agent completed", "exit " + String(runtime.exit_code) + " · " + formatDuration(run.metrics.max_offset_ms), "ok"),
    node("div", "terminal-arrow", "→"),
    terminalNode("artifact exists", formatBytes(finalState.size_bytes) + " · " + shortSha(finalState.artifact_sha256), "warn"),
    node("div", "terminal-arrow", "→"),
    terminalNode("Runtime " + runtime.classification, runtime.failure_message || runtime.agent_result_status || "—", "bad")
  );
}

function terminalNode(title, detail, stateName) {
  const item = node("div", "terminal-node " + stateName);
  item.append(node("strong", "", title), node("span", "", detail));
  return item;
}

function renderToolBars() {
  const entries = Object.entries(state.summary.metrics.tool_counts).sort((a, b) => b[1] - a[1]);
  const max = entries.length ? entries[0][1] : 1;
  elements.toolSummary.textContent = state.summary.metrics.tool_calls + " calls · " + entries.length + " kinds";
  elements.toolBars.replaceChildren();
  entries.forEach(([name, count]) => {
    const row = node("div", "tool-bar");
    row.append(node("span", "", name));
    const track = node("div", "tool-track");
    const fill = node("div", "tool-fill");
    fill.style.width = count / max * 100 + "%";
    track.append(fill);
    row.append(track, node("span", "tool-value", String(count)));
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
    if (call.contact_sheet_relative_path) {
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
