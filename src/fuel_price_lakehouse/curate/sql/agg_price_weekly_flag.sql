-- agg_price_weekly_flag: weekly price statistics per product, for unbranded
-- ("white flag", brand BRANCA) and branded stations (public).
--
-- Covers the six automotive fuels only (see weekly_cell). A cell with fewer than
-- min_stations_published distinct stations is left out, so a published number
-- never stands for one or two identifiable businesses.
CREATE TABLE agg_price_weekly_flag AS
SELECT
    survey_week,
    product,
    cell AS flag_type,
    unit,
    n_stations,
    n_observations,
    price_mean,
    price_median,
    price_p10,
    price_p90
FROM weekly_cell
WHERE level = 'flag'
    AND n_stations >= getvariable('min_stations_published');
