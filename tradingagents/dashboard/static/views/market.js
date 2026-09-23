// Market (#/market): the full sector chart beside a names-first quadrant summary.

import {
  benchmarkName, el, formatDate, quadrantOf, QUADRANTS, sectorName, signed, validSectorPoints,
} from "../format.js";
import { mountSectorChart } from "../sector-chart.js";
import { errorPanel, loadingState, sectorBanner, sectorMissing } from "./shared.js";

const QUADRANT_ORDER = ["lead", "weak", "impr", "lag"];

export function renderMarket(root, ctx) {
  const page = el("div", "view-pad");
  root.replaceChildren(page);
  if (ctx.sectorsError) {
    page.append(errorPanel("Couldn't load sector observations", ctx.sectorsError, ctx.retrySectors));
    return {};
  }
  if (!ctx.sectors) {
    page.append(loadingState("Loading sector observations…"));
    return {};
  }
  const snapshot = ctx.sectors;
  const points = validSectorPoints(snapshot);
  const banner = sectorBanner(snapshot);
  if (banner) page.append(banner);
  if (!points.length) {
    const panel = el("section", "panel");
    panel.append(el("h2", "heading", "Sector rotation"), sectorMissing(snapshot));
    page.append(panel);
    return {};
  }

  const bench = benchmarkName(snapshot.benchmark);
  const newest = points.map((point) => point.as_of).sort().at(-1);
  const grid = el("div", "market");
  const chartPanel = el("section", "panel");
  chartPanel.setAttribute("aria-label", "Sector rotation chart");
  chartPanel.append(el("h1", "eyebrow",
    `Sector rotation · ${points.length} sectors vs the ${bench} (${snapshot.benchmark || "SPY"}) · week of ${formatDate(newest, "day")}`));
  const holder = el("div");
  chartPanel.append(holder);
  chartPanel.append(el("p", "chart-note",
    `Left–right: 13-week return vs the ${bench}. Up–down: whether that gap grew or shrank over the last 4 weeks. `
    + "Hover a sector to preview its path; select one to keep it."));

  const side = el("div");
  const summaryPanel = el("section", "panel");
  summaryPanel.setAttribute("aria-label", "Sectors by quadrant");
  const rows = new Map();
  const maxAbs = Math.max(...points.map((point) => Math.abs(Number(point.x))), 1);
  QUADRANT_ORDER.forEach((key) => {
    const members = points.filter((point) => quadrantOf(point) === key).sort((a, b) => Number(b.x) - Number(a.x));
    if (!members.length) return;
    const groupEl = el("div", "quad-group");
    const head = el("div", "quad-head");
    head.append(el("b", `q-${key}`, QUADRANTS[key].name.toUpperCase()), el("span", "", QUADRANTS[key].description));
    groupEl.append(head);
    members.forEach((point) => {
      const row = el("button", "quad-row");
      row.type = "button";
      row.setAttribute("aria-pressed", "false");
      row.setAttribute("aria-label", `${sectorName(point)}, ${QUADRANTS[key].name.toLowerCase()}, ${signed(point.x, 1)} points versus the ${bench}`);
      const bar = el("span", `quad-bar q-${key}`);
      bar.style.width = `${Math.max(4, (Math.abs(Number(point.x)) / maxAbs) * 100)}%`;
      row.append(el("span", "", sectorName(point)), el("span", "fund", point.symbol), el("span", "num", `${signed(point.x, 1)} pts`), bar);
      row.addEventListener("click", () => setFocus(ctx.marketFocus === point.symbol ? null : point.symbol));
      rows.set(point.symbol, row);
      groupEl.append(row);
    });
    summaryPanel.append(groupEl);
  });
  summaryPanel.append(sourceLine(snapshot));
  summaryPanel.append(exactValues(snapshot, points, bench));
  side.append(summaryPanel);
  grid.append(chartPanel, side);
  page.append(grid);

  const chart = mountSectorChart(holder, snapshot, {
    size: "full", focus: ctx.marketFocus, onFocus: (symbol) => setFocus(symbol),
  });
  function setFocus(symbol) {
    ctx.setMarketFocus(symbol);
    rows.forEach((row, key) => row.setAttribute("aria-pressed", String(key === symbol)));
    chart.update({ focus: symbol });
  }
  rows.forEach((row, key) => row.setAttribute("aria-pressed", String(key === ctx.marketFocus)));

  return {
    onKey(event) {
      if (event.key === "Escape" && ctx.marketFocus) { setFocus(null); return true; }
      return false;
    },
    destroy() { chart.destroy(); },
  };
}

