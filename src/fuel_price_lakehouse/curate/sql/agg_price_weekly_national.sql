-- agg_price_weekly_national: weekly price statistics per product for the whole
-- country, with the dollar and the inflation adjustment next to them (public).
--
-- A survey week can straddle two months, so its IPCA month is the month of its
-- Wednesday, the middle of the Sunday-to-Saturday week. A month whose IPCA is
-- not published yet has no deflator: its weeks are flagged as provisional and
-- left in nominal reais (deflator 1, the same as the latest published month).
CREATE TABLE agg_price_weekly_national AS
WITH states_surveyed AS (
    SELECT
        survey_week,
        product,
        count(*) AS n_states
    FROM weekly_cell
    WHERE level = 'state'
    GROUP BY survey_week, product
),

usd_brl_weekly AS (
    SELECT
        survey_week(observed_on) AS survey_week,
        avg(CAST(usd_brl AS DECIMAL(18, 6))) AS usd_brl_mean  -- exact, see weekly_cell
    FROM usd_brl_daily
    GROUP BY survey_week(observed_on)
)

SELECT
    cell.survey_week,
    cell.product,
    cell.unit,
    cell.n_stations,
    cell.n_observations,
    cell.price_mean,
    cell.price_median,
    cell.price_p10,
    cell.price_p90,
    states_surveyed.n_states,
    round_published(usd_brl_weekly.usd_brl_mean) AS usd_brl_mean,
    coalesce(ipca.deflator_to_latest, 1.0) AS ipca_deflator,
    ipca.month IS NULL AS ipca_provisional,
    round_published(cell.price_mean * coalesce(ipca.deflator_to_latest, 1.0)) AS price_mean_real
FROM weekly_cell AS cell
INNER JOIN states_surveyed
    ON cell.survey_week = states_surveyed.survey_week
    AND cell.product = states_surveyed.product
LEFT JOIN usd_brl_weekly
    ON cell.survey_week = usd_brl_weekly.survey_week
LEFT JOIN ipca_monthly AS ipca
    ON CAST(date_trunc('month', cell.survey_week + 3) AS DATE) = ipca.month
WHERE cell.level = 'national'
    AND cell.n_stations >= getvariable('min_stations_published');
