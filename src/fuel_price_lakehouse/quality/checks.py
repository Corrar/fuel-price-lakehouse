"""Dataset-level checks: is what this run produced fit to be published?

The staging rules judge one row at a time. The checks here judge the result of
a whole run: was every file collected and staged, do the row counts add up, is
the latest week there and plausible, do the public tables hold aggregates only.

Each check has a severity. An ``error`` check that does not hold gives ``fail``,
and a report with a ``fail`` stops the dashboard from being published. A
``warning`` check that does not hold gives ``warn`` and is shown on the
dashboard.

Three decisions shape the catalogue:

- A check that warns on every run teaches people to ignore it, so documented
  facts of the history do not warn: the blank rows that end one semester file
  are left out of the quarantine rates, and the weeks in which ANP ran no
  survey are not gaps.
- A check whose inputs are not there (a state file, a curated table that was
  never built) does not hold, and says what is missing. It never raises: a
  first run and a half-finished one still get a report.
- Results name files, weeks, products and states, and count rows. They never
  quote a row, and never repeat an error message kept in the state files: that
  is free text, and a file that lost its header puts a station's name and CNPJ
  in it.

Check ids are a public contract, like rule ids: they are stored in the report
and in the history of runs. Renaming one is a breaking change.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path
from statistics import median
from typing import Any

import duckdb

from fuel_price_lakehouse.collect import landing
from fuel_price_lakehouse.collect.manifest import FileRecord, Manifest
from fuel_price_lakehouse.config import Clock, utc_now
from fuel_price_lakehouse.curate.tables import PUBLIC_TABLES, STATION_LEVEL_COLUMNS, TABLES
from fuel_price_lakehouse.layout import (
    Source,
    curated_dir,
    last_extract_path,
    manifest_path,
    staging_state_path,
)
from fuel_price_lakehouse.reference import STATES
from fuel_price_lakehouse.stage.rules import Severity
from fuel_price_lakehouse.stage.run import StagedFile, StagingState

QUARANTINE_RATE_WARNING = 0.01
QUARANTINE_RATE_ERROR = 0.05
"""Share of the non-blank rows in quarantine, of the whole run or of one file,
that deserves a look, and the share of the whole run that blocks publication.
The history collected since 2020 has 16 such rows in 6.97 million (0.0002 %),
so one row in a hundred already means a file in a shape nobody has seen."""

BLANK_ROW_RULE = "row_not_blank"
"""Rows caught by this staging rule are left out of the quarantine rates. A
blank row breaks this rule and no other (``stage/anp.py``), so its count is the
number of rows quarantined for being blank. They are padding at the end of a
file, not bad data: one semester file ends with 9,114 of them, 2.1 % of its
rows, and would warn on every run."""

MAX_PRICE_AGE_DAYS = 21
"""ANP updates its files every week (``docs/sources.md``, section 1), so the
latest collection date is normally less than two weeks old. Three weeks means
two updates in a row did not arrive."""

MAX_USD_AGE_DAYS = 7
"""Banco Central publishes the dollar rate on business days only. A week is
longer than any weekend with holidays around it."""

REFERENCE_PRODUCT = "GASOLINA"
"""The product that tells whether a state was surveyed: regular gasoline is
collected in every state, which natural gas (GNV) is not."""

VOLUME_BASELINE_WEEKS = 8
MIN_LATEST_WEEK_SHARE = 0.5
"""The latest week needs this share of the median number of stations of the
weeks before it. Since 2020 the only weeks below it are the eight that followed
the two gaps of the survey, when it restarted with 14 % to 38 % of the median.
The budget cut of mid 2024, the largest drop that was not a restart, left 60 %."""

MAX_WEEKLY_MOVE = 0.15
"""How far the national median of a product may move against the previous
published week before it is flagged. Since 2020 it happened three times: both
diesels in the week of 13 March 2022 and GNV in the week of 2 May 2021. Rare
enough to deserve a look, possible enough to be a warning and not an error."""

KNOWN_SURVEY_GAPS = frozenset(
    {date(2020, 8, 23) + timedelta(weeks=n) for n in range(8)} | {date(2022, 9, 18)}
)
"""Survey weeks (named by their Sunday) in which ANP collected no prices: 23
August to 17 October 2020, and 18 to 24 September 2022, when the contractor
changed (``docs/sources.md``, section 1.8). They will never have data."""

_LISTED = 5
"""How many offenders a detail names before it only says how many more there are."""


class Status(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


_STATUS_WHEN_BROKEN = {Severity.ERROR: Status.FAIL, Severity.WARNING: Status.WARN}


class MissingInputError(Exception):
    """Something a check reads is not there yet: a state file, or a curated table."""


@dataclass(frozen=True)
class Evidence:
    """What the checks look at, opened once for all of them."""

    root: Path
    """The data directory, for the checks that look at files themselves."""
    today: date
    """The day of the run, which the freshness checks count from."""
    collection: Mapping[str, FileRecord] | None
    """The records of the collection manifest by key. None: there is no manifest."""
    staging: Mapping[str, StagedFile] | None
    """The entries of the staging state by key. None: there is no staging state."""
    last_extract: Mapping[str, Any] | None
    """What the latest collection could not get. None: no collection recorded it."""
    connection: duckdb.DuckDBPyConnection
    """A database with one view per curated table in ``tables``, named after it."""
    tables: frozenset[str]
    """The curated tables that are on disk."""

    def collected(self) -> Mapping[str, FileRecord]:
        if self.collection is None:
            raise MissingInputError("no collection manifest: run the collection first")
        return self.collection

    def extracted(self) -> Mapping[str, Any]:
        if self.last_extract is None:
            raise MissingInputError("no record of the latest collection: run the collection")
        return self.last_extract

    def staged(self) -> Mapping[str, StagedFile]:
        if self.staging is None:
            raise MissingInputError("no staging state: run the staging first")
        return self.staging

    def curated(self, *tables: str) -> duckdb.DuckDBPyConnection:
        """The database, once it is known to hold ``tables``."""
        missing = [table for table in tables if table not in self.tables]
        if missing:
            raise MissingInputError(
                f"curated table not built: {', '.join(missing)}; run the curated build first"
            )
        return self.connection


@dataclass(frozen=True)
class Finding:
    """What one check saw."""

    passed: bool
    observed: str
    expected: str
    detail: str = ""
    """What does not hold, named: files, weeks, products, states."""
    severity: Severity | None = None
    """For the checks with two thresholds: how serious what was found is, when
    that is not the severity of the check."""


@dataclass(frozen=True)
class Check:
    id: str
    severity: Severity
    description: str
    """One plain sentence saying what holds when the check passes (shown publicly)."""
    description_pt: str
    run: Callable[[Evidence], Finding]


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one check, as it goes into the report."""

    id: str
    severity: Severity
    """The severity of the check or, when it did not hold, of what was found."""
    status: Status
    description: str
    description_pt: str
    observed: str
    expected: str
    detail: str


