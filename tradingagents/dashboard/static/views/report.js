// Report (#/run/<id>/<section>): run header, rating history, grouped sections, and the reader.

import { getReport } from "../api.js";
import {
  describeChange, el, evidenceNode, formatDate, horizonLabel, isCostCarried, link, money, RATING_ORDER, runHref,
  slugify, valuationLabel, verdictTag,
} from "../format.js";
import { renderMarkdown } from "../markdown.js";
import { hasStructuredAnalyst, renderAnalyst } from "../research.js";
import { renderTargets } from "../targets.js";
import { errorPanel, loadingState } from "./shared.js";

const ANALYST_SECTION = "Analyst expectations";
// [indexer section name, display label]
const SECTION_GROUPS = [
  ["Decision", [["Final decision"], ["Trader"]]],
  ["Debate", [["Bull case"], ["Bear case"], ["Research manager"]]],
  ["Risk", [["Aggressive risk", "Aggressive"], ["Conservative risk", "Conservative"], ["Neutral risk", "Neutral"]]],
  ["Analysts", [["Market"], ["Fundamentals"], ["News"], ["Sentiment"]]],
  ["Evidence", [["Evidence coverage", "Coverage"], [ANALYST_SECTION], ["Research brief"]]],
  ["Full", [["Complete report"]]],
];
const KNOWN_SECTIONS = new Set(SECTION_GROUPS.flatMap(([, entries]) => entries.map(([name]) => name)));

export function sectionGroups(run) {
  const available = new Set(run.sections || []);
  const structured = hasStructuredAnalyst(run);
  const entry = (name, label = name) => ({
    name, label, slug: slugify(name), structured: name === ANALYST_SECTION && structured,
  });
  const groups = SECTION_GROUPS
    .map(([title, entries]) => ({
      title,
      entries: entries
        .filter(([name]) => available.has(name) || (name === ANALYST_SECTION && structured))
        .map(([name, label]) => entry(name, label)),
    }))
    .filter((group) => group.entries.length);
  const extra = (run.sections || []).filter((name) => !KNOWN_SECTIONS.has(name)).map((name) => entry(name));
  if (extra.length) groups.push({ title: "More", entries: extra });
  return groups;
}

export function renderReport(root, ctx, route) {
  const page = el("div");
  root.replaceChildren(page);
  if (!ctx.data) {
    const pad = el("div", "view-pad");
    pad.append(ctx.runsError
      ? errorPanel("Couldn't load the research archive", ctx.runsError, ctx.retry)
      : loadingState("Loading the research archive…"));
    page.append(pad);
    return {};
  }
  const run = ctx.runsById.get(route.runId);
  if (!run) {
    const pad = el("div", "view-pad");
    const panel = el("section", "panel");
    panel.append(el("h2", "heading", "Run not found"),
      el("p", "", "This report may have been removed or re-indexed since the link was made."),
      link("#/", "link-action", "‹ Back to positions"));
    pad.append(panel);
    page.append(pad);
    return {};
  }

  const summary = (ctx.data.tickers || []).find((item) => item.ticker === run.ticker);
  const history = summary?.history?.some((item) => item.run_id === run.id)
    ? summary.history
    : [{ run_id: run.id, analysis_date: run.analysis_date, decision: run.decision, rating: run.rating, evidence_only: run.is_evidence_only }];
  const position = history.findIndex((item) => item.run_id === run.id);
  const previousRated = history.slice(0, position).reverse().find((item) => item.rating in RATING_ORDER) || null;

  page.append(header(run, previousRated));
  const strip = historyStrip(history, position, ctx);
  page.append(strip.element);

  const groups = sectionGroups(run);
  const entries = groups.flatMap((group) => group.entries);
  const defaultEntry = entries.find((item) => item.name === "Final decision") || entries[0] || null;

  const body = el("div", "report-body");
  const nav = el("nav", "section-nav");
  nav.setAttribute("aria-label", "Report sections");
  const links = new Map();
  groups.forEach((group) => {
    const wrap = el("div", "section-group");
    wrap.append(el("p", "eyebrow", group.title));
    group.entries.forEach((item) => {
      const anchor = link(runHref(run.id, item.slug), "section-link", item.label);
      links.set(item.slug, anchor);
      wrap.append(anchor);
    });
    nav.append(wrap);
  });
  const reader = el("article", "reader");
  const inner = el("div", "reader-inner");
  const title = el("h2", "reader-title");
  const content = el("div");
  inner.append(title, content);
  reader.append(inner);
  body.append(nav, reader);
  page.append(body);

  let loadToken = 0;
  const showSection = (slug) => {
    const item = entries.find((candidate) => candidate.slug === slug) || defaultEntry;
    if (!item) {
      title.textContent = "No sections";
      content.replaceChildren(el("p", "muted", "This run has no readable report sections."));
      return;
    }
    const canonical = runHref(run.id, item.slug);
    if (window.location.hash !== canonical) window.history.replaceState(null, "", canonical);
    links.forEach((anchor, key) => {
      if (key === item.slug) anchor.setAttribute("aria-current", "page");
      else anchor.removeAttribute("aria-current");
    });
    title.textContent = item.label;
    if (reader.getBoundingClientRect().top < 0) reader.scrollIntoView();
    const token = ++loadToken;
    if (item.structured) {
      renderAnalyst(run, content);
      return;
    }
    content.replaceChildren(el("p", "muted", "Loading…"));
    getReport(run.id, item.name)
      .then((payload) => {
        if (token !== loadToken || !ctx.isCurrent()) return;
        content.replaceChildren(renderMarkdown(payload.markdown));
      })
      .catch((error) => {
        if (token !== loadToken || !ctx.isCurrent()) return;
        content.replaceChildren(errorPanel("Couldn't load this section", error, () => showSection(item.slug)));
      });
  };
  showSection(route.section);
  strip.reveal();

  const step = (offset) => {
    const target = history[position + offset];
    if (target) ctx.navigate(runHref(target.run_id));
  };
  return {
    handles: (next) => next.name === "report" && next.runId === run.id,
    update: (next) => showSection(next.section),
    onKey(event) {
      if (event.key === "[") { step(-1); return true; }
      if (event.key === "]") { step(1); return true; }
      if (event.key === "Escape") { ctx.navigate("#/"); return true; }
      return false;
    },
    title: `${run.ticker} ${run.decision} · ${formatDate(run.analysis_date, "long")}`,
  };
}

