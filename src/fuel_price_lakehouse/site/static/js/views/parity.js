// Ethanol or gasoline? The price of a litre of ethanol as a share of a litre
// of gasoline, against the 70% rule of thumb: four headline figures for the
// chosen week, the ranking of the states, and how the ratio moved over time
// for Brazil and a highlighted state. Product names come from meta.json.

import { d3 } from "../lib.js";
import { h } from "../dom.js";
import { t, fmt, productLabel, productInline } from "../i18n.js";
import { fileOf } from "../data.js";
import { ETHANOL, GASOLINE, PARITY, ratioSeries } from "../series.js";
import { timeChart } from "../charts.js";
import { figure, legend } from "../figure.js";
import { tipContent, markTip, hideTip } from "../tooltip.js";
import { tokens } from "../theme.js";
import { state, setState, weekIndex, setWeekIndex } from "../state.js";
import { weekScrubber, selectControl, rovingFocus } from "../controls.js";
import { sectionHead, credit, missingFile } from "../ui.js";

export function mountParity(root, { model }) {
  const head = sectionHead({ number: "04", kicker: t("pa.kicker"), title: t("pa.title") });
  const ethanol = model.productById.get(ETHANOL);
  const gasoline = model.productById.get(GASOLINE);
  if (!ethanol || !gasoline) {
    root.replaceChildren(h("div", { class: "wrap" }, head.el, h("p", { class: "empty" }, t("pa.unavailable"))));
    return () => {};
  }
  const names = { e: productInline(ethanol), g: productInline(gasoline) };
  const national = ratioSeries(model.national[ETHANOL].mean, model.national[GASOLINE].mean);
  const byState = new Map(model.states.map((st) => [st.code, ratioSeries(model.byState[ETHANOL]?.[st.code]?.mean, model.byState[GASOLINE]?.[st.code]?.mean)]));
  const hasData = (i) => national[i] != null;

  const week = weekScrubber({ model, getIndex: weekIndex, setIndex: setWeekIndex, hasData });
  const pick = selectControl({
    label: t("pa.pick"),
    placeholder: t("pa.pick.none"),
    options: [...model.states].sort((a, b) => a.name.localeCompare(b.name, "pt")).map((st) => ({ value: st.code, label: st.name, disabled: !byState.get(st.code) })),
    value: state.uf ?? "",
    onChange: (value) => setState({ uf: value || null }),
  });

  const kpis = h("dl", { class: "stat-row stat-row-4" });
  const explain = h(
    "div",
    { class: "explain" },
    h("h3", null, t("pa.rule.title")),
    h("p", null, t("pa.rule.body", names)),
    h("p", { class: "explain-note" }, t("pa.rule.note")),
  );
  const rankFig = figure({ source: credit("anp") });
  const trendFig = figure({ source: credit("anp") });
  const zones = h("div", { class: "zones", "aria-hidden": "true" });
  const rank = h("ol", { class: "rank rank-parity", "aria-label": t("pa.rank.title", names) });
  const rankNote = h("p", { class: "fig-notes" });
  rankFig.body.append(zones, rank, rankNote);
  const keys = h("div");
  const trendHost = h("div");
  trendFig.body.append(keys, trendHost);

  root.replaceChildren(
    h(
      "div",
      { class: "wrap" },
      head.el,
      h("div", { class: "controls" }, week.el, model.has.states ? pick.el : null),
      kpis,
      h("div", { class: "grid-parity" }, model.has.states ? rankFig.el : h("div", null, missingFile(fileOf("states"))), h("div", { class: "parity-side" }, explain, trendFig.el)),
    ),
  );

  function rowsAt(i) {
    return model.states.map((st) => ({
      st,
      ratio: byState.get(st.code)?.[i] ?? null,
      e: model.byState[ETHANOL]?.[st.code]?.mean?.[i] ?? null,
      g: model.byState[GASOLINE]?.[st.code]?.mean?.[i] ?? null,
    }));
  }

  function drawKpis(i, withData) {
    const pays = withData.filter((r) => r.ratio < PARITY).length;
    const stat = (label, value, note) => h("div", { class: "stat" }, h("dt", null, label), h("dd", null, value), note ? h("dd", { class: "stat-note" }, note) : null);
    const low = withData[0];
    const high = withData[withData.length - 1];
    const items = [
      stat(t("pa.kpi.national"), fmt.pct(national[i], 1), national[i] == null ? null : t(`pa.tag.${national[i] < PARITY ? "ethanol" : "gasoline"}.long`)),
      withData.length ? stat(t("pa.kpi.count"), t("pa.kpi.count.value", { n: pays, total: withData.length }), t("pa.kpi.count.note")) : null,
      low ? stat(t("pa.kpi.low"), fmt.pct(low.ratio, 1), low.st.name) : null,
      high ? stat(t("pa.kpi.high"), fmt.pct(high.ratio, 1), high.st.name) : null,
    ];
    kpis.replaceChildren(...items.filter(Boolean));
  }

  function drawRank() {
    const active = document.activeElement;
    const keep = root.contains(active) && active.dataset.uf ? active.dataset.uf : null;
    hideTip();
    const i = weekIndex();
    const weekText = fmt.week(model.dates[i]);
    const rows = rowsAt(i);
    const withData = rows.filter((r) => r.ratio != null).sort((a, b) => a.ratio - b.ratio);
    const without = rows.filter((r) => r.ratio == null);
    const pays = withData.filter((r) => r.ratio < PARITY).length;

    if (withData.length) head.setLede(t("pa.lede", { week: weekText, n: pays, total: withData.length, ratio: fmt.pct(national[i], 1) }));
    else if (national[i] != null) head.setLede(t("pa.lede.national", { week: weekText, ratio: fmt.pct(national[i], 1) }));
    else head.setLede(t("pa.lede.none", { week: weekText }));
    drawKpis(i, withData);
    if (!model.has.states) return;

    rankFig.setTitle(t("pa.rank.title", names));
    rankFig.setTake(t("pa.rank.take", { week: weekText }));
    rankFig.setUnit(t("pa.rank.unit", names));

    // bars grow from the 70% line: left means ethanol pays, right means gasoline does
    const reach = Math.max(0.05, ...withData.map((r) => Math.abs(r.ratio - PARITY))) * 1.08;
    const zone = (key, text) => [h("span", { class: "zone-long" }, text(t(`pa.zone.${key}`))), h("span", { class: "zone-short" }, text(t(`pa.tag.${key}`)))];
    zones.replaceChildren(
      h("span", { class: "zone-ethanol" }, zone("ethanol", (x) => `← ${x}`)),
      h("span", { class: "zone-line" }, fmt.pct(PARITY, 0)),
      h("span", { class: "zone-gasoline" }, zone("gasoline", (x) => `${x} →`)),
    );
    rank.replaceChildren(
      ...withData.map((row) => {
        const side = row.ratio < PARITY ? "ethanol" : "gasoline";
        const share = (Math.abs(row.ratio - PARITY) / reach) * 50;
        const button = h(
          "button",
          {
            type: "button",
            class: `rank-row${state.uf === row.st.code ? " is-selected" : ""}`,
            dataset: { uf: row.st.code },
            "aria-pressed": String(state.uf === row.st.code),
            "aria-label": `${row.st.name}: ${fmt.pct(row.ratio, 1)}, ${t(`pa.verdict.${side}`)}`,
            onclick: () => setState({ uf: state.uf === row.st.code ? null : row.st.code }),
          },
          h("span", { class: "rank-name" }, row.st.name),
          h("span", { class: "rank-track", "aria-hidden": "true" }, h("span", { class: `rank-bar is-${side}`, style: { width: `${share}%` } })),
          h("span", { class: "rank-value" }, fmt.pct(row.ratio, 1)),
          h("span", { class: `rank-tag is-${side}` }, t(`pa.tag.${side}`)),
        );
        markTip(button, () =>
          tipContent(
            row.st.name,
            [
              { label: t("pa.tip.ratio"), value: fmt.pct(row.ratio, 1) },
              { label: productLabel(ethanol), value: fmt.price(row.e, ethanol.unit, true) },
              { label: productLabel(gasoline), value: fmt.price(row.g, gasoline.unit, true) },
              { label: t("pa.tip.breakeven"), value: fmt.price(row.g * PARITY, ethanol.unit, true) },
            ],
            t(`pa.verdict.${side}`),
          ),
        );
        return h("li", null, button);
      }),
    );
    if (!withData.length) rank.append(h("li", { class: "empty" }, t("st.none.week")));
    rankNote.textContent = without.length ? t("pa.rank.missing", { list: without.map((r) => r.st.code).join(", ") }) : "";
    rankNote.hidden = !without.length;
    rovingFocus(rank, ".rank-row");
    if (keep) {
      const again = rank.querySelector(`[data-uf="${keep}"]`);
      if (again) {
        for (const row of rank.querySelectorAll(".rank-row")) row.tabIndex = row === again ? 0 : -1;
        again.focus({ preventScroll: true });
      }
    }

    rankFig.setTable(() => ({
      columns: [{ label: t("tbl.state") }, { label: t("pa.tip.ratio"), num: true }, { label: productLabel(ethanol), num: true }, { label: productLabel(gasoline), num: true }, { label: t("pa.col.verdict") }],
      rows: [...withData, ...without].map((row) => [
        row.st.name,
        fmt.pct(row.ratio, 1),
        fmt.num(row.e, 3),
        fmt.num(row.g, 3),
        row.ratio == null ? "—" : t(`pa.tag.${row.ratio < PARITY ? "ethanol" : "gasoline"}`),
      ]),
    }));
  }

  function drawTrend() {
    const tk = tokens();
    const picked = state.uf && byState.get(state.uf) ? state.uf : null;
    const series = [{ label: t("place.br"), short: t("place.br"), color: tk.series[0], values: national }];
    if (picked) series.push({ label: model.stateByCode.get(picked).name, short: picked, color: tk.series[1], values: byState.get(picked) });
    const first = d3.min(series, (sr) => sr.values.findIndex((v) => v != null));
    const below = national.filter((v) => v != null && v < PARITY).length;
    const total = national.filter((v) => v != null).length;

    trendFig.setTitle(t("pa.trend.title"));
    trendFig.setTake(total ? t("pa.trend.take", { n: below, total, pct: fmt.pct(below / total, 0) }) : "");
    trendFig.setUnit(t(model.has.states ? "pa.trend.unit" : "pa.trend.unit.br", names));
    keys.replaceChildren(series.length > 1 ? legend(series) : "");
    timeChart(trendHost, {
      dates: model.dates,
      i0: Math.max(0, first ?? 0),
      i1: model.latest,
      series,
      height: 300,
      endLabels: series.length > 1 ? "both" : "value",
      marker: weekIndex() < model.latest ? weekIndex() : null,
      yFormat: (v) => fmt.pct(v, 0),
      refs: [{ y: PARITY, label: t("pa.trend.ref"), strong: true }],
      label: t("pa.trend.title"),
      tip: (i) =>
        tipContent(
          t("tip.week", { week: fmt.week(model.dates[i]) }),
          series.map((sr) => ({ color: sr.color, label: sr.label, value: sr.values[i] == null ? t("lbl.nodata") : fmt.pct(sr.values[i], 1) })),
          national[i] == null ? t("tip.gap") : t(`pa.verdict.${national[i] < PARITY ? "ethanol" : "gasoline"}`),
        ),
    });
    trendFig.setTable(() => ({
      columns: [{ label: t("tbl.week") }, ...series.map((sr) => ({ label: sr.label, num: true }))],
      rows: d3.range(model.latest, -1, -1).map((i) => [fmt.dateIso(model.dates[i]), ...series.map((sr) => fmt.pct(sr.values[i], 1))]),
    }));
  }

  drawRank();
  drawTrend();
  return (changed) => {
    if (changed.has("week") || changed.has("uf")) {
      week.sync();
      pick.set(state.uf ?? "");
      drawRank();
    }
    if (changed.has("uf") || changed.has("week") || changed.has("theme") || changed.has("resize")) drawTrend();
  };
}
