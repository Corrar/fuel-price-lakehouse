-- weekly_cell: weekly price statistics at the four published levels, BEFORE
-- small-cell suppression. Never exported: the agg_price_weekly_* tables are the
-- cells of this one that have enough stations.
--
-- A station counts once per week and product. Surveyed twice in the same week,
-- it enters with the mean of its prices, so every statistic is a simple
-- statistic over stations, not weighted by sales volume (ANP's official averages
-- are weighted, and cannot be reproduced from the open data). Quantiles are
-- continuous: interpolated linearly between the two nearest stations, the
-- method NumPy and pandas use by default (R type 7).
--
-- Station prices are added up as decimals with 6 places. A sum of
-- floating-point numbers depends on the order the threads add them in, so a
-- mean close to a rounding boundary could be published as 5.1234 by one build
-- and as 5.1235 by the next; a sum of decimals is exact. (The 6 places hold
-- the mean of one or two prices exactly; a mean of three or more prices may be
-- rounded to them.)
--
-- Weeks before first_full_week are left out: the files do not cover all of
-- their days.
CREATE MACRO weekly_price_stats(cell_of_row) AS TABLE
WITH station_week AS (
    SELECT
        survey_week,
        cell_of_row AS cell,
        product,
        unit,
        station_cnpj,
        count(*) AS n_observations,
        avg(CAST(sale_price AS DECIMAL(18, 6))) AS price
    FROM fuel_price
    WHERE survey_week >= getvariable('first_full_week')
    GROUP BY survey_week, cell_of_row, product, unit, station_cnpj
)

SELECT
    survey_week,
    cell,
    product,
    unit,
    count(*) AS n_stations,
    CAST(sum(n_observations) AS BIGINT) AS n_observations,
    round_published(avg(CAST(price AS DECIMAL(18, 6)))) AS price_mean,
    round_published(quantile_cont(price, 0.5)) AS price_median,
    round_published(quantile_cont(price, 0.1)) AS price_p10,
    round_published(quantile_cont(price, 0.9)) AS price_p90
FROM station_week
GROUP BY survey_week, cell, product, unit;

-- One statement per level: each one groups every observation by station, and
-- run together (UNION ALL) they would hold four such groupings in memory at once.
CREATE TABLE weekly_cell AS
SELECT 'national' AS level, * FROM weekly_price_stats('BR');

INSERT INTO weekly_cell
SELECT 'region' AS level, * FROM weekly_price_stats(region_code);

INSERT INTO weekly_cell
SELECT 'state' AS level, * FROM weekly_price_stats(state_code);

-- What "Bandeira" means on LPG rows is not confirmed, so the flag level covers
-- the six automotive fuels only.
INSERT INTO weekly_cell
SELECT 'flag' AS level, *
FROM weekly_price_stats(CASE WHEN brand = 'BRANCA' THEN 'white_flag' ELSE 'branded' END)
WHERE product <> 'GLP';
