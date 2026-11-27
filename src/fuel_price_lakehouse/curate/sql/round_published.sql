-- round_published(x): a price or rate rounded to the 4 decimals the aggregate
-- tables publish, halves away from zero.
--
-- Means and quantiles come out of DuckDB as binary floating-point numbers, in
-- which a decimal half such as 3.04425 is stored as 3.044249999... round() on
-- the float then takes some halves down and others up, depending on how each
-- one happens to be stored (650 of 78,433 published means of 2020-2026 went
-- down). Converted first to a decimal with 10 places, the float gives back the
-- decimal it stands for (it is off by about 1e-15), and that decimal is rounded.
CREATE MACRO round_published(x) AS CAST(round(CAST(x AS DECIMAL(18, 10)), 4) AS DOUBLE);
