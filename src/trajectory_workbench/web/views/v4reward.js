import { api } from "../api.js";
import { chip, details, h } from "../dom.js";
import { cropGeometry, formatReward, groupByLayer, parseViewport } from "../presentation.mjs";
import { t } from "../i18n.mjs";

const SEVERITY_TONE = { fatal: "bad", severe: "bad", major: "warn", normal: "warn", minor: "muted", invalid: "muted" };
const STAGE_ORDER = ["L0-L1", "L2", "L3"];

/** The slides-v4 RL reward of this trajectory's deck (scorer output, matched by task id + path). */
export function renderV4Reward(reward, { checkNote } = {}) {
  const stillUrl = (relative) => api.v4StillUrl(reward.label, reward.task_id, relative);
  const stillFor = (viewport, slide) => {
    const list = reward.stills?.[viewport] || [];
    const name = String(slide).padStart(2, "0") + ".";
    return list.find((path) => path.split("/").pop().startsWith(name)) || null;
  };
  const masked = reward.masked || reward.R === null || reward.R === undefined;
  const numbers = [
    ["R", formatReward(reward.R, masked)],
    ["tier", reward.tier ?? "—"],
    ["a_L3", reward.a_L3 ?? "—"],
    ["m", reward.m ?? "—"],
    ["q", reward.q ?? "—"],
    [t("页"), reward.slides ?? "—"],
  ];
  const items = [...(reward.failed || []), ...(reward.derived_items || []).map((item) => ({ ...item, derived: true }))];
  // A failed check is known to the replay by its position in reward.failed.
  const failedIndex = new Map((reward.failed || []).map((item, index) => [item, index]));
  return h(
    "section",
    { class: "panel v4-reward" },
    h(
      "div",
      { class: "panel-head" },
      h(
        "div",
        {},
        h("span", { class: "eyebrow" }, t("v4 reward · RL 奖励 · {label}", { label: reward.label })),
        h(
          "h2",
          {},
          masked ? t("masked（不是 0）") : "R " + formatReward(reward.R),
          reward.tier_name ? " · tier " + (reward.tier ?? "—") + " " + reward.tier_name : ""
        ),
        h("p", {}, ["scorer " + (reward.scorer || "—"), reward.rubric, reward.reward_path].filter(Boolean).join(" · "))
      ),
      h("dl", { class: "task-numbers" }, numbers.map(([label, value]) => h("div", {}, h("dt", {}, label), h("dd", {}, String(value)))))
    ),
    masked ? renderUnknown(reward.unknown) : null,
    renderRunInfo(reward),
    items.length
      ? groupByLayer(items).map((group) =>
          h(
            "div",
            { class: "v4-layer" },
            h("h3", {}, t("{layer} · {count} 项未通过", { layer: group.layer, count: group.items.length })),
            group.items.map((item) => renderItem(item, stillFor, stillUrl, checkNote?.(failedIndex.get(item))))
          )
        )
      : h("p", { class: "muted" }, t("没有未通过的检查项")),
    renderStills(reward, stillUrl),
    reward.critique ? details(t("L3 评审原文 critique"), reward.critique) : null
  );
}

function renderUnknown(unknown) {
  const list = Array.isArray(unknown) ? unknown : [];
  return h(
    "div",
    { class: "v4-unknown" },
    h("strong", {}, t("R 为空（masked）：这条不计入奖励，不能当 0 分。原因：")),
    list.length
      ? h("ul", {}, list.map((entry) => h("li", {}, typeof entry === "string" ? entry : Object.entries(entry || {}).map(([k, v]) => k + "=" + (typeof v === "string" ? v : JSON.stringify(v))).join(" · "))))
      : h("span", { class: "muted" }, t(" 打分器没有给出原因"))
  );
}

