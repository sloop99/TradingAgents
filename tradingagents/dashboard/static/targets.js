// The model's own 12-month targets beside published (Street) analyst targets and the
// last close in the evidence, on one shared scale. Street figures stay labelled as
// opinions; nothing here computes "upside" from them.

import { el, formatDate, money } from "./format.js";

const finite = (value) => value != null && Number.isFinite(Number(value));

/** The first valid vendor snapshot on a run, flattened: {mean, median, low, high, count, currency, retrieved}. */
export function streetTargets(run) {
  const snapshot = (run.vendor_analyst_targets || []).find((item) => finite(item?.values?.mean ?? item?.mean));
  if (!snapshot) return null;
  const values = snapshot.values || snapshot;
  return {
    mean: Number(values.mean),
    median: finite(values.median) ? Number(values.median) : null,
    low: finite(values.low) ? Number(values.low) : null,
    high: finite(values.high) ? Number(values.high) : null,
    count: snapshot.analyst_count ?? snapshot.count ?? null,
    currency: snapshot.currency || "USD",
    retrieved: snapshot.retrieved_at || null,
  };
}

export function modelTarget(run) {
  const target = run.model_target;
  return target && finite(target.base) ? target : null;
}

export function streetTitle(street) {
  const parts = [`Mean of ${street.count ?? "?"} analysts`];
  if (street.median != null) parts.push(`median ${money(street.median, street.currency)}`);
  if (street.low != null && street.high != null) parts.push(`range ${money(street.low, street.currency)}–${money(street.high, street.currency)}`);
  if (street.retrieved) parts.push(`Yahoo, retrieved ${formatDate(street.retrieved, "long")}`);
  return parts.join(" · ");
}

export function modelTitle(target) {
  const parts = ["Model 12-month base case"];
  if (finite(target.bear)) parts.push(`bear ${money(target.bear)}`);
  if (finite(target.bull)) parts.push(`bull ${money(target.bull)}`);
  return parts.join(" · ");
}

/** The report-header panel, or null when the run has neither a model nor a Street target. */
export function renderTargets(run) {
  const model = modelTarget(run);
  const street = streetTargets(run);
  if (!model && !street) return null;
  const close = run.evidence_close && finite(run.evidence_close.value) ? run.evidence_close : null;

  const panel = el("section", "targets panel");
  panel.setAttribute("aria-label", "Price targets");
  const grid = el("div", "targets-grid");
  const stat = (label, value, sub, cls) => {
    const box = el("div", `target-stat ${cls}`);
    box.append(el("p", "eyebrow", label), el("p", "target-value", value));
    if (sub) box.append(el("p", "target-sub", sub));
    grid.append(box);
  };
  if (model) {
    const range = [finite(model.bear) && `bear ${money(model.bear)}`, finite(model.bull) && `bull ${money(model.bull)}`]
      .filter(Boolean).join(" · ");
    stat("Model · 12-month", money(model.base), range, "is-model");
  } else {
    stat("Model · 12-month", "—", "No target in this run", "is-model");
  }
  if (street) {
    const sub = [street.median != null && `median ${money(street.median, street.currency)}`,
      street.low != null && street.high != null && `${money(street.low, street.currency)}–${money(street.high, street.currency)}`]
      .filter(Boolean).join(" · ");
    stat(`Street · ${street.count ?? "?"} analysts`, money(street.mean, street.currency), sub, "is-street");
  } else {
    stat("Street", "—", "Not collected for this run", "is-street");
  }
  if (close) stat("Last close in evidence", money(close.value), formatDate(close.date, "day"), "is-close");
  panel.append(grid);

  const scale = rangeScale(model, street, close);
  if (scale) panel.append(scale);

  if (model?.basis) {
    const basis = el("details", "target-basis");
    basis.append(el("summary", "", "How the model set its target"), el("p", "", model.basis));
    panel.append(basis);
  }
  if (street) {
    panel.append(el("p", "target-note",
      `Street: Yahoo aggregate${street.retrieved ? `, retrieved ${formatDate(street.retrieved, "long")}` : ""}. `
      + "Published opinions with no stated horizon, not a valuation."));
  }
  return panel;
}

