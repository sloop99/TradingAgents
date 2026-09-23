// Research Ledger shell: hash routing, data loading, keyboard, theme, search, footer.

import { getRuns, getSectors, refreshRuns } from "./api.js";
import { el, isCostCarried } from "./format.js";
import { renderMarket } from "./views/market.js";
import { renderPositions } from "./views/positions.js";
import { renderReport } from "./views/report.js";

const $ = (selector) => document.querySelector(selector);

const state = {
  data: null,
  runsById: new Map(),
  runsError: null,
  refreshError: null,
  sectors: null,
  sectorsError: null,
  query: "",
  showTests: false,
  selectedKey: null,
  marketFocus: null,
};

let controller = {};
let currentRoute = null;
let renderSerial = 0;

const KEY_HINTS = {
  positions: "↑↓ move · enter open · / search",
  report: "[ ] older/newer run · esc back",
  market: "/ search",
};

// ---------- routing ----------

const isRouteHash = (hash) => !hash || hash === "#" || hash.startsWith("#/");

function parseRoute(hash) {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map((part) => {
    try { return decodeURIComponent(part); } catch { return part; }
  });
  if (parts[0] === "run" && parts[1]) return { name: "report", runId: parts[1], section: parts[2] || null };
  if (parts[0] === "market") return { name: "market" };
  return { name: "positions" };
}

function navigate(hash) {
  if (window.location.hash === hash) render({ force: true });
  else window.location.hash = hash;
}

function context() {
  const serial = renderSerial;
  return {
    data: state.data,
    runsById: state.runsById,
    runsError: state.runsError,
    sectors: state.sectors,
    sectorsError: state.sectorsError,
    query: state.query,
    showTests: state.showTests,
    selectedKey: state.selectedKey,
    marketFocus: state.marketFocus,
    isCurrent: () => serial === renderSerial,
    navigate,
    retry: loadRuns,
    retrySectors: loadSectors,
    setSelected: (key) => { state.selectedKey = key; },
    setMarketFocus: (symbol) => { state.marketFocus = symbol; },
  };
}

function render({ force = false, focusView = false } = {}) {
  const route = isRouteHash(window.location.hash) ? parseRoute(window.location.hash) : currentRoute || parseRoute("");
  if (!force && controller.handles?.(route)) {
    controller.update(route);
    currentRoute = route;
    updateChrome(route);
    return;
  }
  const pageChanged = !currentRoute || currentRoute.name !== route.name || currentRoute.runId !== route.runId;
  controller.destroy?.();
  renderSerial += 1;
  const view = $("#view");
  const ctx = context();
  if (route.name === "report") controller = renderReport(view, ctx, route) || {};
  else if (route.name === "market") controller = renderMarket(view, ctx) || {};
  else controller = renderPositions(view, ctx) || {};
  if (state.refreshError) view.prepend(refreshBanner(state.refreshError));
  currentRoute = route;
  updateChrome(route);
  if (pageChanged) {
    window.scrollTo(0, 0);
    if (focusView) view.focus({ preventScroll: true });
  }
}

function refreshBanner(error) {
  const banner = el("p", "banner bad", `${error.message} Showing the previously loaded archive.`);
  const retry = el("button", "btn", "Retry");
  retry.type = "button";
  retry.addEventListener("click", refresh);
  banner.append(retry);
  const wrap = el("div", "view-pad");
  wrap.append(banner);
  return wrap;
}

// ---------- data ----------

function setData(payload) {
  state.data = payload;
  state.runsById = new Map(payload.runs.map((run) => [run.id, run]));
}

async function loadRuns() {
  state.runsError = null;
  if (!state.data) render({ force: true });
  try {
    setData(await getRuns());
  } catch (error) {
    state.runsError = error;
  }
  render({ force: true });
}

async function loadSectors() {
  state.sectorsError = null;
  try {
    state.sectors = await getSectors();
  } catch (error) {
    state.sectorsError = error;
  }
  if (currentRoute?.name !== "report") render({ force: true });
}

async function refresh() {
  const button = $("#refresh");
  button.disabled = true;
  button.querySelector(".label").textContent = "Refreshing…";
  try {
    setData(await refreshRuns());
    state.refreshError = null;
    state.runsError = null;
    loadSectors();
    render({ force: true });
    toast(`Archive refreshed · ${state.data.summary.runs} runs`);
  } catch (error) {
    state.refreshError = error;
    render({ force: true });
  } finally {
    button.disabled = false;
    button.querySelector(".label").textContent = "Refresh";
  }
}

