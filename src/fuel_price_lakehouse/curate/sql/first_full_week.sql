-- first_full_week: the first survey week whose seven days all lie inside the
-- collected history. The weekly aggregates start there.
--
-- The history starts on the first day of the earliest period a staged file is
-- for (2020-S1 starts on Wednesday 2020-01-01). The survey week that holds that
-- day began on a Sunday no file covers, so its statistics would rest on part of
-- the week only: a first point with about half the usual stations. The first
-- full week is the one that starts on the first Sunday on or after that day,
-- which is also the week of the sixth day after it.
--
-- A rolling four-week file says nothing about where the history starts (its
-- period is "latest") and holds whole survey weeks, so it does not count. With
-- no semester or monthly file, no week is dropped.
SET VARIABLE first_full_week = (
    SELECT coalesce(survey_week(min(period_start) + 6), DATE '-infinity')
    FROM (
        SELECT DISTINCT
            CASE _source_kind
                WHEN 'semester' THEN make_date(
                    CAST(left(_source_period, 4) AS INTEGER),
                    CASE right(_source_period, 2) WHEN 'S1' THEN 1 ELSE 7 END,
                    1
                )
                WHEN 'monthly' THEN CAST(_source_period || '-01' AS DATE)
            END AS period_start
        FROM staged_fuel_price
    )
);
