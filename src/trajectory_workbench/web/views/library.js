import { api, pollJob, runningJob } from "../api.js";
import { chip, clear, debounce, empty, h } from "../dom.js";
import { OUTCOME_LABELS, ROLE_LABELS, formatCount, formatDuration, formatReward, outcomeTone } from "../presentation.mjs";
import { t, tb } from "../i18n.mjs";

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
    v4_tier: params.get("v4_tier") || "",
    v4_errors: params.get("v4_errors") || "",
    v4_r_min: params.get("v4_r_min") || "",
    v4_r_max: params.get("v4_r_max") || "",
    sort: params.get("sort") || "imported",
    offset: Number(params.get("offset") || 0),
  };
  const taxonomy = ctx.taxonomy;
  const title = ctx.collection || t("全部集合");

  const status = h("span", { class: "muted" });
  // Its own line, so the episode count written by load() does not overwrite the job's progress.
  const jevStatus = h("span", { class: "muted" });
  const batchButton = h("button", { type: "button", class: "secondary", disabled: !ctx.jevAvailable }, t("对未分析的轨迹跑 Jev"));
  const compareLink = h("span");
  ctx.onCompareChange = () => {
    const selection = ctx.compareSelection;
    // toggleCompare keeps only the last two, so a box other than the clicked one can drop out.
    root.querySelectorAll("input.pick[data-id]").forEach((box) => {
      box.checked = selection.includes(box.dataset.id);
    });
    clear(
      compareLink,
      selection.length === 2
        ? h("a", { class: "button-like primary-link", href: "#/pair/" + selection.map(encodeURIComponent).join("/") }, t("对比所选 2 条 →"))
        : h("span", { class: "muted" }, t("勾选 2 条可对比（已选 {n}）", { n: selection.length }))
    );
  };
  ctx.onCompareChange();
  const header = h(
    "header",
    { class: "page-head" },
    h("div", {}, h("span", { class: "eyebrow" }, t("轨迹库")), h("h1", {}, title)),
    h("div", { class: "head-actions" }, compareLink, status, exportMenu(), jevStatus, batchButton)
  );

  /** Export what the current filters show as training JSONL plus a data card. */
  function exportMenu() {
    const stamp = new Date().toISOString().slice(0, 10);
    const name = h("input", { type: "text", "aria-label": t("导出文件夹名"), value: ((ctx.collection || "all") + "-" + stamp).replace(/[^\w.-]+/g, "_") });
    const mode = h(
      "select",
      { "aria-label": t("导出格式") },
      h("option", { value: "raw" }, t("原始行（字节拷贝，SFT/TraceLab）")),
      h("option", { value: "chat" }, t("转成 OpenAI chat（任何格式）"))
    );
    const note = h("span", { class: "save-status" });
    const start = h("button", { type: "button", class: "primary" }, t("导出"));
    start.addEventListener("click", async () => {
      start.disabled = true;
      note.textContent = t("导出中…");
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
            v4_tier: ctx.blind ? "" : filters.v4_tier,
            v4_errors: ctx.blind ? "" : filters.v4_errors,
            v4_r_min: ctx.blind ? "" : filters.v4_r_min,
            v4_r_max: ctx.blind ? "" : filters.v4_r_max,
            episodes: filters.view === "episode" ? 1 : "",
            roles: filters.view === "episode" || filters.view === "all" ? "" : filters.view,
          },
          name.value.trim(),
          mode.value
        );
        const finished = await pollJob(job.id, (state) => { note.textContent = t("导出中 {done} / {total}", { done: state.done || 0, total: state.total ?? "…" }); });
        if (finished.status === "failed") throw new Error(finished.error);
        note.textContent = finished.result.skipped
          ? t("写出 {written} 条（跳过 {skipped}，见 data card） → {path}", { written: finished.result.written, skipped: finished.result.skipped, path: finished.result.path })
          : t("写出 {written} 条 → {path}", { written: finished.result.written, path: finished.result.path });
      } catch (error) {
        note.textContent = t("导出失败：{error}", { error: error.message });
      } finally {
        start.disabled = false;
      }
    });
    return h(
      "details",
      { class: "export-excerpt" },
      h("summary", { class: "button-like" }, t("导出筛选结果")),
      h(
        "div",
        { class: "export-excerpt-body" },
        h("small", { class: "muted" }, t("按当前筛选导出；按 episode 看时整段 episode（含分段、子代理）一起导出。写到服务器的导出目录下。")),
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
  // Typed filters re-render the view through the URL. The rebuilt input gets back the focus,
  // caret and untrimmed text of the one being typed in (ctx.libraryFocus); nothing runs while
  // an IME composition is open, and a pending change is dropped once the view has been left.
  const typedInputs = {};
  function typed(input, name, wait) {
    let composing = false;
    const fire = debounce(() => {
      if (composing || !still()) return;
      if (document.activeElement === input) {
        let start = null;
        let end = null;
        try {
          start = input.selectionStart;
          end = input.selectionEnd;
        } catch (_) {
          // Number inputs have no caret position.
        }
        ctx.libraryFocus = { name, value: input.value, start, end };
      }
      update({ [name]: input.value.trim(), offset: 0 });
    }, wait);
    input.addEventListener("compositionstart", () => { composing = true; });
    input.addEventListener("compositionend", () => { composing = false; fire(); });
    input.addEventListener("input", (event) => { if (!event.isComposing && !composing) fire(); });
    typedInputs[name] = input;
    return input;
  }
  function restoreFocus() {
    const saved = ctx.libraryFocus;
    ctx.libraryFocus = null;
    const input = saved && typedInputs[saved.name];
    if (!input) return;
    if (saved.value.trim() === input.value) input.value = saved.value;
    input.focus();
    try {
      if (saved.start != null) input.setSelectionRange(saved.start, saved.end);
    } catch (_) {
      // Number inputs have no caret position.
    }
  }
  const search = typed(h("input", { type: "search", placeholder: t("搜索标题 / 任务 / id"), value: filters.search }), "search", 250);
  const controls = h(
    "div",
    { class: "filters panel" },
    search,
    ctx.blind
      ? h("span", { class: "chip muted" }, t("盲判模式：隐藏评分"))
      : select("outcome", [["", t("全部结果")], ...Object.entries(OUTCOME_LABELS)], t("评分结果")),
    select("flag", [["", t("全部信号")], ...Object.entries(taxonomy.flags).map(([key, text]) => [key, tb(text)])], t("规则信号")),
    select("jev_flag", [["", t("全部 Jev 信号")], ...Object.entries(taxonomy.jev_flags || {}).map(([key, text]) => [key, tb(text)])], t("Jev 信号（有一步被标出）")),
    select("label", [["", t("全部标签")], ...taxonomy.labels.map((item) => [item.key, tb(item.label)])], t("人工标签")),
    select("reviewed", [["", t("读过/未读")], ["0", t("未读")], ["1", t("已读")]], t("是否已读")),
    select("ready", [["", t("训练就绪：全部")], ["1", t("可直接训练")], ["0", t("有阻断问题")]], t("训练就绪")),
    select("issue", [["", t("全部就绪问题")], ...Object.entries(taxonomy.readiness_issues || {}).map(([key, text]) => [key, tb(text)])], t("就绪问题")),
    select("view", [["episode", t("按 episode（一行一次尝试）")], ["main,segment", t("主轨迹+分段")], ["subagent", t("只看子代理")], ["all", t("全部轨迹")]], t("显示单位")),
    ctx.blind ? null : select("v4_tier", [["", t("v4 tier：全部")], ["any", t("有 v4 reward")], ["none", t("没有 v4 reward")], ["masked", "masked"], ["4", "tier 4"], ["3", "tier 3"], ["2", "tier 2"], ["1", "tier 1"], ["0", "tier 0"]], "v4 reward tier"),
    ctx.blind ? null : select("v4_errors", [["", t("v4 报错：全部")], ["1", t("有报错（masked / 失败阶段 / deck fault / 重试）")], ["0", t("无报错")]], t("v4 reward 是否有报错")),
    ctx.blind ? null : rangeInput("v4_r_min", "R ≥"),
    ctx.blind ? null : rangeInput("v4_r_max", "R ≤"),
    select("sort", [["imported", t("最近导入")], ["steps", t("步数最多")], ["errors", t("报错最多")], ["task", t("按任务分组")], ["title", t("标题")], ...(ctx.blind ? [] : [["v4_r", t("v4 R 高→低")], ["v4_r_asc", t("v4 R 低→高")], ["v4_tier", t("v4 tier 高→低")], ["v4_seconds", t("v4 打分耗时最长")]])], t("排序"))
  );

  function rangeInput(name, label) {
    return typed(h("input", { type: "number", step: "0.05", class: "range-input", placeholder: label, "aria-label": "v4 " + label, value: filters[name] }), name, 400);
  }

  const table = h("div", { class: "trajectory-table panel" });
  const pager = h("div", { class: "pager" });
  clear(root, header, controls, table, pager);
  restoreFocus();

  // A batch Jev job outlives this view (filters and navigation re-render it), so every render
  // looks for the collection's running job and follows it instead of offering a second one.
  let following = false;
  async function followJev(job) {
    if (following) return;
    following = true;
    batchButton.disabled = true;
    try {
      const finished = await pollJob(job.id, (state) => {
        jevStatus.textContent = t("Jev 分析 {done} / {total}", { done: state.done || 0, total: state.total ?? "…" });
      }, 700, still);
      if (!finished) return; // the view was left; the next render picks the job up again
      if (finished.status === "failed") throw new Error(finished.error);
      jevStatus.textContent = [
        t("Jev 完成 {n} 条 · {tokens} tokens", { n: finished.result.analyzed, tokens: formatCount(finished.result.input_tokens) }),
        finished.result.failed.length ? t("失败 {n}", { n: finished.result.failed.length }) : null,
        finished.result.stopped ? t("连续失败已停止，{n} 条没跑：{error}", { n: finished.result.not_run, error: finished.result.failed[finished.result.failed.length - 1].error }) : null,
      ].filter(Boolean).join(" · ");
      if (still()) load();
    } catch (error) {
      jevStatus.textContent = t("Jev 失败：{error}", { error: error.message });
    } finally {
      following = false;
      batchButton.disabled = !ctx.jevAvailable;
    }
  }
  batchButton.addEventListener("click", async () => {
    batchButton.disabled = true;
    let job;
    try {
      job = await api.batchJev(ctx.collection || null, 200);
    } catch (error) {
      jevStatus.textContent = t("Jev 失败：{error}", { error: error.message });
      batchButton.disabled = !ctx.jevAvailable;
      return;
    }
    followJev(job);
  });
  if (ctx.jevAvailable) {
    runningJob("jev", ctx.collection || "")
      .then((job) => {
        if (job && still()) followJev(job);
      })
      .catch(() => {
        // Only a convenience: without it the button just stays as it is.
      });
  }

  function update(changes, { replace = false } = {}) {
    const next = { ...filters, ...changes };
    const query = new URLSearchParams();
    Object.entries(next).forEach(([key, value]) => {
      if (key === "view" ? value !== "episode" : value !== "" && value !== 0 && value !== "imported") query.set(key, value);
    });
    const hash = "#/library" + (query.toString() ? "?" + query : "");
    // replace: a correction of the current URL, which Back must not return to.
    if (replace && location.hash !== hash) location.replace(hash);
    else ctx.navigate(hash);
  }

  async function load() {
    clear(table, empty(t("读取中…")));
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
      v4_tier: ctx.blind ? "" : filters.v4_tier,
      v4_errors: ctx.blind ? "" : filters.v4_errors,
      v4_r_min: ctx.blind ? "" : filters.v4_r_min,
      v4_r_max: ctx.blind ? "" : filters.v4_r_max,
      sort: ctx.blind && filters.sort.startsWith("v4_") ? "imported" : filters.sort,
      offset: filters.offset,
      limit: PAGE,
    });
    if (!still()) return;
    // The offset can outlive its collection (the sidebar re-runs the same URL): go to the last page.
    if (!payload.items.length && payload.total > 0 && filters.offset >= payload.total) {
      update({ offset: Math.floor((payload.total - 1) / PAGE) * PAGE }, { replace: true });
      return;
    }
    status.textContent = filters.view === "episode" ? t("{n} 个 episode", { n: formatCount(payload.total) }) : t("{n} 条", { n: formatCount(payload.total) });
    if (!payload.items.length) {
      clear(table, empty(ctx.collections.length ? t("没有符合筛选的轨迹") : t("左下角导入一个目录或文件开始")));
    } else {
      clear(
        table,
        h(
          "div",
          { class: "row head" },
          h("span", { title: t("勾选两条进行对比") }, t("比")),
          h("span", {}, t("轨迹")),
          h("span", {}, t("模型 / 来源")),
          h("span", { class: "num" }, t("步")),
          h("span", { class: "num" }, t("工具错")),
          h("span", {}, t("信号")),
          h("span", {}, t("阅读"))
        ),
        payload.items.map((item) => (filters.view === "episode" ? episodeRow(item, ctx) : row(item, ctx)))
      );
    }
    const first = payload.total ? filters.offset + 1 : 0;
    const last = Math.min(payload.total, filters.offset + payload.items.length);
    clear(
      pager,
      h("button", { type: "button", disabled: filters.offset <= 0, onclick: () => update({ offset: Math.max(0, filters.offset - PAGE) }) }, t("上一页")),
      h("span", {}, first + "–" + last + " / " + payload.total),
      h("button", { type: "button", disabled: filters.offset + PAGE >= payload.total, onclick: () => update({ offset: filters.offset + PAGE }) }, t("下一页"))
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
  const toggle = h("button", { type: "button", class: "episode-toggle", "aria-expanded": "false", title: t("展开 episode 成员") }, "▸");
  toggle.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    const open = members.hidden;
    members.hidden = !open;
    toggle.textContent = open ? "▾" : "▸";
    toggle.setAttribute("aria-expanded", String(open));
    if (open && !loaded) {
      loaded = true;
      clear(members, empty(t("读取中…")));
      try {
        const payload = await api.trajectories({ collection: item.collection, group_key: item.group_key, sort: "episode", limit: 500 });
        clear(
          members,
          payload.items.map((member) => row(member, ctx, true)),
          payload.total > payload.items.length ? empty(t("只列出前 {shown} / {total} 条成员", { shown: payload.items.length, total: payload.total })) : null
        );
      } catch (error) {
        clear(members, empty(t("读取失败：{error}", { error: error.message })));
      }
    }
  });
  const parts = [];
  if (episode.threads > 1) parts.push(t("{n} 段", { n: episode.threads }));
  if (episode.missing_segments?.length) parts.push(t("缺第 {list} 段", { list: episode.missing_segments.join(t("、")) }));
  if (episode.subagents) parts.push(t("{n} 个子代理", { n: episode.subagents }));
  if (episode.head_missing) parts.push(t("主轨迹未导入"));
  return h(
    "div",
    { class: "episode" },
    row({ ...item, steps: episode.steps, tool_errors: episode.tool_errors, flags: episode.flags, duration_ms: episode.duration_ms ?? item.duration_ms }, ctx, false, {
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
  const flags = (item.flags || []).map((key) => chip(tb(ctx.taxonomy.flags[key] || key), "flag"));
  (episode?.subagentFlags || []).forEach((key) => flags.push(chip(t("子代理·{flag}", { flag: tb(ctx.taxonomy.flags[key] || key) }), "flag")));
  const blocking = (item.readiness?.issues || []).filter((issue) => issue.severity === "block");
  if (blocking.length) {
    const names = blocking.map((issue) => tb(ctx.taxonomy.readiness_issues?.[issue.key] || issue.key));
    flags.push(chip(t("不可直接训练"), "bad", { title: names.join(t("、")) }));
  }
  const jev = item.jev_summary || {};
  if (jev.turning_candidates?.length) flags.push(chip(t("Jev 转折候选 {n}", { n: jev.turning_candidates.length }), "jev"));
  if (jev.claims_done_unverified?.length) flags.push(chip(t("Jev 未验证即宣称完成"), "jev"));
  if (!ctx.blind && jev.consistency === "overclaim") flags.push(chip(t("声称完成但评分失败"), "bad"));
  const labels = (item.review_labels || []).map((key) => {
    const found = ctx.taxonomy.labels.find((entry) => entry.key === key);
    return chip(found ? tb(found.label) : key, "label");
  });
  const role = item.group_role && item.group_role !== "main" ? ROLE_LABELS[item.group_role] + (item.segment && item.group_role === "segment" ? " " + item.segment : "") : "";
  const pick = h("input", {
    type: "checkbox",
    class: "pick",
    title: t("加入对比"),
    "aria-label": t("加入对比"),
    checked: ctx.compareSelection.includes(item.id),
    dataset: { id: item.id },
    onclick: (event) => {
      event.stopPropagation();
      event.target.checked = ctx.toggleCompare(item.id);
      ctx.onCompareChange?.();
    },
  });
  // The dot is the harness verifier's outcome; on a design task the v4 badge next to it is a
  // different score, so say which one the dot is.
  const dotTitle = status === "hidden"
    ? t("盲判模式")
    : item.v4_label
      ? t("评测脚本结果：{status}（不是 v4 奖励）", { status: OUTCOME_LABELS[status] || status || "—" })
      : OUTCOME_LABELS[status];
  const kind = item.extra?.subagent_type ? (item.extra.description ? t("{type}：{description}", { type: item.extra.subagent_type, description: item.extra.description }) : item.extra.subagent_type) : "";
  return h(
    "a",
    { class: "row" + (nested ? " nested" : ""), href: "#/t/" + encodeURIComponent(item.id), onclick: () => { ctx.queueNav = null; } },
    h("span", { class: "row-pick" }, episode?.toggle || pick, nested ? null : h("span", { class: "dot " + (status === "hidden" ? "muted" : outcomeTone(status)), title: dotTitle })),
    h(
      "span",
      { class: "cell-title" },
      h("strong", {}, nested ? [role || t("主轨迹"), kind].filter(Boolean).join(" · ") : item.title || item.run_id),
      h("small", {}, [item.task_id || "", nested ? "" : role, item.run_id].filter(Boolean).join(" · ")),
      episode?.summary ? h("small", { class: episode.warn ? "episode-summary warn" : "episode-summary" }, episode.summary) : null
    ),
    h("span", { class: "cell-meta" }, h("span", {}, item.model || "—"), h("small", {}, (item.harness || item.adapter_id) + (item.duration_ms ? " · " + formatDuration(item.duration_ms) : "")), ctx.blind ? null : v4Badge(item)),
    h("span", { class: "num" }, String(item.steps ?? "—")),
    h("span", { class: "num" + (item.tool_errors ? " bad-text" : "") }, String(item.tool_errors ?? 0)),
    h("span", { class: "chips" }, flags),
    h("span", { class: "chips" }, item.reviewed_at ? [chip(t("已读"), "ok"), ...labels] : chip(t("未读"), "muted"))
  );
}

/** Compact v4 reward badge: tier and R (or masked), with errors marked. */
function v4Badge(item) {
  if (!item.v4_label) return null;
  const masked = item.v4_masked || item.v4_r === null || item.v4_r === undefined;
  const tone = masked ? "warn" : item.v4_tier >= 3 ? "ok" : item.v4_tier >= 1 ? "info" : "bad";
  const text = "v4 " + (item.v4_tier ?? "—") + " · " + formatReward(item.v4_r, masked) + (item.v4_errors ? " ⚠" : "");
  const title = [
    t("v4 reward（{label}）", { label: item.v4_label }),
    item.v4_tier_name ? "tier " + item.v4_tier + " " + item.v4_tier_name : null,
    item.v4_seconds != null ? t("打分 {seconds}s", { seconds: Number(item.v4_seconds).toFixed(0) }) : null,
    item.v4_errors ? t("有报错 / 重试") : null,
  ].filter(Boolean).join(" · ");
  return chip(text, "v4-badge " + tone, { title });
}
