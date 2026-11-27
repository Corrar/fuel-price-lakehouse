// The time-series chart every view uses, and the sparkline of the price board.
// Observable Plot draws the static picture (axes, lines, bands, gap shading);
// D3 adds what Plot leaves out: direct end labels with collision handling,
// reference labels placed where no line runs, a crosshair with an HTML tooltip
// listing every series, keyboard reading of the same values, a week marker,
// status-shaped points and the event markers.
//
// Conventions kept by every chart here:
//  - one y axis, never two; the domain hugs the data (no rounding up to a
//    "nice" number that leaves an empty band);
//  - null is a gap (lines break, weeks with no value are shaded), never zero;
//  - text is drawn in ink, the coloured mark beside it carries identity;
//  - a tooltip never holds a value that is not also in the table view.

import { d3, Plot } from "./lib.js";
import { h } from "./dom.js";
import { t, fmt } from "./i18n.js";
import { tokens } from "./theme.js";
import { lastValid, gapRuns } from "./series.js";
import { showTip, hideTip, claimCrosshair, releaseCrosshair } from "./tooltip.js";
import { addEventMarkers } from "./chart-events.js";

const DAY_MS = 86_400_000;
/** Beyond this many weeks before the end of the window, a line's end gets no label. */
const STALE_LABEL_WEEKS = 13;
const FONT_FAMILY = '"IBM Plex Sans", system-ui, sans-serif';
const SYMBOLS = { pass: d3.symbolCircle, warn: d3.symbolTriangle, fail: d3.symbolSquare };

let measureContext = null;
function textWidth(text, size = 12, weight = 400) {
  if (!measureContext) measureContext = document.createElement("canvas").getContext("2d");
  if (!measureContext) return String(text).length * size * 0.56;
  measureContext.font = `${weight} ${size}px ${FONT_FAMILY}`;
  return measureContext.measureText(String(text)).width;
}

/** Tick dates and a locale-aware formatter for a UTC time axis. */
function timeAxis(first, last, plotWidth) {
  const isYearStart = (d) => d.getUTCMonth() === 0 && d.getUTCDate() === 1;
  // Year labels are short, so a multi-year axis can carry more ticks than one
  // labelled by month or day: try the dense spacing first and keep it only if
  // every tick falls on a 1 January.
  let ticks = d3.utcTicks(first, last, Math.max(2, Math.floor(plotWidth / 54)));
  let yearly = ticks.length > 0 && ticks.every(isYearStart);
  if (!yearly) {
    ticks = d3.utcTicks(first, last, Math.max(2, Math.floor(plotWidth / 84)));
    yearly = ticks.length > 0 && ticks.every(isYearStart);
  }
  // A series that starts in the first days of a year would leave that year
  // unlabelled (its 1 January is just before the domain): label the start.
  if (yearly && first.getUTCMonth() === 0 && first.getUTCDate() <= 14 && !isYearStart(first)) {
    const step = ticks.length > 1 ? ticks[1].getUTCFullYear() - ticks[0].getUTCFullYear() : 1;
    if (step === 1 && (!ticks.length || ticks[0].getUTCFullYear() > first.getUTCFullYear())) ticks = [first, ...ticks];
  }
  // Ticks finer than a day (the run history of a young dashboard) would repeat
  // the same day label: tick by whole days instead, or once within a single day.
  if (!yearly && ticks.some((d) => d.getTime() % DAY_MS !== 0)) {
    const days = Math.ceil((last - first) / DAY_MS);
    const step = Math.max(1, Math.ceil(days / Math.max(2, Math.floor(plotWidth / 84))));
    ticks = d3.utcDay.every(step).range(d3.utcDay.ceil(first), new Date(last.getTime() + 1));
    if (!ticks.length) ticks = [first];
  }
  const monthly = ticks.every((d) => d.getUTCDate() === 1);
  const format = (date, index) => {
    if (yearly) return String(date.getUTCFullYear());
    if (monthly) {
      const month = fmt.monthShort(date);
      const withYear = index === 0 || date.getUTCMonth() === 0;
      return withYear ? `${month}\n${date.getUTCFullYear()}` : month;
    }
    const day = fmt.dayMonth(date);
    return index === 0 ? `${day}\n${date.getUTCFullYear()}` : day;
  };
  return { ticks, format, twoLines: !yearly };
}

