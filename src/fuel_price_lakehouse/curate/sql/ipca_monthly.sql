-- ipca_monthly: consumer price inflation (IPCA), one row per month (public).
--
-- SGS series 433 is the percentage change within each month. The changes are
-- chained into an index worth 100 in the month before the first one, and
-- deflator_to_latest turns a price of any month into reais of the latest month
-- with a published IPCA: price x (index of the latest month / index of the month).
CREATE TABLE ipca_monthly AS
WITH chained AS (
    SELECT
        CAST(date_trunc('month', observed_on) AS DATE) AS month,
        value AS pct_change,
        100 * product(1 + value / 100) OVER (ORDER BY observed_on) AS index
    FROM staged_sgs
    WHERE series_code = 433
)
SELECT
    month,
    pct_change,
    index,
    arg_max(index, month) OVER () / index AS deflator_to_latest
FROM chained;
