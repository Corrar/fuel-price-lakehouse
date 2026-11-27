// A sortable table. Sorting is done by buttons in the column headers, with
// aria-sort on the active column; filtering is left to the caller, which
// passes the rows to show. An optional row limit shows the first rows of the
// sorted order (the caller offers "show all").

import { h } from "./dom.js";
import { t } from "./i18n.js";

/**
 * columns: [{key, label, num?, title?, sortValue?(row), cell(row) -> Node|string, className?}]
 * initial: {key, dir: "asc" | "desc"}
 * stack:   on phones, show each row as a block instead of scrolling sideways
 * limit:   show at most this many rows (null: all)
 * rowClass (row) -> class name for a row that needs a mark, or null
 */
export function sortableTable({ columns, rows, initial, caption, emptyText, maxHeight, stack = false, limit = null, rowClass }) {
  let sort = initial ?? null;
  let current = rows;
  let shown = limit;
  const tbody = h("tbody");
  const headCells = new Map();

  const headRow = h(
    "tr",
    null,
    columns.map((column) => {
      const cell = h("th", { scope: "col", class: [column.num ? "num" : "", column.className ?? ""].join(" ").trim() || null, title: column.title || null });
      if (column.sortValue) {
        cell.append(
          h(
            "button",
            {
              type: "button",
              class: "th-sort",
              onclick: () => {
                const same = sort?.key === column.key;
                sort = { key: column.key, dir: same ? (sort.dir === "asc" ? "desc" : "asc") : column.num ? "desc" : "asc" };
                render();
              },
            },
            column.label,
            h("span", { class: "sort-mark", "aria-hidden": "true" }),
          ),
        );
      } else {
        cell.append(column.label);
      }
      headCells.set(column.key, cell);
      return cell;
    }),
  );

  function render() {
    for (const [key, cell] of headCells) {
      if (sort?.key === key) cell.setAttribute("aria-sort", sort.dir === "asc" ? "ascending" : "descending");
      else cell.removeAttribute("aria-sort");
    }
    let ordered = current;
    const column = columns.find((c) => c.key === sort?.key);
    if (column?.sortValue) {
      const sign = sort.dir === "asc" ? 1 : -1;
      ordered = [...current].sort((a, b) => {
        const x = column.sortValue(a);
        const y = column.sortValue(b);
        if (x == null && y == null) return 0;
        if (x == null) return 1;
        if (y == null) return -1;
        return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * sign;
      });
    }
    if (!ordered.length) {
      tbody.replaceChildren(h("tr", null, h("td", { colspan: columns.length, class: "empty-cell" }, emptyText ?? t("tbl.empty"))));
      return;
    }
    if (shown != null) ordered = ordered.slice(0, shown);
    tbody.replaceChildren(
      ...ordered.map((row) =>
        h(
          "tr",
          { class: rowClass?.(row) ?? null },
          columns.map((c, k) => {
            const content = c.cell(row);
            const attrs = { class: [c.num ? "num" : "", c.className ?? ""].join(" ").trim() || null, "data-label": c.label };
            return k === 0 ? h("th", { scope: "row", ...attrs }, content) : h("td", attrs, content);
          }),
        ),
      ),
    );
  }

  render();
  const el = h(
    "div",
    { class: "table-scroll", tabindex: 0, role: "region", "aria-label": caption, style: maxHeight ? { maxHeight: `${maxHeight}px` } : null },
    h("table", { class: `data sortable${stack ? " stack" : ""}` }, h("caption", { class: "sr-only" }, caption), h("thead", null, headRow), tbody),
  );
  return {
    el,
    setRows(next) {
      current = next;
      render();
    },
    setLimit(next) {
      shown = next;
      render();
    },
  };
}
