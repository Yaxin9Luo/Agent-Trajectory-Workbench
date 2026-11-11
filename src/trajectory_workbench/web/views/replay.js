import { api } from "../api.js";
import { chip, clear, h, persist, remember } from "../dom.js";
import { formatBytes, lineDiff } from "../presentation.mjs";
import { t } from "../i18n.mjs";

// The deck is laid out for this viewport; the stage scales it down to the panel width.
const STAGE = { width: 1920, height: 1080 };
const EVENT_LABELS = { change: t("改交付物"), look: t("看图"), render: t("渲染 / 截图"), support: t("改辅助文件") };
const OP_LABELS = { write: t("整份写入"), edit: t("编辑"), multiedit: t("多处编辑"), insert: t("插入"), patch: t("补丁"), shell: t("shell 写入"), script: t("脚本改写"), read: t("读回的内容"), delete: t("删除") };
const SEEN = {
  never: ["bad", t("之后没再看过图")],
  other: ["warn", t("之后看过图，但没有文件名指向这一页的")],
  slide: ["ok", t("之后看过这一页")],
};

/**
 * What the run built, step by step: the replayed versions of its deliverable rendered in a
 * sandboxed frame, a lane on the step bar for the design behaviours, and for a failed reward
 * check the step that last changed the text it points at. The server replays the transcript
 * (artifact.py); nothing is shown as exact unless the last version equals the kept file.
 */
