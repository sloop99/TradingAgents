// Sector rotation chart shared by the Positions panel ("compact") and the Market page ("full").
// Dots are colored by quadrant and labeled with sector names; labels are placed greedily so
// they never collide (full size adds leader lines, compact drops a label to its tooltip).

import {
  benchmarkName, formatDate, quadrantOf, QUADRANTS, sectorName, sectorShortName, signed, svg, validSectorPoints,
} from "./format.js";

let chartSerial = 0;

/**
 * Render into `container` and keep it sized to the container's width.
 * options: { size: "full" | "compact", focus: symbol | null, onFocus(symbol | null) }
 */
export function mountSectorChart(container, snapshot, options = {}) {
  let settings = { size: "full", focus: null, ...options };
  let lastWidth = 0;
  const draw = () => {
    const width = Math.round(container.clientWidth);
    if (!width) return;
    lastWidth = width;
    drawChart(container, snapshot, width, settings);
  };
  const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => {
    const width = Math.round(container.clientWidth);
    if (Math.abs(width - lastWidth) >= 2) window.requestAnimationFrame(draw);
  });
  observer?.observe(container);
  draw();
  return {
    update(next) { settings = { ...settings, ...next }; draw(); },
    destroy() { observer?.disconnect(); },
  };
}

function drawChart(container, snapshot, width, { size, focus, onFocus }) {
  const full = size === "full";
  const points = validSectorPoints(snapshot);
  const focused = full ? points.find((point) => point.symbol === focus) || null : null;
  const narrow = width <= 520;
  const height = full
    ? Math.min(narrow ? 360 : 470, Math.max(300, Math.round(width * 0.62)))
    : Math.min(300, Math.round(width * 0.75));

  const pad = full ? 10 : 6;
  const plot = { left: pad, top: pad, right: width - pad, bottom: height - pad };
  const cx = (plot.left + plot.right) / 2;
  const cy = (plot.top + plot.bottom) / 2;
  const inset = full ? 28 : 18;
  const halfW = (plot.right - plot.left) / 2 - inset;
  const halfH = (plot.bottom - plot.top) / 2 - inset;

  const extentValues = focused ? [...points, ...trailOf(focused)] : points;
  const maxX = Math.max(2, ...extentValues.map((item) => Math.abs(Number(item.x)))) * 1.15;
  const maxY = Math.max(2, ...extentValues.map((item) => Math.abs(Number(item.y)))) * 1.15;
  const sx = (value) => cx + (Number(value) / maxX) * halfW;
  const sy = (value) => cy - (Number(value) / maxY) * halfH;

  const counts = { lead: 0, weak: 0, impr: 0, lag: 0 };
  points.forEach((point) => { counts[quadrantOf(point)] += 1; });
  const bench = benchmarkName(snapshot?.benchmark);
  const root = svg("svg", {
    viewBox: `0 0 ${width} ${height}`,
    width, height,
    class: `sector-chart ${full ? "full" : "compact"}`,
    role: "img",
    "aria-label": `Sector rotation versus the ${bench}: ${counts.lead} leading, ${counts.weak} weakening, `
      + `${counts.impr} improving and ${counts.lag} lagging sectors.`,
  });
  container.replaceChildren(root);

  const clipId = `sc-clip-${++chartSerial}`;
  const defs = svg("defs");
  const clip = svg("clipPath", { id: clipId });
  clip.append(svg("rect", { x: plot.left, y: plot.top, width: plot.right - plot.left, height: plot.bottom - plot.top }));
  defs.append(clip);
  root.append(defs);

  root.append(svg("rect", { class: "sc-plot", x: plot.left, y: plot.top, width: plot.right - plot.left, height: plot.bottom - plot.top }));
  root.append(svg("rect", { class: "sc-tint-lead", x: cx, y: plot.top, width: plot.right - cx, height: cy - plot.top }));
  root.append(svg("rect", { class: "sc-tint-lag", x: plot.left, y: cy, width: cx - plot.left, height: plot.bottom - cy }));
  root.append(svg("line", { class: "sc-zero", x1: cx, y1: plot.top, x2: cx, y2: plot.bottom }));
  root.append(svg("line", { class: "sc-zero", x1: plot.left, y1: cy, x2: plot.right, y2: cy }));

  const obstacles = [];
  const addText = (attrs, text) => {
    const node = svg("text", attrs, text);
    root.append(node);
    obstacles.push(boxOf(node, attrs, text));
    return node;
  };

  // Quadrant corners: name everywhere, plain-English meaning on the full chart.
  const corners = [
    ["impr", plot.left + 8, "start", "top"], ["lead", plot.right - 8, "end", "top"],
    ["lag", plot.left + 8, "start", "bottom"], ["weak", plot.right - 8, "end", "bottom"],
  ];
  corners.forEach(([key, x, anchor, edge]) => {
    const quadrant = QUADRANTS[key];
    const nameY = edge === "top" ? plot.top + 13 : plot.bottom - (full ? 18 : 6);
    addText({ class: `sc-corner q-${key}`, x, y: nameY, "text-anchor": anchor }, quadrant.name.toUpperCase());
    if (full) {
      addText({ class: "sc-corner-desc", x, y: nameY + 11, "text-anchor": anchor }, cornerDescription(key, narrow));
    }
  });

  if (full) {
    const roomy = cx - plot.left > 300;
    addText({ class: "sc-caption", x: cx - 6, y: plot.bottom - 6, "text-anchor": "end" },
      roomy ? `← UNDERPERFORMING ${bench.toUpperCase()}` : "← BEHIND");
    addText({ class: "sc-caption", x: cx + 6, y: plot.bottom - 6, "text-anchor": "start" },
      roomy ? `OUTPERFORMING ${bench.toUpperCase()} →` : "AHEAD →");
    [["GAINING ↑", plot.top + 44], ["↓ FADING", plot.bottom - 44]].forEach(([text, y]) => {
      const node = svg("text", { class: "sc-caption", x: cx + 11, y, "text-anchor": "middle", transform: `rotate(-90 ${cx + 11} ${y})` }, text);
      root.append(node);
      const length = text.length * 5.4;
      obstacles.push({ left: cx + 3, top: y - length / 2, width: 11, height: length });
    });
  }

  // Trails: the focused sector's is permanent; hovering another dot previews its trail.
  const trails = svg("g", { "clip-path": `url(#${clipId})` });
  const hoverTrail = svg("g", { "clip-path": `url(#${clipId})` });
  root.append(trails, hoverTrail);
  const drawTrail = (group, point) => {
    const path = trailOf(point);
    if (!path.length) return;
    const key = quadrantOf(point);
    const coordinates = [...path, point].map((item) => `${sx(item.x)},${sy(item.y)}`).join(" ");
    group.append(svg("polyline", { class: `sc-trail q-${key}`, points: coordinates }));
    path.forEach((item) => group.append(svg("circle", { class: `sc-trail-dot q-${key}`, cx: sx(item.x), cy: sy(item.y), r: 2.2 })));
    const oldest = path[0];
    group.append(svg("text", { class: "sc-trail-date", x: sx(oldest.x) + 5, y: sy(oldest.y) - 5 }, formatDate(oldest.date, "short")));
  };
  if (focused) drawTrail(trails, focused);

  const labelItems = [];
  const dots = svg("g");
  root.append(dots);
  points.forEach((point) => {
    const key = quadrantOf(point);
    const isFocused = focused?.symbol === point.symbol;
    const radius = full ? (isFocused ? 7 : 5.5) : 4;
    const x = sx(point.x);
    const y = sy(point.y);
    const dot = svg("circle", { class: `sc-dot q-${key}${isFocused ? " focused" : ""}`, cx: x, cy: y, r: radius });
    dot.append(svg("title", {}, `${sectorName(point)} (${point.symbol}): ${signed(point.x, 2)} pts vs the ${bench} over 13 weeks, `
      + `${signed(point.y, 2)} pts 4-week change, as of ${formatDate(point.as_of, "long")}`));
    if (full) {
      dot.addEventListener("click", () => onFocus?.(isFocused ? null : point.symbol));
      dot.addEventListener("mouseenter", () => { if (!isFocused) drawTrail(hoverTrail, point); });
      dot.addEventListener("mouseleave", () => hoverTrail.replaceChildren());
    }
    dots.append(dot);
    obstacles.push({ left: x - radius - 1, top: y - radius - 1, width: 2 * radius + 2, height: 2 * radius + 2 });
    labelItems.push({ point, key, x, y, radius, text: sectorShortName(point) });
  });

  const labels = svg("g");
  root.append(labels);
  placeLabels(labels, labelItems, plot, obstacles, { full, cx, cy });
}

