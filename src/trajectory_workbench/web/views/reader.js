import { api } from "../api.js";
import { chip, clear, debounce, details, empty, h, persist, remember } from "../dom.js";
import {
  LAYER_LABELS,
  OUTCOME_LABELS,
  PHASE_LABELS,
  ROLE_LABELS,
  WORK_LABELS,
  formatClock,
  formatCount,
  formatDuration,
  outcomeTone,
  foldLines,
  lineDiff,
  railCells,
  trackCells,
} from "../presentation.mjs";
import { renderJevPanel } from "./jev.js";
import { createAnnotations } from "./annotations.js";
import { renderLedgerPanel } from "./ledgers.js";
import { highlighted } from "./search.js";
import { renderMohPanels } from "./moh.js";
import { renderReviewPanel } from "./review.js";

const TEXT_CLIP = 6000;

export async function renderReader(root, trajectoryId, ctx, still, params = new URLSearchParams()) {
  clear(root, empty("读取轨迹…"));
  const [run, episode] = await Promise.all([api.trajectory(trajectoryId), api.episode(trajectoryId).catch(() => null)]);
  if (!still()) return;
  // Calls that launched a subagent in this episode, by call id.
  const spawned = new Map();
  (episode?.subagents || []).forEach((sub) => {
    if (sub.spawn?.trajectory_id === trajectoryId) spawned.set(sub.spawn.call_id, sub);
  });

  const state = {
    run,
    review: run.review,
    jev: run.jev,
    highlight: null, // {label, steps:Set}
    roles: new Set(["user", "assistant", "tool", "result"]),
    search: "",
    onlyHighlighted: false,
    revealed: !ctx.blind || Boolean(run.review?.predicted_status),
    compact: remember("reader.compact") === "1",
  };

  const notes = createAnnotations({ ctx }, trajectoryId, () => {
    transcript.querySelectorAll("article.message").forEach((article) => notes.decorate(article.querySelector(".step-notes"), Number(article.dataset.step)));
    renderRail();
  });
  const header = h("header", { class: "reader-head panel" });
  const episodeStrip = renderEpisodeStrip(episode, trajectoryId, ctx);
  const taskCard = h("section", { class: "task-card panel" });
  const signalRow = h("section", { class: "signal-row" });
  const rail = h("div", { class: "step-rail", role: "list", "aria-label": "步骤条" });
  const contextRow = h("div", { class: "track-row context-row", "aria-label": "每步上下文大小" });
  const clockRow = h("div", { class: "track-row clock-row", "aria-label": "每步耗时" });
  const trackLegend = h("div", { class: "track-legend" });
  // The three rows share one scroll box so their cells stay aligned on long runs.
  const railPanel = h(
    "section",
    { class: "rail-panel panel" },
    h("div", { class: "rail-legend" }),
    h("div", { class: "rail-scroll" }, rail, contextRow, clockRow),
    trackLegend
  );
  const transcriptTools = h("div", { class: "transcript-tools" });
  const transcript = h("div", { class: "messages" });
  const more = h("div", { class: "pager" });
  const earlier = h("div", { class: "pager" });
  const side = h("aside", { class: "inspector" });
  const reviewSlot = h("div");
  const jevSlot = h("div");
  // Analysis first; the reading record is its own tab (first only when judging blind).
  const analysisPane = h("div", { class: "side-pane" });
  const reviewPane = h("div", { class: "side-pane" }, reviewSlot);
  const ledgerPane = h("div", { class: "side-pane" });
  const tabs = h("div", { class: "side-tabs" });
  let activeTab = ctx.blind && !run.review?.predicted_status ? "review" : "analysis";
  let ledgerPanel = null;
  function renderTabs() {
    clear(
      tabs,
      [["analysis", "分析"], ["ledgers", "台账"], ["review", "阅读记录" + (state.review?.updated_at ? " ✓" : "")]].map(([key, label]) =>
        h("button", { type: "button", "aria-pressed": String(activeTab === key), onclick: () => { activeTab = key; renderTabs(); } }, label)
      )
    );
    analysisPane.classList.toggle("hidden", activeTab !== "analysis");
    ledgerPane.classList.toggle("hidden", activeTab !== "ledgers");
    reviewPane.classList.toggle("hidden", activeTab !== "review");
    if (activeTab === "ledgers") ledgerPanel?.open();
  }
  clear(
    root,
    header,
    episodeStrip,
    taskCard,
    signalRow,
    railPanel,
    h("div", { class: "work-grid" }, h("section", { class: "transcript panel" }, transcriptTools, earlier, transcript, more), side)
  );
  analysisPane.append(jevSlot);
  side.append(tabs, analysisPane, ledgerPane, reviewPane);

  const api_ = {
    state,
    ctx,
    jump: (step) => jumpTo(step),
    highlight: (label, steps) => setHighlight(label, steps),
    setTurning: (step) => {
      activeTab = "review";
      renderTabs();
      review.setTurning(step);
    },
    reveal: () => {
      state.revealed = true;
      renderHeader();
      renderSignals();
      jevPanel?.refresh();
    },
    onJev: (jev) => {
      state.jev = jev;
      renderSignals();
      renderRail();
      renderTranscriptMarks();
    },
    onReview: (reviewValue) => {
      state.review = reviewValue;
      renderTabs();
      renderRail();
      renderTranscriptMarks();
    },
  };

  // ---- header & task --------------------------------------------------------------

  function renderHeader() {
    const outcome = run.outcome || {};
    const nav = ctx.queueNav;
    const position = nav ? nav.ids.indexOf(trajectoryId) : -1;
    const navButtons = nav && position >= 0
      ? h(
          "div",
          { class: "queue-nav" },
          h("button", { type: "button", disabled: position <= 0, onclick: () => ctx.navigate("#/t/" + encodeURIComponent(nav.ids[position - 1])) }, "← 上一条"),
          h("span", {}, "练习 " + (position + 1) + " / " + nav.ids.length),
          h("button", { type: "button", disabled: position >= nav.ids.length - 1, onclick: () => ctx.navigate("#/t/" + encodeURIComponent(nav.ids[position + 1])) }, "下一条 →")
        )
      : null;
    const meta = run.meta || {};
    const facts = [
      run.collection,
      run.adapter_id,
      meta.harness && meta.harness !== run.adapter_id ? meta.harness + (meta.harness_version ? " " + meta.harness_version : "") : null,
      meta.model || run.metrics.model,
      meta.group_role && meta.group_role !== "main" ? ROLE_LABELS[meta.group_role] + (meta.segment ? " #" + meta.segment : "") : null,
    ].filter(Boolean);
    let verdict;
    if (!state.revealed) {
      verdict = h("div", { class: "verdict hidden-verdict" }, h("strong", {}, "评分已隐藏"), h("span", {}, "在右侧先给出你的判断"));
    } else {
      verdict = h(
        "div",
        { class: "verdict " + outcomeTone(outcome.status) },
        h("strong", {}, OUTCOME_LABELS[outcome.status] || outcome.status || "无评分"),
        h("span", {}, [outcome.score != null ? "score " + outcome.score : null, outcome.reason, outcome.source].filter(Boolean).join(" · ") || "来源未提供评分"),
        outcome.infra_failure ? chip("疑似基础设施失败", "warn") : null
      );
    }
    clear(
      header,
      h(
        "div",
        { class: "reader-identity" },
        h("span", { class: "eyebrow" }, facts.join(" · ")),
        h("h1", {}, run.title),
        h("code", {}, run.source_path + (meta.sample_id ? " # " + meta.sample_id : ""))
      ),
      h("div", { class: "reader-side" }, verdict, navButtons, compareButton())
    );
  }

  function compareButton() {
    const picked = ctx.compareSelection.includes(trajectoryId);
    const others = ctx.compareSelection.filter((item) => item !== trajectoryId);
    return h(
      "div",
      { class: "inline" },
      h("button", { type: "button", class: "secondary", onclick: () => { ctx.toggleCompare(trajectoryId); renderHeader(); } }, picked ? "✓ 已加入对比" : "加入对比"),
      others.length ? h("a", { class: "link-button", href: "#/pair/" + encodeURIComponent(others[0]) + "/" + encodeURIComponent(trajectoryId) }, "与已选的对比 →") : null
    );
  }

  function renderTask() {
    const task = run.task || {};
    const instruction = task.instruction || "（来源里没有可识别的任务说明）";
    const metrics = run.metrics || {};
    const numbers = [
      ["步", run.step_index.length],
      ["工具调用", metrics.tool_calls],
      ["工具报错", metrics.tool_errors],
      ["时长", metrics.has_clock || metrics.wall_ms ? formatDuration(metrics.wall_ms ?? metrics.max_offset_ms) : "—"],
      ["tokens", formatCount(metrics.total_tokens)],
      ["费用", metrics.total_cost_usd != null ? "$" + Number(metrics.total_cost_usd).toFixed(2) : "—"],
    ];
    const body = h("div", { class: "task-text" }, instruction.length > 1200 ? instruction.slice(0, 1200) + "…" : instruction);
    const toggle = instruction.length > 1200
      ? h("button", { type: "button", class: "link-button", onclick: () => { body.textContent = instruction; toggle.remove(); } }, "展开全文")
      : null;
    clear(
      taskCard,
      h("div", { class: "task-main" }, h("span", { class: "eyebrow" }, "任务" + (task.id ? " · " + task.id : "")), body, toggle),
      h("dl", { class: "task-numbers" }, numbers.map(([label, value]) => h("div", {}, h("dt", {}, label), h("dd", {}, String(value ?? "—")))))
    );
  }

  // ---- signals --------------------------------------------------------------------

  function renderSignals() {
    const chips = [];
    const suppressed = new Set(run.signals?.suppressed || []);
    const flags = (run.signals?.flags || []).filter((flag) => !suppressed.has(flag.key));
    (run.signals?.flags || []).filter((flag) => suppressed.has(flag.key)).forEach((flag) => {
      chips.push(h("span", { class: "signal muted", title: "后面还有上下文分段，这条信号只看 episode 的最后一段" }, h("b", {}, flag.label), h("span", {}, "后续分段继续，不计")));
    });
    flags.forEach((flag) => {
      chips.push(
        h(
          "button",
          { type: "button", class: "signal " + flag.severity, title: flag.detail, onclick: () => setHighlight("规则 · " + flag.label, flag.steps) },
          h("b", {}, flag.label),
          h("span", {}, flag.detail)
        )
      );
    });
    const values = run.signals?.values || {};
    if (values.verified_after_last_change === true && values.verification) {
      chips.push(
        h(
          "button",
          { type: "button", class: "signal ok", onclick: () => setHighlight("最后改动后的检查", [values.verification.step]) },
          h("b", {}, "改完有检查"),
          h("span", {}, "#" + values.verification.step + " " + values.verification.reason)
        )
      );
    }
    const summary = state.jev?.summary;
    if (summary) {
      Object.entries(summary.flagged || {}).forEach(([key, steps]) => {
        if (key === "notices_problem") return;
        chips.push(
          h(
            "button",
            { type: "button", class: "signal jev", onclick: () => setHighlight("Jev · " + jevLabel(key), steps) },
            h("b", {}, "Jev · " + jevLabel(key)),
            h("span", {}, steps.length + " 步")
          )
        );
      });
      if (summary.claims_done_unverified?.length) {
        chips.push(h("button", { type: "button", class: "signal jev warn", onclick: () => setHighlight("Jev · 未验证即宣称完成", summary.claims_done_unverified) }, h("b", {}, "Jev · 未验证即宣称完成"), h("span", {}, summary.claims_done_unverified.map((s) => "#" + s).join(" "))));
      }
      if (state.revealed && summary.consistency === "overclaim") chips.push(h("span", { class: "signal bad" }, h("b", {}, "声称完成但评分失败"), h("span", {}, "对比最终汇报与评分")));
      if (state.revealed && summary.consistency === "underclaim") chips.push(h("span", { class: "signal warn" }, h("b", {}, "自称未完成但评分通过"), h("span", {}, "评分器可能太松")));
    }
    const layers = values.layers || {};
    const layerText = Object.entries(layers).filter(([, count]) => count).map(([key, count]) => (LAYER_LABELS[key.replace("_events", "")] || key) + " " + count);
    clear(
      signalRow,
      chips.length ? chips : h("span", { class: "muted" }, "规则信号没有触发"),
      layerText.length ? h("span", { class: "layer-summary", title: "工具调用与事件按 harness 层归属" }, "harness 层：" + layerText.join(" · ")) : null
    );
  }

  function setHighlight(label, steps) {
    state.highlight = steps && steps.length ? { label, steps: new Set(steps) } : null;
    renderRail();
    renderTranscriptMarks();
    renderTranscriptTools();
    if (state.onlyHighlighted) loadMessages();
    else if (steps?.length) jumpTo(steps[0]);
  }

  // ---- rail -----------------------------------------------------------------------

  // Mark on the rail which steps are on screen (the rail doubles as a minimap).
  const visible = new Set();
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      const step = Number(entry.target.dataset.step);
      if (entry.isIntersecting) visible.add(step);
      else visible.delete(step);
    });
    rail.querySelectorAll(".rail-cell.in-view").forEach((cell) => cell.classList.remove("in-view"));
    visible.forEach((step) => rail.children[step - 1]?.classList.add("in-view"));
  });

  function renderRail() {
    const noted = notes.steps();
    const cells = railCells(run.step_index, {
      jevSteps: state.jev?.steps?.steps || [],
      flaggedSteps: state.highlight?.steps || new Set(),
      turningStep: state.review?.turning_step ?? null,
    });
    clear(
      rail,
      cells.map((cell) =>
        h("button", {
          type: "button",
          class: ["rail-cell", "k-" + cell.kind, cell.error && "error", cell.flagged && "flagged", cell.turning && "turning", noted.has(cell.step) && "noted"].filter(Boolean).join(" "),
          title: "#" + cell.step + " · " + (PHASE_LABELS[cell.kind] || cell.kind) + (cell.error ? " · 工具报错" : ""),
          onclick: () => jumpTo(cell.step),
        })
      )
    );
    const hasPhase = Boolean(state.jev?.steps?.steps?.length);
    const legend = hasPhase
      ? Object.entries(PHASE_LABELS).map(([key, label]) => h("span", { class: "legend k-" + key }, label))
      : [["user", "用户"], ["assistant", "模型回复"], ["tool", "工具调用"], ["system", "系统"], ["result", "结果"], ["layer-hook", "Hook"]].map(([key, label]) => h("span", { class: "legend k-" + key }, label));
    clear(
      railPanel.querySelector(".rail-legend"),
      h("strong", {}, hasPhase ? "步骤条 · Jev 阶段" : "步骤条"),
      legend,
      h("span", { class: "legend error" }, "工具报错"),
      h("span", { class: "legend turning" }, "转折点"),
      noted.size ? h("span", { class: "legend noted" }, "批注 " + noted.size) : null,
      state.highlight ? h("span", { class: "legend flagged" }, "高亮：" + state.highlight.label + "（" + state.highlight.steps.size + "）") : null
    );
      visible.forEach((step) => rail.children[step - 1]?.classList.add("in-view"));
  }

  function renderTracks() {
    const metrics = run.metrics || {};
    const cells = trackCells(run.step_index, metrics.context_window);
    const hasContext = cells.some((cell) => cell.context != null);
    const hasClock = cells.some((cell) => cell.duration_ms != null);
    const cell = (item, child, extraClass, title) =>
      h("button", { type: "button", class: "track-cell " + extraClass, title, onclick: () => jumpTo(item.step) }, child);
    contextRow.hidden = !hasContext;
    clockRow.hidden = !hasClock;
    clear(
      contextRow,
      cells.map((item) =>
        cell(
          item,
          h("span", { class: "context-bar" + (item.measured ? "" : " estimated"), style: { height: Math.max(item.context ? 4 : 0, item.ratio * 100) + "%" } }),
          item.compaction ? "compaction" : "",
          "#" + item.step + " · 上下文 " + (item.context == null ? "—" : formatCount(item.context) + " tokens" + (item.measured ? "" : "（估算）")) + (item.compaction ? " · 压缩边界" : "")
        )
      )
    );
    clear(
      clockRow,
      cells.map((item) => cell(item, null, "heat-" + item.heat, "#" + item.step + " · " + (item.duration_ms == null ? "无时间" : "到下一步 " + formatDuration(item.duration_ms))))
    );
    const measured = metrics.context_measured_steps > 0;
    const facts = [
      hasContext ? "上下文峰值 " + formatCount(metrics.context_peak) + (measured ? "" : "（按字数估算）") + (metrics.context_window ? " / 窗口 " + formatCount(metrics.context_window) : "") : null,
      metrics.compactions ? "压缩 " + metrics.compactions + " 次" : null,
      metrics.longest_gap_ms != null ? "最长停顿 " + formatDuration(metrics.longest_gap_ms) : null,
    ].filter(Boolean);
    clear(
      trackLegend,
      facts.length ? h("span", {}, facts.join(" · ")) : null,
      hasContext ? h("span", { class: "legend context" }, "上下文") : null,
      hasContext && metrics.compactions ? h("span", { class: "legend compaction" }, "压缩边界") : null,
      hasClock ? [["fast", "<10s"], ["normal", "<1min"], ["slow", "<5min"], ["stall", "≥5min"]].map(([key, label]) => h("span", { class: "legend heat-" + key }, label)) : null
    );
  }

  // ---- transcript -----------------------------------------------------------------

  function renderTranscriptTools() {
    const roleButtons = [["user", "用户"], ["assistant", "模型"], ["system", "系统/Hook"], ["result", "结果"]].map(([role, label]) => {
      const pressed = role === "assistant" ? state.roles.has("assistant") : state.roles.has(role);
      return h(
        "button",
        {
          type: "button",
          "aria-pressed": String(pressed),
          onclick: () => {
            const roles = role === "assistant" ? ["assistant", "tool"] : [role];
            roles.forEach((item) => (pressed ? state.roles.delete(item) : state.roles.add(item)));
            renderTranscriptTools();
            loadMessages();
          },
        },
        label
      );
    });
    const search = h("input", { type: "search", placeholder: "搜索消息、命令、输出", value: state.search });
    search.addEventListener("input", debounce(() => { state.search = search.value.trim(); loadMessages(); }, 220));
    const only = state.highlight
      ? h("label", { class: "only-toggle" }, h("input", { type: "checkbox", checked: state.onlyHighlighted, onchange: (event) => { state.onlyHighlighted = event.target.checked; loadMessages(); } }), "只看高亮步骤")
      : null;
    const clearHighlight = state.highlight ? h("button", { type: "button", class: "link-button", onclick: () => setHighlight(null, null) }, "清除高亮") : null;
    const compact = h("button", { type: "button", "aria-pressed": String(Boolean(state.compact)), title: "一步一行（c）", onclick: () => setCompact(!state.compact) }, "紧凑");
    const training = h(
      "button",
      {
        type: "button",
        "aria-pressed": String(Boolean(state.training)),
        title: "SFT 训练视角：模型自己的回合（被训练）高亮，上下文变淡",
        onclick: () => {
          state.training = !state.training;
          transcript.classList.toggle("training-view", state.training);
          renderTranscriptTools();
        },
      },
      "训练视角"
    );
    clear(transcriptTools, h("div", { class: "role-filters" }, roleButtons), search, only, clearHighlight, compact, training, exportPanel, shortcuts);
  }

  // The transcript shows a window [windowStart, windowEnd) of the (filtered) message list,
  // so a deep link can open at step 3000 without loading everything before it.
  let messageToken = 0;
  let windowStart = 0;
  let windowEnd = 0;
  const PAGE = 300;
  async function loadMessages(offset = 0, mode = "replace", limit = PAGE) {
    const token = ++messageToken;
    if (mode === "replace") clear(transcript, empty("读取消息…"));
    const onlyHighlighted = state.onlyHighlighted && state.highlight;
    const params = onlyHighlighted
      ? { steps: [...state.highlight.steps].join(","), limit: 500 }
      : { roles: [...state.roles].join(","), search: state.search, offset, limit };
    const payload = await api.messages(trajectoryId, params);
    if (token !== messageToken || !still()) return;
    const nodes = payload.items.map((message) => renderMessage(message));
    if (mode === "prepend") {
      const anchor = transcript.firstElementChild;
      const before = anchor ? anchor.getBoundingClientRect().top : 0;
      transcript.prepend(...nodes);
      windowStart = payload.offset;
      const scroller = scrollParent(transcript);
      if (anchor && scroller) scroller.scrollTop += anchor.getBoundingClientRect().top - before;
    } else if (mode === "append") {
      transcript.append(...nodes);
      windowEnd = payload.offset + payload.items.length;
    } else {
      transcript.replaceChildren(...nodes);
      windowStart = payload.offset;
      windowEnd = payload.offset + payload.items.length;
      if (!payload.items.length) transcript.append(empty("没有符合筛选的消息"));
    }
    clear(
      earlier,
      !onlyHighlighted && windowStart > 0
        ? h("button", { type: "button", onclick: () => loadMessages(Math.max(0, windowStart - PAGE), "prepend", Math.min(PAGE, windowStart)) }, "加载前面的（还有 " + windowStart + " 条）")
        : null
    );
    clear(
      more,
      !onlyHighlighted && windowEnd < payload.total
        ? h("button", { type: "button", onclick: () => loadMessages(windowEnd, "append") }, "继续加载（" + windowEnd + " / " + payload.total + "）")
        : h("span", { class: "muted" }, payload.total + " 条消息")
    );
    renderTranscriptMarks();
  }

  const tokensByStep = new Map(run.step_index.map((item) => [item.step, item.tokens || { trained: 0, context: 0 }]));
  const residueSteps = new Set((run.readiness?.issues || []).filter((issue) => issue.key === "residue_in_trained").flatMap((issue) => issue.steps));
  function trainingBadge(step) {
    const tokens = tokensByStep.get(step) || { trained: 0, context: 0 };
    return h(
      "span",
      { class: "training-badge" + (tokens.trained ? " trained" : "") },
      tokens.trained ? "训练 " + formatCount(tokens.trained) : "上下文 " + formatCount(tokens.context),
      residueSteps.has(step) ? " · 含 harness 痕迹" : ""
    );
  }

  function stepFlags(step) {
    const labels = [];
    const suppressed = new Set(run.signals?.suppressed || []);
    (run.signals?.flags || []).forEach((flag) => { if (!suppressed.has(flag.key) && flag.steps.includes(step)) labels.push([flag.label, flag.severity]); });
    const record = (state.jev?.steps?.steps || []).find((item) => item.step === step);
    Object.entries(record?.p || {}).forEach(([key, p]) => {
      if (p >= 0.5 && key !== "notices_problem") labels.push(["Jev · " + jevLabel(key) + " " + Math.round(p * 100) + "%", "jev"]);
    });
    if (record?.work && record.work !== "polish") labels.push(["改动：" + (WORK_LABELS[record.work] || record.work), "muted"]);
    return { labels, phase: record?.phase, problem: (record?.p?.notices_problem || 0) >= 0.5 };
  }

  function renderMessage(message) {
    const article = h("article", { class: "message " + message.role, id: "step-" + message.step, dataset: { step: message.step } });
    observer.observe(article);
    const marks = h("span", { class: "step-marks" });
    const head = h(
      "div",
      { class: "message-head" },
      h("span", { class: "step-no" }, "#" + message.step),
      h("span", { class: "message-role" }, message.layer ? LAYER_LABELS[message.layer] : message.role),
      message.offset_ms != null ? h("span", { class: "message-time" }, formatClock(message.offset_ms)) : null,
      message.sidechain ? chip("sidechain", "muted") : null,
      h("span", { class: "message-summary" }, compactSummary(message)),
      trainingBadge(message.step),
      marks,
      h("button", { type: "button", class: "turning-button", title: "给这一步写批注（a）", onclick: () => notes.edit(noteSlot, message.step) }, "批注"),
      message.role === "assistant" || message.role === "tool"
        ? h("button", { type: "button", class: "turning-button", title: "把这一步记为从正轨走偏的转折点", onclick: () => api_.setTurning(message.step) }, "设为转折点")
        : null
    );
    const noteSlot = h("div", { class: "step-notes" });
    article.append(head, noteSlot);
    notes.decorate(noteSlot, message.step);
    article.addEventListener("click", (event) => {
      if (!state.compact || event.target.closest("button, a, summary, input, details[open] pre")) return;
      article.classList.toggle("expanded");
    });
    if (message.thinking) {
      const preview = message.thinking.length > 280 ? message.thinking.slice(0, 280) + "…" : message.thinking;
      const block = h("details", { class: "thinking-detail" }, h("summary", {}, "思考 · " + preview.replace(/\s+/g, " ")), clipped("div", "thinking-text", message.thinking));
      article.append(block);
    }
    if (message.text) article.append(clipped("div", "message-text", message.text));
    appendImages(article, message.images, "消息图片");
    message.tools.forEach((tool) => article.append(renderTool(tool)));
    if (message.native_result) article.append(details("原生 result 记录", JSON.stringify(message.native_result, null, 2)));
    return article;
  }

  function renderTranscriptMarks() {
    transcript.querySelectorAll("article.message").forEach((article) => {
      const step = Number(article.dataset.step);
      const info = stepFlags(step);
      const marks = article.querySelector(".step-marks");
      if (!marks) return;
      clear(
        marks,
        info.phase ? chip(PHASE_LABELS[info.phase] || info.phase, "phase k-" + info.phase) : null,
        info.problem ? chip("Jev · 发现问题", "jev-soft") : null,
        info.labels.map(([label, tone]) => chip(label, tone))
      );
      article.classList.toggle("highlighted", Boolean(state.highlight?.steps.has(step)));
      article.classList.toggle("turning", state.review?.turning_step === step);
    });
  }

  function renderTool(tool) {
    const result = tool.result;
    const box = h("div", { class: "tool-box" + (result?.is_error ? " error" : "") });
    box.append(
      h(
        "div",
        { class: "tool-head" },
        h(
          "div",
          { class: "tool-identity" },
          h("strong", {}, tool.name),
          tool.layer && tool.layer !== "base" ? chip(LAYER_LABELS[tool.layer], "layer") : null,
          tool.mutation ? chip("写操作", "muted") : null,
          tool.raw_name && tool.raw_name !== tool.name ? h("code", { class: "tool-raw-name" }, tool.raw_name) : null
        ),
        h(
          "div",
          { class: "tool-call-status" + (result?.is_error ? " error" : "") },
          h(
            "span",
            result?.error_inferred ? { title: "源数据没有报错标记，由返回文本推断（Exit code / tool_use_error / isError）" } : {},
            result ? (result.is_error ? (result.error_inferred ? "报错（推断）" : "报错") : "返回") : "无返回"
          ),
          h("code", {}, tool.id)
        )
      )
    );
    const input = typeof tool.input === "string" ? tool.input : JSON.stringify(tool.input, null, 2);
    box.append(details("输入", input, false));
    const output = result ? result.text : "没有匹配的工具返回";
    box.append(details(result?.is_error ? "返回 · 报错" : "返回", foldedOutput(output), Boolean(result?.is_error)));
    if (tool.repeat_of && result) box.append(repeatDiff(tool));
    appendImages(box, result?.images || [], tool.name);
    const child = spawned.get(tool.id);
    if (child) {
      box.append(
        h(
          "a",
          { class: "spawn-link", href: "#/t/" + encodeURIComponent(child.id) },
          "→ 打开子代理" + (child.subagent_type ? " " + child.subagent_type : "") + " · " + (child.steps ?? "?") + " 步" + (child.tool_errors ? " · 报错 " + child.tool_errors : "")
        )
      );
    }
    return box;
  }

  function appendImages(target, images, label) {
    if (!images?.length) return;
    const gallery = h("div", { class: "tool-images" });
    images.forEach((image, index) => {
      const src = image.url || (image.data ? "data:" + image.media_type + ";base64," + image.data : null);
      if (!src) return;
      const element = h("img", { loading: "lazy", alt: label + " #" + (index + 1), src });
      element.addEventListener("click", () => ctx.openImage(src, element.alt));
      element.addEventListener("error", () => element.replaceWith(h("span", { class: "chip muted" }, "图片不可读")));
      gallery.append(element);
    });
    target.append(gallery);
  }

  /** Long outputs show their head and tail; the middle opens on request. */
  function foldedOutput(text) {
    const fold = foldLines(text);
    if (!fold.folded) return h("pre", {}, text);
    const pre = h("pre", {}, fold.head.join("\n"));
    const button = h("button", { type: "button", class: "fold-button", onclick: () => { pre.textContent = text; button.remove(); tailPre.remove(); } }, "… 展开中间 " + fold.hidden + " 行 …");
    const tailPre = h("pre", {}, fold.tail.join("\n"));
    return h("div", { class: "folded-output" }, pre, button, tailPre);
  }

  /** "Same command as step N": diff this output against that earlier run. */
  function repeatDiff(tool) {
    const slot = h("div", { class: "repeat-diff" });
    const button = h(
      "button",
      {
        type: "button",
        class: "link-button",
        onclick: async () => {
          button.disabled = true;
          try {
            const page = await api.messages(trajectoryId, { steps: String(tool.repeat_of.step), limit: 5 });
            const earlier = page.items.flatMap((message) => message.tools).find((item) => item.id === tool.repeat_of.id);
            const diff = lineDiff(earlier?.result?.text || "", tool.result?.text || "");
            if (!diff) {
              clear(slot, h("span", { class: "muted" }, "输出太长，不做逐行对比"));
            } else if (diff.every((line) => line.op === "=")) {
              clear(slot, h("span", { class: "muted" }, "和 #" + tool.repeat_of.step + " 的输出完全一样"));
            } else {
              const changed = diff.filter((line) => line.op !== "=").length;
              clear(
                slot,
                h("div", { class: "muted" }, "与 #" + tool.repeat_of.step + " 相比，" + changed + " 行不同（- 那次 / + 这次）"),
                h("pre", { class: "diff" }, diff.filter((line, index) => line.op !== "=" || diff.slice(Math.max(0, index - 2), index + 3).some((near) => near.op !== "=")).map((line) =>
                  h("span", { class: "diff-line " + (line.op === "+" ? "add" : line.op === "-" ? "del" : "same") }, line.op + " " + line.text + "\n")
                ))
              );
            }
          } catch (error) {
            clear(slot, h("span", { class: "muted" }, error.message));
          }
        },
      },
      "同一命令在 #" + tool.repeat_of.step + " 跑过 · 对比输出"
    );
    return h("div", { class: "repeat-row" }, button, slot);
  }

  function clipped(tag, className, text) {
    const element = h(tag, { class: className });
    if (text.length <= TEXT_CLIP) {
      element.textContent = text;
      return element;
    }
    element.textContent = text.slice(0, TEXT_CLIP) + "\n…";
    const button = h("button", { type: "button", class: "link-button", onclick: () => { element.textContent = text; button.remove(); } }, "展开剩余 " + formatCount(text.length - TEXT_CLIP) + " 字");
    return h("div", {}, element, button);
  }

  async function jumpTo(step, { smooth = true } = {}) {
    let target = document.getElementById("step-" + step);
    if (!target) {
      // Unfiltered, steps are numbered 1…N in order, so step k sits at offset k-1.
      state.roles = new Set(["user", "assistant", "tool", "system", "result"]);
      state.search = "";
      state.onlyHighlighted = false;
      renderTranscriptTools();
      await loadMessages(Math.max(0, step - 1 - 20), "replace");
      target = document.getElementById("step-" + step);
    }
    if (!target) return;
    focusStep(target, smooth);
    target.classList.remove("flash");
    void target.offsetWidth;
    target.classList.add("flash");
  }

  // ---- keyboard & compact view ------------------------------------------------------

  let focused = null;
  function focusStep(article, smooth = true) {
    focused?.classList.remove("focused");
    focused = article;
    article.classList.add("focused");
    article.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "center" });
    // Keep the step in the address bar so the link can be shared (no re-render).
    history.replaceState(null, "", "#/t/" + encodeURIComponent(trajectoryId) + "?step=" + article.dataset.step);
  }

  async function moveFocus(direction, predicate = () => true) {
    const articles = [...transcript.querySelectorAll("article.message")];
    if (!articles.length) return;
    let index = focused ? articles.indexOf(focused) : -1;
    for (;;) {
      index += direction;
      if (index >= articles.length) {
        if (!more.querySelector("button")) return;
        await loadMessages(windowEnd, "append");
        return moveFocus(direction, predicate);
      }
      if (index < 0) {
        if (!earlier.querySelector("button")) return;
        await loadMessages(Math.max(0, windowStart - PAGE), "prepend", Math.min(PAGE, windowStart));
        return moveFocus(direction, predicate);
      }
      if (predicate(articles[index])) return focusStep(articles[index]);
    }
  }

  function nextMatching(direction, steps) {
    const sorted = [...steps].sort((a, b) => a - b);
    const current = focused ? Number(focused.dataset.step) : 0;
    const step = direction > 0 ? sorted.find((item) => item > current) : sorted.reverse().find((item) => item < current);
    if (step != null) jumpTo(step);
  }

  function setCompact(value) {
    state.compact = value;
    persist("reader.compact", value ? "1" : "");
    transcript.classList.toggle("compact", value);
    renderTranscriptTools();
    focused?.scrollIntoView({ block: "center" });
  }

  const errorSteps = run.step_index.filter((item) => item.error).map((item) => item.step);
  function onKey(event) {
    if (!still() || !document.body.contains(transcript)) {
      document.removeEventListener("keydown", onKey);
      return;
    }
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.target.closest?.("input, textarea, select, [contenteditable]")) return;
    const actions = {
      j: () => moveFocus(1),
      k: () => moveFocus(-1),
      J: () => moveFocus(1, (article) => article.classList.contains("assistant") || article.classList.contains("tool")),
      K: () => moveFocus(-1, (article) => article.classList.contains("assistant") || article.classList.contains("tool")),
      n: () => state.highlight && nextMatching(1, state.highlight.steps),
      p: () => state.highlight && nextMatching(-1, state.highlight.steps),
      e: () => nextMatching(1, errorSteps),
      E: () => nextMatching(-1, errorSteps),
      t: () => focused && api_.setTurning(Number(focused.dataset.step)),
      a: () => focused && notes.edit(focused.querySelector(".step-notes"), Number(focused.dataset.step)),
      c: () => setCompact(!state.compact),
      o: () => focused && focused.classList.toggle("expanded"),
      "/": () => transcriptTools.querySelector("input[type=search]")?.focus(),
      "[": () => ctx.queueNav && header.querySelector(".queue-nav button:first-child")?.click(),
      "]": () => ctx.queueNav && header.querySelector(".queue-nav button:last-child")?.click(),
      "?": () => shortcuts.toggleAttribute("open"),
    };
    const action = actions[event.key];
    if (!action) return;
    event.preventDefault();
    action();
  }
  document.addEventListener("keydown", onKey);
  const exportPanel = notes.exportPanel();
  const shortcuts = h(
    "details",
    { class: "shortcuts" },
    h("summary", {}, "快捷键"),
    h(
      "dl",
      {},
      [
        ["j / k", "下一步 / 上一步"],
        ["J / K", "只在模型步骤间跳"],
        ["n / p", "下一个 / 上一个高亮步骤"],
        ["e / E", "下一个 / 上一个工具报错"],
        ["t", "把当前步设为转折点"],
        ["a", "给当前步写批注"],
        ["c", "紧凑模式（一步一行）"],
        ["o", "紧凑模式下展开当前步"],
        ["/", "搜索消息"],
        ["[ / ]", "练习队列上一条 / 下一条"],
      ].map(([key, text]) => h("div", {}, h("dt", {}, h("kbd", {}, key)), h("dd", {}, text)))
    )
  );

  // ---- side panels ----------------------------------------------------------------

  renderHeader();
  renderTask();
  renderSignals();
  renderRail();
  renderTracks();
  renderTranscriptTools();
  const review = renderReviewPanel(reviewSlot, api_);
  const jevPanel = renderJevPanel(jevSlot, api_);
  ledgerPanel = renderLedgerPanel(ledgerPane, api_, episode);
  analysisPane.append(renderReadiness(run, ctx, api_), renderRelated(run, ctx), renderToolCatalog(run));
  if (run.adapter_id === "moh-v1") renderMohPanels(analysisPane, run, trajectoryId, ctx);
  renderTabs();
  notes.load().catch(() => null);
  transcript.classList.toggle("compact", Boolean(state.compact));
  const initialStep = Number(params.get("step"));
  // Offsets count every step only when no role is filtered out.
  if (initialStep > 0) state.roles = new Set(["user", "assistant", "tool", "system", "result"]);
  await loadMessages(initialStep > 0 ? Math.max(0, initialStep - 1 - 20) : 0);
  if (initialStep > 0 && still()) {
    await jumpTo(initialStep, { smooth: false });
    const terms = (params.get("hl") || "").split(/\s+/).filter(Boolean);
    const target = document.getElementById("step-" + initialStep);
    if (target && terms.length) markTerms(target, terms);
  }
}

