// Price history explorer: any fuel, for Brazil, a region or up to five places
// at once; nominal or real; the p10–p90 spread (for one place at a time); the
// dollar as an aligned companion chart (its own axis, never a second axis on
// the price chart); dated policy events as numbered markers, listed under the
// chart; and the period, chosen with presets, the brush or two date fields.

import { d3 } from "../lib.js";
import { h, rafThrottle } from "../dom.js";
import { t, fmt, productLabel, unitLabel, placeLabel, placeShort, eventText } from "../i18n.js";
import { fileOf } from "../data.js";
import { BRAZIL, placeSeries, toReal, nationalMean, firstValid, lastValid, pctChange, eventsFor } from "../series.js";
import { timeChart } from "../charts.js";
import { figure } from "../figure.js";
import { tipContent } from "../tooltip.js";
import { brushStrip } from "../brush.js";
import { tokens } from "../theme.js";
import { state, setState, periodRange, selectedPlaces, addPlace, removePlace, bandActive, MAX_PLACES, PERIOD_PRESETS } from "../state.js";
import { fuelSelect, segmented, toggle, selectControl, dateRange } from "../controls.js";
import { sectionHead, credit, externalLink } from "../ui.js";

const DEPENDS_ON = ["fuel", "places", "period", "real", "band", "usd", "events", "theme", "resize"];
const MIN_WEEKS = 4;
const TIMELINE_FIRST = 5;

/**
 * Tall enough to read, short enough that the controls, the chart and its axis
 * fit one screen after jumping to the section: at most 45% of the viewport,
 * and no more than the room left under the controls.
 */
function chartHeight(section, host) {
  const narrow = window.innerWidth < 640;
  const offset = host.getBoundingClientRect().top - section.getBoundingClientRect().top;
  const scrollMargin = parseFloat(getComputedStyle(section).scrollMarginTop) || 0;
  const room = window.innerHeight - scrollMargin - offset - 30;
  return Math.round(Math.max(narrow ? 280 : 236, Math.min(narrow ? 340 : 420, window.innerHeight * 0.45, room)));
}

