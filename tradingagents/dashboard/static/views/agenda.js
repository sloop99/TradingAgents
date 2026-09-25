// Agenda (Positions, right column): the rerun queue, then upcoming earnings and dividends by week.

import {
  compactMoney, daysBetween, el, formatDate, link, runHref, signed, timingLabel, verdictTag, weekdayDate,
} from "../format.js";
import { EARNINGS_COMMAND } from "./shared.js";

const VISIBLE_ROWS = 6;
const DAY_MS = 86_400_000;

export function agendaPanel(ctx) {
  const panel = el("section", "panel agenda");
  panel.setAttribute("aria-label", "Upcoming earnings, dividends and reruns");
  const earnings = ctx.earnings;

  const head = el("p", "eyebrow eyebrow-split");
  head.append(el("span", "", "Upcoming · earnings & dividends"));
  if (earnings?.retrieved_at) {
    const source = earnings.source === "Yahoo Finance" ? "yahoo" : earnings.source;
    head.append(el("span", "eyebrow-note", `${source} · ${formatDate(earnings.retrieved_at, "day")}`));
  }
  panel.append(head);

  if (ctx.earningsError) {
    const note = el("p", "chart-note", ctx.earningsError.message);
    const retry = el("button", "btn", "Retry");
    retry.type = "button";
    retry.addEventListener("click", ctx.retryEarnings);
    note.append(" ", retry);
    panel.append(note);
    return panel;
  }
  if (!earnings) {
    panel.append(el("p", "chart-note", "Loading the earnings calendar…"));
    return panel;
  }
  if (earnings.status === "stale") {
    const age = daysBetween(earnings.retrieved_at, earnings.today);
    panel.append(el("p", "banner warn",
      `Earnings dates are ${age} days old; refresh before relying on them.`));
  }

  const summaries = new Map((ctx.data?.tickers || []).map((summary) => [summary.ticker, summary]));
  panel.append(rerunQueue(earnings.alerts || [], summaries));

  if (earnings.status === "unavailable") {
    panel.append(calendarMissing(earnings));
    return panel;
  }

  const list = el("div", "agenda-list");
  const draw = (expanded, refocus = false) => {
    list.replaceChildren(...agendaNodes(earnings, summaries, ctx, expanded, (next) => {
      ctx.setAgendaExpanded(next);
      draw(next, true);
    }));
    if (refocus) list.querySelector(".agenda-more")?.focus();
  };
  draw(ctx.agendaExpanded);
  panel.append(list);

  const foot = footNotes(earnings);
  if (foot.length) panel.append(...foot);
  return panel;
}

// ---------- rerun queue ----------

function rerunQueue(alerts, summaries) {
  const box = el("div", "queue");
  if (!alerts.length) {
    box.append(el("p", "queue-clear", "Research is current."));
    return box;
  }
  box.append(el("p", "queue-head", "Re-run"));
  const weekly = alerts.filter((alert) => alert.kind === "weekly_due");
  let weeklyShown = false;
  for (const alert of alerts) {
    if (alert.kind === "weekly_due" && weekly.length > 1) {
      if (!weeklyShown) box.append(weeklyRow(weekly, summaries));
      weeklyShown = true;
      continue;
    }
    box.append(alertRow(alert));
  }
  return box;
}

function alertRow(alert) {
  const row = alert.run_id ? link(runHref(alert.run_id), "queue-row") : el("div", "queue-row");
  if (alert.group === "watching") row.classList.add("is-watch");
  row.append(dot(alert.severity), el("span", "tk", alert.ticker), el("span", "msg", alert.message));
  return row;
}

function weeklyRow(alerts, summaries) {
  const row = el("div", "queue-row");
  const tickers = el("span", "msg");
  tickers.append("Weekly run due ·");
  alerts.forEach((alert) => {
    const target = alert.run_id || summaries.get(alert.ticker)?.row_run_id;
    const node = target ? link(runHref(target), "tk", alert.ticker) : el("span", "tk", alert.ticker);
    node.title = alert.message;
    tickers.append(" ", node);
  });
  row.append(dot("due"), tickers);
  return row;
}

function dot(severity) {
  const node = el("span", `dot sev-${severity}`, "●");
  node.setAttribute("aria-hidden", "true");
  return node;
}

// ---------- agenda ----------

function agendaNodes(earnings, summaries, ctx, expanded, onToggle) {
  const events = earnings.events || [];
  if (!events.length) return [el("p", "chart-note", "No earnings or dividends in the next 60 days.")];
  const nodes = [];
  let week = null;
  let body = null;
  for (const event of expanded ? events : events.slice(0, VISIBLE_ROWS)) {
    const label = weekLabel(event.date, earnings.today);
    if (label !== week) {
      week = label;
      const table = el("table", "agenda-table");
      table.append(el("caption", "agenda-week", label));
      body = el("tbody");
      table.append(body);
      nodes.push(table);
    }
    body.append(eventRow(event, summaries.get(event.ticker), ctx));
  }
  if (events.length > VISIBLE_ROWS) {
    const more = el("button", "agenda-more", expanded
      ? "Show fewer"
      : `+ ${events.length - VISIBLE_ROWS} more through ${formatDate(events.at(-1).date, "day")}`);
    more.type = "button";
    more.setAttribute("aria-expanded", String(expanded));
    more.addEventListener("click", () => onToggle(!expanded));
    nodes.push(more);
  }
  return nodes;
}

