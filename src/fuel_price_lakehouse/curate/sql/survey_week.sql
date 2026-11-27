-- survey_week(day): the ANP survey week a day belongs to, named by its first day.
--
-- The week runs from Sunday to Saturday. That is ANP's own convention (its
-- weekly spreadsheets are all Sunday to Saturday), and bucketing the collection
-- dates this way reproduces ANP's station counts. isodow() is 1 on Monday and
-- 7 on Sunday, so the remainder is the number of days since Sunday.
CREATE MACRO survey_week(day) AS day - CAST(isodow(day) % 7 AS INTEGER);
