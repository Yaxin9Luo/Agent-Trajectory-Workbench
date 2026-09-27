import { api } from "../api.js";
import { chip, clear, details, empty, h } from "../dom.js";
import { formatCount, percent } from "../presentation.mjs";

const ADOPTION = { acts_on: ["据此行动", "ok"], acknowledges: ["只提了一句", "warn"], rejects: ["有理由地否决", "muted"], ignores: ["没理会", "bad"], unclear: ["看不出", "muted"] };
const HOOK = { acts: ["照做了", "ok"], acknowledges: ["只是回应", "warn"], ignores: ["没理会", "bad"], nothing_asked: ["无具体要求", "muted"] };
const PLAN_STATUS = { completed: ["完成", "ok"], in_progress: ["进行中", "warn"], pending: ["未开始", "muted"], removed: ["被删掉", "bad"] };

/**
 * Ledgers tab: requirements, compactions, delegations, plans and hook reminders.
 * Loaded when the tab is first opened; Jev judgments are an explicit extra step.
 */
export function renderLedgerPanel(slot, reader, episode) {
  const { ctx } = reader;
  const trajectoryId = reader.state.run.id;
  const status = h("span", { class: "save-status" });
  let loaded = false;

  async function load(runJev = false) {
    status.textContent = runJev ? "Jev 判断中…" : "读取中…";
    try {
      const ledgers = runJev ? await api.runLedgers(trajectoryId, true) : await api.ledgers(trajectoryId);
      status.textContent = "";
      render(ledgers);
    } catch (error) {
      status.textContent = error.message;
    }
  }

  const stepLink = (step) => h("button", { type: "button", class: "step-link", onclick: () => reader.jump(step) }, "#" + step);

  function section(title, help, body) {
    return h("section", { class: "panel inspector-card ledger" }, h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, title), help ? h("p", {}, help) : null)), body);
  }

  function render(ledgers) {
    const parts = [];
    const head = h(
      "div",
      { class: "ledger-head" },
      ledgers.jev
        ? h("span", { class: "muted" }, "已用 Jev 判断 · " + formatCount(ledgers.input_tokens) + " tokens")
        : h("span", { class: "muted" }, "下面只有代码抽取的条目；需求筛选、处理步骤、压缩保留、委派采纳、Hook 响应需要 Jev"),
      ctx.jevAvailable ? h("button", { type: "button", class: ledgers.jev ? "link-button" : "primary", onclick: () => load(true) }, ledgers.jev ? "重新判断" : "用 Jev 分析台账") : null,
      status
    );
    parts.push(head);

    // Requirements.
    const requirements = ledgers.requirements || [];
    const compactions = ledgers.compactions || [];
    parts.push(
      section(
        "需求台账",
        ledgers.jev
          ? "题面里被判为要求的句子；处理步骤是 Jev 在全程步骤摘要里找到的最相关一步（线索，不是结论）"
          : "题面拆出的候选句（还没区分要求和背景）" + (ledgers.task_is_wrapped ? " · 题面是 JSON 包装，已分开用户请求和 harness 规则" : ""),
        requirements.length
          ? h(
              "div",
              { class: "ledger-list" },
              requirements.map((item, index) => {
                const lost = compactions.map((c, k) => (c.dropped || []).includes(index) ? k + 1 : null).filter(Boolean);
                return h(
                  "div",
                  { class: "ledger-row" },
                  h("div", { class: "ledger-text" }, item.text),
                  h(
                    "div",
                    { class: "ledger-meta" },
                    !ledgers.jev ? null : item.step ? h("span", {}, "处理于 ", stepLink(item.step), h("small", {}, " " + percent(item.p_step))) : chip("没找到处理步骤", "warn"),
                    (item.p_harness_conflict || 0) >= 0.5 ? chip("与 harness 规则冲突 " + percent(item.p_harness_conflict), "bad") : null,
                    lost.length ? chip("第 " + lost.join("、") + " 次压缩后摘要里没了", "bad") : null
                  )
                );
              })
            )
          : empty("题面里没有拆出候选句")
      )
    );

    // Compactions.
    if (compactions.length) {
      parts.push(
        section(
          "压缩审计",
          "每次上下文压缩：触发方式、压缩前后大小、摘要长度，以及摘要里丢了哪些需求",
          h(
            "div",
            { class: "ledger-list" },
            compactions.map((item, index) =>
              h(
                "div",
                { class: "ledger-row" },
                h("div", { class: "ledger-text" }, "第 " + (index + 1) + " 次 · ", stepLink(item.step), " · " + (item.trigger || "?")),
                h(
                  "div",
                  { class: "ledger-meta" },
                  item.pre_tokens != null ? h("span", {}, formatCount(item.pre_tokens) + " → " + formatCount(item.post_tokens) + " tokens") : null,
                  h("span", { class: "muted" }, item.summary_chars ? "摘要 " + formatCount(item.summary_chars) + " 字" : "转录里没有摘要"),
                  item.checked ? (item.dropped.length ? chip("丢了 " + item.dropped.length + " 条需求", "bad") : chip("需求都还在", "ok")) : null
                )
              )
            )
          )
        )
      );
    }

    // Delegations.
    const delegations = ledgers.delegations || [];
    if (delegations.length) {
      const children = new Map((episode?.subagents || []).filter((sub) => sub.spawn).map((sub) => [sub.spawn.call_id, sub]));
      parts.push(
        section(
          "委派台账",
          "派给子代理的任务 → 子代理的汇报 → 父代理接下来有没有据此行动",
          h(
            "div",
            { class: "ledger-list" },
            delegations.map((item) => {
              const child = children.get(item.call_id);
              const adoption = item.adoption ? ADOPTION[item.adoption.choice] : null;
              return h(
                "div",
                { class: "ledger-row" },
                h("div", { class: "ledger-text" }, stepLink(item.step), " ", h("b", {}, item.subagent_type || item.tool), " ", item.description || ""),
                h(
                  "div",
                  { class: "ledger-meta" },
                  item.result_step ? h("span", {}, "汇报于 ", stepLink(item.result_step)) : chip("没收到汇报", "warn"),
                  adoption ? chip(adoption[0] + " " + percent(item.adoption.confidence), adoption[1]) : null,
                  child ? h("a", { href: "#/t/" + encodeURIComponent(child.id) }, "打开子代理轨迹 →") : null
                ),
                item.brief ? details("派出的任务", item.brief, false) : item.brief_encrypted ? h("small", { class: "muted" }, "任务说明已加密（Codex）") : null,
                item.result ? details("子代理汇报", item.result, false) : null
              );
            })
          )
        )
      );
    }

    // Plans.
    const plans = ledgers.plans || [];
    if (plans.length) {
      const open = plans.filter((item) => item.status !== "completed" && item.status !== "removed").length;
      parts.push(
        section(
          "计划 / Todo",
          plans.length + " 项" + (open ? " · 结束时还有 " + open + " 项没完成" : " · 全部完成或删除"),
          h(
            "div",
            { class: "ledger-list" },
            plans.map((item) => {
              const [label, tone] = PLAN_STATUS[item.status] || [item.status || "?", "muted"];
              return h(
                "div",
                { class: "ledger-row" },
                h("div", { class: "ledger-text" }, item.text),
                h(
                  "div",
                  { class: "ledger-meta" },
                  chip(label, tone),
                  h("span", {}, "建于 ", stepLink(item.created_step)),
                  item.completed_step ? h("span", {}, "完成于 ", stepLink(item.completed_step)) : null
                )
              );
            })
          )
        )
      );
    }

    // Hook reminders.
    const hooks = ledgers.hooks || [];
    if (hooks.length) {
      parts.push(
        section(
          "Hook 提醒响应",
          "harness 自动注入的提醒，下一步有没有回应",
          h(
            "div",
            { class: "ledger-list" },
            hooks.map((item) => {
              const response = item.response ? HOOK[item.response.choice] : null;
              return h(
                "div",
                { class: "ledger-row" },
                h("div", { class: "ledger-text" }, stepLink(item.step), " ", item.text.replace(/<\/?system-reminder>/g, "").trim().slice(0, 220)),
                h("div", { class: "ledger-meta" }, response ? chip(response[0] + " " + percent(item.response.confidence), response[1]) : null, item.next_steps?.length ? h("span", {}, "下一步 ", stepLink(item.next_steps[0])) : null)
              );
            })
          )
        )
      );
    }
    clear(slot, parts);
  }

  return {
    open() {
      if (loaded) return;
      loaded = true;
      clear(slot, empty("读取台账…"));
      load(false);
    },
  };
}
