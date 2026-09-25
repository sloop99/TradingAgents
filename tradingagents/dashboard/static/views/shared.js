// View pieces used by more than one page.

import { el, formatDate } from "../format.js";

export const SECTOR_COMMAND = ".\\.venv\\Scripts\\python.exe scripts\\research.py sectors";
export const EARNINGS_COMMAND = ".\\.venv\\Scripts\\python.exe scripts\\research.py earnings";

export function errorPanel(title, error, onRetry = null) {
  const panel = el("section", "panel error-panel");
  panel.setAttribute("role", "alert");
  panel.append(el("h2", "heading", title), el("p", "", error?.message || String(error || "Something went wrong.")));
  if (onRetry) {
    const button = el("button", "btn", "Retry");
    button.type = "button";
    button.addEventListener("click", onRetry);
    panel.append(button);
  }
  return panel;
}

export function loadingState(text) {
  return el("p", "state", text);
}

/** A stale/unavailable banner for a sector snapshot, or null when it's fine. */
export function sectorBanner(snapshot) {
  if (snapshot?.status === "stale") {
    return el("p", "banner warn",
      `Stored sector snapshot is stale (as of ${formatDate(snapshot.as_of, "long")}). Refresh it before reading current conditions.`);
  }
  return null;
}

/** Explain why there is nothing to plot, and how to collect a snapshot. */
export function sectorMissing(snapshot, error = null) {
  const reason = error?.message || snapshot?.warnings?.[0] || "No sector observations are stored yet.";
  const wrap = el("div", "chart-note");
  wrap.append(el("p", "", reason));
  const how = el("p", "");
  how.append("To collect a snapshot, run ", el("code", "mono", SECTOR_COMMAND), " from the project folder, then press Refresh.");
  wrap.append(how);
  return wrap;
}
