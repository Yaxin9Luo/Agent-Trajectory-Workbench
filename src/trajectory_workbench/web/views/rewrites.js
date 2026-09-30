import { api, pollJob } from "../api.js";
import { chip, clear, details, empty, h } from "../dom.js";
import { formatCount } from "../presentation.mjs";

/*
 * Rewrite review: an original and a rewritten collection paired by sample id. The server
 * aligns the steps, diffs them word by word and works out the harness delta and residue
 * (any harness, any pipeline); pipeline annotations, when a batch has them, explain changes.
 */

const KIND_LABELS = { mcp: "MCP", tool: "工具", skill: "Skill", subagent: "子代理类型", hook: "Hook", instruction: "指令段落", file: "harness 文件" };
const USE_LABELS = { calls: "调用", files: "参数里", mentions: "提到", outputs: "用到其产出" };
const GATE = { include: ["可进", "ok"], review: ["待看", "warn"], exclude: ["不进", "bad"] };
const FIELD_LABELS = { thinking: "思考", text: "正文" };
const ROW_LABELS = { changed: "改动", merged: "合并", added: "新增", removed: "删除", same: "未改", system_prompt: "系统提示" };
const SECTION_CHANGE = { removed: "删掉", added: "新增", changed: "改了", same: "未改" };
const DECLARATION_CHANGE = { removed: "删掉", added: "新增", redefined: "重新定义", same: "未改" };
const TOOL_CHANGE = { removed: "删掉的调用", added: "新增的调用", changed: "改了的调用" };
const ALIGN_LABELS = { index_map: "按流水线的消息映射对齐", inferred: "按内容推断对齐" };
const STATUS_TONE = { rewritten: "ok", blocked: "bad", unsupported: "bad", spec_mismatch: "bad", error: "bad" };
// Unchanged text kept on each side of a change; the rest folds.
const FOLD_KEEP = 240;

export async function renderRewrites(root, ctx, parts, params, still) {
  if (parts[1] && parts[2] === "r" && parts[3]) return renderRecord(root, ctx, parts[1], parts[3], still);
  if (parts[1]) return renderBatch(root, ctx, parts[1], still);
  return renderBatches(root, ctx, still);
}

function annotationLabel(annotations) {
  const meta = annotations.meta || {};
  const adapter = annotations.adapter === "rewrite-run-dir" ? "改写运行目录" : "注解 JSONL";
  return [adapter, meta.locator ? "定位 " + meta.locator : null, meta.prompt ? "prompt " + String(meta.prompt).slice(0, 8) : null].filter(Boolean).join(" · ");
}

// ---- batches ---------------------------------------------------------------------------

async function renderBatches(root, ctx, still) {
  clear(root, empty("读取改写批次…"));
  const payload = await api.rewriteBatches();
  if (!still()) return;
  const list = payload.batches.length
    ? h(
        "div",
        { class: "explore-table rr-table rr-batches" },
        h("div", { class: "explore-row head" }, h("span", {}, "批次"), h("span", {}, "原始 → 改写后"), h("span", {}, "流水线注解"), h("span", { class: "num" }, "已判定"), h("span", {}, "创建时间")),
        payload.batches.map((batch) =>
          h(
            "a",
            { class: "explore-row", href: "#/rewrites/" + encodeURIComponent(batch.id) },
            h("strong", {}, batch.name),
            h("span", {}, batch.original_collection + " → " + batch.rewritten_collection),
            h("span", { class: "muted" }, batch.annotations ? annotationLabel(batch.annotations) : "无，只看 diff"),
            h("span", { class: "num" }, String(batch.verdicts)),
            h("span", { class: "muted" }, (batch.created_at || "").slice(0, 16).replace("T", " "))
          )
        )
      )
    : empty("还没有改写批次。用右边的表单新建，或在命令行运行 trajectory-workbench rewrite-batch。");
  clear(
    root,
    h("header", { class: "page-head" }, h("div", {}, h("span", { class: "eyebrow" }, "改写审阅"), h("h1", {}, "改写批次"))),
    h("div", { class: "rr-batches-grid" }, h("section", { class: "panel explore-card" }, list), createForm(ctx))
  );
}

function createForm(ctx) {
  const status = h("p", { class: "hint", role: "status" });
  const names = ctx.collections.map((item) => item.collection);
  const select = (name, label) => h("select", { name, "aria-label": label }, h("option", { value: "" }, label), names.map((item) => h("option", { value: item }, item)));
  const form = h(
    "form",
    { class: "panel rr-create" },
    h("h2", { class: "mini-title" }, "新建批次"),
    h("label", {}, "名字", h("input", { type: "text", name: "name", required: true, placeholder: "如 pilot_skills_v2", autocomplete: "off" })),
    h("label", {}, "原始集合", select("original", "选原始集合")),
    h("label", {}, "改写后集合（已导入）", select("rewritten", "选改写后集合")),
    h("label", {}, "或者：改写后轨迹的路径（先导入）", h("input", { type: "text", name: "rewritten_path", placeholder: "/abs/path/rewritten.jsonl" })),
    h("label", {}, "流水线注解（可选）", h("input", { type: "text", name: "annotations", placeholder: "注解 .jsonl 或改写运行目录" })),
    h("button", { type: "submit" }, "建批次"),
    status,
    h("p", { class: "hint" }, "两个集合按样本 id 配对。没有注解也能用：diff、结构变化、harness 增减和残留都由平台算。注解只读。")
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fields = Object.fromEntries([...new FormData(form).entries()].map(([key, value]) => [key, String(value).trim()]).filter(([, value]) => value));
    if (!fields.rewritten && !fields.rewritten_path) {
      status.textContent = "选一个改写后集合，或填改写后轨迹的路径";
      return;
    }
    const submit = form.querySelector("button[type=submit]");
    submit.disabled = true;
    status.textContent = "建批次…";
    try {
      const job = await api.createRewriteBatch(fields);
      const finished = await pollJob(job.id, (state) => {
        status.textContent = state.message ? "导入 " + (state.done || 0) + " 条 · " + state.message : "建批次…";
      });
      if (finished.status === "failed") throw new Error(finished.error);
      if (fields.rewritten_path) await ctx.refreshCollections();
      ctx.navigate("#/rewrites/" + encodeURIComponent(finished.result.batch.id));
    } catch (error) {
      status.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });
  return form;
}

