import { api, pollJob } from "./api.js";
import { clear, empty, h, persist, remember } from "./dom.js";
import { LANGUAGES, language, setLanguage, t, tb } from "./i18n.mjs";
import { formatCount } from "./presentation.mjs";
import { renderLibrary } from "./views/library.js";
import { renderCompareHome, renderPair } from "./views/pair.js";
import { renderQueue } from "./views/queue.js";
import { renderReader } from "./views/reader.js";
import { renderExplore } from "./views/explore.js";
import { renderRewrites } from "./views/rewrites.js";
import { renderSearch } from "./views/search.js";
import { renderStats } from "./views/stats.js";

export const ctx = {
  collections: [],
  collection: remember("collection", ""),
  blind: remember("blind", false),
  taxonomy: null,
  jevAvailable: false,
  queueNav: null,
  compareSelection: remember("compare", []),
  toggleCompare,
  refreshCollections,
  navigate,
  openImage,
};

const elements = {
  view: document.querySelector("#view"),
  collectionList: document.querySelector("#collection-list"),
  refresh: document.querySelector("#refresh-collections"),
  importForm: document.querySelector("#import-form"),
  importPath: document.querySelector("#import-path"),
  importCollection: document.querySelector("#import-collection"),
  importStatus: document.querySelector("#import-status"),
  blind: document.querySelector("#blind-mode"),
  modal: document.querySelector("#image-modal"),
};

function navigate(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, search = ""] = raw.split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  return { parts, params: new URLSearchParams(search) };
}

let routeToken = 0;
async function route() {
  const token = ++routeToken;
  const { parts, params } = parseHash();
  const page = parts[0] || "library";
  if (page !== "t" && page !== "queue") ctx.queueNav = null;
  document.querySelectorAll("[data-nav]").forEach((link) => {
    const current = page === "t" ? (ctx.queueNav ? "queue" : "library") : page === "pair" ? "compare" : page;
    link.classList.toggle("active", link.dataset.nav === current);
  });
  const target = h("div", { class: "view-root" });
  clear(elements.view, target);
  const still = () => token === routeToken;
  try {
    if (page === "t" && parts[1]) await renderReader(target, parts[1], ctx, still, params);
    else if (page === "pair" && parts[1] && parts[2]) await renderPair(target, parts[1], parts[2], ctx, still);
    else if (page === "compare") await renderCompareHome(target, ctx);
    else if (page === "queue") await renderQueue(target, ctx, params, still);
    else if (page === "stats") await renderStats(target, ctx, still);
    else if (page === "search") await renderSearch(target, ctx, params, still);
    else if (page === "explore") await renderExplore(target, ctx, params, still);
    else if (page === "rewrites") await renderRewrites(target, ctx, parts, params, still);
    else await renderLibrary(target, ctx, params, still);
  } catch (error) {
    if (still()) clear(target, h("div", { class: "panel error-panel" }, h("strong", {}, t("读取失败")), h("p", {}, error.message)));
  }
  if (still()) elements.view.scrollTop = 0;
}

async function refreshCollections() {
  const payload = await api.collections();
  ctx.collections = payload.collections;
  ctx.jevAvailable = payload.jev_available;
  if (ctx.collection && !ctx.collections.some((item) => item.collection === ctx.collection)) {
    ctx.collection = "";
    persist("collection", "");
  }
  renderCollections();
  return payload;
}

function renderCollections() {
  const total = ctx.collections.reduce((sum, item) => sum + item.total, 0);
  const episodes = ctx.collections.reduce((sum, item) => sum + (item.episodes ?? item.total), 0);
  const reviewed = ctx.collections.reduce((sum, item) => sum + item.reviewed, 0);
  const entries = [{ collection: "", total, episodes, reviewed, label: t("全部集合") }, ...ctx.collections];
  clear(
    elements.collectionList,
    ctx.collections.length
      ? entries.map((item) => {
          const button = h(
            "button",
            {
              type: "button",
              class: "run-item" + (item.collection === ctx.collection ? " active" : ""),
              // The name may be cut off by the sidebar width.
              title: item.label || item.collection,
              onclick: () => {
                ctx.collection = item.collection;
                persist("collection", item.collection);
                renderCollections();
                route();
              },
            },
            h("strong", {}, item.label || item.collection),
            h(
              "span",
              {},
              t("{n} 个 episode", { n: formatCount(item.episodes ?? item.total) }) +
                (item.episodes != null && item.episodes !== item.total ? t("（{n} 条）", { n: formatCount(item.total) }) : "") +
                t(" · 已读 {n}", { n: item.reviewed }) +
                (item.adapter_ids ? " · " + item.adapter_ids : "") +
                (item.pass != null && (item.pass || item.fail) ? t(" · 过 {pass} / 败 {fail}", { pass: item.pass, fail: (item.fail || 0) + (item.error || 0) }) : "")
            )
          );
          return button;
        })
      : h("div", { class: "empty-message dark" }, t("还没有导入轨迹"))
  );
}