/** Wrap occurrences of the terms inside an element's text in <mark> (text nodes only). */
function markTerms(root, terms) {
  const wanted = terms.map((term) => term.toLowerCase());
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach((node) => {
    const parts = highlighted(node.nodeValue, wanted);
    if (parts.length > 1) node.replaceWith(...parts);
  });
  root.querySelectorAll("details").forEach((block) => {
    if (block.querySelector("mark")) block.open = true;
  });
}

/** One line for compact mode: what the step says or thinks, then its calls. */
function compactSummary(message) {
  const words = (message.text || message.thinking || "").replace(/\s+/g, " ").trim();
  const calls = message.tools
    .map((tool) => {
      const input = tool.input && typeof tool.input === "object" ? tool.input : {};
      const target = input.command || input.file_path || input.path || input.pattern || input.url || input.description || "";
      return tool.name + (target ? "(" + String(target).replace(/\s+/g, " ").slice(0, 60) + ")" : "") + (tool.result?.is_error ? " ✗" : "");
    })
    .join(" · ");
  return [words.slice(0, 160) + (words.length > 160 ? "…" : ""), calls ? "▸ " + calls : ""].filter(Boolean).join("  ");
}

function scrollParent(element) {
  for (let node = element.parentElement; node; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (/(auto|scroll)/.test(style.overflowY) && node.scrollHeight > node.clientHeight) return node;
  }
  return document.scrollingElement;
}

