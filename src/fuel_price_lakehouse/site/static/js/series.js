// Pure helpers over the week-aligned arrays of the model: null-aware search,
// changes, places (Brazil, a region or a state), real terms, survey gaps,
// the latest national figures and the ethanol / gasoline ratio. None of them
// turns a null into a number.

const DAY_MS = 86_400_000;

/** Index of the last non-null value at or before `upTo`, or -1. */
export function lastValid(values, upTo) {
  if (!values) return -1;
  for (let i = Math.min(upTo ?? values.length - 1, values.length - 1); i >= 0; i--) {
    if (values[i] != null) return i;
  }
  return -1;
}

/** Index of the first non-null value at or after `from`, or -1. */
export function firstValid(values, from = 0, upTo) {
  if (!values) return -1;
  const end = Math.min(upTo ?? values.length - 1, values.length - 1);
  for (let i = Math.max(0, from); i <= end; i++) {
    if (values[i] != null) return i;
  }
  return -1;
}

export function pctChange(now, before) {
  if (now == null || before == null || before === 0) return null;
  return now / before - 1;
}

// ---- places ---------------------------------------------------------------
// A place is "BR", a region ("R-SE") or a state ("SP"). Regions carry a prefix
// because SE is both the Southeast region and the state of Sergipe.

export const BRAZIL = "BR";

function isRegionKey(key) {
  return typeof key === "string" && key.startsWith("R-");
}

export function placeExists(model, key) {
  if (key === BRAZIL) return true;
  if (isRegionKey(key)) return model.regionByCode.has(key.slice(2));
  return model.stateByCode.has(key);
}

/** The measures of one place for one product, or null when there are none. */
export function placeSeries(model, key, fuel) {
  if (key === BRAZIL) return model.national[fuel] ?? null;
  if (isRegionKey(key)) return model.byRegion[fuel]?.[key.slice(2)] ?? null;
  return model.byState[fuel]?.[key] ?? null;
}

// ---- time -----------------------------------------------------------------

/** Index of the week closest to one year (52 weeks) before week `i`, or -1. */
function indexYearBefore(model, i) {
  const target = model.dates[i].getTime() - 364 * DAY_MS;
  let best = -1;
  let bestGap = Infinity;
  for (let j = Math.max(0, i - 56); j <= Math.min(model.weeks.length - 1, i - 48); j++) {
    const gap = Math.abs(model.dates[j].getTime() - target);
    if (gap < bestGap) {
      best = j;
      bestGap = gap;
    }
  }
  return bestGap <= 10 * DAY_MS ? best : -1;
}

/**
 * The comparison point a year before week `i` for one series: that week, or,
 * when the survey had no value then, the closest earlier one within 4 weeks.
 */
function yearAgoIndex(model, values, i) {
  const target = indexYearBefore(model, i);
  if (target < 0 || values?.[target] != null) return target;
  const near = lastValid(values, target);
  return near >= 0 && target - near <= 4 ? near : -1;
}

/** Multiply a nominal series by the IPCA deflator (prices of the latest IPCA month). */
export function toReal(model, values) {
  if (!values) return null;
  return values.map((v, i) => (v == null || model.deflator[i] == null ? null : v * model.deflator[i]));
}

/** The national weekly mean in the chosen terms. */
export function nationalMean(model, fuel, real) {
  const series = model.national[fuel];
  if (!series) return null;
  if (!real) return series.mean;
  return Array.isArray(series.mean_real) ? series.mean_real : toReal(model, series.mean);
}

/** Runs of consecutive weeks in [i0, i1] where every given series is null. */
export function gapRuns(seriesList, i0, i1, minLength = 2) {
  const runs = [];
  let start = -1;
  for (let i = i0; i <= i1 + 1; i++) {
    const empty = i <= i1 && seriesList.every((values) => values[i] == null);
    if (empty && start < 0) start = i;
    if (!empty && start >= 0) {
      if (i - start >= minLength) runs.push([start, i - 1]);
      start = -1;
    }
  }
  return runs;
}

/** Events that concern one product (an empty `fuels` list concerns all). */
export function eventsFor(model, fuel) {
  return model.events.filter((e) => e.fuels.length === 0 || e.fuels.includes(fuel));
}

export function endOfWeek(date) {
  return new Date(date.getTime() + 6 * DAY_MS);
}

// ---- figures --------------------------------------------------------------

/** Latest value of any weekly series with its week-on-week and 12-month changes. */
export function latestChange(model, values) {
  const i = lastValid(values, model.latest);
  if (i < 0) return null;
  const previous = lastValid(values, i - 1);
  const yearAgo = yearAgoIndex(model, values, i);
  return {
    index: i,
    value: values[i],
    previousIndex: previous,
    consecutive: previous === i - 1,
    week: previous >= 0 ? pctChange(values[i], values[previous]) : null,
    yearIndex: yearAgo,
    year: yearAgo >= 0 ? pctChange(values[i], values[yearAgo]) : null,
  };
}

/** Latest national figures of one product: price, changes and sample size. */
export function latestStats(model, fuel) {
  const series = model.national[fuel];
  if (!series) return null;
  const base = latestChange(model, series.mean);
  if (!base) return null;
  const real = nationalMean(model, fuel, true);
  return {
    ...base,
    price: base.value,
    yearReal: base.yearIndex >= 0 ? pctChange(real[base.index], real[base.yearIndex]) : null,
    stations: series.n_stations?.[base.index] ?? null,
    states: series.n_states?.[base.index] ?? null,
    isLatestWeek: base.index === model.latest,
  };
}

/** Every state's mean for one product and week, with its deviation from the national mean. */
export function stateRows(model, fuel, i) {
  const national = model.national[fuel]?.mean?.[i] ?? null;
  return model.states.map((st) => {
    const measures = model.byState[fuel]?.[st.code];
    const v = measures?.mean?.[i] ?? null;
    return {
      st,
      v,
      deviation: v != null && national != null ? v / national - 1 : null,
      n: measures?.n_stations?.[i] ?? null,
      p10: measures?.p10?.[i] ?? null,
      p90: measures?.p90?.[i] ?? null,
    };
  });
}

// ---- ethanol / gasoline ---------------------------------------------------
// Product ids are part of the data contract; the rule of thumb is that a litre
// of ethanol takes a car about 70% as far as a litre of gasoline.

export const ETHANOL = "ETANOL";
export const GASOLINE = "GASOLINA";
export const PARITY = 0.7;

export function ratioSeries(ethanol, gasoline) {
  if (!ethanol || !gasoline) return null;
  return ethanol.map((e, i) => (e == null || gasoline[i] == null || gasoline[i] === 0 ? null : e / gasoline[i]));
}
