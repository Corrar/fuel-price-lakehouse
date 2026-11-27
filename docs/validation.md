# Validation against ANP's own figures

Tests show that the code does what it was written to do. They cannot show that
the numbers are right. For that, the curated table was compared with figures
ANP publishes itself.

## What was compared

ANP's weekly summary for the survey week of **15 to 21 November 2026**
([`resumo_semanal_lpc_2026_11_15_2026-11-21.xlsx`](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/arquivos-lpc/2026/resumo_semanal_lpc_2026_11_15_2026-11-21.xlsx),
downloaded on 2026-11-26) gives, for every municipality and product, the
number of stations surveyed and the mean, minimum and maximum price, and the
same for each state and for Brazil.

From the curated `fuel_price` table, the same figures were computed for that
week: per state, municipality and product, the stations with a price in the
week (each counted once, with the mean of its prices that week, as in the
curated aggregates), with exact fractions instead of floating point.

To repeat it, after a run of the pipeline, with the workbook downloaded:

```sh
uv run python tools/validate_against_anp.py resumo_semanal_lpc_2026_11_15_2026-11-21.xlsx
```

[`tools/validate_against_anp.py`](../tools/validate_against_anp.py) reads the
week from the workbook, so any later weekly summary can be checked the same
way; it exits with 1 when fewer than 99 % of the municipal pairs match.

## Municipalities: the means are reproduced

ANP computes the municipal average as a simple mean of the surveyed stations,
so it should be reproducible from the open station-level data. It is:

| Product | Municipality-product pairs matched exactly |
| --- | ---: |
| Diesel S500 | 370 of 370 |
| Diesel S10 | 406 of 406 |
| Hydrous ethanol | 401 of 401 |
| Regular gasoline | 406 of 406 |
| Premium gasoline | 405 of 405 |
| LPG | 402 of 403 |
| CNG | 115 of 115 |
| **All** | **2,505 of 2,506** |

"Exactly" means the same number of stations, the same minimum and maximum, and
the same mean to the cent. How ANP turns a mean into the two decimals it
publishes is not documented; the rule that reproduces every published mean is
*round to three decimals, then truncate to two* (2,505 matches, against 1,456
for ordinary half-up rounding to two decimals).

The one pair that differs is LPG in Gurupi (TO). ANP counts, under Gurupi, a
station that its own station-level file places in the neighbouring
municipality of Aguiarnópolis; with that station moved, ANP's figures
(2 stations, mean 135.00) are reproduced. The pipeline keeps the municipality
the station-level file gives.

## States and Brazil: different on purpose

ANP weights its state and national averages by the volume each distributor
sells, which is not in the open data. This project publishes simple means over
the surveyed stations, so its national figures differ from ANP's bulletins.
The number of stations matches in every row; the means differ by:

| Product | Stations (ANP = ours) | ANP | This project | Difference |
| --- | ---: | ---: | ---: | ---: |
| Hydrous ethanol | 3,915 | 4.29 | 4.472 | +4.24 % |
| Premium gasoline | 3,511 | 6.64 | 6.702 | +0.93 % |
| Regular gasoline | 4,584 | 6.47 | 6.493 | +0.36 % |
| LPG (13 kg) | 3,334 | 115.29 | 116.270 | +0.85 % |
| CNG | 358 | 4.84 | 4.972 | +2.73 % |
| Diesel S500 | 1,895 | 6.98 | 7.049 | +0.99 % |
| Diesel S10 | 3,189 | 7.26 | 7.277 | +0.23 % |

The dashboard states this where it shows national and state averages.

## What else was reconciled

The quality report checks on every run that the rows reconcile from the
source files to the published tables (`row_counts_reconcile`), that no key of
the fact table appears twice (`fuel_price_key_unique`) and that no public table
gains a station column. When the curated layer was first built, these were also
checked once by hand on the full history since 2020, independently of the code
under test:

- Staged ANP rows = curated rows + repeated rows removed (6,965,798 =
  6,945,021 + 20,777); every staged key is in the curated table, none twice.
- For the 20,773 keys published in two files, the row kept is the one the
  precedence rule says, in every case. The 4 keys whose rows disagree on the
  price are the 4 found when profiling the source.
- 300 random cells of each public aggregate table were recomputed with exact
  arithmetic (means, and percentiles by linear interpolation): no difference.
- The cells suppressed are exactly those with fewer than three stations; the
  weeks without a survey have no row.
