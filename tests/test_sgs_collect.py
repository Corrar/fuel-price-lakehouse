import json
from datetime import date, datetime, timedelta

import httpx
import pytest

from fuel_price_lakehouse import http
from fuel_price_lakehouse.layout import manifest_path
from fuel_price_lakehouse.manifest import Manifest
from fuel_price_lakehouse.report import Status
from fuel_price_lakehouse.sgs import collect as sgs
from tests.conftest import no_sleep

DAILY = sgs.Series(1, "usd_brl_sell_rate", settle_days=30)
MONTHLY = sgs.Series(433, "ipca_monthly_change", settle_days=100)

EMPTY_WINDOW = httpx.Response(
    404,
    json={"erro": {"statusCode": 404, "detail": "SGSNegocioException: Value(s) not found"}},
)


class FakeSgs:
    """Answers like the API: weekdays only for the daily series, 404 for an empty window."""

    def __init__(self, last_published: date) -> None:
        self.last_published = last_published
        self.requests: list[tuple[int, int]] = []
        self.revised: dict[date, str] = {}
        self.failing_years: set[int] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        code = int(request.url.path.split("bcdata.sgs.")[1].split("/")[0])
        start = datetime.strptime(request.url.params["dataInicial"], "%d/%m/%Y").date()
        end = datetime.strptime(request.url.params["dataFinal"], "%d/%m/%Y").date()
        assert request.url.params["formato"] == "json"
        assert (start.month, start.day, end.month, end.day) == (1, 1, 12, 31)
        assert start.year == end.year  # always far below the API's ten-year limit
        self.requests.append((code, start.year))
        if start.year in self.failing_years:
            return httpx.Response(503)
        rows = []
        day = start
        while day <= min(end, self.last_published):
            if day.weekday() < 5 if code == 1 else day.day == 1:
                value = self.revised.get(day, "5.1234" if code == 1 else "0.14")
                rows.append({"data": day.strftime("%d/%m/%Y"), "valor": value})
            day += timedelta(days=1)
        return httpx.Response(200, json=rows) if rows else EMPTY_WINDOW


def run(api, tmp_path, policy, *, since, today, series=(DAILY,), **options):
    if isinstance(api, FakeSgs):
        api.last_published = today
    manifest = Manifest.load(manifest_path(tmp_path))
    client = httpx.Client(transport=httpx.MockTransport(api))
    report = sgs.collect(
        client,
        manifest,
        tmp_path,
        since=since,
        today=today,
        policy=policy,
        series=series,
        sleep=no_sleep,
        **options,
    )
    return report, Manifest.load(manifest_path(tmp_path))


@pytest.fixture
def api() -> FakeSgs:
    return FakeSgs(date(2026, 11, 26))


def test_everything_is_requested_when_nothing_was_collected():
    years = sgs.years_to_request(set(), DAILY, date(2022, 6, 1), date(2026, 11, 26))

    assert years == [2022, 2023, 2024, 2025, 2026]


def test_only_the_current_year_is_requested_once_the_past_is_collected():
    collected = {2022, 2023, 2024, 2025, 2026}

    assert sgs.years_to_request(collected, DAILY, date(2022, 1, 1), date(2026, 11, 26)) == [2026]


def test_the_previous_year_stays_open_for_a_while_after_it_ends():
    collected = {2025, 2026}

    assert sgs.years_to_request(collected, DAILY, date(2025, 1, 1), date(2027, 1, 20)) == [
        2026,
        2027,
    ]
    assert sgs.years_to_request(collected, DAILY, date(2025, 1, 1), date(2027, 2, 15)) == [2027]
    # the monthly series is published later, so it is given longer
    assert sgs.years_to_request(collected, MONTHLY, date(2025, 1, 1), date(2027, 2, 15)) == [
        2026,
        2027,
    ]


def test_a_year_missing_in_the_middle_is_requested_again():
    collected = {2022, 2024, 2025, 2026}

    assert sgs.years_to_request(collected, DAILY, date(2022, 1, 1), date(2026, 11, 26)) == [
        2023,
        2026,
    ]


def test_force_requests_every_year():
    collected = {2024, 2025, 2026}

    years = sgs.years_to_request(collected, DAILY, date(2024, 1, 1), date(2026, 11, 26), force=True)

    assert years == [2024, 2025, 2026]


def test_first_run_stores_each_response_exactly_as_received(api, tmp_path, policy):
    report, manifest = run(api, tmp_path, policy, since=date(2025, 1, 1), today=date(2026, 11, 26))

    assert report.count(Status.DOWNLOADED) == 2
    assert [e.period for e in manifest.entries] == ["2025", "2026"]
    entry = manifest.entries[1]
    assert entry.dataset == "sgs_1"
    assert entry.raw_path == f"raw/bcb_sgs/sgs_1/year=2026/sha256={entry.sha256[:12]}/data.json"
    stored = json.loads((tmp_path / entry.raw_path).read_bytes())
    assert stored[0] == {"data": "01/01/2026", "valor": "5.1234"}  # values stay strings
    assert entry.details["first_observation"] == "2026-01-01"
    assert entry.details["last_observation"] == "2026-11-26"
    assert entry.details["observations"] == len(stored)


