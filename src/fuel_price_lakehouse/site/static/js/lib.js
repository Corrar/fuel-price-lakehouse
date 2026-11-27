// The two vendored libraries are loaded as classic scripts (Plot's UMD build
// needs the global `d3`), so the modules take them from the global object in
// one place. If a script failed to load, the boot code reports it instead of
// every module failing with "undefined is not an object".

export const d3 = globalThis.d3;
export const Plot = globalThis.Plot;

export function assertLibraries() {
  if (!d3 || !Plot) {
    throw new Error("vendor/d3.min.js or vendor/plot.umd.min.js did not load");
  }
}