// ---------- chrome: nav, footer, title ----------

function updateChrome(route) {
  document.querySelectorAll("[data-nav]").forEach((anchor) => {
    const active = anchor.dataset.nav === (route.name === "market" ? "market" : "positions");
    if (active) anchor.setAttribute("aria-current", "page");
    else anchor.removeAttribute("aria-current");
  });
  document.title = route.name === "market" ? "Market · Research Ledger"
    : route.name === "report" && controller.title ? `${controller.title} · Research Ledger`
      : "Research Ledger";
  $("#footer-keys").textContent = KEY_HINTS[route.name];
  updateFooter();
}

function updateFooter() {
  const data = state.data;
  const counts = $("#footer-counts");
  if (!data) {
    counts.textContent = state.runsError ? "ARCHIVE UNAVAILABLE" : "LOADING…";
    return;
  }
  const indexed = new Date(data.generated_at);
  const time = Number.isNaN(indexed.valueOf()) ? "" : ` · INDEXED ${indexed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  counts.textContent = `${data.summary.runs} RUNS · ${data.summary.tickers} TICKERS${time}`;

  const carried = (data.tickers || []).some((summary) => {
    const run = state.runsById.get(summary.row_run_id);
    return summary.group === "holding" && run?.average_cost_usd != null && isCostCarried(run);
  });
  $("#footer-carried").hidden = !(carried && currentRoute?.name === "positions");

  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  const details = $("#footer-warnings");
  details.hidden = !warnings.length;
  if (warnings.length) {
    details.querySelector("summary").textContent = `${warnings.length} index warning${warnings.length === 1 ? "" : "s"}`;
    details.querySelector("ul").replaceChildren(...warnings.map((warning) => el("li", "", warning)));
  }
}

let toastTimer = 0;
function toast(message) {
  const node = $("#status");
  node.textContent = message;
  node.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => node.classList.remove("show"), 2600);
}

// ---------- theme ----------

const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
const effectiveTheme = () => document.documentElement.dataset.theme || (darkQuery.matches ? "dark" : "light");

function syncThemeButton() {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  const button = $("#theme-toggle");
  button.setAttribute("aria-label", `Switch to ${next} theme`);
  button.title = `Switch to ${next} theme`;
}

function toggleTheme() {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("ledger-theme", next); } catch { /* Not persisted; still applies now. */ }
  syncThemeButton();
}

// ---------- input ----------

function onKeyDown(event) {
  if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
  const search = $("#search");
  const target = event.target instanceof Element ? event.target : null;
  if (target?.closest("input, textarea, select, [contenteditable='true']")) {
    if (event.key === "Escape" && target === search) {
      event.preventDefault();
      if (search.value) {
        search.value = "";
        state.query = "";
        render({ force: true });
      } else {
        search.blur();
      }
    }
    return;
  }
  if (event.key === "/") {
    event.preventDefault();
    if (currentRoute?.name !== "positions") window.location.hash = "#/";
    search.focus();
    search.select();
    return;
  }
  if ((event.key === "Enter" || event.key === " ") && target?.closest("a, button, summary")) return;
  if (controller.onKey?.(event)) event.preventDefault();
}

function onSearch(event) {
  state.query = event.target.value;
  state.selectedKey = null;
  if (currentRoute?.name !== "positions") window.location.hash = "#/";
  else render({ force: true });
}

// ---------- boot ----------

function boot() {
  $("#search").addEventListener("input", onSearch);
  $("#refresh").addEventListener("click", refresh);
  $("#theme-toggle").addEventListener("click", toggleTheme);
  $("#show-tests").addEventListener("change", (event) => {
    state.showTests = event.target.checked;
    render({ force: true });
  });
  darkQuery.addEventListener("change", syncThemeButton);
  document.addEventListener("keydown", onKeyDown);
  window.addEventListener("hashchange", () => {
    if (!isRouteHash(window.location.hash)) return;
    render({ focusView: document.activeElement !== $("#search") });
  });
  syncThemeButton();
  loadRuns();
  loadSectors();
}

boot();
