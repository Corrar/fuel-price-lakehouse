// The detailed blocks of the quality report: every validation rule with the
// rows it caught (sortable, filterable), the dataset checks, the per-file
// table and the run history. On phones the long lists start folded: only the
// rules that caught rows, only the checks that did not pass, the first files.

import { h } from "../dom.js";
import { t, fmt, localized, getLang } from "../i18n.js";
import { parseIsoDate, toDate } from "../data.js";
import { timeChart } from "../charts.js";
import { figure } from "../figure.js";
import { tipContent, markTip } from "../tooltip.js";
import { tokens } from "../theme.js";
import { segmented, toggle } from "../controls.js";
import { sortableTable } from "../table.js";
import { statusBadge, englishText } from "../ui.js";

const SOURCE_NAMES = { anp: "ANP", bcb_sgs: "Banco Central" };
const STATUS_ORDER = { fail: 0, warn: 1, pass: 2 };
const SEVERITY_ORDER = { error: 0, warning: 1 };
const FIRST_ROWS = 10;

const sourceName = (source) => SOURCE_NAMES[source] ?? String(source ?? "—");
const phone = () => window.matchMedia("(max-width: 599px)").matches;
const statusWord = (status) => t(`status.${status in STATUS_ORDER ? status : "fail"}`);

export function subhead(title, text) {
  return h("div", { class: "qa-subhead" }, h("h3", null, title), text ? h("p", null, text) : null);
}

/** `scope` is "sev" for row rules (what happens to the row) or "csev" for dataset checks. */
function severityTag(severity, scope = "sev") {
  const known = severity === "error" || severity === "warning";
  return h("span", { class: `sev is-${known ? severity : "other"}` }, known ? t(`qa.${scope}.${severity}`) : String(severity ?? "—"));
}

/** A share of rows; a non-zero share that would round to zero is shown as "< 0.001%". */
function sharePct(share) {
  if (!share) return "—";
  return share < 0.000005 ? `< ${fmt.pct(0.00001, 3)}` : fmt.pct(share, 3);
}

/** "Show all (55)" / "Show the first 10" under a long table. */
function moreButton(total, onToggle) {
  let open = false;
  const button = h("button", { type: "button", class: "textbtn more-rows", "aria-expanded": "false" });
  const label = () => (button.textContent = open ? t("qa.rows.fewer", { n: FIRST_ROWS }) : t("qa.rows.all", { n: total }));
  button.addEventListener("click", () => {
    open = !open;
    button.setAttribute("aria-expanded", String(open));
    label();
    onToggle(open);
  });
  label();
  return button;
}

// ---- rules ----------------------------------------------------------------

function yearBars(byYear) {
  const years = Object.keys(byYear ?? {}).sort();
  if (!years.some((y) => byYear[y])) return "—";
  const max = Math.max(1, ...years.map((y) => byYear[y]));
  const text = years.map((y) => `${y}: ${fmt.int(byYear[y])}`).join(" · ");
  const el = h(
    "span",
    { class: "yearbars", role: "img", "aria-label": text, tabindex: 0 },
    years.map((y) => h("span", { class: `yearbar${byYear[y] ? "" : " is-zero"}`, style: { "--h": `${byYear[y] ? Math.max(12, (byYear[y] / max) * 100) : 0}%` } })),
  );
  markTip(el, () => tipContent(t("qa.rules.byYear"), years.map((y) => ({ label: y, value: fmt.int(byYear[y]) }))));
  return h("span", { class: "yearbars-wrap" }, el, h("span", { class: "yearbars-ends", "aria-hidden": "true" }, h("span", null, years[0]), h("span", null, years[years.length - 1])));
}