export function createReplay({ trajectoryId, jump, onLoaded }) {
  const panel = h("section", { class: "panel replay-panel" });
  const lane = h("div", { class: "track-row design-row", "aria-label": t("设计行为") });
  const laneLegend = h("span", { class: "design-legend" });
  const state = { data: null, path: null, version: null, compare: false, shown: remember("reader.replay") === "1", error: null };
  const pendingMarks = [];

  const file = () => state.data?.files.find((entry) => entry.path === state.path) || null;
  const versionsByTool = () => {
    const map = new Map();
    const entry = state.data?.files.find((item) => item.path === state.data.main);
    (entry?.versions || []).forEach((version) => map.set(version.tool_id, version));
    return map;
  };

  async function load() {
    try {
      state.data = await api.artifacts(trajectoryId);
    } catch (error) {
      state.error = error.message;
    }
    if (state.data) {
      state.path = state.data.main || null;
      state.version = file()?.versions.length || null;
    }
    render();
    pendingMarks.splice(0).forEach(([box, tool]) => mark(box, tool));
    onLoaded?.();
  }

  /** On a call that made a version of the deliverable: a button that shows that version. */
  function mark(box, tool) {
    if (!state.data && !state.error) {
      pendingMarks.push([box, tool]);
      return;
    }
    const version = versionsByTool().get(tool.id);
    if (!version) return;
    box.append(
      h(
        "button",
        { type: "button", class: "link-button replay-mark", onclick: () => show(state.data.main, version.n) },
        t("▶ 看这一步之后的产物（第 {n} 版）", { n: version.n })
      )
    );
  }

  function show(path, version, { scroll = true } = {}) {
    state.path = path;
    state.version = version;
    state.shown = true;
    persist("reader.replay", "1");
    render();
    if (scroll) panel.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function trust(entry) {
    if (entry.final?.matches) return chip(t("最后一版与留存的最终产物一致"), "ok");
    if (entry.uncertain_from_step != null) {
      const note = [...entry.notes, ...entry.failed.map((item) => ({ step: item.step, note: item.reason }))].sort((a, b) => a.step - b.step)[0];
      return h("span", { class: "chip warn", title: note?.note || "" }, t("第 {step} 步起可能不准：有改动没能回放", { step: entry.uncertain_from_step }));
    }
    if (entry.final && !entry.final.matches) return h("span", { class: "chip warn", title: t("例如由生成脚本改写：这类改动不在可回放的调用里") }, t("最后一版和留存的最终产物不一致"));
    return chip(t("没有留存的最终产物可核对"), "muted");
  }

  function behaviourLine(counts) {
    if (!counts) return null;
    const facts = [
      t("改交付物 {n} 次（{steps} 步）", { n: counts.changes, steps: counts.change_steps }),
      t("渲染 / 截图 {n} 次", { n: counts.renders }),
      t("看图 {n} 次", { n: counts.looks }),
      t("看图后再改 {n} 轮", { n: counts.cycles }),
    ];
    const tail = !counts.changes
      ? null
      : !counts.looks
        ? chip(t("从没看过图"), "bad")
        : counts.changes_after_last_look
          ? chip(t("最后一次看图之后又改了 {n} 次，没再看", { n: counts.changes_after_last_look }), "warn")
          : chip(t("最后一次改动之后看过图"), "ok");
    return h("p", { class: "replay-facts" }, h("span", {}, facts.join(" · ")), tail);
  }

  function frame(src, label) {
    const box = h("div", { class: "replay-frame" });
    const inner = h("iframe", { sandbox: "allow-scripts", src, title: label, referrerpolicy: "no-referrer" });
    box.append(h("div", { class: "replay-frame-label" }, label), h("div", { class: "replay-scale" }, inner));
    // Scale the fixed-size page to the width it is given.
    const fit = () => {
      if (!box.clientWidth) return;
      const scale = box.clientWidth / STAGE.width;
      inner.style.transform = "scale(" + scale + ")";
      box.querySelector(".replay-scale").style.height = STAGE.height * scale + "px";
    };
    new ResizeObserver(fit).observe(box);
    // The observer reports the first size once the frame is laid out; these cover a panel
    // that is rendered while its tab is in the background.
    inner.addEventListener("load", fit);
    setTimeout(fit, 0);
    return box;
  }

  function stage(entry, version) {
    const url = (n) => api.artifactUrl(trajectoryId, entry.path, n);
    const isPage = /\.html?$/i.test(entry.path);
    if (!isPage) {
      const pre = h("pre", { class: "replay-source" }, t("读取…"));
      api.artifactText(trajectoryId, entry.path, version.n).then((text) => { pre.textContent = text; }).catch((error) => { pre.textContent = error.message; });
      return pre;
    }
    const frames = h("div", { class: "replay-frames" + (state.compare && version.n > 1 ? " two" : "") });
    if (state.compare && version.n > 1) frames.append(frame(url(version.n - 1), t("第 {n} 版（改之前）", { n: version.n - 1 })));
    frames.append(frame(url(version.n), t("第 {n} 版 · 第 {step} 步之后", { n: version.n, step: version.step })));
    return frames;
  }

  function sourceDiff(entry, version) {
    const slot = h("div", { class: "replay-diff" });
    const button = h(
      "button",
      {
        type: "button",
        class: "link-button",
        onclick: async () => {
          button.disabled = true;
          try {
            const [before, after] = await Promise.all([
              version.n > 1 ? api.artifactText(trajectoryId, entry.path, version.n - 1) : Promise.resolve(""),
              api.artifactText(trajectoryId, entry.path, version.n),
            ]);
            const diff = lineDiff(before, after, 6000);
            if (!diff) {
              clear(slot, h("span", { class: "muted" }, t("文件太长，不做逐行对比")));
              return;
            }
            // Changed lines with two lines of context on each side.
            const keep = diff.map((line, index) => line.op !== "=" || diff.slice(Math.max(0, index - 2), index + 3).some((near) => near.op !== "="));
            const lines = [];
            diff.forEach((line, index) => {
              if (!keep[index]) {
                if (index && keep[index - 1]) lines.push(h("span", { class: "diff-line same" }, "  ⋯\n"));
                return;
              }
              lines.push(h("span", { class: "diff-line " + (line.op === "+" ? "add" : line.op === "-" ? "del" : "same") }, (line.op === "=" ? " " : line.op) + " " + (line.text.length > 400 ? line.text.slice(0, 400) + "…" : line.text) + "\n"));
            });
            clear(slot, h("pre", { class: "diff" }, lines));
          } catch (error) {
            clear(slot, h("span", { class: "muted" }, error.message));
          } finally {
            button.disabled = false;
          }
        },
      },
      t("看这一版改了哪些源码行")
    );
    return h("div", {}, button, slot);
  }

  function render() {
    renderLane();
    const data = state.data;
    const head = h("div", { class: "panel-head" }, h("div", {}, h("h2", {}, t("产物回放")), h("p", {}, t("按轨迹里的写文件调用重建交付物的每一版，在沙箱里渲染。画面是活的：可以点、可以按方向键翻页。"))));
    if (state.error) return clear(panel, head, h("p", { class: "muted" }, state.error));
    if (!data) return clear(panel, head, h("p", { class: "muted" }, t("读取…")));
    const entry = file();
    if (!entry) {
      const kept = data.deliverables || [];
      return clear(
        panel,
        head,
        behaviourLine(data.behaviour),
        h(
          "p",
          { class: "muted" },
          data.files.length
            ? t("交付物不是通过可回放的写文件调用产生的（例如由生成脚本构建），所以没有中间版本。")
            : t("这条轨迹里没有写文件的调用。"),
          kept.length ? " " + t("留存的最终产物：") : "",
          kept.map((item) => h("a", { href: api.fileUrl(trajectoryId, item.path), target: "_blank", rel: "noopener noreferrer" }, " " + item.path.split("/").pop()))
        ),
        data.files.length ? fileList(data) : null
      );
    }
    const version = entry.versions[Math.min(entry.versions.length, Math.max(1, state.version || entry.versions.length)) - 1];
    const select = data.files.length > 1
      ? h(
          "select",
          { "aria-label": t("文件"), onchange: (event) => show(event.target.value, null, { scroll: false }) },
          data.files.map((item) => h("option", { value: item.path, selected: item.path === entry.path }, item.path.split("/").pop() + " · " + t("{n} 版", { n: item.versions.length }) + (item.deliverable ? "" : t("（辅助文件）"))))
        )
      : h("code", {}, entry.path);
    const strip = h(
      "div",
      { class: "replay-strip", role: "list" },
      entry.versions.map((item) =>
        h(
          "button",
          {
            type: "button",
            class: ["replay-version", item.n === version.n && "current", entry.uncertain_from_step != null && item.step >= entry.uncertain_from_step && "uncertain"].filter(Boolean).join(" "),
            title: t("第 {n} 版 · 第 {step} 步 · {op} · +{added} −{removed} 行", { n: item.n, step: item.step, op: OP_LABELS[item.op] || item.op, added: item.added, removed: item.removed }),
            onclick: () => show(entry.path, item.n, { scroll: false }),
          },
          "#" + item.step
        )
      )
    );
    const move = (delta) => show(entry.path, Math.min(entry.versions.length, Math.max(1, version.n + delta)), { scroll: false });
    const controls = h(
      "div",
      { class: "replay-controls" },
      h("button", { type: "button", disabled: version.n <= 1, onclick: () => move(-1) }, t("← 上一版")),
      h("button", { type: "button", disabled: version.n >= entry.versions.length, onclick: () => move(1) }, t("下一版 →")),
      h("span", { class: "replay-current" }, t("第 {n} / {total} 版 · 第 {step} 步 · {op} · +{added} −{removed} 行 · {size}", { n: version.n, total: entry.versions.length, step: version.step, op: OP_LABELS[version.op] || version.op, added: version.added, removed: version.removed, size: formatBytes(version.bytes) })),
      h("button", { type: "button", class: "link-button", onclick: () => jump(version.step) }, t("跳到这一步")),
      /\.html?$/i.test(entry.path)
        ? h("button", { type: "button", "aria-pressed": String(state.compare), disabled: version.n <= 1, onclick: () => { state.compare = !state.compare; render(); } }, t("和上一版并排"))
        : null,
      h("a", { class: "link-button", href: api.artifactUrl(trajectoryId, entry.path, version.n), target: "_blank", rel: "noopener noreferrer" }, t("新标签打开")),
      entry.final && !entry.final.matches
        ? h("a", { class: "link-button", href: api.fileUrl(trajectoryId, entry.final.path), target: "_blank", rel: "noopener noreferrer" }, t("打开留存的最终产物"))
        : null
    );
    const body = state.shown
      ? [stage(entry, version), sourceDiff(entry, version)]
      : [h("button", { type: "button", class: "primary", onclick: () => show(entry.path, version.n, { scroll: false }) }, t("渲染这一版"))];
    clear(panel, h("div", { class: "panel-head" }, head.firstChild, h("div", { class: "replay-file" }, select, trust(entry))), behaviourLine(data.behaviour), strip, controls, body);
  }

  function fileList(data) {
    return h("p", { class: "muted" }, t("可回放的其他文件："), data.files.map((item) => h("button", { type: "button", class: "link-button", onclick: () => show(item.path, null, { scroll: false }) }, " " + item.path.split("/").pop())));
  }

  /** One cell per step under the step bar: what the step did to the design. */
  function renderLane() {
    const data = state.data;
    lane.hidden = !data || !data.events.length;
    if (lane.hidden) return clear(laneLegend);
    const byStep = new Map();
    data.events.forEach((event) => byStep.set(event.step, [...(byStep.get(event.step) || []), event.kind]));
    const steps = data.behaviour.steps;
    const cells = [];
    for (let step = 1; step <= steps; step += 1) {
      const kinds = [...new Set(byStep.get(step) || [])];
      cells.push(
        h("button", {
          type: "button",
          class: "track-cell design-cell " + kinds.map((kind) => "ev-" + kind).join(" "),
          title: "#" + step + (kinds.length ? " · " + kinds.map((kind) => EVENT_LABELS[kind]).join(" + ") : ""),
          tabindex: kinds.length ? "0" : "-1",
          onclick: () => jump(step),
        })
      );
    }
    clear(lane, cells);
    clear(laneLegend, Object.entries(EVENT_LABELS).map(([kind, label]) => h("span", { class: "legend ev-" + kind }, label)));
  }

  /** For a failed reward check (by its position in reward.failed): where its text was last changed. */
  function checkNote(index) {
    const link = state.data?.checks?.[index];
    if (!link || link.seen === "untraceable") return null;
    const [tone, label] = SEEN[link.seen];
    const stepButton = (step) => h("button", { type: "button", class: "step-link", onclick: () => jump(step) }, "#" + step);
    return h(
      "div",
      { class: "check-link" },
      h("span", {}, link.last_edit_via === "style" ? t("它的样式最后改于") : t("这段文字最后改于")),
      stepButton(link.last_edit_step),
      h("button", { type: "button", class: "link-button", onclick: () => show(state.data.main, link.last_edit_version) }, t("看那一版")),
      h("span", { class: "chip " + tone, title: t("只看转录里有没有看图的调用，以及截图文件名里的页码；模型看了有没有看出来，要读那一步") }, label),
      (link.seen === "slide" ? link.slide_look_steps : link.look_steps).slice(0, 5).map(stepButton)
    );
  }

  render();
  return { panel, lane, laneLegend, load, mark, checkNote, show };
}