/** Main trajectory, context segments and subagents of this attempt, in reading order. */
function renderEpisodeStrip(episode, currentId, ctx) {
  const members = (episode?.threads?.length || 0) + (episode?.subagents?.length || 0);
  if (!episode || members <= 1) return null;
  const tone = (item) => (ctx.blind ? "muted" : outcomeTone(item.outcome_status));
  const lane = (item) => {
    if (item.missing) return h("span", { class: "episode-node missing", title: "这一段没有在导入的数据里" }, "第 " + item.segment + " 段缺失");
    const label = item.role === "main" ? "主轨迹" : "第 " + (item.segment ?? "?") + " 段";
    return h(
      "a",
      { class: "episode-node" + (item.current ? " current" : ""), href: "#/t/" + encodeURIComponent(item.id), title: item.context_reason ? "分段原因：" + item.context_reason : "" },
      h("span", { class: "dot " + tone(item) }),
      h("b", {}, label),
      h("span", {}, item.steps + " 步" + (item.tool_errors ? " · 报错 " + item.tool_errors : "") + (item.reviewed ? " · 已读" : ""))
    );
  };
  const threads = [];
  episode.threads.forEach((item, index) => {
    if (index) threads.push(h("span", { class: "episode-arrow", "aria-hidden": "true" }, "→"));
    threads.push(lane(item));
  });
  const subagents = episode.subagents.map((sub) =>
    h(
      "div",
      { class: "episode-sub" + (sub.current ? " current" : "") },
      h("a", { href: "#/t/" + encodeURIComponent(sub.id) }, h("span", { class: "dot " + tone(sub) }), h("b", {}, sub.subagent_type || "子代理"), " ", sub.description || sub.run_id),
      h("span", { class: "muted" }, sub.steps + " 步" + (sub.tool_errors ? " · 报错 " + sub.tool_errors : "")),
      sub.spawn
        ? h("a", { class: "link-button", href: "#/t/" + encodeURIComponent(sub.spawn.trajectory_id) + "?step=" + sub.spawn.step }, "← 由 " + sub.spawn.tool + " #" + sub.spawn.step + " 派出")
        : h("span", { class: "muted" }, "派出位置未知")
    )
  );
  const summary = episode.summary || {};
  return h(
    "section",
    { class: "episode-strip panel" },
    h(
      "div",
      { class: "episode-head" },
      h("span", { class: "eyebrow" }, "Episode"),
      h("span", { class: "muted" }, [summary.steps != null ? "共 " + summary.steps + " 步" : null, summary.tool_errors ? "工具报错 " + summary.tool_errors : null, episode.head_missing ? "主轨迹未导入" : null].filter(Boolean).join(" · "))
    ),
    h("div", { class: "episode-lanes" }, threads),
    subagents.length ? h("div", { class: "episode-subs" }, subagents) : null
  );
}

