// The frame every chart sits in: a title, a takeaway written from the data, a
// unit line, the chart, the source credit and a "show as table" switch that
// builds the accessible twin of the chart on demand. Also the colour legend.

import { h } from "./dom.js";
import { t } from "./i18n.js";

/** A plain table: the accessible twin of a chart. Rows are arrays of text. */
function simpleTable(columns, rows, caption) {
  const head = h("tr", null, columns.map((c) => h("th", { scope: "col", class: c.num ? "num" : null }, c.label)));
  const body = rows.map((row) =>
    h("tr", null, row.map((cell, k) => (k === 0 ? h("th", { scope: "row" }, cell) : h("td", { class: columns[k]?.num ? "num" : null }, cell)))),
  );
  return h(
    "div",
    { class: "table-scroll", tabindex: 0, role: "region", "aria-label": caption },
    h("table", { class: "data" }, h("caption", { class: "sr-only" }, caption), h("thead", null, head), h("tbody", null, body)),
  );
}

export function figure({ title = "", take = "", unit = "", source = "", className = "" } = {}) {
  const titleEl = h("h3", { class: "fig-title" }, title);
  const takeEl = h("p", { class: "fig-take" }, take);
  const unitEl = h("p", { class: "fig-unit" }, unit);
  const body = h("div", { class: "fig-body" });
  const tableHost = h("div", { class: "fig-table", hidden: true });
  const sourceEl = h("span", { class: "fig-source" }, source);
  let tableBuilder = null;

  const button = h(
    "button",
    {
      type: "button",
      class: "textbtn",
      hidden: true,
      "aria-expanded": "false",
      onclick: () => {
        const opening = tableHost.hidden;
        tableHost.hidden = !opening;
        button.setAttribute("aria-expanded", String(opening));
        button.textContent = opening ? t("fig.table.hide") : t("fig.table.show");
        if (opening) renderTable();
      },
    },
    t("fig.table.show"),
  );

  function renderTable() {
    if (tableHost.hidden || !tableBuilder) return;
    const { columns, rows, caption } = tableBuilder();
    tableHost.replaceChildren(simpleTable(columns, rows, caption ?? titleEl.textContent));
  }

  const el = h(
    "figure",
    { class: `fig ${className}`.trim() },
    h("figcaption", { class: "fig-head" }, titleEl, takeEl),
    unitEl,
    body,
    h("div", { class: "fig-foot" }, sourceEl, button),
    tableHost,
  );
  unitEl.hidden = !unit;
  takeEl.hidden = !take;

  return {
    el,
    body,
    setTitle(text) {
      titleEl.textContent = text;
    },
    title: () => titleEl.textContent,
    setTake(text) {
      takeEl.textContent = text;
      takeEl.hidden = !text;
    },
    setUnit(text) {
      unitEl.textContent = text;
      unitEl.hidden = !text;
    },
    setSource(text) {
      sourceEl.textContent = text;
    },
    /** Register the table twin; it is built only while the table is open. */
    setTable(builder) {
      tableBuilder = builder;
      button.hidden = !builder;
      if (!builder) {
        tableHost.hidden = true;
        button.setAttribute("aria-expanded", "false");
        button.textContent = t("fig.table.show");
      }
      renderTable();
    },
  };
}

/** Colour key + label, for legends above a chart. */
export function legend(items) {
  return h(
    "ul",
    { class: "legend", role: "list" },
    items.map((item) =>
      h("li", null, h("span", { class: `legend-key${item.dash ? " is-dashed" : ""}${item.quiet ? " is-quiet" : ""}`, style: { "--key": item.color } }), item.label),
    ),
  );
}
