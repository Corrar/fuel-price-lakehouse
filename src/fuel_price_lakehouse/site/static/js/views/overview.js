// Overview: what a litre costs this week, and what this page is. A headline
// written from the latest numbers, a three-line statement of the project
// (automated pipeline, quarantine instead of deletion, a quality gate), how
// fresh the data is, the price board with every fuel and the dollar, the
// dearest and cheapest states, and the size of the dataset.

import { h } from "../dom.js";
import { t, fmt, productLabel, productInline, unitLabel, unitPhrase } from "../i18n.js";
import { parseIsoDate, toDate, fileOf } from "../data.js";
import { latestStats, latestChange, endOfWeek, stateRows } from "../series.js";
import { sparkline } from "../charts.js";
import { linkTo } from "../state.js";
import { delta, statusBadge, credit, viewLink, missingFile } from "../ui.js";

const DAY_MS = 86_400_000;

function headline(product, stats) {
  const price = h("mark", { class: "hl" }, fmt.price(stats.price, product.unit));
  let change = "";
  if (stats.week != null) {
    if (Math.abs(stats.week) < 0.0005) change = t(stats.consecutive ? "ov.h.flat" : "ov.h.flat.since");
    else change = t(`ov.h.${stats.week > 0 ? "up" : "down"}${stats.consecutive ? "" : ".since"}`, { pct: fmt.pct(Math.abs(stats.week), 1) });
  }
  return h("h1", { class: "hero-title" }, `${productLabel(product)} ${t("ov.h.at")} `, price, ` ${unitPhrase(product.unit)}`, change ? `, ${change}` : "");
}

function standfirst(model, stats) {
  const parts = [];
  if (stats.year != null) parts.push(t("ov.dek.year", { nominal: fmt.pct(stats.year, 1, true), real: fmt.pct(stats.yearReal, 1, true) }));
  if (stats.stations != null) {
    parts.push(t("ov.dek.base", { stations: fmt.int(stats.stations), states: stats.states ?? "—", week: fmt.week(model.dates[stats.index]) }));
  }
  return h("p", { class: "standfirst" }, parts.join(" "));
}

/** What the project is, in the first screen: the reason this page exists. */
function projectBox(model, goTo) {
  const quality = model.quality;
  const rules = Array.isArray(quality?.rules) ? quality.rules.length : 0;
  const checks = Array.isArray(quality?.checks) ? quality.checks : [];
  const passed = checks.filter((c) => c.status === "pass").length;
  const item = (key, text) => h("li", null, h("strong", null, t(`ov.pr.${key}.lead`)), " ", text);
  return h(
    "section",
    { class: "project", "aria-labelledby": "project-title" },
    h("h2", { class: "project-title", id: "project-title" }, t("ov.pr.title")),
    h(
      "ul",
      { class: "project-list", role: "list" },
      item("auto", t("ov.pr.auto")),
      item("quarantine", rules ? t("ov.pr.quarantine", { n: rules }) : t("ov.pr.quarantine.plain")),
      item("gate", checks.length ? t("ov.pr.gate", { n: checks.length, pass: passed }) : t("ov.pr.gate.plain")),
    ),
    h(
      "p",
      { class: "project-links" },
      viewLink(linkTo("about"), t("ov.pr.how"), () => goTo("about")),
      model.has.quality ? viewLink(linkTo("quality"), t("ov.pr.quality"), () => goTo("quality")) : null,
    ),
  );
}

