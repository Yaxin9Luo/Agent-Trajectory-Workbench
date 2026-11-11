// Two interface languages. The Chinese text in the source is the key; `locales/en.mjs` maps it
// to English. A text with no English entry shows in Chinese, so a missing entry never breaks a view.
// Only interface text goes through here: trajectory content is never translated.
import en from "./locales/en.mjs";
import { BACKEND_PATTERNS } from "./locales/en/backend.mjs";

export const LANGUAGES = { zh: "中文", en: "English" };
const STORAGE_KEY = "atw:lang";

function detect() {
  try {
    const stored = JSON.parse(globalThis.window?.localStorage?.getItem(STORAGE_KEY) ?? "null");
    if (stored in LANGUAGES) return stored;
  } catch (_) {
    // No storage (private window, Node): fall through to the browser language.
  }
  // Outside a browser (Node tests) the source language is used.
  const browser = (globalThis.window && globalThis.navigator?.language) || "zh";
  return browser.toLowerCase().startsWith("zh") ? "zh" : "en";
}

let current = detect();

export function language() {
  return current;
}

/** Tests and the language switch use this; the switch reloads the page afterwards. */
export function setLanguage(lang, { store = true } = {}) {
  if (!(lang in LANGUAGES)) return;
  current = lang;
  if (!store) return;
  try {
    globalThis.window?.localStorage?.setItem(STORAGE_KEY, JSON.stringify(lang));
  } catch (_) {
    // The choice just does not stick.
  }
}

// {name} is replaced by a parameter. {name|one|many} picks a word by that parameter's count:
// "{n} {n|step|steps}" gives "1 step" and "3 steps". English tables use it; Chinese needs none.
function fill(template, params) {
  if (!params) return template;
  return template.replace(/\{(\w+)(?:\|([^|{}]*)\|([^{}]*))?\}/g, (whole, name, one, many) => {
    if (!(name in params)) return whole;
    if (one === undefined) return String(params[name]);
    return String(params[name]).trim() === "1" ? one : many;
  });
}

/** Interface text. `source` is the Chinese text, with {name} placeholders filled from `params`. */
export function t(source, params) {
  const template = current === "en" && Object.hasOwn(en, source) ? en[source] : source;
  return fill(template, params);
}

/**
 * Text the server wrote in Chinese (labels, flag details, queue reasons), some of it stored in the
 * index at import time. Exact entries first, then patterns for the sentences that carry numbers
 * or names. Anything unknown is returned as it came.
 */
export function tb(text) {
  if (current !== "en" || typeof text !== "string" || !text) return text;
  if (Object.hasOwn(en, text)) return en[text];
  for (const [pattern, render] of BACKEND_PATTERNS) {
    const match = pattern.exec(text);
    if (match) return render(match, tb);
  }
  return text;
}