function eventRow(event, summary, ctx) {
  const row = el("tr", `row agenda-row${event.group === "watching" ? " is-watch" : ""}`);
  const dateCell = el("td", "ag-date", `${event.estimated ? "~" : ""}${weekdayDate(event.date)}`);
  const tickerCell = el("td", "ag-tk");
  tickerCell.append(summary ? link(runHref(summary.row_run_id), "", event.ticker) : event.ticker);
  if (event.kind === "earnings") {
    const verdictCell = el("td", "ag-verdict");
    if (summary) verdictCell.append(verdictTag(summary.decision, summary.rating));
    const eps = event.eps?.avg;
    row.append(dateCell, tickerCell, verdictCell,
      el("td", "ag-when", timingLabel(event.timing) || "—"),
      el("td", "ag-eps", eps == null ? "" : `EPS ${signed(eps, 2, { sign: false })}`));
    row.title = earningsTitle(event);
  } else {
    const detail = el("td", "ag-div", event.kind === "ex_dividend" ? "$ ex-dividend" : "$ dividend paid");
    detail.colSpan = 3;
    row.append(dateCell, tickerCell, detail);
    row.title = event.kind === "ex_dividend"
      ? `${event.ticker} goes ex-dividend: own the shares before this date to receive it`
      : `${event.ticker} pays its declared dividend`;
  }
  if (summary) {
    row.addEventListener("click", (clickEvent) => {
      if (!clickEvent.target.closest("a")) ctx.navigate(runHref(summary.row_run_id));
    });
  }
  return row;
}

function earningsTitle(event) {
  const through = event.window_end ? ` to ${formatDate(event.window_end, "long")}` : "";
  const lines = [`${event.ticker} earnings · ${formatDate(event.date, "long")}${through}`];
  if (event.estimated) lines.push("Estimated date: not yet confirmed by the company");
  const timing = timingLabel(event.timing);
  lines.push(timing ? `Reports ${timing}` : "Report time not published");
  const eps = event.eps || {};
  if (eps.avg != null) {
    const range = eps.low != null && eps.high != null
      ? ` (range ${signed(eps.low, 2, { sign: false })} to ${signed(eps.high, 2, { sign: false })})` : "";
    lines.push(`EPS estimate ${signed(eps.avg, 2, { sign: false })}${range}`);
  }
  if (event.revenue?.avg != null) lines.push(`Revenue estimate ${compactMoney(event.revenue.avg)}`);
  return lines.join("\n");
}

/** "This week", "Next week", or "Week of Oct 26 · in 31d" (weeks run Monday to Sunday). */
function weekLabel(date, today) {
  const day = Math.floor(Date.parse(`${date}T00:00:00Z`) / DAY_MS);
  const now = Math.floor(Date.parse(`${today}T00:00:00Z`) / DAY_MS);
  const monday = (value) => value - ((new Date(value * DAY_MS).getUTCDay() + 6) % 7);
  const weeks = Math.round((monday(day) - monday(now)) / 7);
  if (weeks <= 0) return "This week";
  if (weeks === 1) return "Next week";
  const start = new Date(monday(day) * DAY_MS).toISOString().slice(0, 10);
  return `Week of ${formatDate(start, "day")} · in ${monday(day) - now}d`;
}

// ---------- notes ----------

function footNotes(earnings) {
  const notes = [];
  if (earnings.undated?.length) {
    notes.push(el("p", "agenda-foot", `No date published: ${earnings.undated.join(", ")}`));
  }
  if (earnings.missing?.length) {
    notes.push(el("p", "agenda-foot", `Not in the calendar yet: ${earnings.missing.join(", ")}. Refresh it to add them.`));
  }
  const warnings = earnings.warnings || [];
  if (warnings.length) {
    const details = el("details", "agenda-notes");
    details.append(el("summary", "", `Data notes (${warnings.length})`));
    const list = el("ul");
    warnings.forEach((warning) => list.append(el("li", "", warning)));
    details.append(list);
    notes.push(details);
  }
  return notes;
}

function calendarMissing(earnings) {
  const wrap = el("div", "chart-note");
  wrap.append(el("p", "", earnings.warnings?.[0] || "No earnings calendar collected yet."));
  const how = el("p", "");
  how.append("To collect it, run ", el("code", "mono", EARNINGS_COMMAND), " from the project folder, then press Refresh.");
  wrap.append(how);
  return wrap;
}
