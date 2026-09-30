import { api, pollJob } from "../api.js";
import { chip, clear, details, empty, h } from "../dom.js";
import { formatCount, percent } from "../presentation.mjs";

/*
 * A rewrite batch as a whole: the overview (funnel, where the changes are and how often
 * they are wrong, the training gate, export) and the change queue (every change of the
 * batch in one list, riskiest first, judged from the keyboard).
 */

const GATE = { include: ["可进", "ok"], review: ["待看", "warn"], exclude: ["不进", "bad"] };
const STATUS_TONE = { rewritten: "ok", blocked: "bad", unsupported: "bad", spec_mismatch: "bad", error: "bad" };
const KIND_LABELS = { text: "文字", tool: "工具调用", step: "整步" };
const FIELD_LABELS = { thinking: "思考", text: "正文" };
const TOOL_CHANGE = { removed: "删掉的调用", added: "新增的调用", changed: "改了的调用" };
const VERDICT = { correct: ["对", "ok"], error: ["错", "bad"], uncertain: ["待定", "muted"] };

const batchHref = (batchId, suffix = "") => "#/rewrites/" + encodeURIComponent(batchId) + suffix;
const recordHref = (batchId, sampleId, changeId) =>
  batchHref(batchId, "/r/" + encodeURIComponent(sampleId) + (changeId ? "?change=" + encodeURIComponent(changeId) : ""));
const queueHref = (batchId, filters = {}) => batchHref(batchId, "/queue" + (Object.keys(filters).length ? "?" + new URLSearchParams(filters) : ""));

function rate(counts) {
  return counts.judged ? percent(counts.error / counts.judged) + " 错（判 " + counts.judged + "）" : "未判";
}

// ---- overview -----------------------------------------------------------------------

