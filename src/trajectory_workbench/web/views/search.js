import { api } from "../api.js";
import { chip, clear, empty, h } from "../dom.js";
import { OUTCOME_LABELS, ROLE_LABELS, outcomeTone } from "../presentation.mjs";

const FIELD_LABELS = { text: "回复", thinking: "思考", input: "工具输入", output: "工具返回" };

/** Full-text search over every indexed step of the current collection. */
export async function renderSearch(root, ctx, params, still) {
  const query = params.get("q") || "";
  const input = h("input", { type: "search", value: query, placeholder: "搜索所有步骤：命令、报错、文件名、中文短语…（空格分隔的词都要出现，\"引号\"内按原样）", "aria-label": "全文搜索" });
  const form = h(
    "form",
    {
      class: "search-form panel",
      onsubmit: (event) => {
        event.preventDefault();
        const value = input.value.trim();
        ctx.navigate("#/search" + (value ? "?q=" + encodeURIComponent(value) : ""));
      },
    },
    input,
    h("button", { type: "submit", class: "primary" }, "搜索")
  );
  const results = h("div", { class: "search-results" });
  clear(
    root,
    h("header", { class: "page-head" }, h("div", {}, h("span", { class: "eyebrow" }, "全文搜索"), h("h1", {}, ctx.collection || "全部集合"))),
    form,
    results
  );
  input.focus();
  if (!query) {
    clear(results, empty("在导入时建立的步骤索引里搜索。结果按相关度，点一条直接跳到那一步并高亮。"));
    return;
  }
  clear(results, empty("搜索中…"));
  const payload = await api.search(query, ctx.collection || null);
  if (!still()) return;
  if (!payload.results.length) {
    clear(results, empty("没有找到。导入时关闭了全文索引的集合搜不到。"));
    return;
  }
  const terms = payload.terms;
  clear(
    results,
    h("p", { class: "muted" }, payload.hits + " 个步骤 · " + payload.results.length + " 条轨迹" + (payload.limited ? "（只显示最相关的前 " + payload.hits + " 个）" : "")),
    payload.results.map((group) => {
      const item = group.trajectory;
      const status = ctx.blind ? "hidden" : item.outcome_status;
      const role = item.group_role && item.group_role !== "main" ? ROLE_LABELS[item.group_role] + (item.segment ? " " + item.segment : "") : "";
      return h(
        "section",
        { class: "panel search-group" },
        h(
          "a",
          { class: "search-title", href: "#/t/" + encodeURIComponent(item.id) },
          h("span", { class: "dot " + (status === "hidden" ? "muted" : outcomeTone(status)), title: status === "hidden" ? "盲判模式" : OUTCOME_LABELS[status] }),
          h("strong", {}, item.title || item.run_id),
          h("small", {}, [item.collection, role, item.model].filter(Boolean).join(" · "))
        ),
        group.hits.map((hit) =>
          h(
            "a",
            { class: "search-hit", href: "#/t/" + encodeURIComponent(item.id) + "?step=" + hit.step + "&hl=" + encodeURIComponent(terms.join(" ")) },
            h("span", { class: "step-no" }, "#" + hit.step),
            hit.snippet?.field ? chip(FIELD_LABELS[hit.snippet.field] || hit.snippet.field, "muted") : null,
            h("span", { class: "search-snippet" }, highlighted(hit.snippet?.text || "", terms))
          )
        )
      );
    })
  );
}

/** Text split into plain and <mark> nodes for each case-insensitive term occurrence. */
export function highlighted(text, terms) {
  const wanted = terms.map((term) => term.toLowerCase()).filter(Boolean);
  if (!wanted.length) return [text];
  const lower = text.toLowerCase();
  const nodes = [];
  let index = 0;
  while (index < text.length) {
    let best = -1;
    let length = 0;
    wanted.forEach((term) => {
      const found = lower.indexOf(term, index);
      if (found >= 0 && (best < 0 || found < best)) {
        best = found;
        length = term.length;
      }
    });
    if (best < 0) {
      nodes.push(text.slice(index));
      break;
    }
    if (best > index) nodes.push(text.slice(index, best));
    nodes.push(h("mark", {}, text.slice(best, best + length)));
    index = best + length;
  }
  return nodes;
}
