import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.events = {};
    this.classList = { add() {}, remove() {} };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { this.events[name] = callback; }
  querySelector(selector) {
    this.selected ??= {};
    return this.selected[selector] ??= new Element(selector);
  }
}

function loadApp() {
  const elements = {};
  const document = {
    querySelector: (selector) => elements[selector] ??= new Element("div"),
    querySelectorAll: () => [],
    createElement: (tag) => new Element(tag),
    createTextNode: (text) => text,
    addEventListener() {},
  };
  const context = vm.createContext({
    document,
    listRuns: async () => ({ runs: [] }),
    formatDuration: (value) => String(value ?? "—"),
    shortSha: (value) => value || "—",
    runFileUrl: (_, path) => "/files/" + path,
  });
  const source = readFileSync(new URL("../src/trajectory_workbench/web/app.js", import.meta.url), "utf8");
  vm.runInContext(source.replace(/^import .*;$/gm, ""), context);
  return { context, elements };
}

function descendants(element, tagName) {
  return [
    ...(element.tagName === tagName ? [element] : []),
    ...element.children.flatMap((child) => typeof child === "object" ? descendants(child, tagName) : []),
  ];
}

const image = { media_type: "image/png", data: "cG5n" };

test("native tool images render beside the result and open in the existing image modal", () => {
  const { context, elements } = loadApp();
  const box = context.renderTool({
    name: "Slides Workbench", id: "wb-1", input: {},
    result: { text: "observation", is_error: false, images: [image] },
  });
  const images = descendants(box, "img");
  assert.equal(images.length, 1);
  assert.equal(images[0].src, "data:image/png;base64,cG5n");
  assert.equal(images[0].loading, "lazy");
  images[0].events.click();
  assert.equal(elements["#image-modal"].querySelector("img").src, images[0].src);
});

test("Workbench shows native images in preference to a legacy contact sheet", () => {
  const { context, elements } = loadApp();
  context.fixture = {
    tools: [{ id: "wb-1", result: { images: [image] } }],
    workbench: [{ tool_id: "wb-1", observation: { status: "completed" }, contact_sheet_relative_path: "old.png" }],
  };
  vm.runInContext("state.summary = fixture; renderWorkbench();", context);
  const images = descendants(elements["#workbench-list"], "img");
  assert.equal(images.length, 1);
  assert.equal(images[0].src, "data:image/png;base64,cG5n");
});

test("a Workbench result without recoverable images does not create an image element", () => {
  const { context, elements } = loadApp();
  context.fixture = {
    tools: [{ id: "wb-1", result: { images: [] } }],
    workbench: [{ tool_id: "wb-1", observation: { status: "completed" }, contact_sheet_relative_path: null }],
  };
  vm.runInContext("state.summary = fixture; renderWorkbench();", context);
  assert.equal(descendants(elements["#workbench-list"], "img").length, 0);
});

test("the terminal result exposes the original modelUsage JSON", () => {
  const { context, elements } = loadApp();
  const nativeResult = { type: "result", modelUsage: { "kimi-k3": { outputTokens: 12 } } };
  context.renderMessages([{
    id: "line-8", line_number: 8, role: "result", offset_ms: 8,
    text: "done", thinking: "", tools: [], native_result: nativeResult,
  }]);
  const json = descendants(elements["#messages"], "pre").map((item) => item.textContent);
  assert.deepEqual(json, [JSON.stringify(nativeResult, null, 2)]);
});

test("MoH prompt records render separately without inventing native message identities or times", () => {
  const { context, elements } = loadApp();
  const system = "# Harness extension\n<img src=x onerror=alert(1)>";
  const user = "# 用户任务\r\n制作两页。\r\n";
  context.fixture = {
    model_prompt: {
      scope: "moh_model_prompt_v1", native_system_complete: false,
      source: "attempts/001/records/request.json",
      system: { text: system, status: "records_match", record_path: "attempts/001/records/system_prompt.md", sha256: "abc" },
      user: { text: user, status: "records_match", record_path: "attempts/001/records/stdin.txt", sha256: "def" },
    },
  };
  vm.runInContext("state.summary = fixture;", context);
  context.renderMessages([{
    id: "line-3", line_number: 3, role: "assistant", offset_ms: 3000,
    text: "native reply", thinking: "", tools: [],
  }]);

  const panel = descendants(elements["#messages"], "section");
  assert.equal(panel.length, 1);
  assert.deepEqual(descendants(panel[0], "pre").map((item) => item.textContent), [system, user]);
  const labels = descendants(panel[0], "summary").map((item) => item.textContent).join("\n");
  assert.match(labels, /MoH system 扩展/);
  assert.match(labels, /MoH user 输入/);
  const notes = descendants(panel[0], "p").map((item) => item.textContent).join("\n");
  assert.match(notes, /不包含 Claude Code 的完整原生 system/);
  assert.match(notes, /request\.json/);
  assert.match(notes, /system_prompt\.md/);
  assert.match(notes, /stdin\.txt/);
  assert.equal(descendants(panel[0], "img").length, 0);
  assert.equal(descendants(panel[0], "article").length, 0);
  const native = descendants(elements["#messages"], "article");
  assert.equal(native.length, 1);
  assert.equal(native[0].id, "line-3");
  assert.equal(descendants(native[0], "span")[0].textContent, "#3");
});

test("unmatched or missing prompt evidence stays visibly unverified even with no native messages", () => {
  const { context, elements } = loadApp();
  context.fixture = {
    model_prompt: {
      scope: "moh_model_prompt_v1", native_system_complete: false,
      source: "attempts/001/records/request.json",
      system: { text: "request system", status: "mismatch", record_path: "system_prompt.md", sha256: "abc" },
      user: { text: "request user", status: "unverified", record_path: "stdin.txt", sha256: "def" },
    },
  };
  vm.runInContext("state.summary = fixture;", context);
  context.renderMessages([]);

  const panel = descendants(elements["#messages"], "section");
  assert.equal(panel.length, 1);
  const labels = descendants(panel[0], "summary").map((item) => item.textContent).join("\n");
  assert.match(labels, /不可验证：记录不一致/);
  assert.match(labels, /未验证：记录或摘要缺失/);
  assert.doesNotMatch(labels, /记录与摘要一致/);
});
