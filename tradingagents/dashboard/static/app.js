"use strict";

const state = { payload: null, selectedTicker: "ALL", activeRun: null, activeSection: null };
const $ = (selector) => document.querySelector(selector);

document.addEventListener("DOMContentLoaded", async () => {
  bindControls();
  ResearchFeatures.loadSectors();
  await loadRuns();
});

function bindControls() {
  ["#search", "#latest-only", "#show-smoke", "#show-cost"].forEach((selector) => {
    $(selector).addEventListener("input", render);
  });
  $("#refresh").addEventListener("click", refreshRuns);
  $("#dialog-close").addEventListener("click", () => $("#report-dialog").close());
  $("#report-dialog").addEventListener("click", (event) => {
    if (event.target === $("#report-dialog")) $("#report-dialog").close();
  });
}

async function loadRuns() {
  try {
    const response = await fetch("/api/runs", { cache: "no-store" });
    if (!response.ok) throw new Error(`Index request failed (${response.status})`);
    state.payload = await response.json();
    render();
  } catch (error) {
    showToast(error.message);
  }
}

async function refreshRuns() {
  const button = $("#refresh");
  button.disabled = true;
  button.textContent = "Refreshing…";
  try {
    const response = await fetch("/api/refresh", { method: "POST" });
    if (!response.ok) throw new Error(`Refresh failed (${response.status})`);
    state.payload = await response.json();
    render();
    ResearchFeatures.loadSectors();
    showToast("Research archive refreshed");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Refresh runs";
  }
}

function render() {
  if (!state.payload) return;
  const showSmoke = $("#show-smoke").checked;
  const archiveRuns = state.payload.runs.filter((run) => showSmoke || !run.is_smoke);
  const tickers = groupTickers(archiveRuns);
  if (state.selectedTicker !== "ALL" && !tickers.has(state.selectedTicker)) state.selectedTicker = "ALL";
  renderSummary(archiveRuns);
  renderTickerNav(tickers, archiveRuns.length);
  renderTape(archiveRuns);
  renderCards(filteredRuns(archiveRuns));
}

function groupTickers(runs) {
  const result = new Map();
  runs.forEach((run) => result.set(run.ticker, (result.get(run.ticker) || 0) + 1));
  return new Map([...result].sort((a, b) => a[0].localeCompare(b[0])));
}

function renderSummary(runs) {
  $("#summary-runs").textContent = runs.length;
  $("#summary-tickers").textContent = new Set(runs.map((run) => run.ticker)).size;
  const latest = runs.map((run) => run.analysis_date).filter((value) => value !== "unknown").sort().at(-1);
  $("#summary-date").textContent = latest ? formatDate(latest, { month: "short", day: "numeric" }) : "—";
}

function renderTickerNav(tickers, total) {
  const nav = $("#ticker-nav");
  nav.replaceChildren();
  nav.append(tickerButton("ALL", total, "All companies"));
  tickers.forEach((count, ticker) => nav.append(tickerButton(ticker, count, ticker)));
}

function tickerButton(value, count, label) {
  const button = el("button", "ticker-button");
  button.type = "button";
  button.classList.toggle("active", state.selectedTicker === value);
  button.setAttribute("aria-pressed", String(state.selectedTicker === value));
  button.append(el("span", "", label), el("span", "", String(count)));
  button.addEventListener("click", () => { state.selectedTicker = value; render(); });
  return button;
}

function filteredRuns(runs) {
  const query = $("#search").value.trim().toLowerCase();
  const latestOnly = $("#latest-only").checked;
  const showPositionData = $("#show-cost").checked;
  return runs.filter((run) => {
    if (state.selectedTicker !== "ALL" && run.ticker !== state.selectedTicker) return false;
    if (latestOnly && !run.is_latest) return false;
    if (!query) return true;
    const searchableThesis = showPositionData || !isPositionRun(run) ? run.thesis : "";
    return [run.ticker, run.decision, searchableThesis, run.perspective, run.analysis_date]
      .some((value) => String(value || "").toLowerCase().includes(query));
  });
}

function isPositionRun(run) {
  return run.perspective === "Existing holder" || run.average_cost_usd != null;
}

function renderTape(runs) {
  const tape = $("#research-tape");
  tape.replaceChildren();
  const candidates = state.selectedTicker === "ALL"
    ? runs.filter((run) => run.is_latest).slice().sort((a, b) => a.analysis_date.localeCompare(b.analysis_date))
    : runs.filter((run) => run.ticker === state.selectedTicker).slice().sort((a, b) => a.analysis_date.localeCompare(b.analysis_date));
  $("#tape-caption").textContent = state.selectedTicker === "ALL"
    ? "Most recent run for each company"
    : `${candidates.length} dated run${candidates.length === 1 ? "" : "s"} for ${state.selectedTicker}`;
  candidates.forEach((run) => {
    const item = el("div", "tape-run");
    item.dataset.evidence = run.evidence_status;
    item.setAttribute("role", "listitem");
    item.append(
      el("time", "", formatDate(run.analysis_date, { month: "short", day: "numeric", year: "numeric" })),
      el("strong", "", state.selectedTicker === "ALL" ? `${run.ticker} · ${run.decision}` : run.decision),
      el("small", "", evidenceLabel(run.evidence_status)),
    );
    tape.append(item);
  });
}

