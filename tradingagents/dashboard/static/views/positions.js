// Positions (#/): holdings and watching tables, the latest report, and a sector mini chart.

import {
  benchmarkName, countdown, daysBetween, describeChange, el, evidenceNode, formatDate, isCostCarried, link, money,
  nextEarningsText, RATING_ORDER, runHref, truncate, validSectorPoints, valuationLabel, verdictTag,
} from "../format.js";
import { mountSectorChart } from "../sector-chart.js";
import { estimateTitle, headlineMultiple, modelTarget, modelTitle, streetTargets, streetTitle } from "../targets.js";
import { agendaPanel } from "./agenda.js";
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
  side.setAttribute("aria-label", "Upcoming events, latest report and market context");
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

  const earnings = ctx.earnings;
  if (query && !holdings.length && !watching.length && !tests.length) {
    tables.append(el("p", "no-match", `No tickers match "${ctx.query.trim()}".`));
  } else {
    tables.append(group("Holdings", holdings, { withCost: true, earnings }, register));
    tables.append(group("Watching", watching, { withCost: false, earnings }, register));
    if (ctx.showTests) tables.append(group("Test runs", tests, { withCost: false, isTest: true, earnings }, register));
  }

  side.append(agendaPanel(ctx), latestReportCard(tickerRows));
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

function positionsTable(title, rows, { withCost, isTest = false, earnings }, register) {
  const alerts = new Map((earnings?.alerts || [])
    .filter((alert) => alert.severity !== "pending")
    .map((alert) => [alert.ticker, alert]));
  const table = el("table", "ledger");
  table.append(el("caption", "sr-only", `${title}: one row per ticker, showing its latest rated run`));
  const head = el("tr");
  [["Ticker"], ["Verdict"], ["Since last run"], ["Evidence", true], ["Valuation", true], ["Target", true], ["Street", true], ...(withCost ? [["Cost", true]] : []), ["Next", true], ["Run"]]
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
    const alert = isTest ? null : alerts.get(run.ticker);
    if (alert) tickerCell.append(alertDot(alert));
    const verdictCell = el("td");
    const rating = summary ? summary.rating : run.rating;
    verdictCell.append(verdictTag(summary ? summary.decision : run.decision, rating));
    const inlineEvidence = evidenceNode(run.evidence_status, { dotOnly: true });
    inlineEvidence.classList.add("ev-inline");
    verdictCell.append(inlineEvidence);

    const change = isTest
      ? { text: "test run", cls: "chg-muted" }
      : describeChange({ rating: summary.rating, decision: summary.decision }, summary.previous);
    const changeCell = el("td", `chg ${change.cls}`, change.text);

    const evidenceCell = el("td", "col-opt");
    evidenceCell.append(evidenceNode(run.evidence_status));
    const valuationCell = valuationCellFor(run);

    const cells = [tickerCell, verdictCell, changeCell, evidenceCell, valuationCell, targetCell(run), streetCell(run)];
    if (withCost) cells.push(costCell(run));
    cells.push(nextCell(run.ticker, earnings));
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

function valuationCellFor(run) {
  // A verified status wins; otherwise show the headline estimated multiple, marked "est".
  const verified = !["unsupported", "unknown", "", undefined, null].includes(run.valuation_status);
  const headline = headlineMultiple(run);
  if (!verified && headline) {
    const cell = el("td", "num col-opt", `${headline.label} ${headline.value}`);
    cell.append(el("span", "est", " est"));
    cell.title = estimateTitle(run);
    return cell;
  }
  const label = valuationLabel(run.valuation_status);
  return el("td", `col-opt${label === "—" ? " muted" : ""}`, label);
}

function targetCell(run) {
  const target = modelTarget(run);
  const cell = el("td", `num col-opt${target ? "" : " muted"}`, target ? money(target.base) : "—");
  if (target) cell.title = modelTitle(target);
  return cell;
}

function streetCell(run) {
  const street = streetTargets(run);
  const cell = el("td", `num col-opt${street ? "" : " muted"}`, street ? money(street.mean, street.currency) : "—");
  if (street) cell.title = streetTitle(street);
  return cell;
}

function alertDot(alert) {
  const node = el("span", `alert-dot sev-${alert.severity}`);
  node.title = alert.message;
  node.append(el("span", "", "●"), el("span", "sr-only", ` ${alert.message}`));
  node.firstChild.setAttribute("aria-hidden", "true");
  return node;
}

function nextCell(ticker, earnings) {
  const next = earnings?.next_earnings?.[ticker];
  if (!next) return el("td", "num col-opt muted", "—");
  const days = daysBetween(earnings.today, next.date);
  const cell = el("td", `num col-opt${days != null && days <= 7 ? " soon" : ""}`,
    `${next.estimated ? "~" : ""}${countdown(days)}`);
  cell.title = `Earnings ${nextEarningsText(next)}`;
  return cell;
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
  const target = modelTarget(run);
  const street = streetTargets(run);
  if (target || street) {
    const line = el("p", "latest-targets");
    if (target) line.append(el("span", "", `Target ${money(target.base)}`));
    if (street) line.append(el("span", "", `Street ${money(street.mean, street.currency)}`));
    panel.append(line);
  }
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