def file_identity(key: str, source: str, details: Mapping[str, str]) -> tuple[str, str | None, str]:
    """``(kind, fuel_group, period)``: how the report names one upstream file.

    A Banco Central window is named from its key, ``bcb_sgs/<code>/<year>``:
    a window that never downloaded has no details yet.
    """
    if source == Source.BCB_SGS:
        _, series, year = key.split("/")
        return f"series_{series}", None, year
    return details["kind"], details["fuel_group"], details["period"]


def staging_problem(record: FileRecord, entry: StagedFile | None) -> str | None:
    """Why the staged layer does not hold the current content of ``record``.

    None when it does, and when there is nothing to stage: a Banco Central
    window without observations has no raw file.
    """
    if not record.raw_path:
        return None
    if entry is None:
        return "not staged"
    if entry.error is not None:
        return "staging failed"
    if entry.raw_sha256 != record.sha256:
        return "staged from an earlier download"
    if record.raw_file_sha256 and entry.raw_file_sha256 != record.raw_file_sha256:
        return "staged from an earlier raw copy"
    return None


def _label(key: str, source: str, details: Mapping[str, str]) -> str:
    kind, fuel_group, period = file_identity(key, source, details)
    return " ".join(part for part in (source, kind, fuel_group, period) if part)


def _listed(items: Sequence[str]) -> str:
    more = len(items) - _LISTED
    shown = "; ".join(items[:_LISTED])
    return f"{shown}; and {more} more" if more > 0 else shown


