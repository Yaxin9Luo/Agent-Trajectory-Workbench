import { api } from "../api.js";
import { chip, clear, empty, h } from "../dom.js";
import { formatCount } from "../presentation.mjs";

const KIND_LABELS = { changed: "改了", removed: "删了", added: "新增" };

/** Two collections paired by sample id (e.g. before / after a rewrite pass). */
export async function renderRewrite(root, ctx, params, still) {
  const before = params.get("a") || "";
  const after = params.get("b") || "";
  const pick = (name, value, label) => {
    const select = h(
      "select",
      { "aria-label": label, onchange: () => go({ [name]: select.value }) },
      h("option", { value: "" }, label),
      ctx.collections.map((item) => h("option", { value: item.collection }, item.collection))
    );
    select.value = value;
    return select;
  };
  function go(changes) {
    const next = new URLSearchParams({ a: before, b: after, ...changes });
    [...next.keys()].forEach((key) => { if (!next.get(key)) next.delete(key); });
    ctx.navigate("#/rewrite?" + next.toString());
  }
  const body = h("div", { class: "explore-body" });
  clear(
    root,
    h(
      "header",
      { class: "page-head" },
      h("div", {}, h("span", { class: "eyebrow" }, "改写对比"), h("h1", {}, before && after ? before + " → " + after : "按样本 id 配对两个集合")),
      h("div", { class: "head-actions" }, pick("a", before, "改写前的集合"), pick("b", after, "改写后的集合"))
    ),
    body
  );
  if (!before || !after) {
    clear(body, empty("选两个集合：同一个样本 id 在两边各有一条（例如原始 SFT 与去 harness 痕迹后的改写版）。"));
    return;
  }
  if (params.get("before") && params.get("after")) {
    await renderDiff(body, params.get("before"), params.get("after"), still);
    return;
  }
  clear(body, empty("配对中…"));
  const payload = await api.rewrites(before, after);
  if (!still()) return;
  if (!payload.pairs.length) {
    clear(body, empty("两个集合里没有相同 id 的样本"));
    return;
  }
  const delta = (a, b) => (a == null || b == null ? "—" : formatCount(a) + " → " + formatCount(b));
  clear(
    body,
    h(
      "section",
      { class: "panel explore-card" },
      h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, payload.matched + " 对"), h("p", {}, payload.only_before ? "另有 " + payload.only_before + " 条只在改写前存在" : "改写前的样本都找到了改写版"))),
      h(
        "div",
        { class: "explore-table rewrite" },
        h("div", { class: "explore-row head" }, h("span", {}, "样本"), h("span", { class: "num" }, "步数"), h("span", { class: "num" }, "训练 tokens"), h("span", { class: "num", title: "训练 token 里含 harness 痕迹的步数" }, "harness 痕迹"), h("span", {}, "")),
        payload.pairs.map((pair) =>
          h(
            "div",
            { class: "explore-row" },
            h("strong", {}, pair.run_id),
            h("span", { class: "num" }, delta(pair.before.steps, pair.after.steps)),
            h("span", { class: "num" }, delta(pair.before.trained_tokens, pair.after.trained_tokens)),
            h("span", { class: "num" + (pair.after.residue ? " bad-text" : "") }, delta(pair.before.residue, pair.after.residue)),
            pair.same_source ? h("span", { class: "muted" }, "同一个文件") : h("a", { href: "#/rewrite?" + new URLSearchParams({ a: before, b: after, before: pair.before.id, after: pair.after.id }) }, "看改动 →")
          )
        )
      )
    )
  );
}

async function renderDiff(body, beforeId, afterId, still) {
  clear(body, empty("对比中…"));
  const diff = await api.rewriteDiff(beforeId, afterId);
  if (!still()) return;
  const counts = diff.counts;
  const blocks = diff.blocks.map((block) => {
    if (block.kind === "same") {
      return h("div", { class: "rewrite-same muted" }, "… " + block.before.length + " 条未改（#" + block.before[0] + (block.before.length > 1 ? "–#" + block.before[block.before.length - 1] : "") + "）…");
    }
    return h(
      "section",
      { class: "panel rewrite-block " + block.kind },
      h(
        "div",
        { class: "rewrite-head" },
        chip(KIND_LABELS[block.kind], block.kind === "changed" ? "warn" : block.kind === "removed" ? "bad" : "ok"),
        h("b", {}, block.role),
        block.before.length ? h("a", { href: "#/t/" + encodeURIComponent(beforeId) + "?step=" + block.before[0] }, "改写前 #" + block.before[0]) : null,
        block.after.length ? h("a", { href: "#/t/" + encodeURIComponent(afterId) + "?step=" + block.after[0] }, "改写后 #" + block.after[0]) : null
      ),
      Object.entries(block.fields).map(([name, lines]) =>
        h(
          "div",
          { class: "rewrite-field" },
          h("small", { class: "muted" }, name),
          h("pre", { class: "diff" }, lines.map((line) => h("span", { class: "diff-line " + (line.op === "+" ? "add" : line.op === "-" ? "del" : "same") }, line.op + " " + line.text + "\n")))
        )
      )
    );
  });
  const tokens = (side) => side.readiness?.trained_tokens;
  clear(
    body,
    h(
      "section",
      { class: "panel explore-card" },
      h("h2", {}, (diff.after.title || "") + ""),
      h("p", { class: "muted" }, "改了 " + counts.changed + " 条 · 删了 " + counts.removed + " 条 · 新增 " + counts.added + " 条 · 未改 " + counts.same + " 条 · 训练 tokens " + formatCount(tokens(diff.before)) + " → " + formatCount(tokens(diff.after)))
    ),
    blocks
  );
}
