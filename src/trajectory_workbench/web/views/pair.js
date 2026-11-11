import { api } from "../api.js";
import { chip, clear, details, empty, h } from "../dom.js";
import {
  OUTCOME_LABELS,
  PHASE_LABELS,
  firstDivergence,
  formatCount,
  formatDuration,
  outcomeTone,
  percent,
  railCells,
} from "../presentation.mjs";
import { t, tb } from "../i18n.mjs";

/** Compare landing page: what is selected, and how to pick. */
export async function renderCompareHome(root, ctx) {
  const selection = ctx.compareSelection;
  const picked = await Promise.all(selection.map((id) => api.trajectory(id).catch(() => null)));
  // app.js gives this view no `still`; a newer route has replaced `root` if the user moved on.
  if (!root.isConnected) return;
  // A picked trajectory that no longer loads (re-imported, deleted) would make the pair page an
  // error every time; stay here so it can be removed.
  if (selection.length === 2 && picked.every(Boolean)) {
    ctx.navigate("#/pair/" + selection.map(encodeURIComponent).join("/"));
    return;
  }
  clear(
    root,
    h("header", { class: "page-head" }, h("div", {}, h("span", { class: "eyebrow" }, t("对比")), h("h1", {}, t("并排对比两条轨迹")))),
    h(
      "section",
      { class: "panel guide" },
      h("strong", {}, t("怎么选：")),
      h(
        "ol",
        {},
        h("li", {}, t("在轨迹库勾选任意两条（同题的成功/失败、同题的新旧 checkpoint、改写前后……）；")),
        h("li", {}, t("或在阅读器右上角点“加入对比”；")),
        h("li", {}, t("阅读器“关联轨迹”里同一道题的其他尝试可以直接点“对照读”。"))
      ),
      h("p", {}, t("整批改写前后（同一个样本 id 在两个集合里）："), h("a", { href: "#/rewrites" }, t("改写审阅 →")))
    ),
    h(
      "section",
      { class: "panel compare-card" },
      h("h2", { class: "mini-title" }, t("已选 {n} / 2", { n: selection.length })),
      selection.length
        ? selection.map((id, index) =>
            h(
              "div",
              { class: "related-row" },
              picked[index] ? h("a", { href: "#/t/" + encodeURIComponent(id) }, picked[index].title) : h("span", { class: "muted" }, t("{id}（读不到，可能已被删除）", { id })),
              h("button", { type: "button", class: "link-button", onclick: () => { ctx.toggleCompare(id); ctx.navigate("#/compare"); } }, t("移除"))
            )
          )
        : empty(t("还没有选择")),
      h("a", { class: "link-button", href: "#/library" }, t("去轨迹库挑选 →"))
    )
  );
}

/** Two trajectories side by side: summary, tool-use differences, first divergence. */
export async function renderPair(root, leftId, rightId, ctx, still) {
  clear(root, empty(t("读取两条轨迹…")));
  const [left, right] = await Promise.all([api.trajectory(leftId), api.trajectory(rightId)]);
  const [leftMessages, rightMessages] = await Promise.all([
    api.messages(leftId, { roles: "user,assistant,tool,result", limit: 500 }),
    api.messages(rightId, { roles: "user,assistant,tool,result", limit: 500 }),
  ]);
  if (!still()) return;
  const hide = (run) => ctx.blind && !run.review?.predicted_status;
  const divergence = firstDivergence(leftMessages.items, rightMessages.items);

  const header = h(
    "header",
    { class: "page-head" },
    h("div", {}, h("span", { class: "eyebrow" }, left.task?.key && left.task.key === right.task?.key ? t("同题对比") : t("对比")), h("h1", {}, left.title)),
    h(
      "div",
      { class: "head-actions" },
      h("a", { class: "link-button", href: "#/pair/" + encodeURIComponent(rightId) + "/" + encodeURIComponent(leftId) }, t("左右互换")),
      h("a", { class: "link-button", href: "#/t/" + encodeURIComponent(leftId) }, t("打开左侧")),
      h("a", { class: "link-button", href: "#/t/" + encodeURIComponent(rightId) }, t("打开右侧"))
    )
  );
  const summary = summaryTable(left, right, hide);
  const divergenceBar = divergence
    ? h(
        "div",
        { class: "divergence" },
        h("strong", {}, t("第一次分叉")),
        h("span", {}, t("左 #{left}（{leftLabel}） vs 右 #{right}（{rightLabel}）；此前 {shared} 个工具调用相同", { left: divergence.left, leftLabel: divergence.leftLabel, right: divergence.right, rightLabel: divergence.rightLabel, shared: divergence.shared })),
        h("button", { type: "button", class: "link-button", onclick: () => jumpBoth(divergence) }, t("两侧同时跳到这里"))
      )
    : h("div", { class: "divergence" }, h("strong", {}, t("工具调用序列")), h("span", {}, t("前 {n} 步没有找到分叉", { n: Math.min(leftMessages.items.length, rightMessages.items.length) })));
  const columns = h(
    "div",
    { class: "pair-grid" },
    column(left, leftMessages, ctx, hide(left), divergence?.left),
    column(right, rightMessages, ctx, hide(right), divergence?.right)
  );
  clear(root, header, summary, divergenceBar, columns);

  function jumpBoth(point) {
    [[leftId, point.left], [rightId, point.right]].forEach(([id, step]) =>
      document.getElementById(id + "-step-" + step)?.scrollIntoView({ behavior: "smooth", block: "center" })
    );
  }
}

