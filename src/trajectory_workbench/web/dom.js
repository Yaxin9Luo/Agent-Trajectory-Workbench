/** Tiny DOM helpers. All text goes through textContent; nothing is parsed as HTML. */

export function h(tag, props = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") element.className = value;
    else if (key === "text") element.textContent = value;
    else if (key === "style" && typeof value === "object") Object.assign(element.style, value);
    else if (key === "dataset") Object.assign(element.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") element.addEventListener(key.slice(2), value);
    else if (value === true) element.setAttribute(key, "");
    else element.setAttribute(key, String(value));
  }
  append(element, children);
  return element;
}

export function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function clear(element, ...children) {
  element.replaceChildren();
  return append(element, children);
}

export function chip(text, tone = "", props = {}) {
  return h("span", { class: "chip " + tone, ...props }, text);
}

export function details(summary, content, open = false) {
  const block = h("details", { open }, h("summary", {}, summary));
  if (content instanceof Node) block.append(content);
  else block.append(h("pre", {}, content ?? ""));
  return block;
}

export function empty(text) {
  return h("div", { class: "empty-message" }, text);
}

export function debounce(fn, wait) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

const store = (() => {
  try {
    return window.localStorage;
  } catch (_) {
    return null;
  }
})();

export function remember(key, fallback) {
  try {
    const value = store?.getItem("atw:" + key);
    return value == null ? fallback : JSON.parse(value);
  } catch (_) {
    return fallback;
  }
}

export function persist(key, value) {
  try {
    store?.setItem("atw:" + key, JSON.stringify(value));
  } catch (_) {
    // Private windows may refuse storage; the setting just does not stick.
  }
}
