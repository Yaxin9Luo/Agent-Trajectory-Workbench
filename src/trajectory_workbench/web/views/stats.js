import { api, pollJob } from "../api.js";
import { clear, empty, h } from "../dom.js";
import { HUMAN_LABELS, OUTCOME_LABELS, formatCount, percent } from "../presentation.mjs";
import { t, tb } from "../i18n.mjs";

const JEV_KEYS = {
  ignores_error: t("忽略报错"),
  misreads_observation: t("误读工具返回"),
  thought_action_mismatch: t("思行不一"),
  claims_done: t("宣称完成"),
  harness_reliance: t("依赖 harness"),
  filler: t("空转/道歉"),
  violates_constraint: t("违反题目约束"),
  notices_problem: t("发现问题"),
  claims_done_unverified: t("未验证即宣称完成"),
  overclaim: t("声称完成但评分失败"),
  underclaim: t("自称未完成但评分通过"),
};

export async function renderStats(root, ctx, still) {
  clear(root, empty(t("统计中…")));
  const stats = await api.stats(ctx.collection);
  if (!still()) return;
  const reviews = stats.reviews;
  const scope = ctx.collection || t("全部集合");

  const kpi = (label, value, note, tone = "") => h("div", { class: "metric " + tone }, h("label", {}, label), h("b", {}, value), h("small", {}, note));
  const kpis = h(
    "section",
    { class: "metrics panel" },
    kpi("Episode", formatCount(stats.total), Object.entries(stats.outcomes).map(([key, count]) => (OUTCOME_LABELS[key] || key) + " " + count).join(" · ")),
    kpi(t("已读"), String(reviews.count), t("占 {share}", { share: percent(stats.total ? reviews.count / stats.total : null, 1) })),
    kpi(t("评分器 vs 人工分歧"), percent(reviews.disagreement_rate), t("{n} 条可比", { n: reviews.comparable }), reviews.disagreement_rate > 0.1 ? "alert" : ""),
    kpi(t("评分虚高率"), percent(reviews.inflated_rate), t("评分器判过 → 人工判不过：{n}", { n: reviews.inflated }), reviews.inflated ? "alert" : ""),
    kpi(t("盲判准确率"), percent(reviews.blind_accuracy), t("{n} 次盲判", { n: reviews.blind_total })),
    kpi(t("Jev 已分析"), t("{n} 个 episode", { n: formatCount(stats.jev.analyzed) }), (ctx.collection ? t("本集合累计 {tokens} tokens ≈ ${cost}（含旧版本）", { tokens: formatCount(stats.jev.usage.input_tokens), cost: (stats.jev.usage.input_tokens * 0.042 / 1e6).toFixed(2) }) : t("全部集合累计 {tokens} tokens ≈ ${cost}（含旧版本）", { tokens: formatCount(stats.jev.usage.input_tokens), cost: (stats.jev.usage.input_tokens * 0.042 / 1e6).toFixed(2) })))
  );

  const signalTable = h(
    "section",
    { class: "panel stat-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, t("自动信号（不用人读也能盯住）")), h("p", {}, t("同一信号在评分通过 / 失败样本里的比例差越大，越值得放进 KPI 卡")))),
    h(
      "table",
      { class: "stat-table" },
      h("tr", {}, h("th", {}, t("信号")), h("th", {}, t("命中")), h("th", {}, t("全体")), h("th", {}, t("通过样本中")), h("th", {}, t("失败样本中")), h("th", {}, "")),
      stats.signals.map((signal) =>
        h(
          "tr",
          {},
          h("td", {}, h("a", { href: "#/library?flag=" + signal.key }, tb(signal.label))),
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
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, t("Jev 语义信号")), h("p", {}, t("至少一步命中的轨迹数（共分析 {n} 条）", { n: stats.jev.analyzed })))),
    jevRows.length
      ? h("div", { class: "bar-list" }, jevRows.map(([key, count]) => barRow(JEV_KEYS[key] || key, count, stats.jev.analyzed)))
      : h("div", {}, empty(t("还没有 Jev 结果")), ctx.jevAvailable ? batchButton(ctx) : null)
  );

  const labelCard = h(
    "section",
    { class: "panel stat-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, t("失败类型分布")), h("p", {}, t("最常见的几类，应该做成自动检测指标")))),
    reviews.labels.length
      ? h(
          "div",
          { class: "bar-list" },
          reviews.labels.map((item) =>
            barRow(
              tb(item.label) + (item.decisive ? " · " + t("决定性 {n}", { n: item.decisive }) : "") + (item.severe ? " · " + t("重 {n}", { n: item.severe }) : ""),
              item.count,
              reviews.count,
              "#/library?view=all&label=" + encodeURIComponent(item.key)
            )
          )
        )
      : empty(t("还没有带标签的阅读记录"))
  );

  const confusionCard = h(
    "section",
    { class: "panel stat-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, t("评分器 × 人工判定")), h("p", {}, t("行：评分器；列：你的判断。左下与右上是 reward 与真实目标的差距")))),
    confusionTable(reviews.confusion)
  );

  const interventionCard = h(
    "section",
    { class: "panel stat-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, t("修复手段与归因")), h("p", {}, t("读完之后准备怎么改：数据 / 奖励 / eval / 环境 / harness 四层")))),
    h("div", { class: "bar-list" }, Object.entries(reviews.interventions).sort((a, b) => b[1] - a[1]).map(([key, count]) => barRow(tb(ctx.taxonomy.interventions[key] || key), count, reviews.count))),
    h("div", { class: "bar-list" }, Object.entries(reviews.attributions).sort((a, b) => b[1] - a[1]).map(([key, count]) => barRow(t("归因 · {name}", { name: tb(ctx.taxonomy.attributions[key] || key) }), count, reviews.count)))
  );

  const maxTurning = Math.max(1, ...reviews.turning_histogram);
  const turningCard = h(
    "section",
    { class: "panel stat-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, t("转折点出现在轨迹的哪里")), h("p", {}, t("按相对位置分 10 段；集中在后段常意味着长上下文遗忘或收尾不验证")))),
    h("div", { class: "histogram" }, reviews.turning_histogram.map((count, index) => h("span", { title: index * 10 + "–" + (index + 1) * 10 + "%: " + count, style: { height: (count / maxTurning) * 100 + "%" } })))
  );

  const maxDaily = Math.max(1, ...reviews.daily.map((day) => day.reviews || 0));
  const dailyCard = h(
    "section",
    { class: "panel stat-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, t("最近 14 天")), h("p", {}, t("每天读了多少条")))),
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
        h("h2", {}, t("训练就绪")),
        h(
          "p",
          {},
          ready.checked
            ? t("{ready} / {checked} 条可直接训练 · tokens P50 {p50} · P90 {p90} · 最长 {max}（上限 {limit}）· 平均训练 token 占 {share}", { ready: ready.ready, checked: ready.checked, p50: formatCount(ready.tokens.p50), p90: formatCount(ready.tokens.p90), max: formatCount(ready.tokens.max), limit: formatCount(ready.max_seq_len), share: percent(ready.trained_share) })
            : t("还没有检查结果（重新导入后生成）")
        )
      )
    ),
    ready.issues.length
      ? h("div", { class: "bar-list" }, ready.issues.map((item) => barRow(item.block && item.block < item.count ? t("{label}（{n} 条阻断）", { label: tb(item.label), n: item.block }) : tb(item.label), item.count, ready.checked, "#/library?view=all&issue=" + encodeURIComponent(item.key))))
      : null
  );

  const hx = stats.harness;
  const KIND = { mcp: "MCP", tool: t("工具"), skill: "Skill", subagent: t("子代理类型"), hook: "Hook", instruction: t("指令"), file: t("harness 文件") };
  const RELIANCE = { uses_component: t("要用/解读组件"), cites_instruction: t("拿 harness 指令当理由"), unclear: t("方式不明确") };
  const unanalyzed = hx.trajectories - hx.jev_analyzed;
  const harnessCard = h(
    "section",
    { class: "panel stat-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, t("依赖 harness 的样本")), h("p", {}, t("按条（训练样本）统计，共 {n} 条", { n: formatCount(hx.trajectories) })))),
    h("h3", {}, t("Jev 语义判断：推理是否依赖 harness")),
    hx.jev_analyzed
      ? h(
          "p",
          {},
          h("a", { href: "#/library?view=all&jev_flag=harness_reliance" }, t("依赖 {hits} 条 / 已分析 {analyzed} 条", { hits: formatCount(hx.jev_hits), analyzed: formatCount(hx.jev_analyzed) })),
          Object.keys(hx.jev_kinds).length ? t("（{list}）", { list: Object.entries(hx.jev_kinds).map(([key, count]) => t("{label} {n} 条", { label: RELIANCE[key] || key, n: count })).join(" · ") }) : "",
          unanalyzed > 0 ? h("span", { class: "muted" }, " · " + t("还有 {n} 条没跑 Jev", { n: formatCount(unanalyzed) })) : null
        )
      : h("p", { class: "muted" }, t("还没跑 Jev（共 {n} 条未分析）。在轨迹库「对未分析的轨迹跑 Jev」后，这里给出语义判断的条数。", { n: formatCount(hx.trajectories) })),
    h("h3", {}, t("规则统计：模型回合里用到了哪些 harness 组件")),
    h(
      "p",
      { class: "hint" },
      hx.traced
        ? t("导入时由代码统计，不是 Jev 的判断，覆盖全部 {n} 条：调用组件、在参数里用 harness 文件、字面提到、或用到组件的产出。只要出现就算，包括顺带提一句的；推理是否真的依赖看上面的 Jev。", { n: formatCount(hx.traced) })
        : t("还没有组件记录（重新索引后生成）")
    ),
    hx.components.length
      ? h(
          "div",
          { class: "bar-list" },
          hx.components.slice(0, 12).map((item) =>
            barRow(
              KIND[item.kind] + " · " + item.name + (item.any ? t("（调用 {calls} · 参数 {files} · 提到 {mentions}{outputs}）", { calls: item.calls, files: item.files, mentions: item.mentions, outputs: item.outputs ? t(" · 用到其产出 {n}", { n: item.outputs }) : "" }) : item.agent_output ? t("（{n} 条里是 agent 自己写的产物：命名约定，不算依赖）", { n: item.agent_output }) : t("（有但没用到）")),
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
    h("div", {}, h("span", { class: "eyebrow" }, t("统计 · {scope}", { scope })), h("h1", {}, t("读出来的东西，要变成指标"))),
    h(
      "div",
      { class: "head-actions" },
      h("a", { class: "button-like", href: api.exportUrl(ctx.collection), download: "reviews.jsonl" }, t("导出阅读记录 JSONL")),
      h("a", { class: "button-like", href: api.correctionsUrl(ctx.collection, "sft"), download: "corrections-sft.jsonl", title: t("有转折点和修正续写的阅读记录 → SFT 样本") }, t("修正 → SFT")),
      h("a", { class: "button-like", href: api.correctionsUrl(ctx.collection, "dpo"), download: "corrections-dpo.jsonl", title: t("同一上下文：修正为 chosen，原来那一步为 rejected") }, t("修正 → DPO"))
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
  if (!graders.length) return empty(t("还没有人工判定"));
  const humans = Object.keys(HUMAN_LABELS);
  return h(
    "table",
    { class: "stat-table confusion" },
    h("tr", {}, h("th", {}, t("评分器 \\ 人工")), humans.map((key) => h("th", {}, HUMAN_LABELS[key]))),
    graders.map((grader) =>
      h(
        "tr",
        {},
        h("td", {}, OUTCOME_LABELS[grader] || grader),
        humans.map((human) => {
          const value = confusion[grader]?.[human] || 0;
          const off = (grader === "pass" && (human === "fail" || human === "partial")) || ((grader === "fail" || grader === "error") && human === "pass");
          return h("td", { class: "num" + (off && value ? " bad-cell" : "") }, String(value));
        })
      )
    )
  );
}

function batchButton(ctx) {
  const status = h("span", { class: "hint" });
  const button = h("button", { type: "button", class: "secondary" }, t("对本集合跑 Jev 分析"));
  button.addEventListener("click", async () => {
    button.disabled = true;
    const job = await api.batchJev(ctx.collection || null, 200);
    const done = await pollJob(job.id, (state) => { status.textContent = (state.done || 0) + " / " + (state.total ?? "…"); });
    status.textContent = done.status === "done" ? t("完成，刷新查看") : t("失败：{error}", { error: done.error });
    button.disabled = false;
  });
  return h("div", { class: "inline" }, button, status);
}
