import { api } from "../api.js";
import { clear, empty, h } from "../dom.js";
import { PHASE_LABELS, WORK_LABELS, claimConsistency, formatCount, formatDuration, percent, v4Status } from "../presentation.mjs";
import { t } from "../i18n.mjs";

const QUESTION_LABELS = {
  notices_problem: [t("发现问题"), t("推理或回复里说出了具体问题")],
  ignores_error: [t("忽略报错"), t("上一步工具报错，这一步当作成功继续")],
  misreads_observation: [t("误读工具返回"), t("对工具返回的描述与原文矛盾（只在返回完整可见时判断）")],
  thought_action_mismatch: [t("思行不一"), t("推理说要做 A，工具调用做了 B")],
  claims_done: [t("宣称完成"), t("对用户说任务已完成")],
  harness_reliance: [t("依赖 harness"), t("推理/回复/调用依赖 harness 额外加的东西：要用或解读 MCP/工具/Skill/Hook/harness 文件，或拿 harness 加的指令当理由")],
  filler: [t("空转/道歉"), t("主要是道歉、自我安慰、重复确认")],
  polish: [t("打磨"), t("改文件的步骤里，Jev 判为“对已能用的东西做可选美化”")],
  violates_constraint: [t("违反题目约束"), t("动作违反任务里写明的要求")],
};
const RELIANCE_LABELS = { uses_component: t("要用/解读 harness 组件"), cites_instruction: t("拿 harness 指令当理由"), unclear: t("方式不明确") };
const CLAIMS = { complete: t("声称全部完成"), partial: t("声称部分完成"), failed: t("报告失败/受阻"), asks_user: t("以提问结束"), none: t("没有最终汇报") };

// Above this many model steps (~$0.04 at 3000 tokens a step) the scan waits for a click.
const AUTO_SCAN_STEPS = 300;

function modelSteps(run) {
  return run.step_index.filter((item) => item.role === "assistant" || item.role === "tool").length;
}

