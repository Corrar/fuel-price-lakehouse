// Small presentational pieces used by several views: icons, the signed change
// with its arrow, section headings, status badges, source credits, links, and
// the notices for text that comes in English and for a data file that did not
// load.

import { h, s, safeUrl } from "./dom.js";
import { t, fmt } from "./i18n.js";

const ICON_PATHS = {
  up: "M8 3l5 9H3z",
  down: "M8 13l5-9H3z",
  check: "M3.5 8.5l3 3 6-7",
  warn: "M8 4v5M8 11.5v.5",
  fail: "M4.5 4.5l7 7M11.5 4.5l-7 7",
  arrow: "M3 8h9M8.5 4.5L12 8l-3.5 3.5",
  external: "M6 3H3v10h10v-3M9 3h4v4M13 3L7.5 8.5",
  sun: "M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4",
  moon: "M13 9.5A5.5 5.5 0 0 1 6.5 3 5.5 5.5 0 1 0 13 9.5z",
  code: "M5.5 4.5L2 8l3.5 3.5M10.5 4.5L14 8l-3.5 3.5",
  download: "M8 2.5v7.5M4.8 7L8 10.2 11.2 7M3 13.5h10",
};
const FILLED = new Set(["up", "down"]);

export function icon(name, size = 16) {
  const svg = s("svg", { class: `icon icon-${name}`, width: size, height: size, viewBox: "0 0 16 16", "aria-hidden": "true", focusable: "false" });
  if (name === "sun") svg.append(s("circle", { cx: 8, cy: 8, r: 2.8, fill: "none", stroke: "currentColor", "stroke-width": 1.5 }));
  svg.append(
    s("path", {
      d: ICON_PATHS[name] ?? "",
      fill: FILLED.has(name) ? "currentColor" : "none",
      stroke: FILLED.has(name) ? "none" : "currentColor",
      "stroke-width": 1.6,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    }),
  );
  return svg;
}

/**
 * A signed percentage change. Direction is carried by the arrow and the sign;
 * colour only repeats it. A missing base gives a dash with an explanation.
 */
export function delta(value, { digits = 1 } = {}) {
  if (value == null || Number.isNaN(value)) {
    return h("span", { class: "delta is-none", title: t("delta.none") }, "—");
  }
  const flat = Math.abs(value) < 0.0005;
  const direction = flat ? "flat" : value > 0 ? "up" : "down";
  return h(
    "span",
    { class: `delta is-${direction}` },
    flat ? null : icon(direction, 10),
    h("span", null, fmt.pct(value, digits, true)),
    h("span", { class: "sr-only" }, ` (${t(`delta.${direction}`)})`),
  );
}

/** Numbered section heading: kicker, title, standfirst. */
export function sectionHead({ number, kicker, title, lede }) {
  const ledeEl = h("p", { class: "lede" }, lede ?? "");
  ledeEl.hidden = !lede;
  const el = h(
    "header",
    { class: "sec-head" },
    h("p", { class: "kicker" }, h("span", { class: "kicker-n" }, number), kicker),
    h("h2", { class: "sec-title" }, title),
    ledeEl,
  );
  return {
    el,
    setLede(content) {
      ledeEl.replaceChildren(...(Array.isArray(content) ? content : [content]));
      ledeEl.hidden = !content;
    },
  };
}

const STATUS_ICON = { pass: "check", ok: "check", warn: "warn", fail: "fail", failed: "fail" };

/** pass / warn / fail with icon and word, never colour alone. */
export function statusBadge(status, label) {
  const kind = status === "ok" ? "pass" : status === "failed" ? "fail" : status;
  const known = kind === "pass" || kind === "warn" || kind === "fail";
  return h(
    "span",
    { class: `status is-${known ? kind : "unknown"}` },
    known ? h("span", { class: "status-dot" }, icon(STATUS_ICON[kind], 12)) : null,
    label ?? (known ? t(`status.${kind}`) : String(status ?? "—")),
  );
}

/** "Fonte: ANP — ...; Banco Central do Brasil — SGS" for the given sources. */
export function credit(...sources) {
  return `${t("src.label")}: ${sources.map((key) => t(`src.${key}`)).join(" · ")}`;
}

/** Empty state of a section whose data file did not load. */
export function missingFile(files) {
  return h("p", { class: "empty" }, t("ui.missing.section", { file: [].concat(files).join(", ") }));
}

/**
 * Text that the pipeline writes in English only (check results, table
 * descriptions): marked with lang="en" and, on the Portuguese page, labelled
 * as English instead of being machine-translated.
 */
export function englishText(text, tag = "span") {
  return h(tag, { lang: "en", class: "en-text" }, text);
}

/** An in-page link that also works as a plain shareable href. */
export function viewLink(href, label, onGo, className = "more") {
  return h(
    "a",
    {
      class: className,
      href,
      onclick: (event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey) return;
        event.preventDefault();
        onGo();
      },
    },
    label,
    icon("arrow", 14),
  );
}

/** A link to another site; a value that is not an http(s) URL is shown as plain text. */
export function externalLink(href, label) {
  const url = safeUrl(href);
  if (!url) return h("span", null, label);
  return h("a", { href: url, target: "_blank", rel: "noopener noreferrer", class: "ext" }, label, icon("external", 12));
}