/** Domain and ticks that hug the visible values instead of rounding outwards. */
function yScale(lo, hi, { zero }) {
  const span = hi - lo || Math.abs(hi) * 0.1 || 1;
  const domain = [zero && lo === 0 ? 0 : lo - span * 0.06, hi + span * 0.06];
  let ticks = d3.ticks(domain[0], domain[1], 5);
  if (ticks.length < 3) ticks = d3.ticks(domain[0], domain[1], 8);
  return { domain, ticks };
}

/**
 * Draw a time-series chart into `host`.
 *
 * config:
 *   dates        Date[] aligned with every series
 *   i0, i1       first and last index shown
 *   series       [{label, short, color, values, lo?, hi?, dash?, width?, quiet?}]
 *                `quiet` series are context: thin, recessive, no end label,
 *                no hover dot and not listed by the default tooltip
 *   height       px, includes the x-axis band
 *   yFormat      (value) => text for axis ticks and end labels
 *   zero         include zero in the y domain
 *   refs         [{y, label?, strong?}] horizontal reference rules
 *   marker       index of a week to mark with a vertical rule (and its label)
 *   points       [{i, status}] status-shaped points on the first series
 *   events       [{n, when, dateText, title, detail, url}] dated annotations
 *   annotations  [{i, series?, text}] one labelled point (index into dates)
 *   endLabels    "value" | "name" | "both" | false
 *   marginRight  fixed right margin (to align two stacked charts)
 *   insetLeft    fixed left inset, minInsetLeft a lower bound (same purpose)
 *   bandOpacity  fill opacity of the lo..hi band
 *   tip          (index) => Node, the tooltip for one date
 *   onIndex      (index|null) => void, the crosshair moved (for syncing)
 *   label        accessible name
 *   emptyText    message when nothing can be drawn
 *   gapNote      text under the chart naming the shaded weeks (default: no survey)
 */
