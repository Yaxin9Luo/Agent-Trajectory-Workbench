import { api } from "../api.js";
import { clear, empty, h, persist, remember } from "../dom.js";
import { OUTCOME_LABELS, formatDuration, formatReward, outcomeTone, tierClass, tierText } from "../presentation.mjs";
import { t } from "../i18n.mjs";

/**
 * Same tasks across collections (checkpoints): one row per task, one cell per collection, each
 * cell the deck's first slide and its v4 tier / R, or the verifier outcome when it is not scored.
 */
export async function renderMatrix(root, ctx, still) {
  const known = ctx.collections.map((item) => item.collection);
  // null: the server's default (the collections that share tasks).
  const saved = remember("matrix-collections", null);
  const picked = Array.isArray(saved) ? known.filter((name) => saved.includes(name)) : null;

  const compareLink = h("span");
  ctx.onCompareChange = () => {
    const selection = ctx.compareSelection;
    root.querySelectorAll("input.pick[data-id]").forEach((box) => {
      box.checked = selection.includes(box.dataset.id);
    });
    clear(
      compareLink,
      selection.length === 2
        ? h("a", { class: "button-like primary-link", href: "#/pair/" + selection.map(encodeURIComponent).join("/") }, t("对比所选 2 条 →"))
        : h("span", { class: "muted" }, t("勾选 2 格可对比（已选 {n}）", { n: selection.length }))
    );
  };
  ctx.onCompareChange();
  const header = h(
    "header",
    { class: "page-head" },
    h("div", {}, h("span", { class: "eyebrow" }, t("同题矩阵")), h("h1", {}, t("每道题在各集合上的结果"))),
    h("div", { class: "head-actions" }, compareLink)
  );
  const body = h("div", { class: "matrix-body" }, empty(t("读取中…")));
  clear(root, header, body);
  if (!known.length) {
    clear(body, empty(t("左下角导入一个目录或文件开始")));
    return;
  }
  if (picked && picked.length < 2) {
    clear(body, picker(ctx, known, picked, null), emptyState(t("至少勾选两个集合。")));
    return;
  }
  const payload = await api.matrix(picked);
  if (!still()) return;
  const columns = payload.collections;
  const names = columns.map((column) => column.name);
  if (columns.length < 2 || !payload.shared_tasks) {
    clear(body, picker(ctx, known, picked || names, payload.rule), emptyState(t("所选集合之间没有同一道题。同一批题跑在不同 checkpoint 上、各导入成一个集合后，这里按任务对齐。")));
    return;
  }

  const state = {
    sort: remember("matrix-sort", "title"),
    differ: ctx.blind ? false : remember("matrix-differ", false),
  };
  // A column sort survives only while that column is shown, and never in blind mode.
  if (state.sort.startsWith("col:") && (ctx.blind || !names.includes(state.sort.slice(4)))) state.sort = "title";
  if (state.sort === "spread" && ctx.blind) state.sort = "title";

  const status = h("span", { class: "muted" });
  const sortSelect = h(
    "select",
    { "aria-label": t("排序") },
    h("option", { value: "title" }, t("按题目")),
    ctx.blind ? null : h("option", { value: "spread" }, t("按差距（最大在前）")),
    ctx.blind ? null : names.map((name) => h("option", { value: "col:" + name }, t("按 {name} 的 R", { name })))
  );
  sortSelect.value = state.sort;
  sortSelect.addEventListener("change", () => {
    state.sort = sortSelect.value;
    persist("matrix-sort", state.sort);
    draw();
  });
  const differBox = h("input", { type: "checkbox", checked: state.differ });
  differBox.addEventListener("change", () => {
    state.differ = differBox.checked;
    persist("matrix-differ", state.differ);
    draw();
  });
  const controls = h(
    "div",
    { class: "filters panel" },
    sortSelect,
    ctx.blind
      ? h("span", { class: "chip muted" }, t("盲判模式：隐藏评分"))
      : h("label", { class: "inline matrix-toggle" }, differBox, t("只看各列 tier 不同的题")),
    status
  );
  const table = h("div", { class: "matrix-scroll panel" });
  clear(body, picker(ctx, known, picked || names, payload.rule), controls, table);

  function draw() {
    let tasks = payload.tasks;
    if (state.differ) tasks = tasks.filter((task) => task.tiers_differ);
    tasks = sortTasks(tasks, state.sort, names);
    status.textContent = t("{shown} / {total} 道题", { shown: tasks.length, total: payload.tasks.length });
    clear(
      table,
      h(
        "table",
        { class: "matrix" },
        h(
          "thead",
          {},
          h(
            "tr",
            {},
            h("th", { class: "matrix-task" }, t("题目")),
            columns.map((column) => h("th", { class: "matrix-col" }, columnHead(column, ctx))),
            ctx.blind ? null : h("th", { class: "num matrix-spread", title: t("各列最好一次的 R：最高减最低（只算打了分的格）") }, t("差距"))
          )
        ),
        h(
          "tbody",
          {},
          tasks.length
            ? tasks.map((task) => taskRow(task, ctx))
            : h("tr", {}, h("td", { colspan: columns.length + 2 }, empty(t("没有各列 tier 不同的题"))))
        )
      )
    );
    ctx.onCompareChange();
  }
  draw();
}

