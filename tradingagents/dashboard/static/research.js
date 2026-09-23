"use strict";

// Research context is deliberately separate from the archived decision and valuation.
const ResearchFeatures = (() => {
  let sectors = null;
  let selectedSector = "ALL";
  let sectorResizeObserver = null;
  let sectorWidth = 0;
  const $ = (selector) => document.querySelector(selector);
  const node = (tag, className = "", value = null) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value != null) element.textContent = String(value);
    return element;
  };
  const number = (value, digits = 2) => Number.isFinite(Number(value)) && value != null
    ? Number(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "—";
  const money = (value, currency = "USD") => {
    if (value == null || !Number.isFinite(Number(value))) return "—";
    try { return new Intl.NumberFormat("en-US", { style: "currency", currency: currency || "USD" }).format(Number(value)); }
    catch { return `${number(value)} ${currency || ""}`.trim(); }
  };
  const date = (value) => value ? String(value).slice(0, 10) : "Unknown date";
  const text = (value, fallback = "Unknown") => value == null || value === "" ? fallback : String(value);
  const items = (value) => Array.isArray(value) ? value : [];
  const link = (label, rawUrl) => {
    const value = String(rawUrl || "");
    try {
      const url = new URL(value);
      if (url.protocol === "http:" || url.protocol === "https:") {
        const anchor = node("a", "", label);
        anchor.href = url.href;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        return anchor;
      }
    } catch { /* Display the source label as text. */ }
    return node("span", "", label || "No public link");
  };
  const appendText = (parent, tag, value, className = "") => parent.append(node(tag, className, value));
  const detail = (label, value) => {
    const wrap = node("div", "research-detail");
    appendText(wrap, "dt", label);
    appendText(wrap, "dd", value);
    return wrap;
  };

  function appendAnalystTab(tabs, run, activate) {
    const button = node("button", "tab", "Analyst expectations");
    button.type = "button";
    button.addEventListener("click", () => activate(button));
    tabs.append(button);
  }

  function renderAnalyst(run, content) {
    const root = node("div", "analyst-panel");
    appendText(root, "p", "External price targets", "eyebrow");
    appendText(root, "h2", "Analyst expectations");
    appendText(root, "p", "These are published analyst expectations, not an intrinsic valuation or a Research Ledger price estimate.", "research-muted");
    const consensus = run.analyst_consensus;
    const cohorts = items(consensus?.cohorts);
    if (cohorts.length) {
      const policy = consensus.policy || {};
      const policyLabel = typeof policy === "object"
        ? `one latest eligible target per firm; maximum age ${text(policy.max_age_days)} days; top-ranked cutoff ${text(policy.top_percentile)}%`
        : text(policy, "not stated");
      const provenance = node("p", "research-muted", `Target records as of ${date(consensus.as_of)}. Policy: ${policyLabel}.`);
      root.append(provenance);
      cohorts.forEach((cohort) => root.append(renderCohort(cohort)));
      const quote = consensus.quote;
      if (quote && (quote.price != null || quote.close != null)) {
        const box = node("div", "research-note");
        box.append(node("strong", "", "Archived comparison quote"));
        box.append(node("p", "", `${money(quote.price ?? quote.close, quote.currency)} as of ${date(quote.observed_at ?? quote.as_of ?? quote.date)}. This is a saved observation, not a current price.`));
        if (quote.source_url) box.append(link("Quote source", quote.source_url));
        root.append(box);
      }
    } else {
      root.append(node("div", "research-note", "No firm-level, source-linked analyst targets are available for this run."));
    }
    if (items(consensus?.exclusions).length) {
      const exclusions = node("details", "research-exclusions");
      exclusions.append(node("summary", "", `${consensus.exclusions.length} excluded target record${consensus.exclusions.length === 1 ? "" : "s"}`));
      const list = node("ul");
      consensus.exclusions.forEach((entry) => {
        const item = node("li", "", text(entry.reason, "Unspecified exclusion"));
        if (entry.source_url) item.append(" · ", link("source", entry.source_url));
        list.append(item);
      });
      exclusions.append(list);
      root.append(exclusions);
    }
    const vendor = items(run.vendor_analyst_targets);
    if (vendor.length) {
      appendText(root, "h3", "Vendor summary snapshots");
      appendText(root, "p", "These aggregate snapshots have unknown firm constituents and target horizon. They are kept separate from the firm-level consensus above.", "research-muted");
      const table = researchTable(["Snapshot", "Mean", "Median", "Low–high", "Count", "Retrieved", "Source"], vendor.map((record) => [
        text(record.ticker, run.ticker), money(record.values?.mean ?? record.mean, record.currency),
        money(record.values?.median ?? record.median, record.currency),
        `${money(record.values?.low ?? record.low, record.currency)}–${money(record.values?.high ?? record.high, record.currency)}`,
        text(record.analyst_count ?? record.count ?? record.values?.count, "Unknown"), date(record.retrieved_at),
        link("Vendor source", record.source_url),
      ]));
      root.append(table);
    }
    content.replaceChildren(root);
    content.scrollTop = 0;
  }

  function renderCohort(cohort) {
    const key = cohort.key || {};
    const horizon = key.horizon || {};
    const summary = cohort.summary || {};
    const block = node("section", "cohort-block");
    appendText(block, "h3", `${text(key.security_id, "Security unknown")} · ${text(key.currency, "Currency unknown")} · ${text(key.share_basis, "Share basis unknown")}`);
    const horizonLabel = horizon.months == null ? `Horizon unknown${horizon.label ? ` (${horizon.label})` : ""}` : `${horizon.months} month${horizon.months === 1 ? "" : "s"}${horizon.label ? ` (${horizon.label})` : ""}`;
    block.append(node("p", "research-muted", horizonLabel));
    const metrics = node("dl", "research-metrics");
    metrics.append(detail("Firm targets", text(summary.count, "0")), detail("Average", money(summary.mean, key.currency)), detail("Median", money(summary.median, key.currency)), detail("Range", `${money(summary.min, key.currency)}–${money(summary.max, key.currency)}`));
    block.append(metrics);
    block.append(node("p", "research-muted", `Target end dates: ${date(summary.target_end_date_min)} to ${date(summary.target_end_date_max)} · Median record age: ${summary.median_age_days == null ? "unknown" : `${number(summary.median_age_days, 0)} days`}.`));
    const implied = cohort.implied_price_return;
    if (implied && typeof implied === "object") block.append(node("p", "research-muted", `Targets versus archived ${date(implied.quote?.observed_at)} quote: average ${number(implied.mean_percent)}%, median ${number(implied.median_percent)}% opinion-implied price change. Excludes dividends and forecast error.`));
    const top = cohort.top_group || {};
    const topSummary = top.summary || {};
    if (top.status === "available" && items(top.targets).length) {
      const title = node("h4", "", `Top-ranked group · ${text(topSummary.count, top.targets.length)} target${Number(topSummary.count ?? top.targets.length) === 1 ? "" : "s"}`);
      block.append(title);
      const selection = top.selection || {};
      block.append(node("p", "research-muted", `Average ${money(topSummary.mean, key.currency)} · Median ${money(topSummary.median, key.currency)}. Top ${text(selection.top_percentile)}% by ${text(selection.provider)} ranking as of ${date(selection.as_of)}; ranking does not establish target accuracy.`));
      if (implied?.top_mean_percent != null && implied?.top_median_percent != null) {
        block.append(node("p", "research-muted", `Top-ranked targets versus the same archived ${date(implied.quote?.observed_at)} quote: average ${number(implied.top_mean_percent)}%, median ${number(implied.top_median_percent)}% opinion-implied price change. Excludes dividends and forecast error.`));
      }
      block.append(targetTable(top.targets, key.currency));
    } else {
      block.append(node("p", "research-note", `Top-ranked group unavailable${top.reason ? ` (${String(top.reason).replaceAll("_", " ")})` : ""}. No ranked subset is inferred.`));
    }
    appendText(block, "h4", `All source-linked targets · ${text(summary.count, items(cohort.targets).length)}`);
    block.append(targetTable(items(cohort.targets), key.currency));
    return block;
  }

  function targetTable(targets, currency) {
    if (!targets.length) return node("p", "research-muted", "No individual target records in this group.");
    return researchTable(["Analyst / firm", "Target", "Published", "Target end", "Ranking", "Publication", "Rank source"], targets.map((record) => [
      `${text(record.firm ?? record.institution, "Unknown firm")}${record.analyst ? ` · ${record.analyst}` : ""}`,
      money(record.target ?? record.target_price ?? record.price_target ?? record.value, record.currency ?? currency),
      date(record.published_at ?? record.as_of ?? record.issued_at),
      date(record.target_end_date ?? record.horizon_end_date),
      record.ranking?.rank ? `#${record.ranking.rank} of ${text(record.ranking.universe_size)}` : "Unavailable",
      link("View source", record.source_url),
      record.ranking?.source_url ? link("Rank source", record.ranking.source_url) : "—",
    ]));
  }

  function researchTable(headings, rows) {
    const wrap = node("div", "research-table-wrap");
    const table = node("table", "research-table");
    const caption = node("caption", "sr-only", headings.join(", "));
    table.append(caption);
    const thead = node("thead");
    const header = node("tr");
    headings.forEach((heading) => appendText(header, "th", heading));
    thead.append(header);
    table.append(thead);
    const body = node("tbody");
    rows.forEach((cells) => {
      const row = node("tr");
      cells.forEach((cell) => {
        const td = node("td");
        td.append(cell instanceof Node ? cell : document.createTextNode(String(cell)));
        row.append(td);
      });
      body.append(row);
    });
    table.append(body);
    wrap.append(table);
    return wrap;
  }

  async function loadSectors() {
    const container = $("#sector-overview");
    if (!container) return;
    if (!sectorResizeObserver && typeof ResizeObserver !== "undefined") {
      sectorWidth = container.clientWidth;
      sectorResizeObserver = new ResizeObserver(() => {
        const nextWidth = container.clientWidth;
        if (Math.abs(nextWidth - sectorWidth) < 2) return;
        sectorWidth = nextWidth;
        if (sectors) window.requestAnimationFrame(renderSectors);
      });
      sectorResizeObserver.observe(container);
      window.addEventListener("pagehide", () => sectorResizeObserver?.disconnect(), { once: true });
    }
    try {
      const response = await fetch("/api/sectors", { cache: "no-store" });
      if (!response.ok) throw new Error(`Sector observations unavailable (${response.status})`);
      sectors = await response.json();
      renderSectors();
    } catch (error) {
      sectors = null;
      container.replaceChildren(node("p", "sector-empty", `${error.message}. To collect a snapshot, run .\\.venv\\Scripts\\python.exe scripts\\research.py sectors from the project folder, then reload this page.`));
    }
  }

  function renderSectors() {
    const container = $("#sector-overview");
    const data = sectors || {};
    const points = items(data.points).filter((point) => Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y)));
    container.replaceChildren();
    if (data.status === "stale") container.append(node("p", "sector-status stale", `Stored sector snapshot is stale (as of ${date(data.as_of)}). Refresh before interpreting current conditions.`));
    else if (data.status === "unavailable") container.append(node("p", "sector-status unavailable", "Stored sector snapshot is unavailable."));
    if (!points.length) {
      container.append(node("p", "sector-empty", "No stored sector observations are available. Run .\\.venv\\Scripts\\python.exe scripts\\research.py sectors from the project folder, then reload this page."));
    } else {
      const top = node("div", "sector-toolbar");
      const label = node("label", "", "Focus sector ");
      const select = node("select", "sector-select");
      const all = node("option", "", "All sectors"); all.value = "ALL"; select.append(all);
      points.forEach((point) => { const option = node("option", "", `${text(point.sector, point.symbol)} (${point.symbol})`); option.value = point.symbol; select.append(option); });
      if (!points.some((point) => point.symbol === selectedSector)) selectedSector = "ALL";
      select.value = selectedSector;
      select.addEventListener("change", () => { selectedSector = select.value; renderSectors(); });
      label.append(select); top.append(label);
      container.append(top, sectorChart(points, Math.max(280, container.clientWidth - 2 * parseFloat(getComputedStyle(container).paddingLeft || "0"))));
      container.append(node("p", "sector-axis-note", "Horizontal: 13-week return versus benchmark (percentage points). Vertical: 4-week change in that relative return (percentage points). Trails show observed weekly positions. This is a custom relative-performance view, not a proprietary RRG."));
      const visible = selectedSector === "ALL" ? points : points.filter((point) => point.symbol === selectedSector);
      container.append(researchTable(["Sector / fund", "13-week relative", "4-week change", "13-week absolute", "As of"], visible.map((point) => [
        `${text(point.sector, point.symbol)} · ${point.symbol}`, `${number(point.x)} pp`, `${number(point.y)} pp`,
        `${number(point.absolute_return_13w)}%`, date(point.as_of),
      ])));
      const trailRows = visible.flatMap((point) => items(point.trail).map((observation) => [
        `${text(point.sector, point.symbol)} · ${point.symbol}`, date(observation.date), `${number(observation.x)} pp`, `${number(observation.y)} pp`,
      ]));
      if (trailRows.length) {
        const trailDetails = node("details", "sector-notes");
        trailDetails.append(node("summary", "", `Weekly plotted values (${trailRows.length} observations)`));
        trailDetails.append(researchTable(["Sector / fund", "Week", "13-week relative", "4-week change"], trailRows));
        container.append(trailDetails);
      }
    }
    const method = data.methodology;
    const methodLabel = typeof method === "object" && method ? text(method.name, "Methodology unavailable") : text(method, "Methodology unavailable");
    const meta = node("p", "sector-source", `Observed as of ${date(data.as_of)} · Retrieved ${date(data.retrieved_at)} · Source ${text(data.source)} · Benchmark ${text(data.benchmark)} · ${methodLabel}`);
    container.append(meta);
    if (data.source_url) container.append(link("Sector data source", data.source_url));
    const notes = [...items(data.warnings).map((warning) => typeof warning === "string" ? warning : text(warning.reason)), ...items(data.excluded).map((entry) => `${text(entry.symbol)} excluded: ${text(entry.reason)}`)];
    if (notes.length) {
      const details = node("details", "sector-notes");
      details.append(node("summary", "", `${notes.length} data note${notes.length === 1 ? "" : "s"} and exclusions`));
      const list = node("ul"); notes.forEach((note) => appendText(list, "li", note)); details.append(list); container.append(details);
    }
  }

  function sectorChart(points, availableWidth) {
    const NS = "http://www.w3.org/2000/svg";
    const width = Math.max(280, Math.round(availableWidth));
    const height = width < 480 ? 330 : 440;
    const left = 50, right = width - 18, top = 26, bottom = height - 62;
    const middleX = (left + right) / 2, middleY = (top + bottom) / 2;
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `Sector rotation chart with ${points.length} stored sector funds. Axes are percentage points; exact values follow in the table.`);
    svg.classList.add("sector-chart");
    const paths = points.flatMap((point) => [...items(point.trail).filter((item) => Number.isFinite(Number(item.x)) && Number.isFinite(Number(item.y))), point]);
    const maxX = Math.max(2, ...paths.map((point) => Math.abs(Number(point.x) || 0))) * 1.15;
    const maxY = Math.max(2, ...paths.map((point) => Math.abs(Number(point.y) || 0))) * 1.15;
    const sx = (value) => middleX + Number(value) * (right - left) / (2 * maxX);
    const sy = (value) => middleY - Number(value) * (bottom - top) / (2 * maxY);
    const svgNode = (tag, attrs = {}) => { const element = document.createElementNS(NS, tag); Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, String(value))); return element; };
    const label = (value, x, y, attrs = {}) => { const element = svgNode("text", { x, y, ...attrs }); element.textContent = value; svg.append(element); };
    svg.append(svgNode("rect", { x: left, y: top, width: right - left, height: bottom - top, fill: "#f8fafd", stroke: "#d7e0ea" }));
    svg.append(svgNode("line", { x1: middleX, y1: top, x2: middleX, y2: bottom, class: "sector-zero" }));
    svg.append(svgNode("line", { x1: left, y1: middleY, x2: right, y2: middleY, class: "sector-zero" }));
    [-maxX, 0, maxX].forEach((value) => {
      const x = sx(value);
      svg.append(svgNode("line", { x1: x, y1: bottom, x2: x, y2: bottom + 5, class: "sector-zero" }));
      label(number(value, 1), x, bottom + 19, { class: "sector-tick", "text-anchor": "middle" });
    });
    [-maxY, 0, maxY].forEach((value) => {
      const y = sy(value);
      svg.append(svgNode("line", { x1: left - 5, y1: y, x2: left, y2: y, class: "sector-zero" }));
      label(number(value, 1), left - 8, y + 4, { class: "sector-tick", "text-anchor": "end" });
    });
    label("13-week relative return (pp)", middleX, height - 9, { class: "sector-axis-title", "text-anchor": "middle" });
    label("4-week change (pp)", 12, middleY, { class: "sector-axis-title", "text-anchor": "middle", transform: `rotate(-90 12 ${middleY})` });
    if (width >= 480) {
      [["Improving", left + 10, top + 18], ["Leading", right - 10, top + 18], ["Lagging", left + 10, bottom - 12], ["Weakening", right - 10, bottom - 12]].forEach(([name, x, y], index) =>
        label(name, x, y, { class: "sector-quadrant", "text-anchor": index % 2 ? "end" : "start" }));
    }
    points.forEach((point, index) => {
      const focused = selectedSector === "ALL" || point.symbol === selectedSector;
      const trail = items(point.trail).filter((item) => Number.isFinite(Number(item.x)) && Number.isFinite(Number(item.y)));
      if (trail.length) svg.append(svgNode("polyline", { points: trail.map((item) => `${sx(item.x)},${sy(item.y)}`).join(" "), fill: "none", stroke: focused ? "#5a789c" : "#cbd4df", "stroke-width": focused ? 2 : 1, "stroke-dasharray": "4 4" }));
      const circle = svgNode("circle", { cx: sx(point.x), cy: sy(point.y), r: focused ? 6 : 4, fill: focused ? (index % 2 ? "#2f67ad" : "#b67718") : "#aab6c6" });
      const title = svgNode("title"); title.textContent = `${text(point.sector, point.symbol)}: ${number(point.x)} pp relative, ${number(point.y)} pp change, as of ${date(point.as_of)}`;
      circle.append(title); svg.append(circle);
      if (selectedSector === point.symbol) label(point.symbol, Math.min(right - 35, sx(point.x) + 10), Math.max(top + 14, sy(point.y) - 8), { class: "sector-point-label" });
    });
    return svg;
  }

  return { appendAnalystTab, renderAnalyst, loadSectors };
})();
