// Findings: three annotated charts, one point each, written from the data:
// inflation, the dollar (both as an index on one axis) and the spread between
// states. Each links to the tool below with the matching choices already made.
// The dollar is grey and dashed here as everywhere on the page.

import { d3 } from "../lib.js";
import { h } from "../dom.js";
import { t, fmt, productLabel, productInline, unitLabel, unitShort } from "../i18n.js";
import { firstValid, lastValid, pctChange, nationalMean } from "../series.js";
import { timeChart } from "../charts.js";
import { figure, legend } from "../figure.js";
import { tipContent, markTip } from "../tooltip.js";
import { tokens } from "../theme.js";
import { linkTo } from "../state.js";
import { sectionHead, credit, viewLink } from "../ui.js";

const CHART_HEIGHT = 280;

function weekTitle(model, i) {
  return t("tip.week", { week: fmt.week(model.dates[i]) });
}

/** Nominal price against the same price corrected by IPCA. */
function inflationFigure(model, product, goTo) {
  const fig = figure({ source: credit("anp", "bcb"), className: "fig-small" });
  const nominal = model.national[product.id].mean;
  const real = nationalMean(model, product.id, true);
  const a = firstValid(nominal);
  const b = lastValid(nominal, model.latest);
  const month = fmt.month(model.meta.ipca_reference_month);
  let peak = a;
  for (let i = a; i <= b; i++) if (real[i] != null && real[i] > real[peak]) peak = i;

  fig.setTitle(
    t("fi.real.title", {
      fuel: productLabel(product),
      nominal: fmt.pct(pctChange(nominal[b], nominal[a]), 0, true),
      real: fmt.pct(pctChange(real[b], real[a]), 0, true),
      since: fmt.monthYear(model.dates[a]),
    }),
  );
  fig.setTake(t("fi.real.take", { month, peak: fmt.price(real[peak], product.unit), when: fmt.monthYear(model.dates[peak]) }));
  fig.setUnit(unitLabel(product.unit));
  const host = h("div");
  const keys = h("div");
  fig.body.append(keys, host);
  fig.el.append(
    h("p", { class: "fig-more" }, viewLink(linkTo("explorer", { fuel: product.id, real: true, period: "max" }), t("fi.real.more"), () => goTo("explorer", { fuel: product.id, real: true, period: "max" }))),
  );

  const draw = () => {
    const tk = tokens();
    const series = [
      { label: t("lbl.nominal"), color: tk.series[0], values: nominal },
      { label: t("lbl.real", { month }), color: tk.series[1], values: real },
    ];
    keys.replaceChildren(legend(series));
    timeChart(host, {
      dates: model.dates,
      i0: a,
      i1: b,
      series,
      height: CHART_HEIGHT,
      endLabels: false,
      yFormat: (v) => fmt.num(v, 2),
      annotations: [{ i: peak, series: 1, text: t("fi.real.peak", { price: fmt.price(real[peak], product.unit) }) }],
      label: fig.title(),
      tip: (i) =>
        tipContent(
          weekTitle(model, i),
          series.map((sr) => ({ color: sr.color, label: sr.label, value: fmt.price(sr.values[i], product.unit, true) })),
          model.provisional[i] ? t("tip.provisional") : null,
        ),
    });
  };
  fig.setTable(() => ({
    columns: [{ label: t("tbl.week") }, { label: t("lbl.nominal"), num: true }, { label: t("lbl.real", { month }), num: true }],
    rows: d3.range(b, a - 1, -1).map((i) => [fmt.dateIso(model.dates[i]), fmt.num(nominal[i], 3), fmt.num(real[i], 3)]),
  }));
  return { el: fig.el, draw };
}

