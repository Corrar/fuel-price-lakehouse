// Form controls shared by the views. They are ordinary HTML controls (select,
// radio, checkbox, range) with a little styling, so keyboard and screen-reader
// behaviour comes from the browser. Each builder returns its element and a
// `set` function: the store is the source of truth and pushes values back in.

import { h } from "./dom.js";
import { t, fmt, productLabel } from "./i18n.js";
import { lastValid, firstValid } from "./series.js";

let uid = 0;
const nextId = (prefix) => `${prefix}-${++uid}`;

/** A labelled native <select>. Options may carry `group` and `disabled`. */
export function selectControl({ label, options, value, onChange, placeholder, className = "" }) {
  const id = nextId("sel");
  const select = h("select", { id, class: "select", onchange: () => onChange(select.value) });

  function setOptions(list, selected) {
    select.replaceChildren();
    if (placeholder) select.append(h("option", { value: "" }, placeholder));
    const groups = new Map();
    for (const option of list) {
      const node = h("option", { value: option.value, disabled: option.disabled || null }, option.label);
      if (option.group) {
        if (!groups.has(option.group)) {
          const group = h("optgroup", { label: option.group });
          groups.set(option.group, group);
          select.append(group);
        }
        groups.get(option.group).append(node);
      } else {
        select.append(node);
      }
    }
    select.value = selected ?? "";
  }

  setOptions(options, value);
  const element = h(
    "div",
    { class: `field ${className}`.trim() },
    h("label", { class: "field-label", for: id }, label),
    h("div", { class: "select-wrap" }, select),
  );
  return {
    el: element,
    select,
    set: (v) => {
      select.value = v ?? "";
    },
    setOptions,
  };
}

/** One-of-many as a row of radio buttons drawn like a segmented control. */
export function segmented({ label, options, value, onChange, className = "" }) {
  const name = nextId("seg");
  const inputs = new Map();
  const items = options.map((option) => {
    const id = nextId("opt");
    const input = h("input", {
      type: "radio",
      name,
      id,
      value: option.value,
      class: "sr-only",
      onchange: () => {
        if (input.checked) onChange(option.value);
      },
    });
    input.checked = option.value === value;
    inputs.set(option.value, input);
    return h("span", { class: "seg-item" }, input, h("label", { for: id, title: option.title || null }, option.label));
  });
  const element = h(
    "fieldset",
    { class: `field seg ${className}`.trim() },
    h("legend", { class: "field-label" }, label),
    h("div", { class: "seg-row" }, items),
  );
  return {
    el: element,
    set: (v) => {
      for (const [key, input] of inputs) input.checked = key === v;
    },
  };
}

/** A checkbox drawn as a small square box with its label. */
export function toggle({ label, checked, onChange, title }) {
  const id = nextId("tog");
  const input = h("input", { type: "checkbox", id, class: "sr-only", onchange: () => onChange(input.checked) });
  input.checked = Boolean(checked);
  const element = h(
    "span",
    { class: "toggle", title: title || null },
    input,
    h("label", { for: id }, h("span", { class: "toggle-box", "aria-hidden": "true" }), label),
  );
  return {
    el: element,
    set: (v) => {
      input.checked = Boolean(v);
    },
    /** Disabled with a reason shown as the tooltip (the title of the control). */
    setDisabled(disabled, reason) {
      input.disabled = Boolean(disabled);
      element.classList.toggle("is-disabled", Boolean(disabled));
      element.title = disabled ? reason : title || "";
    },
  };
}

export function fuelSelect(model, value, onChange) {
  return selectControl({
    label: t("ctl.fuel"),
    options: model.products.map((p) => ({ value: p.id, label: productLabel(p) })),
    value,
    onChange,
    className: "field-fuel",
  });
}

/**
 * Week scrubber: previous / slider / next / latest. `hasData(i)` tells which
 * weeks have something to show, so the step buttons skip the survey gaps.
 */
