import assert from "node:assert/strict";
import test from "node:test";

// A minimal DOM: enough to prove the helpers build nodes and never parse strings as HTML.
class Node {}
class Text extends Node {
  constructor(text) { super(); this.textContent = text; }
}
class Element extends Node {
  constructor(tag) {
    super();
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.className = "";
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  set innerHTML(_) { throw new Error("innerHTML must not be used"); }
  set textContent(value) { this.children = [new Text(String(value))]; }
  get textContent() { return this.children.map((child) => child.textContent).join(""); }
}
globalThis.Node = Node;
globalThis.document = {
  createElement: (tag) => new Element(tag),
  createTextNode: (text) => new Text(text),
};
globalThis.window = {};

const { chip, clear, h } = await import("../src/trajectory_workbench/web/dom.js");

test("h() keeps markup-looking transcript text as plain text", () => {
  const hostile = '<img src=x onerror="alert(1)">';
  const element = h("div", { class: "message-text", text: hostile });
  assert.equal(element.children.length, 1);
  assert.ok(element.children[0] instanceof Text);
  assert.equal(element.textContent, hostile);
});

test("h() maps props to attributes, dataset, style and listeners", () => {
  let clicked = false;
  const button = h(
    "button",
    { type: "button", "aria-pressed": "true", dataset: { step: 4 }, style: { width: "10%" }, onclick: () => { clicked = true; }, disabled: false, hidden: null },
    "#4",
    null,
    ["a", ["b"]]
  );
  assert.equal(button.attributes.type, "button");
  assert.equal(button.attributes["aria-pressed"], "true");
  assert.equal("disabled" in button.attributes, false);
  assert.equal(button.dataset.step, 4);
  assert.equal(button.style.width, "10%");
  button.listeners.click();
  assert.equal(clicked, true);
  assert.equal(button.textContent, "#4ab");
});

test("clear() replaces children and chip() carries its tone", () => {
  const parent = h("div", {}, "old");
  clear(parent, chip("Jev · 忽略报错", "jev"));
  assert.equal(parent.children.length, 1);
  assert.equal(parent.children[0].className, "chip jev");
  assert.equal(parent.textContent, "Jev · 忽略报错");
});