function freshness(model, goTo) {
  const quality = model.quality;
  const fresh = quality?.freshness ?? {};
  const checks = Array.isArray(quality?.checks) ? quality.checks : [];
  const warned = checks.filter((c) => c.status === "warn").length;
  const failed = checks.filter((c) => c.status === "fail").length;
  const generated = toDate(model.meta.generated_at);
  const age = generated ? Math.floor((Date.now() - generated.getTime()) / DAY_MS) : null;
  const item = (label, value, note) => h("div", { class: "fresh-item" }, h("dt", null, label), h("dd", null, value, note ? h("span", { class: "fresh-note" }, note) : null));

  let summary;
  if (failed) summary = t("ov.q.fail", { n: failed, total: checks.length });
  else if (warned) summary = t("ov.q.warn", { n: warned, total: checks.length });
  else summary = t("ov.q.pass", { total: checks.length });

  return h(
    "section",
    { class: "fresh", "aria-labelledby": "fresh-title" },
    h("h2", { class: "fresh-title", id: "fresh-title" }, t("ov.fresh.title")),
    h(
      "dl",
      { class: "fresh-list" },
      generated ? item(t("ov.fresh.updated"), fmt.date(generated), age != null && age > 10 ? t("ov.fresh.stale", { n: age }) : null) : null,
      fresh.latest_collection_date
        ? item(t("ov.fresh.collected"), fmt.date(parseIsoDate(fresh.latest_collection_date)), fresh.days_since_latest != null ? t("ov.fresh.days", { n: fresh.days_since_latest }) : null)
        : item(t("qa.fresh.week"), fmt.week(model.dates[model.latest])),
      fresh.usd_latest ? item(t("ov.fresh.usd"), fmt.date(parseIsoDate(fresh.usd_latest))) : null,
      fresh.ipca_latest_month || model.meta.ipca_reference_month ? item(t("ov.fresh.ipca"), fmt.month(fresh.ipca_latest_month ?? model.meta.ipca_reference_month)) : null,
    ),
    checks.length
      ? h("p", { class: "fresh-quality" }, statusBadge(quality.status), h("span", null, summary), viewLink(linkTo("quality"), t("ov.q.link"), () => goTo("quality")))
      : h("p", { class: "fresh-quality" }, t("ui.missing.section", { file: fileOf("quality") })),
    model.meta.sample ? h("p", { class: "fresh-sample" }, t("ov.sample")) : null,
  );
}

function board(model, goTo) {
  const month = fmt.month(model.meta.ipca_reference_month);
  const head = h(
    "tr",
    null,
    h("th", { scope: "col" }, t("ov.col.fuel")),
    h("th", { scope: "col", class: "num" }, t("ov.col.price")),
    h("th", { scope: "col", class: "num" }, t("ov.col.week")),
    h("th", { scope: "col", class: "num" }, t("ov.col.year")),
    h("th", { scope: "col", class: "num", title: t("ov.col.real.title", { month }) }, t("ov.col.real")),
    h("th", { scope: "col", class: "col-spark" }, t("ov.col.trend")),
  );
  const rows = [];
  for (const product of model.products) {
    const stats = latestStats(model, product.id);
    if (!stats) continue;
    const values = model.national[product.id].mean;
    rows.push(
      h(
        "tr",
        null,
        h(
          "th",
          { scope: "row", class: "b-fuel" },
          viewLink(linkTo("explorer", { fuel: product.id }), productLabel(product), () => goTo("explorer", { fuel: product.id }), "b-name"),
          h("span", { class: "b-unit" }, unitLabel(product.unit), stats.isLatestWeek ? "" : ` · ${t("ov.row.stale", { week: fmt.week(model.dates[stats.index]) })}`),
        ),
        h("td", { class: "num b-price" }, fmt.price(stats.price, product.unit)),
        h("td", { class: "num", "data-label": t("ov.col.week") }, delta(stats.week)),
        h("td", { class: "num", "data-label": t("ov.col.year") }, delta(stats.year)),
        h("td", { class: "num", "data-label": t("ov.col.real") }, delta(stats.yearReal)),
        h("td", { class: "col-spark" }, sparkline(values.slice(Math.max(0, stats.index - 52), stats.index + 1))),
      ),
    );
  }

  // The dollar, for context: same columns, a dashed line, no real-terms change.
  const usd = latestChange(model, model.usd);
  if (usd) {
    rows.push(
      h(
        "tr",
        { class: "b-usd" },
        h(
          "th",
          { scope: "row", class: "b-fuel" },
          viewLink(linkTo("explorer", { usd: true }), t("ov.usd.name"), () => goTo("explorer", { usd: true }), "b-name"),
          h("span", { class: "b-unit" }, t("ov.usd.unit")),
        ),
        h("td", { class: "num b-price" }, fmt.brl(usd.value, 2)),
        h("td", { class: "num", "data-label": t("ov.col.week") }, delta(usd.week)),
        h("td", { class: "num", "data-label": t("ov.col.year") }, delta(usd.year)),
        h("td", { class: "num", "data-label": t("ov.col.real") }, h("span", { class: "delta is-none", title: t("ov.usd.noreal") }, "—")),
        h("td", { class: "col-spark" }, sparkline(model.usd.slice(Math.max(0, usd.index - 52), usd.index + 1), { dashed: true })),
      ),
    );
  }
  return h(
    "div",
    { class: "board" },
    h("h2", { class: "board-title" }, t("ov.board.title")),
    h("table", { class: "board-table" }, h("caption", { class: "sr-only" }, t("ov.board.caption")), h("thead", null, head), h("tbody", null, rows)),
    h("p", { class: "fig-source" }, `${t("ov.board.note", { month })} ${credit("anp", "bcb")}`),
  );
}

