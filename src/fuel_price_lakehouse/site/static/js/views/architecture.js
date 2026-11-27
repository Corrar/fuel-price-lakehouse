// The pipeline architecture as an inline SVG, drawn from a small node/edge
// table in two layouts (left to right on wide screens, top to bottom on
// phones), inside a frame that says where it runs. Row and check counts in
// the nodes come from the quality report of the latest run. Colours are CSS
// custom properties, so the diagram follows the theme without a redraw.

import { s } from "../dom.js";
import { t, fmt } from "../i18n.js";

// stage = position along the flow, lane 0 = main path, lane 1 = side branch
const NODES = [
  { id: "anp", stage: 0, lane: 0, kind: "source" },
  { id: "bcb", stage: 0, lane: 1, kind: "source" },
  { id: "collect", stage: 1, lane: 0, kind: "process" },
  { id: "raw", stage: 2, lane: 0, kind: "store" },
  { id: "stage", stage: 3, lane: 0, kind: "process" },
  { id: "staged", stage: 4, lane: 0, kind: "store" },
  { id: "quarantine", stage: 4, lane: 1, kind: "side" },
  { id: "curated", stage: 5, lane: 0, kind: "store" },
  { id: "checks", stage: 6, lane: 0, kind: "process" },
  { id: "site", stage: 7, lane: 0, kind: "output" },
  { id: "report", stage: 7, lane: 1, kind: "output" },
];
const EDGES = [
  ["anp", "collect"],
  ["bcb", "collect"],
  ["collect", "raw"],
  ["raw", "stage"],
  ["stage", "staged"],
  ["stage", "quarantine"],
  ["staged", "curated"],
  ["curated", "checks"],
  ["checks", "site"],
  ["checks", "report"],
  ["quarantine", "report"],
];

/** The live line of a node ("6.976.741 linhas", "15 verificações"), or null. */
function countLine(id, quality) {
  const funnel = quality?.funnel;
  const rows = (n) => (n == null ? null : t("ab.count.rows", { n: fmt.int(n) }));
  switch (id) {
    case "raw":
      return rows(funnel?.rows_raw);
    case "staged":
      return rows(funnel?.rows_staged);
    case "quarantine":
      return rows(funnel?.rows_quarantined);
    case "curated":
      return rows(funnel?.rows_curated);
    case "stage":
      return Array.isArray(quality?.rules) ? t("ab.count.rules", { n: quality.rules.length }) : null;
    case "checks":
      return Array.isArray(quality?.checks) ? t("ab.count.checks", { n: quality.checks.length }) : null;
    case "collect":
      return funnel?.files != null ? t("ab.count.files", { n: fmt.int(funnel.files) }) : null;
    default:
      return null;
  }
}

function wrapWords(text, maxChars) {
  const lines = [];
  let line = "";
  for (const word of String(text).split(/\s+/)) {
    if (line && `${line} ${word}`.length > maxChars) {
      lines.push(line);
      line = word;
    } else {
      line = line ? `${line} ${word}` : word;
    }
  }
  if (line) lines.push(line);
  return lines;
}

export function diagram(horizontal, quality) {
  const W = horizontal ? 124 : 156;
  const H = horizontal ? 80 : 66;
  const gapMain = horizontal ? 18 : 20; // between stages
  const gapLane = horizontal ? 26 : 18; // between lanes
  const pad = 12; // frame padding
  const top = 34; // room for the frame label
  const stages = Math.max(...NODES.map((n) => n.stage)) + 1;
  // wide: the frame label sits above the nodes; tall: below the last node
  const pos = (node) =>
    horizontal ? { x: node.stage * (W + gapMain), y: top + node.lane * (H + gapLane) } : { x: pad + node.lane * (W + gapLane), y: node.stage * (H + gapMain) + (node.stage ? pad : 0) };
  const width = horizontal ? stages * W + (stages - 1) * gapMain + pad : 2 * W + gapLane + 2 * pad;
  const height = horizontal ? top + 2 * H + gapLane + pad : stages * H + (stages - 1) * gapMain + pad + top;
  const byId = new Map(NODES.map((n) => [n.id, { ...n, ...pos(n) }]));

  const svg = s("svg", { class: "arch", viewBox: `-2 -2 ${width + 4} ${height + 4}`, width: "100%", style: { maxWidth: `${width + 4}px` }, role: "img", "aria-label": t("ab.arch.alt") });
  svg.append(s("defs", null, s("marker", { id: "arch-arrow", viewBox: "0 0 8 8", refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, s("path", { d: "M0 0.8L7 4L0 7.2z", class: "arch-head" }))));

  // the frame: everything from collection onwards runs on the schedule
  const collect = byId.get("collect");
  const frame = horizontal
    ? { x: collect.x - pad, y: 2, w: width - collect.x + pad - 1, h: height - 4 }
    : { x: 2, y: collect.y - pad + 2, w: width - 4, h: height - (collect.y - pad + 2) - 2 };
  svg.append(s("rect", { class: "arch-frame", x: frame.x, y: frame.y, width: frame.w, height: frame.h, rx: 10 }));
  svg.append(s("text", { class: "arch-frame-label", x: frame.x + 12, y: horizontal ? frame.y + 19 : frame.y + frame.h - 12 }, t("ab.arch.frame")));

  for (const [fromId, toId] of EDGES) {
    const a = byId.get(fromId);
    const b = byId.get(toId);
    let d;
    if (horizontal) {
      const [x1, y1, x2, y2] = [a.x + W, a.y + H / 2, b.x, b.y + H / 2];
      const mid = b.x - gapMain / 2;
      d = y1 === y2 ? `M${x1} ${y1}H${x2 - 1}` : `M${x1} ${y1}H${mid}V${y2}H${x2 - 1}`;
    } else {
      const [x1, y1, x2, y2] = [a.x + W / 2, a.y + H, b.x + W / 2, b.y];
      const mid = b.y - Math.min(gapMain, (b.y - y1) / 2);
      d = x1 === x2 ? `M${x1} ${y1}V${y2 - 1}` : `M${x1} ${y1}V${mid}H${x2}V${y2 - 1}`;
    }
    svg.append(s("path", { d, class: `arch-edge${toId === "quarantine" || fromId === "quarantine" ? " is-side" : ""}`, "marker-end": "url(#arch-arrow)" }));
  }

  for (const node of byId.values()) {
    const group = s("g", { class: `arch-node is-${node.kind}`, transform: `translate(${node.x},${node.y})` });
    group.append(s("rect", { width: W, height: H, rx: node.kind === "process" ? H / 2 : 4 }));
    const live = countLine(node.id, quality);
    const lines = wrapWords(t(`ab.node.${node.id}.sub`), horizontal ? 19 : 24).slice(0, live ? 2 : 3);
    const total = 15 + (lines.length + (live ? 1 : 0)) * 13;
    let y = (H - total) / 2 + 12;
    group.append(s("text", { x: W / 2, y, class: "arch-title", "text-anchor": "middle" }, t(`ab.node.${node.id}`)));
    if (live) {
      y += 14;
      group.append(s("text", { x: W / 2, y: y + 1, class: "arch-count", "text-anchor": "middle" }, live));
    }
    for (const line of lines) {
      y += 13;
      group.append(s("text", { x: W / 2, y: y + 2, class: "arch-sub", "text-anchor": "middle" }, line));
    }
    svg.append(group);
  }
  return svg;
}