function summaryTable(left, right, hide) {
  const outcome = (run) => (hide(run) ? t("隐藏") : OUTCOME_LABELS[run.outcome?.status] || t("无评分"));
  const flags = (run) => (run.signals?.flags || []).map((flag) => tb(flag.label)).join(t("、")) || "—";
  const phases = (run) => {
    const share = run.jev?.steps?.summary?.phase_share;
    if (!share) return t("未跑 Jev");
    return Object.entries(share).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([key, value]) => (PHASE_LABELS[key] || key) + " " + percent(value)).join(" · ");
  };
  const numeric = [
    [t("步数"), (run) => run.step_index.length, "low"],
    [t("工具调用"), (run) => run.metrics.tool_calls, "low"],
    [t("工具报错"), (run) => run.metrics.tool_errors, "low"],
    [t("时长"), (run) => run.metrics.wall_ms ?? (run.metrics.has_clock ? run.metrics.max_offset_ms : null), "low", formatDuration],
    ["tokens", (run) => run.metrics.total_tokens, "low", formatCount],
    [t("费用"), (run) => run.metrics.total_cost_usd, "low", (value) => (value == null ? "—" : "$" + Number(value).toFixed(2))],
  ];
  const rows = [
    [t("评分"), outcome(left), outcome(right)],
    [t("模型 / 集合"), [left.meta?.model, left.collection].filter(Boolean).join(" · "), [right.meta?.model, right.collection].filter(Boolean).join(" · ")],
  ];
  const table = h("table", { class: "compare-table" }, h("tr", {}, h("th", {}, ""), h("th", {}, t("左")), h("th", {}, t("右"))));
  rows.forEach(([label, a, b]) => table.append(h("tr", {}, h("td", { class: "label" }, label), h("td", {}, a), h("td", {}, b))));
  numeric.forEach(([label, read, better, format = (value) => (value == null ? "—" : String(value))]) => {
    const a = read(left);
    const b = read(right);
    const lower = a != null && b != null && a !== b ? (a < b ? "a" : "b") : null;
    table.append(
      h(
        "tr",
        {},
        h("td", { class: "label" }, label),
        h("td", { class: lower === "a" && better === "low" ? "better" : "" }, format(a)),
        h("td", { class: lower === "b" && better === "low" ? "better" : "" }, format(b))
      )
    );
  });
  table.append(h("tr", {}, h("td", { class: "label" }, t("规则信号")), h("td", {}, flags(left)), h("td", {}, flags(right))));
  table.append(h("tr", {}, h("td", { class: "label" }, t("Jev 阶段占比")), h("td", {}, phases(left)), h("td", {}, phases(right))));

  const counts = (run) => {
    const result = new Map();
    run.tool_index.forEach((tool) => result.set(tool.name, (result.get(tool.name) || 0) + 1));
    return result;
  };
  const a = counts(left);
  const b = counts(right);
  const names = [...new Set([...a.keys(), ...b.keys()])].filter((name) => (a.get(name) || 0) !== (b.get(name) || 0));
  names.sort((x, y) => Math.abs((b.get(y) || 0) - (a.get(y) || 0)) - Math.abs((b.get(x) || 0) - (a.get(x) || 0)));
  const tools = names.length
    ? h("div", { class: "chips" }, h("span", { class: "hint" }, t("工具用量差异：")), names.slice(0, 14).map((name) => chip(name + " " + (a.get(name) || 0) + " → " + (b.get(name) || 0), "muted")))
    : h("p", { class: "hint" }, t("两边工具用量相同"));
  return h("section", { class: "panel compare-card" }, table, tools);
}

