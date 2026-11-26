"""End-to-end runs of the collection against the fake sources."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest

from fuel_price_lakehouse import cli
from fuel_price_lakehouse.collect.run import run as collect
from fuel_price_lakehouse.layout import manifest_path

from .conftest import (
    FOUR_WEEKS,
    HEADER,
    OCTOBER_DIESEL,
    OCTOBER_GASOLINE,
    OCTOBER_LPG,
    SEMESTER_ZIP,
    FakeWeb,
    anp_csv,
    zipped,
)

SINCE = date(2026, 7, 1)


def run(web: FakeWeb, root: Path, fast: dict, since: date = SINCE, **options):
    settings = {key: fast[key] for key in ("sleep", "retry", "now")}
    with web.client() as client:
        return collect(root, since, client=client, **settings, **options)


def actions(outcomes) -> dict[str, str]:
    return {outcome.key: outcome.action for outcome in outcomes}


def manifest(root: Path) -> dict:
    return json.loads(manifest_path(root).read_text(encoding="utf-8"))["files"]


def snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    """Every file under the data root with its modification time and content."""
    return {
        path.relative_to(root).as_posix(): (path.stat().st_mtime_ns, path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_first_run_collects_everything_selected(web, tmp_path, fast):
    outcomes = run(web, tmp_path, fast)

    assert set(actions(outcomes).values()) == {"new"}
    assert sorted(web.downloads()) == sorted(
        [OCTOBER_DIESEL, OCTOBER_GASOLINE, OCTOBER_LPG, FOUR_WEEKS]
    )
    record = manifest(tmp_path)[OCTOBER_DIESEL]
    content = web.files[OCTOBER_DIESEL]
    assert record["status"] == "ok"
    assert record["size_bytes"] == len(content)
    assert len(record["sha256"]) == 64
    assert record["rows"] == 2
    assert record["collected_at"] == "2026-11-26T12:00:00+00:00"
    assert record["revision"] == 1
    assert (tmp_path / record["raw_path"]).is_file()
    assert record["raw_path"].startswith(
        "raw/anp/fuel_prices/kind=monthly/fuel_group=diesel_cng/period=2026-10/"
    )
    assert not list(tmp_path.rglob("*.part")) + list(tmp_path.rglob("*.tmp"))
    assert not list((tmp_path / "tmp").rglob("*.*"))  # downloads are not kept


def test_raw_layer_is_a_faithful_text_copy(web, tmp_path, fast):
    run(web, tmp_path, fast)
    record = manifest(tmp_path)[OCTOBER_DIESEL]
    table = pq.read_table(tmp_path / record["raw_path"])

    source_columns = HEADER.split(";")
    assert table.column_names[:16] == source_columns  # BOM gone, names untouched
    assert all(str(table.schema.field(name).type) == "string" for name in source_columns)
    row = table.to_pylist()[0]
    assert row["CNPJ da Revenda"] == " 01.492.748/0003-83"  # leading space kept
    assert row["Complemento"] == "LOTE: 04;"  # quoted separator is data, not a new field
    assert row["Valor de Venda"] == "6,01"  # decimal comma kept, still text
    assert row["Valor de Compra"] == ""  # empty stays empty, not null
    assert row["Data da Coleta"] == "05/10/2026"
    # tracking columns
    assert row["_source_url"] == OCTOBER_DIESEL
    assert row["_source_sha256"] == record["sha256"]
    assert row["_collected_at"] == datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    assert table.column("_row_number").to_pylist() == [1, 2]
    assert table.column("_field_count").to_pylist() == [16, 16]


def test_second_run_downloads_and_writes_nothing(web, tmp_path, fast):
    run(web, tmp_path, fast)
    before = snapshot(tmp_path)
    web.requests.clear()

    outcomes = run(web, tmp_path, fast)

    assert set(actions(outcomes).values()) == {"unchanged"}
    assert web.downloads() == []
    assert web.count("probe") == 4  # one cheap size check per file
    assert snapshot(tmp_path) == before  # manifest and Parquet untouched, byte for byte


def test_republished_file_is_collected_again(web, tmp_path, fast):
    run(web, tmp_path, fast)
    first = manifest(tmp_path)[OCTOBER_DIESEL]
    web.files[OCTOBER_DIESEL] = anp_csv("6,01", "6,02", "6,03")  # ANP adds a row
    web.requests.clear()

    outcomes = run(web, tmp_path, fast)

    assert actions(outcomes)[OCTOBER_DIESEL] == "updated"
    assert web.downloads() == [OCTOBER_DIESEL]  # only the file that changed
    second = manifest(tmp_path)[OCTOBER_DIESEL]
    assert second["revision"] == 2
    assert second["sha256"] != first["sha256"]
    assert second["raw_path"] == first["raw_path"]  # replaced in place, not duplicated
    table = pq.read_table(tmp_path / second["raw_path"])
    assert table.column("Valor de Venda").to_pylist() == ["6,01", "6,02", "6,03"]
    assert set(table.column("_source_sha256").to_pylist()) == {second["sha256"]}


def test_four_week_file_with_same_size_is_caught_by_the_page_date(web, tmp_path, fast):
    run(web, tmp_path, fast)
    web.files[FOUR_WEEKS] = anp_csv("6,31", "6,41", "6,51")  # new week, same byte count
    web.updated = "30/11/2026"
    web.requests.clear()

    outcomes = run(web, tmp_path, fast)

    assert actions(outcomes)[FOUR_WEEKS] == "updated"
    assert web.downloads() == [FOUR_WEEKS]


def test_new_page_date_with_identical_content_does_not_rewrite(web, tmp_path, fast):
    run(web, tmp_path, fast)
    raw = tmp_path / manifest(tmp_path)[FOUR_WEEKS]["raw_path"]
    written = raw.stat().st_mtime_ns
    web.updated = "30/11/2026"

    outcomes = run(web, tmp_path, fast)
    assert actions(outcomes)[FOUR_WEEKS] == "unchanged"
    assert raw.stat().st_mtime_ns == written
    assert manifest(tmp_path)[FOUR_WEEKS]["revision"] == 1

    web.requests.clear()
    run(web, tmp_path, fast)  # the hint was remembered: no third download
    assert web.downloads() == []


def test_network_failure_is_retried_with_growing_waits(web, tmp_path, fast):
    web.failures[OCTOBER_DIESEL] = [httpx.ConnectError("reset"), 503]

    outcomes = run(web, tmp_path, fast, sources=["anp"])

    assert actions(outcomes)[OCTOBER_DIESEL] == "new"
    backoff = [wait for wait in fast["waits"] if wait != 1.0]  # 1.0 is the polite pause
    assert backoff == [2.0, 4.0]


def test_truncated_download_is_retried(web, tmp_path, fast):
    web.truncate.add(OCTOBER_LPG)

    outcomes = run(web, tmp_path, fast)

    assert actions(outcomes)[OCTOBER_LPG] == "new"
    assert web.downloads().count(OCTOBER_LPG) == 2
    assert manifest(tmp_path)[OCTOBER_LPG]["size_bytes"] == len(web.files[OCTOBER_LPG])


def test_run_survives_a_file_that_keeps_failing_and_resumes_later(web, tmp_path, fast):
    web.failures[OCTOBER_GASOLINE] = [httpx.ReadTimeout("slow")] * 3  # every attempt allowed

    outcomes = run(web, tmp_path, fast)

    got = actions(outcomes)
    assert got[OCTOBER_GASOLINE] == "failed"
    assert got[OCTOBER_DIESEL] == got[OCTOBER_LPG] == got[FOUR_WEEKS] == "new"  # others went on
    failed = manifest(tmp_path)[OCTOBER_GASOLINE]
    assert failed["status"] == "failed"
    assert "ReadTimeout" in failed["last_error"]
    assert failed["sha256"] is None

    web.requests.clear()
    outcomes = run(web, tmp_path, fast)  # the source is back

    assert actions(outcomes)[OCTOBER_GASOLINE] == "new"
    assert web.downloads() == [OCTOBER_GASOLINE]  # only what was missing
    assert manifest(tmp_path)[OCTOBER_GASOLINE]["status"] == "ok"


def test_manifest_is_saved_after_each_file(web, tmp_path, fast):
    """A crash in the middle keeps the files already finished."""

    class Crash(BaseException):
        pass

    web.failures[OCTOBER_LPG] = [Crash()]
    with pytest.raises(Crash):
        run(web, tmp_path, fast)

    done = manifest(tmp_path)
    assert done[OCTOBER_DIESEL]["status"] == done[OCTOBER_GASOLINE]["status"] == "ok"
    assert OCTOBER_LPG not in done

    web.requests.clear()
    run(web, tmp_path, fast)
    assert sorted(web.downloads()) == sorted([OCTOBER_LPG, FOUR_WEEKS])


def test_corrupt_zip_is_reported_and_leaves_no_partial_file(web, tmp_path, fast):
    good = web.files[SEMESTER_ZIP]
    web.files[SEMESTER_ZIP] = good[: len(good) // 2]  # cut in half, served "successfully"

    outcomes = run(web, tmp_path, fast, since=date(2026, 1, 1))

    assert actions(outcomes)[SEMESTER_ZIP] == "failed"
    record = manifest(tmp_path)[SEMESTER_ZIP]
    assert record["status"] == "failed"
    assert record["raw_path"] is None
    assert not list(tmp_path.rglob("kind=semester/fuel_group=automotive/**/*.parquet*"))

    web.files[SEMESTER_ZIP] = good
    outcomes = run(web, tmp_path, fast, since=date(2026, 1, 1))
    assert actions(outcomes)[SEMESTER_ZIP] == "new"
    assert manifest(tmp_path)[SEMESTER_ZIP]["rows"] == 3


def test_corrupt_republication_keeps_the_previous_good_version(web, tmp_path, fast):
    run(web, tmp_path, fast)
    good = manifest(tmp_path)[OCTOBER_DIESEL]
    web.files[OCTOBER_DIESEL] = b"<html><body>Erro interno</body></html>"

    outcomes = run(web, tmp_path, fast)

    assert actions(outcomes)[OCTOBER_DIESEL] == "failed"
    record = manifest(tmp_path)[OCTOBER_DIESEL]
    assert record["status"] == "ok"
    assert record["sha256"] == good["sha256"]
    assert "no CSV header" in record["last_error"]
    assert pq.read_table(tmp_path / record["raw_path"]).num_rows == 2


def test_zip_member_is_read_by_position_whatever_its_name(web, tmp_path, fast):
    web.files[SEMESTER_ZIP] = zipped(anp_csv("5,99"), name="qualquer coisa.dat")
    run(web, tmp_path, fast, since=date(2026, 1, 1))
    assert manifest(tmp_path)[SEMESTER_ZIP]["rows"] == 1


def test_rows_with_unexpected_field_counts_are_kept_lossless(web, tmp_path, fast):
    body = ("﻿" + HEADER + "\r\nSE;SP\r\n" + ";".join(["x"] * 18) + "\r\n").encode()
    web.files[OCTOBER_LPG] = body
    run(web, tmp_path, fast)

    rows = pq.read_table(tmp_path / manifest(tmp_path)[OCTOBER_LPG]["raw_path"]).to_pylist()
    assert rows[0]["_field_count"] == 2
    assert rows[0]["Municipio"] is None  # missing, as opposed to present and empty
    assert rows[1]["_field_count"] == 18
    assert json.loads(rows[1]["_extra_fields"]) == ["x", "x"]


def test_dry_run_reports_sizes_and_writes_nothing(web, tmp_path, fast):
    outcomes = run(web, tmp_path, fast, dry_run=True)

    planned = {o.key: o.size_bytes for o in outcomes if o.key.startswith("https://www.gov.br")}
    assert planned[OCTOBER_DIESEL] == len(web.files[OCTOBER_DIESEL])
    assert web.downloads() == []
    assert not list(tmp_path.rglob("*"))


def test_full_check_downloads_again_but_rewrites_nothing(web, tmp_path, fast):
    run(web, tmp_path, fast)
    before = snapshot(tmp_path)
    web.requests.clear()

    outcomes = run(web, tmp_path, fast, full_check=True)

    assert set(actions(outcomes).values()) == {"unchanged"}
    assert len(web.downloads()) == 4
    assert snapshot(tmp_path) == before


# --- Banco Central SGS -------------------------------------------------------------------------


def test_sgs_values_stay_text_and_windows_are_calendar_years(web, tmp_path, fast):
    web.sgs[(1, 2025)] = b'[{"data":"31/12/2025","valor":"5.3000"}]'
    run(web, tmp_path, fast, since=date(2025, 6, 1), sources=["bcb_sgs"])

    urls = [url for kind, url in web.requests if kind == "sgs" and "sgs.1/" in url]
    assert urls == [
        "https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados?formato=json"
        f"&dataInicial=01/01/{year}&dataFinal=31/12/{year}"
        for year in (2025, 2026)
    ]
    record = manifest(tmp_path)["bcb_sgs/1/2026"]
    assert record["raw_path"] == "raw/bcb_sgs/series/series=1/year=2026/data.parquet"
    rows = pq.read_table(tmp_path / record["raw_path"]).to_pylist()
    assert [(r["data"], r["valor"]) for r in rows] == [
        ("02/01/2026", "5.4321"),
        ("05/01/2026", "5.5000"),
    ]
    assert rows[0]["_source_url"] == urls[1]


def test_sgs_empty_window_is_not_an_error(web, tmp_path, fast):
    # 433 has nothing for 2025 in the fake API: the real one answers 404 for that.
    outcomes = run(web, tmp_path, fast, since=date(2025, 1, 1), sources=["bcb_sgs"])

    assert "failed" not in actions(outcomes).values()
    record = manifest(tmp_path)["bcb_sgs/433/2025"]
    assert (record["status"], record["rows"], record["raw_path"]) == ("ok", 0, None)


def test_sgs_unknown_series_is_not_mistaken_for_empty_windows(web, tmp_path, fast):
    del web.sgs[(433, 2026)]  # every window of 433 now answers 404, like a wrong code
    outcomes = run(web, tmp_path, fast, sources=["bcb_sgs"])

    assert actions(outcomes)["bcb_sgs/433"] == "failed"
    assert actions(outcomes)["bcb_sgs/1/2026"] == "new"


def test_sgs_second_run_revalidates_with_etag_and_writes_nothing(web, tmp_path, fast):
    run(web, tmp_path, fast, sources=["bcb_sgs"])
    before = snapshot(tmp_path)

    outcomes = run(web, tmp_path, fast, sources=["bcb_sgs"])

    assert set(actions(outcomes).values()) == {"unchanged"}
    assert snapshot(tmp_path) == before


def test_sgs_new_observation_updates_the_open_window(web, tmp_path, fast):
    run(web, tmp_path, fast, sources=["bcb_sgs"])
    web.sgs[(433, 2026)] = (
        b'[{"data":"01/01/2026","valor":"0.16"},{"data":"01/02/2026","valor":"0.31"}]'
    )

    outcomes = run(web, tmp_path, fast, sources=["bcb_sgs"])

    assert actions(outcomes) == {"bcb_sgs/1/2026": "unchanged", "bcb_sgs/433/2026": "updated"}
    assert manifest(tmp_path)["bcb_sgs/433/2026"]["rows"] == 2


def test_sgs_finished_year_is_requested_again_only_until_it_settles(web, tmp_path, fast):
    web.sgs[(1, 2025)] = b'[{"data":"31/12/2025","valor":"5.3000"}]'
    web.sgs[(433, 2025)] = b'[{"data":"01/11/2025","valor":"0.20"}]'
    early = dict(fast, now=lambda: datetime(2026, 1, 5, tzinfo=UTC))  # December IPCA not out yet
    run(web, tmp_path, early, since=date(2025, 1, 1), sources=["bcb_sgs"])

    web.requests.clear()
    run(web, tmp_path, fast, since=date(2025, 1, 1), sources=["bcb_sgs"])  # November 2026
    assert sum("01/01/2025" in url for _, url in web.requests) == 2  # 2025 checked once more

    web.requests.clear()
    later = dict(fast, now=lambda: fast["now"]() + timedelta(days=7))
    run(web, tmp_path, later, since=date(2025, 1, 1), sources=["bcb_sgs"])
    assert sum("01/01/2025" in url for _, url in web.requests) == 0  # settled: never again


def test_sgs_malformed_body_is_a_failure_not_a_raw_file(web, tmp_path, fast):
    web.sgs[(1, 2026)] = b"<html>Service Unavailable</html>"
    outcomes = run(web, tmp_path, fast, sources=["bcb_sgs"])

    assert actions(outcomes)["bcb_sgs/1/2026"] == "failed"
    assert not (tmp_path / "raw/bcb_sgs/series/series=1").exists()


# --- command line --------------------------------------------------------------------------------


def test_since_must_be_year_month():
    args = cli.build_parser().parse_args(["extract", "--since", "2026-07"])
    assert args.since == date(2026, 7, 1)
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["extract", "--since", "07/2026"])