def _non_blank(entry: StagedFile) -> tuple[int, int]:
    """Rows of a file in quarantine, and rows read, blank rows left out of both."""
    blank = entry.rule_failures.get(BLANK_ROW_RULE, 0)
    return entry.rows_quarantined - blank, entry.rows_read - blank


def _count(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _rows(
    connection: duckdb.DuckDBPyConnection, query: str, parameters: Sequence[Any] = ()
) -> list[tuple[Any, ...]]:
    return connection.execute(query, parameters).fetchall()


def _collection_complete(evidence: Evidence) -> Finding:
    records = evidence.collected().values()
    never = [record for record in records if not record.ingested]
    stale = [record for record in records if record.ingested and record.last_error]
    problems = [
        f"{_label(record.key, record.source, record.details)}: {problem}"
        for group, problem in ((never, "never collected"), (stale, "latest download failed"))
        for record in group
    ]
    return Finding(
        passed=not problems,
        observed=(
            f"{len(never)} of {len(records)} files never collected; "
            f"{len(stale)} kept from an earlier download after a failed one"
        ),
        expected="0 files never collected, 0 failed downloads",
        detail=_listed(problems),
        severity=None if never else Severity.WARNING,
    )


def _sources_reachable(evidence: Evidence) -> Finding:
    latest = evidence.extracted()
    failed = latest.get("failed", [])
    return Finding(
        passed=not failed,
        observed=f"{len(failed)} failures in the latest collection",
        expected="0 failures",
        detail=_listed([f"{item['key']}: {item['detail']}" for item in failed]),
    )


def _raw_files_read_strictly(evidence: Evidence) -> Finding:
    files = [
        record
        for record in evidence.collected().values()
        if record.source == Source.ANP and record.ingested
    ]
    lenient = [
        _label(record.key, record.source, record.details)
        for record in files
        if record.details.get("csv_parsing") == "lenient"
    ]
    return Finding(
        passed=not lenient,
        observed=f"{len(lenient)} of {len(files)} ANP files read by the lenient reader",
        expected="0 files",
        detail=_listed(lenient),
    )


def _landing_copies_intact(evidence: Evidence) -> Finding:
    files = [record for record in evidence.collected().values() if record.ingested]
    problems = []
    for record in files:
        label = _label(record.key, record.source, record.details)
        if record.landing_path is None:
            # An empty Banco Central window is not a download: there are no bytes to keep.
            if record.raw_path is not None:
                problems.append(f"{label}: no landed copy")
            continue
        landed = evidence.root / record.landing_path
        if not landed.is_file():
            problems.append(f"{label}: landed copy missing")
        elif landing.sha256_of(landed) != record.sha256:
            problems.append(f"{label}: landed copy does not match its SHA-256")
    return Finding(
        passed=not problems,
        observed=f"{len(problems)} of {len(files)} collected files without an intact copy",
        expected="0 files",
        detail=_listed(problems),
    )


def _staging_complete(evidence: Evidence) -> Finding:
    staging = evidence.staged()
    files = [record for record in evidence.collected().values() if record.ingested]
    problems = [
        f"{_label(record.key, record.source, record.details)}: {problem}"
        for record in files
        if (problem := staging_problem(record, staging.get(record.key)))
    ]
    return Finding(
        passed=not problems,
        observed=f"{len(problems)} of {len(files)} collected files not staged as they are now",
        expected="0 files",
        detail=_listed(problems),
    )


def _row_counts_reconcile(evidence: Evidence) -> Finding:
    staging = evidence.staged()
    compared, problems = 0, []
    for record in evidence.collected().values():
        entry = staging.get(record.key)
        if entry is None or entry.error is not None:
            continue  # no counts to compare; staging_complete reports the file
        compared += 1
        adds_up = entry.rows_read == entry.rows_staged + entry.rows_quarantined
        if not adds_up or entry.rows_read != (record.rows or 0):
            problems.append(
                f"{_label(record.key, record.source, record.details)}: {record.rows} collected, "
                f"{entry.rows_read} read, {entry.rows_staged} staged, "
                f"{entry.rows_quarantined} quarantined"
            )
    return Finding(
        passed=not problems,
        observed=f"{len(problems)} of {compared} staged files with row counts that do not add up",
        expected="0 files",
        detail=_listed(problems),
    )


def _quarantine_rate(evidence: Evidence) -> Finding:
    counts = [_non_blank(entry) for entry in evidence.staged().values()]
    quarantined = sum(in_quarantine for in_quarantine, _ in counts)
    rows = sum(read for _, read in counts)
    rate = quarantined / rows if rows else 0.0
    return Finding(
        passed=rate <= QUARANTINE_RATE_WARNING,
        observed=f"{rate:.3%} ({quarantined} of {rows} non-blank rows)",
        expected=f"at most {QUARANTINE_RATE_WARNING:.0%}",
        severity=Severity.ERROR if rate > QUARANTINE_RATE_ERROR else None,
    )


def _file_quarantine_rate(evidence: Evidence) -> Finding:
    staging = evidence.staged()
    offenders = []
    for key, entry in staging.items():
        quarantined, rows = _non_blank(entry)
        if rows and quarantined / rows > QUARANTINE_RATE_WARNING:
            offenders.append(
                f"{_label(key, entry.source, entry.details)}: {quarantined / rows:.3%} "
                f"({quarantined} of {rows} non-blank rows)"
            )
    return Finding(
        passed=not offenders,
        observed=f"{len(offenders)} of {len(staging)} files above {QUARANTINE_RATE_WARNING:.0%}",
        expected="0 files",
        detail=_listed(offenders),
    )


def _fuel_price_key_unique(evidence: Evidence) -> Finding:
    ((rows, repeated),) = _rows(
        evidence.curated("fuel_price"),
        "SELECT coalesce(sum(n), 0), count(*) FILTER (WHERE n > 1) FROM ("
        "SELECT count(*) AS n FROM fuel_price GROUP BY station_cnpj, product, collected_on)",
    )
    return Finding(
        passed=repeated == 0,
        observed=f"{_count(repeated, 'duplicated key')} in {rows} rows",
        expected="0 duplicated keys",
    )


def _not_older_than(latest: date | None, today: date, limit: int, what: str) -> Finding:
    expected = f"at most {_count(limit, 'day')}"
    if latest is None:
        return Finding(False, f"no {what}", expected)
    age = (today - latest).days
    return Finding(
        age <= limit, f"{_count(age, 'day')} (latest {what} {latest.isoformat()})", expected
    )


def _prices_fresh(evidence: Evidence) -> Finding:
    ((latest,),) = _rows(evidence.curated("fuel_price"), "SELECT max(collected_on) FROM fuel_price")
    return _not_older_than(latest, evidence.today, MAX_PRICE_AGE_DAYS, "collection date")


def _all_states_present(evidence: Evidence) -> Finding:
    expected = f"{len(STATES)} states"
    connection = evidence.curated("fuel_price")
    ((week,),) = _rows(connection, "SELECT max(survey_week) FROM fuel_price")
    if week is None:
        return Finding(False, "no prices", expected)
    surveyed = {
        state
        for (state,) in _rows(
            connection,
            "SELECT DISTINCT state_code FROM fuel_price WHERE survey_week = ? AND product = ?",
            [week, REFERENCE_PRODUCT],
        )
    }
    missing = [state.code for state in STATES if state.code not in surveyed]
    return Finding(
        passed=not missing,
        observed=(
            f"{len(STATES) - len(missing)} of {len(STATES)} states "
            f"in the week of {week.isoformat()}"
        ),
        expected=expected,
        detail=f"missing: {', '.join(missing)}" if missing else "",
    )


def _latest_week_volume(evidence: Evidence) -> Finding:
    expected = f"at least {MIN_LATEST_WEEK_SHARE:.0%} of the median"
    stations = [
        count
        for (count,) in _rows(
            evidence.curated("fuel_price"),
            "SELECT count(DISTINCT station_cnpj) FROM fuel_price "
            f"GROUP BY survey_week ORDER BY survey_week DESC LIMIT {VOLUME_BASELINE_WEEKS + 1}",
        )
    ]
    if not stations:
        return Finding(False, "no prices", expected)
    latest, *previous = stations
    if not previous:
        return Finding(
            True, f"{_count(latest, 'station')}; no earlier week to compare with", expected
        )
    baseline = median(previous)
    return Finding(
        passed=latest >= MIN_LATEST_WEEK_SHARE * baseline,
        observed=(
            f"{_count(latest, 'station')}; "
            f"median of the previous {_count(len(previous), 'week')} {baseline:.10g}"
        ),
        expected=expected,
    )


def _no_unexpected_survey_gap(evidence: Evidence) -> Finding:
    expected = "0 weeks without prices other than the known gaps"
    surveyed = [
        week
        for (week,) in _rows(
            evidence.curated("fuel_price"),
            "SELECT DISTINCT survey_week FROM fuel_price ORDER BY survey_week",
        )
    ]
    if not surveyed:
        return Finding(False, "no prices", expected)
    span = (surveyed[-1] - surveyed[0]).days // 7 + 1
    every_week = {surveyed[0] + timedelta(weeks=n) for n in range(span)}
    missing = every_week.difference(surveyed)
    unexpected = sorted(missing - KNOWN_SURVEY_GAPS)
    return Finding(
        passed=not unexpected,
        observed=(
            f"{len(unexpected)} unexpected and {len(missing & KNOWN_SURVEY_GAPS)} known weeks "
            f"without prices, of {span} weeks"
        ),
        expected=expected,
        detail=(
            "weeks starting " + _listed([week.isoformat() for week in unexpected])
            if unexpected
            else ""
        ),
    )


def _weekly_price_jump(evidence: Evidence) -> Finding:
    expected = f"less than {MAX_WEEKLY_MOVE:.0%} for every product"
    medians = _rows(
        evidence.curated("agg_price_weekly_national"),
        "SELECT product, price_median, previous_median FROM ("
        "SELECT *, lag(price_median) OVER (PARTITION BY product ORDER BY survey_week) "
        "AS previous_median FROM agg_price_weekly_national) "
        "WHERE survey_week = (SELECT max(survey_week) FROM agg_price_weekly_national) "
        "ORDER BY product",
    )
    if not medians:
        return Finding(False, "no weekly prices", expected)
    # Six decimals: prices carry four, and a move of exactly the limit must not
    # fall on either side of it by an accident of binary floating point.
    moves = {product: round(now / before - 1, 6) for product, now, before in medians if before}
    if not moves:
        return Finding(True, "no earlier week to compare with", expected)
    offenders = [
        f"{product} {move:+.2%}" for product, move in moves.items() if abs(move) >= MAX_WEEKLY_MOVE
    ]
    largest = max(moves, key=lambda product: abs(moves[product]))
    return Finding(
        passed=not offenders,
        observed=f"largest move {abs(moves[largest]):.2%} ({largest})",
        expected=expected,
        detail=_listed(offenders),
    )


def _usd_fresh(evidence: Evidence) -> Finding:
    ((latest,),) = _rows(
        evidence.curated("usd_brl_daily"), "SELECT max(observed_on) FROM usd_brl_daily"
    )
    return _not_older_than(latest, evidence.today, MAX_USD_AGE_DAYS, "rate")


def _ipca_complete(evidence: Evidence) -> Finding:
    expected = "0 missing months"
    ((first, last, months),) = _rows(
        evidence.curated("ipca_monthly"),
        "SELECT min(month), max(month), count(DISTINCT month) FROM ipca_monthly",
    )
    if first is None:
        return Finding(False, "no IPCA months", expected)
    missing = (last.year - first.year) * 12 + last.month - first.month + 1 - months
    return Finding(
        passed=missing == 0,
        observed=f"{_count(missing, 'missing month')} between {first:%Y-%m} and {last:%Y-%m}",
        expected=expected,
    )


def _public_tables_have_no_station_columns(evidence: Evidence) -> Finding:
    public = [table.name for table in PUBLIC_TABLES]
    connection = evidence.curated(*public)
    found = [
        f"{table}: {column}"
        for table in public
        for column, *_ in _rows(connection, f"DESCRIBE {table}")
        if column in STATION_LEVEL_COLUMNS
    ]
    return Finding(
        passed=not found,
        observed=(
            f"{_count(len(found), 'station-level column')} in the {len(public)} public tables"
        ),
        expected="0 station-level columns",
        detail=_listed(found),
    )


CHECKS: tuple[Check, ...] = (
    Check(
        "collection_complete",
        Severity.ERROR,
        "Every source file was collected. A file never collected is an error; one whose "
        "latest download failed, with an earlier copy still in use, is a warning.",
        "Todo arquivo de origem foi coletado. Arquivo nunca coletado é erro; arquivo cujo "
        "download mais recente falhou, com uma cópia anterior ainda em uso, é alerta.",
        _collection_complete,
    ),
    Check(
        "sources_reachable",
        Severity.WARNING,
        "Every source answered on the latest collection: the ANP landing page could be "
        "read and no file failed to download.",
        "Todas as fontes responderam na coleta mais recente: a página da ANP pôde ser lida "
        "e nenhum arquivo falhou no download.",
        _sources_reachable,
    ),
    Check(
        "raw_files_read_strictly",
        Severity.WARNING,
        "Every ANP file was well-formed CSV: none needed the lenient reader, "
        "which tolerates broken quoting.",
        "Todo arquivo da ANP era um CSV bem formado: nenhum precisou do leitor tolerante, "
        "que aceita aspas malformadas.",
        _raw_files_read_strictly,
    ),
    Check(
        "landing_copies_intact",
        Severity.WARNING,
        "Every collected file has its downloaded bytes in the landing zone, and they still "
        "match the SHA-256 recorded when they were downloaded.",
        "Todo arquivo coletado tem os bytes baixados guardados na zona de pouso, e eles ainda "
        "batem com o SHA-256 registrado no download.",
        _landing_copies_intact,
    ),
    Check(
        "staging_complete",
        Severity.ERROR,
        "Every collected file is in the staged layer as it is now, "
        "without a file-level error such as schema drift.",
        "Todo arquivo coletado está na camada tratada em sua versão atual, "
        "sem erro no nível do arquivo, como mudança de esquema.",
        _staging_complete,
    ),
    Check(
        "row_counts_reconcile",
        Severity.ERROR,
        "For every file, the rows read are the rows collected, "
        "and each of them is either staged or in quarantine.",
        "Em todo arquivo, as linhas lidas são as linhas coletadas, "
        "e cada uma delas está na camada tratada ou na quarentena.",
        _row_counts_reconcile,
    ),
    Check(
        "quarantine_rate",
        Severity.WARNING,
        "The share of non-blank rows sent to quarantine stays low (warning above "
        f"{QUARANTINE_RATE_WARNING:.0%}, error above {QUARANTINE_RATE_ERROR:.0%}).",
        "A parcela de linhas não vazias enviadas à quarentena é baixa (alerta acima de "
        f"{QUARANTINE_RATE_WARNING:.0%}, erro acima de {QUARANTINE_RATE_ERROR:.0%}).",
        _quarantine_rate,
    ),
    Check(
        "file_quarantine_rate",
        Severity.WARNING,
        f"No single file has more than {QUARANTINE_RATE_WARNING:.0%} of its non-blank rows "
        "in quarantine.",
        f"Nenhum arquivo tem mais de {QUARANTINE_RATE_WARNING:.0%} das suas linhas não vazias "
        "em quarentena.",
        _file_quarantine_rate,
    ),
    Check(
        "fuel_price_key_unique",
        Severity.ERROR,
        "There is one curated price per station, product and collection date.",
        "Há um único preço curado por posto, produto e data de coleta.",
        _fuel_price_key_unique,
    ),
    Check(
        "prices_fresh",
        Severity.WARNING,
        f"The latest collection date is at most {MAX_PRICE_AGE_DAYS} days before the run.",
        f"A data de coleta mais recente é de no máximo {MAX_PRICE_AGE_DAYS} dias antes "
        "da execução.",
        _prices_fresh,
    ),
    Check(
        "all_states_present",
        Severity.WARNING,
        f"The latest survey week has regular gasoline prices for all {len(STATES)} states.",
        f"A semana mais recente da pesquisa tem preços de gasolina comum para as {len(STATES)} "
        "UFs.",
        _all_states_present,
    ),
    Check(
        "latest_week_volume",
        Severity.WARNING,
        f"The latest week has prices from at least {MIN_LATEST_WEEK_SHARE:.0%} of the median "
        f"number of stations of the {VOLUME_BASELINE_WEEKS} weeks with prices before it.",
        f"A semana mais recente tem preços de pelo menos {MIN_LATEST_WEEK_SHARE:.0%} do número "
        f"mediano de postos das {VOLUME_BASELINE_WEEKS} semanas com preços anteriores a ela.",
        _latest_week_volume,
    ),
    Check(
        "no_unexpected_survey_gap",
        Severity.WARNING,
        "Every week between the first and the latest one has prices, "
        "except the weeks in which ANP ran no survey.",
        "Toda semana entre a primeira e a mais recente tem preços, "
        "exceto as semanas em que a ANP não fez a pesquisa.",
        _no_unexpected_survey_gap,
    ),
    Check(
        "weekly_price_jump",
        Severity.WARNING,
        f"The national median of each product moved less than {MAX_WEEKLY_MOVE:.0%} "
        "in the latest week, against the previous published week.",
        f"A mediana nacional de cada produto variou menos de {MAX_WEEKLY_MOVE:.0%} "
        "na semana mais recente, em relação à semana publicada anterior.",
        _weekly_price_jump,
    ),
    Check(
        "usd_fresh",
        Severity.WARNING,
        f"The latest dollar rate is at most {MAX_USD_AGE_DAYS} days old.",
        f"A cotação do dólar mais recente tem no máximo {MAX_USD_AGE_DAYS} dias.",
        _usd_fresh,
    ),
    Check(
        "ipca_complete",
        Severity.ERROR,
        "No month is missing between the first and the last IPCA month.",
        "Não falta nenhum mês entre o primeiro e o último mês do IPCA.",
        _ipca_complete,
    ),
    Check(
        "public_tables_have_no_station_columns",
        Severity.ERROR,
        "No published table carries a station-level column (CNPJ, name or address).",
        "Nenhuma tabela publicada contém coluna no nível do posto (CNPJ, nome ou endereço).",
        _public_tables_have_no_station_columns,
    ),
)


@contextmanager
def gather(root: Path, today: date) -> Iterator[Evidence]:
    """Open what the checks read under ``root``. Nothing is written."""
    collection, staging = manifest_path(root), staging_state_path(root)
    last_extract = last_extract_path(root)
    connection = duckdb.connect()
    try:
        yield Evidence(
            root=root,
            today=today,
            collection=Manifest.load(collection).files if collection.exists() else None,
            staging=StagingState.load(staging).files if staging.exists() else None,
            last_extract=(
                json.loads(last_extract.read_text(encoding="utf-8"))
                if last_extract.exists()
                else None
            ),
            connection=connection,
            tables=_create_views(connection, curated_dir(root)),
        )
    finally:
        connection.close()


def evaluate(evidence: Evidence) -> list[CheckResult]:
    """Every check of the catalogue against ``evidence``, in catalogue order."""
    return [_result(check, evidence) for check in CHECKS]


def run_checks(root: Path, *, now: Clock = utc_now) -> list[CheckResult]:
    """Evaluate the catalogue on the data under ``root``, as of the day ``now`` gives."""
    with gather(root, now().date()) as evidence:
        return evaluate(evidence)


def _create_views(connection: duckdb.DuckDBPyConnection, curated: Path) -> frozenset[str]:
    """One view per curated table that has files, over exactly those files."""
    present = []
    for table in TABLES:
        files = sorted(curated.glob(table.path))
        if files:
            listed = ", ".join("'" + str(file).replace("'", "''") + "'" for file in files)
            connection.execute(
                f"CREATE VIEW {table.name} AS "
                f"SELECT * FROM read_parquet([{listed}], hive_partitioning = false)"
            )
            present.append(table.name)
    return frozenset(present)


def _result(check: Check, evidence: Evidence) -> CheckResult:
    try:
        finding = check.run(evidence)
    except MissingInputError as error:
        finding = Finding(False, "not evaluated", "", str(error))
    severity = check.severity if finding.passed else finding.severity or check.severity
    return CheckResult(
        id=check.id,
        severity=severity,
        status=Status.PASS if finding.passed else _STATUS_WHEN_BROKEN[severity],
        description=check.description,
        description_pt=check.description_pt,
        observed=finding.observed,
        expected=finding.expected,
        detail=finding.detail,
    )
