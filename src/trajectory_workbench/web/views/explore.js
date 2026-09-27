import { api, pollJob } from "../api.js";
import { clear, empty, h } from "../dom.js";
import { formatCount, outcomeTone, percent } from "../presentation.mjs";

/**
 * Tools and error templates of the current collection, optionally side by side with a
 * second collection (two checkpoints, two harness versions).
 */
export async function renderExplore(root, ctx, params, still) {
  const other = params.get("b") || "";
  const names = [ctx.collection || "", other].filter((name, index) => index === 0 || name);
  const picker = h(
    "select",
    { "aria-label": "对比集合", onchange: () => ctx.navigate("#/explore" + (picker.value ? "?b=" + encodeURIComponent(picker.value) : "")) },
    h("option", { value: "" }, "不对比"),
    ctx.collections.filter((item) => item.collection !== ctx.collection).map((item) => h("option", { value: item.collection }, "对比：" + item.collection))
  );
  picker.value = other;
  const body = h("div", { class: "explore-body" }, empty("统计中…"));
  clear(
    root,
    h(
      "header",
      { class: "page-head" },
      h("div", {}, h("span", { class: "eyebrow" }, "工具与报错"), h("h1", {}, (ctx.collection || "全部集合") + (other ? " vs " + other : ""))),
      h("div", { class: "head-actions" }, picker)
    ),
    body
  );
  const payload = await api.explore(names);
  if (!still()) return;
  const [a, b] = payload.collections;
  const label = (item) => item.collection || "全部集合";
  const toolsB = new Map((b?.tools || []).map((item) => [item.tool, item]));
  const toolNames = [...new Set([...a.tools.map((item) => item.tool), ...(b?.tools || []).map((item) => item.tool)])];
  const toolA = new Map(a.tools.map((item) => [item.tool, item]));

  const toolCells = (item) =>
    item
      ? [
          h("span", { class: "num" }, item.calls_per_trajectory.toFixed(1)),
          h("span", { class: "num" }, percent(item.usage_share)),
          h("span", { class: "num" + (item.error_rate >= 0.2 ? " bad-text" : "") }, percent(item.error_rate)),
        ]
      : [h("span", { class: "num muted" }, "—"), h("span", { class: "num muted" }, "—"), h("span", { class: "num muted" }, "—")];
  const toolTable = h(
    "div",
    { class: "explore-table tools" + (b ? " paired" : "") },
    h(
      "div",
      { class: "explore-row head" },
      h("span", {}, "工具"),
      h("span", { class: "num", title: "每条轨迹平均调用次数" }, (b ? "A " : "") + "次/条"),
      h("span", { class: "num", title: "用到这个工具的轨迹占比" }, (b ? "A " : "") + "使用率"),
      h("span", { class: "num" }, (b ? "A " : "") + "报错率"),
      b ? [h("span", { class: "num" }, "B 次/条"), h("span", { class: "num" }, "B 使用率"), h("span", { class: "num" }, "B 报错率")] : null
    ),
    toolNames.slice(0, 80).map((name) =>
      h("div", { class: "explore-row" }, h("strong", {}, name), toolCells(toolA.get(name)), b ? toolCells(toolsB.get(name)) : null)
    )
  );

  const templatesB = new Map((b?.templates || []).map((item) => [item.tool + "\u0000" + item.template, item]));
  const seen = new Set();
  const templateKeys = [...a.templates, ...(b?.templates || [])].filter((item) => {
    const key = item.tool + "\u0000" + item.template;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const templateA = new Map(a.templates.map((item) => [item.tool + "\u0000" + item.template, item]));
  const share = (item) =>
    item
      ? h(
          "span",
          { class: "num", title: item.count + " 次 · 成功轨迹 " + item.pass + " · 失败轨迹 " + item.fail },
          item.trajectories + " 条 (" + percent(item.share) + ")"
        )
      : h("span", { class: "num muted" }, "—");
  const examples = (item) =>
    h(
      "span",
      { class: "chips" },
      (item?.examples || []).slice(0, 3).map((example) =>
        h(
          "a",
          { class: "step-link", href: "#/t/" + encodeURIComponent(example.id) + "?step=" + example.step, title: example.title || "" },
          h("span", { class: "dot " + (ctx.blind ? "muted" : outcomeTone(example.outcome_status)) }),
          "#" + example.step
        )
      )
    );
  const templateTable = templateKeys.length
    ? h(
        "div",
        { class: "explore-table templates" + (b ? " paired" : "") },
        h("div", { class: "explore-row head" }, h("span", {}, "报错模板（路径/数字/字符串已抽象）"), h("span", { class: "num" }, (b ? "A " : "") + "出现在"), b ? h("span", { class: "num" }, "B 出现在") : null, h("span", {}, "例子")),
        templateKeys.slice(0, 120).map((item) => {
          const key = item.tool + "\u0000" + item.template;
          const left = templateA.get(key);
          const right = templatesB.get(key);
          return h(
            "div",
            { class: "explore-row" },
            h("span", { class: "template" }, h("b", {}, item.tool), " ", h("code", {}, item.template)),
            share(left),
            b ? share(right) : null,
            examples(left || right)
          );
        })
      )
    : empty("没有工具报错");

  const stale = payload.collections.filter((item) => item.stale);
  const staleNotice = stale.length
    ? h(
        "section",
        { class: "panel guide" },
        h("strong", {}, "有 " + stale.reduce((sum, item) => sum + item.stale, 0) + " 条轨迹是旧版本导入的，还没有工具统计。"),
        stale.filter((item) => item.collection).map((item) => {
          const button = h("button", { type: "button", class: "secondary" }, "重新索引 " + item.collection);
          button.addEventListener("click", async () => {
            button.disabled = true;
            const job = await api.reindex(item.collection);
            const finished = await pollJob(job.id, (state) => { button.textContent = "重新索引中 " + (state.done || 0) + " / " + (state.total ?? "…"); });
            button.textContent = finished.status === "done" ? "完成，刷新页面" : "失败：" + finished.error;
          });
          return button;
        })
      )
    : null;
  const facts = (item) =>
    h("span", { class: "muted" }, label(item) + "：" + formatCount(item.trajectories) + " 条轨迹 · 工具 " + item.tools.length + " 种 · 报错模板 " + item.templates.length + " 种");
  clear(
    body,
    staleNotice,
    h("section", { class: "panel explore-card" }, h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, "工具使用"), h("p", {}, facts(a), b ? [" · ", facts(b)] : null))), toolTable),
    h("section", { class: "panel explore-card" }, h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, "报错模板"), h("p", {}, "按出现的轨迹数排序；例子点进去直接到那一步"))), templateTable)
  );
}