function column(run, payload, ctx, hidden, divergeStep) {
  const status = hidden ? null : run.outcome?.status;
  const jevSteps = run.jev?.steps?.steps || [];
  const phase = new Map(jevSteps.map((item) => [item.step, item.phase]));
  const cells = railCells(run.step_index, { jevSteps, turningStep: run.review?.turning_step ?? null });
  const rail = h(
    "div",
    { class: "step-rail compact" },
    cells.map((cell) =>
      h("button", {
        type: "button",
        class: ["rail-cell", "k-" + cell.kind, cell.error && "error", cell.turning && "turning", cell.step === divergeStep && "flagged"].filter(Boolean).join(" "),
        title: "#" + cell.step,
        onclick: () => document.getElementById(run.id + "-step-" + cell.step)?.scrollIntoView({ behavior: "smooth", block: "center" }),
      })
    )
  );
  const flags = (run.signals?.flags || []).map((flag) => chip(tb(flag.label), flag.severity));
  const steps = payload.items.map((message) => {
    const tools = message.tools.map((tool) =>
      h(
        "div",
        { class: "pair-tool" + (tool.result?.is_error ? " error" : "") },
        h("strong", {}, tool.name),
        h("span", {}, summarizeInput(tool.input)),
        details(tool.result?.is_error ? t("报错") : t("返回"), tool.result ? tool.result.text : t("无返回"))
      )
    );
    return h(
      "article",
      { class: "pair-step " + message.role + (message.step === divergeStep ? " diverge" : ""), id: run.id + "-step-" + message.step },
      h(
        "div",
        { class: "message-head" },
        h("span", { class: "step-no" }, "#" + message.step),
        h("span", { class: "message-role" }, message.role),
        phase.get(message.step) ? chip(PHASE_LABELS[phase.get(message.step)], "phase k-" + phase.get(message.step)) : null,
        message.step === divergeStep ? chip(t("分叉"), "warn") : null,
        run.review?.turning_step === message.step ? chip(t("转折点"), "bad") : null
      ),
      message.thinking ? details(t("思考"), message.thinking) : null,
      message.text ? h("div", { class: "message-text" }, message.text.length > 1500 ? message.text.slice(0, 1500) + "…" : message.text) : null,
      tools
    );
  });
  return h(
    "section",
    { class: "pair-column panel" },
    h(
      "div",
      { class: "pair-head" },
      h("span", { class: "dot " + (status ? outcomeTone(status) : "muted") }),
      h("a", { href: "#/t/" + encodeURIComponent(run.id) }, h("strong", {}, status ? OUTCOME_LABELS[status] : t("评分隐藏"))),
      h("span", {}, [run.meta?.model, run.collection, t("{n} 步", { n: run.step_index.length })].filter(Boolean).join(" · ")),
      h("div", { class: "chips" }, flags)
    ),
    rail,
    h("div", { class: "pair-steps" }, steps, payload.total > payload.items.length ? h("p", { class: "hint" }, t("只显示前 {n} 条，完整内容请单独打开", { n: payload.items.length })) : null)
  );
}

function summarizeInput(input) {
  if (typeof input === "string") return input.slice(0, 140);
  if (input && typeof input === "object") {
    const preferred = input.command || input.cmd || input.file_path || input.path || input.query || input.url || input.pattern;
    if (preferred) return String(Array.isArray(preferred) ? preferred.join(" ") : preferred).slice(0, 140);
  }
  return JSON.stringify(input ?? {}).slice(0, 140);
}
