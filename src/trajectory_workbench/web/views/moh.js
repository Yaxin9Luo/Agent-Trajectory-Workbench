import { api } from "../api.js";
import { clear, details, empty, h } from "../dom.js";
import { formatBytes, formatClock, formatDuration, shortSha, terminalCards } from "../presentation.mjs";

/** Panels that only exist for MoH runs: terminal chain, artifact states, Workbench, inputs. */
export function renderMohPanels(side, run, trajectoryId, ctx) {
  side.append(terminalPanel(run), artifactPanel(run, trajectoryId), workbenchPanel(run, trajectoryId, ctx));
  if (run.model_prompt?.scope === "moh_model_prompt_v1") side.append(promptPanel(run.model_prompt));
}

function card(title, subtitle, ...body) {
  return h(
    "section",
    { class: "panel inspector-card" },
    h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, title), h("p", {}, subtitle))),
    ...body
  );
}

function terminalPanel(run) {
  const cards = terminalCards(run).map((item) =>
    h("div", { class: "terminal-node " + item.state }, h("strong", {}, item.title), h("span", {}, item.detail))
  );
  return card(
    "MoH 终止链路",
    "生成成功不等于 Runtime 已交付",
    h("div", { class: "terminal-flow" }, cards[0], h("div", { class: "terminal-arrow" }, "→"), cards[1], h("div", { class: "terminal-arrow" }, "→"), cards[2])
  );
}

function artifactPanel(run, trajectoryId) {
  const states = run.artifact_states || [];
  const diffs = h("div", { class: "diff-list" }, empty("读取 diff…"));
  api.artifactDiffs(trajectoryId).then(
    (result) =>
      clear(
        diffs,
        result.diffs.length
          ? result.diffs.map((diff) =>
              h(
                "div",
                { class: "diff-row" },
                h("span", {}, formatClock(diff.from_offset_ms) + " → " + formatClock(diff.to_offset_ms)),
                h("code", {}, diff.from_sha + " → " + diff.to_sha),
                h("span", {}, diff.unavailable ? "内容不可读" : "+" + diff.added + " / −" + diff.removed + " 行")
              )
            )
          : empty("artifact.html 内容没有变化")
      ),
    (error) => clear(diffs, empty("读取失败：" + error.message))
  );
  return card(
    "artifact.html",
    states.length + " 个唯一状态（按 SHA 去重）",
    h(
      "div",
      { class: "artifact-states" },
      states.map((item) =>
        h("div", { class: "artifact-state", title: item.artifact_sha256 }, h("span", {}, formatClock(item.received_offset_ms)), h("span", {}, shortSha(item.artifact_sha256)), h("span", {}, formatBytes(item.size_bytes)))
      )
    ),
    diffs
  );
}

function workbenchPanel(run, trajectoryId, ctx) {
  const calls = run.workbench || [];
  if (!calls.length) return card("Slides Workbench", "这次运行没有调用");
  return card(
    "Slides Workbench",
    calls.length + " 次调用 · 观察证据与 artifact SHA",
    calls.map((call, index) => {
      const observation = call.observation || {};
      const body = [
        h("div", { class: "workbench-head" }, h("strong", {}, "#" + (index + 1)), h("span", {}, formatClock(call.offset_ms) + " · " + (observation.status || "unparsed"))),
      ];
      if (observation.error_code || observation.message) body.push(h("div", { class: "workbench-raw" }, [observation.error_code, observation.message].filter(Boolean).join(" · ")));
      if (call.contact_sheet_relative_path) {
        const src = api.fileUrl(trajectoryId, call.contact_sheet_relative_path);
        body.push(h("img", { loading: "lazy", src, alt: "contact sheet", onclick: () => ctx.openImage(src, "contact sheet") }));
      }
      if (call.observation) {
        const diagnostics = observation.diagnostics || {};
        body.push(
          h(
            "div",
            { class: "workbench-meta" },
            h("div", {}, h("strong", {}, formatDuration(observation.timings_ms?.total)), "耗时"),
            h("div", {}, h("strong", {}, String(diagnostics.returned_count ?? "—") + " / " + String(diagnostics.total_count ?? "—")), "诊断"),
            h("div", {}, h("strong", {}, shortSha(observation.artifact_sha256)), "artifact")
          )
        );
      } else {
        body.push(h("div", { class: "workbench-raw" }, call.raw_result || "无法解析"));
      }
      return h("article", { class: "workbench-card" }, body);
    })
  );
}

function promptPanel(prompt) {
  const parts = [["system", "MoH system 扩展"], ["user", "MoH user 输入"]].map(([role, label]) => {
    const part = prompt[role];
    const status = part.status === "records_match" ? "记录与摘要一致" : part.status === "mismatch" ? "记录不一致" : "未验证";
    return details(label + " · " + status, part.text);
  });
  return card("MoH 输入记录", "来源 " + prompt.source + "；不含 Claude Code 原生 system", ...parts);
}
