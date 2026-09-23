// Formatting and DOM helpers shared by every view. Pure functions only.

export function el(tag, className = "", text = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";
export function svg(tag, attrs = {}, text = null) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value != null) node.setAttribute(key, String(value));
  }
  if (text != null) node.textContent = String(text);
  return node;
}

export function link(href, className, text) {
  const anchor = el("a", className, text);
  anchor.href = href;
  return anchor;
}

export const runHref = (runId, slug = null) =>
  `#/run/${encodeURIComponent(runId)}${slug ? `/${encodeURIComponent(slug)}` : ""}`;

// ---------- dates & numbers ----------

function parseDay(value) {
  if (!value || value === "unknown") return null;
  const parsed = new Date(`${String(value).slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(parsed.valueOf()) ? null : parsed;
}

const FORMATS = {
  short: { month: "short", day: "2-digit" },
  day: { month: "short", day: "numeric" },
  long: { month: "short", day: "numeric", year: "numeric" },
};

/** "short" → SEP 17 (table cells), "day" → Sep 17, "long" → Sep 17, 2026, "iso" → 2026-09-17. */
export function formatDate(value, style = "day") {
  const parsed = parseDay(value);
  if (!parsed) return style === "short" ? "—" : "Unknown date";
  if (style === "iso") return String(value).slice(0, 10);
  const text = new Intl.DateTimeFormat("en-US", { ...FORMATS[style], timeZone: "UTC" }).format(parsed);
  return style === "short" ? text.toUpperCase() : text;
}

export function money(value) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(Number(value));
}

/** Fixed decimals with a real minus sign; `sign` adds a leading + for positives. */
export function signed(value, digits = 1, { sign = true } = {}) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  const text = Math.abs(number).toFixed(digits);
  if (Number(text) === 0) return text;
  return number < 0 ? `−${text}` : `${sign ? "+" : ""}${text}`;
}

export function capitalize(value) {
  return String(value || "unknown").replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

export function truncate(value, length) {
  const text = String(value || "");
  return text.length > length ? `${text.slice(0, length - 1).trimEnd()}…` : text;
}

// ---------- verdicts ----------

export const RATING_ORDER = { sell: 1, underweight: 2, hold: 3, overweight: 4, buy: 5 };
const TAG_CLASS = { buy: "buy", overweight: "buy", hold: "hold", underweight: "sell", sell: "sell", evidence: "evidence" };

export function verdictTag(decision, rating, { large = false } = {}) {
  const variant = TAG_CLASS[rating] || "unrated";
  const text = rating === "evidence" ? "EVIDENCE" : String(decision || "Unresolved").toUpperCase();
  return el("span", `tag tag-${variant}${large ? " tag-lg" : ""}`, text);
}

/** Describe how `current` moved relative to `previous` (both {rating, decision, analysis_date} or null). */
export function describeChange(current, previous) {
  if (!current || !(current.rating in RATING_ORDER)) {
    return { text: current?.rating === "evidence" ? "no rated run" : "unrated", cls: "chg-muted", direction: "none" };
  }
  if (!previous) return { text: "first run", cls: "chg-muted", direction: "first" };
  const delta = RATING_ORDER[current.rating] - RATING_ORDER[previous.rating];
  if (delta === 0) return { text: "unchanged", cls: "chg-muted", direction: "unchanged" };
  const arrow = delta > 0 ? "▲" : "▼";
  return {
    text: `${arrow} from ${String(previous.decision).toUpperCase()} · ${formatDate(previous.analysis_date, "short")}`,
    cls: delta > 0 ? "chg-up" : "chg-down",
    direction: delta > 0 ? "up" : "down",
  };
}

// ---------- evidence & valuation ----------

const EVIDENCE = {
  sufficient: { cls: "ev-ok", glyph: "●", label: "Sufficient" },
  partial: { cls: "ev-warn", glyph: "●", label: "Partial" },
  unsupported: { cls: "ev-warn", glyph: "●", label: "Unsupported" },
  material_conflict: { cls: "ev-bad", glyph: "●", label: "Conflict" },
  legacy: { cls: "ev-muted", glyph: "○", label: "Legacy" },
};

export function evidenceInfo(status) {
  return EVIDENCE[status] || { cls: "ev-muted", glyph: "○", label: capitalize(status) };
}

export function evidenceNode(status, { dotOnly = false } = {}) {
  const info = evidenceInfo(status);
  const node = el("span", `ev ${info.cls}`, dotOnly ? info.glyph : `${info.glyph} ${info.label}`);
  if (dotOnly) node.title = `Evidence: ${info.label}`;
  return node;
}

export function valuationLabel(status) {
  return !status || status === "unknown" ? "—" : capitalize(status);
}

export function horizonLabel(value) {
  if (!value) return null;
  const text = capitalize(value);
  return text === "Long term" ? "Long-term" : text === "Short term" ? "Short-term" : text;
}

export function isCostCarried(run) {
  return Boolean(run.average_cost_as_of && run.average_cost_as_of !== run.analysis_date);
}

// ---------- report sections ----------

export const slugify = (name) => String(name).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

// ---------- sectors ----------

const SHORT_SECTOR_NAMES = { XLK: "Technology", XLC: "Comm. Services", XLY: "Consumer Disc." };

export const sectorName = (point) => point.sector || point.symbol;
export const sectorShortName = (point) => SHORT_SECTOR_NAMES[point.symbol] || sectorName(point);
export const benchmarkName = (benchmark) => (!benchmark || benchmark === "SPY" ? "S&P 500" : benchmark);

export const QUADRANTS = {
  lead: { key: "lead", name: "Leading", description: "ahead of the market and gaining" },
  weak: { key: "weak", name: "Weakening", description: "ahead, but losing ground" },
  impr: { key: "impr", name: "Improving", description: "behind, but catching up" },
  lag: { key: "lag", name: "Lagging", description: "behind the market and fading" },
};

export function quadrantOf(point) {
  const ahead = Number(point.x) >= 0;
  const gaining = Number(point.y) >= 0;
  if (ahead) return gaining ? "lead" : "weak";
  return gaining ? "impr" : "lag";
}

export function validSectorPoints(snapshot) {
  const points = Array.isArray(snapshot?.points) ? snapshot.points : [];
  return points.filter((point) => Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y)));
}
