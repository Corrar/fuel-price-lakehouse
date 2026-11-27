# Vendored third-party files

The dashboard is a zero-build static folder and makes **no request to any
third-party host at runtime**. Everything it needs from other projects is
copied here once, pinned, and served from the same origin as the page.

Downloaded on 2026-11-26 with `curl` from jsDelivr (npm mirror).

## JavaScript

| File | Package | Version | Source URL | Licence |
| --- | --- | --- | --- | --- |
| `d3.min.js` | `d3` (UMD bundle) | 7.9.0 | <https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js> | ISC, see `d3-LICENSE.txt` |
| `plot.umd.min.js` | `@observablehq/plot` (UMD bundle) | 0.6.17 | <https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.17/dist/plot.umd.min.js> | ISC, see `plot-LICENSE.txt` |

SHA-256 of the files as downloaded:

```
f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539  d3.min.js
4358086467740777dd788d6b27a95cebdbaefdd50c730a3060117073bd7134cb  plot.umd.min.js
```

Both are loaded as classic deferred scripts from `index.html` (Plot's UMD build
expects the global `d3`), and re-exported to the ES modules by `js/lib.js`.

## Fonts

Latin subset only (it covers Portuguese), WOFF2, from the Fontsource packages.

| File | Family | Package and version | Source URL | Licence |
| --- | --- | --- | --- | --- |
| `fonts/newsreader-latin-wght-normal.woff2` | Newsreader (variable, weight 200–800) | `@fontsource-variable/newsreader` 5.2.10 | <https://cdn.jsdelivr.net/npm/@fontsource-variable/newsreader@5.2.10/files/newsreader-latin-wght-normal.woff2> | SIL OFL 1.1, see `fonts/Newsreader-LICENSE.txt` |
| `fonts/ibm-plex-sans-latin-wdth-normal.woff2` | IBM Plex Sans (variable, weight 100–700, width 75–100) | `@fontsource-variable/ibm-plex-sans` 5.2.8 | <https://cdn.jsdelivr.net/npm/@fontsource-variable/ibm-plex-sans@5.2.8/files/ibm-plex-sans-latin-wdth-normal.woff2> | SIL OFL 1.1, see `fonts/IBM-Plex-Sans-LICENSE.txt` |
| `fonts/ibm-plex-mono-latin-400-normal.woff2` | IBM Plex Mono 400 | `@fontsource/ibm-plex-mono` 5.2.7 | <https://cdn.jsdelivr.net/npm/@fontsource/ibm-plex-mono@5.2.7/files/ibm-plex-mono-latin-400-normal.woff2> | SIL OFL 1.1, see `fonts/IBM-Plex-Mono-LICENSE.txt` |
| `fonts/ibm-plex-mono-latin-500-normal.woff2` | IBM Plex Mono 500 | same | <https://cdn.jsdelivr.net/npm/@fontsource/ibm-plex-mono@5.2.7/files/ibm-plex-mono-latin-500-normal.woff2> | SIL OFL 1.1 |

## Updating

Change the version in the URL, download again, replace the file, update the
table and the checksum above, and look at every chart in both themes.
