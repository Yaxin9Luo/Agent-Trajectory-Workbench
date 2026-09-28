import { api } from "../api.js";
import { clear, empty, h } from "../dom.js";
import { PHASE_LABELS, WORK_LABELS, formatCount, formatDuration, percent } from "../presentation.mjs";

const QUESTION_LABELS = {
  notices_problem: ["发现问题", "推理或回复里说出了具体问题"],
  ignores_error: ["忽略报错", "上一步工具报错，这一步当作成功继续"],
  misreads_observation: ["误读工具返回", "对工具返回的描述与原文矛盾（只在返回完整可见时判断）"],
  thought_action_mismatch: ["思行不一", "推理说要做 A，工具调用做了 B"],
  claims_done: ["宣称完成", "对用户说任务已完成"],
  harness_reliance: ["依赖 harness", "推理/回复/调用依赖 harness 额外加的东西：要用或解读 MCP/工具/Skill/Hook/harness 文件，或拿 harness 加的指令当理由"],
  filler: ["空转/道歉", "主要是道歉、自我安慰、重复确认"],
  polish: ["打磨", "改文件的步骤里，Jev 判为“对已能用的东西做可选美化”"],
  violates_constraint: ["违反题目约束", "动作违反任务里写明的要求"],
};
const RELIANCE_LABELS = { uses_component: "要用/解读 harness 组件", cites_instruction: "拿 harness 指令当理由", unclear: "方式不明确" };
const CLAIMS = { complete: "声称全部完成", partial: "声称部分完成", failed: "报告失败/受阻", asks_user: "以提问结束", none: "没有最终汇报" };