export function rulesBlock(quality) {
  const rules = Array.isArray(quality.rules) ? quality.rules : [];
  let severity = "all";
  let onlyHits = phone();

  const table = sortableTable({
    caption: t("qa.rules.title"),
    emptyText: t("qa.rules.empty"),
    stack: true,
    initial: { key: "rows", dir: "desc" },
    rows: rules,
    columns: [
      {
        key: "id",
        label: t("qa.col.rule"),
        sortValue: (r) => r.id,
        cell: (r) => [h("code", { class: "rule-id" }, r.id), h("span", { class: "rule-text" }, localized(r, "description"))],
        className: "col-rule",
      },
      { key: "source", label: t("qa.col.source"), sortValue: (r) => sourceName(r.source), cell: (r) => sourceName(r.source) },
      { key: "severity", label: t("qa.col.severity"), sortValue: (r) => SEVERITY_ORDER[r.severity] ?? 9, cell: (r) => severityTag(r.severity) },
      { key: "rows", label: t("qa.col.rows"), num: true, sortValue: (r) => r.rows ?? 0, cell: (r) => fmt.int(r.rows) },
      { key: "share", label: t("qa.col.share"), num: true, sortValue: (r) => r.share ?? 0, cell: (r) => (r.rows ? sharePct(r.share) : "—") },
      { key: "years", label: t("qa.col.years"), cell: (r) => yearBars(r.by_year), className: "col-years" },
    ],
  });

  const summary = h("p", { class: "filter-count", role: "status" });
  function apply() {
    const shown = rules.filter((r) => (severity === "all" || r.severity === severity) && (!onlyHits || r.rows > 0));
    table.setRows(shown);
    summary.textContent = t("qa.rules.count", { n: shown.length, total: rules.length });
  }
  const filter = segmented({
    label: t("qa.rules.filter"),
    options: [
      { value: "all", label: t("qa.rules.filter.all") },
      { value: "error", label: t("qa.sev.error.short") },
      { value: "warning", label: t("qa.sev.warning.short") },
    ],
    value: severity,
    onChange: (value) => {
      severity = value;
      apply();
    },
  });
  const hits = toggle({
    label: t("qa.rules.hits"),
    checked: onlyHits,
    onChange: (value) => {
      onlyHits = value;
      apply();
    },
  });
  apply();

  return h(
    "div",
    { class: "qa-block" },
    subhead(t("qa.rules.title"), t("qa.rules.text")),
    h("div", { class: "controls controls-tight" }, filter.el, h("div", { class: "field toggles" }, h("span", { class: "field-label" }, t("ctl.show")), h("div", { class: "toggle-row" }, hits.el)), summary),
    table.el,
    h("p", { class: "fig-notes" }, t("qa.rules.note")),
  );
}

// ---- checks ---------------------------------------------------------------

function checkItem(check) {
  const english = getLang() !== "en";
  const fact = (label, value) => h("div", null, h("dt", null, label), h("dd", null, value ? englishText(value) : "—"));
  return h(
    "li",
    { class: `check is-${check.status}` },
    h(
      "div",
      { class: "check-head" },
      statusBadge(check.status),
      h("code", { class: "rule-id" }, check.id),
      severityTag(check.severity, "csev"),
      english ? h("abbr", { class: "lang-tag", title: t("qa.checks.en") }, "EN") : null,
    ),
    h("p", { class: "check-text" }, localized(check, "description")),
    h(
      "dl",
      { class: "check-facts" },
      fact(t("qa.checks.observed"), check.observed),
      fact(t("qa.checks.expected"), check.expected),
      check.detail ? h("div", { class: "check-detail" }, h("dt", null, t("qa.checks.detail")), h("dd", null, englishText(check.detail))) : null,
    ),
  );
}

