import hashlib
from datetime import date

import httpx
import pytest

from fuel_price_lakehouse import http
from fuel_price_lakehouse.anp import collect as anp
from fuel_price_lakehouse.anp.discovery import LANDING_URL
from fuel_price_lakehouse.layout import manifest_path
from fuel_price_lakehouse.manifest import Manifest
from fuel_price_lakehouse.report import Status
from tests.conftest import no_sleep

SINCE = date(2026, 10, 1)
"""With the fixture page this selects the 2026-10 monthly files and the rolling files."""


class FakeAnp:
    """An in-memory ANP site: a landing page plus file bodies keyed by file name."""

    def __init__(self, landing_html: str) -> None:
        self.landing_html = landing_html
        self.bodies: dict[str, bytes] = {}
        self.failing: set[str] = set()
        self.downloads: list[str] = []
        self.probes: list[str] = []

    def body(self, name: str) -> bytes:
        return self.bodies.get(name, f"content of {name}".encode())

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == LANDING_URL:
            return httpx.Response(200, text=self.landing_html)
        name = request.url.path.rsplit("/", 1)[-1]
        if name in self.failing:
            return httpx.Response(503)
        body = self.body(name)
        if request.headers.get("Range") == "bytes=0-0":
            self.probes.append(name)
            return httpx.Response(
                206, content=body[:1], headers={"Content-Range": f"bytes 0-0/{len(body)}"}
            )
        self.downloads.append(name)
        return httpx.Response(200, content=body, headers={"Content-Type": "text/csv"})


@pytest.fixture
def site(landing_html) -> FakeAnp:
    return FakeAnp(landing_html)


def run(site, tmp_path, policy, **options):
    manifest = Manifest.load(manifest_path(tmp_path))
    client = httpx.Client(transport=httpx.MockTransport(site))
    report = anp.collect(
        client, manifest, tmp_path, since=SINCE, policy=policy, sleep=no_sleep, **options
    )
    return report, Manifest.load(manifest_path(tmp_path))


def test_first_run_downloads_every_selected_file_into_the_raw_layer(site, tmp_path, policy):
    report, manifest = run(site, tmp_path, policy)

    assert report.count(Status.DOWNLOADED) == 6
    assert not report.failed
    assert len(manifest.entries) == 6
    for entry in manifest.entries:
        raw = tmp_path / entry.raw_path
        assert raw.read_bytes() == site.body(entry.filename)
        assert hashlib.sha256(raw.read_bytes()).hexdigest() == entry.sha256
        assert entry.size_bytes == raw.stat().st_size
    assert not list((tmp_path / "tmp").glob("*"))


def test_raw_path_is_partitioned_by_grain_period_and_content(site, tmp_path, policy):
    _, manifest = run(site, tmp_path, policy)

    entry = manifest.latest(
        "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/"
        "arquivos/shpc/dsan/2026/10-glp.csv"
    )
    assert entry.raw_path == (
        f"raw/anp/lpg/grain=month/period=2026-10/sha256={entry.sha256[:12]}/10-glp.csv"
    )


def test_second_run_downloads_only_the_rolling_files_and_records_nothing(site, tmp_path, policy):
    run(site, tmp_path, policy)
    site.downloads.clear()

    report, manifest = run(site, tmp_path, policy)

    assert report.count(Status.UNCHANGED) == 6
    assert report.count(Status.DOWNLOADED) == 0
    assert sorted(site.downloads) == [
        "ultimas-4-semanas-diesel-gnv.csv",
        "ultimas-4-semanas-gasolina-etanol.csv",
        "ultimas-4-semanas-glp.csv",
    ]
    assert sorted(site.probes) == ["10-diesel-gnv.csv", "10-gasolina-etanol.csv", "10-glp.csv"]
    assert len(manifest.entries) == 6


def test_a_new_week_in_the_rolling_file_is_ingested_as_a_new_version(site, tmp_path, policy):
    run(site, tmp_path, policy)
    site.bodies["ultimas-4-semanas-glp.csv"] = b"a newer week, same file name"

    report, manifest = run(site, tmp_path, policy)

    assert report.count(Status.DOWNLOADED) == 1
    versions = [e for e in manifest.entries if e.filename == "ultimas-4-semanas-glp.csv"]
    assert len(versions) == 2
    assert versions[1].details["replaces"] == versions[0].sha256
    assert all((tmp_path / v.raw_path).exists() for v in versions)  # old raw file is kept


def test_a_republished_monthly_file_is_detected_by_its_size(site, tmp_path, policy):
    run(site, tmp_path, policy)
    site.bodies["10-glp.csv"] = b"republished with corrections and a different size"
    site.downloads.clear()

    report, manifest = run(site, tmp_path, policy)

    assert "10-glp.csv" in site.downloads
    assert report.count(Status.DOWNLOADED) == 1
    assert len([e for e in manifest.entries if e.filename == "10-glp.csv"]) == 2


def test_force_downloads_closed_periods_again_but_does_not_duplicate_them(site, tmp_path, policy):
    run(site, tmp_path, policy)
    site.downloads.clear()

    report, manifest = run(site, tmp_path, policy, force=True)

    assert len(site.downloads) == 6
    assert report.count(Status.UNCHANGED) == 6
    assert len(manifest.entries) == 6


def test_one_failing_file_does_not_stop_the_others(site, tmp_path, policy):
    site.failing.add("10-glp.csv")

    report, manifest = run(site, tmp_path, policy)

    assert report.failed
    assert report.count(Status.FAILED) == 1
    assert report.count(Status.DOWNLOADED) == 5
    assert len(manifest.entries) == 5
    assert not list((tmp_path / "tmp").glob("*"))


def test_a_failed_file_is_picked_up_by_the_next_run(site, tmp_path, policy):
    site.failing.add("10-glp.csv")
    run(site, tmp_path, policy)
    site.failing.clear()

    report, manifest = run(site, tmp_path, policy)

    assert report.count(Status.DOWNLOADED) == 1
    assert len(manifest.entries) == 6


def test_dry_run_plans_without_downloading_or_writing(site, tmp_path, policy):
    report, manifest = run(site, tmp_path, policy, dry_run=True)

    assert report.count(Status.PLANNED) == 6
    assert site.downloads == []
    assert manifest.entries == ()
    assert not (tmp_path / "raw").exists()


def test_an_html_error_page_is_not_stored_as_data(site, tmp_path, policy):
    def handler(request):
        if str(request.url) == LANDING_URL:
            return httpx.Response(200, text=site.landing_html)
        return httpx.Response(200, text="<html>erro</html>", headers={"Content-Type": "text/html"})

    report, manifest = run(handler, tmp_path, policy)

    assert report.count(Status.FAILED) == 6
    assert manifest.entries == ()
    assert not (tmp_path / "raw").exists()


def test_unclassified_links_become_warnings(site, tmp_path, policy):
    report, _ = run(site, tmp_path, policy, dry_run=True)

    assert any("arquivo-novo.csv" in warning for warning in report.warnings)


def test_a_landing_page_without_data_links_aborts_the_run(tmp_path, policy):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html></html>"))
    )

    with pytest.raises(http.FetchError, match="layout changed"):
        anp.collect(
            client,
            Manifest.load(manifest_path(tmp_path)),
            tmp_path,
            since=SINCE,
            policy=policy,
            sleep=no_sleep,
        )
