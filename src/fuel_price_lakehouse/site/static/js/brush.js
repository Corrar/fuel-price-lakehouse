// The period strip under the explorer: the whole history of the chosen fuel in
// miniature, with a D3 brush that selects the period shown above it. The
// explorer pairs it with two date fields, the keyboard path to the same choice.

import { d3 } from "./lib.js";

/**
 * The strip under the explorer: the whole history in miniature with a brush
 * that selects the period. `onPreview` fires while dragging, `onChange` once
 * the gesture ends (null means "everything").
 */
export function brushStrip(host, { dates, values, i0, i1, last, onPreview, onChange }) {
  const width = Math.max(260, Math.floor(host.clientWidth || 640));
  const height = 52;
  const band = height - 16;
  const x = d3.scaleUtc().domain([dates[0], dates[last]]).range([1, width - 1]);
  const valid = values.filter((v) => v != null);
  const svg = d3.create("svg").attr("class", "brush-strip").attr("width", width).attr("height", height).attr("viewBox", `0 0 ${width} ${height}`);
  if (valid.length > 1) {
    const y = d3.scaleLinear().domain(d3.extent(valid)).range([band - 2, 4]);
    const area = d3.area().defined((v) => v != null).x((_, i) => x(dates[i])).y0(band).y1((v) => y(v));
    svg.append("path").attr("class", "brush-area").attr("d", area(values.slice(0, last + 1)));
  }
  svg.append("line").attr("class", "brush-base").attr("x1", 0).attr("x2", width).attr("y1", band).attr("y2", band);
  const years = d3.utcYear.range(d3.utcYear.ceil(dates[0]), dates[last]);
  const step = Math.ceil(years.length / Math.max(1, Math.floor(width / 56)));
  svg
    .append("g")
    .attr("class", "brush-years")
    .selectAll("text")
    .data(years.filter((_, k) => k % step === 0))
    .join("text")
    .attr("x", (d) => x(d) + 3)
    .attr("y", height - 3)
    .text((d) => d.getUTCFullYear());
  svg
    .append("g")
    .selectAll("line")
    .data(years)
    .join("line")
    .attr("class", "brush-tick")
    .attr("x1", (d) => x(d))
    .attr("x2", (d) => x(d))
    .attr("y1", band)
    .attr("y2", band + 4);

  const toIndices = ([a, b]) => {
    let from = d3.bisectCenter(dates, x.invert(a), 0, last + 1);
    let to = d3.bisectCenter(dates, x.invert(b), 0, last + 1);
    if (to - from < 8) {
      to = Math.min(last, from + 8);
      from = Math.max(0, to - 8);
    }
    return [from, to];
  };
  const brush = d3
    .brushX()
    .extent([
      [0, 0],
      [width, band],
    ])
    .on("brush", (event) => {
      if (event.sourceEvent && event.selection) onPreview(toIndices(event.selection));
    })
    .on("end", (event) => {
      if (!event.sourceEvent) return;
      onChange(event.selection ? toIndices(event.selection) : null);
    });
  const group = svg.append("g").attr("class", "brush").call(brush);
  if (!(i0 === 0 && i1 === last)) group.call(brush.move, [x(dates[i0]), x(dates[i1])]);
  host.replaceChildren(svg.node());
}