function header(run, previousRated) {
  const head = el("div", "report-head");
  const crumb = el("div", "crumb");
  crumb.append(link("#/", "", "‹ Positions"), ` / ${run.ticker} / ${formatDate(run.analysis_date, "iso")}`);
  head.append(crumb);

  const titleRow = el("div", "title-row");
  titleRow.append(el("h1", "tk", run.ticker), verdictTag(run.decision, run.rating, { large: true }));
  const change = headerChange(run, previousRated);
  if (change) titleRow.append(el("span", `change ${change.cls}`, change.text));
  head.append(titleRow);

  const facts = el("dl", "facts");
  const fact = (label, value) => {
    const wrap = el("div");
    const dd = el("dd");
    dd.append(value);
    wrap.append(el("dt", "", label), dd);
    facts.append(wrap);
  };
  if (run.perspective) fact("Perspective", run.perspective);
  if (run.perspective === "Existing holder" && run.average_cost_usd != null) {
    const cost = el("span", "", money(run.average_cost_usd));
    if (isCostCarried(run)) {
      const mark = el("span", "carried", "†");
      mark.title = `Carried from the ${formatDate(run.average_cost_as_of, "long")} record`;
      cost.append(mark);
    }
    fact("Cost", cost);
  }
  const horizon = horizonLabel(run.horizon);
  if (horizon) fact("Horizon", horizon);
  fact("Evidence", evidenceNode(run.evidence_status));
  const valuation = valuationLabel(run.valuation_status);
  if (valuation !== "—") fact("Valuation", valuation);
  head.append(facts);
  const targets = renderTargets(run);
  if (targets) head.append(targets);

  const warnings = Array.isArray(run.warnings) ? run.warnings : [];
  if (warnings.length) {
    const notes = el("details", "notes");
    notes.append(el("summary", "", `Notes (${warnings.length})`));
    const list = el("ul");
    warnings.forEach((warning) => list.append(el("li", "", warning)));
    notes.append(list);
    head.append(notes);
  }
  return head;
}

function headerChange(run, previousRated) {
  if (run.rating === "evidence") return { text: "evidence packet, no rating", cls: "chg-muted" };
  if (!(run.rating in RATING_ORDER)) return null;
  if (!previousRated) return { text: "first rated run", cls: "chg-muted" };
  const change = describeChange(run, previousRated);
  if (change.direction === "unchanged") {
    return { text: `unchanged from ${formatDate(previousRated.analysis_date, "day")}`, cls: "chg-muted" };
  }
  return {
    text: `${change.direction === "up" ? "▲" : "▼"} from ${String(previousRated.decision).toUpperCase()} on ${formatDate(previousRated.analysis_date, "day")}`,
    cls: change.cls,
  };
}

function historyStrip(history, position, ctx) {
  const element = el("nav", "history");
  element.setAttribute("aria-label", "Run history for this ticker");
  element.append(el("span", "eyebrow", "Rating history"));
  const track = el("div", "history-track");
  let currentNode = null;
  history.forEach((item, index) => {
    if (index > 0) track.append(el("span", "history-link"));
    const isCurrent = index === position;
    const label = `${item.evidence_only ? "Evidence packet" : item.decision} · ${formatDate(item.analysis_date, "long")}`;
    let node;
    if (item.evidence_only) {
      node = el("button", "history-mark");
      node.setAttribute("aria-label", label);
    } else {
      node = el("button", "history-node");
      node.append(verdictTag(item.decision, item.rating), el("span", "", formatDate(item.analysis_date, "short")));
      node.setAttribute("aria-label", label);
    }
    node.type = "button";
    node.title = label;
    if (isCurrent) { node.setAttribute("aria-current", "true"); currentNode = node; }
    node.addEventListener("click", () => { if (!isCurrent) ctx.navigate(runHref(item.run_id)); });
    track.append(node);
  });
  element.append(track);

  const steps = el("div", "history-steps");
  const stepButton = (text, offset, ariaLabel) => {
    const button = el("button", "btn", text);
    button.type = "button";
    button.setAttribute("aria-label", ariaLabel);
    const target = history[position + offset];
    button.disabled = !target;
    button.addEventListener("click", () => { if (target) ctx.navigate(runHref(target.run_id)); });
    return button;
  };
  steps.append(stepButton("[ Older", -1, "Older run"), stepButton("Newer ]", 1, "Newer run"));
  element.append(steps);

  return {
    element,
    reveal() {
      if (currentNode) track.scrollLeft = Math.max(0, currentNode.offsetLeft - track.clientWidth / 2);
    },
  };
}