export function timeChart(host, config) {
  const tk = tokens();
  const { dates } = config;
  const i0 = config.i0 ?? 0;
  const i1 = config.i1 ?? dates.length - 1;
  const width = Math.max(260, Math.floor(host.clientWidth || 640));
  const height = config.height ?? 340;
  const yFormat = config.yFormat ?? ((v) => fmt.num(v, 2));
  const series = config.series.filter((sr) => Array.isArray(sr.values));
  const lead = series.filter((sr) => !sr.quiet);
  const refs = config.refs ?? [];

  let lo = Infinity;
  let hi = -Infinity;
  for (const sr of series) {
    for (let i = i0; i <= i1; i++) {
      for (const values of [sr.values, sr.lo, sr.hi]) {
        const v = values?.[i];
        if (v != null) {
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
      }
    }
  }

  host.classList.add("chart");
  host.style.minHeight = `${height}px`;
  host.classList.remove("has-note");
  if (!Number.isFinite(lo) || i1 <= i0) {
    host.replaceChildren(h("div", { class: "empty", style: { height: `${height}px` } }, config.emptyText ?? t("chart.empty")));
    host.removeAttribute("tabindex");
    host.removeAttribute("role");
    host.onkeydown = host.onfocus = host.onblur = null;
    return { setIndex() {}, marginRight: 0, insetLeft: 0 };
  }
  for (const ref of refs) {
    lo = Math.min(lo, ref.y);
    hi = Math.max(hi, ref.y);
  }
  if (config.zero) {
    lo = Math.min(lo, 0);
    hi = Math.max(hi, 0);
  }
  const { domain: yDomain, ticks: yTicks } = yScale(lo, hi, { zero: config.zero });

  // end labels decide the right margin
  const mode = config.endLabels ?? "value";
  const ends = [];
  if (mode) {
    for (const sr of lead) {
      const li = lastValid(sr.values, i1);
      if (li < i0) continue;
      // A series that ends before the others (its latest weeks suppressed) keeps
      // its label only with the date of that value, and none once the line
      // visibly stops months earlier: an undated label reads as this week's.
      if (i1 - li > STALE_LABEL_WEEKS) continue;
      const name = mode === "value" ? "" : (sr.short ?? sr.label);
      const value = mode === "name" ? "" : yFormat(sr.values[li]);
      const when = li < i1 ? `· ${fmt.dayMonth(dates[li])}` : "";
      const w = textWidth(name, 12, 600) + textWidth(value, 12) + (name && value ? 5 : 0) + (when ? textWidth(when, 12) + 4 : 0);
      ends.push({ sr, li, name, value, when, w });
    }
  }
  const labelWidth = ends.length ? Math.max(...ends.map((e) => e.w)) : 0;
  const marginRight = config.marginRight ?? (ends.length ? Math.min(150, Math.ceil(labelWidth) + 20) : 10);

  const events = (config.events ?? []).filter((e) => e.when >= dates[i0] && e.when <= new Date(dates[i1].getTime() + 6 * DAY_MS));
  const marginTop = events.length || config.marker != null ? 34 : 18;
  const yLabelWidth = Math.max(...yTicks.map((v) => textWidth(yFormat(v), 12)));
  const insetLeft = config.insetLeft ?? Math.max(config.minInsetLeft ?? 0, Math.ceil(yLabelWidth) + 10);
  const axis = timeAxis(dates[i0], dates[i1], width - marginRight - insetLeft);
  const marginBottom = axis.twoLines ? 40 : 28;

  // ---- marks
  const marks = [];
  const gaps = gapRuns(lead.map((sr) => sr.values), i0, i1, 1).map(([a, b]) => ({
    a: new Date(Math.max(dates[i0].getTime(), dates[a].getTime() - 3.5 * DAY_MS)),
    b: new Date(Math.min(dates[i1].getTime(), dates[b].getTime() + 3.5 * DAY_MS)),
  }));
  if (gaps.length) marks.push(Plot.rect(gaps, { x1: "a", x2: "b", fill: tk.paper2 }));
  marks.push(
    Plot.gridY(yTicks, { stroke: tk.grid, strokeOpacity: 1 }),
    Plot.ruleY([yDomain[0]], { stroke: tk.axis }),
    Plot.axisY(yTicks, { anchor: "left", tickSize: 0, tickPadding: 0, dy: -6, textAnchor: "start", lineAnchor: "bottom", tickFormat: yFormat, fill: tk.ink2, label: null }),
    Plot.axisX(axis.ticks, {
      anchor: "bottom",
      tickSize: 4,
      tickPadding: 5,
      tickFormat: axis.format,
      fill: tk.ink2,
      stroke: tk.axis,
      strokeOpacity: 1,
      label: null,
      lineHeight: 1.15,
    }),
  );
  const strong = refs.filter((r) => r.strong);
  const light = refs.filter((r) => !r.strong);
  if (light.length) marks.push(Plot.ruleY(light, { y: "y", stroke: tk.ink2, strokeWidth: 1, strokeDasharray: "2 3" }));
  if (strong.length) marks.push(Plot.ruleY(strong, { y: "y", stroke: tk.ink, strokeWidth: 1.25 }));

  const indices = d3.range(i0, i1 + 1);
  const num = (v) => (v == null ? NaN : v);
  for (const sr of series) {
    sr.rows = indices.map((i) => ({ date: dates[i], v: num(sr.values[i]), lo: num(sr.lo?.[i]), hi: num(sr.hi?.[i]) }));
    if (sr.lo && sr.hi) marks.push(Plot.areaY(sr.rows, { x: "date", y1: "lo", y2: "hi", fill: sr.color, fillOpacity: config.bandOpacity ?? 0.14 }));
  }
  // quiet context lines first, so that the lines the chart is about sit on top
  for (const sr of [...series.filter((s) => s.quiet), ...lead]) {
    marks.push(
      Plot.lineY(sr.rows, {
        x: "date",
        y: "v",
        stroke: sr.quiet ? tk.quiet : sr.color,
        strokeWidth: sr.width ?? (sr.quiet ? 1 : 2),
        strokeOpacity: sr.quiet ? 0.9 : 1,
        strokeDasharray: sr.dash ?? null,
        strokeLinejoin: "round",
        strokeLinecap: "round",
      }),
    );
    // a value with no neighbour on either side would be invisible as a line
    const lonely = sr.rows.filter((row, k) => !Number.isNaN(row.v) && Number.isNaN(sr.rows[k - 1]?.v ?? NaN) && Number.isNaN(sr.rows[k + 1]?.v ?? NaN));
    if (lonely.length && !sr.quiet) marks.push(Plot.dot(lonely, { x: "date", y: "v", r: 2, fill: sr.color }));
  }

  const plot = Plot.plot({
    width,
    height,
    marginTop,
    marginRight,
    marginBottom,
    marginLeft: 0,
    x: { type: "utc", domain: [dates[i0], dates[i1]], insetLeft, axis: null },
    y: { domain: yDomain, axis: null },
    style: { fontFamily: "inherit", fontSize: "12px", background: "transparent", overflow: "visible" },
    marks,
  });
  // The host carries the accessible name and the keyboard reading; the marks
  // inside the svg are decoration for assistive technology.
  plot.setAttribute("aria-hidden", "true");
  plot.classList.add("plot");
  host.replaceChildren(plot);
  if (gaps.length) {
    host.append(h("p", { class: "chart-note" }, h("span", { class: "gap-key" }), config.gapNote ?? t("chart.gap.note")));
    host.classList.add("has-note");
  }

  const X = plot.scale("x");
  const Y = plot.scale("y");
  const svg = d3.select(plot);
  const plotBottom = height - marginBottom;
  const overlay = svg.append("g").attr("class", "ch-overlay");
  const haloText = (x, y, anchor, text, weight = 500) =>
    overlay
      .append("text")
      .attr("x", x)
      .attr("y", y)
      .attr("text-anchor", anchor)
      .attr("fill", tk.ink)
      .attr("font-size", 12)
      .attr("font-weight", weight)
      .attr("stroke", tk.paper)
      .attr("stroke-width", 4)
      .attr("stroke-linejoin", "round")
      .attr("paint-order", "stroke")
      .text(text);

  // reference labels: at whichever end, above or below the rule, crosses the fewest data points
  const points = lead.flatMap((sr) => sr.rows.filter((r) => !Number.isNaN(r.v)).map((r) => [X.apply(r.date), Y.apply(r.v)]));
  for (const ref of refs.filter((r) => r.label)) {
    const w = textWidth(ref.label, 12, 500) + 8;
    const y = Y.apply(ref.y);
    const left = X.apply(dates[i0]) + 2;
    const right = X.apply(dates[i1]) - 2;
    const candidates = [
      { x: left, anchor: "start", box: [left, left + w], dy: -6 },
      { x: left, anchor: "start", box: [left, left + w], dy: 15 },
      { x: right, anchor: "end", box: [right - w, right], dy: -6 },
      { x: right, anchor: "end", box: [right - w, right], dy: 15 },
    ].map((c) => {
      const top = y + c.dy - 13;
      const bottom = y + c.dy + 3;
      const hits = points.filter(([px, py]) => px >= c.box[0] - 3 && px <= c.box[1] + 3 && py >= top - 3 && py <= bottom + 3).length;
      return { ...c, hits };
    });
    const best = candidates.reduce((a, b) => (b.hits < a.hits ? b : a));
    haloText(best.x, y + best.dy, best.anchor, ref.label);
  }

  // annotations: a ringed point with a short label, for the one value a chart is about
  for (const note of config.annotations ?? []) {
    const sr = lead[note.series ?? 0];
    const v = sr?.values[note.i];
    if (v == null || note.i < i0 || note.i > i1) continue;
    const x = X.apply(dates[note.i]);
    const y = Y.apply(v);
    const right = x < width * 0.6;
    overlay.append("circle").attr("cx", x).attr("cy", y).attr("r", 4.5).attr("fill", tk.paper).attr("stroke", tk.ink).attr("stroke-width", 1.5);
    haloText(x + (right ? 9 : -9), y - 9, right ? "start" : "end", note.text);
  }

  // event rules (the numbered markers are HTML buttons, added below)
  for (const event of events) {
    const x = X.apply(event.when);
    overlay.append("line").attr("x1", x).attr("x2", x).attr("y1", marginTop - 6).attr("y2", plotBottom).attr("stroke", tk.axis).attr("stroke-width", 1);
  }

  // the week the rest of the view is about
  if (config.marker != null && config.marker >= i0 && config.marker <= i1) {
    const x = X.apply(dates[config.marker]);
    overlay.append("line").attr("x1", x).attr("x2", x).attr("y1", marginTop - 10).attr("y2", plotBottom).attr("stroke", tk.ink).attr("stroke-width", 1.25).attr("stroke-dasharray", "3 3");
    const anchor = x > width * 0.75 ? "end" : x < width * 0.25 ? "start" : "middle";
    haloText(x, marginTop - 16, anchor, fmt.date(dates[config.marker]), 600);
  }

  // direct end labels, nudged apart when lines end close together
  if (ends.length) {
    for (const end of ends) {
      end.x = X.apply(dates[end.li]);
      end.y = Y.apply(end.sr.values[end.li]);
      end.ly = end.y;
    }
    const ordered = [...ends].sort((a, b) => a.y - b.y);
    const minGap = 15;
    for (let k = 1; k < ordered.length; k++) ordered[k].ly = Math.max(ordered[k].ly, ordered[k - 1].ly + minGap);
    const overflow = ordered[ordered.length - 1].ly - (plotBottom - 4);
    if (overflow > 0) {
      ordered[ordered.length - 1].ly -= overflow;
      for (let k = ordered.length - 2; k >= 0; k--) ordered[k].ly = Math.min(ordered[k].ly, ordered[k + 1].ly - minGap);
    }
    const group = overlay.append("g").attr("class", "ch-ends");
    for (const end of ends) {
      if (Math.abs(end.ly - end.y) > 2) {
        group.append("line").attr("x1", end.x + 5).attr("y1", end.y).attr("x2", end.x + 10).attr("y2", end.ly).attr("stroke", tk.ink3).attr("stroke-width", 1);
      }
      group.append("circle").attr("cx", end.x).attr("cy", end.y).attr("r", 4).attr("fill", end.sr.color).attr("stroke", tk.paper).attr("stroke-width", 2);
      const text = group.append("text").attr("x", end.x + 11).attr("y", end.ly).attr("dy", "0.35em").attr("text-anchor", "start").attr("fill", tk.ink).attr("font-size", 12);
      if (end.name) text.append("tspan").attr("font-weight", 600).text(end.name);
      if (end.value) text.append("tspan").attr("dx", end.name ? 5 : 0).attr("fill", end.name || end.when ? tk.ink2 : tk.ink).attr("font-weight", end.name ? 400 : 600).text(end.value);
      if (end.when) text.append("tspan").attr("dx", 4).attr("fill", tk.ink3).text(end.when);
    }
  }

  // status-shaped points (run history)
  for (const point of config.points ?? []) {
    const v = lead[0]?.values[point.i];
    if (v == null) continue;
    const symbol = d3.symbol(SYMBOLS[point.status] ?? d3.symbolDiamond, point.status === "pass" ? 64 : 80);
    overlay
      .append("path")
      .attr("d", symbol())
      .attr("transform", `translate(${X.apply(dates[point.i])},${Y.apply(v)})`)
      .attr("fill", tk.status[point.status] ?? tk.ink2)
      .attr("stroke", tk.paper)
      .attr("stroke-width", 1.5);
  }

  // ---- crosshair
  const hover = overlay.append("g").attr("class", "ch-hover").attr("display", "none");
  const rule = hover.append("line").attr("y1", marginTop - 6).attr("y2", plotBottom).attr("stroke", tk.ink2).attr("stroke-width", 1);
  const dots = lead.map((sr) => hover.append("circle").attr("r", 4.5).attr("fill", sr.color).attr("stroke", tk.paper).attr("stroke-width", 2));
  let current = null;

  function draw(i) {
    const x = X.apply(dates[i]);
    hover.attr("display", null);
    rule.attr("x1", x).attr("x2", x);
    lead.forEach((sr, k) => {
      const v = sr.values[i];
      dots[k].attr("display", v == null ? "none" : null).attr("cx", x).attr("cy", v == null ? 0 : Y.apply(v));
    });
    return x;
  }

  function showIndex(i, pointerY) {
    const index = Math.max(i0, Math.min(i1, i));
    current = index;
    const x = draw(index);
    claimCrosshair(host, clearIndex);
    config.onIndex?.(index);
    if (config.tip) {
      const box = plot.getBoundingClientRect();
      const scale = box.width / width;
      const firstValue = lead.map((sr) => sr.values[index]).find((v) => v != null);
      const y = pointerY ?? box.top + (firstValue == null ? marginTop : Y.apply(firstValue)) * scale;
      showTip(config.tip(index), box.left + x * scale, y, host);
    }
  }

  function clearIndex() {
    hover.attr("display", "none");
    hideTip(host);
    releaseCrosshair(host);
    config.onIndex?.(null);
  }

  function indexAt(event) {
    const [px] = d3.pointer(event, plot);
    return d3.bisectCenter(dates, X.invert(px), i0, i1 + 1);
  }

  svg
    .on("pointermove", (event) => showIndex(indexAt(event), event.clientY))
    .on("pointerdown", (event) => showIndex(indexAt(event), event.clientY))
    .on("pointerleave", (event) => {
      if (event.pointerType !== "touch") clearIndex();
    });

  // keyboard: the same readout, one step per arrow key
  host.tabIndex = 0;
  host.setAttribute("role", "group");
  host.setAttribute("aria-label", `${config.label ?? ""} ${t("chart.keys")}`.trim());
  host.onkeydown = (event) => {
    const from = current ?? i1;
    const moves = { ArrowLeft: -1, ArrowRight: 1, PageUp: -13, PageDown: 13 };
    let target = null;
    if (event.key in moves) target = from + (current == null ? 0 : moves[event.key]);
    else if (event.key === "Home") target = i0;
    else if (event.key === "End") target = i1;
    if (target == null) return;
    event.preventDefault();
    showIndex(target);
  };
  host.onfocus = () => {
    if (host.matches(":focus-visible")) showIndex(current ?? i1);
  };
  host.onblur = (event) => {
    if (!host.contains(event.relatedTarget)) clearIndex();
  };

  if (events.length) addEventMarkers(host, events, X, { width, marginTop });

  return {
    marginRight,
    insetLeft,
    /** Move the crosshair without a tooltip (used to sync a companion chart). */
    setIndex(i) {
      if (i == null) hover.attr("display", "none");
      else draw(Math.max(i0, Math.min(i1, i)));
    },
  };
}

/** A word-sized line for a table row. Colours come from CSS classes. */
export function sparkline(values, { width = 132, height = 30, dashed = false } = {}) {
  const points = values.map((v, i) => [i, v]);
  const valid = points.filter((p) => p[1] != null);
  const svg = d3.create("svg").attr("class", `spark${dashed ? " is-dashed" : ""}`).attr("width", width).attr("height", height).attr("viewBox", `0 0 ${width} ${height}`).attr("aria-hidden", "true");
  if (valid.length < 2) return svg.node();
  const x = d3.scaleLinear().domain([0, values.length - 1]).range([2, width - 6]);
  const y = d3.scaleLinear().domain(d3.extent(valid, (p) => p[1])).range([height - 4, 4]);
  const line = d3.line().defined((p) => p[1] != null).x((p) => x(p[0])).y((p) => y(p[1]));
  svg.append("path").attr("class", "spark-line").attr("d", line(points));
  const last = valid[valid.length - 1];
  svg.append("circle").attr("class", "spark-dot").attr("cx", x(last[0])).attr("cy", y(last[1])).attr("r", 3);
  return svg.node();
}