export function renderJevPanel(slot, reader) {
  const { state, ctx } = reader;
  const run = state.run;
  const card = h("section", { class: "panel inspector-card jev-card" });
  slot.replaceChildren(card);
  const status = h("span", { class: "save-status" });

  async function analyze(force = false) {
    status.textContent = "Jev 逐步判断中…";
    try {
      const result = await api.analyze(run.id, force);
      reader.onJev(result);
      status.textContent = "";
      render();
    } catch (error) {
      status.textContent = error.message;
    }
  }

  function render() {
    const jev = state.jev;
    const head = h(
      "div",
      { class: "panel-head" },
      h("div", {}, h("h2", {}, "Jev 逐步分析"), h("p", {}, "每一步单独判断（状态只含这一步和它看到的工具返回），计数与聚合在代码里做")),
      status
    );
    if (!ctx.jevAvailable) {
      clear(card, head, empty("未配置 TYPESAFE_API_KEY，Jev 功能不可用"));
      return;
    }
    if (!jev?.steps) {
      const steps = run.step_index.filter((item) => item.role === "assistant" || item.role === "tool").length;
      clear(
        card,
        head,
        h("p", { class: "hint" }, "将对 " + steps + " 个模型步骤各发一次请求（约 " + formatCount(steps * 3000) + " tokens，按 $0.042/Mtok 约 $" + (steps * 3000 * 0.042 / 1e6).toFixed(4) + "）。"),
        h("button", { type: "button", class: "primary", onclick: () => analyze(false) }, "运行 Jev 分析")
      );
      return;
    }
    const summary = jev.summary || jev.steps.summary || {};
    const stepSummary = jev.steps.summary || {};
    const parts = [head];

    const shares = Object.entries(stepSummary.phase_share || {}).sort((a, b) => b[1] - a[1]);
    parts.push(
      h(
        "div",
        { class: "phase-bar", title: "阶段占比" },
        shares.map(([phase, share]) => h("span", { class: "k-" + phase, style: { flex: String(share) }, title: (PHASE_LABELS[phase] || phase) + " " + percent(share) }))
      ),
      h("p", { class: "hint" }, shares.map(([phase, share]) => (PHASE_LABELS[phase] || phase) + " " + percent(share)).join(" · "))
    );

    const work = Object.entries(stepSummary.work_counts || {}).sort((a, b) => b[1] - a[1]);
    if (work.length) {
      parts.push(h("p", { class: "hint" }, "改文件的原因：" + work.map(([key, count]) => (WORK_LABELS[key] || key) + " " + count).join(" · ")));
    }
    const reliance = Object.entries(stepSummary.harness_counts || {}).sort((a, b) => b[1] - a[1]);
    if (reliance.length) {
      parts.push(h("p", { class: "hint" }, "依赖 harness 的方式：" + reliance.map(([key, count]) => (RELIANCE_LABELS[key] || key) + " " + count + " 步").join(" · ")));
    }

    const task = jev.task || {};
    if (task.final_claim) {
      const consistency = state.revealed ? summary.consistency : null;
      parts.push(
        h(
          "div",
          { class: "jev-fact" },
          h("span", {}, "最终汇报"),
          h("strong", {}, CLAIMS[task.final_claim.choice] || task.final_claim.choice),
          h("small", {}, "置信 " + percent(task.final_claim.confidence) + (consistency === "overclaim" ? " · 但评分失败" : consistency === "underclaim" ? " · 但评分通过" : ""))
        )
      );
    }
    if (task.task_ambiguity) {
      parts.push(
        h(
          "div",
          { class: "jev-fact" },
          h("span", {}, "题目清晰度"),
          h("strong", {}, task.task_ambiguity.level.split(":")[0]),
          h("small", {}, "歧义分 " + task.task_ambiguity.score + "（0 清晰 · 1 含糊）")
        )
      );
    }

    const tail = run.continues ? null : stepSummary.polish_tail;
    if (run.continues && stepSummary.polish_tail) {
      parts.push(h("p", { class: "hint" }, "这是 episode 的中间分段，后面还有分段，打磨尾巴只在最后一段计算。"));
    }
    if (tail) {
      parts.push(
        h(
          "div",
          { class: "jev-fact" },
          h("span", {}, "打磨尾巴"),
          h(
            "strong",
            {},
            "#" + tail.start_step + " 起 " + tail.steps.length + " 步（" + percent(tail.step_share) + "）" +
              (tail.time_ms != null ? " · " + formatDuration(tail.time_ms) + (tail.time_share != null ? "（" + percent(tail.time_share) + " 时间）" : "") : "")
          ),
          h(
            "small",
            {},
            (tail.milestone_step ? "交付物在 #" + tail.milestone_step + " 已基本完成 · " : "") + "其中打磨 " + tail.polish_steps.length + " 步 ",
            h("button", { type: "button", class: "link-button", onclick: () => reader.highlight("Jev · 打磨尾巴", tail.steps) }, "高亮"),
            tail.milestone_step ? h("button", { type: "button", class: "link-button", onclick: () => reader.jump(tail.milestone_step) }, "跳到里程碑") : null
          )
        )
      );
    }

    const flagged = stepSummary.flagged || {};
    parts.push(
      h(
        "div",
        { class: "jev-questions" },
        Object.entries(QUESTION_LABELS).map(([key, [label, help]]) => {
          const unvalidated = (ctx.taxonomy.jev_unvalidated || []).includes(key);
          if (unvalidated) {
            // Failed the accuracy check: raw hits for reference, not flagged.
            const raw = stepSummary.unvalidated?.[key] || [];
            return h(
              "div",
              { class: "jev-question" },
              h("div", {}, h("strong", { class: "muted" }, label), h("small", {}, "未通过准确率验证：Jev 标出的步骤大多是误报，所以不标出、不进筛选和统计")),
              h("span", { class: "muted" }, raw.length ? "仅供参考 " + raw.length + " 步" : "—")
            );
          }
          const steps = flagged[key] || [];
          return h(
            "div",
            { class: "jev-question" + (steps.length ? " hit" : "") },
            h("div", {}, h("strong", {}, label), h("small", {}, help)),
            steps.length
              ? h(
                  "div",
                  { class: "step-links" },
                  h("button", { type: "button", class: "link-button", onclick: () => reader.highlight("Jev · " + label, steps) }, "高亮 " + steps.length),
                  steps.slice(0, 8).map((step) => h("button", { type: "button", class: "step-link", onclick: () => reader.jump(step) }, "#" + step))
                )
              : h("span", { class: "muted" }, "—")
          );
        })
      )
    );

    // Semantic search over steps.
    const input = h("input", { type: "search", placeholder: "用一句话找步骤，例如：它在哪一步决定跳过测试" });
    const results = h("div", { class: "find-results" });
    const form = h(
      "form",
      {
        class: "find-form",
        onsubmit: async (event) => {
          event.preventDefault();
          if (!input.value.trim()) return;
          clear(results, h("span", { class: "hint" }, "查找中…"));
          try {
            const found = await api.findSteps(run.id, input.value.trim());
            clear(
              results,
              found.matches.length
                ? found.matches.map((match) =>
                    h("button", { type: "button", class: "find-hit", onclick: () => reader.jump(match.step) }, h("strong", {}, "#" + match.step + " · " + percent(match.p)), h("span", {}, match.summary.slice(0, 160)))
                  )
                : h("span", { class: "hint" }, "没有匹配的步骤")
            );
          } catch (error) {
            clear(results, h("span", { class: "hint bad-text" }, error.message));
          }
        },
      },
      input,
      h("button", { type: "submit" }, "找")
    );
    parts.push(h("h3", { class: "mini-title" }, "语义定位"), form, results);
    parts.push(
      h(
        "p",
        { class: "hint" },
        (jev.steps.model || "jev") + " · " + formatCount((jev.steps.input_tokens || 0) + (task.input_tokens || 0)) + " tokens · " + (stepSummary.analyzed_steps || 0) + " 步" +
          (jev.steps.errors ? " · " + jev.steps.errors + " 步请求失败" : "") + " · 阈值 0.5 · ",
        h("button", { type: "button", class: "link-button", onclick: () => analyze(true) }, "重新分析")
      )
    );
    clear(card, parts);
  }

  render();
  // A step scan costs ~$0.002 and a few seconds, so run it on first open.
  if (ctx.jevAvailable && !state.jev?.steps) analyze(false);
  return { refresh: render };
}
