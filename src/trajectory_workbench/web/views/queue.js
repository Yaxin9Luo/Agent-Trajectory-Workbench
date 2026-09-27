import { api } from "../api.js";
import { chip, clear, empty, h } from "../dom.js";
import { OUTCOME_LABELS, outcomeTone, queueProgress } from "../presentation.mjs";

const BUCKET_HELP = {
  suspicious_pass: "步数异常少/多、改了测试、没验证就交……reward hack 基本藏在这里",
  failure: "按失败类型轮流抽，不只看一种",
  pair: "同一道题一条成功一条失败，对比着读最容易看出差在哪一步",
  checkpoint: "同一道题、不同模型或 checkpoint：看这次训练改了什么行为",
  random: "少量随机样本，防止自己的筛选带偏",
};

export async function renderQueue(root, ctx, params, still) {
  const date = params.get("date") || new Date().toISOString().slice(0, 10);
  const size = Number(params.get("size") || 20);
  clear(root, empty("生成今日练习…"));
  if (!ctx.collections.length) {
    clear(
      root,
      h(
        "section",
        { class: "empty-state" },
        h("div", { class: "empty-mark" }, "↗"),
        h("h1", {}, "先导入一批轨迹"),
        h("p", {}, "左下角粘贴绝对路径：~/.claude/projects、~/.codex/sessions、Harbor job 目录、TraceLab 导出或 SFT dialog.jsonl 都可以。")
      )
    );
    return;
  }
  const queue = await api.queue({ collection: ctx.collection, date, size });
  if (!still()) return;
  const progress = queueProgress(queue);
  ctx.queueNav = { ids: queue.items.map((item) => item.trajectory.id) };

  const dateInput = h("input", { type: "date", value: date, onchange: (event) => ctx.navigate("#/queue?date=" + event.target.value + "&size=" + size) });
  const sizeSelect = h(
    "select",
    { onchange: (event) => ctx.navigate("#/queue?date=" + date + "&size=" + event.target.value) },
    [10, 20, 30, 50].map((value) => h("option", { value }, value + " 条"))
  );
  sizeSelect.value = String(size);
  const header = h(
    "header",
    { class: "page-head" },
    h(
      "div",
      {},
      h("span", { class: "eyebrow" }, "今日练习 · " + (ctx.collection || "全部集合")),
      h("h1", {}, "已读 " + progress.done + " / " + progress.size),
      h("div", { class: "progress" }, h("span", { style: { width: progress.ratio * 100 + "%" } }))
    ),
    h("div", { class: "head-actions" }, dateInput, sizeSelect, h("span", { class: "muted" }, "候选池 " + queue.pool + " 条"))
  );
  const guide = h(
    "section",
    { class: "panel guide" },
    h("strong", {}, "每条按这个顺序读："),
    h(
      "ol",
      {},
      h("li", {}, "结果名副其实吗？评分器说成功，你亲自判断任务是否真的完成。"),
      h("li", {}, "转折点在哪一步？找到从“还在正轨”到“走偏”的那一步。"),
      h("li", {}, "工具使用、验证行为、思行是否一致、长度冗余、风格漂移。"),
      h("li", {}, "是不是环境的问题？是的话记下来去修环境。"),
      h("li", {}, "写一个能用实验验证的假设，并选修复手段。")
    )
  );
  // Bucket names reveal the grader result ("suspicious pass"), so blind mode shows one
  // flat list and each reason only after that trajectory has been read.
  const groups = {};
  queue.items.forEach((item) => (groups[ctx.blind ? "blind" : item.bucket] ||= []).push(item));
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
          h("h2", {}, (bucket === "blind" ? "今日轨迹" : items[0].bucket_label) + "（" + items.length + "）"),
          h("p", {}, bucket === "blind" ? "盲判模式：抽样原因和评分在读完后显示" : BUCKET_HELP[bucket] || "")
        )
      ),
      items.map((item) => queueRow(item, ctx))
    )
  );
  clear(root, header, guide, sections.length ? sections : empty("这个集合里没有可抽的轨迹（或都读过了）"));
}

function queueRow(item, ctx) {
  const row = item.trajectory;
  const status = ctx.blind && !item.done ? null : row.outcome_status;
  return h(
    "div",
    { class: "queue-row" + (item.done ? " done" : "") },
    h("span", { class: "dot " + (status ? outcomeTone(status) : "muted") }),
    h(
      "a",
      { class: "queue-title", href: "#/t/" + encodeURIComponent(row.id) },
      h("strong", {}, row.title || row.run_id),
      h("small", {}, [row.task_id, row.model, row.steps + " 步", status ? OUTCOME_LABELS[status] : null].filter(Boolean).join(" · "))
    ),
    h("span", { class: "queue-reason" }, ctx.blind ? (item.done ? item.bucket_label + "：" + item.reason : "读完后显示抽样原因") : item.reason),
    item.pair_with ? h("a", { class: "link-button", href: "#/pair/" + encodeURIComponent(row.id) + "/" + encodeURIComponent(item.pair_with) }, "对照读") : h("span"),
    item.done ? chip("已读", "ok") : chip("未读", "muted")
  );
}
