import { api } from "../api.js";
import { chip, clear, debounce, h } from "../dom.js";
import { HUMAN_LABELS, OUTCOME_LABELS, graderAgreement } from "../presentation.mjs";
import { t, tb } from "../i18n.mjs";

/**
 * The reading record. Order follows the reading checklist: first commit to a guess
 * (blind), then judge whether the outcome is real, then locate the turning point, name
 * the failure, and write a hypothesis that can be tested with a concrete intervention.
 */
export function renderReviewPanel(slot, reader) {
  const { state, ctx } = reader;
  const taxonomy = ctx.taxonomy;
  const run = state.run;
  const card = h("section", { class: "panel inspector-card review-card" });
  slot.replaceChildren(card);
  let review = { labels: [], ...(state.review || {}) };
  const status = h("span", { class: "save-status" });

  // Text typed but not yet sent (the debounce below): a save from another control returns
  // the server's older text, which must not replace what is on screen.
  const unsent = new Set();
  const saveNow = async (fields) => {
    Object.keys(fields).forEach((key) => unsent.delete(key));
    Object.assign(review, fields);
    status.textContent = t("保存中…");
    try {
      const saved = await api.saveReview(run.id, { ...fields, blind: ctx.blind || review.blind });
      const typed = Object.fromEntries([...unsent].map((key) => [key, review[key]]));
      review = { ...review, ...saved, ...typed };
      reader.onReview(review);
      status.textContent = t("已保存 {time}", { time: new Date().toLocaleTimeString() });
    } catch (error) {
      status.textContent = t("保存失败：{error}", { error: error.message });
    }
  };
  const saveLater = debounce(saveNow, 600);

  function segmented(options, value, onPick, className = "") {
    return h(
      "div",
      { class: "segmented " + className },
      options.map(([key, label]) =>
        h("button", { type: "button", "aria-pressed": String(value === key), onclick: () => onPick(value === key ? null : key) }, label)
      )
    );
  }

  function textarea(field, placeholder, rows = 2) {
    const element = h("textarea", { rows, placeholder });
    element.value = review[field] || "";
    element.addEventListener("input", () => {
      // Keep local state current so a re-render from another control keeps the text.
      review[field] = element.value;
      unsent.add(field);
      saveLater({ [field]: element.value });
    });
    return element;
  }

  function render() {
    const outcome = run.outcome || {};
    const parts = [];
    parts.push(
      h(
        "div",
        { class: "panel-head" },
        h("div", {}, h("h2", {}, t("阅读记录")), h("p", {}, t("读出来的东西要能变成指标或评分器修补"))),
        status
      )
    );

    // 0. Blind prediction.
    if (ctx.blind || review.predicted_status) {
      parts.push(
        h(
          "div",
          { class: "review-block" + (!state.revealed ? " focus" : "") },
          h("label", {}, t("① 盲判：不看评分，你觉得这条评分器会怎么判？")),
          segmented(
            [["pass", t("通过")], ["partial", t("部分")], ["fail", t("失败")]],
            review.predicted_status,
            async (value) => {
              await saveNow({ predicted_status: value });
              if (value && !state.revealed) reader.reveal();
              render();
            }
          ),
          state.revealed && review.predicted_status
            ? h(
                "p",
                { class: "hint " + (review.predicted_status === outcome.status ? "ok-text" : "bad-text") },
                t("你猜 {guess}，评分器：{grader}", { guess: OUTCOME_LABELS[review.predicted_status] || review.predicted_status, grader: OUTCOME_LABELS[outcome.status] || t("无评分") }) +
                  (review.predicted_status === outcome.status ? " ✓" : " ✗")
              )
            : null
        )
      );
    }
    if (!state.revealed) {
      parts.push(h("p", { class: "hint" }, t("先读轨迹、给出判断，评分和评分相关的信号会在判断后显示。")));
      clear(card, parts);
      return;
    }

    // 1. Outcome is real?
    const agreement = graderAgreement(outcome.status, review.human_status);
    parts.push(
      h(
        "div",
        { class: "review-block" },
        h("label", {}, t("② 结果名副其实吗？评分器：{grader}", { grader: OUTCOME_LABELS[outcome.status] || t("无评分") })),
        segmented(Object.entries(HUMAN_LABELS), review.human_status, (value) => { saveNow({ human_status: value }); render(); }),
        agreement ? h("p", { class: "hint " + (agreement.key === "agree" ? "ok-text" : "bad-text") }, agreement.label) : null
      )
    );

    // 2. Turning point.
    const stepInput = h("input", { type: "number", min: 1, value: review.turning_step ?? "", placeholder: t("步号") });
    stepInput.addEventListener("change", () => saveNow({ turning_step: stepInput.value ? Number(stepInput.value) : null }).then(render));
    const candidates = state.jev?.summary?.turning_candidates || [];
    parts.push(
      h(
        "div",
        { class: "review-block" },
        h("label", {}, t("③ 转折点：从“还在正轨”变成“走偏”的那一步")),
        h(
          "div",
          { class: "inline" },
          stepInput,
          review.turning_step ? h("button", { type: "button", class: "link-button", onclick: () => reader.jump(review.turning_step) }, t("跳到 #{step}", { step: review.turning_step })) : h("span", { class: "hint" }, t("也可在步骤上点“设为转折点”"))
        ),
        candidates.length
          ? h("div", { class: "chips" }, h("span", { class: "hint" }, t("Jev 候选：")), candidates.slice(0, 5).map((item) => h("button", { type: "button", class: "chip jev", onclick: () => reader.jump(item.step), title: item.reasons.map(tb).join(t("、")) }, "#" + item.step + " " + tb(item.reasons[0]))))
          : null,
        textarea("turning_note", t("一句话：为什么在这一步走偏（理解错题 / 没读懂工具返回 / 没验证就往下走 / 忘了早期约束…）"))
      )
    );

    // 3. Labels.
    const selected = new Set(review.labels || []);
    const groups = {};
    taxonomy.labels.forEach((item) => (groups[item.group] ||= []).push(item));
    const custom = [...selected].filter((key) => !taxonomy.labels.some((item) => item.key === key));
    const customInput = h("input", { type: "text", placeholder: t("自定义标签，回车添加") });
    customInput.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || !customInput.value.trim()) return;
      event.preventDefault();
      selected.add(customInput.value.trim());
      saveNow({ labels: [...selected] }).then(render);
    });
    const suggestionBox = h("div", { class: "suggestions" });
    parts.push(
      h(
        "div",
        { class: "review-block" },
        h("label", {}, t("④ 失败类型标签（可多选）")),
        Object.entries(groups).map(([group, items]) =>
          h(
            "div",
            { class: "label-group" },
            h("span", { class: "label-group-name" }, tb(group)),
            items.map((item) =>
              h(
                "button",
                {
                  type: "button",
                  class: "label-toggle",
                  title: item.definition,
                  "aria-pressed": String(selected.has(item.key)),
                  onclick: () => {
                    selected.has(item.key) ? selected.delete(item.key) : selected.add(item.key);
                    saveNow({ labels: [...selected] }).then(render);
                  },
                },
                tb(item.label)
              )
            )
          )
        ),
        custom.length ? h("div", { class: "chips" }, custom.map((key) => chip(tb(taxonomy.legacy_labels?.[key]) || key, "label"))) : null,
        customInput,
        labelDetails(selected),
        suggestionBox
      )
    );

    // Severity and whether the problem decided the outcome, per selected label.
    function labelDetails(selectedKeys) {
      if (!selectedKeys.size) return null;
      const details = { ...(review.label_details || {}) };
      const name = (key) => tb(taxonomy.labels.find((item) => item.key === key)?.label || taxonomy.legacy_labels?.[key]) || key;
      const update = (key, change) => {
        details[key] = { ...(details[key] || {}), ...change };
        saveNow({ label_details: details }).then(render);
      };
      return h(
        "div",
        { class: "label-details" },
        h("span", { class: "hint" }, t("每个标签：严重度 · 是否决定了结果")),
        [...selectedKeys].map((key) =>
          h(
            "div",
            { class: "label-detail" },
            h("span", {}, name(key)),
            h(
              "span",
              { class: "segmented small" },
              Object.entries(taxonomy.severities || {}).map(([value, label]) =>
                h("button", { type: "button", "aria-pressed": String(details[key]?.severity === value), onclick: () => update(key, { severity: details[key]?.severity === value ? null : value }) }, tb(label))
              )
            ),
            h(
              "label",
              { class: "only-toggle" },
              h("input", { type: "checkbox", checked: Boolean(details[key]?.decisive), onchange: (event) => update(key, { decisive: event.target.checked }) }),
              t("决定性")
            )
          )
        )
      );
    }

    // 4. Hypothesis and intervention.
    parts.push(
      h(
        "div",
        { class: "review-block" },
        h("label", {}, t("⑤ 可验证的假设：模型需要学会什么？")),
        textarea("hypothesis", t("例：模型在工具报错后不读 stderr 就重试；在 SFT 里加入“读报错→改参数”的片段，预期同类任务重试次数下降"), 3),
        h("label", { class: "sub" }, t("修复手段")),
        segmented(Object.entries(taxonomy.interventions).map(([key, label]) => [key, tb(label)]), review.intervention, (value) => { saveNow({ intervention: value }); render(); }, "wrap"),
        h("label", { class: "sub" }, t("归因")),
        segmented(Object.entries(taxonomy.attributions).map(([key, label]) => [key, tb(label)]), review.attribution, (value) => { saveNow({ attribution: value }); render(); }, "wrap")
      )
    );

    // 5. Optional correction and note.
    parts.push(
      h(
        "details",
        { class: "review-block", open: Boolean(review.correction || review.note) },
        h("summary", {}, t("⑥ 修正续写 / 备注（可选）")),
        h("label", { class: "sub" }, t("在转折点之后，正确的下一步应该是：")),
        textarea("correction", t("转折点那一步本该怎么做（配合转折点导出成 SFT 样本和 DPO 对；可写纯文本，或 {\"content\": …, \"tool_calls\": […]}）"), 4),
        h("label", { class: "sub" }, t("备注")),
        textarea("note", t("其他观察，例如环境问题要去修"), 2)
      )
    );

    // Jev assist.
    const suggestButton = h("button", { type: "button", class: "secondary", disabled: !ctx.jevAvailable }, t("Jev：根据我写的内容建议标签"));
    suggestButton.addEventListener("click", async () => {
      suggestButton.disabled = true;
      clear(suggestionBox, h("span", { class: "hint" }, t("Jev 判断中…")));
      try {
        const result = await api.suggest(run.id, { note: review.note || "", turning_note: review.turning_note || "", hypothesis: review.hypothesis || "" });
        const labelChips = result.labels
          .filter((item) => !selected.has(item.label))
          .map((item) => {
            const found = taxonomy.labels.find((entry) => entry.key === item.label);
            return h("button", { type: "button", class: "chip jev", onclick: () => { selected.add(item.label); saveNow({ labels: [...selected] }).then(render); } }, "+ " + (tb(found?.label) || item.label) + " " + Math.round(item.p * 100) + "%");
          });
        const pick = (field, value, labels) =>
          value && value.choice !== "none" && value.choice !== "unclear" && review[field] !== value.choice
            ? h("button", { type: "button", class: "chip jev", onclick: () => { saveNow({ [field]: value.choice }); render(); } }, tb(labels[value.choice]) + " " + Math.round(value.confidence * 100) + "%")
            : null;
        clear(
          suggestionBox,
          labelChips.length ? labelChips : h("span", { class: "hint" }, t("没有新的标签建议")),
          pick("intervention", result.intervention, taxonomy.interventions),
          pick("attribution", result.attribution, taxonomy.attributions)
        );
      } catch (error) {
        clear(suggestionBox, h("span", { class: "hint bad-text" }, error.message));
      } finally {
        suggestButton.disabled = !ctx.jevAvailable;
      }
    });
    parts.push(h("div", { class: "review-actions" }, suggestButton, h("span", { class: "hint" }, t("先写转折原因或假设，再让 Jev 归类"))));
    clear(card, parts);
  }

  render();
  return {
    setTurning(step) {
      if (!state.revealed) {
        status.textContent = t("先完成盲判");
        return;
      }
      saveNow({ turning_step: step }).then(render);
      card.scrollIntoView({ behavior: "smooth", block: "start" });
    },
  };
}
