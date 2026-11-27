// The page chrome around the sections: masthead (title, week, language and
// theme), the sticky section nav (with the data-quality chip and the link to
// the code, so that trust in the data is one click away from anywhere), the
// notice for data files that did not load, and the footer credits.

import { h } from "./dom.js";
import { t, fmt, getLang } from "./i18n.js";
import { toDate } from "./data.js";
import { state, linkTo, VIEWS } from "./state.js";
import { currentTheme, toggleTheme } from "./theme.js";
import { icon, credit, externalLink } from "./ui.js";

const STATUS_ICON = { pass: "check", warn: "warn", fail: "fail" };

function qualityChip(model, goTo) {
  const quality = model.quality;
  if (!quality?.status || !(quality.status in STATUS_ICON)) return null;
  const checks = Array.isArray(quality.checks) ? quality.checks : [];
  const count = (status) => checks.filter((c) => c.status === status).length;
  const text =
    quality.status === "pass"
      ? t("chip.pass", { n: count("pass"), total: checks.length })
      : quality.status === "warn"
        ? t("chip.warn", { n: count("warn") })
        : t("chip.fail", { n: count("fail") });
  const label = `${t("chip.label")}: ${t(`status.${quality.status}`)}. ${text}. ${t("ov.q.link")}`;
  return h(
    "a",
    {
      class: `qchip is-${quality.status}`,
      href: linkTo("quality"),
      dataset: { view: "quality" },
      "aria-label": label,
      title: label,
      onclick: (event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey) return;
        event.preventDefault();
        goTo("quality");
      },
    },
    h("span", { class: "status-dot", "aria-hidden": "true" }, icon(STATUS_ICON[quality.status], 12)),
    h("span", { class: "qchip-text", "aria-hidden": "true" }, text),
  );
}

export function renderChrome(model, { goTo, setLanguage, navClick }) {
  document.documentElement.lang = getLang() === "pt" ? "pt-BR" : "en";
  document.title = t("site.title");
  document.querySelector('meta[name="description"]')?.setAttribute("content", t("site.description"));
  document.getElementById("skip").textContent = t("ui.skip");
  const nav = document.getElementById("sectionnav");
  nav.setAttribute("aria-label", t("ui.sections"));

  const lang = getLang();
  const langButton = (code, label, name) =>
    h("button", { type: "button", class: "lang-btn", "aria-pressed": String(lang === code), lang: code === "pt" ? "pt-BR" : "en", title: name, onclick: () => setLanguage(code) }, label);
  const dark = currentTheme() === "dark";
  const themeLabel = t(dark ? "ui.theme.toLight" : "ui.theme.toDark");

  document.getElementById("masthead").replaceChildren(
    h(
      "div",
      { class: "wrap masthead-row" },
      h(
        "a",
        { class: "brand", href: "#/overview", onclick: (event) => navClick(event, "overview") },
        h("span", { class: "brand-name" }, t("site.name")),
        h("span", { class: "brand-tag" }, "fuel-price-lakehouse"),
      ),
      h(
        "div",
        { class: "masthead-tools" },
        model ? h("p", { class: "dateline" }, t("site.dateline", { week: fmt.week(model.dates[model.latest]) })) : null,
        h("div", { class: "lang", role: "group", "aria-label": t("ui.language") }, langButton("pt", "PT", "Português"), langButton("en", "EN", "English")),
        h("button", { type: "button", class: "iconbtn theme-btn", "aria-label": themeLabel, title: themeLabel, onclick: () => toggleTheme() }, icon(dark ? "sun" : "moon", 18)),
      ),
    ),
  );

  const repo = model?.meta.repository;
  nav.replaceChildren(
    h(
      "div",
      { class: "wrap nav-row" },
      h(
        "ul",
        { class: "nav-list", role: "list" },
        VIEWS.map((view) => h("li", null, h("a", { href: model ? linkTo(view) : `#/${view}`, dataset: { view }, onclick: (event) => navClick(event, view) }, t(`nav.${view}`)))),
      ),
      h(
        "div",
        { class: "nav-tools" },
        model?.meta.sample ? h("span", { class: "badge-sample", title: t("ov.sample") }, t("chip.sample")) : null,
        model ? qualityChip(model, goTo) : null,
        repo ? h("a", { class: "iconbtn repo-btn", href: repo, target: "_blank", rel: "noopener noreferrer", "aria-label": t("ui.repo"), title: t("ui.repo") }, icon("code", 18)) : null,
      ),
    ),
  );
  markActive(state.view);

  const notice = document.getElementById("notice");
  if (model?.missing.length) {
    notice.hidden = false;
    notice.replaceChildren(h("div", { class: "wrap" }, h("p", { class: "notice-text" }, icon("warn", 14), t("ui.missing.banner", { files: model.missing.join(", ") }))));
  } else {
    notice.hidden = true;
  }

  document.getElementById("footer").replaceChildren(
    h(
      "div",
      { class: "wrap footer-row" },
      h("p", null, credit("anp", "bcb"), "."),
      h("p", null, t("footer.disclaimer")),
      model
        ? h(
            "p",
            null,
            t("footer.generated", { when: fmt.dateTime(toDate(model.meta.generated_at)), run: model.meta.run_id ?? "—" }),
            model.meta.sample ? ` ${t("ov.sample")}` : "",
            " ",
            repo ? externalLink(repo, t("footer.code")) : null,
          )
        : null,
    ),
  );
}

export function markActive(view) {
  for (const link of document.querySelectorAll("#sectionnav .nav-list a[data-view]")) {
    if (link.dataset.view !== view) {
      link.removeAttribute("aria-current");
      continue;
    }
    link.setAttribute("aria-current", "true");
    // keep the active item visible in the horizontally scrolling nav on phones
    const list = link.closest(".nav-list");
    if (list && list.scrollWidth > list.clientWidth) {
      const target = link.offsetLeft - (list.clientWidth - link.offsetWidth) / 2;
      list.scrollTo({ left: Math.max(0, target), behavior: "auto" });
    }
  }
}

/** Nav links carry the current choices, so that "copy link" works anywhere. */
export function refreshNavLinks() {
  for (const link of document.querySelectorAll("#sectionnav a[data-view]")) link.setAttribute("href", linkTo(link.dataset.view));
}
