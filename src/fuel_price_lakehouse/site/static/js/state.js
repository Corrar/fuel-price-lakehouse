// One small store for everything a visitor can choose, mirrored in the URL
// hash so that any view can be shared:
//
//   #/explorer?fuel=ETANOL&places=BR,SP,R-NE&period=3y&prices=real
//
// Values equal to the defaults are left out of the URL. Everything read from
// the URL is validated against the loaded data; an unknown fuel or state is
// dropped instead of breaking the page.

import { BRAZIL, placeExists } from "./series.js";

export const VIEWS = ["overview", "findings", "explorer", "states", "parity", "flags", "quality", "about"];
export const MAX_PLACES = 5;
export const PERIOD_PRESETS = ["1y", "3y", "5y", "max"];
const LANGS = ["pt", "en"];
const LANG_KEY = "fpl.lang";

export const state = {
  view: "overview",
  fuel: null,
  // Slots, not a plain list: a place keeps its slot (and so its colour) when
  // another one is removed. Empty slots are null.
  places: [BRAZIL],
  period: "max", // a preset, or {from: ISO week, to: ISO week}
  real: false,
  band: true,
  usd: false,
  events: true,
  week: null, // null follows the latest week
  uf: null, // the state highlighted in the comparison views
  lang: "pt",
};

let model = null;
let defaults = null;
const listeners = new Set();

function sameValue(a, b) {
  return a === b || JSON.stringify(a) === JSON.stringify(b);
}

function readStoredLang() {
  try {
    const stored = localStorage.getItem(LANG_KEY);
    return LANGS.includes(stored) ? stored : null;
  } catch {
    return null;
  }
}

function storeLang(lang) {
  try {
    localStorage.setItem(LANG_KEY, lang);
  } catch {
    /* private mode or blocked storage: the choice lasts for this visit only */
  }
}

/** Language is needed before the data (for the loading and error messages). */
export function initialLang() {
  const fromUrl = new URLSearchParams(location.hash.split("?")[1] ?? "").get("lang");
  return LANGS.includes(fromUrl) ? fromUrl : (readStoredLang() ?? "pt");
}

function normalisePlaces(list) {
  const seen = new Set();
  const slots = [];
  for (const key of list.slice(0, MAX_PLACES)) {
    if (key && placeExists(model, key) && !seen.has(key)) {
      seen.add(key);
      slots.push(key);
    } else {
      slots.push(null);
    }
  }
  while (slots.length && slots[slots.length - 1] == null) slots.pop();
  return slots.some(Boolean) ? slots : [BRAZIL];
}

function parsePeriod(text) {
  if (PERIOD_PRESETS.includes(text)) return text;
  const match = /^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$/.exec(text ?? "");
  if (!match) return null;
  const from = nearestWeek(match[1]);
  const to = nearestWeek(match[2]);
  if (from == null || to == null || from >= to) return null;
  return { from, to };
}

/** The week on the axis at or just before an ISO date, as ISO text. */
function nearestWeek(iso) {
  if (model.weekIndex.has(iso)) return iso;
  let found = null;
  for (const week of model.weeks) {
    if (week <= iso) found = week;
    else break;
  }
  return found ?? model.weeks[0];
}

