import { api, pollJob } from "../api.js";
import { chip, clear, debounce, empty, h } from "../dom.js";
import { OUTCOME_LABELS, ROLE_LABELS, formatCount, formatDuration, outcomeTone } from "../presentation.mjs";

const PAGE = 100;

export async function renderLibrary(root, ctx, params, still) {
  const filters = {
    outcome: params.get("outcome") || "",
    flag: params.get("flag") || "",
    label: params.get("label") || "",
    reviewed: params.get("reviewed") || "",
    search: params.get("search") || "",
    view: params.get("view") || "episode",
    ready: params.get("ready") || "",
    issue: params.get("issue") || "",
    jev_flag: params.get("jev_flag") || "",
    task_key: params.get("task_key") || "",
    sort: params.get("sort") || "imported",
    offset: Number(params.get("offset") || 0),
  };
  const taxonomy = ctx.taxonomy;
  const title = ctx.collection || "全部集合";

  const status = h("span", { class: "muted" });
  const batchButton = h("button", { type: "button", class: "secondary", disabled: !ctx.jevAvailable }, "对未分析的轨迹跑 Jev");
  const compareLink = h("span");
  ctx.onCompareChange = () => {
    const selection = ctx.compareSelection;
    clear(
      compareLink,
      selection.length === 2
        ? h("a", { class: "button-like primary-link", href: "#/pair/" + selection.map(encodeURIComponent).join("/") }, "对比所选 2 条 →")
        : h("span", { class: "muted" }, "勾选 2 条可对比（已选 " + selection.length + "）")
    );
  };
  ctx.onCompareChange();
  const header = h(
    "header",
    { class: "page-head" },
    h("div", {}, h("span", { class: "eyebrow" }, "轨迹库"), h("h1", {}, title)),
    h("div", { class: "head-actions" }, compareLink, status, exportMenu(), batchButton)
  );

  /** Export what the current filters show as training JSONL plus a data card. */
  function exportMenu() {
    const stamp = new Date().toISOString().slice(0, 10);
    const name = h("input", { type: "text", "aria-label": "导出文件夹名", value: ((ctx.collection || "all") + "-" + stamp).replace(/[^\w.-]+/g, "_") });
    const mode = h(
      "select",
      { "aria-label": "导出格式" },
      h("option", { value: "raw" }, "原始行（字节拷贝，SFT/TraceLab）"),
      h("option", { value: "chat" }, "转成 OpenAI chat（任何格式）")
    );
    const note = h("span", { class: "save-status" });
    const start = h("button", { type: "button", class: "primary" }, "导出");
    start.addEventListener("click", async () => {
      start.disabled = true;
      note.textContent = "导出中…";
      try {
        const job = await api.startExport(
          {
            collection: ctx.collection || null,
            outcome: ctx.blind ? "" : filters.outcome,
            flag: filters.flag,
            label: filters.label,
            reviewed: filters.reviewed,
            ready: filters.ready,
            issue: filters.issue,
            jev_flag: filters.jev_flag,
            search: filters.search,
            task_key: filters.task_key,
            episodes: filters.view === "episode" ? 1 : "",
            roles: filters.view === "episode" || filters.view === "all" ? "" : filters.view,
          },
          name.value.trim(),
          mode.value
        );
        const finished = await pollJob(job.id, (state) => { note.textContent = "导出中 " + (state.done || 0) + " / " + (state.total ?? "…"); });
        if (finished.status === "failed") throw new Error(finished.error);
        note.textContent = "写出 " + finished.result.written + " 条" + (finished.result.skipped ? "（跳过 " + finished.result.skipped + "，见 data card）" : "") + " → " + finished.result.path;
      } catch (error) {
        note.textContent = "导出失败：" + error.message;
      } finally {
        start.disabled = false;
      }
    });
    return h(
      "details",
      { class: "export-excerpt" },
      h("summary", { class: "button-like" }, "导出筛选结果"),
      h(
        "div",
        { class: "export-excerpt-body" },
        h("small", { class: "muted" }, "按当前筛选导出；按 episode 看时整段 episode（含分段、子代理）一起导出。写到服务器的导出目录下。"),
        name,
        mode,
        h("div", { class: "inline" }, start),
        note
      )
    );
  }

  const select = (name, options, label) => {
    const element = h(
      "select",
      { "aria-label": label, onchange: () => update({ [name]: element.value, offset: 0 }) },
      options.map(([value, text]) => h("option", { value }, text))
    );
    element.value = filters[name];
    return element;
  };
  const search = h("input", { type: "search", placeholder: "搜索标题 / 任务 / id", value: filters.search });
  search.addEventListener("input", debounce(() => update({ search: search.value.trim(), offset: 0 }), 250));
  const controls = h(
    "div",
    { class: "filters panel" },
    search,
    ctx.blind
      ? h("span", { class: "chip muted" }, "盲判模式：隐藏评分")
      : select("outcome", [["", "全部结果"], ...Object.entries(OUTCOME_LABELS)], "评分结果"),
    select("flag", [["", "全部信号"], ...Object.entries(taxonomy.flags)], "规则信号"),
    select("jev_flag", [["", "全部 Jev 信号"], ...Object.entries(taxonomy.jev_flags || {})], "Jev 信号（有一步被标出）"),
    select("label", [["", "全部标签"], ...taxonomy.labels.map((item) => [item.key, item.label])], "人工标签"),
    select("reviewed", [["", "读过/未读"], ["0", "未读"], ["1", "已读"]], "是否已读"),
    select("ready", [["", "训练就绪：全部"], ["1", "可直接训练"], ["0", "有阻断问题"]], "训练就绪"),
    select("issue", [["", "全部就绪问题"], ...Object.entries(taxonomy.readiness_issues || {})], "就绪问题"),
    select("view", [["episode", "按 episode（一行一次尝试）"], ["main,segment", "主轨迹+分段"], ["subagent", "只看子代理"], ["all", "全部轨迹"]], "显示单位"),
    select("sort", [["imported", "最近导入"], ["steps", "步数最多"], ["errors", "报错最多"], ["task", "按任务分组"], ["title", "标题"]], "排序")
  );

  const table = h("div", { class: "trajectory-table panel" });
  const pager = h("div", { class: "pager" });
  clear(root, header, controls, table, pager);

  batchButton.addEventListener("click", async () => {
    batchButton.disabled = true;
    try {
      const job = await api.batchJev(ctx.collection || null, 200);
      const finished = await pollJob(job.id, (state) => {
        status.textContent = "Jev 分析 " + (state.done || 0) + " / " + (state.total ?? "…");
      });
      if (finished.status === "failed") throw new Error(finished.error);
      status.textContent =
        "Jev 完成 " + finished.result.analyzed + " 条 · " + formatCount(finished.result.input_tokens) + " tokens" +
        (finished.result.failed.length ? " · 失败 " + finished.result.failed.length : "");
      if (still()) load();
    } catch (error) {
      status.textContent = "Jev 失败：" + error.message;
    } finally {
      batchButton.disabled = !ctx.jevAvailable;
    }
  });

  function update(changes) {
    const next = { ...filters, ...changes };
    const query = new URLSearchParams();
    Object.entries(next).forEach(([key, value]) => {
      if (key === "view" ? value !== "episode" : value !== "" && value !== 0 && value !== "imported") query.set(key, value);
    });
    ctx.navigate("#/library" + (query.toString() ? "?" + query : ""));
  }

  async function load() {
    clear(table, empty("读取中…"));
    const payload = await api.trajectories({
      collection: ctx.collection,
      outcome: ctx.blind ? "" : filters.outcome,
      flag: filters.flag,
      label: filters.label,
      reviewed: filters.reviewed,
      ready: filters.ready,
      issue: filters.issue,
      jev_flag: filters.jev_flag,
      search: filters.search,
      episodes: filters.view === "episode" ? 1 : "",
      roles: filters.view === "episode" || filters.view === "all" ? "" : filters.view,
      task_key: filters.task_key,
      sort: filters.sort,
      offset: filters.offset,
      limit: PAGE,
    });
    if (!still()) return;
    status.textContent = formatCount(payload.total) + (filters.view === "episode" ? " 个 episode" : " 条");
    if (!payload.items.length) {
      clear(table, empty(ctx.collections.length ? "没有符合筛选的轨迹" : "左下角导入一个目录或文件开始"));
    } else {
      clear(
        table,
        h(
          "div",
          { class: "row head" },
          h("span", { title: "勾选两条进行对比" }, "比"),
          h("span", {}, "轨迹"),
          h("span", {}, "模型 / 来源"),
          h("span", { class: "num" }, "步"),
          h("span", { class: "num" }, "工具错"),
          h("span", {}, "信号"),
          h("span", {}, "阅读")
        ),
        payload.items.map((item) => (filters.view === "episode" ? episodeRow(item, ctx) : row(item, ctx)))
      );
    }
    const first = payload.total ? filters.offset + 1 : 0;
    const last = Math.min(payload.total, filters.offset + payload.items.length);
    clear(
      pager,
      h("button", { type: "button", disabled: filters.offset <= 0, onclick: () => update({ offset: Math.max(0, filters.offset - PAGE) }) }, "上一页"),
      h("span", {}, first + "–" + last + " / " + payload.total),
      h("button", { type: "button", disabled: filters.offset + PAGE >= payload.total, onclick: () => update({ offset: filters.offset + PAGE }) }, "下一页")
    );
  }
  await load();
}