function jevLabel(key) {
  return {
    notices_problem: "发现问题",
    ignores_error: "忽略报错",
    misreads_observation: "误读工具返回",
    thought_action_mismatch: "思行不一",
    claims_done: "宣称完成",
    harness_reference: "引用 harness",
    filler: "空转/道歉",
    polish: "打磨",
    violates_constraint: "违反题目约束",
  }[key] || key;
}

const READINESS_TONE = { block: "bad", warn: "warn", info: "muted" };

/** Would this trajectory train cleanly as an SFT sample? */
function renderReadiness(run, ctx, reader) {
  const result = run.readiness;
  if (!result) return null;
  const labels = ctx.taxonomy.readiness_issues || {};
  const trained = result.residue?.trained || {};
  const context = result.residue?.context || {};
  return h(
    "section",
    { class: "panel inspector-card" },
    h(
      "div",
      { class: "panel-head" },
      h(
        "div",
        {},
        h("h2", {}, "训练就绪 · " + (result.ready ? "可直接训练" : "有阻断问题")),
        h("p", {}, "约 " + formatCount(result.tokens) + " tokens（上限 " + formatCount(result.max_seq_len) + "）· 训练 token 占 " + Math.round((result.trained_share || 0) * 100) + "% · 图片 " + result.images)
      )
    ),
    result.issues.length
      ? h(
          "div",
          { class: "ledger-list" },
          result.issues.map((issue) =>
            h(
              "div",
              { class: "ledger-row" },
              h("div", { class: "ledger-meta" }, chip(labels[issue.key] || issue.key, READINESS_TONE[issue.severity] || "muted"), issue.continued ? h("small", {}, "（后面还有分段，不算问题）") : null),
              h("div", { class: "ledger-text" }, issue.detail, " ", ...(issue.steps || []).slice(0, 8).map((step) => h("button", { type: "button", class: "step-link", onclick: () => reader.jump(step) }, "#" + step)))
            )
          )
        )
      : h("p", { class: "muted" }, "没有发现问题"),
    Object.keys(trained).length || Object.keys(context).length
      ? h(
          "p",
          { class: "hint" },
          "harness 痕迹按位置：训练 token 里 " + (Object.entries(trained).map(([key, steps]) => key + " × " + steps.length).join("、") || "无") +
            "；上下文里 " + (Object.entries(context).map(([key, steps]) => key + " × " + steps.length).join("、") || "无") + "（上下文里的不会被训练）"
        )
      : null
  );
}