function rangeScale(model, street, close) {
  const values = [
    model?.bear, model?.base, model?.bull, street?.low, street?.mean, street?.high, close?.value,
  ].filter(finite).map(Number);
  if (values.length < 2) return null;
  const lo = Math.min(...values) * 0.97;
  const hi = Math.max(...values) * 1.03;
  if (!(hi > lo)) return null;
  const pct = (value) => `${((Number(value) - lo) / (hi - lo)) * 100}%`;

  const wrap = el("div", "range");
  const rows = el("div", "range-rows");
  wrap.append(rows);
  const describe = [];
  const row = (label, cls, from, to, mark) => {
    const line = el("div", "range-row");
    line.append(el("span", "range-label", label));
    const track = el("div", "range-track");
    if (finite(from) && finite(to)) {
      const bar = el("span", `range-bar ${cls}`);
      bar.style.left = pct(Math.min(from, to));
      bar.style.width = `calc(${pct(Math.max(from, to))} - ${pct(Math.min(from, to))})`;
      track.append(bar);
    }
    if (finite(mark)) {
      const point = el("span", `range-mark ${cls}`);
      point.style.left = pct(mark);
      track.append(point);
    }
    line.append(track);
    rows.append(line);
  };
  if (model) {
    row("Model", "is-model", model.bear, model.bull, model.base);
    describe.push(`model base ${money(model.base)}`);
  }
  if (street) {
    row("Street", "is-street", street.low, street.high, street.mean);
    describe.push(`Street mean ${money(street.mean, street.currency)}`);
  }
  if (close) {
    // Overlay aligned with the tracks (not the labels) so the line sits at the right price.
    const layer = el("div", "range-layer");
    const marker = el("span", "range-close");
    marker.style.left = pct(close.value);
    marker.title = `Last close ${money(close.value)} on ${formatDate(close.date, "long")}`;
    layer.append(marker);
    rows.append(layer);
    describe.push(`last close ${money(close.value)}`);
  }
  const axis = el("div", "range-axis");
  axis.append(el("span", "", money(lo)), el("span", "", money(hi)));
  wrap.append(axis);
  wrap.setAttribute("role", "img");
  wrap.setAttribute("aria-label", `Price target ranges: ${describe.join(", ")}.`);
  return wrap;
}

// ---------- estimated (unverified) valuation ----------

const MULTIPLES = [
  ["price_to_earnings", "P/E"],
  ["price_to_sales", "P/S"],
  ["ev_to_revenue", "EV/Revenue"],
  ["price_to_free_cash_flow", "P/FCF"],
];

export function compactMoney(value) {
  if (!finite(value)) return "—";
  const abs = Math.abs(Number(value));
  if (abs >= 1e12) return `$${(value / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
  return `$${(value / 1e6).toFixed(1)}M`;
}

const multiple = (value) => (finite(value) ? `${Number(value).toFixed(1)}×` : "n/a");

/** The first available headline multiple, e.g. {label: "P/E", value: "38.5×"}, or null. */
export function headlineMultiple(run) {
  const estimate = run.estimated_valuation;
  if (!estimate?.multiples) return null;
  // A near-breakeven P/E (hundreds or thousands of x) is not a useful headline.
  const hit = MULTIPLES.find(([key]) => finite(estimate.multiples[key])
    && !(key === "price_to_earnings" && Number(estimate.multiples[key]) > 150));
  return hit ? { label: hit[1], value: multiple(estimate.multiples[hit[0]]) } : null;
}

export function estimateTitle(run) {
  const estimate = run.estimated_valuation;
  if (!estimate) return "";
  const parts = [`Estimated, unverified · market cap ${compactMoney(estimate.market_cap)}`];
  if (finite(estimate.enterprise_value)) parts.push(`EV ${compactMoney(estimate.enterprise_value)}`);
  MULTIPLES.forEach(([key, label]) => parts.push(`${label} ${multiple(estimate.multiples?.[key])}`));
  return parts.join(" · ");
}

export function renderValuation(run) {
  const estimate = run.estimated_valuation;
  if (!estimate || !finite(estimate.market_cap)) return null;
  const panel = el("section", "valuation panel");
  panel.setAttribute("aria-label", "Estimated valuation");
  const head = el("div", "valuation-head");
  head.append(el("p", "eyebrow", "Estimated valuation · unverified"));
  const basis = { sec_cover: "SEC share count", vendor: "vendor share count", vendor_market_cap: "vendor market cap" }[estimate.shares?.source]
    || "unknown share basis";
  head.append(el("span", "valuation-basis", `${formatDate(estimate.price?.date, "day")} close ${money(estimate.price?.value)} × ${basis}`));
  panel.append(head);
  const grid = el("dl", "valuation-grid");
  const cell = (label, value) => {
    const wrap = el("div");
    wrap.append(el("dt", "", label), el("dd", value === "n/a" || value === "—" ? "muted" : "", value));
    grid.append(wrap);
  };
  cell("Market cap", compactMoney(estimate.market_cap));
  cell("Enterprise value", compactMoney(estimate.enterprise_value));
  MULTIPLES.forEach(([key, label]) => cell(label, multiple(estimate.multiples?.[key])));
  panel.append(grid);
  const notes = (estimate.notes || []).filter((note) => !note.includes("completeness is not verified"));
  if (notes.length) {
    const details = el("details", "target-basis");
    details.append(el("summary", "", `Caveats (${notes.length})`));
    const list = el("ul");
    notes.forEach((note) => list.append(el("li", "", note)));
    details.append(list);
    panel.append(details);
  }
  return panel;
}