async function renderBatch(root, ctx, batchId, still) {
  clear(root, empty("读取批次…"));
  const payload = await api.rewriteBatch(batchId);
  if (!still()) return;
  const batch = payload.batch;
  ctx.rewriteNav = { batch: batchId, ids: payload.records.filter((record) => record.rewritten).map((record) => record.sample_id) };
  const meta = batch.annotations?.meta || {};
  const report = meta.report || {};
  const facts = [
    batch.original_collection + " → " + batch.rewritten_collection,
    batch.annotations ? annotationLabel(batch.annotations) : "没有流水线注解",
    batch.plan_items ? "方案条目 " + batch.plan_items : null,
    report.edits != null ? "流水线改动 " + formatCount(report.edits) : null,
  ].filter(Boolean);
  const paired = payload.records.filter((record) => record.original && record.rewritten).length;
  const rows = payload.records.map((record) => {
    const href = record.rewritten || record.original ? "#/rewrites/" + encodeURIComponent(batchId) + "/r/" + encodeURIComponent(record.sample_id) : null;
    return h(
      href ? "a" : "div",
      { class: "explore-row" + (record.role !== "main" ? " rr-member" : ""), href },
      h("strong", {}, record.sample_id),
      h("span", {}, record.segment ? "第 " + record.segment + " 段" : record.role === "subagent" ? "子代理" : ""),
      record.status ? chip(record.status, STATUS_TONE[record.status] || "muted") : h("span", { class: "muted" }, "—"),
      h("span", { class: "num" }, (record.original ? record.original.steps : "—") + " → " + (record.rewritten ? record.rewritten.steps : "—")),
      h("span", { class: "num" }, record.changes == null ? "—" : String(record.changes)),
      h("span", { class: "num" + (record.warned ? " bad-text" : "") }, record.warned == null ? "—" : String(record.warned)),
      h("span", { class: "num" }, String(record.verdicts || ""))
    );
  });
  clear(
    root,
    h(
      "header",
      { class: "page-head" },
      h("div", {}, h("span", { class: "eyebrow" }, "改写审阅 · 批次"), h("h1", {}, batch.name), h("p", { class: "hint" }, facts.join(" · "))),
      h("div", { class: "head-actions" }, h("a", { class: "link-button", href: "#/rewrites" }, "← 全部批次"))
    ),
    h(
      "section",
      { class: "panel explore-card" },
      h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, payload.records.length + " 条记录"), h("p", {}, paired + " 条两边都有；点一条进入逐处审阅（J / K 在记录间切换）"))),
      h(
        "div",
        { class: "explore-table rr-table rr-records" },
        h("div", { class: "explore-row head" }, h("span", {}, "样本"), h("span", {}, "段"), h("span", {}, "流水线状态"), h("span", { class: "num" }, "步数"), h("span", { class: "num" }, "流水线改动"), h("span", { class: "num" }, "带警告"), h("span", { class: "num" }, "已判定")),
        rows
      )
    )
  );
}

// ---- one record ----------------------------------------------------------------------