export function mountExplorer(root, { model }) {
  const head = sectionHead({ number: "02", kicker: t("ex.kicker"), title: t("ex.title"), lede: t("ex.lede") });

  // ---- controls
  const fuel = fuelSelect(model, state.fuel, (value) => setState({ fuel: value }));
  const prices = segmented({
    label: t("ctl.prices"),
    options: [
      { value: "nominal", label: t("ctl.prices.nominal"), title: t("ctl.prices.nominal.title") },
      { value: "real", label: t("ctl.prices.real"), title: t("ctl.prices.real.title", { month: fmt.month(model.meta.ipca_reference_month) }) },
    ],
    value: state.real ? "real" : "nominal",
    onChange: (value) => setState({ real: value === "real" }),
  });
  const period = segmented({
    label: t("ctl.period"),
    options: PERIOD_PRESETS.map((preset) => ({ value: preset, label: t(`ctl.period.${preset}`) })),
    value: typeof state.period === "string" ? state.period : null,
    onChange: (value) => setState({ period: value }),
  });
  const band = toggle({ label: t("ctl.band"), checked: state.band, onChange: (value) => setState({ band: value }), title: t("ctl.band.title") });
  const usd = toggle({ label: t("ctl.usd"), checked: state.usd, onChange: (value) => setState({ usd: value }), title: t("ctl.usd.title") });
  const events = toggle({ label: t("ctl.events"), checked: state.events, onChange: (value) => setState({ events: value }) });

  const chips = h("ul", { class: "chips", role: "list" });
  const add = selectControl({
    label: t("ctl.places.add"),
    placeholder: t("ctl.places.placeholder"),
    options: [],
    value: "",
    className: "field-add",
    onChange: (value) => {
      if (value) addPlace(value);
      add.set("");
    },
  });
  const range = dateRange({
    model,
    onChange: (a, b) => {
      const to = Math.max(b, Math.min(model.latest, a + MIN_WEEKS));
      const from = Math.min(a, to - MIN_WEEKS);
      const whole = from <= 0 && to >= model.latest;
      setState({ period: whole ? "max" : { from: model.weeks[Math.max(0, from)], to: model.weeks[to] } });
    },
  });

  // ---- figure
  const fig = figure({ className: "fig-main" });
  const mainHost = h("div");
  const usdBlock = h("div", { class: "companion", hidden: true });
  const usdCaption = h("p", { class: "fig-unit" });
  const usdHost = h("div");
  usdBlock.append(usdCaption, usdHost);
  const brushHost = h("div", { class: "brush-host" });
  const notes = h("div", { class: "fig-notes" });
  const periodRow = h("div", { class: "period-row" }, h("span", { class: "field-label" }, t("ex.period.label")), range.el, h("span", { class: "period-hint" }, t("ex.brush")));
  fig.body.append(mainHost, usdBlock, periodRow, brushHost, notes);
  const timeline = h("section", { class: "timeline", "aria-labelledby": "timeline-title" });
  fig.el.append(timeline);

  root.replaceChildren(
    h(
      "div",
      { class: "wrap" },
      head.el,
      h(
        "div",
        { class: "controls controls-explorer" },
        fuel.el,
        prices.el,
        period.el,
        h(
          "div",
          { class: "controls-row" },
          h("div", { class: "places" }, h("span", { class: "field-label" }, t("ctl.places", { max: MAX_PLACES })), chips, add.el),
          h("div", { class: "toggles-inline", role: "group", "aria-label": t("ctl.show") }, h("span", { class: "field-label" }, t("ctl.show")), band.el, usd.el, model.has.events ? events.el : null),
        ),
      ),
      fig.el,
    ),
  );

  function syncControls() {
    fuel.set(state.fuel);
    prices.set(state.real ? "real" : "nominal");
    period.set(typeof state.period === "string" ? state.period : null);
    band.set(state.band);
    usd.set(state.usd);
    events.set(state.events);

    const tk = tokens();
    const selected = selectedPlaces();
    band.setDisabled(selected.length > 1, t("ctl.band.many"));
    chips.replaceChildren(
      ...selected.map(({ key, slot }) => {
        const name = placeLabel(model, key);
        const missing = !placeSeries(model, key, state.fuel);
        return h(
          "li",
          { class: `chip${missing ? " is-missing" : ""}` },
          h("span", { class: "legend-key", style: { "--key": tk.series[slot % tk.series.length] } }),
          h("span", null, name, missing ? ` (${t("lbl.nodata")})` : ""),
          selected.length > 1
            ? h("button", { type: "button", class: "chip-x", "aria-label": t("ctl.places.remove", { name }), title: t("ctl.places.remove", { name }), onclick: () => removePlace(key) }, "×")
            : null,
        );
      }),
    );

    const taken = new Set(selected.map((p) => p.key));
    const option = (key, group) => {
      const missing = !placeSeries(model, key, state.fuel);
      return { value: key, group, label: missing ? `${placeLabel(model, key)} — ${t("lbl.nodata")}` : placeLabel(model, key), disabled: missing || taken.has(key) };
    };
    const full = selected.length >= MAX_PLACES;
    add.setOptions(
      [
        option(BRAZIL, t("ctl.places.country")),
        ...model.regions.map((r) => option(`R-${r.code}`, t("ctl.places.regions"))),
        ...[...model.states].sort((a, b) => a.name.localeCompare(b.name, "pt")).map((st) => option(st.code, t("ctl.places.states"))),
      ],
      "",
    );
    add.select.disabled = full;
    add.select.options[0].textContent = full ? t("ctl.places.full", { max: MAX_PLACES }) : t("ctl.places.placeholder");
  }

  function draw(previewRange) {
    const tk = tokens();
    const product = model.productById.get(state.fuel);
    const [i0, i1] = previewRange ?? periodRange();
    const withBand = bandActive();
    const inReal = (values) => (state.real ? toReal(model, values) : values);
    const places = selectedPlaces().map((p) => ({ ...p, data: placeSeries(model, p.key, state.fuel) }));
    const series = places
      .filter((p) => p.data)
      .map((p) => ({
        key: p.key,
        label: placeLabel(model, p.key),
        short: placeShort(model, p.key),
        color: tk.series[p.slot % tk.series.length],
        values: p.key === BRAZIL ? nationalMean(model, state.fuel, state.real) : inReal(p.data.mean),
        lo: withBand ? inReal(p.data.p10) : null,
        hi: withBand ? inReal(p.data.p90) : null,
        raw: p.data,
      }));
    const month = fmt.month(model.meta.ipca_reference_month);

    // ---- words around the chart
    fig.setTitle(t("ex.fig.title", { fuel: productLabel(product) }));
    const lead = series[0];
    const a = lead ? firstValid(lead.values, i0, i1) : -1;
    const b = lead ? lastValid(lead.values, i1) : -1;
    if (lead && a >= 0 && b > a) {
      fig.setTake(
        t("ex.fig.take", {
          place: lead.label,
          price: fmt.price(lead.values[b], product.unit),
          week: fmt.week(model.dates[b]),
          pct: fmt.pct(pctChange(lead.values[b], lead.values[a]), 1, true),
          from: fmt.date(model.dates[a]),
        }),
      );
    } else {
      fig.setTake(t("ex.fig.none"));
    }
    fig.setUnit(
      [unitLabel(product.unit), t("ex.unit.mean"), state.real ? t("ex.unit.real", { month }) : t("ex.unit.nominal"), withBand && series.length ? t("ex.unit.band") : null]
        .filter(Boolean)
        .join(" · "),
    );
    fig.setSource(state.real || state.usd ? credit("anp", "bcb") : credit("anp"));

    const visibleEvents =
      state.events && model.has.events
        ? eventsFor(model, state.fuel)
            .filter((e) => e.when >= model.dates[i0] && e.when.getTime() <= model.dates[i1].getTime() + 6 * 86_400_000)
            .map((e, k) => ({ n: k + 1, when: e.when, dateText: fmt.date(e.when), fuels: e.fuels, ...eventText(e), url: e.source_url }))
        : [];

    let companion = null;
    const main = timeChart(mainHost, {
      dates: model.dates,
      i0,
      i1,
      series,
      height: chartHeight(root, mainHost),
      endLabels: series.length > 1 ? "both" : "value",
      yFormat: (v) => fmt.num(v, 2),
      events: visibleEvents,
      minInsetLeft: 38,
      emptyText: t("ex.fig.none"),
      label: `${t("ex.fig.title", { fuel: productLabel(product) })}.`,
      onIndex: (i) => companion?.setIndex(i),
      tip: (i) => {
        const rows = series.map((sr) => {
          const v = sr.values[i];
          const detail = [];
          if (v != null && series.length <= 2) {
            if (sr.lo && sr.lo[i] != null && sr.hi[i] != null) detail.push(`p10–p90: ${fmt.num(sr.lo[i], 2)}–${fmt.num(sr.hi[i], 2)}`);
            if (sr.raw.n_stations?.[i] != null) detail.push(t("lbl.nStations", { n: fmt.int(sr.raw.n_stations[i]) }));
          }
          return { color: sr.color, label: sr.label, value: v == null ? t("lbl.nodata") : fmt.price(v, product.unit, true), sub: detail.join(" · ") || null };
        });
        if (state.usd) rows.push({ color: tk.neutral, dash: true, label: t("lbl.usd"), value: fmt.brl(model.usd[i], 3) });
        const empty = series.every((sr) => sr.values[i] == null);
        const note = empty ? t("tip.gap") : state.real && model.provisional[i] ? t("tip.provisional") : null;
        return tipContent(t("tip.week", { week: fmt.week(model.dates[i]) }), rows, note);
      },
    });

    usdBlock.hidden = !state.usd;
    if (state.usd) {
      usdCaption.textContent = t("ex.usd.caption");
      companion = timeChart(usdHost, {
        dates: model.dates,
        i0,
        i1,
        series: [{ label: t("lbl.usd"), color: tk.neutral, dash: "5 4", values: model.usd }],
        height: 150,
        endLabels: "value",
        marginRight: main.marginRight || undefined,
        insetLeft: main.insetLeft || undefined,
        yFormat: (v) => fmt.num(v, 2),
        label: t("ex.usd.caption"),
        onIndex: (i) => main.setIndex(i),
        tip: (i) => tipContent(t("tip.week", { week: fmt.week(model.dates[i]) }), [{ color: tk.neutral, dash: true, label: t("lbl.usd"), value: fmt.brl(model.usd[i], 3) }]),
      });
    }

    // notes that explain what is, or is not, on the chart
    const missing = places.filter((p) => !p.data).map((p) => placeLabel(model, p.key));
    const provisional = state.real ? d3.range(i0, i1 + 1).filter((i) => model.provisional[i]).length : 0;
    const needs = [!model.has.states ? fileOf("states") : null, !model.has.regions ? fileOf("regions") : null].filter(Boolean);
    notes.replaceChildren(
      ...[
        missing.length ? t("ex.note.missing", { fuel: productLabel(product), places: missing.join(", ") }) : null,
        state.band && series.length > 1 ? t("ctl.band.many") : null,
        state.real ? t("ex.note.real", { month }) : null,
        provisional ? t("ex.note.provisional", { n: provisional }) : null,
        needs.length ? t("ex.note.files", { files: needs.join(", ") }) : null,
      ]
        .filter(Boolean)
        .map((text) => h("p", null, text)),
    );

    fig.setTable(() => ({
      columns: [{ label: t("tbl.week") }, ...series.map((sr) => ({ label: sr.label, num: true })), ...(state.usd ? [{ label: t("lbl.usd"), num: true }] : [])],
      rows: d3.range(i1, i0 - 1, -1).map((i) => [fmt.dateIso(model.dates[i]), ...series.map((sr) => fmt.num(sr.values[i], 3)), ...(state.usd ? [fmt.num(model.usd[i], 3)] : [])]),
    }));

    range.set(i0, i1);
    if (previewRange) return;
    drawTimeline(visibleEvents);
    brushStrip(brushHost, {
      dates: model.dates,
      values: model.national[state.fuel]?.mean ?? [],
      i0,
      i1,
      last: model.latest,
      onPreview: rafThrottle((preview) => draw(preview)),
      onChange: (chosen) => {
        const whole = !chosen || (chosen[0] === 0 && chosen[1] === model.latest);
        setState({ period: whole ? "max" : { from: model.weeks[chosen[0]], to: model.weeks[chosen[1]] } });
      },
    });
  }

  /**
   * Every event of the period, readable without hovering: number, date, title,
   * fuels, source. On a phone the list starts with the first few.
   */
  let timelineOpen = false;
  function drawTimeline(visibleEvents) {
    timeline.hidden = visibleEvents.length === 0;
    if (timeline.hidden) return;
    const fold = window.matchMedia("(max-width: 599px)").matches && visibleEvents.length > TIMELINE_FIRST + 1;
    const more = fold
      ? h(
          "button",
          {
            type: "button",
            class: "textbtn more-rows",
            "aria-expanded": String(timelineOpen),
            onclick: () => {
              timelineOpen = !timelineOpen;
              drawTimeline(visibleEvents);
              timeline.querySelector(".more-rows")?.focus();
            },
          },
          timelineOpen ? t("ex.timeline.fewer", { n: TIMELINE_FIRST }) : t("ex.timeline.all.n", { n: visibleEvents.length }),
        )
      : null;
    const shown = fold && !timelineOpen ? visibleEvents.slice(0, TIMELINE_FIRST) : visibleEvents;
    timeline.replaceChildren(
      h("h4", { class: "timeline-title", id: "timeline-title" }, t("ex.timeline", { n: visibleEvents.length })),
      h(
        "ol",
        { class: "timeline-list", role: "list" },
        shown.map((e) =>
          h(
            "li",
            null,
            h("span", { class: "evt-n" }, String(e.n)),
            h(
              "div",
              { class: "timeline-body" },
              h("p", { class: "timeline-head" }, h("time", { datetime: e.when.toISOString().slice(0, 10) }, e.dateText), h("strong", null, e.title)),
              h(
                "p",
                { class: "timeline-meta" },
                e.fuels.length
                  ? h("span", { class: "tags" }, e.fuels.map((id) => h("span", { class: "tag" }, model.productById.has(id) ? productLabel(model.productById.get(id)) : id)))
                  : h("span", { class: "tags" }, h("span", { class: "tag" }, t("ex.timeline.all"))),
                e.url ? externalLink(e.url, t("evt.source")) : null,
              ),
            ),
          ),
        ),
      ),
    );
    if (more) timeline.append(more);
  }

  syncControls();
  draw();
  return (changed) => {
    if (!DEPENDS_ON.some((key) => changed.has(key))) return;
    syncControls();
    draw();
  };
}