function emptyState(text) {
  return h("div", { class: "panel" }, empty(text));
}

/** Checkbox list of every collection; the choice is kept in the browser. */
function picker(ctx, known, chosen, rule) {
  const boxes = known.map((name) =>
    h(
      "label",
      { class: "matrix-pick", title: name },
      h("input", {
        type: "checkbox",
        checked: chosen.includes(name),
        onchange: (event) => {
          const next = new Set(chosen);
          if (event.target.checked) next.add(name);
          else next.delete(name);
          persist("matrix-collections", known.filter((item) => next.has(item)));
          ctx.navigate("#/matrix");
        },
      }),
      h("span", {}, name)
    )
  );
  return h(
    "div",
    { class: "panel matrix-picker" },
    h("strong", {}, t("列（集合）")),
    h("div", { class: "matrix-picks" }, boxes),
    rule === "shared"
      ? h("small", { class: "muted" }, t("默认：和别的集合有同一道题的集合"))
      : h(
          "button",
          {
            type: "button",
            class: "secondary",
            onclick: () => {
              persist("matrix-collections", null);
              ctx.navigate("#/matrix");
            },
          },
          t("恢复默认")
        )
  );
}

function columnHead(column, ctx) {
  const parts = [t("{n} 道题", { n: column.tasks })];
  if (!ctx.blind) {
    if (column.rewarded) {
      parts.push(t("均值 R {r}", { r: column.r_mean == null ? "—" : formatReward(column.r_mean) }));
      parts.push(t("tier 4：{n}", { n: column.tiers["4"] }));
      if (column.masked) parts.push("masked " + column.masked);
    } else {
      parts.push(t("未打分"));
    }
  }
  return [
    h("strong", { class: "matrix-col-name", title: column.name }, column.name),
    h("small", { class: "matrix-col-summary" }, parts.join(" · ")),
    column.unkeyed ? h("small", { class: "matrix-col-summary matrix-warn", title: t("这些 episode 既没有任务 id 也没有任务说明，无法和别的集合对齐") }, t("{n} 个 episode 没有任务键，未列入", { n: column.unkeyed })) : null,
  ];
}

/** The best scored R of a cell, or null. */
function bestR(cell) {
  const values = cell.filter((attempt) => attempt.reward && !attempt.reward.masked).map((attempt) => attempt.reward.R);
  return values.length ? Math.max(...values) : null;
}

/** Best R first; unscored and masked attempts after, newest first (the order they came in). */
function ordered(cell) {
  const rank = (attempt) => (attempt.reward && !attempt.reward.masked ? attempt.reward.R : -Infinity);
  return cell.map((attempt, index) => [attempt, index]).sort((a, b) => rank(b[0]) - rank(a[0]) || a[1] - b[1]).map(([attempt]) => attempt);
}

