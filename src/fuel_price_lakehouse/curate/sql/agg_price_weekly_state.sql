-- agg_price_weekly_state: weekly price statistics per state and product (public).
--
-- A cell with fewer than min_stations_published distinct stations is left out, so
-- a published number never stands for one or two identifiable businesses. A week
-- without a survey has no row: gaps stay gaps.
CREATE TABLE agg_price_weekly_state AS
SELECT
    survey_week,
    cell AS state_code,
    product,
    unit,
    n_stations,
    n_observations,
    price_mean,
    price_median,
    price_p10,
    price_p90
FROM weekly_cell
WHERE level = 'state'
    AND n_stations >= getvariable('min_stations_published');
