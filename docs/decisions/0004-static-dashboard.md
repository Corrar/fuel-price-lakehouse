# 0004 — A static dashboard on GitHub Pages

- Status: accepted
- Date: 2026-11-27

## Context

The pipeline needs a public face: something a visitor can open without an
account and that shows both what the data says and how much it can be trusted.
Constraints from the start of the project: no paid service, no secret beyond
the token GitHub Actions already provides, nothing station-level published
([ADR 0002](0002-what-is-published.md)).

## Options considered

| Option | Verdict |
| --- | --- |
| Streamlit or Dash app | Rejected: needs a server that stays up. Free hosting sleeps after a few minutes and the first visitor waits for it to wake. |
| BI tool (Looker Studio, Power BI) | Rejected: a third-party account owns the dashboard, and its data has to be pushed there. |
| Evidence, Observable Framework or another static-site generator | Rejected for now: adds a Node.js build to a Python project for what a few modules do. |
| **Plain static site**: HTML, CSS and ES modules, data as JSON files | **Chosen** |

## Decision

- The site builder (`site/`) writes the public aggregates and the quality
  report as JSON files with a fixed shape, validated before anything is
  written ([`site/contract.py`](../../src/fuel_price_lakehouse/site/contract.py)).
  The front-end (`site/static/`) is copied next to them.
- No framework and no build step: the browser loads ES modules directly.
  Charts use Observable Plot and D3, vendored into the repository with their
  licences, so the page makes no request to any other host (no CDN, no web
  fonts, no analytics).
- Everything on the page is derived from the data files: the latest week, the
  products and states present, the rules and checks. Weeks without data are
  gaps, never zeros.
- Portuguese by default, English on demand; light and dark themes; every view
  can be shared with a link that restores its state.
- The quality report is a first-class view, not a footnote: the funnel from
  raw to curated rows, every rule with the rows it caught, every check, every
  source file, and the history of runs.
- GitHub Actions deploys the site only when the quality gate passes, so a bad
  run leaves the previous dashboard in place.

## Consequences

- Hosting is free and has nothing to keep running. The whole site is a few
  megabytes, most of it the state-level series.
- Anyone can rebuild the site locally with `make pipeline` and open
  `build/site/index.html` through any static server.
- Interactivity is limited to what the published aggregates allow: there is no
  query box over station-level data, by design.
