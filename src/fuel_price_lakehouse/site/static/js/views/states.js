// States: the 27 states for one week, as a tile-grid map coloured by distance
// from the national average and as a ranking, then the trajectory of every
// state over the whole history with Brazil and the selected state picked out.
// The week can be scrubbed or stepped; weeks without a survey are skipped by
// the step buttons and shown as an explicit empty state, never as zeros.
// Hovering a tile marks its row in the ranking (and the other way round);
// selecting a state offers to compare it with Brazil in the explorer.

import { d3 } from "../lib.js";
import { h } from "../dom.js";
import { t, fmt, productLabel, unitLabel } from "../i18n.js";
import { fileOf } from "../data.js";
import { stateRows, BRAZIL } from "../series.js";
import { timeChart } from "../charts.js";
import { figure, legend } from "../figure.js";
import { tipContent, markTip, hideTip } from "../tooltip.js";
import { tokens } from "../theme.js";
import { state, setState, weekIndex, setWeekIndex, addPlace, linkTo } from "../state.js";
import { fuelSelect, weekScrubber, nationalHasData, rovingFocus } from "../controls.js";
import { sectionHead, credit, missingFile, viewLink } from "../ui.js";

// Row and column of each state on a 6-column grid that keeps neighbours near
// each other. A state missing from this table is appended after the last row.
const TILE_GRID = {
  RR: [0, 1], AP: [0, 2],
  AM: [1, 0], PA: [1, 1], MA: [1, 2], CE: [1, 3], RN: [1, 4],
  AC: [2, 0], RO: [2, 1], TO: [2, 2], PI: [2, 3], PE: [2, 4], PB: [2, 5],
  MT: [3, 1], GO: [3, 2], BA: [3, 3], SE: [3, 4], AL: [3, 5],
  MS: [4, 1], DF: [4, 2], MG: [4, 3], ES: [4, 4],
  SP: [5, 2], RJ: [5, 3],
  PR: [6, 2],
  SC: [7, 2],
  RS: [8, 2],
};
const GRID_COLUMNS = 6;

// Deviation from the national average, in classes that stay the same from
// week to week so that scrubbing compares like with like.
const BIN_EDGES = [-0.1, -0.05, -0.02, 0.02, 0.05, 0.1];
const BIN_NAMES = ["n3", "n2", "n1", "0", "p1", "p2", "p3"];

function binOf(deviation) {
  let k = 0;
  while (k < BIN_EDGES.length && deviation >= BIN_EDGES[k]) k++;
  return BIN_NAMES[k];
}

/** Positions of the tiles, including states the layout table does not know. */
function tilePositions(states) {
  const positions = new Map();
  let extra = 0;
  const lastRow = Math.max(...Object.values(TILE_GRID).map(([row]) => row));
  for (const st of states) {
    if (TILE_GRID[st.code]) positions.set(st.code, TILE_GRID[st.code]);
    else {
      positions.set(st.code, [lastRow + 1 + Math.floor(extra / GRID_COLUMNS), extra % GRID_COLUMNS]);
      extra++;
    }
  }
  return positions;
}

/** Largest deviation worth a full bar: the 98th percentile over all weeks. */
function deviationScale(model, fuel) {
  const national = model.national[fuel]?.mean ?? [];
  const all = [];
  for (const measures of Object.values(model.byState[fuel] ?? {})) {
    measures.mean.forEach((v, i) => {
      if (v != null && national[i] != null) all.push(Math.abs(v / national[i] - 1));
    });
  }
  if (!all.length) return 0.1;
  all.sort((a, b) => a - b);
  return Math.max(0.05, all[Math.floor(all.length * 0.98)]);
}