function renderRelated(run, ctx) {
  const card = h("section", { class: "panel inspector-card" });
  const sameTask = run.same_task || [];
  if (!sameTask.length) return card.appendChild(h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, "同一道题的其他尝试"), h("p", {}, "没有找到（按任务 id 或题面开头匹配）")))) && card;
  const item = (entry, pairable) =>
    h(
      "div",
      { class: "related-row" },
      h("span", { class: "dot " + (ctx.blind ? "muted" : outcomeTone(entry.outcome_status)) }),
      h(
        "a",
        { href: "#/t/" + encodeURIComponent(entry.id) },
        (entry.group_role && entry.group_role !== "main" ? ROLE_LABELS[entry.group_role] + (entry.segment ? " " + entry.segment : "") + " · " : "") + (entry.model || entry.collection || "") + " · " + entry.steps + " 步"
      ),
      pairable ? h("a", { class: "link-button", href: "#/pair/" + encodeURIComponent(run.id) + "/" + encodeURIComponent(entry.id) }, "对照读") : null
    );
  card.append(h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, "同一道题的其他尝试（" + sameTask.length + "）"), h("p", {}, "每行一个 episode；分段和子代理在顶部 Episode 条"))));
  card.append(sameTask.slice(0, 30).map((entry) => item(entry, true)));
  return card;
}

