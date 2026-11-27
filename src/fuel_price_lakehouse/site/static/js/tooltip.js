// The one shared tooltip (#tooltip, role=status) and the hover / focus wiring
// of single marks. A tooltip never holds a value that is not also in the
// table view of its chart. Only one chart owns the crosshair at a time; a tap
// outside it, Escape or scrolling dismisses it.

import { h } from "./dom.js";

let tipOwner = null;
let activeChart = null;

function tipElement() {
  return document.getElementById("tooltip");
}

/** Show the tooltip near a viewport point. `owner` is who shows it. */
export function showTip(content, clientX, clientY, owner) {
  const element = tipElement();
  if (!element) return;
  element.replaceChildren(content);
  element.hidden = false;
  tipOwner = owner ?? null;
  const box = element.getBoundingClientRect();
  const gap = 14;
  let x = clientX + gap;
  if (x + box.width > window.innerWidth - 8) x = clientX - gap - box.width;
  x = Math.max(8, Math.min(x, window.innerWidth - box.width - 8));
  let y = clientY - box.height - gap;
  if (y < 8) y = clientY + gap + 4;
  y = Math.max(8, Math.min(y, window.innerHeight - box.height - 8));
  element.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
}

export function hideTip(owner) {
  if (owner && tipOwner && owner !== tipOwner) return;
  const element = tipElement();
  if (element) element.hidden = true;
  tipOwner = null;
}

/** Tooltip body: a title, rows of "key label ...... value", an optional note. */
export function tipContent(title, rows, note) {
  const fragment = document.createDocumentFragment();
  fragment.append(h("div", { class: "tip-title" }, title));
  for (const row of rows) {
    if (!row) continue;
    const key = row.color
      ? h("span", { class: `tip-key${row.dash ? " is-dashed" : ""}`, style: { "--key": row.color } })
      : h("span", { class: "tip-key is-empty" });
    fragment.append(h("div", { class: "tip-row" }, key, h("span", { class: "tip-label" }, row.label), h("strong", { class: "tip-value" }, row.value)));
    if (row.sub) fragment.append(h("div", { class: "tip-sub" }, row.sub));
  }
  if (note) fragment.append(h("div", { class: "tip-note" }, note));
  return fragment;
}

/**
 * Attach the hover / focus tooltip of a single mark (tile, bar, dot).
 * Pass `keyboard: false` for marks that must stay out of the tab order: in
 * Chromium an SVG element becomes focusable as soon as it listens to focus.
 */
export function markTip(element, build, { keyboard = true } = {}) {
  const show = (event) => {
    const box = element.getBoundingClientRect();
    const x = event?.clientX ?? box.left + box.width / 2;
    const y = event?.clientY ?? box.top;
    showTip(build(), x, y, element);
  };
  element.addEventListener("pointerenter", show);
  element.addEventListener("pointermove", show);
  element.addEventListener("pointerleave", () => hideTip(element));
  if (!keyboard) return;
  element.addEventListener("focus", () => {
    if (element.matches(":focus-visible")) show();
  });
  element.addEventListener("blur", () => hideTip(element));
}

/** A chart takes the crosshair; the previous owner, if any, clears its own. */
export function claimCrosshair(host, clear) {
  if (activeChart && activeChart.host !== host) activeChart.clear();
  activeChart = { host, clear };
}

export function releaseCrosshair(host) {
  if (activeChart?.host === host) activeChart = null;
}

document.addEventListener("pointerdown", (event) => {
  if (activeChart && !activeChart.host.contains(event.target)) activeChart.clear();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (activeChart) activeChart.clear();
    hideTip();
  }
});
window.addEventListener("scroll", () => hideTip(), { passive: true });
