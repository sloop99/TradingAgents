// Markdown → DOM for model-written reports. Everything is built with createElement and
// textContent; report text is never parsed as HTML, so markup inside a report stays inert.

import { el } from "./format.js";

const LIST_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/;
const FENCE = /^\s{0,3}(`{3,}|~{3,})/;
const RULE = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^\s{0,3}>/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{2,}/;

export function renderMarkdown(markdown) {
  const root = el("div", "md");
  appendBlocks(root, String(markdown || "").replace(/\r\n?/g, "\n").split("\n"));
  return root;
}

function appendBlocks(parent, lines) {
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }

    const fence = line.match(FENCE);
    if (fence) {
      const body = [];
      index += 1;
      while (index < lines.length && !lines[index].trimStart().startsWith(fence[1])) body.push(lines[index++]);
      index += 1;
      const pre = el("pre");
      pre.append(el("code", "", body.join("\n")));
      parent.append(pre);
      continue;
    }

    const heading = line.match(HEADING);
    if (heading) {
      const node = el(`h${heading[1].length}`);
      appendInline(node, heading[2]);
      parent.append(node);
      index += 1;
      continue;
    }

    if (RULE.test(line)) {
      parent.append(el("hr"));
      index += 1;
      continue;
    }

    if (QUOTE.test(line)) {
      const inner = [];
      while (index < lines.length && QUOTE.test(lines[index])) inner.push(lines[index++].replace(/^\s{0,3}>\s?/, ""));
      const quote = el("blockquote");
      appendBlocks(quote, inner);
      parent.append(quote);
      continue;
    }

    if (isTableStart(lines, index)) {
      const rows = [];
      while (index < lines.length && TABLE_ROW.test(lines[index])) rows.push(lines[index++]);
      parent.append(renderTable(rows));
      continue;
    }

    if (LIST_ITEM.test(line)) {
      const [list, next] = parseList(lines, index);
      parent.append(list);
      index = next;
      continue;
    }

    const chunk = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim() && !startsBlock(lines, index)) chunk.push(lines[index++].trim());
    const paragraph = el("p");
    appendInline(paragraph, chunk.join(" "));
    parent.append(paragraph);
  }
}

function startsBlock(lines, index) {
  const line = lines[index];
  return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line)
    || LIST_ITEM.test(line) || isTableStart(lines, index);
}

function isTableStart(lines, index) {
  return TABLE_ROW.test(lines[index]) && index + 1 < lines.length && TABLE_SEPARATOR.test(lines[index + 1])
    && lines[index + 1].includes("-");
}

const indentWidth = (whitespace) => whitespace.replace(/\t/g, "    ").length;
const isOrdered = (marker) => /\d/.test(marker);

/** Parse a (possibly nested) list starting at `start`; returns [listElement, nextIndex]. */
function parseList(lines, start) {
  const first = lines[start].match(LIST_ITEM);
  const baseIndent = indentWidth(first[1]);
  const ordered = isOrdered(first[2]);
  const list = el(ordered ? "ol" : "ul");
  if (ordered) {
    const startNumber = parseInt(first[2], 10);
    if (startNumber !== 1) list.start = startNumber;
  }
  let item = null;
  let index = start;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      let next = index + 1;
      while (next < lines.length && !lines[next].trim()) next += 1;
      const upcoming = next < lines.length ? lines[next].match(LIST_ITEM) : null;
      if (upcoming && indentWidth(upcoming[1]) >= baseIndent) { index = next; continue; }
      break;
    }
    const match = line.match(LIST_ITEM);
    const indent = indentWidth(match ? match[1] : line.match(/^\s*/)[0]);
    if (indent < baseIndent) break;
    if (match && indent < baseIndent + 2) {
      if (isOrdered(match[2]) !== ordered) break;
      item = el("li");
      appendInline(item, match[3]);
      list.append(item);
      index += 1;
      continue;
    }
    if (!item) break;
    if (match) {
      const [nested, next] = parseList(lines, index);
      item.append(nested);
      index = next;
      continue;
    }
    if (indent >= baseIndent + 2) {
      item.append(" ");
      appendInline(item, line.trim());
      index += 1;
      continue;
    }
    break;
  }
  return [list, index];
}

function splitRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function renderTable(lines) {
  const wrap = el("div", "table-wrap");
  const table = el("table");
  const [header, , ...body] = lines;
  const thead = el("thead");
  const headRow = el("tr");
  splitRow(header).forEach((cell) => { const th = el("th"); appendInline(th, cell); headRow.append(th); });
  thead.append(headRow);
  const tbody = el("tbody");
  body.forEach((line) => {
    const row = el("tr");
    splitRow(line).forEach((cell) => { const td = el("td"); appendInline(td, cell); row.append(td); });
    tbody.append(row);
  });
  table.append(thead, tbody);
  wrap.append(table);
  return wrap;
}

// Code spans, bold, italic (* or _ at word boundaries, so snake_case survives), links.
const INLINE = /(`[^`]+`)|(\*\*[^*]+?\*\*|__[^_]+?__)|(\*[^*\s](?:[^*]*?[^*\s])?\*|(?<![\w])_[^_\s](?:[^_]*?[^_\s])?_(?![\w]))|(\[[^\]]+\]\([^)\s]+\))/g;

export function appendInline(parent, text) {
  let cursor = 0;
  for (const match of String(text).matchAll(INLINE)) {
    if (match.index > cursor) parent.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    if (match[1]) {
      parent.append(el("code", "", token.slice(1, -1)));
    } else if (match[2]) {
      const strong = el("strong");
      appendInline(strong, token.slice(2, -2));
      parent.append(strong);
    } else if (match[3]) {
      const em = el("em");
      appendInline(em, token.slice(1, -1));
      parent.append(em);
    } else {
      parent.append(renderLink(token));
    }
    cursor = match.index + token.length;
  }
  if (cursor < text.length) parent.append(document.createTextNode(text.slice(cursor)));
}

function renderLink(token) {
  const [, label, target] = token.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
  let url = null;
  try { url = new URL(target); } catch { /* Relative or local reference. */ }
  if (url && (url.protocol === "http:" || url.protocol === "https:")) {
    const anchor = el("a");
    appendInline(anchor, label);
    anchor.href = url.href;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    return anchor;
  }
  const span = el("span", "local-ref");
  appendInline(span, label);
  span.title = `Local source retained in the report: ${target}`;
  return span;
}
