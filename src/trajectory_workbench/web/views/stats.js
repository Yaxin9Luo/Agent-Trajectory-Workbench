import { api, pollJob } from "../api.js";
import { clear, empty, h } from "../dom.js";
import { HUMAN_LABELS, OUTCOME_LABELS, formatCount, percent } from "../presentation.mjs";

const JEV_KEYS = {
  ignores_error: "忽略报错",
  misreads_observation: "误读工具返回",
  thought_action_mismatch: "思行不一",
  claims_done: "宣称完成",
  harness_reliance: "依赖 harness",
  filler: "空转/道歉",
  violates_constraint: "违反题目约束",
  notices_problem: "发现问题",
  claims_done_unverified: "未验证即宣称完成",
  overclaim: "声称完成但评分失败",
  underclaim: "自称未完成但评分通过",
};

export async function renderStats(root, ctx, still) {
  clear(root, empty("统计中…"));
  const stats = await api.stats(ctx.collection);
  if (!still()) return;
  const reviews = stats.reviews;
  const scope = ctx.collection || "全部集合";

  const kpi = (label, value, note, tone = "") => h("div", { class: "metric " + tone }, h("label", {}, label), h("b", {}, value), h("small", {}, note));
  const kpis = h(
    "section",
    { class: "metrics panel" },
    kpi("轨迹", formatCount(stats.total), Object.entries(stats.outcomes).map(([key, count]) => (OUTCOME_LABELS[key] || key) + " " + count).join(" · ")),
    kpi("已读", String(reviews.count), "占 " + percent(stats.total ? reviews.count / stats.total : null, 1)),
    kpi("评分器 vs 人工分歧", percent(reviews.disagreement_rate), reviews.comparable + " 条可比", reviews.disagreement_rate > 0.1 ? "alert" : ""),
    kpi("评分虚高率", percent(reviews.inflated_rate), "评分器判过 → 人工判不过：" + reviews.inflated, reviews.inflated ? "alert" : ""),
    kpi("盲判准确率", percent(reviews.blind_accuracy), reviews.blind_total + " 次盲判"),
    kpi("Jev 已分析", formatCount(stats.jev.analyzed), formatCount(stats.jev.usage.input_tokens) + " tokens ≈ $" + (stats.jev.usage.input_tokens * 0.042 / 1e6).toFixed(3))
  );

  const signalTable = h(
    "section",
    { class: "panel stat-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, "自动信号（不用人读也能盯住）"), h("p", {}, "同一信号在评分通过 / 失败样本里的比例差越大，越值得放进 KPI 卡"))),
    h(
      "table",
      { class: "stat-table" },
      h("tr", {}, h("th", {}, "信号"), h("th", {}, "命中"), h("th", {}, "全体"), h("th", {}, "通过样本中"), h("th", {}, "失败样本中"), h("th", {}, "")),
      stats.signals.map((signal) =>
        h(
          "tr",
          {},
          h("td", {}, h("a", { href: "#/library?flag=" + signal.key }, signal.label)),
          h("td", { class: "num" }, String(signal.count)),
          h("td", { class: "num" }, percent(signal.share)),
          h("td", { class: "num" }, percent(signal.pass_share)),
          h("td", { class: "num" }, percent(signal.fail_share)),
          h("td", {}, bar(signal.share))
        )
      )
    )
  );

  const jevRows = Object.entries(stats.jev.prevalence).sort((a, b) => b[1] - a[1]);
  const jevCard = h(
    "section",
    { class: "panel stat-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, "Jev 语义信号"), h("p", {}, "至少一步命中的轨迹数（共分析 " + stats.jev.analyzed + " 条）"))),
    jevRows.length
      ? h("div", { class: "bar-list" }, jevRows.map(([key, count]) => barRow(JEV_KEYS[key] || key, count, stats.jev.analyzed)))
      : h("div", {}, empty("还没有 Jev 结果"), ctx.jevAvailable ? batchButton(ctx) : null)
  );

  const labelCard = h(
    "section",
    { class: "panel stat-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, "失败类型分布"), h("p", {}, "最常见的几类，应该做成自动检测指标"))),
    reviews.labels.length
      ? h(
          "div",
          { class: "bar-list" },
          reviews.labels.map((item) =>
            barRow(
              item.label + (item.decisive ? " · 决定性 " + item.decisive : "") + (item.severe ? " · 重 " + item.severe : ""),
              item.count,
              reviews.count,
              "#/library?view=all&label=" + encodeURIComponent(item.key)
            )
          )
        )
      : empty("还没有带标签的阅读记录")
  );

  const confusionCard = h(
    "section",
    { class: "panel stat-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, "评分器 × 人工判定"), h("p", {}, "行：评分器；列：你的判断。左下与右上是 reward 与真实目标的差距"))),
    confusionTable(reviews.confusion)
  );

  const interventionCard = h(
    "section",
    { class: "panel stat-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, "修复手段与归因"), h("p", {}, "读完之后准备怎么改：数据 / 奖励 / eval / 环境 / harness 四层"))),
    h("div", { class: "bar-list" }, Object.entries(reviews.interventions).sort((a, b) => b[1] - a[1]).map(([key, count]) => barRow(ctx.taxonomy.interventions[key] || key, count, reviews.count))),
    h("div", { class: "bar-list" }, Object.entries(reviews.attributions).sort((a, b) => b[1] - a[1]).map(([key, count]) => barRow("归因 · " + (ctx.taxonomy.attributions[key] || key), count, reviews.count)))
  );

  const maxTurning = Math.max(1, ...reviews.turning_histogram);
  const turningCard = h(
    "section",
    { class: "panel stat-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, "转折点出现在轨迹的哪里"), h("p", {}, "按相对位置分 10 段；集中在后段常意味着长上下文遗忘或收尾不验证"))),
    h("div", { class: "histogram" }, reviews.turning_histogram.map((count, index) => h("span", { title: index * 10 + "–" + (index + 1) * 10 + "%: " + count, style: { height: (count / maxTurning) * 100 + "%" } })))
  );

  const maxDaily = Math.max(1, ...reviews.daily.map((day) => day.reviews || 0));
  const dailyCard = h(
    "section",
    { class: "panel stat-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, "最近 14 天"), h("p", {}, "每天读了多少条"))),
    h("div", { class: "histogram daily" }, reviews.daily.map((day) => h("span", { title: day.date + ": " + (day.reviews || 0), style: { height: ((day.reviews || 0) / maxDaily) * 100 + "%" } })))
  );

  const ready = stats.readiness;
  const readinessCard = h(
    "section",
    { class: "panel stat-card" },
    h(
      "div",
      { class: "panel-head" },
      h(
        "div",
        {},
        h("h2", {}, "训练就绪"),
        h(
          "p",
          {},
          ready.checked
            ? ready.ready + " / " + ready.checked + " 条可直接训练 · tokens P50 " + formatCount(ready.tokens.p50) + " · P90 " + formatCount(ready.tokens.p90) + " · 最长 " + formatCount(ready.tokens.max) + "（上限 " + formatCount(ready.max_seq_len) + "）· 平均训练 token 占 " + percent(ready.trained_share)
            : "还没有检查结果（重新导入后生成）"
        )
      )
    ),
    ready.issues.length
      ? h("div", { class: "bar-list" }, ready.issues.map((item) => barRow(item.label + (item.block && item.block < item.count ? "（" + item.block + " 条阻断）" : ""), item.count, ready.checked, "#/library?view=all&issue=" + encodeURIComponent(item.key))))
      : null
  );

  const hx = stats.harness;
  const KIND = { mcp: "MCP", tool: "工具", skill: "Skill", hook: "Hook", instruction: "指令", file: "harness 文件" };
  const RELIANCE = { uses_component: "要用/解读组件", cites_instruction: "拿 harness 指令当理由", unclear: "方式不明确" };
  const harnessCard = h(
    "section",
    { class: "panel stat-card" },
    h(
      "div",
      { class: "panel-head" },
      h(
        "div",
        {},
        h("h2", {}, "依赖 harness 的样本"),
        h(
          "p",
          {},
          hx.traced
            ? "按条（训练样本）统计：" + hx.with_components + " / " + hx.traced + " 条的 harness 相对原生 agent 加了东西；规则（调用/参数/字面提到）命中 " + hx.rule_hits + " 条"
            : "还没有组件记录（重新索引后生成）"
        )
      )
    ),
    hx.jev_analyzed
      ? h(
          "p",
          { class: "hint" },
          h("a", { href: "#/library?view=all&jev_flag=harness_reliance" }, "Jev 判为推理依赖 harness：" + hx.jev_hits + " / " + hx.jev_analyzed + " 条已分析"),
          Object.keys(hx.jev_kinds).length ? "（" + Object.entries(hx.jev_kinds).map(([key, count]) => (RELIANCE[key] || key) + " " + count + " 条").join(" · ") + "）" : ""
        )
      : h("p", { class: "hint" }, "还没有 Jev 结果：在轨迹库「对未分析的轨迹跑 Jev」后，这里给出语义判断的依赖条数"),
    hx.components.length
      ? h(
          "div",
          { class: "bar-list" },
          hx.components.slice(0, 12).map((item) =>
            barRow(
              KIND[item.kind] + " · " + item.name + (item.any ? "（调用 " + item.calls + " · 参数 " + item.files + " · 提到 " + item.mentions + (item.outputs ? " · 用到其产出 " + item.outputs : "") + "）" : "（有但没用到）"),
              item.any,
              hx.traced,
              "#/library?view=all&flag=harness_ref"
            )
          )
        )
      : null
  );

  const header = h(
    "header",
    { class: "page-head" },
    h("div", {}, h("span", { class: "eyebrow" }, "统计 · " + scope), h("h1", {}, "读出来的东西，要变成指标")),
    h(
      "div",
      { class: "head-actions" },
      h("a", { class: "button-like", href: api.exportUrl(ctx.collection), download: "reviews.jsonl" }, "导出阅读记录 JSONL"),
      h("a", { class: "button-like", href: api.correctionsUrl(ctx.collection, "sft"), download: "corrections-sft.jsonl", title: "有转折点和修正续写的阅读记录 → SFT 样本" }, "修正 → SFT"),
      h("a", { class: "button-like", href: api.correctionsUrl(ctx.collection, "dpo"), download: "corrections-dpo.jsonl", title: "同一上下文：修正为 chosen，原来那一步为 rejected" }, "修正 → DPO")
    )
  );
  clear(root, header, kpis, h("div", { class: "stat-grid" }, readinessCard, harnessCard, signalTable, jevCard, labelCard, confusionCard, interventionCard, turningCard, dailyCard));
}

function bar(value) {
  return h("div", { class: "bar" }, h("div", { class: "bar-fill", style: { width: Math.min(100, (value || 0) * 100) + "%" } }));
}

function barRow(label, count, total, href) {
  return h(
    "div",
    { class: "bar-row" },
    href ? h("a", { href }, label) : h("span", {}, label),
    bar(total ? count / total : 0),
    h("span", { class: "num" }, count + (total ? " · " + percent(count / total) : ""))
  );
}

function confusionTable(confusion) {
  const graders = ["pass", "partial", "fail", "error", "unknown"].filter((key) => confusion[key]);
  if (!graders.length) return empty("还没有人工判定");
  const humans = Object.keys(HUMAN_LABELS);
  return h(
    "table",
    { class: "stat-table confusion" },
    h("tr", {}, h("th", {}, "评分器 \\ 人工"), humans.map((key) => h("th", {}, HUMAN_LABELS[key]))),
    graders.map((grader) =>
      h(
        "tr",
        {},
        h("td", {}, OUTCOME_LABELS[grader] || grader),
        humans.map((human) => {
          const value = confusion[grader]?.[human] || 0;
          const off = (grader === "pass" && (human === "fail" || human === "partial")) || (grader === "fail" && human === "pass");
          return h("td", { class: "num" + (off && value ? " bad-cell" : "") }, String(value));
        })
      )
    )
  );
}

function batchButton(ctx) {
  const status = h("span", { class: "hint" });
  const button = h("button", { type: "button", class: "secondary" }, "对本集合跑 Jev 分析");
  button.addEventListener("click", async () => {
    button.disabled = true;
    const job = await api.batchJev(ctx.collection || null, 200);
    const done = await pollJob(job.id, (state) => { status.textContent = (state.done || 0) + " / " + (state.total ?? "…"); });
    status.textContent = done.status === "done" ? "完成，刷新查看" : "失败：" + done.error;
    button.disabled = false;
  });
  return h("div", { class: "inline" }, button, status);
}
