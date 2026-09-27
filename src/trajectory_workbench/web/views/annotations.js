import { api } from "../api.js";
import { h } from "../dom.js";

/**
 * Step annotations in the reader: notes under a step, an inline editor, and the Markdown
 * excerpt of annotated steps.
 */
export function createAnnotations(reader, trajectoryId, onChange) {
  const { ctx } = reader;
  let items = [];

  async function load() {
    items = (await api.annotations(trajectoryId)).items;
    onChange();
  }

  const steps = () => new Set(items.map((item) => item.step));

  function labelSelect(value) {
    const select = h(
      "select",
      { "aria-label": "标签（可选）" },
      h("option", { value: "" }, "不加标签"),
      (ctx.taxonomy?.labels || []).map((label) => h("option", { value: label.key }, label.label))
    );
    select.value = value || "";
    return select;
  }

  function labelText(key) {
    const found = (ctx.taxonomy?.labels || []).find((label) => label.key === key);
    return found ? found.label : key;
  }

  /** Notes of one step, rendered into the slot that sits under the message head. */
  function decorate(slot, step) {
    const nodes = items
      .filter((item) => item.step === step)
      .map((note) =>
        h(
          "div",
          { class: "step-note" },
          h("span", { class: "step-note-text" }, note.label ? h("b", {}, labelText(note.label) + " · ") : null, note.note),
          h(
            "span",
            { class: "step-note-actions" },
            h("button", { type: "button", class: "link-button", onclick: () => edit(slot, step, note) }, "改"),
            h(
              "button",
              {
                type: "button",
                class: "link-button",
                onclick: async () => {
                  await api.deleteAnnotation(trajectoryId, note.id);
                  await load();
                },
              },
              "删"
            )
          )
        )
      );
    // Replace the notes but leave an open editor where it is: moving it would drop the
    // keyboard focus of whoever is typing in it.
    const editor = slot.querySelector("form.step-note-editor");
    [...slot.children].forEach((child) => { if (child !== editor) child.remove(); });
    if (editor) editor.before(...nodes);
    else slot.append(...nodes);
  }

  function edit(slot, step, note = null) {
    slot.querySelector("form.step-note-editor")?.remove();
    // In compact mode the step is collapsed and its notes hidden: open it first.
    slot.closest("article")?.classList.add("expanded");
    const text = h("textarea", { rows: 3, placeholder: "这一步哪里好、哪里走偏、为什么" });
    text.value = note?.note || "";
    const label = labelSelect(note?.label);
    const status = h("span", { class: "save-status" });
    const form = h(
      "form",
      {
        class: "step-note-editor",
        onsubmit: async (event) => {
          event.preventDefault();
          if (!text.value.trim()) return;
          try {
            await api.saveAnnotation(trajectoryId, { id: note?.id, step, note: text.value, label: label.value || null });
            form.remove();
            await load();
          } catch (error) {
            status.textContent = error.message;
          }
        },
      },
      text,
      h("div", { class: "inline" }, label, h("button", { type: "submit", class: "primary" }, "保存批注"), h("button", { type: "button", class: "link-button", onclick: () => form.remove() }, "取消"), status)
    );
    slot.append(form);
    text.focus();
  }

  function exportPanel() {
    const range = h("input", { type: "text", placeholder: "步骤，如 3-9, 15（留空=批注过的步骤+转折点）" });
    const hide = h("input", { type: "checkbox", checked: ctx.blind });
    const status = h("span", { class: "save-status" });
    async function fetchText() {
      const response = await fetch(api.excerptUrl(trajectoryId, range.value.trim(), hide.checked));
      const body = await response.text();
      if (!response.ok) {
        let message = body;
        try {
          message = JSON.parse(body).error || body;
        } catch (_) {
          // plain text error
        }
        throw new Error(message);
      }
      return body;
    }
    const copy = h("button", { type: "button" }, "复制 Markdown");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(await fetchText());
        status.textContent = "已复制";
      } catch (error) {
        status.textContent = error.message;
      }
    });
    const download = h("button", { type: "button" }, "下载 .md");
    download.addEventListener("click", async () => {
      try {
        const blob = new Blob([await fetchText()], { type: "text/markdown" });
        const link = h("a", { href: URL.createObjectURL(blob), download: "trajectory-" + trajectoryId + ".md" });
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        status.textContent = "";
      } catch (error) {
        status.textContent = error.message;
      }
    });
    return h(
      "details",
      { class: "export-excerpt" },
      h("summary", {}, "导出片段"),
      h("div", { class: "export-excerpt-body" }, range, h("label", { class: "only-toggle" }, hide, "不写评分"), h("div", { class: "inline" }, copy, download, status))
    );
  }

  return { load, steps, decorate, edit, exportPanel, count: () => items.length };
}