export function renderJevPanel(slot, reader) {
  const { state, ctx } = reader;
  const run = state.run;
  const card = h("section", { class: "panel inspector-card jev-card" });
  slot.replaceChildren(card);
  const status = h("span", { class: "save-status" });

  // Every Jev request is billed: one at a time per panel. While one runs, the controls that
  // start another (marked data-jev-request) are disabled, and a second call gets the running one.
  let running = null;
  let findToken = 0;
  const lock = (on) => card.querySelectorAll("[data-jev-request]").forEach((element) => { element.disabled = on; });
  function once(work) {
    if (running) return running;
    lock(true);
    running = work().finally(() => {
      running = null;
      lock(false);
    });
    return running;
  }

  function analyze(force = false) {
    return once(async () => {
      status.textContent = t("Jev 逐步判断中…");
      try {
        const result = await api.analyze(run.id, force);
        reader.onJev(result);
        status.textContent = "";
        render();
      } catch (error) {
        status.textContent = error.message;
      }
    });
  }

  function render() {
    const jev = state.jev;
    const head = h(
      "div",
      { class: "panel-head" },
      h("div", {}, h("h2", {}, t("Jev 逐步分析")), h("p", {}, t("每一步单独判断（状态只含这一步和它看到的工具返回），计数与聚合在代码里做"))),
      status
    );
    if (!ctx.jevAvailable) {
      clear(card, head, empty(t("未配置 TYPESAFE_API_KEY，Jev 功能不可用")));
      return;
    }
    if (!jev?.steps) {
      const steps = modelSteps(run);
      clear(
        card,
        head,
        h("p", { class: "hint" }, t("将对 {steps} 个模型步骤各发一次请求（约 {tokens} tokens，按 $0.042/Mtok 约 ${cost}）。", { steps, tokens: formatCount(steps * 3000), cost: (steps * 3000 * 0.042 / 1e6).toFixed(4) })),
        h("button", { type: "button", class: "primary", dataset: { jevRequest: "" }, disabled: Boolean(running), onclick: () => analyze(false) }, t("运行 Jev 分析"))
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
        { class: "phase-bar", title: t("阶段占比") },
        shares.map(([phase, share]) => h("span", { class: "k-" + phase, style: { flex: String(share) }, title: (PHASE_LABELS[phase] || phase) + " " + percent(share) }))
      ),
      h("p", { class: "hint" }, shares.map(([phase, share]) => (PHASE_LABELS[phase] || phase) + " " + percent(share)).join(" · "))
    );

    const work = Object.entries(stepSummary.work_counts || {}).sort((a, b) => b[1] - a[1]);
    if (work.length) {
      parts.push(h("p", { class: "hint" }, t("改文件的原因：{list}", { list: work.map(([key, count]) => (WORK_LABELS[key] || key) + " " + count).join(" · ") })));
    }
    const reliance = Object.entries(stepSummary.harness_counts || {}).sort((a, b) => b[1] - a[1]);
    if (reliance.length) {
      parts.push(h("p", { class: "hint" }, t("依赖 harness 的方式：{list}", { list: reliance.map(([key, count]) => t("{label} {count} 步", { label: RELIANCE_LABELS[key] || key, count })).join(" · ") })));
    }

    const task = jev.task || {};
    if (task.final_claim) {
      const consistency = state.revealed ? claimConsistency(task.final_claim.choice, v4Status(run.v4_reward) || (run.outcome || {}).status) : null;
      parts.push(
        h(
          "div",
          { class: "jev-fact" },
          h("span", {}, t("最终汇报")),
          h("strong", {}, CLAIMS[task.final_claim.choice] || task.final_claim.choice),
          h("small", {}, t("置信 {p}", { p: percent(task.final_claim.confidence) }) + (consistency === "overclaim" ? " · " + t("但评分失败") : consistency === "underclaim" ? " · " + t("但评分通过") : ""))
        )
      );
    }
    if (task.task_ambiguity) {
      parts.push(
        h(
          "div",
          { class: "jev-fact" },
          h("span", {}, t("题目清晰度")),
          h("strong", {}, task.task_ambiguity.level.split(":")[0]),
          h("small", {}, t("歧义分 {score}（0 清晰 · 1 含糊）", { score: task.task_ambiguity.score }))
        )
      );
    }

    const tail = run.continues ? null : stepSummary.polish_tail;
    if (run.continues && stepSummary.polish_tail) {
      parts.push(h("p", { class: "hint" }, t("这是 episode 的中间分段，后面还有分段，打磨尾巴只在最后一段计算。")));
    }
    if (tail) {
      parts.push(
        h(
          "div",
          { class: "jev-fact" },
          h("span", {}, t("打磨尾巴")),
          h(
            "strong",
            {},
            t("#{start} 起 {count} 步（{share}）", { start: tail.start_step, count: tail.steps.length, share: percent(tail.step_share) }) +
              (tail.time_ms != null ? " · " + formatDuration(tail.time_ms) + (tail.time_share != null ? t("（{share} 时间）", { share: percent(tail.time_share) }) : "") : "")
          ),
          h(
            "small",
            {},
            (tail.milestone_step ? t("交付物在 #{step} 已基本完成", { step: tail.milestone_step }) + " · " : "") + t("其中打磨 {count} 步", { count: tail.polish_steps.length }) + " ",
            h("button", { type: "button", class: "link-button", onclick: () => reader.highlight(t("Jev · 打磨尾巴"), tail.steps) }, t("高亮")),
            tail.milestone_step ? h("button", { type: "button", class: "link-button", onclick: () => reader.jump(tail.milestone_step) }, t("跳到里程碑")) : null
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
              h("div", {}, h("strong", { class: "muted" }, label), h("small", {}, t("未通过准确率验证：Jev 标出的步骤大多是误报，所以不标出、不进筛选和统计"))),
              h("span", { class: "muted" }, raw.length ? t("仅供参考 {count} 步", { count: raw.length }) : "—")
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
                  h("button", { type: "button", class: "link-button", onclick: () => reader.highlight("Jev · " + label, steps) }, t("高亮 {count}", { count: steps.length })),
                  steps.slice(0, 8).map((step) => h("button", { type: "button", class: "step-link", onclick: () => reader.jump(step) }, "#" + step))
                )
              : h("span", { class: "muted" }, "—")
          );
        })
      )
    );

    // Semantic search over steps.
    const input = h("input", { type: "search", placeholder: t("用一句话找步骤，例如：它在哪一步决定跳过测试") });
    const results = h("div", { class: "find-results" });
    const form = h(
      "form",
      {
        class: "find-form",
        onsubmit: (event) => {
          event.preventDefault();
          const query = input.value.trim();
          if (!query || running) return;
          // Only the latest search may fill the results.
          const token = ++findToken;
          clear(results, h("span", { class: "hint" }, t("查找中…")));
          once(async () => {
            try {
              const found = await api.findSteps(run.id, query);
              if (token !== findToken) return;
              clear(
                results,
                found.matches.length
                  ? found.matches.map((match) =>
                      h("button", { type: "button", class: "find-hit", onclick: () => reader.jump(match.step) }, h("strong", {}, "#" + match.step + " · " + percent(match.p)), h("span", {}, match.summary.slice(0, 160)))
                    )
                  : h("span", { class: "hint" }, t("没有匹配的步骤"))
              );
            } catch (error) {
              if (token === findToken) clear(results, h("span", { class: "hint bad-text" }, error.message));
            }
          });
        },
      },
      input,
      h("button", { type: "submit", dataset: { jevRequest: "" }, disabled: Boolean(running) }, t("找"))
    );
    parts.push(h("h3", { class: "mini-title" }, t("语义定位")), form, results);
    parts.push(
      h(
        "p",
        { class: "hint" },
        (jev.steps.model || "jev") + " · " + formatCount((jev.steps.input_tokens || 0) + (task.input_tokens || 0)) + " tokens · " + t("{count} 步", { count: stepSummary.analyzed_steps || 0 }) +
          (jev.steps.errors ? " · " + t("{count} 步请求失败", { count: jev.steps.errors }) : "") + " · ",
        h("button", { type: "button", class: "link-button", dataset: { jevRequest: "" }, disabled: Boolean(running), onclick: () => analyze(true) }, t("重新分析"))
      )
    );
    clear(card, parts);
  }

  render();
  // A step scan of a short run costs ~$0.002 and a few seconds, so run it on first open.
  // A long run is one request per step with no cap: leave it to the button, which shows the cost.
  if (ctx.jevAvailable && !state.jev?.steps && modelSteps(run) <= AUTO_SCAN_STEPS) analyze(false);
  return { refresh: render };
}