const CORNER_TEXT = {
  lead: ["ahead of the market, gaining", "ahead, gaining"],
  impr: ["behind the market, gaining", "behind, gaining"],
  weak: ["ahead of the market, fading", "ahead, fading"],
  lag: ["behind the market, fading", "behind, fading"],
};
const cornerDescription = (key, narrow) => CORNER_TEXT[key][narrow ? 1 : 0];

function trailOf(point) {
  const trail = Array.isArray(point.trail) ? point.trail : [];
  const valid = trail.filter((item) => Number.isFinite(Number(item.x)) && Number.isFinite(Number(item.y)));
  // The last trail entry is the current week, which the dot itself already marks.
  return valid.length && valid.at(-1).date === point.as_of ? valid.slice(0, -1) : valid;
}

function measure(node, fallbackText) {
  try {
    const box = node.getBBox();
    if (box.width > 0) return box;
  } catch { /* Not rendered yet: estimate below. */ }
  const size = parseFloat(getComputedStyle(node).fontSize) || 10;
  return { x: 0, y: -size * 0.8, width: String(fallbackText).length * size * 0.58, height: size * 1.2 };
}

function boxOf(node, attrs, text) {
  const box = measure(node, text);
  return { left: box.x, top: box.y, width: box.width, height: box.height };
}