/** The product and the dollar on one axis, both as an index. */
function dollarFigure(model, product, goTo) {
  const fig = figure({ source: credit("anp", "bcb"), className: "fig-small" });
  const price = model.national[product.id].mean;
  let a = -1;
  for (let i = 0; i <= model.latest; i++) {
    if (price[i] != null && model.usd[i] != null) {
      a = i;
      break;
    }
  }
  const b = Math.min(lastValid(price, model.latest), lastValid(model.usd, model.latest));
  if (a < 0 || b <= a) return null;
  const index = (values) => values.map((v) => (v == null ? null : (v / values[a]) * 100));
  const fuelIndex = index(price);
  const usdIndex = index(model.usd);

  fig.setTitle(
    t("fi.usd.title", {
      since: fmt.monthYear(model.dates[a]),
      usd: fmt.pct(usdIndex[b] / 100 - 1, 0, true),
      fuel: productInline(product),
      price: fmt.pct(fuelIndex[b] / 100 - 1, 0, true),
    }),
  );
  fig.setTake(t("fi.usd.take"));
  fig.setUnit(t("fi.usd.unit", { week: fmt.date(model.dates[a]) }));
  const host = h("div");
  const keys = h("div");
  fig.body.append(keys, host);
  fig.el.append(
    h("p", { class: "fig-more" }, viewLink(linkTo("explorer", { fuel: product.id, usd: true, period: "max" }), t("fi.usd.more"), () => goTo("explorer", { fuel: product.id, usd: true, period: "max" }))),
  );

  const draw = () => {
    const tk = tokens();
    const series = [
      { label: productLabel(product), color: tk.series[0], values: fuelIndex },
      { label: t("lbl.usd"), color: tk.neutral, dash: "5 4", values: usdIndex },
    ];
    keys.replaceChildren(legend(series));
    timeChart(host, {
      dates: model.dates,
      i0: a,
      i1: b,
      series,
      height: CHART_HEIGHT,
      endLabels: "value",
      yFormat: (v) => fmt.num(v, 0),
      refs: [{ y: 100, strong: true }],
      label: fig.title(),
      tip: (i) =>
        tipContent(weekTitle(model, i), [
          { color: series[0].color, label: series[0].label, value: fmt.num(fuelIndex[i], 1), sub: fmt.price(price[i], product.unit, true) },
          { color: series[1].color, dash: true, label: series[1].label, value: fmt.num(usdIndex[i], 1), sub: fmt.brl(model.usd[i], 3) },
        ]),
    });
  };
  fig.setTable(() => ({
    columns: [
      { label: t("tbl.week") },
      { label: `${productLabel(product)} (${t("lbl.index")})`, num: true },
      { label: `${t("lbl.usd")} (${t("lbl.index")})`, num: true },
    ],
    rows: d3.range(b, a - 1, -1).map((i) => [fmt.dateIso(model.dates[i]), fmt.num(fuelIndex[i], 1), fmt.num(usdIndex[i], 1)]),
  }));
  return { el: fig.el, draw };
}