function renderToolCatalog(run) {
  const entries = [...run.tool_catalog].sort((a, b) => b.call_count - a.call_count || a.name.localeCompare(b.name));
  const max = entries.length ? Math.max(1, entries[0].call_count) : 1;
  const layerOf = new Map(run.tool_index.map((tool) => [tool.name, tool.layer]));
  const errorsOf = new Map();
  run.tool_index.forEach((tool) => { if (tool.is_error) errorsOf.set(tool.name, (errorsOf.get(tool.name) || 0) + 1); });
  return h(
    "section",
    { class: "panel inspector-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, "工具目录"), h("p", {}, entries.filter((t) => t.observed).length + " 个实调 · " + entries.filter((t) => t.available_in_session).length + " 个声明可用"))),
    h(
      "div",
      { class: "tool-bars" },
      entries.map((tool) =>
        h(
          "div",
          { class: "tool-bar" },
          h(
            "div",
            { class: "tool-label" },
            h("strong", {}, tool.name),
            h("span", {}, [LAYER_LABELS[layerOf.get(tool.name) || (tool.name.startsWith("mcp__") ? "mcp" : "base")], tool.observed ? null : "未调用", errorsOf.get(tool.name) ? "报错 " + errorsOf.get(tool.name) : null].filter(Boolean).join(" · "))
          ),
          h("div", { class: "tool-track" }, h("div", { class: "tool-fill", style: { width: (tool.call_count / max) * 100 + "%" } })),
          h("span", { class: "tool-value" }, String(tool.call_count))
        )
      )
    )
  );
}
