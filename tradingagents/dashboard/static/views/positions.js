// Positions (#/): holdings and watching tables, the latest report, and a sector mini chart.

import {
  benchmarkName, describeChange, el, evidenceNode, formatDate, isCostCarried, link, money, RATING_ORDER,
  runHref, truncate, validSectorPoints, valuationLabel, verdictTag,
} from "../format.js";
import { mountSectorChart } from "../sector-chart.js";
import { errorPanel, loadingState, sectorBanner, sectorMissing } from "./shared.js";

const newestFirst = (a, b) =>
  `${b.run.analysis_date}${b.run.completed_at || ""}`.localeCompare(`${a.run.analysis_date}${a.run.completed_at || ""}`)
  || a.run.ticker.localeCompare(b.run.ticker);

export function renderPositions(root, ctx) {
  const page = el("div", "view-pad");
  root.replaceChildren(page);
  if (!ctx.data) {
    page.append(ctx.runsError
      ? errorPanel("Couldn't load the research archive", ctx.runsError, ctx.retry)
      : loadingState("Loading the research archive…"));
    return {};
  }

  const tickerRows = (ctx.data.tickers || [])
    .map((summary) => ({ key: summary.ticker, summary, run: ctx.runsById.get(summary.row_run_id) }))
    .filter((row) => row.run);
  const testRows = ctx.showTests
    ? ctx.data.runs.filter((run) => run.is_smoke).map((run) => ({ key: `test:${run.id}`, summary: null, run }))
    : [];

  const query = ctx.query.trim().toLowerCase();
  const matches = (row) => !query || [row.run.ticker, row.run.decision, row.run.perspective, row.run.thesis]
    .some((value) => String(value || "").toLowerCase().includes(query));
  const holdings = tickerRows.filter((row) => row.summary.group === "holding" && matches(row)).sort(newestFirst);
  const watching = tickerRows.filter((row) => row.summary.group !== "holding" && matches(row)).sort(newestFirst);
  const tests = testRows.filter(matches).sort(newestFirst);

  const grid = el("div", "positions");
  const tables = el("div", "positions-tables");
  const side = el("aside", "positions-side");
  side.setAttribute("aria-label", "Latest report and market context");
  grid.append(tables, side);
  page.append(grid);

  const rowElements = [];
  const register = (tr, row) => {
    rowElements.push({ tr, row });
    tr.addEventListener("click", (event) => {
      if (event.target.closest("a")) return;
      ctx.navigate(runHref(row.run.id));
    });
  };

  if (query && !holdings.length && !watching.length && !tests.length) {
    tables.append(el("p", "no-match", `No tickers match "${ctx.query.trim()}".`));
  } else {
    tables.append(group("Holdings", holdings, { withCost: true }, register));
    tables.append(group("Watching", watching, { withCost: false }, register));
    if (ctx.showTests) tables.append(group("Test runs", tests, { withCost: false, isTest: true }, register));
  }

  side.append(latestReportCard(tickerRows));
  const sectorPanel = el("section", "panel");
  side.append(sectorPanel);
  const chart = renderSectorPanel(sectorPanel, ctx);

  let selected = rowElements.findIndex(({ row }) => row.key === ctx.selectedKey);
  const select = (index) => {
    rowElements[selected]?.tr.classList.remove("selected");
    selected = index;
    const current = rowElements[selected];
    if (!current) return;
    current.tr.classList.add("selected");
    current.tr.scrollIntoView({ block: "nearest" });
    ctx.setSelected(current.row.key);
  };
  if (selected >= 0) rowElements[selected].tr.classList.add("selected");

  return {
    onKey(event) {
      if (!rowElements.length) return false;
      if (event.key === "ArrowDown" || event.key === "j") {
        select(selected < 0 ? 0 : Math.min(rowElements.length - 1, selected + 1));
        return true;
      }
      if (event.key === "ArrowUp" || event.key === "k") {
        select(selected < 0 ? 0 : Math.max(0, selected - 1));
        return true;
      }
      if (event.key === "Enter" && selected >= 0) {
        ctx.navigate(runHref(rowElements[selected].row.run.id));
        return true;
      }
      return false;
    },
    destroy() { chart?.destroy(); },
  };
}

function group(title, rows, options, register) {
  const section = el("section");
  const heading = el("h2", "heading", title);
  heading.append(el("small", "", String(rows.length)));
  section.append(heading);
  if (!rows.length) {
    section.append(el("p", "none", options.isTest ? "No test runs." : `No ${title.toLowerCase()} match this view.`));
    return section;
  }
  section.append(positionsTable(title, rows, options, register));
  return section;
}

