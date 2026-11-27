-- fuel_price: one row per station, product and collection date. Station level, never published.
--
-- ANP publishes the same observation in up to three files (semester, monthly,
-- rolling last four weeks), so the staged layer repeats it. The row kept comes
-- from the file that can be downloaded again: the closed semester file before
-- the monthly one before the rolling one, then the most recently ingested file,
-- then the earliest row of that file. The source URL closes the order, so the
-- choice never depends on the order the files were read in.
CREATE TABLE fuel_price AS
SELECT
    station_cnpj,
    product,
    collected_on,
    survey_week(collected_on) AS survey_week,
    sale_price,
    purchase_price,
    unit,
    state_code,
    region_code,
    municipality,
    brand,
    _source_kind AS source_kind,
    _source_period AS source_period,
    count(*) OVER same_key AS n_candidates,
    min(sale_price) OVER same_key <> max(sale_price) OVER same_key AS price_conflict
FROM staged_fuel_price
WINDOW same_key AS (PARTITION BY station_cnpj, product, collected_on)
QUALIFY row_number() OVER (
    same_key
    ORDER BY
        list_position(['semester', 'monthly', 'last_four_weeks'], _source_kind),
        _ingested_at DESC,
        _source_row_number,
        _source_url
) = 1;