/** Arrow keys move between tiles by position on the grid; one tab stop. */
function gridFocus(container, positions) {
  const tiles = [...container.querySelectorAll(".tile")];
  const at = (tile) => positions.get(tile.dataset.uf);
  const start = tiles.find((tile) => tile.getAttribute("aria-pressed") === "true") ?? tiles.find((tile) => tile.dataset.uf === "SP") ?? tiles[0];
  for (const tile of tiles) tile.tabIndex = tile === start ? 0 : -1;
  container.onkeydown = (event) => {
    const current = tiles.indexOf(document.activeElement);
    if (current < 0) return;
    const [row, col] = at(tiles[current]);
    const dirs = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0] };
    if (!(event.key in dirs)) return;
    event.preventDefault();
    const [dr, dc] = dirs[event.key];
    // the nearest tile in that direction, preferring the same row or column
    let best = null;
    let bestScore = Infinity;
    for (const tile of tiles) {
      const [r, c] = at(tile);
      const along = dr ? (r - row) * dr : (c - col) * dc;
      if (along <= 0) continue;
      const across = dr ? Math.abs(c - col) : Math.abs(r - row);
      const score = along + across * 2.5;
      if (score < bestScore) {
        best = tile;
        bestScore = score;
      }
    }
    if (!best) return;
    tiles[current].tabIndex = -1;
    best.tabIndex = 0;
    best.focus();
  };
}

