import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { language, setLanguage, t, tb } from "../src/trajectory_workbench/web/i18n.mjs";
import en, { TABLES } from "../src/trajectory_workbench/web/locales/en.mjs";

const WEB = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "trajectory_workbench", "web");
const SOURCES = [
  "app.js", "api.js", "dom.js", "presentation.mjs",
  ...readdirSync(join(WEB, "views")).filter((name) => name.endsWith(".js")).map((name) => "views/" + name),
];
const CJK = /[㐀-鿿]/;
const read = (name) => readFileSync(join(WEB, name), "utf-8");

/** Every string literal of a source: [{quote, text, before}], `before` is the code just ahead of it. */
function literals(source) {
  const out = [];
  const pattern = /(["'`])((?:\\[\s\S]|(?!\1)[^\\])*)\1/g;
  // Comments would otherwise be read as code; none of the sources has a literal containing "//".
  const code = source.replace(/\/\*[\s\S]*?\*\//g, (block) => " ".repeat(block.length)).replace(/(^|[^:"'`\\])\/\/.*$/gm, (line, lead) => lead + " ".repeat(line.length - lead.length));
  for (const match of code.matchAll(pattern)) {
    out.push({ quote: match[1], text: match[2], before: code.slice(Math.max(0, match.index - 4), match.index) });
  }
  return out;
}

/** The Chinese texts a source passes to t(), unescaped. */
function translatedKeys(source) {
  return literals(source).filter((item) => /\bt\($/.test(item.before) && item.quote === '"').map((item) => JSON.parse('"' + item.text + '"'));
}

// Chinese literals that are data or logic, not interface text.
const NOT_INTERFACE = {
  "presentation.mjs": ["行", "字"], // foldText's unit, translated where it is shown
};

test("t() falls back to the Chinese source and fills placeholders in both languages", () => {
  assert.equal(language(), "zh");
  assert.equal(t("轨迹库"), "轨迹库");
  assert.equal(t("（{n} 条）", { n: 3 }), "（3 条）");
  setLanguage("en", { store: false });
  try {
    assert.equal(t("轨迹库"), "Library");
    assert.equal(t("（{n} 条）", { n: 3 }), " (3 trajectories)");
    assert.equal(t("没有翻译的文本 {n}", { n: 1 }), "没有翻译的文本 1");
    assert.equal(t("{missing} 保留"), "{missing} 保留");
  } finally {
    setLanguage("zh", { store: false });
  }
});

test("every Chinese literal in the interface code goes through t()", () => {
  for (const name of SOURCES) {
    const allowed = new Set(NOT_INTERFACE[name] || []);
    const loose = literals(read(name)).filter((item) => CJK.test(item.text) && !/\bt\($/.test(item.before) && !allowed.has(item.text));
    assert.deepEqual(loose.map((item) => item.text), [], name + " has Chinese text outside t()");
  }
});

test("every t() text has an English entry with the same placeholders", () => {
  const placeholders = (text) => [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
  for (const name of SOURCES) {
    for (const key of translatedKeys(read(name))) {
      assert.ok(Object.hasOwn(en, key), name + ": no English for " + JSON.stringify(key));
    }
  }
  for (const [key, value] of Object.entries(en)) {
    assert.equal(typeof value, "string");
    assert.deepEqual(placeholders(value), placeholders(key), "placeholders differ for " + JSON.stringify(key));
    assert.ok(!CJK.test(value), "English entry still has Chinese: " + JSON.stringify(key));
  }
});

test("the tables do not give one text two translations, and have no dead entries", () => {
  const seen = new Map();
  for (const [table, entries] of Object.entries(TABLES)) {
    for (const [key, value] of Object.entries(entries)) {
      if (seen.has(key)) assert.equal(value, seen.get(key).value, JSON.stringify(key) + " differs between " + seen.get(key).table + " and " + table);
      else seen.set(key, { table, value });
    }
  }
  const used = new Set(SOURCES.flatMap((name) => translatedKeys(read(name))));
  const shell = read("index.html");
  for (const [table, entries] of Object.entries(TABLES)) {
    if (table === "backend") continue; // keyed by server text, checked by tests/test_i18n_backend.py
    for (const key of Object.keys(entries)) {
      assert.ok(used.has(key) || (table === "app" && shell.includes(key)), table + ": unused entry " + JSON.stringify(key));
    }
  }
});

test("every marked text in index.html has an English entry", () => {
  const shell = read("index.html");
  const texts = [...shell.matchAll(/data-i18n>([^<]+)</g)].map((match) => match[1].trim());
  assert.ok(texts.length >= 12);
  for (const tag of shell.matchAll(/<[^>]*data-i18n-attrs="([^"]+)"[^>]*>/g)) {
    for (const attribute of tag[1].split(" ")) texts.push(new RegExp(attribute + '="([^"]*)"').exec(tag[0])[1]);
  }
  for (const text of texts) assert.ok(Object.hasOwn(en, text), "no English for " + JSON.stringify(text));
  // Chinese text in the shell that is not marked would stay Chinese.
  const unmarked = shell.replace(/<[^>]*data-i18n[^>]*>[^<]*/g, "").match(/[㐀-鿿]+/g);
  assert.equal(unmarked, null);
});

test("tb() translates server sentences that carry numbers and names", () => {
  assert.equal(tb("同一调用连续 4 次"), "同一调用连续 4 次");
  setLanguage("en", { store: false });
  try {
    const cases = {
      "原地打转": "Stuck in a loop",
      "同一调用连续 4 次": "Same call 4 times in a row",
      "2 次报错后以相同参数重试": "2 retries with the same arguments after an error",
      "改动了已有的 tests/test_a.py, grade.py": "Changed existing tests/test_a.py, grade.py",
      "重复段落 3 · 道歉/自我确认 1": "Repeated paragraphs 3 · apology / self-confirmation 1",
      "2/9 段正文换成另一种语言": "2/9 text segments switch to another language",
      "命令 pytest -q": "command pytest -q",
      "命令查看了 parse.py": "a command looked at parse.py",
      "工具 mcp__browser__screenshot": "tool mcp__browser__screenshot",
      "约 300,000 tokens，超过 256,000": "About 300,000 tokens, over 256,000",
      "3 个工具调用没有返回": "3 tool calls have no result",
      "训练 token 只占 4.2%": "Trained tokens are only 4.2%",
      "工具 slides_workbench（调用 3 步 · 提到 1 步）": "Tool slides_workbench (calls 3 steps · mentions 1 steps)",
      "训练 token 里用到 harness 组件：MCP slides（调用 2 步）、Skill deck-review（提到 1 步） 等 7 个":
        "Trained tokens use harness components: MCP slides (calls 2 steps), Skill deck-review (mentions 1 steps) and others, 7 in total",
      "评分通过，但 步数 4，成功样本 P10 为 5；改完未验证；Jev：宣称完成前没有验证":
        "Passed grading, but 4 steps; P10 of passing samples is 5; Not verified after the change; Jev: claimed done before verifying",
      "失败类型：agent（本集合 12+ 条）": "Failure type: agent (12+ in this collection)",
      "同题不同 checkpoint：ckpt-100 vs ckpt-500": "Same task, different checkpoints: ckpt-100 vs ckpt-500",
      "流水线状态：failed（timeout）": "Pipeline status: failed (timeout)",
      "2 处改动判错": "2 changes judged wrong",
      "3 处带警告（adds_status_words、drops_negation）的改动还没判定": "3 changes with a warning (adds_status_words, drops_negation) not judged yet",
      "人工标为不进：归因没删干净": "Marked by a person as exclude: 归因没删干净",
    };
    for (const [source, expected] of Object.entries(cases)) assert.equal(tb(source), expected);
    // Unknown text, and anything that is not text, comes back as it was.
    assert.equal(tb("用户自己写的批注"), "用户自己写的批注");
    assert.equal(tb(null), null);
    assert.equal(tb(""), "");
  } finally {
    setLanguage("zh", { store: false });
  }
});

test("every web module parses as an ES module", () => {
  // The browser is the only place these files load; a syntax error there blanks the whole page.
  const locales = readdirSync(join(WEB, "locales", "en")).map((name) => "locales/en/" + name);
  for (const name of [...SOURCES, "i18n.mjs", "locales/en.mjs", ...locales]) {
    const result = spawnSync(process.execPath, ["--input-type=module", "--check"], { input: read(name), encoding: "utf-8" });
    assert.equal(result.status, 0, name + "\n" + result.stderr);
  }
});