function renderRunInfo(reward) {
  const timing = reward.timing || {};
  const stages = STAGE_ORDER.filter((key) => typeof timing[key] === "number").map((key) => key + " " + timing[key].toFixed(1) + "s");
  const sub = Object.entries(timing.stages || {}).map(([key, value]) => key + " " + Number(value).toFixed(1) + "s");
  const problems = [];
  if (reward.failed_stage) problems.push(chip(t("失败阶段 {stage}", { stage: reward.failed_stage }), "bad"));
  if (timing.retried) problems.push(chip(t("有重试"), "warn"));
  if (timing.L3_dropped) problems.push(chip(t("L3 被丢弃"), "warn"));
  (Array.isArray(reward.deck_fault) ? reward.deck_fault : reward.deck_fault ? [reward.deck_fault] : []).forEach((fault) =>
    problems.push(chip("deck fault " + (typeof fault === "string" ? fault : JSON.stringify(fault)), "bad"))
  );
  return h(
    "div",
    { class: "v4-run" },
    h("span", {}, t("总耗时 {time}", { time: typeof reward.seconds === "number" ? reward.seconds.toFixed(1) + "s" : "—" })),
    stages.length ? h("span", { class: "muted" }, stages.join(" · ")) : null,
    sub.length ? h("small", { class: "muted" }, t("L0-L1 内：{stages}", { stages: sub.join(" · ") })) : null,
    problems.length ? h("span", { class: "chips" }, problems) : h("span", { class: "chip ok" }, t("无报错 / 重试")),
    reward.env ? h("small", { class: "muted" }, Object.entries(reward.env).map(([k, v]) => k + " " + (typeof v === "string" ? v : JSON.stringify(v))).join(" · ")) : null
  );
}

function renderItem(item, stillFor, stillUrl, note) {
  const evidence = typeof item.evidence === "string" ? { note: item.evidence } : item.evidence || {};
  const viewport = evidence.viewport || "1920x1080";
  const slides = Array.isArray(item.slides) ? item.slides : [];
  const boxes = (evidence.boxes || []).filter((entry) => Array.isArray(entry?.box));
  const still = slides.length ? stillFor(viewport, slides[0]) : null;
  const crops = still && boxes.length ? boxes.slice(0, 3).map((entry) => renderCrop(stillUrl(still), viewport, entry)) : [];
  return h(
    "div",
    { class: "v4-item" },
    h(
      "div",
      { class: "ledger-meta" },
      chip(item.severity || "?", SEVERITY_TONE[item.severity] || "muted"),
      h("code", {}, item.id || "?"),
      item.derived ? chip("derived", "muted") : null,
      slides.length ? h("small", {}, t("第 {pages} 页", { pages: slides.join(t("、")) })) : null
    ),
    evidence.note ? h("div", { class: "ledger-text" }, evidence.note) : null,
    crops.length ? h("div", { class: "v4-crops" }, crops) : boxes.length ? h("small", { class: "muted" }, t("（没有 {viewport} 的截图）", { viewport })) : null,
    note || null
  );
}

/** The evidence box cut out of the still (box plus margin), and a thumbnail marking it. */
function renderCrop(url, viewport, entry) {
  const geometry = cropGeometry(entry.box, viewport);
  if (!geometry) return null;
  const [vw, vh] = parseViewport(viewport);
  const background = 'url("' + url + '")';
  const crop = h(
    "div",
    {
      class: "v4-crop",
      title: entry.text || entry.role || "",
      style: {
        width: geometry.width + "px",
        height: geometry.height + "px",
        backgroundImage: background,
        backgroundSize: geometry.backgroundWidth + "px " + geometry.backgroundHeight + "px",
        backgroundPosition: geometry.offsetX + "px " + geometry.offsetY + "px",
      },
    },
    h("span", {
      class: "v4-box",
      style: { left: geometry.box.left + "px", top: geometry.box.top + "px", width: geometry.box.width + "px", height: geometry.box.height + "px" },
    })
  );
  const pct = geometry.percent;
  const thumb = h(
    "a",
    { class: "v4-thumb", href: url, target: "_blank", rel: "noopener noreferrer", style: { backgroundImage: background, aspectRatio: vw + " / " + vh } },
    h("span", { class: "v4-box", style: { left: pct.left + "%", top: pct.top + "%", width: pct.width + "%", height: pct.height + "%" } })
  );
  return h("figure", { class: "v4-figure" }, crop, thumb, entry.text ? h("figcaption", {}, entry.text.slice(0, 120)) : null);
}

function renderStills(reward, stillUrl) {
  const list = reward.stills?.["1920x1080"] || [];
  if (!list.length) return null;
  const strip = h(
    "div",
    { class: "v4-stills" },
    list.map((path) =>
      h(
        "a",
        { href: stillUrl(path), target: "_blank", rel: "noopener noreferrer", title: path },
        h("img", { src: stillUrl(path), alt: t("第 {page} 页", { page: parseInt(path.split("/").pop(), 10) }), loading: "lazy" }),
        h("small", {}, String(parseInt(path.split("/").pop(), 10)))
      )
    )
  );
  return details(t("逐页截图 1920x1080 · {count} 页", { count: list.length }), strip, true);
}