export function mountStates(root, { model, goTo }) {
  const head = sectionHead({ number: "03", kicker: t("st.kicker"), title: t("st.title") });
  if (!model.has.states) {
    root.replaceChildren(h("div", { class: "wrap" }, head.el, missingFile(fileOf("states"))));
    return () => {};
  }
  const fuel = fuelSelect(model, state.fuel, (value) => setState({ fuel: value }));
  const week = weekScrubber({ model, getIndex: weekIndex, setIndex: setWeekIndex, hasData: (i) => nationalHasData(model, state.fuel)(i) });

  const mapFig = figure({ source: credit("anp") });
  const rankFig = figure({ source: credit("anp") });
  const trendFig = figure({ source: credit("anp"), className: "fig-wide" });
  const tiles = h("div", { class: "tiles", role: "group" });
  const scale = h("div", { class: "tile-legend" });
  const action = h("p", { class: "tile-action", "aria-live": "polite" });
  mapFig.body.append(tiles, scale, action);
  const rank = h("ol", { class: "rank", "aria-label": t("st.rank.title") });
  const rankNote = h("p", { class: "fig-notes" });
  rankFig.body.append(rank, rankNote);
  const trendKeys = h("div");
  const trendHost = h("div");
  trendFig.body.append(trendKeys, trendHost);

  root.replaceChildren(
    h(
      "div",
      { class: "wrap" },
      head.el,
      h("div", { class: "controls" }, fuel.el, week.el),
      h("div", { class: "grid-map" }, mapFig.el, rankFig.el),
      trendFig.el,
    ),
  );

  const positions = tilePositions(model.states);
  const scaleCache = new Map();

  function legendScale() {
    // class boundaries, written from the same numbers that bin the tiles
    const edges = BIN_EDGES.map((edge) => fmt.pct(edge, 0, true));
    scale.replaceChildren(
      h(
        "div",
        { class: "tile-legend-scale" },
        h("div", { class: "tile-legend-ends" }, h("span", null, `← ${t("st.legend.below")}`), h("span", null, `${t("st.legend.above")} →`)),
        h("div", { class: "tile-legend-bar", "aria-hidden": "true" }, BIN_NAMES.map((name, k) => h("span", { class: `swatch div-${name}` }, k < edges.length ? h("i", null, edges[k]) : null))),
      ),
      h("div", { class: "tile-legend-none" }, h("span", { class: "swatch div-none" }), t("st.legend.none")),
    );
  }

  /** A tile and its ranking row light up together. */
  function pair(uf, on) {
    for (const el of root.querySelectorAll(`[data-uf="${uf}"]`)) el.classList.toggle("is-hover", on);
  }

  function drawAction() {
    const st = state.uf ? model.stateByCode.get(state.uf) : null;
    if (!st || !model.byState[state.fuel]?.[st.code]) {
      action.replaceChildren(t("st.action.hint"));
      return;
    }
    const patch = { places: [BRAZIL, st.code] };
    action.replaceChildren(
      h("strong", null, st.name),
      ` ${t("st.action.selected")} · `,
      viewLink(linkTo("explorer", patch), t("st.action.compare", { name: st.name }), () => {
        if (!state.places.includes(st.code)) addPlace(st.code);
        goTo("explorer");
      }),
    );
  }

  function draw() {
    // Redrawing replaces the buttons: remember which one had focus.
    const active = document.activeElement;
    const keep = root.contains(active) && active.dataset.uf ? [active.dataset.kind, active.dataset.uf] : null;
    hideTip();
    const product = model.productById.get(state.fuel);
    const i = weekIndex();
    const national = model.national[state.fuel]?.mean?.[i] ?? null;
    const rows = stateRows(model, state.fuel, i);
    const withData = rows.filter((r) => r.v != null).sort((a, b) => b.v - a.v);
    const without = rows.filter((r) => r.v == null);
    const weekText = fmt.week(model.dates[i]);
    const unit = unitLabel(product.unit);

    if (withData.length >= 2) {
      const high = withData[0];
      const low = withData[withData.length - 1];
      head.setLede(
        t("st.lede", {
          fuel: productLabel(product),
          week: weekText,
          low: fmt.price(low.v, product.unit),
          lowName: low.st.name,
          high: fmt.price(high.v, product.unit),
          highName: high.st.name,
          diff: fmt.price(high.v - low.v, product.unit),
        }),
      );
    } else {
      head.setLede(t("st.lede.none", { fuel: productLabel(product), week: weekText }));
    }

    const tipFor = (row) => () =>
      row.v == null
        ? tipContent(row.st.name, [], t("st.tip.none"))
        : tipContent(
            row.st.name,
            [
              { label: t("lbl.mean"), value: fmt.price(row.v, product.unit, true) },
              row.deviation != null ? { label: t("lbl.vsNational"), value: `${fmt.pct(row.deviation, 1, true)} (${fmt.signed(row.v - national, product.unit === "BRL/13kg" ? 2 : 3)})` } : null,
              row.p10 != null && row.p90 != null ? { label: "p10–p90", value: `${fmt.num(row.p10, 2)}–${fmt.num(row.p90, 2)}` } : null,
              row.n != null ? { label: t("lbl.stations"), value: fmt.int(row.n) } : null,
            ],
            t("tip.week", { week: weekText }),
          );
    const select = (code) => setState({ uf: state.uf === code ? null : code });
    const hoverPair = (element, code) => {
      element.addEventListener("pointerenter", () => pair(code, true));
      element.addEventListener("pointerleave", () => pair(code, false));
      element.addEventListener("focus", () => pair(code, true));
      element.addEventListener("blur", () => pair(code, false));
    };

    // ---- tile map
    mapFig.setTitle(t("st.map.title"));
    mapFig.setTake(national != null ? t("st.map.take", { national: fmt.price(national, product.unit), week: weekText }) : t("st.none.week"));
    mapFig.setUnit(`${unit} · ${t("st.map.unit")}`);
    tiles.setAttribute("aria-label", `${t("st.map.title")}. ${t("st.map.keys")}`);
    tiles.style.setProperty("--rows", Math.max(...[...positions.values()].map(([row]) => row)) + 1);
    tiles.replaceChildren(
      ...rows.map((row) => {
        const [r, c] = positions.get(row.st.code);
        const empty = row.v == null;
        const cls = empty ? "div-none" : row.deviation == null ? "div-0" : `div-${binOf(row.deviation)}`;
        const label = empty
          ? `${row.st.name}: ${t("lbl.nodata")}`
          : `${row.st.name}: ${fmt.price(row.v, product.unit)}${row.deviation != null ? `, ${fmt.pct(row.deviation, 1, true)} ${t("lbl.vsNational")}` : ""}`;
        const tile = h(
          "button",
          {
            type: "button",
            class: `tile ${cls}${state.uf === row.st.code ? " is-selected" : ""}`,
            dataset: { uf: row.st.code, kind: "tile" },
            style: { gridRow: String(r + 1), gridColumn: String(c + 1) },
            "aria-label": label,
            "aria-pressed": String(state.uf === row.st.code),
            onclick: () => select(row.st.code),
          },
          h("span", { class: "tile-code" }, row.st.code),
          h("span", { class: "tile-value" }, empty ? "—" : fmt.num(row.v, 2)),
        );
        markTip(tile, tipFor(row));
        hoverPair(tile, row.st.code);
        return tile;
      }),
    );
    gridFocus(tiles, positions);
    drawAction();

    // ---- ranking with deviation bars
    rankFig.setTitle(t("st.rank.title"));
    rankFig.setTake(t("st.rank.take"));
    rankFig.setUnit(`${unit} · ${t("st.rank.unit")}`);
    if (!scaleCache.has(state.fuel)) scaleCache.set(state.fuel, deviationScale(model, state.fuel));
    const full = scaleCache.get(state.fuel);
    rank.replaceChildren(
      ...withData.map((row, k) => {
        const share = row.deviation == null ? 0 : Math.min(1, Math.abs(row.deviation) / full) * 50;
        const side = row.deviation == null || Math.abs(row.deviation) < 0.0005 ? "zero" : row.deviation > 0 ? "pos" : "neg";
        const button = h(
          "button",
          {
            type: "button",
            class: `rank-row${state.uf === row.st.code ? " is-selected" : ""}`,
            dataset: { uf: row.st.code, kind: "rank" },
            "aria-pressed": String(state.uf === row.st.code),
            onclick: () => select(row.st.code),
          },
          h("span", { class: "rank-n" }, String(k + 1)),
          h("span", { class: "rank-name" }, row.st.name),
          h("span", { class: "rank-value" }, fmt.num(row.v, 2)),
          h("span", { class: "rank-track", "aria-hidden": "true" }, h("span", { class: `rank-bar is-${side}`, style: { width: `${share}%` } })),
          h("span", { class: "rank-dev" }, row.deviation == null ? "—" : fmt.pct(row.deviation, 1, true)),
        );
        markTip(button, tipFor(row));
        hoverPair(button, row.st.code);
        return h("li", null, button);
      }),
    );
    if (!withData.length) rank.append(h("li", { class: "empty" }, t("st.none.week")));
    // A week without an ANP survey has no national value either: say so, and do
    // not blame suppression for it.
    const surveyed = model.national[state.fuel]?.mean?.[i] != null;
    if (!surveyed) rankNote.textContent = t("st.rank.nosurvey");
    else rankNote.textContent = without.length ? t("st.rank.missing", { n: without.length, list: without.map((r) => r.st.code).join(", "), min: model.meta.min_stations_published ?? "—" }) : "";
    rankNote.hidden = !rankNote.textContent;
    rovingFocus(rank, ".rank-row");

    if (keep) {
      const again = root.querySelector(`[data-kind="${keep[0]}"][data-uf="${keep[1]}"]`);
      if (again) {
        const group = keep[0] === "rank" ? rank.querySelectorAll(".rank-row") : tiles.querySelectorAll(".tile");
        for (const el of group) el.tabIndex = el === again ? 0 : -1;
        again.focus({ preventScroll: true });
      }
    }

    rankFig.setTable(() => ({
      columns: [{ label: t("tbl.state") }, { label: unit, num: true }, { label: t("lbl.vsNational"), num: true }, { label: t("lbl.stations"), num: true }],
      rows: [...withData, ...without].map((row) => [row.st.name, fmt.num(row.v, 3), row.deviation == null ? "—" : fmt.pct(row.deviation, 1, true), fmt.int(row.n)]),
    }));
    drawTrend();
  }

  /** Every state as a thin grey line, Brazil and the selected state on top. */
  function drawTrend() {
    const tk = tokens();
    const product = model.productById.get(state.fuel);
    const byState = model.byState[state.fuel] ?? {};
    const national = model.national[state.fuel]?.mean ?? [];
    const codes = model.states.map((st) => st.code).filter((code) => byState[code]);
    const picked = state.uf && byState[state.uf] ? model.stateByCode.get(state.uf) : null;
    const i = weekIndex();
    const series = codes.filter((code) => code !== picked?.code).map((code) => ({ label: code, quiet: true, values: byState[code].mean }));
    series.push({ label: t("place.br"), short: t("place.br"), color: tk.series[0], values: national });
    if (picked) series.push({ label: picked.name, short: picked.code, color: tk.series[1], values: byState[picked.code].mean });

    const extremesAt = (k) => {
      let low = null;
      let high = null;
      for (const code of codes) {
        const v = byState[code].mean[k];
        if (v == null) continue;
        if (!low || v < low.v) low = { code, v };
        if (!high || v > high.v) high = { code, v };
      }
      return { low, high };
    };

    trendFig.setTitle(t("st.trend.title", { fuel: productLabel(product) }));
    const { low, high } = extremesAt(i);
    trendFig.setTake(
      low && high && national[i] != null
        ? t("st.trend.take", { n: codes.length, week: fmt.week(model.dates[i]), spread: fmt.price(high.v - low.v, product.unit), pct: fmt.pct(high.v / low.v - 1, 0) })
        : t("st.none.week"),
    );
    trendFig.setUnit(`${unitLabel(product.unit)} · ${t("st.trend.unit")}`);
    trendKeys.replaceChildren(
      legend([
        { label: t("place.br"), color: tk.series[0] },
        ...(picked ? [{ label: picked.name, color: tk.series[1] }] : []),
        { label: t(picked ? "st.trend.others" : "st.trend.all", { n: codes.length - (picked ? 1 : 0) }), color: tk.quiet, quiet: true },
      ]),
    );
    const first = Math.max(0, d3.min(codes, (code) => byState[code].mean.findIndex((v) => v != null)) ?? 0);
    timeChart(trendHost, {
      dates: model.dates,
      i0: first,
      i1: model.latest,
      series,
      height: window.innerWidth < 640 ? 300 : 360,
      endLabels: "name",
      marker: i,
      yFormat: (v) => fmt.num(v, product.unit === "BRL/13kg" ? 0 : 2),
      label: trendFig.title(),
      tip: (k) => {
        const ext = extremesAt(k);
        const name = (code) => model.stateByCode.get(code)?.name ?? code;
        return tipContent(
          t("tip.week", { week: fmt.week(model.dates[k]) }),
          [
            { color: tk.series[0], label: t("place.br"), value: fmt.price(national[k], product.unit, true) },
            picked ? { color: tk.series[1], label: picked.name, value: fmt.price(byState[picked.code].mean[k], product.unit, true) } : null,
            ext.high ? { label: t("st.trend.high", { name: name(ext.high.code) }), value: fmt.price(ext.high.v, product.unit, true) } : null,
            ext.low ? { label: t("st.trend.low", { name: name(ext.low.code) }), value: fmt.price(ext.low.v, product.unit, true) } : null,
          ],
          national[k] == null ? t("tip.gap") : null,
        );
      },
    });
    trendFig.setTable(() => ({
      columns: [{ label: t("tbl.week") }, { label: t("place.br"), num: true }, ...(picked ? [{ label: picked.name, num: true }] : []), { label: t("st.trend.col.high"), num: true }, { label: t("st.trend.col.low"), num: true }],
      rows: d3.range(model.latest, first - 1, -1).map((k) => {
        const ext = extremesAt(k);
        return [
          fmt.dateIso(model.dates[k]),
          fmt.num(national[k], 3),
          ...(picked ? [fmt.num(byState[picked.code].mean[k], 3)] : []),
          ext.high ? `${ext.high.code} ${fmt.num(ext.high.v, 3)}` : "—",
          ext.low ? `${ext.low.code} ${fmt.num(ext.low.v, 3)}` : "—",
        ];
      }),
    }));
  }

  legendScale();
  draw();
  return (changed) => {
    if (changed.has("fuel") || changed.has("week") || changed.has("uf")) {
      fuel.set(state.fuel);
      week.sync();
      draw();
    } else if (changed.has("theme") || changed.has("resize")) {
      drawTrend();
    } else if (changed.has("places")) {
      drawAction();
    }
  };
}

