// Boot: load the data, restore the state from the URL, mount the sections and
// keep them in sync with the store, the theme, the language and the window.
// Every section is a view: `mount(section, {model, goTo})` builds its DOM and
// returns `update(changedKeys)`.

import { h, debounce } from "./dom.js";
import { assertLibraries } from "./lib.js";
import { loadData } from "./data.js";
import { t, setLang } from "./i18n.js";
import { state, setState, subscribe, initState, initialLang, VIEWS } from "./state.js";
import { initTheme, onThemeChange } from "./theme.js";
import { hideTip } from "./tooltip.js";
import { renderChrome, markActive, refreshNavLinks } from "./chrome.js";
import { mountOverview } from "./views/overview.js";
import { mountFindings } from "./views/findings.js";
import { mountExplorer } from "./views/explorer.js";
import { mountStates } from "./views/states.js";
import { mountParity } from "./views/parity.js";
import { mountFlags } from "./views/flags.js";
import { mountQuality } from "./views/quality.js";
import { mountAbout } from "./views/about.js";

const MOUNTS = {
  overview: mountOverview,
  findings: mountFindings,
  explorer: mountExplorer,
  states: mountStates,
  parity: mountParity,
  flags: mountFlags,
  quality: mountQuality,
  about: mountAbout,
};

const main = document.getElementById("main");
const boot = document.getElementById("boot");
let model = null;
let updaters = [];
let spyPausedUntil = 0;
let bootError = null;

const chrome = () => renderChrome(model, { goTo, setLanguage, navClick });

// ---- navigation -----------------------------------------------------------

function scrollToView(view, smooth) {
  const section = document.getElementById(view);
  if (!section) return;
  const behavior = smooth && !window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "smooth" : "auto";
  spyPausedUntil = performance.now() + (behavior === "smooth" ? 900 : 150);
  if (view === "overview") window.scrollTo({ top: 0, behavior });
  else section.scrollIntoView({ behavior, block: "start" });
}

/** Go to a view, optionally changing the state first (used by in-page links). */
function goTo(view, patch = {}) {
  setState({ ...patch, view });
  markActive(view);
  scrollToView(view, true);
  const heading = document.querySelector(`#${view} h2, #${view} h1`);
  if (heading) {
    heading.setAttribute("tabindex", "-1");
    heading.focus({ preventScroll: true });
  }
}

function navClick(event, view) {
  if (event.metaKey || event.ctrlKey || event.shiftKey || !model) return;
  event.preventDefault();
  goTo(view);
}

function watchSections() {
  // The section crossing a line 40% down the viewport is the current view.
  const observer = new IntersectionObserver(
    (entries) => {
      if (performance.now() < spyPausedUntil) return;
      for (const entry of entries) {
        if (entry.isIntersecting && entry.target.id !== state.view) {
          setState({ view: entry.target.id });
          markActive(entry.target.id);
        }
      }
    },
    { rootMargin: "-40% 0px -59% 0px" },
  );
  for (const view of VIEWS) observer.observe(document.getElementById(view));
}

// The hash holds the state, so the skip link moves the focus itself instead of
// navigating to "#main" (which would reset every choice).
document.getElementById("skip").addEventListener("click", (event) => {
  event.preventDefault();
  main.focus({ preventScroll: true });
  main.scrollIntoView({ block: "start" });
});

// ---- views ----------------------------------------------------------------

function mountViews() {
  hideTip();
  const context = { model, goTo };
  updaters = VIEWS.map((view) => {
    const section = document.getElementById(view);
    section.setAttribute("aria-label", t(`nav.${view}`));
    try {
      return MOUNTS[view](section, context);
    } catch (error) {
      // one broken view must not take the page down with it
      console.warn(`view "${view}" failed to render`, error);
      section.replaceChildren(h("div", { class: "wrap" }, h("p", { class: "empty" }, t("ui.viewError"))));
      return () => {};
    }
  });
}

function updateViews(changed) {
  for (const update of updaters) {
    try {
      update(changed);
    } catch (error) {
      console.warn("view update failed", error);
    }
  }
}

function setLanguage(code) {
  if (model) setState({ lang: code });
  else {
    // still loading, or the data failed: only the chrome and the message change
    setLang(code);
    chrome();
    if (bootError) showError(bootError);
    else showLoading();
  }
}

// ---- boot -----------------------------------------------------------------

function showLoading() {
  boot.replaceChildren(h("p", { class: "boot-msg" }, t("ui.loading")));
}

function showError(error) {
  bootError = error;
  main.dataset.state = "error";
  boot.hidden = false;
  boot.replaceChildren(
    h(
      "div",
      { class: "boot-error", role: "alert" },
      h("h1", null, t("ui.error.title")),
      h("p", null, t("ui.error.text")),
      h("p", { class: "mono" }, String(error?.message ?? error)),
      location.protocol === "file:" ? h("p", null, t("ui.error.file")) : null,
      h("button", { type: "button", class: "btn", onclick: () => location.reload() }, t("ui.error.retry")),
    ),
  );
}

async function fontsReady() {
  // Labels are measured before drawing; wait (briefly) for the real fonts.
  try {
    await Promise.race([
      Promise.all([document.fonts.load('12px "IBM Plex Sans"'), document.fonts.load('600 12px "IBM Plex Sans"'), document.fonts.load('600 32px "Newsreader"')]),
      new Promise((resolve) => setTimeout(resolve, 1500)),
    ]);
  } catch {
    /* fall back to whatever is available */
  }
}

async function start() {
  initTheme();
  setLang(initialLang());
  chrome();
  showLoading();
  try {
    assertLibraries();
    [model] = await Promise.all([loadData(), fontsReady()]);
    initState(model);
    setLang(state.lang);
  } catch (error) {
    showError(error);
    return;
  }

  // The sections must be laid out before the views mount: charts measure
  // the width of their containers.
  main.dataset.state = "ready";
  boot.hidden = true;
  chrome();
  mountViews();

  subscribe((changed, options) => {
    if (changed.has("lang")) {
      setLang(state.lang);
      chrome();
      mountViews();
    } else if ([...changed].some((key) => key !== "view")) {
      updateViews(changed);
    }
    if (options.fromUrl) {
      markActive(state.view);
      if (changed.has("view")) scrollToView(state.view, false);
    }
    refreshNavLinks();
  });

  onThemeChange(() => {
    chrome();
    updateViews(new Set(["theme"]));
  });

  let lastWidth = window.innerWidth;
  window.addEventListener(
    "resize",
    debounce(() => {
      if (window.innerWidth === lastWidth) return; // mobile URL bar: height only
      lastWidth = window.innerWidth;
      hideTip();
      updateViews(new Set(["resize"]));
    }, 150),
  );

  if (state.view !== "overview") scrollToView(state.view, false);
  markActive(state.view);
  // Start watching after the initial jump so the URL keeps the requested view.
  requestAnimationFrame(() => watchSections());
}

start();
