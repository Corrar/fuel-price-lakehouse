// Light and dark themes. The page follows the operating system until the
// visitor uses the toggle; from then on the choice is stored and stamped on
// <html data-theme>. Colours live in styles.css as custom properties; charts
// read them through `tokens()` and are redrawn when the theme changes.

const THEME_KEY = "fpl.theme";
const media = window.matchMedia("(prefers-color-scheme: dark)");
const listeners = new Set();
let cachedTokens = null;

function storedTheme() {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

export function currentTheme() {
  const stamped = document.documentElement.dataset.theme;
  if (stamped === "light" || stamped === "dark") return stamped;
  return media.matches ? "dark" : "light";
}

function notify() {
  cachedTokens = null;
  for (const listener of listeners) listener(currentTheme());
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* the choice lasts for this visit only */
  }
  notify();
}

export function toggleTheme() {
  setTheme(currentTheme() === "dark" ? "light" : "dark");
}

export function initTheme() {
  const stored = storedTheme();
  if (stored) document.documentElement.dataset.theme = stored;
  media.addEventListener("change", () => {
    if (!storedTheme()) notify();
  });
}

export function onThemeChange(listener) {
  listeners.add(listener);
}

/** The colours charts need, resolved for the active theme. */
export function tokens() {
  if (cachedTokens) return cachedTokens;
  const style = getComputedStyle(document.documentElement);
  const read = (name) => style.getPropertyValue(name).trim();
  cachedTokens = {
    paper: read("--paper"),
    paper2: read("--paper-2"),
    ink: read("--ink"),
    ink2: read("--ink-2"),
    ink3: read("--ink-3"),
    grid: read("--grid"),
    axis: read("--axis"),
    series: [1, 2, 3, 4, 5].map((i) => read(`--series-${i}`)),
    neutral: read("--series-neutral"),
    quiet: read("--series-quiet"),
    ethanol: read("--c-ethanol"),
    gasoline: read("--c-gasoline"),
    status: { pass: read("--ok"), warn: read("--warn"), fail: read("--fail") },
  };
  return cachedTokens;
}
