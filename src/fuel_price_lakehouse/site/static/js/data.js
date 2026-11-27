// Loads the JSON files the pipeline publishes next to index.html (data/*.json)
// and builds the model the views read. meta.json and national.json are
// required: without them there is nothing to show. The other five are
// optional: a file that fails to load, or comes in an unexpected shape, is
// listed in `model.missing` and the sections that need it show an empty state
// naming it, while the rest of the page works.
//
// Nothing here knows today's values: the latest week, the products, which
// states have data and which cells are null all come from the files. A null in
// a series means "suppressed or not surveyed" and stays null all the way to the
// screen, where it is drawn as a gap.

import { lastValid } from "./series.js";

const REQUIRED = ["meta", "national"];
const OPTIONAL = ["states", "regions", "flags", "events", "quality"];

class DataLoadError extends Error {
  constructor(file, reason) {
    super(`${file}: ${reason}`);
    this.name = "DataLoadError";
  }
}

/** Where a data file is published, relative to index.html. */
export const fileOf = (name) => `data/${name}.json`;

async function fetchJson(name) {
  const file = fileOf(name);
  let response;
  try {
    response = await fetch(file, { cache: "no-cache" });
  } catch (error) {
    throw new DataLoadError(file, error instanceof Error ? error.message : String(error));
  }
  if (!response.ok) throw new DataLoadError(file, `HTTP ${response.status}`);
  try {
    return await response.json();
  } catch {
    throw new DataLoadError(file, "invalid JSON");
  }
}

export function parseIsoDate(iso) {
  return new Date(`${String(iso).slice(0, 10)}T00:00:00Z`);
}

/** A Date from an ISO timestamp, or null when the value is missing or malformed. */
export function toDate(value) {
  if (value == null || value === "") return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * Re-index every series of `source` onto the national week axis. The pipeline
 * writes all files with the same axis, so this is normally a no-op; it is here
 * so that a file with a different axis shows gaps instead of shifted prices.
 * Returns null when the file does not have the expected shape.
 */
function alignProducts(source, weeks) {
  if (!source || !Array.isArray(source.weeks) || typeof source.products !== "object" || !source.products) return null;
  const own = source.weeks;
  const same = own.length === weeks.length && own.every((w, i) => w === weeks[i]);
  if (same) return source.products;
  const position = new Map(own.map((w, i) => [w, i]));
  const remap = (values) => weeks.map((w) => (position.has(w) ? (values[position.get(w)] ?? null) : null));
  const out = {};
  for (const [product, groups] of Object.entries(source.products)) {
    out[product] = {};
    for (const [group, measures] of Object.entries(groups ?? {})) {
      out[product][group] = {};
      for (const [measure, values] of Object.entries(measures ?? {})) {
        out[product][group][measure] = Array.isArray(values) ? remap(values) : values;
      }
    }
  }
  return out;
}

function checkRequired(meta, national) {
  if (!meta || typeof meta !== "object") throw new DataLoadError(fileOf("meta"), "unexpected shape");
  if (!Array.isArray(meta.products)) throw new DataLoadError(fileOf("meta"), "products is not a list");
  if (!Array.isArray(national?.weeks) || national.weeks.length === 0) throw new DataLoadError(fileOf("national"), "no weeks");
  if (!national.products || typeof national.products !== "object") throw new DataLoadError(fileOf("national"), "products is missing");
  for (const [id, measures] of Object.entries(national.products)) {
    if (!Array.isArray(measures?.mean) || measures.mean.length !== national.weeks.length) {
      throw new DataLoadError(fileOf("national"), `series of ${id} is not aligned with weeks`);
    }
  }
}

function buildModel(raw, missing) {
  const { meta, national } = raw;
  checkRequired(meta, national);
  const weeks = national.weeks;
  const dates = weeks.map(parseIsoDate);
  const weekIndex = new Map(weeks.map((w, i) => [w, i]));
  const blank = () => weeks.map(() => null);

  // Products: the catalogue is meta.json, restricted to those with a national
  // series; a product present only in the data still gets a row.
  const products = meta.products.filter((p) => national.products[p.id]);
  for (const id of Object.keys(national.products)) {
    if (!products.some((p) => p.id === id)) products.push({ id, label_pt: id, label_en: id, unit: "" });
  }
  if (products.length === 0) throw new DataLoadError(fileOf("meta"), "no product has data");
  const states = Array.isArray(meta.states) ? meta.states : [];
  const regions = Array.isArray(meta.regions) ? meta.regions : [];

  // The latest week is the one the pipeline declares; if it is not on the axis
  // fall back to the last week in which any product has a national price.
  let latest = weekIndex.get(meta.latest_week) ?? -1;
  if (latest < 0) latest = Math.max(...Object.values(national.products).map((p) => lastValid(p.mean)));
  if (latest < 0) latest = weeks.length - 1;

  // Optional files: a wrong shape counts as missing.
  const optional = (name, value) => {
    if (value == null && !missing.includes(fileOf(name))) missing.push(fileOf(name));
    return value;
  };
  const byState = optional("states", raw.states === undefined ? undefined : alignProducts(raw.states, weeks));
  const byRegion = optional("regions", raw.regions === undefined ? undefined : alignProducts(raw.regions, weeks));
  const byFlag = optional("flags", raw.flags === undefined ? undefined : alignProducts(raw.flags, weeks));
  const events = optional("events", raw.events === undefined ? undefined : Array.isArray(raw.events) ? raw.events : null);
  const qualityOk = raw.quality && typeof raw.quality === "object" && raw.quality.run;
  const quality = optional("quality", raw.quality === undefined ? undefined : qualityOk ? raw.quality : null);

  return {
    meta,
    weeks,
    dates,
    weekIndex,
    latest,
    products,
    productById: new Map(products.map((p) => [p.id, p])),
    leadFuel: products.some((p) => p.id === "GASOLINA") ? "GASOLINA" : products[0].id,
    states,
    stateByCode: new Map(states.map((st) => [st.code, st])),
    regions,
    regionByCode: new Map(regions.map((r) => [r.code, r])),
    national: national.products,
    usd: Array.isArray(national.usd_brl) ? national.usd_brl : blank(),
    deflator: Array.isArray(national.ipca_deflator) ? national.ipca_deflator : blank(),
    provisional: Array.isArray(national.ipca_provisional) ? national.ipca_provisional : weeks.map(() => false),
    byState: byState ?? {},
    byRegion: byRegion ?? {},
    byFlag: byFlag ?? {},
    events: (events ?? [])
      .filter((e) => e && e.date)
      .map((e) => ({ ...e, when: parseIsoDate(e.date), fuels: Array.isArray(e.fuels) ? e.fuels : [] }))
      .sort((a, b) => a.when - b.when),
    quality: quality ?? null,
    /** Which optional files arrived: { states, regions, flags, events, quality }. */
    has: { states: Boolean(byState), regions: Boolean(byRegion), flags: Boolean(byFlag), events: Boolean(events), quality: Boolean(quality) },
    missing,
  };
}

export async function loadData() {
  const names = [...REQUIRED, ...OPTIONAL];
  const results = await Promise.allSettled(names.map(fetchJson));
  const raw = {};
  const missing = [];
  results.forEach((result, i) => {
    const name = names[i];
    if (result.status === "fulfilled") raw[name] = result.value;
    else if (REQUIRED.includes(name)) throw result.reason;
    else {
      console.info(`optional file not loaded: ${result.reason?.message ?? name}`);
      missing.push(fileOf(name));
      raw[name] = undefined;
    }
  });
  return buildModel(raw, missing);
}

