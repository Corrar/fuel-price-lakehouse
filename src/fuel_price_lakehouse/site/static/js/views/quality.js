// Data quality: the part of the project that says how far each number can be
// trusted. Overall status and freshness, the row funnel from raw to what this
// page publishes (with the invariant the contract guarantees, and the rest of
// the arithmetic shown as data), where the quarantined rows come from, then
// the rules, checks, files and runs (js/views/quality-tables.js). Everything
// comes from quality.json.

import { h } from "../dom.js";
import { t, fmt, localized } from "../i18n.js";
import { parseIsoDate, toDate, fileOf } from "../data.js";
import { sectionHead, statusBadge, missingFile } from "../ui.js";
import { rulesBlock, checksBlock, filesBlock, historyBlock, subhead } from "./quality-tables.js";

const STATUS_ORDER = { fail: 0, warn: 1, pass: 2 };
// The rule that quarantines empty lines; blank rows are not bad data, and the
// checks leave them out of their rates (blueprint 4.5). Rule ids are a contract.
const BLANK_RULE = "row_not_blank";

function statusBlock(quality) {
  const checks = Array.isArray(quality.checks) ? quality.checks : [];
  const count = (status) => checks.filter((c) => c.status === status).length;
  const run = quality.run ?? {};
  const fresh = quality.freshness ?? {};
  const started = toDate(run.started_at);
  const finished = toDate(run.finished_at);
  const seconds = started && finished ? Math.round((finished - started) / 1000) : null;
  const status = quality.status ?? "unknown";
  const fact = (label, value, note) => h("div", { class: "stat" }, h("dt", null, label), h("dd", null, value), note ? h("dd", { class: "stat-note" }, note) : null);

  return h(
    "div",
    { class: `qa-status is-${status}` },
    h(
      "div",
      { class: "qa-verdict" },
      statusBadge(status, t(`qa.status.${status in STATUS_ORDER ? status : "unknown"}`)),
      h("p", { class: "qa-verdict-text" }, t("qa.status.counts", { pass: count("pass"), warn: count("warn"), fail: count("fail"), total: checks.length })),
      h(
        "p",
        { class: "qa-run" },
        [
          run.id ? t("qa.run.id", { id: run.id }) : null,
          finished ? t("qa.run.finished", { when: fmt.dateTime(finished) }) : null,
          seconds != null ? t("qa.run.duration", { s: fmt.int(seconds) }) : null,
          t("qa.run.version", { v: run.code_version ?? t("qa.run.noversion") }),
        ]
          .filter(Boolean)
          .join(" · "),
      ),
    ),
    h(
      "dl",
      { class: "stat-row" },
      fresh.latest_collection_date ? fact(t("qa.fresh.collected"), fmt.date(parseIsoDate(fresh.latest_collection_date)), fresh.days_since_latest != null ? t("ov.fresh.days", { n: fresh.days_since_latest }) : null) : null,
      fresh.latest_week ? fact(t("qa.fresh.week"), fmt.week(parseIsoDate(fresh.latest_week))) : null,
      fresh.usd_latest ? fact(t("qa.fresh.usd"), fmt.date(parseIsoDate(fresh.usd_latest))) : null,
      fresh.ipca_latest_month ? fact(t("qa.fresh.ipca"), fmt.month(fresh.ipca_latest_month)) : null,
    ),
  );
}

/**
 * Where the quarantine comes from: when one file holds most of it, name the
 * file and the rules its rows broke (a row counts once in every rule it
 * breaks, so several rules can show the same rows).
 */
function quarantineOrigin(quality) {
  const files = Array.isArray(quality.files) ? quality.files : [];
  const rules = Array.isArray(quality.rules) ? quality.rules : [];
  const total = quality.funnel?.rows_quarantined ?? files.reduce((sum, f) => sum + (f.rows_quarantined ?? 0), 0);
  const top = files.reduce((best, f) => ((f.rows_quarantined ?? 0) > (best?.rows_quarantined ?? 0) ? f : best), null);
  if (!total || !top?.rows_quarantined || top.rows_quarantined / total < 0.5) return null;
  const year = String(top.period ?? "").slice(0, 4);
  let matched = rules.filter((r) => r.severity === "error" && r.by_year?.[year] === top.rows_quarantined);
  if (!matched.length) matched = rules.filter((r) => r.severity === "error" && (r.by_year?.[year] ?? 0) >= top.rows_quarantined * 0.5);
  const fileName = `${top.period} (${[top.kind, top.fuel_group].filter(Boolean).join(" · ")})`;
  const parts = [t("qa.origin.lead", { n: fmt.int(top.rows_quarantined), total: fmt.int(total), pct: fmt.pct(top.rows_quarantined / total, 1), file: fileName })];
  if (matched.length === 1) parts.push(t("qa.origin.one", { rule: matched[0].id, text: localized(matched[0], "description").replace(/\.$/, "") }));
  else if (matched.length > 1) parts.push(t("qa.origin.many", { rules: matched.map((r) => r.id).join(", ") }));
  if (matched.some((r) => r.id === BLANK_RULE)) parts.push(t("qa.origin.blank"));
  return { file: top, el: h("p", { class: "callout" }, h("strong", null, t("qa.origin.title")), " ", parts.join(" ")) };
}

