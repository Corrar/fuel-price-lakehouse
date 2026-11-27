-- usd_brl_daily: the US dollar in Brazilian reais, one row per day with a rate (public).
--
-- SGS series 1 is the selling rate. Banco Central publishes it on business days
-- only, and the gaps are kept: a missing day is not filled with the day before.
CREATE TABLE usd_brl_daily AS
SELECT
    observed_on,
    value AS usd_brl
FROM staged_sgs
WHERE series_code = 1;
