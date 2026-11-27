// Brands: branded stations against white-flag (unbranded) ones, the gap
// between the two national averages week by week for the chosen fuel, with
// the latest averages and station counts as headline figures.

import { d3 } from "../lib.js";
import { h } from "../dom.js";
import { t, fmt, productLabel, unitLabel } from "../i18n.js";
import { fileOf } from "../data.js";
import { lastValid, firstValid } from "../series.js";
import { timeChart } from "../charts.js";
import { figure } from "../figure.js";
import { tipContent } from "../tooltip.js";
import { tokens } from "../theme.js";
import { state, setState } from "../state.js";
import { fuelSelect } from "../controls.js";
import { sectionHead, credit, missingFile } from "../ui.js";

export function mountFlags(root, { model }) {
  const head = sectionHead({ number: "05", kicker: t("fl.kicker"), title: t("fl.title") });
  if (!model.has.flags) {
    root.replaceChildren(h("div", { class: "wrap" }, head.el, missingFile(fileOf("flags"))));
    return () => {};
  }
  const fuel = fuelSelect(model, state.fuel, (value) => setState({ fuel: value }));
  const tiles = h("dl", { class: "stat-row" });
  const fig = figure({ source: credit("anp") });
  const host = h("div");
  fig.body.append(host);
  root.replaceChildren(h("div", { class: "wrap" }, head.el, h("div", { class: "controls" }, fuel.el), tiles, fig.el, h("p", { class: "fig-notes" }, t("fl.note"))));

  function draw() {
    const tk = tokens();
    const product = model.productById.get(state.fuel);
    const groups = model.byFlag[state.fuel] ?? {};
    const branded = groups.branded?.mean ?? null;
    const white = groups.white_flag?.mean ?? null;
    const gap = branded && white ? branded.map((b, i) => (b == null || white[i] == null ? null : b - white[i])) : null;
    const b = gap ? lastValid(gap, model.latest) : -1;
    const a = gap ? firstValid(gap) : -1;
    const digits = product.unit === "BRL/13kg" ? 2 : 3;

    if (!gap || b < 0) {
      head.setLede(t("fl.lede.none", { fuel: productLabel(product) }));
      tiles.replaceChildren();
      fig.setTitle(t("fl.fig.title", { fuel: productLabel(product) }));
      fig.setTake("");
      fig.setUnit("");
      host.replaceChildren(h("div", { class: "empty" }, t("fl.none", { fuel: productLabel(product) })));
      fig.setTable(null);
      return;
    }

    const share = gap[b] / white[b];
    const key = Math.abs(gap[b]) < 0.0005 ? "same" : gap[b] > 0 ? "more" : "less";
    head.setLede(
      t(`fl.lede.${key}`, {
        fuel: productLabel(product),
        week: fmt.week(model.dates[b]),
        gap: fmt.price(Math.abs(gap[b]), product.unit),
        pct: fmt.pct(Math.abs(share), 1),
      }),
    );
    const stat = (label, value, note) => h("div", { class: "stat" }, h("dt", null, label), h("dd", null, value), note ? h("dd", { class: "stat-note" }, note) : null);
    tiles.replaceChildren(
      stat(t("fl.branded"), fmt.price(branded[b], product.unit), groups.branded.n_stations?.[b] != null ? t("lbl.nStations", { n: fmt.int(groups.branded.n_stations[b]) }) : null),
      stat(t("fl.white"), fmt.price(white[b], product.unit), groups.white_flag.n_stations?.[b] != null ? t("lbl.nStations", { n: fmt.int(groups.white_flag.n_stations[b]) }) : null),
      stat(t("fl.gap"), fmt.signed(gap[b], 2), fmt.pct(share, 1, true)),
    );

    const valid = gap.filter((v) => v != null);
    const positive = valid.filter((v) => v > 0).length;
    fig.setTitle(t("fl.fig.title", { fuel: productLabel(product) }));
    fig.setTake(t("fl.fig.take", { n: positive, total: valid.length, mean: fmt.price(d3.mean(valid), product.unit) }));
    fig.setUnit(`${unitLabel(product.unit)} · ${t("fl.fig.unit")}`);
    timeChart(host, {
      dates: model.dates,
      i0: a,
      i1: b,
      series: [{ label: t("fl.gap"), color: tk.series[0], values: gap }],
      height: window.innerWidth < 640 ? 280 : 340,
      zero: true,
      refs: [{ y: 0, strong: true }],
      endLabels: "value",
      yFormat: (v) => fmt.signed(v, 2),
      label: t("fl.fig.title", { fuel: productLabel(product) }),
      tip: (i) =>
        tipContent(
          t("tip.week", { week: fmt.week(model.dates[i]) }),
          [
            { color: tk.series[0], label: t("fl.gap"), value: gap[i] == null ? t("lbl.nodata") : fmt.signed(gap[i], digits) },
            { label: t("fl.branded"), value: fmt.price(branded[i], product.unit, true) },
            { label: t("fl.white"), value: fmt.price(white[i], product.unit, true) },
          ],
          gap[i] == null ? t("tip.gap") : null,
        ),
    });
    fig.setTable(() => ({
      columns: [{ label: t("tbl.week") }, { label: t("fl.branded"), num: true }, { label: t("fl.white"), num: true }, { label: t("fl.gap"), num: true }],
      rows: d3.range(b, a - 1, -1).map((i) => [fmt.dateIso(model.dates[i]), fmt.num(branded[i], 3), fmt.num(white[i], 3), gap[i] == null ? "—" : fmt.signed(gap[i], digits)]),
    }));
  }

  draw();
  return (changed) => {
    if (changed.has("fuel") || changed.has("theme") || changed.has("resize")) {
      fuel.set(state.fuel);
      draw();
    }
  };
}