def test_next_run_asks_only_for_the_open_year_and_stores_the_new_version(api, tmp_path, policy):
    run(api, tmp_path, policy, since=date(2022, 1, 1), today=date(2026, 11, 20))
    api.requests.clear()

    report, manifest = run(api, tmp_path, policy, since=date(2022, 1, 1), today=date(2026, 11, 27))

    assert api.requests == [(1, 2026)]
    assert report.count(Status.DOWNLOADED) == 1
    versions = [e for e in manifest.entries if e.period == "2026"]
    assert [v.details["last_observation"] for v in versions] == ["2026-11-20", "2026-11-27"]
    assert versions[1].details["replaces"] == versions[0].sha256
    assert all((tmp_path / v.raw_path).exists() for v in versions)  # nothing overwritten


def test_nothing_is_stored_again_while_the_source_has_not_changed(api, tmp_path, policy):
    """The monthly series stays the same for weeks; later runs must not pile up copies."""
    options = {"since": date(2026, 1, 1), "series": (MONTHLY,)}
    run(api, tmp_path, policy, today=date(2026, 11, 2), **options)

    report, manifest = run(api, tmp_path, policy, today=date(2026, 11, 26), **options)

    assert report.count(Status.UNCHANGED) == 1
    assert len(manifest.entries) == 1


def test_a_revised_value_in_the_open_year_is_captured(api, tmp_path, policy):
    run(api, tmp_path, policy, since=date(2026, 1, 1), today=date(2026, 11, 20))
    api.revised[date(2026, 3, 18)] = "5.9999"

    _, manifest = run(api, tmp_path, policy, since=date(2026, 1, 1), today=date(2026, 11, 20))

    latest = json.loads((tmp_path / manifest.entries[-1].raw_path).read_bytes())
    assert {"data": "18/03/2026", "valor": "5.9999"} in latest
    assert len(manifest.entries) == 2


def test_a_year_that_failed_is_fetched_by_the_next_run(api, tmp_path, policy):
    """A failure in the middle of the history must not leave a permanent hole."""
    api.failing_years.add(2023)
    report, manifest = run(api, tmp_path, policy, since=date(2022, 1, 1), today=date(2026, 11, 26))
    assert report.failed
    assert [e.period for e in manifest.entries] == ["2022", "2024", "2025", "2026"]
    api.failing_years.clear()
    api.requests.clear()

    report, manifest = run(api, tmp_path, policy, since=date(2022, 1, 1), today=date(2026, 11, 26))

    assert api.requests == [(1, 2023), (1, 2026)]
    assert not report.failed
    assert sorted(e.period for e in manifest.entries) == ["2022", "2023", "2024", "2025", "2026"]


def test_moving_the_start_earlier_backfills_only_the_missing_years(api, tmp_path, policy):
    run(api, tmp_path, policy, since=date(2024, 1, 1), today=date(2026, 11, 26))
    api.requests.clear()

    run(api, tmp_path, policy, since=date(2022, 1, 1), today=date(2026, 11, 26))

    assert api.requests == [(1, 2022), (1, 2023), (1, 2026)]


def test_a_year_without_observations_yet_is_not_an_error(tmp_path, policy):
    api = FakeSgs(date(2026, 12, 31))

    def run_on_new_years_day():
        manifest = Manifest.load(manifest_path(tmp_path))
        client = httpx.Client(transport=httpx.MockTransport(api))
        return sgs.collect(
            client,
            manifest,
            tmp_path,
            since=date(2027, 1, 1),
            today=date(2027, 1, 1),
            policy=policy,
            series=(DAILY,),
            sleep=no_sleep,
        )

    report = run_on_new_years_day()

    assert report.count(Status.EMPTY) == 1
    assert not report.failed
    assert Manifest.load(manifest_path(tmp_path)).entries == ()


def test_an_unknown_series_is_an_error_even_though_it_is_also_a_404(tmp_path, policy):
    def handler(request):
        return httpx.Response(
            404, text="A serie nao existe!", headers={"Content-Type": "text/plain; charset=utf-8"}
        )

    report, _ = run(handler, tmp_path, policy, since=date(2026, 1, 1), today=date(2026, 11, 26))

    assert report.failed
    assert "404" in report.outcomes[0].detail


def test_each_series_keeps_its_own_history(api, tmp_path, policy):
    _, manifest = run(
        api,
        tmp_path,
        policy,
        since=date(2026, 1, 1),
        today=date(2026, 11, 26),
        series=(DAILY, MONTHLY),
    )

    assert api.requests == [(1, 2026), (433, 2026)]
    monthly = manifest.for_dataset("bcb_sgs", "sgs_433")[0]
    assert monthly.details["last_observation"] == "2026-11-01"
    assert monthly.details["observations"] == 11


def test_dry_run_makes_no_request(api, tmp_path, policy):
    report, manifest = run(
        api, tmp_path, policy, since=date(2025, 1, 1), today=date(2026, 11, 26), dry_run=True
    )

    assert report.count(Status.PLANNED) == 2
    assert api.requests == []
    assert manifest.entries == ()


@pytest.mark.parametrize(
    "body",
    [
        b"<html>manutencao</html>",
        b'{"erro": "algo"}',
        b'[{"data": "01/11/2026"}]',
        b'[{"data": "2026-11-01", "valor": "1"}]',
    ],
)
def test_a_response_with_an_unexpected_shape_is_rejected(body):
    with pytest.raises(http.FetchError):
        sgs.parse_observations(body)