function renderCards(runs) {
  const list = $("#run-list");
  list.replaceChildren();
  $("#result-count").textContent = `${runs.length} run${runs.length === 1 ? "" : "s"}`;
  $("#empty").hidden = runs.length > 0;
  runs.forEach((run, index) => list.append(runCard(run, index)));
}

function runCard(run, index) {
  const card = el("button", "run-card");
  card.type = "button";
  card.dataset.evidence = run.evidence_status;
  card.style.animationDelay = `${Math.min(index * 35, 250)}ms`;
  card.setAttribute("aria-label", `Open ${run.ticker} ${run.analysis_date} report`);

  const top = el("div", "card-top");
  const lockup = el("div", "ticker-lockup");
  lockup.append(el("span", "ticker-symbol", run.ticker));
  if (run.is_latest) lockup.append(el("span", "latest-chip", "Latest"));
  top.append(lockup, el("time", "card-date", formatDate(run.analysis_date, { month: "short", day: "numeric", year: "numeric" })));
  const context = isPositionRun(run) && !$("#show-cost").checked
    ? "Position context hidden. Turn on Show position data to view this summary."
    : run.decision_interpretation || run.thesis || "Open the report for the full research context.";
  card.append(top, el("div", "card-decision", run.decision), el("div", "card-context", truncate(context, 150)));

  const meta = el("div", "card-meta");
  meta.append(badge(run.perspective), badge(`Evidence: ${evidenceLabel(run.evidence_status)}`, evidenceClass(run.evidence_status)));
  meta.append(badge(`Valuation: ${capitalize(run.valuation_status)}`, run.valuation_status === "unsupported" ? "warning" : ""));
  if (run.is_smoke) meta.append(badge("Test run", "warning"));
  if (run.analyst_consensus?.cohorts?.length) meta.append(badge("Analyst expectations"));
  card.append(meta);
  if ($("#show-cost").checked && run.average_cost_usd != null) {
    const costDate = run.average_cost_as_of && run.average_cost_as_of !== run.analysis_date
      ? `Last recorded ${formatDate(run.average_cost_as_of, { month: "short", day: "numeric", year: "numeric" })}`
      : "Recorded average cost";
    card.append(el("div", "cost-line", `${costDate} · $${run.average_cost_usd.toFixed(2)} per share`));
  }
  card.addEventListener("click", () => openReport(run));
  return card;
}

function badge(text, modifier = "") {
  return el("span", `badge ${modifier}`.trim(), text);
}

async function openReport(run) {
  state.activeRun = run;
  const { sections } = analystTabPolicy(run);
  const preferred = sections.includes("Final decision") ? "Final decision" : sections[0] || "analyst-expectations";
  $("#dialog-eyebrow").textContent = `${run.ticker} · ${formatDate(run.analysis_date, { month: "long", day: "numeric", year: "numeric" })}`;
  $("#dialog-title").textContent = `${run.decision} · ${run.perspective}`;
  renderTabs(run, preferred);
  $("#report-dialog").showModal();
  if (preferred === "analyst-expectations") {
    state.activeSection = preferred;
    ResearchFeatures.renderAnalyst(run, $("#report-content"));
  } else {
    await loadSection(run, preferred);
  }
}

function analystTabPolicy(run) {
  const hasStructured = Boolean(run.analyst_consensus || run.vendor_analyst_targets?.length);
  const hasRaw = run.sections.some((section) => section.trim().toLowerCase() === "analyst expectations");
  return {
    showStructured: hasStructured || !hasRaw,
    sections: hasStructured
      ? run.sections.filter((section) => section.trim().toLowerCase() !== "analyst expectations")
      : run.sections,
  };
}

function renderTabs(run, selected) {
  const tabs = $("#section-tabs");
  tabs.replaceChildren();
  const { showStructured, sections } = analystTabPolicy(run);
  if (showStructured) {
    ResearchFeatures.appendAnalystTab(tabs, run, (button) => {
      state.activeSection = "analyst-expectations";
      tabs.querySelectorAll(".tab").forEach((node) => node.classList.remove("active"));
      button.classList.add("active");
      ResearchFeatures.renderAnalyst(run, $("#report-content"));
    });
    tabs.lastElementChild.classList.toggle("active", selected === "analyst-expectations");
  }
  sections.forEach((section) => {
    const button = el("button", "tab", section);
    button.type = "button";
    button.classList.toggle("active", section === selected);
    button.addEventListener("click", async () => {
      tabs.querySelectorAll(".tab").forEach((node) => node.classList.remove("active"));
      button.classList.add("active");
      await loadSection(run, section);
    });
    tabs.append(button);
  });
}