function funnelBlock(quality, meta, origin) {
  const funnel = quality.funnel;
  if (!funnel) return null;
  const share = (part, whole) => (whole ? fmt.pct(part / whole, part / whole < 0.01 ? 2 : 1) : "—");
  const step = (label, value, note, branch, kind = "") =>
    h(
      "div",
      { class: `flow-col ${kind}`.trim() },
      h("div", { class: "flow-step" }, h("p", { class: "flow-label" }, label), h("p", { class: "flow-value" }, value), note ? h("p", { class: "flow-note" }, note) : null),
      branch
        ? h("div", { class: "flow-branch" }, h("p", { class: "flow-label" }, branch.label), h("p", { class: "flow-value" }, `− ${fmt.int(branch.value)}`), h("p", { class: "flow-note" }, branch.note))
        : null,
    );
  const balanced = funnel.rows_raw === funnel.rows_staged + funnel.rows_quarantined;
  const difference = funnel.rows_raw - funnel.rows_staged - funnel.rows_quarantined;
  const curatedFromStaged = funnel.rows_staged - funnel.duplicates_removed;
  const curatedMatches = curatedFromStaged === funnel.rows_curated;

  return h(
    "div",
    { class: "qa-block" },
    subhead(t("qa.funnel.title"), t("qa.funnel.text")),
    h(
      "div",
      { class: "flow" },
      step(t("qa.funnel.raw"), fmt.int(funnel.rows_raw), t("qa.funnel.raw.note", { files: fmt.int(funnel.files) })),
      step(t("qa.funnel.staged"), fmt.int(funnel.rows_staged), t("qa.funnel.staged.note", { pct: share(funnel.rows_staged, funnel.rows_raw) }), {
        label: t("qa.funnel.quarantine"),
        value: funnel.rows_quarantined,
        note: t("qa.funnel.quarantine.note", { pct: share(funnel.rows_quarantined, funnel.rows_raw) }),
      }),
      step(t("qa.funnel.curated"), fmt.int(funnel.rows_curated), t("qa.funnel.curated.note"), {
        label: t("qa.funnel.duplicates"),
        value: funnel.duplicates_removed,
        note: t("qa.funnel.duplicates.note", { n: fmt.int(funnel.price_conflicts) }),
      }),
      step(t("qa.funnel.site"), t("qa.funnel.site.value"), t("qa.funnel.site.note", { n: fmt.int(funnel.cells_suppressed), min: meta.min_stations_published ?? "—" }), null, "is-site"),
    ),
    h(
      "div",
      { class: "equations" },
      h(
        "p",
        { class: `invariant is-${balanced ? "pass" : "fail"}` },
        statusBadge(balanced ? "pass" : "fail", balanced ? t("qa.invariant.ok") : t("qa.invariant.bad")),
        h("span", { class: "mono" }, `${fmt.int(funnel.rows_raw)} = ${fmt.int(funnel.rows_staged)} + ${fmt.int(funnel.rows_quarantined)}${balanced ? "" : ` (${fmt.signed(difference, 0)})`}`),
        h("span", null, balanced ? t("qa.invariant.ok.text") : t("qa.invariant.bad.text")),
      ),
      funnel.duplicates_removed != null
        ? h(
            "p",
            { class: "invariant is-data" },
            h("span", { class: "eq-label" }, t("qa.arith.label")),
            h("span", { class: "mono" }, `${fmt.int(funnel.rows_staged)} − ${fmt.int(funnel.duplicates_removed)} ${curatedMatches ? "=" : "≠"} ${fmt.int(funnel.rows_curated)}`),
            h("span", null, t(curatedMatches ? "qa.arith.same" : "qa.arith.diff", { n: fmt.int(curatedFromStaged) })),
          )
        : null,
    ),
    origin?.el ?? null,
  );
}

export function mountQuality(root, { model }) {
  const quality = model.quality;
  const head = sectionHead({ number: "06", kicker: t("qa.kicker"), title: t("qa.title"), lede: t("qa.lede") });
  if (!quality) {
    root.replaceChildren(h("div", { class: "wrap" }, head.el, missingFile(fileOf("quality"))));
    return () => {};
  }
  const origin = quarantineOrigin(quality);
  const history = historyBlock(quality);
  root.replaceChildren(
    h(
      "div",
      { class: "wrap" },
      head.el,
      statusBlock(quality),
      funnelBlock(quality, model.meta, origin),
      rulesBlock(quality),
      checksBlock(quality),
      filesBlock(quality, origin?.file),
      history.el,
    ),
  );
  history.draw();
  return (changed) => {
    if (changed.has("theme") || changed.has("resize")) history.draw();
  };
}