async function renderRecord(root, ctx, batchId, sampleId, still) {
  clear(root, empty("对齐并比较…"));
  const [record, listing] = await Promise.all([
    api.rewriteRecord(batchId, sampleId),
    ctx.rewriteNav?.batch === batchId ? Promise.resolve(null) : api.rewriteBatch(batchId).catch(() => null),
  ]);
  if (!still()) return;
  if (listing) ctx.rewriteNav = { batch: batchId, ids: listing.records.filter((item) => item.rewritten).map((item) => item.sample_id) };
  const errorTypes = ctx.taxonomy?.rewrite_errors || {};
  const missTypes = ctx.taxonomy?.rewrite_misses || {};
  const gateWarnings = new Set(record.batch.settings?.gate_warnings || []);

  const state = {
    verdicts: { ...record.verdicts },
    gate: record.gate,
    selected: null, // change id
    pendingMiss: null, // {step, field, quote}
    awaitingType: false,
    training: false,
    expandAll: false,
    openRuns: new Set(),
    split: new Set(),
    message: "",
  };
  const changeById = new Map(record.changes.map((change) => [change.id, change]));
  const order = record.changes.map((change) => change.id);
  const residueByKey = new Map(record.residue.map((item) => [item.key, item]));
  const nodes = new Map(); // change id → elements
  const rowHome = new Map(); // row index → element that shows it

  const head = h("header", { class: "rr-head panel" });
  const map = h("div", { class: "rr-map", role: "list", "aria-label": "改写迷你地图" });
  const main = h("section", { class: "rr-main" });
  const inspector = h("aside", { class: "rr-inspector" });
  const missButton = h("button", { type: "button", class: "rr-miss-button hidden" }, "标为漏改");
  const shortcuts = h(
    "details",
    { class: "shortcuts" },
    h("summary", {}, "快捷键"),
    h(
      "dl",
      {},
      [
        ["j / k", "下一处 / 上一处改动"],
        ["1 / 2 / 3", "对 / 错 / 待定（错之后按 1–6 选类型）"],
        ["J / K", "下一条 / 上一条记录"],
        ["t", "训练视角：只看改写后的正文"],
        ["o", "当前改动所在步左右并排"],
        ["e", "展开 / 折叠全部"],
        ["Esc", "回到本条概况"],
      ].map(([key, text]) => h("div", {}, h("dt", {}, h("kbd", {}, key)), h("dd", {}, text)))
    )
  );
  clear(
    root,
    head,
    h("section", { class: "panel rr-map-panel" }, map, mapLegend()),
    h("div", { class: "rr-grid" }, main, inspector),
    missButton
  );

  // ---- header ------------------------------------------------------------------------

  function counts() {
    const judged = record.changes.filter((change) => state.verdicts[change.id]).length;
    const errors = record.changes.filter((change) => state.verdicts[change.id]?.verdict === "error").length;
    const misses = Object.values(state.verdicts).filter((item) => item.kind === "miss").length;
    return { judged, errors, misses };
  }

  function renderHead() {
    const { judged, errors, misses } = counts();
    const nav = ctx.rewriteNav?.batch === batchId ? ctx.rewriteNav.ids : [];
    const position = nav.indexOf(sampleId);
    const [gateLabel, gateTone] = GATE[state.gate.decision];
    const recorded = record.changes.filter((change) => change.annotations?.length).length;
    const facts = [
      record.original ? "原始 " + record.original.steps + " 步" : "原始集合里没有",
      record.rewritten ? "改写后 " + record.rewritten.steps + " 步" : "改写后集合里没有",
      record.aligned_by ? ALIGN_LABELS[record.aligned_by] : null,
      record.changes.length + " 处改动" + (record.batch.annotations ? "（流水线记录 " + recorded + "）" : ""),
    ].filter(Boolean);
    clear(
      head,
      h(
        "div",
        { class: "rr-identity" },
        h("span", { class: "eyebrow" }, h("a", { href: "#/rewrites/" + encodeURIComponent(batchId) }, "改写审阅 · " + record.batch.name)),
        h("h1", {}, sampleId),
        h(
          "div",
          { class: "rr-facts" },
          facts.join(" · "),
          record.status ? chip("流水线：" + record.status.value, STATUS_TONE[record.status.value] || "muted") : null,
          record.original ? h("a", { class: "link-button", href: "#/t/" + encodeURIComponent(record.original.id) }, "原始轨迹") : null,
          record.rewritten ? h("a", { class: "link-button", href: "#/t/" + encodeURIComponent(record.rewritten.id) }, "改写后轨迹") : null
        ),
        record.segments?.length > 1
          ? h(
              "div",
              { class: "rr-segments" },
              h("span", { class: "muted" }, "同一 episode："),
              record.segments.map((segment) =>
                h(
                  "a",
                  { class: "rr-segment" + (segment.current ? " current" : ""), href: "#/rewrites/" + encodeURIComponent(batchId) + "/r/" + encodeURIComponent(segment.sample_id) },
                  segment.segment ? "第 " + segment.segment + " 段" : segment.role === "main" ? "主轨迹" : segment.sample_id
                )
              )
            )
          : null
      ),
      h(
        "div",
        { class: "rr-side-head" },
        h("div", { class: "rr-gate " + gateTone, title: state.gate.reasons.map((reason) => reason.text).join("\n") }, h("strong", {}, gateLabel), h("span", {}, state.gate.reasons[0]?.text || "没有待处理的问题")),
        h("div", { class: "rr-progress" }, "已判 " + judged + " / " + record.changes.length + (errors ? " · 错 " + errors : "") + (misses ? " · 漏改 " + misses : "")),
        h(
          "div",
          { class: "inline" },
          h("button", { type: "button", class: "secondary", "aria-pressed": String(state.training), title: "只看改写后的正文（t）", onclick: toggleTraining }, "训练视角"),
          h("button", { type: "button", class: "secondary", disabled: position <= 0, title: "上一条记录（K）", onclick: () => gotoRecord(-1) }, "←"),
          h("span", { class: "muted" }, position >= 0 ? position + 1 + " / " + nav.length : ""),
          h("button", { type: "button", class: "secondary", disabled: position < 0 || position >= nav.length - 1, title: "下一条记录（J）", onclick: () => gotoRecord(1) }, "→"),
          shortcuts
        )
      )
    );
  }

  // ---- minimap -----------------------------------------------------------------------

  function rowState(row) {
    const ids = row.changes || [];
    if (!ids.length) return "";
    const verdicts = ids.map((id) => state.verdicts[id]?.verdict);
    if (verdicts.includes("error")) return "v-error";
    if (verdicts.every(Boolean)) return "v-done";
    return verdicts.some(Boolean) ? "v-partial" : "";
  }

  function renderMap() {
    clear(
      map,
      record.rows.map((row, index) =>
        h("button", {
          type: "button",
          class: ["rr-cell", "k-" + (row.system_prompt ? "system" : row.kind), row.residue?.length && "residue", rowState(row)].filter(Boolean).join(" "),
          title: "#" + (row.after?.step ?? row.before[0]?.step) + " · " + (row.system_prompt ? "系统提示" : ROW_LABELS[row.kind]) + (row.residue?.length ? " · 残留" : ""),
          onclick: () => showRow(index),
        })
      )
    );
  }

  function mapLegend() {
    return h(
      "div",
      { class: "rr-legend" },
      [["changed", "改动"], ["merged", "合并"], ["removed", "删除"], ["added", "新增"], ["same", "未改"]].map(([key, label]) => h("span", { class: "legend k-" + key }, label)),
      h("span", { class: "legend residue" }, "残留"),
      h("span", { class: "legend v-done" }, "已判"),
      h("span", { class: "legend v-error" }, "有错")
    );
  }

  // ---- harness delta -----------------------------------------------------------------

  function componentChips(items, tone, limit = 14) {
    const chips = items.map((item) => chip(KIND_LABELS[item.kind] + " · " + item.name, tone, { title: item.name }));
    if (chips.length <= limit) return h("div", { class: "chips" }, chips);
    const rest = h("div", { class: "chips hidden" }, chips.slice(limit));
    return h(
      "div",
      {},
      h("div", { class: "chips" }, chips.slice(0, limit), h("button", { type: "button", class: "link-button", onclick: (event) => { rest.classList.remove("hidden"); event.target.remove(); } }, "还有 " + (chips.length - limit) + " 个")),
      rest
    );
  }

  function harnessCard() {
    const delta = record.harness;
    if (!delta) return h("section", { class: "panel rr-harness" }, h("strong", {}, "harness 增减"), h("p", { class: "hint" }, "原始集合里没有这条样本，无法比较。"));
    const sections = delta.sections.filter((section) => section.change !== "same");
    const tools = delta.tools.filter((tool) => tool.change !== "same");
    const summary = [
      "组件：删掉 " + delta.removed.length + " · 新增 " + delta.added.length + " · 保留 " + delta.kept.length,
      "系统提示：" + (sections.length ? sections.map((section) => SECTION_CHANGE[section.change] + "「" + (section.heading || "开头") + "」").slice(0, 3).join("、") + (sections.length > 3 ? " 等 " + sections.length + " 段" : "") : "没变"),
      "工具声明：" + (tools.length ? tools.map((tool) => DECLARATION_CHANGE[tool.change] + " " + tool.name.split("__").pop()).slice(0, 3).join("、") + (tools.length > 3 ? " 等 " + tools.length + " 个" : "") : "没变"),
    ];
    const sectionRows = delta.sections.map((section) => {
      const body = h("div", { class: "rr-section-diff hidden" });
      const toggle = section.diff
        ? h("button", { type: "button", class: "link-button", onclick: () => { if (!body.childNodes.length) body.append(renderSegments(section.diff, { selectable: false })); body.classList.toggle("hidden"); } }, "看内容")
        : null;
      return h(
        "div",
        { class: "rr-section" },
        h("div", { class: "rr-section-row" }, chip(SECTION_CHANGE[section.change], section.change === "removed" ? "bad" : section.change === "added" ? "ok" : section.change === "changed" ? "warn" : "muted"), h("span", {}, section.heading || "（开头）"), section.stock ? chip("原生", "muted") : chip("harness 加的", "layer"), h("span", { class: "muted" }, formatCount(section.before_chars) + " → " + formatCount(section.after_chars) + " 字"), toggle),
        body
      );
    });
    const toolRows = tools.map((tool) => {
      const body = tool.diff ? details("描述的改动", renderSegments(tool.diff, { selectable: false })) : null;
      return h("div", { class: "rr-section" }, h("div", { class: "rr-section-row" }, chip(DECLARATION_CHANGE[tool.change], tool.change === "removed" ? "bad" : tool.change === "added" ? "ok" : "warn"), h("code", {}, tool.name), tool.native ? chip("原生", "muted") : null), body);
    });
    return h(
      "section",
      { class: "panel rr-harness" },
      h("div", { class: "rr-harness-head" }, h("strong", {}, "harness 增减"), h("span", { class: "muted" }, summary.join("　"))),
      details(
        "展开",
        h(
          "div",
          { class: "rr-harness-body" },
          h("div", { class: "rr-harness-group" }, h("span", { class: "rr-label" }, "删掉的组件（改写后仍用到就是残留）"), delta.removed.length ? componentChips(delta.removed, "bad") : h("span", { class: "muted" }, "无")),
          h("div", { class: "rr-harness-group" }, h("span", { class: "rr-label" }, "新增的组件"), delta.added.length ? componentChips(delta.added, "ok") : h("span", { class: "muted" }, "无")),
          h("div", { class: "rr-harness-group" }, h("span", { class: "rr-label" }, "改写后仍超出原生 agent 的组件（只作提示）"), delta.kept.length + delta.added.length ? componentChips([...delta.kept, ...delta.added], "muted") : h("span", { class: "muted" }, "无，改写后就是原生 agent")),
          h("div", { class: "rr-harness-group" }, h("span", { class: "rr-label" }, "系统提示（按一级标题）"), sectionRows),
          tools.length ? h("div", { class: "rr-harness-group" }, h("span", { class: "rr-label" }, "工具声明"), toolRows) : null
        )
      )
    );
  }

  // ---- rows --------------------------------------------------------------------------

  function register(id, node) {
    if (!nodes.has(id)) nodes.set(id, []);
    nodes.get(id).push(node);
  }

  function renderRows() {
    nodes.clear();
    rowHome.clear();
    if (!record.rows.length) {
      clear(
        main,
        h(
          "section",
          { class: "panel rr-empty" },
          h("strong", {}, record.rewritten ? "没有可比较的步骤" : "改写后集合里没有这条样本"),
          record.status?.reason ? h("p", { class: "hint" }, "流水线：" + record.status.reason) : null,
          record.original ? h("a", { class: "link-button", href: "#/t/" + encodeURIComponent(record.original.id) }, "看原始轨迹 →") : null
        )
      );
      return;
    }
    clear(main, harnessCard());
    let run = [];
    const flush = () => {
      if (run.length) main.append(sameRun(run));
      run = [];
    };
    record.rows.forEach((row, index) => {
      if (row.system_prompt) return;
      // Unchanged steps fold away, unless they still use a removed component.
      if (row.kind === "same" && !row.residue?.length) {
        run.push(index);
        return;
      }
      flush();
      main.append(renderRow(row, index));
    });
    flush();
    markChanges();
  }

  function stepLabel(row) {
    return row.after ? "#" + row.after.step : "原 #" + row.before.map((item) => item.step).join(" #");
  }

  function roleLabel(ref) {
    if (!ref) return "";
    if (ref.role === "tool" || ref.role === "assistant") return "模型";
    return ref.layer === "hook" ? "Hook" : ref.role === "user" ? "用户" : "系统";
  }

  function sameRun(indices) {
    const box = h("div", { class: "rr-run" });
    const steps = indices.map((index) => record.rows[index].after.step);
    const label = "· · · " + indices.length + " 步未改（#" + steps[0] + (steps.length > 1 ? "–#" + steps[steps.length - 1] : "") + "）· · ·";
    const close = () => {
      state.openRuns.delete(indices[0]);
      clear(box, h("button", { type: "button", class: "rr-run-toggle", onclick: open }, label));
    };
    const open = () => {
      state.openRuns.add(indices[0]);
      clear(box, h("button", { type: "button", class: "rr-run-toggle open", onclick: close }, "收起这 " + indices.length + " 步"), indices.map((index) => previewLine(index)));
    };
    if (state.openRuns.has(indices[0]) || state.expandAll) open();
    else close();
    indices.forEach((index) => rowHome.set(index, { open, first: indices[0] }));
    return box;
  }

  function previewLine(index) {
    const row = record.rows[index];
    const full = h("div", { class: "rr-preview-full" });
    const line = h(
      "div",
      { class: "rr-preview", id: "rr-row-" + index, dataset: { row: index } },
      h(
        "div",
        { class: "rr-preview-head" },
        h("span", { class: "step-no" }, stepLabel(row)),
        h("span", { class: "rr-role" }, roleLabel(row.after)),
        row.residue?.length ? chip("残留", "residue") : null,
        row.targets?.length ? chip("流水线审阅后保留", "muted", { title: "流水线审阅过这一步，没有改" }) : null,
        h("span", { class: "rr-preview-text" }, row.preview || ""),
        h("button", { type: "button", class: "link-button", onclick: () => loadFull(row, full) }, "全文")
      ),
      full
    );
    if (row.residue?.length) markResidue(line, row.residue);
    return line;
  }

  async function loadFull(row, slot) {
    if (slot.childNodes.length) {
      clear(slot);
      return;
    }
    clear(slot, h("span", { class: "muted" }, "读取…"));
    try {
      const page = await api.messages(record.rewritten.id, { steps: String(row.after.step), limit: 5 });
      const message = page.items[0];
      clear(
        slot,
        message.thinking ? h("div", { class: "rr-field", dataset: { field: "thinking" } }, h("span", { class: "rr-label" }, "思考"), h("div", { class: "rr-text" }, message.thinking)) : null,
        message.text ? h("div", { class: "rr-field", dataset: { field: "text" } }, h("span", { class: "rr-label" }, "正文"), h("div", { class: "rr-text" }, message.text)) : null,
        message.tools.map((tool) => h("div", { class: "rr-tool" }, h("span", { class: "rr-tool-name" }, tool.name), h("span", { class: "rr-tool-preview" }, inputPreview(tool.input)))),
        row.targets?.length ? targetsCard(row.targets) : null
      );
      if (row.residue?.length) markResidue(slot, row.residue);
    } catch (error) {
      clear(slot, h("span", { class: "bad-text" }, error.message));
    }
  }

  function renderRow(row, index) {
    const article = h("article", { class: "rr-row kind-" + row.kind + (row.residue?.length ? " has-residue" : ""), id: "rr-row-" + index, dataset: { row: index } });
    const badges = [];
    if (row.kind === "merged") badges.push(chip("合并自原 #" + row.before.map((item) => item.step).join(" #"), "merged"));
    if (row.kind === "removed") badges.push(chip("改写删掉了这一步", "bad"));
    if (row.kind === "added") badges.push(chip("改写新增的一步", "ok"));
    if (row.kind === "same") badges.push(chip("未改", "muted"));
    if (row.residue?.length) badges.push(chip("残留 " + row.residue.length, "residue"));
    if (row.targets?.length) badges.push(chip("流水线审阅过", "muted"));
    record.syncs
      .filter((sync) => sync.direction !== "pipeline" && ((sync.direction === "next" && sync.up_step === row.after?.step) || (sync.direction === "previous" && sync.down_step === row.after?.step)))
      .forEach((sync) => badges.push(chip(syncLabel(sync), sync.ok === false ? "bad" : sync.ok ? "ok" : "muted")));
    article.append(
      h(
        "div",
        { class: "rr-row-head" },
        h("span", { class: "step-no" }, stepLabel(row)),
        h("span", { class: "rr-role" }, roleLabel(row.after || row.before[0])),
        badges,
        row.after && row.before.length && row.kind !== "same" ? h("button", { type: "button", class: "link-button rr-split-toggle", title: "原文和改写左右并排（o）", onclick: () => toggleSplit(index) }, state.split.has(index) ? "收起并排" : "并排") : null
      )
    );
    if (state.split.has(index)) article.append(splitView(row));
    // Residue can sit anywhere in the text: do not fold it away.
    else Object.entries(row.fields || {}).forEach(([field, segments]) => article.append(fieldBlock(field, segments, !row.residue?.length)));
    (row.tools || []).forEach((tool) => article.append(toolLine(tool)));
    if (row.residue?.length) markResidue(article, row.residue);
    return article;
  }

  function syncLabel(sync) {
    const other = sync.direction === "next" ? "下一段" : "上一段";
    if (!sync.paired) return "⇄ " + other + "（原文不是同一份摘要）";
    return "⇄ 与" + other + "摘要" + (sync.ok ? "同步" : "不同步");
  }

  function fieldBlock(field, segments, fold = true) {
    return h("div", { class: "rr-field", dataset: { field } }, h("span", { class: "rr-label" }, FIELD_LABELS[field] || field), renderSegments(segments, { fold }));
  }

  function renderSegments(segments, { selectable = true, fold = true } = {}) {
    const box = h("div", { class: "rr-text" });
    const anyChange = segments.some((segment) => segment[0] !== "=");
    segments.forEach((segment, index) => {
      const [op, text, id] = segment;
      if (op === "=") {
        box.append(fold ? foldable(text, index === 0, index === segments.length - 1, !anyChange) : text);
        return;
      }
      const node = h(op === "-" ? "del" : "ins", { class: op === "-" ? "rr-del" : "rr-ins" }, text);
      if (selectable && id) {
        node.dataset.change = id;
        node.addEventListener("click", (event) => {
          if (window.getSelection()?.toString()) return;
          event.stopPropagation();
          select(id);
        });
        register(id, node);
      }
      box.append(node);
    });
    return box;
  }

  function foldable(text, first, last, whole) {
    if (state.expandAll || text.length <= FOLD_KEEP * 2 + 160) return text;
    let head = "";
    let tail = "";
    if (whole) head = text.slice(0, 200);
    else if (first) tail = text.slice(-FOLD_KEEP);
    else if (last) head = text.slice(0, FOLD_KEEP);
    else {
      head = text.slice(0, FOLD_KEEP);
      tail = text.slice(-FOLD_KEEP);
    }
    const hidden = text.length - head.length - tail.length;
    const span = h("span", {});
    const button = h(
      "button",
      { type: "button", class: "rr-fold", onclick: (event) => { event.stopPropagation(); span.replaceWith(document.createTextNode(text)); } },
      "… 展开 " + formatCount(hidden) + " 字 …"
    );
    span.append(head, button, tail);
    return span;
  }

  function inputPreview(value) {
    const text = typeof value === "string" ? value : JSON.stringify(value ?? "");
    return text.replace(/\s+/g, " ").slice(0, 140);
  }

  function toolLine(tool) {
    if (tool.change === "same") return h("div", { class: "rr-tool" }, h("span", { class: "rr-tool-name" }, tool.name), h("span", { class: "rr-tool-preview" }, tool.preview ? tool.preview.replace(/\s+/g, " ") : ""));
    const bar = h(
      "div",
      { class: "rr-event " + tool.change, dataset: { change: tool.change_id }, onclick: () => select(tool.change_id) },
      h("strong", {}, TOOL_CHANGE[tool.change]),
      h("span", {}, tool.name + (tool.from_step ? "（原 #" + tool.from_step + "）" : "")),
      h("span", { class: "rr-event-preview" }, tool.change === "changed" ? [tool.input ? "参数变了" : null, tool.result ? "返回变了" : null].filter(Boolean).join(" · ") : inputPreview(tool.input))
    );
    register(tool.change_id, bar);
    const more = tool.change === "changed"
      ? [tool.input ? details("参数的改动", renderSegments(tool.input, { selectable: false })) : null, tool.result ? details("返回的改动", renderSegments(tool.result, { selectable: false })) : null]
      : [details("参数", tool.input || ""), tool.result ? details("返回", tool.result) : null];
    return h("div", { class: "rr-event-box" }, bar, more);
  }

  function splitView(row) {
    const side = (op) =>
      Object.entries(row.fields || {}).map(([field, segments]) =>
        h(
          "div",
          { class: "rr-field", dataset: { field } },
          h("span", { class: "rr-label" }, FIELD_LABELS[field] || field),
          h("div", { class: "rr-text" }, segments.filter((segment) => segment[0] === "=" || segment[0] === op).map((segment) => (segment[0] === "=" ? segment[1] : h(op === "-" ? "del" : "ins", { class: op === "-" ? "rr-del" : "rr-ins" }, segment[1]))))
        )
      );
    return h(
      "div",
      { class: "rr-split" },
      h("div", {}, h("div", { class: "rr-split-title" }, "原文" + (row.before.length > 1 ? "（" + row.before.length + " 步拼接）" : "")), side("-")),
      h("div", {}, h("div", { class: "rr-split-title" }, "改写后"), side("+"))
    );
  }

  function markResidue(element, keys) {
    const terms = [...new Set(keys.flatMap((key) => residueByKey.get(key)?.terms || []))].sort((a, b) => b.length - a.length);
    if (!terms.length) return;
    const pattern = new RegExp(terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "g");
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    const found = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (node.parentElement?.closest("del, button, .rr-row-head, .rr-preview-head .step-no")) continue;
      pattern.lastIndex = 0;
      if (pattern.test(node.nodeValue)) found.push(node);
    }
    found.forEach((node) => {
      const text = node.nodeValue;
      const parts = [];
      let last = 0;
      pattern.lastIndex = 0;
      for (const match of text.matchAll(pattern)) {
        parts.push(text.slice(last, match.index), h("mark", { class: "rr-residue", title: "残留：改写后的 harness 里已经没有这个组件" }, match[0]));
        last = match.index + match[0].length;
      }
      parts.push(text.slice(last));
      node.replaceWith(...parts.filter((part) => part !== ""));
    });
  }

  function markChanges() {
    nodes.forEach((list, id) =>
      list.forEach((node) => {
        const verdict = state.verdicts[id]?.verdict;
        node.classList.toggle("selected", id === state.selected);
        node.classList.toggle("v-correct", verdict === "correct");
        node.classList.toggle("v-error", verdict === "error");
        node.classList.toggle("v-uncertain", verdict === "uncertain");
      })
    );
  }

  // ---- selection & navigation --------------------------------------------------------

  function select(id, { scroll = true } = {}) {
    state.selected = id;
    state.pendingMiss = null;
    state.awaitingType = false;
    state.message = "";
    markChanges();
    renderInspector();
    if (scroll) nodes.get(id)?.[0]?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function step(direction) {
    if (!order.length) return;
    const current = order.indexOf(state.selected);
    const next = current < 0 ? (direction > 0 ? 0 : order.length - 1) : Math.min(order.length - 1, Math.max(0, current + direction));
    select(order[next]);
  }

  function showRow(index) {
    const home = rowHome.get(index);
    if (home && !state.openRuns.has(home.first)) home.open();
    const target = document.getElementById("rr-row-" + index);
    if (!target) {
      main.scrollIntoView({ behavior: "smooth" });
      return;
    }
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.remove("flash");
    void target.offsetWidth;
    target.classList.add("flash");
    const first = record.rows[index].changes?.[0];
    if (first) select(first, { scroll: false });
  }

  function gotoRecord(direction) {
    const ids = ctx.rewriteNav?.batch === batchId ? ctx.rewriteNav.ids : [];
    const next = ids[ids.indexOf(sampleId) + direction];
    if (next) ctx.navigate("#/rewrites/" + encodeURIComponent(batchId) + "/r/" + encodeURIComponent(next));
  }

  function toggleTraining() {
    state.training = !state.training;
    main.classList.toggle("training", state.training);
    renderHead();
  }

  function toggleSplit(index) {
    state.split.has(index) ? state.split.delete(index) : state.split.add(index);
    renderRows();
    document.getElementById("rr-row-" + index)?.scrollIntoView({ block: "center" });
  }

  function toggleExpandAll() {
    state.expandAll = !state.expandAll;
    if (!state.expandAll) state.openRuns.clear();
    renderRows();
  }

  // ---- verdicts ------------------------------------------------------------------------

  async function save(fields, { advance = false } = {}) {
    try {
      const result = await api.rewriteVerdict(batchId, sampleId, fields);
      if (result.verdict) state.verdicts[result.verdict.key] = result.verdict;
      if (result.deleted) delete state.verdicts[result.deleted];
      state.gate = result.gate;
      state.awaitingType = false;
      state.pendingMiss = null;
      state.message = "";
      markChanges();
      renderMap();
      renderHead();
      if (advance && fields.kind === "change") {
        const next = order.slice(order.indexOf(fields.key) + 1).find((id) => !state.verdicts[id]);
        if (next) return select(next);
      }
      renderInspector();
    } catch (error) {
      state.message = error.message;
      renderInspector();
    }
  }

  function judge(change, verdict, errorType = null) {
    const previous = state.verdicts[change.id];
    const note = inspector.querySelector("textarea[name=note]")?.value || previous?.note || "";
    if (verdict === "error" && !errorType) {
      state.awaitingType = true;
      renderInspector();
      return;
    }
    if (errorType === "other" && !note.trim()) {
      state.message = "类型选了“其他”，先在备注里写原因";
      state.awaitingType = true;
      renderInspector();
      inspector.querySelector("textarea[name=note]")?.focus();
      return;
    }
    save({ key: change.id, kind: "change", verdict, error_type: errorType, note }, { advance: !previous });
  }

  // ---- inspector -----------------------------------------------------------------------

  function renderInspector() {
    if (state.pendingMiss) clear(inspector, missPanel());
    else if (state.selected && changeById.has(state.selected)) clear(inspector, changePanel(changeById.get(state.selected)));
    else clear(inspector, recordPanel());
  }

  function message() {
    return state.message ? h("p", { class: "bad-text rr-message" }, state.message) : null;
  }

  function changePanel(change) {
    const row = record.rows[change.row];
    const current = state.verdicts[change.id];
    const where = [stepLabel(row), change.kind === "text" ? FIELD_LABELS[change.field] : change.kind === "tool" ? "工具调用" : "整步", row.kind === "merged" ? "合并步骤" : null].filter(Boolean).join(" · ");
    const body = [];
    if (change.kind === "text") {
      body.push(h("div", { class: "rr-change-text" }, change.removed ? h("del", { class: "rr-del" }, change.removed) : null, change.added ? h("ins", { class: "rr-ins" }, change.added) : null));
    } else if (change.kind === "tool") {
      const tool = row.tools.find((item) => item.change_id === change.id);
      body.push(h("p", {}, TOOL_CHANGE[tool.change] + " · ", h("code", {}, tool.name)));
      if (tool.change !== "changed") body.push(details("参数", tool.input || "", true));
    } else {
      body.push(h("p", {}, change.change === "removed" ? "改写删掉了这一步。" : "改写新增了这一步。"));
    }
    if (change.annotations?.length) change.annotations.forEach((edit) => body.push(annotationCard(edit)));
    else if (change.kind === "text") {
      body.push(
        h(
          "p",
          { class: "hint" },
          change.explained === "summary_sync"
            ? "这是随上一段压缩摘要一起重建的续写开头，改动来自那段摘要的改写。"
            : record.batch.annotations
              ? "流水线没有记录这处改动。"
              : "这个批次没有流水线注解，只有 diff。"
        )
      );
    }
    if (row.targets?.length) body.push(targetsCard(row.targets));
    const buttons = [["correct", "对", "1"], ["error", "错", "2"], ["uncertain", "待定", "3"]].map(([value, label, key]) =>
      h("button", { type: "button", class: "rr-verdict-button " + value + (current?.verdict === value ? " active" : ""), onclick: () => judge(change, value) }, label, h("kbd", {}, key))
    );
    const types = state.awaitingType || current?.verdict === "error"
      ? h(
          "div",
          { class: "rr-types" },
          h("span", { class: "rr-label" }, "错在哪（按 1–6）"),
          Object.entries(errorTypes).map(([key, label], index) =>
            h("button", { type: "button", class: "rr-type" + (current?.error_type === key ? " active" : ""), onclick: () => judge(change, "error", key) }, h("kbd", {}, String(index + 1)), label)
          )
        )
      : null;
    const note = h("textarea", { name: "note", rows: 2, placeholder: "备注（可选；类型选“其他”时必填）" });
    note.value = current?.note || "";
    note.addEventListener("change", () => {
      if (current) save({ key: change.id, kind: "change", verdict: current.verdict, error_type: current.error_type, note: note.value });
    });
    return h(
      "div",
      { class: "panel inspector-card rr-panel" },
      h(
        "div",
        { class: "rr-panel-head" },
        h("strong", {}, "改动 " + (order.indexOf(change.id) + 1) + " / " + order.length),
        h("span", { class: "muted" }, where),
        h("button", { type: "button", class: "link-button", onclick: () => { state.selected = null; markChanges(); renderInspector(); } }, "本条概况")
      ),
      body,
      h(
        "div",
        { class: "rr-verdict" },
        h("div", { class: "rr-verdict-buttons" }, buttons),
        types,
        note,
        current ? h("button", { type: "button", class: "link-button", onclick: () => save({ key: change.id, delete: true }) }, "清除判定") : null,
        message()
      )
    );
  }

  function annotationCard(edit) {
    const items = record.plan?.items || {};
    const type = (warning) => String(warning).split(":")[0].trim();
    return h(
      "div",
      { class: "rr-note" },
      h(
        "div",
        { class: "chips" },
        h("span", { class: "rr-label" }, "流水线记录"),
        edit.category ? chip(edit.category, "info") : null,
        edit.source ? chip("来源 " + edit.source, "muted") : null,
        edit.target ? chip(edit.target, "muted") : null
      ),
      edit.reason ? h("p", { class: "rr-reason" }, edit.reason) : null,
      edit.warnings?.length ? h("div", { class: "chips" }, edit.warnings.map((warning) => chip(warning, gateWarnings.has(type(warning)) ? "bad" : "warn"))) : null,
      edit.refs?.length ? h("div", { class: "rr-refs" }, h("span", { class: "rr-label" }, "方案条目"), edit.refs.map((ref) => planItem(ref, items[ref]))) : null,
      edit.evidence?.length ? details("证据 " + edit.evidence.length, h("div", {}, edit.evidence.map(evidenceLine))) : null
    );
  }

  function planItem(ref, item) {
    if (!item) return h("div", { class: "rr-plan" }, h("code", {}, ref));
    const fates = record.plan?.fates || {};
    return details(
      h("span", {}, h("code", {}, ref), " ", chip(item.fate || "?", item.fate === "removed" ? "bad" : item.fate?.startsWith("retained") ? "ok" : "warn")),
      h(
        "div",
        { class: "rr-plan-body" },
        item.group ? h("div", { class: "muted" }, item.group) : null,
        item.before ? h("div", {}, h("span", { class: "rr-label" }, "原文"), h("div", { class: "rr-plan-text" }, item.before)) : null,
        item.after ? h("div", {}, h("span", { class: "rr-label" }, "新文"), h("div", { class: "rr-plan-text" }, item.after)) : null,
        item.note ? h("p", { class: "hint" }, item.note) : null,
        fates[item.fate] ? h("p", { class: "hint" }, "去向“" + item.fate + "”：" + fates[item.fate]) : null
      )
    );
  }

  function evidenceLine(item) {
    if (!item || typeof item !== "object") return h("div", {}, String(item));
    const verified = item.verified ?? item.ok;
    return h(
      "div",
      { class: "rr-evidence" },
      verified === true ? chip("已核实", "ok") : verified === false ? chip("未核实", "warn") : null,
      h("q", {}, item.quote || item.text || JSON.stringify(item)),
      item.source || item.msg != null ? h("span", { class: "muted" }, " — " + (item.source || "m" + item.msg)) : null
    );
  }

  function targetsCard(targets) {
    return details(
      "流水线在这一步审阅了什么",
      h(
        "div",
        {},
        targets.map((target) =>
          h(
            "div",
            { class: "rr-target" },
            h(
              "div",
              { class: "chips" },
              chip(target.task || "审阅", "muted"),
              target.need_context ? chip("需要更多上下文", "warn") : null,
              target.evidence?.unverified ? chip("未核实证据 " + target.evidence.unverified, "warn") : null,
              target.rejected?.length ? chip("校验拒绝 " + target.rejected.length, "warn") : null
            ),
            target.locator?.length
              ? h("div", {}, h("span", { class: "rr-label" }, "定位提示"), target.locator.map((hint) => h("div", { class: "rr-locator" }, chip(hint.source || "?", "info"), hint.quote ? h("q", {}, hint.quote) : null, hint.reason ? h("span", { class: "hint" }, " — " + hint.reason) : null)))
              : null,
            target.kept?.length
              ? h("div", {}, h("span", { class: "rr-label" }, "审阅后保留原文"), target.kept.map((kept) => h("div", { class: "rr-kept" }, h("q", {}, kept.quote || ""), kept.reason ? h("span", { class: "hint" }, " — " + kept.reason) : null)))
              : null
          )
        )
      ),
      !record.batch.annotations
    );
  }

  function recordPanel() {
    const { judged, errors, misses } = counts();
    const [gateLabel, gateTone] = GATE[state.gate.decision];
    const override = state.verdicts.record;
    const missList = Object.values(state.verdicts).filter((item) => item.kind === "miss");
    return h(
      "div",
      { class: "rr-side" },
      h(
        "section",
        { class: "panel inspector-card" },
        h("div", { class: "rr-gate big " + gateTone }, h("strong", {}, gateLabel), h("span", {}, "入训分流")),
        state.gate.reasons.length ? h("ul", { class: "rr-reasons" }, state.gate.reasons.map((reason) => h("li", { class: reason.level }, reason.text))) : h("p", { class: "hint" }, "没有待处理的问题"),
        h(
          "div",
          { class: "inline" },
          h("button", { type: "button", class: "secondary" + (override?.verdict === "include" ? " active" : ""), onclick: () => save({ key: "record", kind: "record", verdict: "include" }) }, "标为可进"),
          h("button", { type: "button", class: "secondary" + (override?.verdict === "exclude" ? " active" : ""), onclick: () => save({ key: "record", kind: "record", verdict: "exclude" }) }, "标为不进"),
          override ? h("button", { type: "button", class: "link-button", onclick: () => save({ key: "record", delete: true }) }, "清除人工决定") : null
        ),
        message()
      ),
      h(
        "section",
        { class: "panel inspector-card" },
        h("strong", {}, "进度"),
        h("p", {}, "已判 " + judged + " / " + record.changes.length + " 处 · 错 " + errors + " · 漏改 " + misses),
        h("p", { class: "hint" }, order.length ? "按 j 从第一处开始；1 对 · 2 错 · 3 待定，判完自动跳到下一处。选中正文里的一段可以标漏改。" : "这条记录没有改动。")
      ),
      record.status || record.syncs.length ? statusCard() : null,
      residueCard(),
      missList.length ? missCard(missList) : null,
      record.unplaced?.length ? unplacedCard() : null
    );
  }

  function statusCard() {
    return h(
      "section",
      { class: "panel inspector-card" },
      h("strong", {}, "流水线与摘要同步"),
      record.status ? h("p", {}, "流水线状态：", chip(record.status.value, STATUS_TONE[record.status.value] || "muted"), record.status.reason ? h("span", { class: "hint" }, " " + record.status.reason) : null) : null,
      record.syncs.map((sync) =>
        h(
          "div",
          { class: "rr-sync" },
          chip(sync.direction === "pipeline" ? "流水线记录" : sync.ok === false ? "不同步" : sync.ok ? "同步" : "无法判断", sync.ok === false ? "bad" : sync.ok ? "ok" : "muted"),
          h("span", {}, (sync.from || "?") + " → " + (sync.to || "?")),
          sync.direction !== "pipeline" && sync.paired === false ? h("span", { class: "hint" }, "（原文两段的摘要本来就不一致）") : null,
          sync.detail ? h("span", { class: "muted" }, " " + sync.detail) : null
        )
      )
    );
  }

  function residueCard() {
    if (!record.residue.length) {
      return h("section", { class: "panel inspector-card" }, h("strong", {}, "规则层残留"), h("p", { class: "hint" }, record.harness ? "改写后的训练步骤里没有再用到已删除的组件。" : "没有原始轨迹，无法检查。"));
    }
    return h(
      "section",
      { class: "panel inspector-card" },
      h("strong", {}, "规则层残留 " + record.residue.length),
      h("p", { class: "hint" }, "改写后的 harness 里已经没有、但改写后的步骤还在用的组件。确认漏改会让这条不进训练。"),
      record.residue.map((item) => {
        const verdict = state.verdicts[item.key]?.verdict;
        const uses = Object.entries(item.uses).map(([how, steps]) => USE_LABELS[how] + " " + steps.map((s) => "#" + s).join(" "));
        const firstRow = record.rows.findIndex((row) => row.after && item.steps.includes(row.after.step));
        return h(
          "div",
          { class: "rr-residue-item" + (verdict ? " done" : "") },
          h("button", { type: "button", class: "link-button", onclick: () => firstRow >= 0 && showRow(firstRow) }, KIND_LABELS[item.kind] + " · " + item.name),
          h("div", { class: "muted" }, uses.join(" · ")),
          h(
            "div",
            { class: "inline" },
            h("button", { type: "button", class: "secondary" + (verdict === "confirm" ? " active" : ""), onclick: () => save({ key: item.key, kind: "residue", verdict: "confirm" }) }, "确认漏改"),
            h("button", { type: "button", class: "secondary" + (verdict === "ignore" ? " active" : ""), onclick: () => save({ key: item.key, kind: "residue", verdict: "ignore" }) }, "忽略"),
            verdict ? h("button", { type: "button", class: "link-button", onclick: () => save({ key: item.key, delete: true }) }, "清除") : null
          )
        );
      })
    );
  }

  function missCard(misses) {
    return h(
      "section",
      { class: "panel inspector-card" },
      h("strong", {}, "标注的漏改 " + misses.length),
      misses.map((item) =>
        h(
          "div",
          { class: "rr-miss-item" },
          h("span", { class: "step-no" }, "#" + item.detail?.step),
          chip(missTypes[item.error_type] || item.error_type || "漏改", "bad"),
          h("q", {}, (item.detail?.quote || "").slice(0, 160)),
          item.note ? h("div", { class: "hint" }, item.note) : null,
          h("button", { type: "button", class: "link-button", onclick: () => save({ key: item.key, delete: true }) }, "删除")
        )
      )
    );
  }

  function unplacedCard() {
    return h(
      "section",
      { class: "panel inspector-card" },
      h("strong", {}, "流水线记了、但在文本里对不上的改动 " + record.unplaced.length),
      record.unplaced.map((edit) => h("div", { class: "rr-kept" }, h("code", {}, edit.key), " ", h("q", {}, (edit.old || "").slice(0, 120)), edit.reason ? h("div", { class: "hint" }, edit.reason) : null))
    );
  }

  function missPanel() {
    const pending = state.pendingMiss;
    const note = h("textarea", { name: "note", rows: 2, placeholder: "备注（可选；类型选“其他”时必填）" });
    return h(
      "div",
      { class: "panel inspector-card rr-panel" },
      h("div", { class: "rr-panel-head" }, h("strong", {}, "标为漏改"), h("span", { class: "muted" }, "#" + pending.step + " · " + (FIELD_LABELS[pending.field] || pending.field))),
      h("q", { class: "rr-quote" }, pending.quote),
      h("span", { class: "rr-label" }, "类型"),
      h(
        "div",
        { class: "rr-types" },
        Object.entries(missTypes).map(([key, label]) =>
          h(
            "button",
            {
              type: "button",
              class: "rr-type",
              onclick: () => {
                if (key === "other" && !note.value.trim()) {
                  state.message = "类型选了“其他”，先在备注里写原因";
                  clear(inspector, missPanel());
                  return;
                }
                save({ kind: "miss", verdict: "miss", error_type: key, note: note.value, detail: pending });
              },
            },
            label
          )
        )
      ),
      note,
      h("button", { type: "button", class: "link-button", onclick: () => { state.pendingMiss = null; renderInspector(); } }, "取消"),
      message()
    );
  }

  // ---- missed spots: select text in the rewritten trajectory ---------------------------

  function hideMissButton() {
    missButton.classList.add("hidden");
  }

  main.addEventListener("mouseup", () => {
    const selection = window.getSelection();
    const text = selection?.toString().trim() || "";
    if (text.length < 2 || !selection.rangeCount) return hideMissButton();
    const holder = (node) => (node?.nodeType === 1 ? node : node?.parentElement)?.closest(".rr-row, .rr-preview");
    const home = holder(selection.anchorNode);
    if (!home || home !== holder(selection.focusNode)) return hideMissButton();
    const row = record.rows[Number(home.dataset.row)];
    const within = (selection.anchorNode.nodeType === 1 ? selection.anchorNode : selection.anchorNode.parentElement);
    if (!row?.after || within.closest("del")) return hideMissButton();
    const field = within.closest("[data-field]")?.dataset.field || "text";
    const rect = selection.getRangeAt(0).getBoundingClientRect();
    missButton.style.top = Math.max(8, rect.top - 38) + "px";
    missButton.style.left = Math.min(window.innerWidth - 120, rect.left) + "px";
    missButton.classList.remove("hidden");
    missButton.onclick = () => {
      state.pendingMiss = { step: row.after.step, field, quote: text.slice(0, 2000) };
      state.selected = null;
      state.message = "";
      markChanges();
      hideMissButton();
      renderInspector();
    };
  });
  function onScroll() {
    if (!document.body.contains(main)) document.removeEventListener("scroll", onScroll, true);
    else hideMissButton();
  }
  document.addEventListener("scroll", onScroll, { passive: true, capture: true });

  // ---- keyboard ------------------------------------------------------------------------

  function onKey(event) {
    if (!still() || !document.body.contains(main)) {
      document.removeEventListener("keydown", onKey);
      return;
    }
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.target.closest?.("input, textarea, select, [contenteditable]")) return;
    const change = state.selected ? changeById.get(state.selected) : null;
    if (state.awaitingType && change && /^[1-6]$/.test(event.key)) {
      const type = Object.keys(errorTypes)[Number(event.key) - 1];
      if (type) {
        event.preventDefault();
        judge(change, "error", type);
      }
      return;
    }
    const actions = {
      j: () => step(1),
      k: () => step(-1),
      J: () => gotoRecord(1),
      K: () => gotoRecord(-1),
      1: () => change && judge(change, "correct"),
      2: () => change && judge(change, "error"),
      3: () => change && judge(change, "uncertain"),
      t: toggleTraining,
      o: () => change && toggleSplit(change.row),
      e: toggleExpandAll,
      Escape: () => {
        state.awaitingType = false;
        state.pendingMiss = null;
        state.selected = null;
        markChanges();
        renderInspector();
      },
      "?": () => shortcuts.toggleAttribute("open"),
    };
    const action = actions[event.key];
    if (!action) return;
    event.preventDefault();
    action();
  }
  document.addEventListener("keydown", onKey);

  renderHead();
  renderMap();
  renderRows();
  renderInspector();
}