export function weekScrubber({ model, getIndex, setIndex, hasData }) {
  const id = nextId("week");
  const output = h("output", { class: "week-out", for: id });
  const range = h("input", {
    type: "range",
    id,
    class: "range",
    min: 0,
    max: model.latest,
    step: 1,
    oninput: () => setIndex(Number(range.value)),
  });

  function step(direction) {
    let i = getIndex() + direction;
    while (i >= 0 && i <= model.latest && !hasData(i)) i += direction;
    if (i >= 0 && i <= model.latest) setIndex(i);
  }
  // aria-disabled rather than disabled: a button that becomes disabled while it
  // has focus drops the focus to <body>.
  const guarded = (fn) => (event) => {
    if (event.currentTarget.getAttribute("aria-disabled") === "true") return;
    fn();
  };

  const previous = h(
    "button",
    { type: "button", class: "iconbtn", "aria-label": t("ctl.week.prev"), title: t("ctl.week.prev"), onclick: guarded(() => step(-1)) },
    "‹",
  );
  const next = h(
    "button",
    { type: "button", class: "iconbtn", "aria-label": t("ctl.week.next"), title: t("ctl.week.next"), onclick: guarded(() => step(1)) },
    "›",
  );
  const latest = h("button", { type: "button", class: "textbtn", onclick: guarded(() => setIndex(model.latest)) }, t("ctl.week.latest"));

  const element = h(
    "div",
    { class: "field week" },
    h("label", { class: "field-label", for: id }, t("ctl.week")),
    h("div", { class: "week-row" }, previous, range, next, latest),
    output,
  );

  function sync() {
    const i = getIndex();
    range.value = String(i);
    const text = fmt.week(model.dates[i]);
    output.textContent = i === model.latest ? `${text} · ${t("ctl.week.isLatest")}` : text;
    range.setAttribute("aria-valuetext", text);
    let before = i - 1;
    while (before >= 0 && !hasData(before)) before--;
    let after = i + 1;
    while (after <= model.latest && !hasData(after)) after++;
    previous.setAttribute("aria-disabled", String(before < 0));
    next.setAttribute("aria-disabled", String(after > model.latest));
    latest.setAttribute("aria-disabled", String(i === model.latest));
  }

  sync();
  return { el: element, sync };
}

/**
 * The period as two date fields ("from … to …"), the keyboard path to what the
 * brush selects. Dates snap to the survey week they fall in.
 */
export function dateRange({ model, onChange }) {
  const iso = (i) => model.weeks[i];
  const max = model.weeks[model.latest];
  const field = (label) => {
    const id = nextId("date");
    const input = h("input", { type: "date", id, class: "date", min: model.weeks[0], max, required: true });
    return { input, el: h("span", { class: "date-field" }, h("label", { for: id }, label), input) };
  };
  const from = field(t("ctl.range.from"));
  const to = field(t("ctl.range.to"));
  const toIndex = (value) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    let found = 0;
    for (let i = 0; i <= model.latest; i++) if (model.weeks[i] <= value) found = i;
    return found;
  };
  const commit = () => {
    const a = toIndex(from.input.value);
    const b = toIndex(to.input.value);
    if (a == null || b == null) return;
    onChange(Math.min(a, b), Math.max(a, b));
  };
  from.input.addEventListener("change", commit);
  to.input.addEventListener("change", commit);
  return {
    el: h("span", { class: "date-range", role: "group", "aria-label": t("ctl.period") }, from.el, to.el),
    set(i0, i1) {
      from.input.value = iso(i0);
      to.input.value = iso(i1);
    },
  };
}

/**
 * One tab stop for a list of buttons: Tab enters the list on the selected (or
 * first) item, the arrow keys move between items. Call again after the list
 * is redrawn.
 */
export function rovingFocus(container, selector) {
  const items = [...container.querySelectorAll(selector)];
  if (!items.length) return;
  const start = items.find((item) => item.getAttribute("aria-pressed") === "true") ?? items[0];
  for (const item of items) item.tabIndex = item === start ? 0 : -1;
  container.onkeydown = (event) => {
    const current = items.indexOf(document.activeElement);
    if (current < 0) return;
    const moves = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 };
    let target = null;
    if (event.key in moves) target = current + moves[event.key];
    else if (event.key === "Home") target = 0;
    else if (event.key === "End") target = items.length - 1;
    if (target == null || target < 0 || target >= items.length) return;
    event.preventDefault();
    items[current].tabIndex = -1;
    items[target].tabIndex = 0;
    items[target].focus();
  };
}

/** True when a national series has a value in week i (used by the scrubbers). */
export function nationalHasData(model, fuel) {
  const values = model.national[fuel]?.mean;
  const first = firstValid(values);
  const last = lastValid(values);
  return (i) => Boolean(values) && i >= first && i <= last && values[i] != null;
}
