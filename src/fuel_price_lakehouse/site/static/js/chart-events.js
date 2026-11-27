// Numbered event markers over a time chart: HTML buttons placed on the x scale,
// clustered when they would overlap ("5–7"), each opening a small card with the
// date, title, detail and source of the events it stands for. One card can be
// open at a time; Escape or a press outside closes it.

import { h, safeUrl } from "./dom.js";
import { t } from "./i18n.js";
import { markTip, tipContent, hideTip } from "./tooltip.js";

// One event card can be open at a time, anywhere on the page; a press outside
// it closes it. Kept at module level so that redrawing a chart does not leave
// a listener behind.
let openCard = null;

function closeCard(returnFocus) {
  if (!openCard) return;
  openCard.pop.remove();
  openCard.button.setAttribute("aria-expanded", "false");
  if (returnFocus && openCard.button.isConnected) openCard.button.focus();
  openCard = null;
}

document.addEventListener("pointerdown", (event) => {
  if (openCard && !openCard.pop.contains(event.target) && !openCard.button.contains(event.target)) closeCard(false);
});

export function addEventMarkers(host, events, X, { width, marginTop }) {
  // Events closer than a marker's width share one marker ("4–6").
  const clusters = [];
  for (const event of events) {
    const x = X.apply(event.when);
    const last = clusters[clusters.length - 1];
    if (last && x - last.end < 22) {
      last.items.push(event);
      last.end = x;
    } else {
      clusters.push({ start: x, end: x, items: [event] });
    }
  }
  const layer = h("div", { class: "evt-layer" });
  closeCard(false);

  for (const cluster of clusters) {
    const first = cluster.items[0];
    const lastItem = cluster.items[cluster.items.length - 1];
    const text = cluster.items.length > 1 ? `${first.n}–${lastItem.n}` : String(first.n);
    const centre = (cluster.start + cluster.end) / 2;
    const name = cluster.items.map((e) => `${e.dateText}: ${e.title}`).join("; ");
    const button = h(
      "button",
      {
        type: "button",
        class: "evt",
        style: { left: `${centre}px` },
        "aria-expanded": "false",
        "aria-label": `${t("evt.marker")} ${text}. ${name}`,
      },
      text,
    );
    markTip(button, () =>
      tipContent(
        cluster.items.length > 1 ? t("evt.count", { n: cluster.items.length }) : first.dateText,
        cluster.items.map((e) => ({ label: cluster.items.length > 1 ? `${e.n}. ${e.dateText}` : e.title, value: cluster.items.length > 1 ? e.title : "" })),
        t("evt.hint"),
      ),
    );
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      hideTip();
      const wasOpen = openCard?.button === button;
      closeCard(false);
      if (wasOpen) return;
      const pop = h(
        "div",
        { class: "evt-pop", role: "dialog", "aria-label": t("evt.details"), tabindex: -1 },
        h("button", { type: "button", class: "evt-close", "aria-label": t("ui.close"), onclick: () => closeCard(true) }, "×"),
        cluster.items.map((e) =>
          h(
            "article",
            { class: "evt-item" },
            h("p", { class: "evt-date" }, h("span", { class: "evt-n" }, String(e.n)), e.dateText),
            h("h4", null, e.title),
            h("p", null, e.detail),
            safeUrl(e.url) ? h("a", { href: safeUrl(e.url), rel: "noopener noreferrer", target: "_blank" }, t("evt.source")) : null,
          ),
        ),
      );
      const popWidth = Math.min(340, width - 8);
      pop.style.width = `${popWidth}px`;
      pop.style.left = `${Math.max(0, Math.min(centre - popWidth / 2, width - popWidth))}px`;
      pop.style.top = `${marginTop - 4}px`;
      pop.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") {
          ev.stopPropagation();
          closeCard(true);
        }
      });
      host.append(pop);
      button.setAttribute("aria-expanded", "true");
      openCard = { pop, button };
      pop.focus({ preventScroll: true });
    });
    layer.append(button);
  }
  host.append(layer);
}
