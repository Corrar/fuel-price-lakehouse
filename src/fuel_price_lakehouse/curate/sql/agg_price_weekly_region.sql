-- agg_price_weekly_region: weekly price statistics per region and product (public).
--
-- A cell with fewer than min_stations_published distinct stations is left out, so
-- no published number is the price of one or two stations (not a disclosure-control
-- guarantee: see ADR 0002). A week without a survey has no row: gaps stay gaps.
CREATE TABLE agg_price_weekly_region AS
SELECT
    survey_week,
    cell AS region_code,
    product,
    unit,
    n_stations,
    n_observations,
    price_mean,
    price_median,
    price_p10,
    price_p90
FROM weekly_cell
WHERE level = 'region'
    AND n_stations >= getvariable('min_stations_published');