/** One episode: the head row with episode-wide totals, expandable to its members. */
function episodeRow(item, ctx) {
  const episode = item.episode;
  if (!episode || episode.members <= 1) return row(item, ctx);
  const members = h("div", { class: "episode-members", hidden: true });
  let loaded = false;
  const toggle = h("button", { type: "button", class: "episode-toggle", "aria-expanded": "false", title: "展开 episode 成员" }, "▸");
  toggle.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    const open = members.hidden;
    members.hidden = !open;
    toggle.textContent = open ? "▾" : "▸";
    toggle.setAttribute("aria-expanded", String(open));
    if (open && !loaded) {
      loaded = true;
      clear(members, empty("读取中…"));
      try {
        const payload = await api.trajectories({ collection: item.collection, group_key: item.group_key, sort: "episode", limit: 500 });
        clear(
          members,
          payload.items.map((member) => row(member, ctx, true)),
          payload.total > payload.items.length ? empty("只列出前 " + payload.items.length + " / " + payload.total + " 条成员") : null
        );
      } catch (error) {
        clear(members, empty("读取失败：" + error.message));
      }
    }
  });
  const parts = [];
  if (episode.threads > 1) parts.push(episode.threads + " 段");
  if (episode.missing_segments?.length) parts.push("缺第 " + episode.missing_segments.join("、") + " 段");
  if (episode.subagents) parts.push(episode.subagents + " 个子代理");
  if (episode.head_missing) parts.push("主轨迹未导入");
  return h(
    "div",
    { class: "episode" },
    row({ ...item, steps: episode.steps, tool_errors: episode.tool_errors, flags: episode.flags }, ctx, false, {
      toggle,
      summary: parts.join(" · "),
      warn: Boolean(episode.missing_segments?.length || episode.head_missing),
      subagentFlags: (episode.subagent_flags || []).filter((key) => !(episode.flags || []).includes(key)),
    }),
    members
  );
}

