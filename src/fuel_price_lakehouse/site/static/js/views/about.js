// How it works: what the project does and why, the pipeline architecture
// (js/views/architecture.js), the engineering guarantees, the public tables to
// download (meta.json "downloads"), where to read more and notes on the data.

import { h } from "../dom.js";
import { t, fmt, getLang } from "../i18n.js";
import { fileOf } from "../data.js";
import { sectionHead, externalLink, englishText, icon } from "../ui.js";
import { diagram } from "./architecture.js";

const STACK = "Python 3.12 · uv · httpx · tenacity · pandas · Pandera · pyarrow · Parquet · DuckDB · pytest · ruff · mypy · GitHub Actions · GitHub Pages · Observable Plot · D3";

/** A download link only for a file next to the page (downloads/…) or a plain http(s) URL. */
function downloadHref(path) {
  if (typeof path !== "string") return null;
  if (/^https?:\/\//i.test(path)) return path;
  return /^downloads\/[A-Za-z0-9._-]+$/.test(path) ? path : null;
}

function downloads(meta) {
  const tables = Array.isArray(meta.downloads) ? meta.downloads.filter((d) => d && d.table) : [];
  if (!tables.length) return null;
  const link = (path, label) => {
    const href = downloadHref(path);
    return href ? h("a", { class: "dl-link", href, download: "" }, icon("download", 14), label) : null;
  };
  return h(
    "section",
    { class: "downloads", "aria-labelledby": "downloads-title" },
    h("h3", { class: "sub-title", id: "downloads-title" }, t("ab.dl.title")),
    h("p", { class: "dl-intro" }, t("ab.dl.intro")),
    h(
      "div",
      { class: "table-scroll", tabindex: 0, role: "region", "aria-label": t("ab.dl.region") },
      h(
        "table",
        { class: "data dl-table" },
        h("caption", { class: "sr-only" }, t("ab.dl.title")),
        h("thead", null, h("tr", null, h("th", { scope: "col" }, t("ab.dl.col.table")), h("th", { scope: "col", class: "num" }, t("ab.dl.col.rows")), h("th", { scope: "col" }, t("ab.dl.col.files")))),
        h(
          "tbody",
          null,
          tables.map((d) =>
            h(
              "tr",
              null,
              h("th", { scope: "row" }, h("code", { class: "rule-id" }, d.table), d.description ? englishText(d.description, "span") : null),
              h("td", { class: "num" }, fmt.int(d.rows)),
              h("td", { class: "dl-files" }, link(d.files?.parquet, "Parquet"), link(d.files?.csv, "CSV")),
            ),
          ),
        ),
      ),
    ),
    h(
      "p",
      { class: "dl-licence" },
      h("strong", null, t("ab.dl.licence.lead")),
      " ",
      t("ab.dl.licence", { min: meta.min_stations_published ?? "—", anp: t("src.anp"), bcb: t("src.bcb") }),
      getLang() !== "en" ? ` ${t("ab.dl.english")}` : "",
    ),
  );
}

export function mountAbout(root, { model }) {
  const meta = model.meta;
  const repo = meta.repository;
  const min = meta.min_stations_published ?? "—";
  const head = sectionHead({ number: "07", kicker: t("ab.kicker"), title: t("ab.title") });
  const archHost = h("div", { class: "arch-host" });
  const guarantee = (key) => h("li", { class: "card" }, h("h4", null, t(`ab.g.${key}`)), h("p", null, t(`ab.g.${key}.text`, { min })));
  const doc = (path) => `${repo}/blob/HEAD/${path}`;
  const links = repo
    ? [
        [repo, t("ab.link.repo")],
        [`${repo}#readme`, t("ab.link.readme")],
        [doc("docs/sources.md"), t("ab.link.sources")],
        [doc("docs/validation.md"), t("ab.link.validation")],
        [doc("docs/quality-rules.md"), t("ab.link.rules")],
        [doc("docs/decisions/0002-what-is-published.md"), t("ab.link.published")],
        [doc("docs/decisions/0003-validation-and-quarantine.md"), t("ab.link.quarantine")],
      ]
    : [];

  root.replaceChildren(
    h(
      "div",
      { class: "wrap" },
      head.el,
      h("div", { class: "about-what" }, h("p", null, t("ab.what.1")), h("p", null, t("ab.what.2")), h("p", null, t("ab.what.3"))),
      h(
        "figure",
        { class: "fig" },
        h("figcaption", { class: "fig-head" }, h("h3", { class: "fig-title" }, t("ab.arch.title")), h("p", { class: "fig-take" }, t("ab.arch.take"))),
        archHost,
        h("p", { class: "fig-notes" }, model.has.quality ? t("ab.arch.note", { run: model.quality.run?.id ?? "—" }) : t("ui.missing.section", { file: fileOf("quality") })),
      ),
      h("h3", { class: "sub-title" }, t("ab.g.title")),
      h("ul", { class: "cards", role: "list" }, ["incremental", "idempotent", "quarantine", "contracts", "cost"].map(guarantee)),
      downloads(meta),
      h(
        "div",
        { class: "about-foot" },
        h(
          "div",
          null,
          h("h3", { class: "sub-title" }, t("ab.read.title")),
          h(
            "ul",
            { class: "linklist", role: "list" },
            links.map(([href, label]) => h("li", null, externalLink(href, label))),
            model.has.quality ? h("li", null, h("a", { href: fileOf("quality") }, t("ab.link.report"))) : null,
          ),
          h("p", { class: "stack" }, h("strong", null, `${t("ab.stack")}: `), STACK),
        ),
        h(
          "div",
          null,
          h("h3", { class: "sub-title" }, t("ab.notes.title")),
          h(
            "ul",
            { class: "notes", role: "list" },
            h("li", null, t("ab.notes.aggregates", { min })),
            h("li", null, t("ab.notes.sources")),
            h("li", null, t("ab.notes.affiliation")),
            h("li", null, t("ab.notes.since", { since: fmt.month(meta.since) })),
          ),
        ),
      ),
    ),
  );

  let layout = null;
  const draw = () => {
    const horizontal = (archHost.clientWidth || window.innerWidth) >= 900;
    if (layout === horizontal) return;
    layout = horizontal;
    archHost.replaceChildren(diagram(horizontal, model.quality));
  };
  draw();
  return (changed) => {
    if (changed.has("resize")) draw();
  };
}