/** The three dearest and three cheapest states for the headline fuel. */
function extremes(model, product, stats, goTo) {
  const wrap = (...children) => h("section", { class: "extremes", "aria-labelledby": "extremes-title" }, h("h2", { class: "board-title", id: "extremes-title" }, t("ov.ex.title", { fuel: productInline(product) })), ...children);
  if (!model.has.states) return wrap(missingFile(fileOf("states")));
  const ranked = stateRows(model, product.id, stats.index)
    .filter((r) => r.v != null)
    .sort((a, b) => b.v - a.v);
  if (ranked.length < 2) return wrap(h("p", { class: "empty" }, t("st.none.week")));
  const take = Math.min(3, Math.floor(ranked.length / 2));
  const list = (title, items) =>
    h(
      "div",
      { class: "mini" },
      h("h3", { class: "mini-title" }, title),
      h(
        "ol",
        { class: "mini-list", role: "list" },
        items.map((row) =>
          h(
            "li",
            null,
            h("span", { class: "mini-code" }, row.st.code),
            h("span", { class: "mini-name" }, row.st.name),
            h("span", { class: "mini-value" }, fmt.price(row.v, product.unit)),
            h("span", { class: "mini-dev" }, fmt.pct(row.deviation, 1, true)),
          ),
        ),
      ),
    );
  return wrap(
    h("div", { class: "mini-cols" }, list(t("ov.ex.high"), ranked.slice(0, take)), list(t("ov.ex.low"), ranked.slice(-take).reverse())),
    h(
      "p",
      { class: "fig-source" },
      t("ov.ex.note", { week: fmt.week(model.dates[stats.index]) }),
      " ",
      credit("anp"),
    ),
    h("p", { class: "fig-more" }, viewLink(linkTo("states", { fuel: product.id }), t("ov.ex.more"), () => goTo("states", { fuel: product.id }))),
  );
}

function totals(model) {
  const totalsData = model.meta.totals ?? {};
  const entries = [
    ["observations", "ov.tot.observations"],
    ["stations", "ov.tot.stations"],
    ["municipalities", "ov.tot.municipalities"],
    ["weeks", "ov.tot.weeks"],
  ].filter(([key]) => totalsData[key] != null);
  if (!entries.length) return null;
  return h(
    "section",
    { class: "totals", "aria-labelledby": "totals-title" },
    h("h2", { class: "board-title", id: "totals-title" }, t("ov.tot.lead", { since: fmt.month(model.meta.since) })),
    h(
      "dl",
      { class: "totals-list" },
      entries.map(([key, label]) => h("div", null, h("dt", null, t(label)), h("dd", { title: fmt.int(totalsData[key]) }, fmt.compact(totalsData[key])))),
    ),
  );
}

export function mountOverview(root, { model, goTo }) {
  const product = model.productById.get(model.leadFuel);
  const stats = latestStats(model, product.id);
  if (!stats) {
    root.replaceChildren(h("div", { class: "wrap" }, h("p", { class: "empty" }, t("chart.empty"))));
    return () => {};
  }
  const start = model.dates[stats.index];
  root.replaceChildren(
    h(
      "div",
      { class: "wrap" },
      h(
        "div",
        { class: "hero" },
        h(
          "div",
          { class: "hero-main" },
          h("p", { class: "kicker" }, t("ov.kicker", { week: fmt.weekLong(start, endOfWeek(start)) })),
          headline(product, stats),
          standfirst(model, stats),
        ),
        freshness(model, goTo),
        projectBox(model, goTo),
      ),
      board(model, goTo),
      h("div", { class: "ov-foot" }, extremes(model, product, stats, goTo), totals(model)),
    ),
  );
  // Nothing here depends on the choices in the store.
  return () => {};
}
