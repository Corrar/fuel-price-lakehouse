-- station: one row per CNPJ. Station level, never published.
--
-- Name, brand and address are the ones of the station's most recent
-- observation: ANP writes the registry as it stood when each file was
-- generated, so the latest file knows the current name. A fact row cannot be
-- used for this, because the row that wins the de-duplication comes from the
-- oldest kind of file, not the newest. n_names and n_brands say how much the
-- registry changed over the station's history.
CREATE TABLE station AS
WITH history AS (
    SELECT
        station_cnpj,
        arg_max(
            {
                'station_name': station_name,
                'brand': brand,
                'street': street,
                'street_number': street_number,
                'address_complement': address_complement,
                'neighbourhood': neighbourhood,
                'postal_code': postal_code,
                'municipality': municipality,
                'state_code': state_code,
                'region_code': region_code
            },
            -- Latest date, then the most recently ingested file; the last two
            -- only make the order total.
            (collected_on, _ingested_at, _source_row_number, _source_url)
        ) AS latest,
        min(collected_on) AS first_seen,
        max(collected_on) AS last_seen,
        count(DISTINCT (product, collected_on)) AS n_observations,
        count(DISTINCT station_name) AS n_names,
        count(DISTINCT brand) AS n_brands
    FROM staged_fuel_price
    GROUP BY station_cnpj
)
SELECT
    station_cnpj,
    latest.*,
    first_seen,
    last_seen,
    n_observations,
    n_names,
    n_brands
FROM history;