function row(item, ctx, nested = false, episode = null) {
  const status = ctx.blind ? "hidden" : item.outcome_status;
  const flags = (item.flags || []).map((key) => chip(ctx.taxonomy.flags[key] || key, "flag"));
  (episode?.subagentFlags || []).forEach((key) => flags.push(chip("子代理·" + (ctx.taxonomy.flags[key] || key), "flag")));
  const blocking = (item.readiness?.issues || []).filter((issue) => issue.severity === "block");
  if (blocking.length) {
    const names = blocking.map((issue) => ctx.taxonomy.readiness_issues?.[issue.key] || issue.key);
    flags.push(chip("不可直接训练", "bad", { title: names.join("、") }));
  }
  const jev = item.jev_summary || {};
  if (jev.turning_candidates?.length) flags.push(chip("Jev 转折候选 " + jev.turning_candidates.length, "jev"));
  if (jev.claims_done_unverified?.length) flags.push(chip("Jev 未验证即宣称完成", "jev"));
  if (!ctx.blind && jev.consistency === "overclaim") flags.push(chip("声称完成但评分失败", "bad"));
  const labels = (item.review_labels || []).map((key) => {
    const found = ctx.taxonomy.labels.find((entry) => entry.key === key);
    return chip(found ? found.label : key, "label");
  });
  const role = item.group_role && item.group_role !== "main" ? ROLE_LABELS[item.group_role] + (item.segment && item.group_role === "segment" ? " " + item.segment : "") : "";
  const pick = h("input", {
    type: "checkbox",
    class: "pick",
    title: "加入对比",
    "aria-label": "加入对比",
    checked: ctx.compareSelection.includes(item.id),
    onclick: (event) => {
      event.stopPropagation();
      event.target.checked = ctx.toggleCompare(item.id);
      ctx.onCompareChange?.();
    },
  });
  const kind = item.extra?.subagent_type ? item.extra.subagent_type + (item.extra.description ? "：" + item.extra.description : "") : "";
  return h(
    "a",
    { class: "row" + (nested ? " nested" : ""), href: "#/t/" + encodeURIComponent(item.id), onclick: (event) => { if (event.target === pick) event.preventDefault(); ctx.queueNav = null; } },
    h("span", { class: "row-pick" }, episode?.toggle || pick, nested ? null : h("span", { class: "dot " + (status === "hidden" ? "muted" : outcomeTone(status)), title: status === "hidden" ? "盲判模式" : OUTCOME_LABELS[status] })),
    h(
      "span",
      { class: "cell-title" },
      h("strong", {}, nested ? [role || "主轨迹", kind].filter(Boolean).join(" · ") : item.title || item.run_id),
      h("small", {}, [item.task_id || "", nested ? "" : role, item.run_id].filter(Boolean).join(" · ")),
      episode?.summary ? h("small", { class: episode.warn ? "episode-summary warn" : "episode-summary" }, episode.summary) : null
    ),
    h("span", { class: "cell-meta" }, h("span", {}, item.model || "—"), h("small", {}, (item.harness || item.adapter_id) + (item.duration_ms ? " · " + formatDuration(item.duration_ms) : ""))),
    h("span", { class: "num" }, String(item.steps ?? "—")),
    h("span", { class: "num" + (item.tool_errors ? " bad-text" : "") }, String(item.tool_errors ?? 0)),
    h("span", { class: "chips" }, flags),
    h("span", { class: "chips" }, item.reviewed_at ? [chip("已读", "ok"), ...labels] : chip("未读", "muted"))
  );
}
