// Structured analyst expectations for a run. Deliberately separate from the archived decision
// and valuation: these are published opinions, not a Research Ledger price estimate.

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

export function hasStructuredAnalyst(run) {
  return Boolean(items(run.analyst_consensus?.cohorts).length || items(run.vendor_analyst_targets).length);
}

export function renderAnalyst(run, content) {
  const root = node("div", "analyst-panel");
  appendText(root, "h2", "Analyst expectations");
  appendText(root, "p", "These are published analyst expectations, not an intrinsic valuation or a Research Ledger price estimate.", "research-muted");
  const consensus = run.analyst_consensus;
  const cohorts = items(consensus?.cohorts);
  if (cohorts.length) {
    const policy = consensus.policy || {};
    const policyLabel = typeof policy === "object"
      ? `one latest eligible target per firm; maximum age ${text(policy.max_age_days)} days; top-ranked cutoff ${text(policy.top_percentile)}%`
      : text(policy, "not stated");
    root.append(node("p", "research-muted", `Target records as of ${date(consensus.as_of)}. Policy: ${policyLabel}.`));
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
    root.append(researchTable(["Snapshot", "Mean", "Median", "Low–high", "Count", "Retrieved", "Source"], vendor.map((record) => [
      text(record.ticker, run.ticker), money(record.values?.mean ?? record.mean, record.currency),
      money(record.values?.median ?? record.median, record.currency),
      `${money(record.values?.low ?? record.low, record.currency)}–${money(record.values?.high ?? record.high, record.currency)}`,
      text(record.analyst_count ?? record.count ?? record.values?.count, "Unknown"), date(record.retrieved_at),
      link("Vendor source", record.source_url),
    ])));
  }
  content.replaceChildren(root);
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
    block.append(node("h4", "", `Top-ranked group · ${text(topSummary.count, top.targets.length)} target${Number(topSummary.count ?? top.targets.length) === 1 ? "" : "s"}`));
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
  table.append(node("caption", "sr-only", headings.join(", ")));
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