function sortTasks(tasks, sort, names) {
  const byTitle = (a, b) => String(a.title).localeCompare(String(b.title)) || a.task_key.localeCompare(b.task_key);
  const nullsLast = (value) => (value == null ? -Infinity : value);
  if (sort === "spread") return [...tasks].sort((a, b) => nullsLast(b.spread) - nullsLast(a.spread) || byTitle(a, b));
  if (sort.startsWith("col:")) {
    const index = names.indexOf(sort.slice(4));
    return [...tasks].sort((a, b) => nullsLast(bestR(b.cells[index])) - nullsLast(bestR(a.cells[index])) || byTitle(a, b));
  }
  return [...tasks].sort(byTitle);
}

function taskRow(task, ctx) {
  return h(
    "tr",
    {},
    h("th", { class: "matrix-task", scope: "row", title: task.title + (task.task_id ? "\n" + task.task_id : "") }, h("span", {}, task.title), task.task_id && task.task_id !== task.title ? h("small", {}, task.task_id) : null),
    task.cells.map((cell) => h("td", { class: "matrix-cell" }, cell.length ? cellView(ordered(cell), ctx) : h("span", { class: "matrix-none" }, "—"))),
    ctx.blind ? null : h("td", { class: "num matrix-spread" }, task.spread == null ? "—" : formatReward(task.spread))
  );
}

function cellView(attempts, ctx) {
  const [first, ...rest] = attempts;
  const href = "#/t/" + encodeURIComponent(first.id);
  const reward = ctx.blind ? null : first.reward;
  const pick = h("input", {
    type: "checkbox",
    class: "pick",
    title: t("加入对比"),
    "aria-label": t("加入对比"),
    checked: ctx.compareSelection.includes(first.id),
    dataset: { id: first.id },
    onclick: (event) => {
      event.target.checked = ctx.toggleCompare(first.id);
      ctx.onCompareChange?.();
    },
  });
  let visual;
  if (ctx.blind) {
    visual = h("a", { class: "matrix-open", href }, t("打开轨迹"));
  } else if (reward) {
    const still = reward.still ? api.v4StillUrl(reward.label, reward.task_id, reward.still) : null;
    visual = [
      h(
        "a",
        { class: "matrix-thumb", href, title: t("打开轨迹") },
        still ? h("img", { src: still, alt: t("第一页截图"), loading: "lazy" }) : h("span", {}, t("没有截图"))
      ),
      h("span", { class: "chip v4-badge " + tierClass(reward), title: tierText(reward) + " · " + reward.label }, shortTier(reward)),
    ];
  } else {
    visual = h(
      "a",
      { class: "matrix-unscored", href, title: OUTCOME_LABELS[first.outcome_status] || first.outcome_status },
      h("span", { class: "dot " + outcomeTone(first.outcome_status) }),
      t("未打分")
    );
  }
  const meta = ctx.blind ? null : h("small", { class: "matrix-meta" }, attemptMeta(first));
  return h(
    "div",
    { class: "matrix-cell-body" },
    visual,
    h("div", { class: "matrix-cell-foot" }, pick, meta, rest.length ? more(rest, ctx) : null)
  );
}

/** "tier N · R 0.xx", or "masked". */
function shortTier(reward) {
  return reward.masked ? "masked" : "tier " + (reward.tier ?? "—") + " · R " + formatReward(reward.R);
}

function attemptMeta(attempt) {
  return [
    t("{n} 步", { n: attempt.steps ?? "—" }),
    attempt.tool_errors ? t("工具错 {n}", { n: attempt.tool_errors }) : null,
    attempt.duration_ms ? formatDuration(attempt.duration_ms) : null,
  ].filter(Boolean).join(" · ");
}

/** "+k": the other attempts of this task in this collection, each a link. */
function more(rest, ctx) {
  const line = (attempt) =>
    [ctx.blind ? null : attempt.reward ? shortTier(attempt.reward) : t("未打分"), ctx.blind ? null : attemptMeta(attempt), attempt.id].filter(Boolean).join(" · ");
  return h(
    "details",
    { class: "matrix-more" },
    h("summary", { title: rest.map(line).join("\n") }, "+" + rest.length),
    h("div", {}, rest.map((attempt) => h("a", { href: "#/t/" + encodeURIComponent(attempt.id) }, line(attempt))))
  );
}