export function checksBlock(quality) {
  const checks = [...(Array.isArray(quality.checks) ? quality.checks : [])].sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9));
  if (!checks.length) return null;
  const flagged = checks.filter((c) => c.status !== "pass");
  const passed = checks.filter((c) => c.status === "pass");
  let list;
  if (phone() && passed.length) {
    // on a phone the checks that passed fold away behind one line
    list = [
      flagged.length ? h("ul", { class: "checks", role: "list" }, flagged.map(checkItem)) : null,
      h(
        "details",
        { class: "checks-more" },
        h("summary", null, t("qa.checks.more", { n: passed.length })),
        h("ul", { class: "checks", role: "list" }, passed.map(checkItem)),
      ),
    ];
  } else {
    list = h("ul", { class: "checks", role: "list" }, checks.map(checkItem));
  }
  return h("div", { class: "qa-block" }, subhead(t("qa.checks.title"), t("qa.checks.text")), list, h("p", { class: "fig-notes" }, t("qa.checks.note")));
}

// ---- files ----------------------------------------------------------------

export function filesBlock(quality, flaggedFile) {
  const files = Array.isArray(quality.files) ? quality.files : [];
  if (!files.length) return null;
  const rate = (f) => (f.rows_read ? f.rows_quarantined / f.rows_read : 0);
  const table = sortableTable({
    caption: t("qa.files.title"),
    initial: { key: "quarantined", dir: "desc" },
    rows: files,
    limit: files.length > FIRST_ROWS ? FIRST_ROWS : null,
    rowClass: (f) => (f === flaggedFile ? "is-flagged" : null),
    columns: [
      { key: "source", label: t("qa.col.source"), sortValue: (f) => sourceName(f.source), cell: (f) => sourceName(f.source) },
      { key: "kind", label: t("qa.col.kind"), sortValue: (f) => `${f.kind} ${f.fuel_group ?? ""}`, cell: (f) => h("code", null, [f.kind, f.fuel_group].filter(Boolean).join(" · ")), className: "nowrap" },
      { key: "period", label: t("qa.col.period"), sortValue: (f) => String(f.period), cell: (f) => h("code", null, String(f.period ?? "—")) },
      { key: "read", label: t("qa.col.read"), num: true, sortValue: (f) => f.rows_read, cell: (f) => fmt.int(f.rows_read) },
      { key: "staged", label: t("qa.col.staged"), num: true, sortValue: (f) => f.rows_staged, cell: (f) => fmt.int(f.rows_staged) },
      {
        key: "quarantined",
        label: t("qa.col.quarantined"),
        num: true,
        sortValue: (f) => f.rows_quarantined,
        cell: (f) => (f.rows_quarantined ? [fmt.int(f.rows_quarantined), h("span", { class: "cell-sub" }, ` (${fmt.pct(rate(f), 2)})`)] : "0"),
      },
      { key: "collected", label: t("qa.col.collected"), sortValue: (f) => f.collected_at, cell: (f) => fmt.stamp(toDate(f.collected_at)), className: "nowrap" },
      { key: "sha", label: "SHA-256", cell: (f) => h("code", null, f.sha256 ?? "—") },
      { key: "revision", label: t("qa.col.revision"), num: true, sortValue: (f) => f.revision, cell: (f) => String(f.revision ?? "—") },
      { key: "status", label: t("qa.col.status"), sortValue: (f) => f.status, cell: (f) => statusBadge(f.status, f.status === "ok" ? "ok" : t("status.fail")) },
    ],
  });
  const totalRead = files.reduce((sum, f) => sum + (f.rows_read ?? 0), 0);
  const quarantined = files.filter((f) => f.rows_quarantined > 0).length;
  return h(
    "div",
    { class: "qa-block" },
    subhead(t("qa.files.title"), t("qa.files.text", { n: fmt.int(files.length), rows: fmt.int(totalRead), q: quarantined })),
    table.el,
    files.length > FIRST_ROWS ? moreButton(files.length, (open) => table.setLimit(open ? null : FIRST_ROWS)) : null,
  );
}

// ---- history --------------------------------------------------------------