/** Up to two trajectories picked anywhere in the app for side-by-side comparison. */
function toggleCompare(id) {
  const selection = ctx.compareSelection.filter((item) => item !== id);
  if (selection.length === ctx.compareSelection.length) selection.push(id);
  ctx.compareSelection = selection.slice(-2);
  persist("compare", ctx.compareSelection);
  renderCompareCount();
  return ctx.compareSelection.includes(id);
}

function renderCompareCount() {
  const badge = document.querySelector("#compare-count");
  if (badge) badge.textContent = ctx.compareSelection.length ? String(ctx.compareSelection.length) : "";
}

function openImage(src, alt = "") {
  const image = elements.modal.querySelector("img");
  image.src = src;
  image.alt = alt;
  elements.modal.classList.remove("hidden");
}

function closeImage() {
  elements.modal.classList.add("hidden");
}

elements.importForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = elements.importForm.querySelector("button[type=submit]");
  submit.disabled = true;
  elements.importStatus.classList.remove("error");
  elements.importStatus.textContent = t("发现并索引轨迹…");
  try {
    const job = await api.startImport(elements.importPath.value.trim(), elements.importCollection.value.trim());
    const finished = await pollJob(job.id, (state) => {
      elements.importStatus.textContent = t("已索引 {n} 条 · {message}", { n: state.done || 0, message: tb(state.message || "") });
    });
    if (finished.status === "failed") throw new Error(finished.error);
    const result = finished.result;
    elements.importStatus.textContent =
      t("完成：{n} 条轨迹，来自 {sources} 个来源", { n: result.trajectories, sources: result.sources }) +
      (result.errors.length ? t("；{n} 个来源读取失败", { n: result.errors.length }) : "");
    elements.importPath.value = "";
    elements.importCollection.value = "";
    ctx.collection = result.collection;
    persist("collection", result.collection);
    await refreshCollections();
    navigate("#/library");
  } catch (error) {
    elements.importStatus.classList.add("error");
    elements.importStatus.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

elements.refresh.addEventListener("click", async () => {
  try {
    await refreshCollections();
    route();
  } catch (error) {
    elements.importStatus.classList.add("error");
    elements.importStatus.textContent = error.message;
  }
});
/** The shell in index.html is written in Chinese; put it in the chosen language. */
function localizeShell() {
  document.documentElement.lang = language() === "zh" ? "zh-CN" : "en";
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    element.textContent = t(element.textContent.trim());
  });
  document.querySelectorAll("[data-i18n-attrs]").forEach((element) => {
    element.dataset.i18nAttrs.split(" ").forEach((name) => element.setAttribute(name, t(element.getAttribute(name))));
  });
  clear(
    document.querySelector("#language-switch"),
    Object.entries(LANGUAGES).map(([code, name]) =>
      h(
        "button",
        {
          type: "button",
          class: "quiet-button" + (code === language() ? " active" : ""),
          "aria-pressed": String(code === language()),
          onclick: () => {
            if (code === language()) return;
            setLanguage(code);
            // Label tables are built when the modules load, so a switch reloads the page.
            location.reload();
          },
        },
        name
      )
    )
  );
}

localizeShell();
elements.blind.checked = ctx.blind;
elements.blind.addEventListener("change", () => {
  ctx.blind = elements.blind.checked;
  persist("blind", ctx.blind);
  route();
});
elements.modal.querySelector("button").addEventListener("click", closeImage);
elements.modal.addEventListener("click", (event) => {
  if (event.target === elements.modal) closeImage();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeImage();
});
window.addEventListener("hashchange", route);
renderCompareCount();
// Taxonomy and collections load before the first view: show that something is coming.
clear(elements.view, empty(t("读取…")));

(async () => {
  try {
    ctx.taxonomy = await api.taxonomy();
    await refreshCollections();
  } catch (error) {
    elements.importStatus.classList.add("error");
    elements.importStatus.textContent = error.message;
  }
  if (!location.hash) {
    location.replace("#/library");
  }
  route();
})();
