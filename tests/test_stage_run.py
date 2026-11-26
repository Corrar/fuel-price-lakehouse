"""The staging driver: what gets staged, what is left alone, and what the state records.

Raw files come from a real collection against the fake sources. SGS windows go
through the real SGS stager; ANP files go through a small stand-in, because all
the driver knows about a source is the stager registered for it.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fuel_price_lakehouse.collect.run import run as collect
from fuel_price_lakehouse.config import Clock
from fuel_price_lakehouse.layout import manifest_path, staging_state_path
from fuel_price_lakehouse.stage import bcb_sgs
from fuel_price_lakehouse.stage import run as driver
from fuel_price_lakehouse.stage.model import SchemaDriftError, StagedFrames
from fuel_price_lakehouse.stage.rules import FileContext

from .conftest import FOUR_WEEKS, OCTOBER_DIESEL, OCTOBER_GASOLINE, OCTOBER_LPG, FakeWeb, anp_csv

STAGED_AT = datetime(2026, 11, 26, 13, 0, tzinfo=UTC)

SGS = ("bcb_sgs",)
ANP = ("anp",)
BOTH = ("anp", "bcb_sgs")

DOLLAR = "bcb_sgs/1/2026"
IPCA = "bcb_sgs/433/2026"
IPCA_RAW = "raw/bcb_sgs/series/series=433/year=2026/data.parquet"
IPCA_STAGED = "staged/bcb_sgs/series/series=433/year=2026/data.parquet"
IPCA_QUARANTINE = "quarantine/bcb_sgs/series/series=433/year=2026/data.parquet"

ONE_BAD_VALUE = b'[{"data":"01/01/2026","valor":"0.16"},{"data":"01/02/2026","valor":"n/d"}]'
ALL_BAD_VALUES = b'[{"data":"01/01/2026","valor":"?"},{"data":"01/02/2026","valor":"n/d"}]'
RENAMED_FIELD = b'[{"data":"01/01/2026","value":"0.16"}]'


class FakePriceStager:
    """Stands in for the ANP staging module: a row is staged when its price is a number."""

    STAGED_ARROW_SCHEMA = pa.schema([("price", pa.float64()), ("_source_row_number", pa.int64())])
    QUARANTINE_ARROW_SCHEMA = pa.schema(
        [("Valor de Venda", pa.string()), ("_source_row_number", pa.int64())]
    )

    def stage(self, raw: pd.DataFrame, context: FileContext, *, now: Clock) -> StagedFrames:
        if "Valor de Venda" not in raw.columns:
            raise SchemaDriftError(f"{context.key}: no price column")
        price = pd.to_numeric(raw["Valor de Venda"].str.replace(",", "."), errors="coerce")
        frame = raw.rename(columns={"_row_number": "_source_row_number"}).assign(price=price)
        bad = price.isna()
        return StagedFrames(
            staged=frame.loc[~bad, self.STAGED_ARROW_SCHEMA.names],
            quarantine=frame.loc[bad, self.QUARANTINE_ARROW_SCHEMA.names],
            error_counts={"price_is_a_number": int(bad.sum())},
            warning_counts={},
        )


@pytest.fixture
def fake_anp(monkeypatch) -> None:
    monkeypatch.setitem(driver.STAGERS, "anp", FakePriceStager)


def collect_raw(
    web: FakeWeb,
    root: Path,
    fast: dict,
    sources: tuple[str, ...] = SGS,
    since: date = date(2026, 7, 1),
) -> None:
    """Run the real collection against the fake sources: raw files and their manifest."""
    settings = {key: fast[key] for key in ("sleep", "retry", "now")}
    with web.client() as client:
        collect(root, since, client=client, sources=sources, **settings)


def stage(root: Path, at: datetime = STAGED_AT, **options) -> dict[str, driver.StageOutcome]:
    return {outcome.key: outcome for outcome in driver.run(root, now=lambda: at, **options)}


def actions(outcomes: dict[str, driver.StageOutcome]) -> dict[str, str]:
    return {key: outcome.action for key, outcome in outcomes.items()}


def manifest(root: Path) -> dict:
    return json.loads(manifest_path(root).read_text(encoding="utf-8"))["files"]


def state(root: Path) -> dict:
    return json.loads(staging_state_path(root).read_text(encoding="utf-8"))["files"]


def rows(root: Path, relative: str) -> list[dict]:
    return pq.read_table(root / relative).to_pylist()


def parquet_files(root: Path, layer: str) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in (root / layer).rglob("*.parquet"))


def snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    """Every file under the data root with its modification time and content."""
    return {
        path.relative_to(root).as_posix(): (path.stat().st_mtime_ns, path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# --- what a run stages ---------------------------------------------------------------------------


def test_first_run_stages_every_raw_file(web, tmp_path, fast, fake_anp):
    collect_raw(web, tmp_path, fast, BOTH)

    outcomes = stage(tmp_path)

    assert actions(outcomes) == dict.fromkeys(
        [OCTOBER_DIESEL, OCTOBER_GASOLINE, OCTOBER_LPG, FOUR_WEEKS, DOLLAR, IPCA], "staged"
    )
    assert set(state(tmp_path)) == set(outcomes)
    assert [row["value"] for row in rows(tmp_path, IPCA_STAGED)] == [0.16]
    assert len(parquet_files(tmp_path, "staged")) == 6
    assert not list(tmp_path.rglob("*.tmp"))


def test_state_records_what_staging_made_of_a_file(web, tmp_path, fast):
    collect_raw(web, tmp_path, fast)

    outcomes = stage(tmp_path)

    record = manifest(tmp_path)[DOLLAR]
    assert state(tmp_path)[DOLLAR] == {
        "source": "bcb_sgs",
        "details": record["details"],
        "raw_sha256": record["sha256"],
        "staging_version": 1,
        "staged_at": "2026-11-26T13:00:00+00:00",
        "staged_path": "staged/bcb_sgs/series/series=1/year=2026/data.parquet",
        "quarantine_path": None,
        "rows_read": 2,
        "rows_staged": 2,
        "rows_quarantined": 0,
        "rule_failures": {
            "observed_on_is_a_date": 0,
            "value_is_a_number": 0,
            "observed_on_within_window_year": 0,
            "date_unique_in_window": 0,
        },
        "rule_warnings": {"observed_on_not_in_future": 0},
        "error": None,
    }
    assert json.loads(staging_state_path(tmp_path).read_text(encoding="utf-8"))["version"] == 1
    assert outcomes[DOLLAR] == driver.StageOutcome(
        DOLLAR, "bcb_sgs", "staged", 2, 2, 0, "2 staged, 0 quarantined"
    )


def test_file_goes_through_the_stager_registered_for_its_source(web, tmp_path, fast, fake_anp):
    web.files[OCTOBER_DIESEL] = anp_csv("6,01", "sem preço", "6,03")
    collect_raw(web, tmp_path, fast, BOTH)

    stage(tmp_path)

    entry = state(tmp_path)[OCTOBER_DIESEL]
    raw_path = manifest(tmp_path)[OCTOBER_DIESEL]["raw_path"]
    assert entry["source"] == "anp"
    assert entry["staged_path"] == raw_path.replace("raw/", "staged/", 1)
    assert entry["quarantine_path"] == raw_path.replace("raw/", "quarantine/", 1)
    assert entry["rule_failures"] == {"price_is_a_number": 1}
    assert rows(tmp_path, entry["staged_path"]) == [
        {"price": 6.01, "_source_row_number": 1},
        {"price": 6.03, "_source_row_number": 3},
    ]
    assert rows(tmp_path, entry["quarantine_path"]) == [
        {"Valor de Venda": "sem preço", "_source_row_number": 2}
    ]


def test_second_run_does_nothing_and_changes_no_byte(web, tmp_path, fast, fake_anp):
    web.sgs[(433, 2026)] = ONE_BAD_VALUE  # so there is a quarantine file as well
    collect_raw(web, tmp_path, fast, BOTH)
    first = stage(tmp_path)
    before = snapshot(tmp_path)

    second = stage(tmp_path, at=STAGED_AT + timedelta(days=7))

    assert set(actions(second).values()) == {"unchanged"}
    assert snapshot(tmp_path) == before  # state and Parquet untouched, modification times included
    assert second[IPCA] == replace(first[IPCA], action="unchanged")  # still says what is there


def test_republished_raw_file_is_staged_again(web, tmp_path, fast):
    collect_raw(web, tmp_path, fast)
    stage(tmp_path)
    first = state(tmp_path)
    dollar_written = (tmp_path / first[DOLLAR]["staged_path"]).stat().st_mtime_ns
    web.sgs[(433, 2026)] = (
        b'[{"data":"01/01/2026","valor":"0.16"},{"data":"01/02/2026","valor":"0.31"}]'
    )
    collect_raw(web, tmp_path, fast)

    outcomes = stage(tmp_path, at=STAGED_AT + timedelta(days=7))

    assert actions(outcomes) == {DOLLAR: "unchanged", IPCA: "staged"}
    second = state(tmp_path)
    assert second[IPCA]["raw_sha256"] == manifest(tmp_path)[IPCA]["sha256"]
    assert second[IPCA]["raw_sha256"] != first[IPCA]["raw_sha256"]
    assert second[IPCA]["staged_at"] == "2026-12-03T13:00:00+00:00"
    assert [row["value"] for row in rows(tmp_path, IPCA_STAGED)] == [0.16, 0.31]
    assert second[DOLLAR] == first[DOLLAR]
    assert (tmp_path / first[DOLLAR]["staged_path"]).stat().st_mtime_ns == dollar_written


def test_staging_version_bump_stages_every_file_again(web, tmp_path, fast, monkeypatch):
    collect_raw(web, tmp_path, fast)
    stage(tmp_path)
    monkeypatch.setattr(driver, "STAGING_VERSION", driver.STAGING_VERSION + 1)

    outcomes = stage(tmp_path)

    assert actions(outcomes) == {DOLLAR: "staged", IPCA: "staged"}
    assert {entry["staging_version"] for entry in state(tmp_path).values()} == {
        driver.STAGING_VERSION
    }
    assert set(actions(stage(tmp_path)).values()) == {"unchanged"}


def test_deleted_output_file_is_rebuilt(web, tmp_path, fast):
    collect_raw(web, tmp_path, fast)
    stage(tmp_path)
    (tmp_path / IPCA_STAGED).unlink()

    outcomes = stage(tmp_path)

    assert actions(outcomes) == {DOLLAR: "unchanged", IPCA: "staged"}
    assert [row["value"] for row in rows(tmp_path, IPCA_STAGED)] == [0.16]


def test_full_refresh_stages_every_file_again(web, tmp_path, fast):
    collect_raw(web, tmp_path, fast)
    stage(tmp_path)

    outcomes = stage(tmp_path, at=STAGED_AT + timedelta(hours=1), full_refresh=True)

    assert actions(outcomes) == {DOLLAR: "staged", IPCA: "staged"}
    assert {entry["staged_at"] for entry in state(tmp_path).values()} == {
        "2026-11-26T14:00:00+00:00"
    }


def test_up_to_date_file_is_not_opened(web, tmp_path, fast):
    collect_raw(web, tmp_path, fast)
    stage(tmp_path)
    (tmp_path / IPCA_RAW).unlink()  # would fail if the driver tried to read it

    assert actions(stage(tmp_path)) == {DOLLAR: "unchanged", IPCA: "unchanged"}


def test_window_without_observations_has_nothing_to_stage(web, tmp_path, fast):
    # The fake API has nothing for 2025: the manifest gets records with no raw file.
    collect_raw(web, tmp_path, fast, since=date(2025, 1, 1))
    assert manifest(tmp_path)["bcb_sgs/433/2025"]["raw_path"] is None

    outcomes = stage(tmp_path)

    assert set(outcomes) == {DOLLAR, IPCA}
    assert set(state(tmp_path)) == {DOLLAR, IPCA}


def test_run_before_any_collection_has_nothing_to_do(tmp_path):
    assert stage(tmp_path) == {}
    assert state(tmp_path) == {}


def test_registry_resolves_the_staging_module_of_each_source():
    anp = pytest.importorskip("fuel_price_lakehouse.stage.anp")

    assert driver.STAGERS["bcb_sgs"]() is bcb_sgs
    assert driver.STAGERS["anp"]() is anp


# --- files with no rows on one side --------------------------------------------------------------


def test_file_with_no_rejected_row_has_no_quarantine_file(web, tmp_path, fast):
    collect_raw(web, tmp_path, fast)

    stage(tmp_path)

    assert state(tmp_path)[IPCA]["quarantine_path"] is None
    assert parquet_files(tmp_path, "quarantine") == []


def test_file_with_every_row_rejected_has_no_staged_file(web, tmp_path, fast):
    web.sgs[(433, 2026)] = ALL_BAD_VALUES
    collect_raw(web, tmp_path, fast)

    outcomes = stage(tmp_path)

    entry = state(tmp_path)[IPCA]
    assert outcomes[IPCA].action == "staged"  # handled in full: nothing in it was usable
    assert (entry["staged_path"], entry["quarantine_path"]) == (None, IPCA_QUARANTINE)
    assert (entry["rows_read"], entry["rows_staged"], entry["rows_quarantined"]) == (2, 0, 2)
    assert not (tmp_path / IPCA_STAGED).exists()
    assert [row["valor"] for row in rows(tmp_path, IPCA_QUARANTINE)] == ["?", "n/d"]
    assert set(actions(stage(tmp_path)).values()) == {"unchanged"}


def test_quarantine_file_is_deleted_once_the_source_fixes_its_rows(web, tmp_path, fast):
    web.sgs[(433, 2026)] = ONE_BAD_VALUE
    collect_raw(web, tmp_path, fast)
    stage(tmp_path)
    assert [row["_rule_ids"] for row in rows(tmp_path, IPCA_QUARANTINE)] == [["value_is_a_number"]]
    web.sgs[(433, 2026)] = (
        b'[{"data":"01/01/2026","valor":"0.16"},{"data":"01/02/2026","valor":"0.31"}]'
    )
    collect_raw(web, tmp_path, fast)

    stage(tmp_path)

    assert state(tmp_path)[IPCA]["quarantine_path"] is None
    assert not (tmp_path / IPCA_QUARANTINE).exists()
    assert len(rows(tmp_path, IPCA_STAGED)) == 2


def test_staged_file_is_deleted_once_no_row_passes_any_more(web, tmp_path, fast):
    collect_raw(web, tmp_path, fast)
    stage(tmp_path)
    web.sgs[(433, 2026)] = ALL_BAD_VALUES
    collect_raw(web, tmp_path, fast)

    stage(tmp_path)

    assert state(tmp_path)[IPCA]["staged_path"] is None
    assert not (tmp_path / IPCA_STAGED).exists()


def test_output_left_by_an_interrupted_run_does_not_survive(web, tmp_path, fast):
    collect_raw(web, tmp_path, fast)
    leftover = tmp_path / IPCA_QUARANTINE  # written by a run that died before saving the state
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"rows of an earlier content")

    stage(tmp_path)

    assert not leftover.exists()


# --- failures of one file ------------------------------------------------------------------------


def test_schema_drift_fails_one_file_and_the_run_goes_on(web, tmp_path, fast, fake_anp):
    web.sgs[(433, 2026)] = RENAMED_FIELD
    collect_raw(web, tmp_path, fast, BOTH)

    outcomes = stage(tmp_path)

    assert outcomes[IPCA].action == "failed"
    assert "['valor'] not found" in outcomes[IPCA].detail
    assert {action for key, action in actions(outcomes).items() if key != IPCA} == {"staged"}
    assert state(tmp_path)[IPCA] == {
        "source": "bcb_sgs",
        "details": manifest(tmp_path)[IPCA]["details"],
        "raw_sha256": manifest(tmp_path)[IPCA]["sha256"],
        "staging_version": 1,
        "staged_at": "2026-11-26T13:00:00+00:00",
        "staged_path": None,
        "quarantine_path": None,
        "rows_read": 0,
        "rows_staged": 0,
        "rows_quarantined": 0,
        "rule_failures": {},
        "rule_warnings": {},
        "error": outcomes[IPCA].detail,
    }
    assert not (tmp_path / IPCA_STAGED).exists()
    assert not (tmp_path / IPCA_QUARANTINE).exists()


def test_schema_drift_in_an_anp_file_is_reported_the_same_way(web, tmp_path, fast, fake_anp):
    header = "Regiao - Sigla;Estado - Sigla;Preco"
    web.files[OCTOBER_LPG] = f"{header}\r\nSE;SP;109,99\r\n".encode()
    collect_raw(web, tmp_path, fast, BOTH)

    outcomes = stage(tmp_path)

    assert outcomes[OCTOBER_LPG].action == "failed"
    assert outcomes[OCTOBER_LPG].detail == f"{OCTOBER_LPG}: no price column"
    assert outcomes[OCTOBER_DIESEL].action == "staged"


def test_file_that_starts_to_drift_loses_its_outputs(web, tmp_path, fast):
    web.sgs[(433, 2026)] = ONE_BAD_VALUE
    collect_raw(web, tmp_path, fast)
    stage(tmp_path)
    assert (tmp_path / IPCA_STAGED).exists() and (tmp_path / IPCA_QUARANTINE).exists()
    web.sgs[(433, 2026)] = RENAMED_FIELD
    collect_raw(web, tmp_path, fast)

    outcomes = stage(tmp_path)

    # The files on disk described the previous content, which is no longer the current one.
    assert outcomes[IPCA].action == "failed"
    assert not (tmp_path / IPCA_STAGED).exists()
    assert not (tmp_path / IPCA_QUARANTINE).exists()
    assert (tmp_path / state(tmp_path)[DOLLAR]["staged_path"]).exists()


def test_failure_is_reported_on_every_run_without_rewriting_the_state(web, tmp_path, fast):
    web.sgs[(433, 2026)] = RENAMED_FIELD
    collect_raw(web, tmp_path, fast)
    first = stage(tmp_path)
    before = snapshot(tmp_path)

    second = stage(tmp_path, at=STAGED_AT + timedelta(days=7))

    assert second[IPCA] == first[IPCA]
    assert second[IPCA].action == "failed"
    assert snapshot(tmp_path) == before


def test_failed_file_is_staged_once_the_source_is_fixed(web, tmp_path, fast):
    web.sgs[(433, 2026)] = RENAMED_FIELD
    collect_raw(web, tmp_path, fast)
    stage(tmp_path)
    web.sgs[(433, 2026)] = b'[{"data":"01/01/2026","valor":"0.16"}]'
    collect_raw(web, tmp_path, fast)

    outcomes = stage(tmp_path)

    assert actions(outcomes) == {DOLLAR: "unchanged", IPCA: "staged"}
    assert state(tmp_path)[IPCA]["error"] is None
    assert [row["value"] for row in rows(tmp_path, IPCA_STAGED)] == [0.16]


def test_missing_raw_file_fails_that_file_and_is_staged_once_it_is_back(web, tmp_path, fast):
    collect_raw(web, tmp_path, fast)
    raw_file = tmp_path / IPCA_RAW
    content = raw_file.read_bytes()
    raw_file.unlink()

    outcomes = stage(tmp_path)

    assert actions(outcomes) == {DOLLAR: "staged", IPCA: "failed"}
    assert outcomes[IPCA].detail == f"raw file is missing: {IPCA_RAW}; run the collection again"
    assert state(tmp_path)[IPCA]["error"] == outcomes[IPCA].detail

    raw_file.write_bytes(content)
    assert actions(stage(tmp_path)) == {DOLLAR: "unchanged", IPCA: "staged"}
    assert state(tmp_path)[IPCA]["error"] is None


# --- the state follows the manifest --------------------------------------------------------------


def test_record_removed_from_the_manifest_is_dropped_with_its_outputs(web, tmp_path, fast):
    web.sgs[(433, 2026)] = ONE_BAD_VALUE
    collect_raw(web, tmp_path, fast)
    stage(tmp_path)
    document = json.loads(manifest_path(tmp_path).read_text(encoding="utf-8"))
    del document["files"][IPCA]
    manifest_path(tmp_path).write_text(json.dumps(document), encoding="utf-8")

    outcomes = stage(tmp_path)

    assert actions(outcomes) == {DOLLAR: "unchanged"}
    assert set(state(tmp_path)) == {DOLLAR}
    assert not (tmp_path / IPCA_STAGED).exists()
    assert not (tmp_path / IPCA_QUARANTINE).exists()
    assert (tmp_path / state(tmp_path)[DOLLAR]["staged_path"]).exists()


def test_outputs_follow_a_raw_file_that_the_collector_filed_elsewhere(web, tmp_path, fast):
    collect_raw(web, tmp_path, fast)
    stage(tmp_path)
    moved = "raw/bcb_sgs/series/series=433/year=2026/republished.parquet"
    (tmp_path / IPCA_RAW).rename(tmp_path / moved)
    document = json.loads(manifest_path(tmp_path).read_text(encoding="utf-8"))
    document["files"][IPCA].update(raw_path=moved, sha256="cd" * 32)
    manifest_path(tmp_path).write_text(json.dumps(document), encoding="utf-8")

    stage(tmp_path)

    assert state(tmp_path)[IPCA]["staged_path"] == moved.replace("raw/", "staged/", 1)
    assert parquet_files(tmp_path, "staged") == sorted(
        [state(tmp_path)[DOLLAR]["staged_path"], state(tmp_path)[IPCA]["staged_path"]]
    )


def test_unsupported_state_version_is_refused(web, tmp_path, fast):
    collect_raw(web, tmp_path, fast)
    staging_state_path(tmp_path).write_text('{"version": 99, "files": {}}', encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported staging state version 99"):
        stage(tmp_path)


# --- nothing is lost -----------------------------------------------------------------------------


def test_rows_read_are_rows_staged_plus_rows_quarantined_everywhere(web, tmp_path, fast, fake_anp):
    web.files[OCTOBER_DIESEL] = anp_csv("6,01", "sem preço", "6,03")  # one row rejected
    web.files[OCTOBER_LPG] = anp_csv("?")  # every row rejected
    web.sgs[(1, 2026)] = ONE_BAD_VALUE
    web.sgs[(433, 2026)] = RENAMED_FIELD  # the whole file fails
    collect_raw(web, tmp_path, fast, BOTH)

    outcomes = stage(tmp_path)

    entries = state(tmp_path)
    assert len(entries) == 6
    for key, entry in entries.items():
        staged = len(rows(tmp_path, entry["staged_path"])) if entry["staged_path"] else 0
        quarantined = (
            len(rows(tmp_path, entry["quarantine_path"])) if entry["quarantine_path"] else 0
        )
        assert (entry["rows_staged"], entry["rows_quarantined"]) == (staged, quarantined), key
        assert entry["rows_read"] == staged + quarantined, key
        assert outcomes[key].rows_read == outcomes[key].rows_staged + outcomes[key].rows_quarantined
        if entry["error"] is None:
            assert entry["rows_read"] == manifest(tmp_path)[key]["rows"], key
    assert sum(entry["rows_quarantined"] for entry in entries.values()) == 3
    listed = [
        path
        for entry in entries.values()
        for path in (entry["staged_path"], entry["quarantine_path"])
        if path
    ]
    on_disk = parquet_files(tmp_path, "staged") + parquet_files(tmp_path, "quarantine")
    assert sorted(on_disk) == sorted(listed)  # the state lists every output file, and only those


def test_stager_that_loses_rows_is_a_bug_and_stops_the_run(web, tmp_path, fast, monkeypatch):
    class LossyStager(FakePriceStager):
        def stage(self, raw: pd.DataFrame, context: FileContext, *, now: Clock) -> StagedFrames:
            frames = super().stage(raw, context, now=now)
            return replace(frames, quarantine=frames.quarantine.head(0))

    monkeypatch.setitem(driver.STAGERS, "anp", LossyStager)
    web.files[OCTOBER_DIESEL] = anp_csv("6,01", "sem preço")  # the first file in manifest order
    collect_raw(web, tmp_path, fast, ANP)

    with pytest.raises(RuntimeError, match="2 rows read, but 1 staged and 0 quarantined"):
        stage(tmp_path)

    assert state(tmp_path) == {}
    assert parquet_files(tmp_path, "staged") == []


def test_state_is_saved_after_each_file(web, tmp_path, fast, monkeypatch):
    """A crash in the middle keeps the files already finished."""

    class Crash(BaseException):
        pass

    real_stage = bcb_sgs.stage

    def crash_on_ipca(raw: pd.DataFrame, context: FileContext, *, now: Clock) -> StagedFrames:
        if context.key == IPCA:
            raise Crash
        return real_stage(raw, context, now=now)

    collect_raw(web, tmp_path, fast)
    with monkeypatch.context() as patch:
        patch.setattr(bcb_sgs, "stage", crash_on_ipca)
        with pytest.raises(Crash):
            stage(tmp_path)

    assert set(state(tmp_path)) == {DOLLAR}

    assert actions(stage(tmp_path)) == {DOLLAR: "unchanged", IPCA: "staged"}