async function loadSection(run, section) {
  state.activeSection = section;
  const content = $("#report-content");
  content.replaceChildren(el("p", "report-loading", "Loading report…"));
  try {
    const url = `/api/report?id=${encodeURIComponent(run.id)}&section=${encodeURIComponent(section)}`;
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`Report request failed (${response.status})`);
    const payload = await response.json();
    if (state.activeRun !== run || state.activeSection !== section) return;
    content.replaceChildren(renderMarkdown(payload.markdown));
    content.scrollTop = 0;
  } catch (error) {
    if (state.activeRun !== run || state.activeSection !== section) return;
    content.replaceChildren(el("p", "report-loading", error.message));
  }
}

function renderMarkdown(markdown) {
  const root = document.createElement("div");
  const lines = markdown.replace(/\r/g, "").split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const node = document.createElement(`h${heading[1].length}`);
      appendInline(node, heading[2]);
      root.append(node); index += 1; continue;
    }
    if (/^\|.*\|\s*$/.test(line) && index + 1 < lines.length && /^\|?\s*:?-+/.test(lines[index + 1])) {
      const block = [];
      while (index < lines.length && /^\|.*\|\s*$/.test(lines[index])) block.push(lines[index++]);
      root.append(renderTable(block)); continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const list = document.createElement("ul");
      while (index < lines.length && /^[-*]\s+/.test(lines[index])) {
        const item = document.createElement("li"); appendInline(item, lines[index].replace(/^[-*]\s+/, "")); list.append(item); index += 1;
      }
      root.append(list); continue;
    }
    if (/^\d+\.\s+/.test(line)) {
      const list = document.createElement("ol");
      while (index < lines.length && /^\d+\.\s+/.test(lines[index])) {
        const item = document.createElement("li"); appendInline(item, lines[index].replace(/^\d+\.\s+/, "")); list.append(item); index += 1;
      }
      root.append(list); continue;
    }
    const paragraph = document.createElement("p");
    const chunk = [line.trim()]; index += 1;
    while (index < lines.length && lines[index].trim() && !/^(#{1,3})\s|^[-*]\s+|^\d+\.\s+|^\|.*\|\s*$/.test(lines[index])) chunk.push(lines[index++].trim());
    appendInline(paragraph, chunk.join(" "));
    root.append(paragraph);
  }
  return root;
}

function renderTable(lines) {
  const table = document.createElement("table");
  const rows = lines.filter((_, index) => index !== 1).map((line) => line.replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim()));
  rows.forEach((cells, rowIndex) => {
    const row = document.createElement("tr");
    cells.forEach((cell) => { const node = document.createElement(rowIndex === 0 ? "th" : "td"); appendInline(node, cell); row.append(node); });
    (rowIndex === 0 ? (table.tHead || table.createTHead()) : (table.tBodies[0] || table.createTBody())).append(row);
  });
  return table;
}

function appendInline(parent, text) {
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    if (token.startsWith("**")) parent.append(el("strong", "", token.slice(2, -2)));
    else if (token.startsWith("`")) parent.append(el("code", "", token.slice(1, -1)));
    else {
      const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const node = el("a", "", link[1]);
      if (/^https?:\/\//i.test(link[2])) { node.href = link[2]; node.target = "_blank"; node.rel = "noreferrer"; }
      else { node.href = "#"; node.title = "Local source path retained in report"; }
      parent.append(node);
    }
    cursor = match.index + token.length;
  }
  parent.append(document.createTextNode(text.slice(cursor)));
}

function el(tag, className = "", text = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function evidenceLabel(value) {
  const labels = { material_conflict: "Material conflict", sufficient: "Sufficient", partial: "Partial", unsupported: "Unsupported", legacy: "Legacy" };
  return labels[value] || capitalize(value || "Unknown");
}
function evidenceClass(value) { return value === "material_conflict" ? "conflict" : (["partial", "unsupported"].includes(value) ? "warning" : ""); }
function capitalize(value) { return String(value || "unknown").replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase()); }
function truncate(value, length) { return value.length > length ? `${value.slice(0, length - 1).trim()}…` : value; }
function formatDate(value, options) {
  if (!value || value === "unknown") return "Unknown date";
  const parsed = new Date(`${value.slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(parsed.valueOf()) ? value : new Intl.DateTimeFormat("en-US", { ...options, timeZone: "UTC" }).format(parsed);
}
function showToast(message) {
  const toast = $("#toast"); toast.textContent = message; toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 2600);
}
