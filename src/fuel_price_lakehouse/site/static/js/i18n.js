// Language and formatting: `t()` looks a key up in the string table
// (js/strings.js), `fmt` holds the Intl formatters of the active locale, and the
// label helpers pick text that comes from the data files (product names,
// regions, rule and check descriptions, events) in the active language.

import { STRINGS } from "./strings.js";

const LOCALES = { pt: "pt-BR", en: "en-GB" };
let lang = "pt";

export function setLang(code) {
  lang = code in LOCALES ? code : "pt";
  formatters.clear();
}

export function getLang() {
  return lang;
}

/** Translate a key, filling {placeholders}. An unknown key shows itself. */
export function t(key, values) {
  const entry = STRINGS[key];
  if (!entry) return key;
  const text = entry[lang === "pt" ? 0 : 1] ?? entry[0];
  if (!values) return text;
  return text.replace(/\{(\w+)\}/g, (match, name) => (name in values ? String(values[name]) : match));
}

// ---- text from the data ---------------------------------------------------

export function productLabel(product) {
  return product[`label_${lang}`] ?? product.label_pt ?? product.id;
}

/** The product name for the middle of a sentence ("gasolina comum", but "GNV"). */
export function productInline(product) {
  const label = productLabel(product);
  const acronym = label.length > 1 && label[1] === label[1].toUpperCase() && label[1] !== label[1].toLowerCase();
  return acronym ? label : label[0].toLowerCase() + label.slice(1);
}

/** Pick `base` (English) or `base_pt` from an object, by language. */
export function localized(object, base) {
  const pt = object?.[`${base}_pt`];
  const en = object?.[`${base}_en`] ?? object?.[base];
  return (lang === "pt" ? (pt ?? en) : (en ?? pt)) ?? "";
}

export function eventText(event) {
  return { title: localized(event, "title"), detail: localized(event, "detail") };
}

export function placeLabel(model, key) {
  if (key === "BR") return t("place.br");
  if (key.startsWith("R-")) {
    const region = model.regionByCode.get(key.slice(2));
    return region?.[`name_${lang}`] ?? region?.name_pt ?? key.slice(2);
  }
  return model.stateByCode.get(key)?.name ?? key;
}

/** Short form for labels at the end of a line: the state code, or the name. */
export function placeShort(model, key) {
  return key === "BR" || key.startsWith("R-") ? placeLabel(model, key) : key;
}

const UNITS = {
  "BRL/L": { label: ["R$ por litro", "R$ per litre"], short: ["litro", "litre"], phrase: ["o litro", "a litre"] },
  "BRL/m3": { label: ["R$ por m³", "R$ per m³"], short: ["m³", "m³"], phrase: ["o m³", "per m³"] },
  "BRL/13kg": { label: ["R$ por botijão de 13 kg", "R$ per 13 kg cylinder"], short: ["botijão", "cylinder"], phrase: ["o botijão de 13 kg", "per 13 kg cylinder"] },
};

function unitText(unit, kind) {
  const entry = UNITS[unit];
  if (entry) return entry[kind][lang === "pt" ? 0 : 1];
  const bare = String(unit ?? "").replace(/^BRL\/?/, "");
  if (kind === "label") return bare ? `R$ / ${bare}` : "R$";
  return bare ? `/ ${bare}` : "";
}

export const unitLabel = (unit) => unitText(unit, "label");
export const unitShort = (unit) => unitText(unit, "short");
export const unitPhrase = (unit) => unitText(unit, "phrase");

// ---- numbers and dates ----------------------------------------------------

const formatters = new Map();
const DASH = "—";

function numberFormat(options) {
  const key = `n${JSON.stringify(options)}`;
  if (!formatters.has(key)) formatters.set(key, new Intl.NumberFormat(LOCALES[lang], options));
  return formatters.get(key);
}

function dateFormat(options) {
  const key = `d${JSON.stringify(options)}`;
  if (!formatters.has(key)) formatters.set(key, new Intl.DateTimeFormat(LOCALES[lang], { timeZone: "UTC", ...options }));
  return formatters.get(key);
}

const missing = (value) => value == null || Number.isNaN(value);
const badDate = (date) => !(date instanceof Date) || Number.isNaN(date.getTime());
// a real minus sign reads better than a hyphen next to digits
const minus = (text) => text.replace(/-/g, "−");

export const fmt = {
  num(value, digits = 2) {
    if (missing(value)) return DASH;
    return minus(numberFormat({ minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value));
  },
  int(value) {
    if (missing(value)) return DASH;
    return minus(numberFormat({ maximumFractionDigits: 0 }).format(value));
  },
  signed(value, digits = 2) {
    if (missing(value)) return DASH;
    return minus(numberFormat({ minimumFractionDigits: digits, maximumFractionDigits: digits, signDisplay: "exceptZero" }).format(value));
  },
  pct(value, digits = 1, signed = false) {
    if (missing(value)) return DASH;
    const options = { style: "percent", minimumFractionDigits: digits, maximumFractionDigits: digits, signDisplay: signed ? "exceptZero" : "auto" };
    return minus(numberFormat(options).format(value));
  },
  brl(value, digits = 2) {
    if (missing(value)) return DASH;
    return minus(numberFormat({ style: "currency", currency: "BRL", minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value));
  },
  /** A price in reais. Litre and m³ prices can show the third decimal. */
  price(value, unit, precise = false) {
    return fmt.brl(value, precise && unit !== "BRL/13kg" ? 3 : 2);
  },
  compact(value, digits = 1) {
    if (missing(value)) return DASH;
    return numberFormat({ notation: "compact", maximumFractionDigits: digits }).format(value);
  },
  date(date) {
    if (badDate(date)) return DASH;
    return dateFormat({ day: "numeric", month: "short", year: "numeric" }).format(date);
  },
  dateIso(date) {
    if (badDate(date)) return DASH;
    return dateFormat({ day: "2-digit", month: "2-digit", year: "numeric" }).format(date);
  },
  dateTime(date) {
    if (badDate(date)) return DASH;
    return `${dateFormat({ day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(date)} UTC`;
  },
  /** Compact UTC timestamp for dense tables: "26/11/2026 01:53". */
  stamp(date) {
    if (badDate(date)) return DASH;
    return dateFormat({ day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(date).replace(",", "");
  },
  monthShort(date) {
    if (badDate(date)) return DASH;
    return dateFormat({ month: "short" }).format(date).replace(".", "");
  },
  dayMonth(date) {
    if (badDate(date)) return DASH;
    return `${date.getUTCDate()} ${fmt.monthShort(date)}`;
  },
  monthYear(date) {
    if (badDate(date)) return DASH;
    return dateFormat({ month: "short", year: "numeric" }).format(date);
  },
  /** "2026-10" -> "out. de 2026" / "Oct 2026". */
  month(text) {
    const match = /^(\d{4})-(\d{2})/.exec(String(text ?? ""));
    if (!match) return String(text ?? DASH);
    return fmt.monthYear(new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1)));
  },
  /** The survey week that starts on `start`: "15 – 21 de nov. de 2026". */
  week(start) {
    return fmt.weekLong(start, new Date(start.getTime() + 6 * 86_400_000), "short");
  },
  weekLong(start, end, month = "long") {
    if (badDate(start) || badDate(end)) return DASH;
    const format = dateFormat({ day: "numeric", month, year: "numeric" });
    if (typeof format.formatRange === "function") return format.formatRange(start, end);
    return `${format.format(start)} – ${format.format(end)}`;
  },
};
