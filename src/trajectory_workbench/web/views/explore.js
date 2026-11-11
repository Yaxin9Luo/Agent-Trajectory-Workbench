import { api, pollJob } from "../api.js";
import { clear, empty, h } from "../dom.js";
import { formatCount, outcomeTone, percent } from "../presentation.mjs";
import { t } from "../i18n.mjs";

/**
 * Tools and error templates of the current collection, optionally side by side with a
 * second collection (two checkpoints, two harness versions).
 */
export async function renderExplore(root, ctx, params, still) {
  const other = params.get("b") || "";
  const names = [ctx.collection || "", other].filter((name, index) => index === 0 || name);
  const picker = h(
    "select",
    { "aria-label": t("对比集合"), onchange: () => ctx.navigate("#/explore" + (picker.value ? "?b=" + encodeURIComponent(picker.value) : "")) },
    h("option", { value: "" }, t("不对比")),
    ctx.collections.filter((item) => item.collection !== ctx.collection).map((item) => h("option", { value: item.collection }, t("对比：{name}", { name: item.collection })))
  );
  picker.value = other;
  const body = h("div", { class: "explore-body" }, empty(t("统计中…")));
  clear(
    root,
    h(
      "header",
      { class: "page-head" },
      h("div", {}, h("span", { class: "eyebrow" }, t("工具与报错")), h("h1", {}, (ctx.collection || t("全部集合")) + (other ? " vs " + other : ""))),
      h("div", { class: "head-actions" }, picker)
    ),
    body
  );
  const payload = await api.explore(names);
  if (!still()) return;
  const [a, b] = payload.collections;
  const label = (item) => item.collection || t("全部集合");
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
      h("span", {}, t("工具")),
      h("span", { class: "num", title: t("每条轨迹平均调用次数") }, b ? t("A 次/条") : t("次/条")),
      h("span", { class: "num", title: t("用到这个工具的轨迹占比") }, b ? t("A 使用率") : t("使用率")),
      h("span", { class: "num" }, b ? t("A 报错率") : t("报错率")),
      b ? [h("span", { class: "num" }, t("B 次/条")), h("span", { class: "num" }, t("B 使用率")), h("span", { class: "num" }, t("B 报错率"))] : null
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
          { class: "num", title: t("{count} 次 · 成功轨迹 {pass} · 失败轨迹 {fail}", { count: item.count, pass: item.pass, fail: item.fail }) },
          t("{n} 条 ({share})", { n: item.trajectories, share: percent(item.share) })
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
        h("div", { class: "explore-row head" }, h("span", {}, t("报错模板（路径/数字/字符串已抽象）")), h("span", { class: "num" }, b ? t("A 出现在") : t("出现在")), b ? h("span", { class: "num" }, t("B 出现在")) : null, h("span", {}, t("例子"))),
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
    : empty(t("没有工具报错"));

  const stale = payload.collections.filter((item) => item.stale);
  const staleNotice = stale.length
    ? h(
        "section",
        { class: "panel guide" },
        h("strong", {}, t("有 {n} 条轨迹是旧版本导入的，还没有工具统计。", { n: stale.reduce((sum, item) => sum + item.stale, 0) })),
        stale.filter((item) => item.collection).map((item) => {
          const button = h("button", { type: "button", class: "secondary" }, t("重新索引 {name}", { name: item.collection }));
          button.addEventListener("click", async () => {
            button.disabled = true;
            const job = await api.reindex(item.collection);
            const finished = await pollJob(job.id, (state) => { button.textContent = t("重新索引中 {done} / {total}", { done: state.done || 0, total: state.total ?? "…" }); });
            button.textContent = finished.status === "done" ? t("完成，刷新页面") : t("失败：{error}", { error: finished.error });
          });
          return button;
        })
      )
    : null;
  const facts = (item) =>
    h("span", { class: "muted" }, t("{label}：{n} 条轨迹 · 工具 {tools} 种 · 报错模板 {templates} 种", { label: label(item), n: formatCount(item.trajectories), tools: item.tools.length, templates: item.templates.length }));
  clear(
    body,
    staleNotice,
    h("section", { class: "panel explore-card" }, h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, t("工具使用")), h("p", {}, facts(a), b ? [" · ", facts(b)] : null))), toolTable),
    h("section", { class: "panel explore-card" }, h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, t("报错模板")), h("p", {}, t("按出现的轨迹数排序；例子点进去直接到那一步")))), templateTable)
  );
}