/** One dot per state on a price axis: the spread inside the country. */
function spreadFigure(model, product, goTo) {
  if (!model.has.states) return null;
  const fig = figure({ source: credit("anp"), className: "fig-small" });
  const national = model.national[product.id].mean;
  const i = lastValid(national, model.latest);
  const points = model.states
    .map((st) => ({ st, v: model.byState[product.id]?.[st.code]?.mean?.[i] ?? null, n: model.byState[product.id]?.[st.code]?.n_stations?.[i] ?? null }))
    .filter((p) => p.v != null)
    .sort((p, q) => p.v - q.v);
  if (points.length < 2) return null;
  const low = points[0];
  const high = points[points.length - 1];

  fig.setTitle(t("fi.spread.title", { diff: fmt.price(high.v - low.v, product.unit), unit: unitShort(product.unit) }));
  fig.setTake(
    t("fi.spread.take", {
      week: fmt.week(model.dates[i]),
      low: fmt.price(low.v, product.unit),
      lowName: low.st.name,
      high: fmt.price(high.v, product.unit),
      highName: high.st.name,
    }),
  );
  fig.setUnit(`${unitLabel(product.unit)} · ${t("fi.spread.unit")}`);
  const host = h("div", { class: "strip" });
  fig.body.append(host);
  fig.el.append(h("p", { class: "fig-more" }, viewLink(linkTo("states", { fuel: product.id }), t("fi.spread.more"), () => goTo("states", { fuel: product.id }))));

  const draw = () => {
    const tk = tokens();
    const width = Math.max(260, Math.floor(host.clientWidth || 360));
    const height = CHART_HEIGHT;
    const margin = { top: 46, right: 18, bottom: 34, left: 18 };
    const pad = (high.v - low.v) * 0.06 || 0.1;
    const x = d3.scaleLinear().domain([low.v - pad, high.v + pad]).range([margin.left, width - margin.right]);
    const mid = margin.top + (height - margin.top - margin.bottom) / 2;
    const radius = 6;

    // simple beeswarm: push a dot up or down until it touches no earlier dot
    const placed = [];
    for (const point of points) {
      const px = x(point.v);
      let offset = 0;
      for (let step = 0; step < 40; step++) {
        const dy = step === 0 ? 0 : Math.ceil(step / 2) * (radius * 2 + 1) * (step % 2 ? -1 : 1);
        if (placed.every((q) => Math.hypot(q.px - px, q.py - (mid + dy)) >= radius * 2 + 1)) {
          offset = dy;
          break;
        }
      }
      placed.push({ ...point, px, py: mid + offset });
    }

    const svg = d3.create("svg").attr("width", width).attr("height", height).attr("viewBox", `0 0 ${width} ${height}`).attr("class", "plot").attr("aria-hidden", "true");
    const ticks = x.ticks(Math.max(2, Math.floor(width / 90)));
    const axisY = height - margin.bottom;
    svg.append("line").attr("x1", margin.left).attr("x2", width - margin.right).attr("y1", axisY).attr("y2", axisY).attr("stroke", tk.axis);
    const tick = svg.append("g").selectAll("g").data(ticks).join("g").attr("transform", (d) => `translate(${x(d)},${axisY})`);
    tick.append("line").attr("y2", 4).attr("stroke", tk.axis);
    tick.append("text").attr("y", 18).attr("text-anchor", "middle").attr("fill", tk.ink2).attr("font-size", 12).text((d) => fmt.num(d, 2));

    // the national average as a reference rule
    const nx = x(national[i]);
    if (nx >= margin.left && nx <= width - margin.right) {
      svg.append("line").attr("x1", nx).attr("x2", nx).attr("y1", margin.top - 14).attr("y2", axisY).attr("stroke", tk.ink).attr("stroke-width", 1.25);
      svg
        .append("text")
        .attr("x", nx)
        .attr("y", margin.top - 20)
        .attr("text-anchor", nx > width * 0.7 ? "end" : nx < width * 0.3 ? "start" : "middle")
        .attr("fill", tk.ink)
        .attr("font-size", 12)
        .attr("font-weight", 600)
        .text(`${t("place.br")} ${fmt.num(national[i], 2)}`);
    }

    const dots = svg.append("g");
    for (const point of placed) {
      // pointer only: 27 tab stops would be a burden, the table view lists every state
      const group = dots.append("g").attr("class", "strip-dot");
      group.append("circle").attr("cx", point.px).attr("cy", point.py).attr("r", 12).attr("fill", "transparent");
      group.append("circle").attr("cx", point.px).attr("cy", point.py).attr("r", radius).attr("fill", tk.series[0]).attr("stroke", tk.paper).attr("stroke-width", 2);
      markTip(group.node(), () =>
        tipContent(point.st.name, [
          { color: tk.series[0], label: t("lbl.mean"), value: fmt.price(point.v, product.unit, true) },
          { label: t("lbl.vsNational"), value: fmt.pct(point.v / national[i] - 1, 1, true) },
          point.n != null ? { label: t("lbl.stations"), value: fmt.int(point.n) } : null,
        ]),
        { keyboard: false },
      );
    }
    // name the two extremes
    const lowDot = placed[0];
    const highDot = placed[placed.length - 1];
    const below = Math.max(...placed.map((p) => p.py)) + radius + 18;
    const label = (dot, anchor) =>
      svg
        .append("text")
        .attr("x", dot.px)
        .attr("y", below)
        .attr("text-anchor", anchor)
        .attr("fill", tk.ink)
        .attr("font-size", 12)
        .call((text) => text.append("tspan").attr("font-weight", 600).text(dot.st.code))
        .call((text) => text.append("tspan").attr("dx", 5).attr("fill", tk.ink2).text(fmt.num(dot.v, 2)));
    label(lowDot, "start").attr("x", lowDot.px - radius);
    label(highDot, "end").attr("x", highDot.px + radius);
    host.replaceChildren(svg.node());
  };
  fig.setTable(() => ({
    columns: [{ label: t("tbl.state") }, { label: unitLabel(product.unit), num: true }, { label: t("lbl.vsNational"), num: true }],
    rows: [...points].reverse().map((p) => [p.st.name, fmt.num(p.v, 3), fmt.pct(p.v / national[i] - 1, 1, true)]),
  }));
  return { el: fig.el, draw };
}

export function mountFindings(root, { model, goTo }) {
  const product = model.productById.get(model.leadFuel);
  const head = sectionHead({ number: "01", kicker: t("fi.kicker"), title: t("fi.title"), lede: t("fi.lede", { fuel: productInline(product) }) });
  const figures = [inflationFigure, dollarFigure, spreadFigure].map((build) => build(model, product, goTo)).filter(Boolean);
  root.replaceChildren(h("div", { class: "wrap" }, head.el, h("div", { class: "grid-3", style: { "--cols": String(figures.length) } }, figures.map((f) => f.el))));
  const draw = () => figures.forEach((f) => f.draw());
  draw();
  return (changed) => {
    if (changed.has("theme") || changed.has("resize")) draw();
  };
}