const overlaps = (a, b) => a.left < b.left + b.width + 1 && a.left + a.width + 1 > b.left
  && a.top < b.top + b.height + 1 && a.top + a.height + 1 > b.top;

function placeLabels(group, items, plot, obstacles, { full, cx, cy }) {
  // Most crowded dots choose first, so the cluster gets first pick of the free space.
  items.forEach((item) => {
    item.crowd = items.filter((other) => other !== item && Math.hypot(other.x - item.x, other.y - item.y) < 60).length;
  });
  items.sort((a, b) => b.crowd - a.crowd || Math.hypot(a.x - cx, a.y - cy) - Math.hypot(b.x - cx, b.y - cy));
  const placed = [...obstacles];

  items.forEach((item) => {
    const text = svg("text", { class: `sc-label q-${item.key}`, x: 0, y: 0 }, item.text);
    group.append(text);
    const metrics = measure(text, item.text);
    const width = metrics.width;
    const height = metrics.height;
    const ascent = -metrics.y;

    const candidate = (ring, index) => {
      const d = item.radius + 4 + ring * 16;
      const k = d * 0.72;
      const options = [
        [d, 0, "start", "middle"], [-d, 0, "end", "middle"], [0, -d, "middle", "bottom"], [0, d, "middle", "top"],
        [k, -k, "start", "bottom"], [-k, -k, "end", "bottom"], [k, k, "start", "top"], [-k, k, "end", "top"],
      ];
      const [dx, dy, anchor, vertical] = options[index];
      const ax = item.x + dx;
      const ay = item.y + dy;
      const left = anchor === "start" ? ax : anchor === "end" ? ax - width : ax - width / 2;
      const top = vertical === "bottom" ? ay - height : vertical === "top" ? ay : ay - height / 2;
      return { box: { left, top, width, height }, anchor, ax, baseline: top + ascent };
    };
    const fits = ({ box }) => box.left >= plot.left + 2 && box.top >= plot.top + 2
      && box.left + box.width <= plot.right - 2 && box.top + box.height <= plot.bottom - 2
      && !placed.some((other) => overlaps(box, other));
    const search = (ring) => {
      for (let index = 0; index < 8; index += 1) {
        const option = candidate(ring, index);
        if (fits(option)) return option;
      }
      return null;
    };

    let choice = search(0);
    let leader = false;
    if (!choice && full) {
      choice = search(1) || search(2);
      if (!choice) {
        // Nothing free: keep the name visible at the outer ring, on whichever side stays in the plot.
        choice = candidate(2, item.x > cx ? 1 : 0);
      }
      leader = true;
    }
    if (!choice) { text.remove(); return; }

    text.setAttribute("x", String(choice.ax));
    text.setAttribute("y", String(choice.baseline));
    text.setAttribute("text-anchor", choice.anchor);
    if (leader) {
      const { box } = choice;
      const tx = Math.min(Math.max(item.x, box.left), box.left + box.width);
      const ty = Math.min(Math.max(item.y, box.top), box.top + box.height);
      const distance = Math.hypot(tx - item.x, ty - item.y) || 1;
      const sx = item.x + ((tx - item.x) / distance) * (item.radius + 1);
      const sy = item.y + ((ty - item.y) / distance) * (item.radius + 1);
      group.insertBefore(svg("line", { class: "sc-leader", x1: sx, y1: sy, x2: tx, y2: ty }), text);
    }
    placed.push(choice.box);
  });
}