function sourceLine(snapshot) {
  const method = snapshot.methodology?.name;
  const parts = [
    snapshot.source || "Unknown source",
    `benchmark ${snapshot.benchmark || "SPY"}`,
    `observed ${formatDate(snapshot.as_of, "long")}`,
    `retrieved ${formatDate(snapshot.retrieved_at, "long")}`,
  ];
  if (method) parts.push(method);
  return el("p", "source-line", parts.join(" · "));
}

function exactValues(snapshot, points, bench) {
  const details = el("details", "exact");
  const notes = [
    ...(snapshot.warnings || []).map(String),
    ...(snapshot.excluded || []).map((entry) => `${entry.symbol} excluded: ${entry.reason}`),
  ];
  details.append(el("summary", "", `Exact values${notes.length ? ` & ${notes.length} data notes` : ""}`));

  const scroll = el("div", "table-scroll");
  scroll.append(table(
    ["Sector", "Fund", `13-wk vs ${bench}`, "4-wk change", "13-wk absolute", "As of"],
    [...points].sort((a, b) => Number(b.x) - Number(a.x)).map((point) => [
      sectorName(point), point.symbol, `${signed(point.x, 2)} pts`, `${signed(point.y, 2)} pts`,
      `${signed(point.absolute_return_13w, 2)}%`, formatDate(point.as_of, "iso"),
    ]),
  ));
  details.append(scroll);

  const weekly = points.flatMap((point) => (point.trail || []).map((item) => [
    sectorName(point), formatDate(item.date, "iso"), `${signed(item.x, 2)} pts`, `${signed(item.y, 2)} pts`,
  ]));
  if (weekly.length) {
    const weeklyDetails = el("details");
    weeklyDetails.append(el("summary", "", `Weekly plotted values (${weekly.length} observations)`));
    const weeklyScroll = el("div", "table-scroll");
    weeklyScroll.append(table(["Sector", "Week", `13-wk vs ${bench}`, "4-wk change"], weekly));
    weeklyDetails.append(weeklyScroll);
    details.append(weeklyDetails);
  }

  const method = snapshot.methodology || {};
  const methodKeys = Object.keys(method).filter((key) => key !== "name");
  if (methodKeys.length) {
    const methodDetails = el("details");
    methodDetails.append(el("summary", "", "Method"));
    const list = el("ul");
    methodKeys.forEach((key) => list.append(el("li", "", `${key.replaceAll("_", " ")}: ${method[key]}`)));
    methodDetails.append(list);
    details.append(methodDetails);
  }
  if (notes.length) {
    const notesDetails = el("details");
    notesDetails.append(el("summary", "", `Data notes (${notes.length})`));
    const list = el("ul");
    notes.forEach((note) => list.append(el("li", "", note)));
    notesDetails.append(list);
    details.append(notesDetails);
  }
  return details;
}

function table(headings, rows) {
  const tableEl = el("table", "ledger");
  const thead = el("thead");
  const head = el("tr");
  headings.forEach((heading) => { const th = el("th", "", heading); th.scope = "col"; head.append(th); });
  thead.append(head);
  const tbody = el("tbody");
  rows.forEach((cells) => {
    const tr = el("tr");
    cells.forEach((cell, index) => tr.append(el("td", index >= 2 ? "num" : "", cell)));
    tbody.append(tr);
  });
  tableEl.append(thead, tbody);
  return tableEl;
}