export async function renderOverview(root, ctx, batchId, params, still) {
  clear(root, empty("汇总批次…"));
  const data = await api.rewriteOverview(batchId);
  if (!still()) return;
  const batch = data.batch;
  const f = data.funnel;
  ctx.rewriteNav = { batch: batchId, ids: data.records.filter((record) => record.status !== "—" || record.changes).map((record) => record.sample_id) };
  const meta = batch.annotations?.meta || {};
  const facts = [
    batch.original_collection + " → " + batch.rewritten_collection,
    batch.annotations ? (batch.annotations.adapter === "rewrite-run-dir" ? "改写运行目录" : "注解 JSONL") + (meta.locator ? " · 定位 " + meta.locator : "") + (meta.prompt ? " · prompt " + String(meta.prompt).slice(0, 8) : "") : "没有流水线注解",
    batch.plan_items ? "方案条目 " + batch.plan_items : null,
  ].filter(Boolean);

  const card = (label, value, note, href) =>
    h(href ? "a" : "div", { class: "rb-card" + (href ? " link" : ""), href }, h("span", { class: "rb-card-label" }, label), h("strong", {}, formatCount(value)), note ? h("span", { class: "rb-card-note" }, note) : null);
  const funnel = h(
    "section",
    { class: "rb-funnel" },
    card("记录", f.records, [f.missing_rewritten ? "缺改写 " + f.missing_rewritten : null, f.missing_original ? "缺原始 " + f.missing_original : null].filter(Boolean).join(" · ") || "两边都有"),
    card("改动", f.changes, "流水线记录 " + f.annotated + " · 工具调用 " + f.tool_changes, queueHref(batchId, { status: "all" })),
    card("没记录的文字改动", f.unrecorded, f.explained ? "另有 " + f.explained + " 处随摘要同步" : "流水线没解释的", queueHref(batchId, { status: "all", recorded: "no" })),
    card("带警告", f.warned, "校验器标的", queueHref(batchId)),
    card("残留", f.residue_items, f.residue_records + " 条记录", "#rb-records?residue"),
    card("摘要不同步", f.sync_broken, "条记录"),
    card("已判", f.judged, "错 " + f.errors + " · 漏改 " + f.misses, queueHref(batchId, { status: "judged" })),
    h(
      "div",
      { class: "rb-card gate" },
      h("span", { class: "rb-card-label" }, "入训分流"),
      h("div", { class: "rb-gates" }, Object.entries(GATE).map(([key, [label, tone]]) => h("button", { type: "button", class: "rb-gate " + tone, onclick: () => filterRecords(key) }, h("strong", {}, String(data.decisions[key])), label)))
    )
  );

  // Rows of [name, counts, item]: each links to the queue filtered to it.
  const groupRows = (rows, filterKey, { extra = null, label = (name) => name } = {}) =>
    rows.length
      ? h(
          "div",
          { class: "rb-group-rows" },
          rows.map(([name, counts, item]) =>
            h(
              "div",
              { class: "rb-group-row" },
              h("a", { href: queueHref(batchId, { status: "all", [filterKey]: name }) }, label(name)),
              extra ? extra(name, item) : h("span", {}),
              h("span", { class: "num" }, formatCount(counts.total)),
              h("span", { class: "rb-rate" + (counts.error ? " bad-text" : "") }, rate(counts))
            )
          )
        )
      : h("p", { class: "hint" }, "没有");
  const groupTable = (title, rows, filterKey, options) =>
    h("section", { class: "panel rb-group" }, h("h2", { class: "mini-title" }, title), groupRows(rows, filterKey, options));

  const warningRows = data.groups.warning.map(([name, counts]) => [name, counts]);
  const gateSet = new Set(data.gate_warnings);
  const settings = h(
    "section",
    { class: "panel rb-group" },
    h("h2", { class: "mini-title" }, "按警告 · 哪些警告卡分流"),
    h("p", { class: "hint" }, "勾上的警告出现在没判定的改动上时，记录进“待看”。先在队列里抽判，看错误率再决定。"),
    warningRows.length
      ? h(
          "div",
          { class: "rb-group-rows" },
          warningRows.map(([name, counts]) => {
            const box = h("input", { type: "checkbox", checked: gateSet.has(name) || null, "aria-label": "卡分流：" + name });
            box.addEventListener("change", async () => {
              box.disabled = true;
              const next = new Set(gateSet);
              box.checked ? next.add(name) : next.delete(name);
              try {
                await api.rewriteSettings(batchId, { gate_warnings: [...next] });
                renderOverview(root, ctx, batchId, params, still);
              } catch (error) {
                box.disabled = false;
                alert(error.message);
              }
            });
            return h(
              "div",
              { class: "rb-group-row" },
              h("label", { class: "rb-check" }, box, h("a", { href: queueHref(batchId, { status: "all", warning: name }) }, name)),
              h("span", { class: "muted" }, data.warning_impact[name] ? "未判的在 " + data.warning_impact[name] + " 条记录" : ""),
              h("span", { class: "num" }, formatCount(counts.total)),
              h("span", { class: "rb-rate" + (counts.error ? " bad-text" : "") }, rate(counts))
            );
          })
        )
      : h("p", { class: "hint" }, "流水线没有给警告")
  );

  const errorTypes = ctx.taxonomy?.rewrite_errors || {};
  const errors = Object.entries(data.error_types).sort((a, b) => b[1] - a[1]);
  const recordsBox = h("div", { class: "explore-table rr-table rb-records", id: "rb-records" });
  let recordFilter = params.get("gate") || "";
  function filterRecords(value) {
    recordFilter = recordFilter === value ? "" : value;
    renderRecords();
    recordsBox.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function renderRecords() {
    const rows = data.records.filter((record) => !recordFilter || (recordFilter === "residue" ? record.residue : record.gate === recordFilter));
    clear(
      recordsBox,
      h(
        "div",
        { class: "rb-filter" },
        [["", "全部"], ["include", "可进"], ["review", "待看"], ["exclude", "不进"], ["residue", "有残留"]].map(([value, label]) =>
          h("button", { type: "button", class: "secondary" + (recordFilter === value ? " active" : ""), onclick: () => { recordFilter = value; renderRecords(); } }, label)
        ),
        h("span", { class: "muted" }, rows.length + " 条")
      ),
      h("div", { class: "explore-row head" }, ["样本", "段", "流水线", "步数", "改动", "带警告", "残留", "已判 / 错", "分流"].map((label, index) => h("span", { class: index >= 3 && index <= 7 ? "num" : "" }, label))),
      rows.map((record) =>
        h(
          "a",
          { class: "explore-row" + (record.role !== "main" ? " rr-member" : ""), href: recordHref(batchId, record.sample_id) },
          h("strong", { title: record.sample_id }, record.sample_id),
          h("span", {}, record.segment ? "第 " + record.segment + " 段" : ""),
          record.status !== "—" ? chip(record.status, STATUS_TONE[record.status] || "muted") : h("span", { class: "muted" }, "—"),
          h("span", { class: "num" }, (record.steps?.[0] ?? "—") + " → " + (record.steps?.[1] ?? "—")),
          h("span", { class: "num" }, String(record.changes)),
          h("span", { class: "num" + (record.warned ? " bad-text" : "") }, String(record.warned)),
          h("span", { class: "num" + (record.residue ? " bad-text" : "") }, String(record.residue)),
          h("span", { class: "num" }, record.judged + " / " + record.errors),
          h("span", { class: "rb-gate-cell", title: record.reason }, chip(GATE[record.gate][0], GATE[record.gate][1]), h("span", { class: "muted" }, record.error ? "读取失败" : record.reason))
        )
      )
    );
  }
  if (params.has("residue")) recordFilter = "residue";
  renderRecords();

  const exportStatus = h("p", { class: "hint", role: "status" });
  const exportName = h("input", { type: "text", value: batchId + "-" + new Date().toISOString().slice(0, 10), "aria-label": "导出目录名" });
  const exportButton = h("button", { type: "button", class: "secondary" }, "导出");
  exportButton.addEventListener("click", async () => {
    exportButton.disabled = true;
    exportStatus.textContent = "导出中…";
    try {
      const job = await api.rewriteExport(batchId, exportName.value.trim());
      const done = await pollJob(job.id);
      if (done.status === "failed") throw new Error(done.error);
      const result = done.result;
      exportStatus.textContent = "已写到 " + result.path + "：可进 " + result.accepted_written + " 条，重跑清单 " + result.rerun_chains + " 个 chain";
    } catch (error) {
      exportStatus.textContent = error.message;
    } finally {
      exportButton.disabled = false;
    }
  });

  clear(
    root,
    h(
      "header",
      { class: "page-head" },
      h("div", {}, h("span", { class: "eyebrow" }, h("a", { href: "#/rewrites" }, "改写审阅"), " · 批次总览"), h("h1", {}, batch.name), h("p", { class: "hint" }, facts.join(" · "))),
      h("div", { class: "head-actions" }, h("a", { class: "button-like primary", href: queueHref(batchId) }, "逐处审阅 →"))
    ),
    funnel,
    h(
      "div",
      { class: "rb-groups" },
      groupTable("按改动类别", data.groups.category, "category"),
      settings,
      groupTable("按方案条目（前 25）", data.groups.ref, "ref", { extra: (name, item) => (item ? chip(item.fate || "?", item.fate === "removed" ? "bad" : "muted", { title: item.before || "" }) : h("span", {})) }),
      h(
        "section",
        { class: "panel rb-group" },
        h("h2", { class: "mini-title" }, "判错的类型"),
        errors.length ? h("div", { class: "rb-group-rows" }, errors.map(([key, count]) => h("div", { class: "rb-group-row" }, h("a", { href: queueHref(batchId, { status: "error" }) }, errorTypes[key] || key), h("span", { class: "num" }, String(count))))) : h("p", { class: "hint" }, "还没有判错的改动"),
        h("h2", { class: "mini-title" }, "按改动种类"),
        groupRows(data.groups.kind, "kind", { label: (name) => KIND_LABELS[name] || name }),
        data.groups.source.length ? details("按来源类型", groupRows(data.groups.source, "source")) : null
      )
    ),
    h("section", { class: "panel explore-card" }, h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, "记录"), h("p", {}, "点一条进入逐步审阅（J / K 在记录间切换）"))), recordsBox),
    h(
      "section",
      { class: "panel explore-card rb-export" },
      h("h2", { class: "mini-title" }, "导出"),
      h("p", { class: "hint" }, "写到平台的导出目录：可进记录的改写后原行（逐字节）、要重跑的 chain 清单、全部判定和 manifest。"),
      h("div", { class: "inline" }, exportName, exportButton),
      exportStatus
    )
  );
}

