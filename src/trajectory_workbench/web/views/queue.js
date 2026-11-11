import { api } from "../api.js";
import { chip, clear, empty, h } from "../dom.js";
import { OUTCOME_LABELS, outcomeTone, queueProgress } from "../presentation.mjs";
import { t, tb } from "../i18n.mjs";

const BUCKET_HELP = {
  suspicious_pass: t("步数异常少/多、改了测试、没验证就交……reward hack 基本藏在这里"),
  failure: t("按失败类型轮流抽，不只看一种"),
  pair: t("同一道题一条成功一条失败，对比着读最容易看出差在哪一步"),
  checkpoint: t("同一道题、不同模型或 checkpoint：看这次训练改了什么行为"),
  random: t("少量随机样本，防止自己的筛选带偏"),
};

export async function renderQueue(root, ctx, params, still) {
  const date = params.get("date") || new Date().toISOString().slice(0, 10);
  const size = Number(params.get("size") || 20);
  clear(root, empty(t("生成今日练习…")));
  if (!ctx.collections.length) {
    clear(
      root,
      h(
        "section",
        { class: "empty-state" },
        h("div", { class: "empty-mark" }, "↗"),
        h("h1", {}, t("先导入一批轨迹")),
        h("p", {}, t("左下角粘贴绝对路径：~/.claude/projects、~/.codex/sessions、Harbor job 目录、TraceLab 导出或 SFT dialog.jsonl 都可以。"))
      )
    );
    return;
  }
  const queue = await api.queue({ collection: ctx.collection, date, size });
  if (!still()) return;
  const progress = queueProgress(queue);
  // The server lists the buckets in a fixed order (pass/fail pairs, suspicious passes,
  // failures, ...), so in blind mode that order alone would give the grade away: shuffle it,
  // the same way every time for the same day.
  const day = queue.date || date;
  const items = ctx.blind
    ? queue.items
        .map((item) => ({ item, key: stableHash(item.trajectory.id + "|" + day) }))
        .sort((a, b) => a.key - b.key || (a.item.trajectory.id < b.item.trajectory.id ? -1 : 1))
        .map(({ item }) => item)
    : queue.items;
  const doneIds = new Set(items.filter((item) => item.done).map((item) => item.trajectory.id));
  ctx.queueNav = { ids: items.map((item) => item.trajectory.id) };

  const dateInput = h("input", { type: "date", value: date, onchange: (event) => ctx.navigate("#/queue?date=" + event.target.value + "&size=" + size) });
  const sizeSelect = h(
    "select",
    { onchange: (event) => ctx.navigate("#/queue?date=" + date + "&size=" + event.target.value) },
    [10, 20, 30, 50].map((value) => h("option", { value }, t("{n} 条", { n: value })))
  );
  sizeSelect.value = String(size);
  const header = h(
    "header",
    { class: "page-head" },
    h(
      "div",
      {},
      h("span", { class: "eyebrow" }, t("今日练习 · {scope}", { scope: ctx.collection || t("全部集合") })),
      h("h1", {}, t("已读 {done} / {size}", { done: progress.done, size: progress.size })),
      h("div", { class: "progress" }, h("span", { style: { width: progress.ratio * 100 + "%" } }))
    ),
    h("div", { class: "head-actions" }, dateInput, sizeSelect, h("span", { class: "muted" }, t("候选池 {n} 条", { n: queue.pool })))
  );
  const guide = h(
    "section",
    { class: "panel guide" },
    h("strong", {}, t("每条按这个顺序读：")),
    h(
      "ol",
      {},
      h("li", {}, t("结果名副其实吗？评分器说成功，你亲自判断任务是否真的完成。")),
      h("li", {}, t("转折点在哪一步？找到从“还在正轨”到“走偏”的那一步。")),
      h("li", {}, t("工具使用、验证行为、思行是否一致、长度冗余、风格漂移。")),
      h("li", {}, t("是不是环境的问题？是的话记下来去修环境。")),
      h("li", {}, t("写一个能用实验验证的假设，并选修复手段。"))
    )
  );
  // Bucket names and reasons reveal the grader result ("suspicious pass", "the failing
  // sample of a pair"), so blind mode shows one flat list and never shows them.
  const groups = {};
  items.forEach((item) => (groups[ctx.blind ? "blind" : item.bucket] ||= []).push(item));
  const sections = Object.entries(groups).map(([bucket, items]) =>
    h(
      "section",
      { class: "queue-group panel" },
      h(
        "div",
        { class: "panel-head" },
        h(
          "div",
          {},
          h("h2", {}, t("{name}（{n}）", { name: bucket === "blind" ? t("今日轨迹") : tb(items[0].bucket_label), n: items.length })),
          h("p", {}, bucket === "blind" ? t("盲判模式：评分在读完后显示，不显示抽样原因；对照读在两条都读完后出现") : BUCKET_HELP[bucket] || "")
        )
      ),
      items.map((item) => queueRow(item, ctx, doneIds))
    )
  );
  clear(root, header, guide, sections.length ? sections : empty(t("这个集合里没有可抽的轨迹（或都读过了）")));
}

/** FNV-1a: a fixed pseudo-random order that does not depend on the server's bucket order. */
function stableHash(text) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index++) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

function queueRow(item, ctx, doneIds) {
  const row = item.trajectory;
  const status = ctx.blind && !item.done ? null : row.outcome_status;
  // Being paired at all says "one passed, one failed", so blind mode offers the pair only once
  // both of its trajectories have been read.
  const showPair = item.pair_with && (!ctx.blind || (item.done && doneIds.has(item.pair_with)));
  return h(
    "div",
    { class: "queue-row" + (item.done ? " done" : "") },
    h("span", { class: "dot " + (status ? outcomeTone(status) : "muted") }),
    h(
      "a",
      { class: "queue-title", href: "#/t/" + encodeURIComponent(row.id) },
      h("strong", {}, row.title || row.run_id),
      h("small", {}, [row.task_id, row.model, t("{n} 步", { n: row.steps }), status ? OUTCOME_LABELS[status] : null].filter(Boolean).join(" · "))
    ),
    h("span", { class: "queue-reason" }, ctx.blind ? "" : tb(item.reason)),
    showPair ? h("a", { class: "link-button", href: "#/pair/" + encodeURIComponent(row.id) + "/" + encodeURIComponent(item.pair_with) }, t("对照读")) : h("span"),
    item.done ? chip(t("已读"), "ok") : chip(t("未读"), "muted")
  );
}