function positionsTable(title, rows, { withCost, isTest = false }, register) {
  const table = el("table", "ledger");
  table.append(el("caption", "sr-only", `${title}: one row per ticker, showing its latest rated run`));
  const head = el("tr");
  [["Ticker"], ["Verdict"], ["Since last run"], ["Evidence", true], ["Valuation", true], ...(withCost ? [["Cost", true]] : []), ["Run"]]
    .forEach(([label, optional]) => {
      const th = el("th", optional ? "col-opt" : "", label);
      th.scope = "col";
      head.append(th);
    });
  const thead = el("thead");
  thead.append(head);
  const tbody = el("tbody");
  rows.forEach((row) => {
    const { run, summary } = row;
    const tr = el("tr", "row");
    tr.dataset.key = row.key;

    const tickerCell = el("td", "tk");
    tickerCell.append(link(runHref(run.id), "", run.ticker));
    const verdictCell = el("td");
    const rating = summary ? summary.rating : run.rating;
    verdictCell.append(verdictTag(summary ? summary.decision : run.decision, rating));
    const inlineEvidence = evidenceNode(run.evidence_status, { dotOnly: true });
    inlineEvidence.classList.add("ev-inline");
    verdictCell.append(inlineEvidence);

    const change = isTest
      ? { text: "test run", cls: "chg-muted" }
      : describeChange({ rating: summary.rating, decision: summary.decision }, summary.previous);
    const changeCell = el("td", change.cls, change.text);

    const evidenceCell = el("td", "col-opt");
    evidenceCell.append(evidenceNode(run.evidence_status));
    const valuation = valuationLabel(run.valuation_status);
    const valuationCell = el("td", `col-opt${valuation === "—" ? " muted" : ""}`, valuation);

    const cells = [tickerCell, verdictCell, changeCell, evidenceCell, valuationCell];
    if (withCost) cells.push(costCell(run));
    const dateCell = el("td", "num muted", formatDate(run.analysis_date, "short"));
    dateCell.title = formatDate(run.analysis_date, "long");
    cells.push(dateCell);
    tr.append(...cells);
    tbody.append(tr);
    register(tr, row);
  });
  table.append(thead, tbody);
  return table;
}

function costCell(run) {
  const cell = el("td", "num col-opt");
  if (run.average_cost_usd == null) {
    cell.classList.add("muted");
    cell.textContent = "—";
    return cell;
  }
  cell.append(money(run.average_cost_usd));
  if (isCostCarried(run)) {
    const mark = el("span", "carried", "†");
    mark.title = `Carried from the ${formatDate(run.average_cost_as_of, "long")} record`;
    cell.append(mark);
  }
  return cell;
}

function latestReportCard(rows) {
  const panel = el("section", "panel");
  const latest = rows
    .filter((row) => row.summary.rating in RATING_ORDER)
    .sort(newestFirst)[0];
  if (!latest) {
    panel.append(el("p", "eyebrow", "Latest report"), el("p", "latest-excerpt", "No rated reports yet."));
    return panel;
  }
  const { run, summary } = latest;
  panel.append(el("p", "eyebrow", `Latest report · ${formatDate(run.analysis_date, "day")}`));
  const title = el("div", "latest-title");
  title.append(el("span", "tk", run.ticker), verdictTag(summary.decision, summary.rating));
  panel.append(title);
  const excerpt = run.executive_summary || run.decision_interpretation || run.thesis;
  if (excerpt) panel.append(el("p", "latest-excerpt", truncate(excerpt, 200)));
  panel.append(link(runHref(run.id), "link-action", "Read report →"));
  return panel;
}

function renderSectorPanel(panel, ctx) {
  const snapshot = ctx.sectors;
  const points = validSectorPoints(snapshot);
  const bench = benchmarkName(snapshot?.benchmark);
  const newest = points.map((point) => point.as_of).sort().at(-1);
  panel.append(el("p", "eyebrow",
    `Sector rotation · vs ${bench}${newest ? ` · week of ${formatDate(newest, "day")}` : ""}`));

  if (ctx.sectorsError || (snapshot && !points.length)) {
    panel.append(sectorMissing(snapshot, ctx.sectorsError));
    return null;
  }
  if (!snapshot) {
    panel.append(el("p", "chart-note", "Loading sector observations…"));
    return null;
  }
  const banner = sectorBanner(snapshot);
  if (banner) panel.append(banner);

  const button = el("button", "mini-chart");
  button.type = "button";
  button.setAttribute("aria-label", "Open the market view");
  button.addEventListener("click", () => ctx.navigate("#/market"));
  const holder = el("div");
  button.append(holder);
  panel.append(button);

  const foot = el("div", "panel-foot");
  foot.append(el("span", "chart-note", `13-week return vs the ${bench}, in points`), link("#/market", "link-action", "Market detail →"));
  panel.append(foot);
  return mountSectorChart(holder, snapshot, { size: "compact" });
}