// ---- change queue ---------------------------------------------------------------------

const FILTERS = ["status", "category", "warning", "kind", "source", "recorded", "ref"];

export async function renderQueue(root, ctx, batchId, params, still) {
  const filters = Object.fromEntries(FILTERS.map((key) => [key, params.get(key) || ""]).filter(([, value]) => value));
  if (!filters.status) filters.status = "unjudged";
  clear(root, empty("读取改动…"));
  const first = await api.rewriteChanges(batchId, { ...filters, limit: 40 });
  if (!still()) return;
  const errorTypes = ctx.taxonomy?.rewrite_errors || {};
  const state = { items: first.items, total: first.total, plan: first.plan, fates: first.fates, index: first.items.length ? 0 : -1, awaitingType: false, message: "" };
  const gateWarnings = new Set(first.gate_warnings);
  const list = h("div", { class: "rq-list" });
  const inspector = h("aside", { class: "rr-inspector" });
  const more = h("div", { class: "pager" });
  const counter = h("span", { class: "muted" });

  const select = (name, label, options) => {
    const element = h(
      "select",
      { "aria-label": label, onchange: () => go({ [name]: element.value }) },
      options.map(([value, text]) => h("option", { value }, text))
    );
    element.value = filters[name] || "";
    return element;
  };
  const facet = (name, label) => select(name, label, [["", label + "：全部"], ...first.facets[name].map(([value, count]) => [value, (name === "kind" ? KIND_LABELS[value] || value : value) + "（" + count + "）"])]);
  function go(changes) {
    const next = { ...filters, ...changes };
    Object.keys(next).forEach((key) => !next[key] && delete next[key]);
    ctx.navigate(queueHref(batchId, next));
  }
  const bar = h(
    "div",
    { class: "panel rq-filters" },
    select("status", "判定", [["unjudged", "未判定"], ["judged", "已判定"], ["error", "判错的"], ["uncertain", "待定的"], ["correct", "判对的"], ["all", "全部"]]),
    facet("category", "类别"),
    facet("warning", "警告"),
    facet("kind", "种类"),
    first.facets.source.length ? facet("source", "来源") : null,
    select("recorded", "流水线记录", [["", "流水线记录：全部"], ["yes", "流水线记录的"], ["no", "流水线没记录的"]]),
    filters.ref ? chip("方案条目 " + filters.ref, "info") : null,
    Object.keys(filters).some((key) => key !== "status") ? h("button", { type: "button", class: "link-button", onclick: () => go(Object.fromEntries(FILTERS.map((key) => [key, key === "status" ? filters.status : ""]))) }, "清除筛选") : null,
    counter
  );

  function snippet(text, limit = 420) {
    return text && text.length > limit ? text.slice(0, limit) + "…" : text || "";
  }

  function cardFor(item, index) {
    const edit = item.annotations?.[0];
    const verdict = item.verdict?.verdict;
    const types = [...new Set((item.annotations || []).flatMap((note) => (note.warnings || []).map((w) => String(w).split(":")[0].trim())))];
    const body = item.kind === "text"
      ? h("div", { class: "rq-text" }, item.removed ? h("del", { class: "rr-del" }, snippet(item.removed)) : null, item.added ? h("ins", { class: "rr-ins" }, snippet(item.added)) : null)
      : h("div", { class: "rq-text muted" }, item.kind === "tool" ? (TOOL_CHANGE[item.change] || "调用") + " · " + (item.tool || "") + (item.removed ? " · " + snippet(item.removed, 140) : "") : item.change === "removed" ? "删掉了这一步" : "新增了这一步");
    return h(
      "article",
      { class: "rq-card" + (index === state.index ? " selected" : "") + (verdict ? " judged v-" + verdict : ""), dataset: { index }, onclick: () => focus(index) },
      h(
        "div",
        { class: "rq-card-head" },
        h("span", { class: "rq-where", title: item.sample_id }, item.sample_id.replace(/_attempt_\d+/, "") + " · #" + item.step + " · " + (item.kind === "text" ? FIELD_LABELS[item.field] || item.field : KIND_LABELS[item.kind])),
        chip(item.category, item.annotations?.length ? "info" : "muted"),
        types.map((type) => chip(type, gateWarnings.has(type) ? "bad" : "warn")),
        verdict ? chip(VERDICT[verdict][0] + (item.verdict.error_type ? " · " + (errorTypes[item.verdict.error_type] || item.verdict.error_type) : ""), VERDICT[verdict][1]) : null
      ),
      body,
      edit?.reason ? h("p", { class: "rq-reason" }, snippet(edit.reason, 240)) : item.explained ? h("p", { class: "rq-reason" }, "随上一段摘要同步重建") : null
    );
  }

  function renderList() {
    clear(list, state.items.length ? state.items.map(cardFor) : empty("没有符合筛选的改动"));
    counter.textContent = state.items.length + " / " + state.total + " 处";
    clear(more, state.items.length < state.total ? h("button", { type: "button", onclick: loadMore }, "继续加载（" + state.items.length + " / " + state.total + "）") : null);
  }

  async function loadMore() {
    const page = await api.rewriteChanges(batchId, { ...filters, offset: state.items.length, limit: 40 });
    state.items.push(...page.items);
    Object.assign(state.plan, page.plan);
    renderList();
  }

  function focus(index, scroll = false) {
    if (index < 0 || index >= state.items.length) return;
    state.index = index;
    state.awaitingType = false;
    state.message = "";
    list.querySelectorAll(".rq-card.selected").forEach((card) => card.classList.remove("selected"));
    const card = list.querySelector('[data-index="' + index + '"]');
    card?.classList.add("selected");
    if (scroll) card?.scrollIntoView({ behavior: "smooth", block: "center" });
    renderInspector();
  }

  async function judge(verdict, errorType = null) {
    const item = state.items[state.index];
    if (!item) return;
    const note = inspector.querySelector("textarea")?.value || item.verdict?.note || "";
    if (verdict === "error" && !errorType) {
      state.awaitingType = true;
      renderInspector();
      return;
    }
    if (errorType === "other" && !note.trim()) {
      state.message = "类型选了“其他”，先在备注里写原因";
      state.awaitingType = true;
      renderInspector();
      return;
    }
    try {
      const result = await api.rewriteVerdict(batchId, item.sample_id, { key: item.id, kind: "change", verdict, error_type: errorType, note });
      const fresh = !item.verdict;
      item.verdict = result.verdict;
      const card = list.querySelector('[data-index="' + state.index + '"]');
      card?.replaceWith(cardFor(item, state.index));
      if (fresh) {
        const next = state.items.findIndex((candidate, index) => index > state.index && !candidate.verdict);
        if (next >= 0) return focus(next, true);
      }
      state.awaitingType = false;
      renderInspector();
    } catch (error) {
      state.message = error.message;
      renderInspector();
    }
  }

  function renderInspector() {
    const item = state.items[state.index];
    if (!item) {
      clear(inspector, h("div", { class: "panel inspector-card" }, h("p", { class: "hint" }, "选一处改动；j / k 上下，1 对 · 2 错 · 3 待定")));
      return;
    }
    const current = item.verdict;
    const notes = (item.annotations || []).map((edit) =>
      h(
        "div",
        { class: "rr-note" },
        h("div", { class: "chips" }, h("span", { class: "rr-label" }, "流水线记录"), edit.category ? chip(edit.category, "info") : null, edit.source ? chip("来源 " + edit.source, "muted") : null, edit.target ? chip(edit.target, "muted") : null),
        edit.reason ? h("p", { class: "rr-reason" }, edit.reason) : null,
        edit.warnings?.length ? h("div", { class: "chips" }, edit.warnings.map((warning) => chip(warning, gateWarnings.has(String(warning).split(":")[0].trim()) ? "bad" : "warn"))) : null,
        edit.refs?.length
          ? h(
              "div",
              { class: "rr-refs" },
              h("span", { class: "rr-label" }, "方案条目"),
              edit.refs.map((ref) => {
                const plan = state.plan[ref];
                if (!plan) return h("code", {}, ref);
                return details(
                  h("span", {}, h("code", {}, ref), " ", chip(plan.fate || "?", plan.fate === "removed" ? "bad" : "muted")),
                  h("div", { class: "rr-plan-body" }, plan.before ? h("div", { class: "rr-plan-text" }, plan.before) : null, plan.note ? h("p", { class: "hint" }, plan.note) : null, state.fates[plan.fate] ? h("p", { class: "hint" }, "去向“" + plan.fate + "”：" + state.fates[plan.fate]) : null)
                );
              })
            )
          : null
      )
    );
    const note = h("textarea", { rows: 2, placeholder: "备注（可选；类型选“其他”时必填）" });
    note.value = current?.note || "";
    clear(
      inspector,
      h(
        "div",
        { class: "panel inspector-card rr-panel" },
        h(
          "div",
          { class: "rr-panel-head" },
          h("strong", {}, "第 " + (state.index + 1) + " / " + state.total + " 处"),
          h("a", { class: "link-button", href: recordHref(batchId, item.sample_id, item.id), title: "在整条记录里看（Enter）" }, "在记录里看 →")
        ),
        h("div", { class: "muted" }, item.sample_id + " · #" + item.step),
        item.kind === "text"
          ? h("div", { class: "rr-change-text" }, item.removed ? h("del", { class: "rr-del" }, item.removed) : null, item.added ? h("ins", { class: "rr-ins" }, item.added) : null)
          : h("p", {}, item.kind === "tool" ? (TOOL_CHANGE[item.change] || "改了的调用") + " · " + (item.tool || "") : item.change === "removed" ? "改写删掉了这一步" : "改写新增了这一步"),
        notes.length ? notes : h("p", { class: "hint" }, item.explained ? "随上一段压缩摘要一起重建的续写开头。" : item.kind === "text" ? "流水线没有记录这处改动。" : "结构变化（删调用、改参数等）。"),
        h(
          "div",
          { class: "rr-verdict" },
          h(
            "div",
            { class: "rr-verdict-buttons" },
            [["correct", "对", "1"], ["error", "错", "2"], ["uncertain", "待定", "3"]].map(([value, label, key]) =>
              h("button", { type: "button", class: "rr-verdict-button " + value + (current?.verdict === value ? " active" : ""), onclick: () => judge(value) }, label, h("kbd", {}, key))
            )
          ),
          state.awaitingType || current?.verdict === "error"
            ? h(
                "div",
                { class: "rr-types" },
                h("span", { class: "rr-label" }, "错在哪（按 1–6）"),
                Object.entries(errorTypes).map(([key, label], index) =>
                  h("button", { type: "button", class: "rr-type" + (current?.error_type === key ? " active" : ""), onclick: () => judge("error", key) }, h("kbd", {}, String(index + 1)), label)
                )
              )
            : null,
          note,
          state.message ? h("p", { class: "bad-text rr-message" }, state.message) : null
        )
      )
    );
  }

  function onKey(event) {
    if (!still() || !document.body.contains(list)) {
      document.removeEventListener("keydown", onKey);
      return;
    }
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.target.closest?.("input, textarea, select, [contenteditable]")) return;
    if (state.awaitingType && /^[1-6]$/.test(event.key)) {
      const type = Object.keys(errorTypes)[Number(event.key) - 1];
      if (type) {
        event.preventDefault();
        judge("error", type);
      }
      return;
    }
    const item = state.items[state.index];
    const actions = {
      j: () => (state.index + 1 >= state.items.length && state.items.length < state.total ? loadMore().then(() => focus(state.index + 1, true)) : focus(state.index + 1, true)),
      k: () => focus(state.index - 1, true),
      1: () => judge("correct"),
      2: () => judge("error"),
      3: () => judge("uncertain"),
      Enter: () => item && ctx.navigate(recordHref(batchId, item.sample_id, item.id)),
      Escape: () => { state.awaitingType = false; renderInspector(); },
    };
    const action = actions[event.key];
    if (!action) return;
    event.preventDefault();
    action();
  }
  document.addEventListener("keydown", onKey);

  clear(
    root,
    h(
      "header",
      { class: "page-head" },
      h("div", {}, h("span", { class: "eyebrow" }, h("a", { href: "#/rewrites" }, "改写审阅"), " · ", h("a", { href: batchHref(batchId) }, batchId)), h("h1", {}, "改动队列"), h("p", { class: "hint" }, "所有记录的改动排成一列：先是卡分流的警告，再是其他警告、流水线没记录的改动，最后是其余。j / k 上下，1 对 · 2 错 · 3 待定，Enter 在整条记录里看。")),
      h("div", { class: "head-actions" }, h("a", { class: "link-button", href: batchHref(batchId) }, "← 批次总览"))
    ),
    bar,
    h("div", { class: "rr-grid" }, h("section", { class: "rq-column" }, list, more), inspector)
  );
  renderList();
  renderInspector();
}