export function historyBlock(quality) {
  const history = (Array.isArray(quality.history) ? quality.history : []).filter((r) => toDate(r.finished_at));
  const fig = figure({ source: t("qa.history.source") });
  fig.setTitle(t("qa.history.title"));
  const chartHost = h("div");
  const runs = [...history].reverse();
  const table = sortableTable({
    caption: t("qa.history.table"),
    rows: runs,
    limit: runs.length > FIRST_ROWS ? FIRST_ROWS : null,
    columns: [
      { key: "run", label: t("qa.history.run"), cell: (r) => h("code", null, r.run_id ?? "—") },
      { key: "finished", label: t("qa.history.finished"), cell: (r) => fmt.stamp(toDate(r.finished_at)), className: "nowrap" },
      { key: "status", label: t("qa.col.status"), cell: (r) => statusBadge(r.status, statusWord(r.status)) },
      { key: "raw", label: t("qa.history.read"), num: true, cell: (r) => fmt.int(r.rows_raw) },
      { key: "quarantined", label: t("qa.funnel.quarantine"), num: true, cell: (r) => fmt.int(r.rows_quarantined) },
      { key: "curated", label: t("qa.funnel.curated"), num: true, cell: (r) => fmt.int(r.rows_curated) },
      { key: "week", label: t("qa.fresh.week"), cell: (r) => (r.latest_week ? fmt.date(parseIsoDate(r.latest_week)) : "—"), className: "nowrap" },
      { key: "warned", label: t("qa.history.warned"), num: true, cell: (r) => fmt.int(r.checks_warned) },
      { key: "failed", label: t("qa.history.failed"), num: true, cell: (r) => fmt.int(r.checks_failed) },
    ],
  });
  fig.body.append(chartHost, h("p", { class: "fig-unit" }, t("qa.history.table")), table.el);
  if (runs.length > FIRST_ROWS) fig.body.append(moreButton(runs.length, (open) => table.setLimit(open ? null : FIRST_ROWS)));

  if (history.length < 2) {
    fig.setTake(t("qa.history.short"));
    chartHost.hidden = true;
    return { el: h("div", { class: "qa-block" }, fig.el), draw() {} };
  }
  const dates = history.map((r) => new Date(r.finished_at));
  const first = history[0];
  const last = history[history.length - 1];
  fig.setTake(t("qa.history.take", { n: history.length, from: fmt.date(dates[0]), to: fmt.date(dates[dates.length - 1]), growth: fmt.int(last.rows_curated - first.rows_curated) }));
  fig.setUnit(t("qa.history.unit"));
  const draw = () => {
    const tk = tokens();
    const label = t("qa.funnel.curated");
    timeChart(chartHost, {
      dates,
      series: [{ label, color: tk.series[0], values: history.map((r) => r.rows_curated ?? null) }],
      points: history.map((r, i) => ({ i, status: r.status })),
      height: 220,
      endLabels: "value",
      yFormat: (v) => fmt.compact(v, 2),
      label: `${t("qa.history.title")}. ${label}`,
      gapNote: t("qa.history.gap"),
      tip: (i) =>
        tipContent(fmt.dateTime(dates[i]), [
          { color: tk.series[0], label, value: fmt.int(history[i].rows_curated) },
          { label: t("qa.funnel.quarantine"), value: fmt.int(history[i].rows_quarantined) },
          { label: t("qa.col.status"), value: statusWord(history[i].status) },
          { label: t("qa.history.warned"), value: fmt.int(history[i].checks_warned) },
          { label: t("qa.history.failed"), value: fmt.int(history[i].checks_failed) },
        ]),
    });
  };
  const keys = h(
    "ul",
    { class: "legend status-legend", role: "list" },
    ["pass", "warn", "fail"].map((s) => h("li", null, h("span", { class: `shape is-${s}`, "aria-hidden": "true" }), statusWord(s))),
  );
  chartHost.before(keys);
  return { el: h("div", { class: "qa-block" }, fig.el), draw };
}