function parseHash(hash) {
  const patch = {};
  const text = hash.replace(/^#\/?/, "");
  const [viewPart, queryPart = ""] = text.split("?");
  if (VIEWS.includes(viewPart)) patch.view = viewPart;
  const query = new URLSearchParams(queryPart);

  const fuel = query.get("fuel");
  patch.fuel = model.productById.has(fuel) ? fuel : defaults.fuel;

  patch.places = query.has("places") ? normalisePlaces(query.get("places").split(",")) : [BRAZIL];
  patch.period = parsePeriod(query.get("period")) ?? "max";
  patch.real = query.get("prices") === "real";
  patch.band = query.get("band") !== "0";
  patch.usd = query.get("usd") === "1";
  patch.events = query.get("events") !== "0";

  const week = query.get("week");
  patch.week = null;
  // a date outside the series is ignored rather than snapped to its first week
  if (week && /^\d{4}-\d{2}-\d{2}$/.test(week) && week >= model.weeks[0]) {
    const snapped = nearestWeek(week);
    if (model.weekIndex.get(snapped) < model.latest) patch.week = snapped;
  }

  const uf = query.get("uf");
  patch.uf = model.stateByCode.has(uf) ? uf : null;

  const lang = query.get("lang");
  if (LANGS.includes(lang)) patch.lang = lang;
  return patch;
}

function serialise(target = state) {
  const query = new URLSearchParams();
  if (target.fuel !== defaults.fuel) query.set("fuel", target.fuel);
  if (!sameValue(target.places, [BRAZIL])) query.set("places", target.places.map((p) => p ?? "").join(","));
  if (target.period !== "max") {
    query.set("period", typeof target.period === "string" ? target.period : `${target.period.from}..${target.period.to}`);
  }
  if (target.real) query.set("prices", "real");
  if (!target.band) query.set("band", "0");
  if (target.usd) query.set("usd", "1");
  if (!target.events) query.set("events", "0");
  if (target.week) query.set("week", target.week);
  if (target.uf) query.set("uf", target.uf);
  if (target.lang !== "pt") query.set("lang", target.lang);
  const text = query.toString().replace(/%2C/gi, ",");
  return `#/${target.view}${text ? `?${text}` : ""}`;
}

/** A shareable link to a view with some of the state overridden. */
export function linkTo(view, patch = {}) {
  return serialise({ ...state, ...patch, view });
}

let lastWritten = null;

function writeUrl() {
  const hash = serialise();
  if (hash === lastWritten) return;
  lastWritten = hash;
  try {
    history.replaceState(null, "", hash);
  } catch {
    /* sandboxed frames may refuse; the page still works without the URL */
  }
}

export function initState(loadedModel) {
  model = loadedModel;
  defaults = { fuel: model.leadFuel };
  state.fuel = defaults.fuel;
  state.lang = initialLang();
  Object.assign(state, parseHash(location.hash));
  writeUrl();

  // A link pasted into the address bar, or back/forward.
  window.addEventListener("hashchange", () => {
    if (location.hash === lastWritten) return;
    setState(parseHash(location.hash), { fromUrl: true });
  });
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Apply a change. Listeners receive the set of keys that really changed and
 * the options, so a view redraws only when something it shows moved.
 */
export function setState(patch, options = {}) {
  const changed = new Set();
  for (const [key, value] of Object.entries(patch)) {
    if (!(key in state)) continue;
    const next = key === "places" ? normalisePlaces(value) : value;
    if (!sameValue(state[key], next)) {
      state[key] = next;
      changed.add(key);
    }
  }
  if (changed.size === 0) return changed;
  if (changed.has("lang")) storeLang(state.lang);
  writeUrl();
  for (const listener of listeners) listener(changed, options);
  return changed;
}

// ---- derived --------------------------------------------------------------

/** Index of the week the comparison views show. */
export function weekIndex() {
  if (state.week && model.weekIndex.has(state.week)) return model.weekIndex.get(state.week);
  return model.latest;
}

/** Set the week by index; the latest week is stored as "follow the latest". */
export function setWeekIndex(i) {
  const clamped = Math.max(0, Math.min(model.latest, i));
  setState({ week: clamped >= model.latest ? null : model.weeks[clamped] });
}

/** The [first, last] week indices of the chosen period. */
export function periodRange() {
  const last = model.latest;
  if (typeof state.period === "object" && state.period) {
    const from = model.weekIndex.get(state.period.from) ?? 0;
    const to = model.weekIndex.get(state.period.to) ?? last;
    return [Math.max(0, Math.min(from, to - 1)), Math.min(last, to)];
  }
  const years = { "1y": 1, "3y": 3, "5y": 5 }[state.period];
  if (!years) return [0, last];
  return [Math.max(0, last - years * 52), last];
}

export function selectedPlaces() {
  return state.places.map((key, slot) => ({ key, slot })).filter((p) => p.key);
}

/** The p10–p90 band is drawn for one place only: overlapping bands would muddy the colours. */
export function bandActive() {
  return state.band && selectedPlaces().length === 1;
}

/** Put a place in the first free slot. Returns false when all are taken. */
export function addPlace(key) {
  if (state.places.includes(key)) return true;
  const slots = [...state.places];
  let free = slots.indexOf(null);
  if (free < 0) free = slots.length;
  if (free >= MAX_PLACES) return false;
  slots[free] = key;
  setState({ places: slots });
  return true;
}

export function removePlace(key) {
  if (selectedPlaces().length <= 1) return;
  setState({ places: state.places.map((p) => (p === key ? null : p)) });
}
