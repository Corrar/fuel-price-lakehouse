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

DAILY = sgs.Series(1, "usd_brl_sell_rate", daily=True, overlap_days=30)
MONTHLY = sgs.Series(433, "ipca_monthly_change", daily=False, overlap_days=100)

EMPTY_WINDOW = httpx.Response(
    404,
    json={"erro": {"statusCode": 404, "detail": "SGSNegocioException: Value(s) not found"}},
)


class FakeSgs:
    """Answers like the API: weekdays only for daily series, 404 for an empty window."""

    def __init__(self) -> None:
        self.requests: list[tuple[int, date, date]] = []
        self.revised: dict[date, str] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        code = int(request.url.path.split("bcdata.sgs.")[1].split("/")[0])
        start = datetime.strptime(request.url.params["dataInicial"], "%d/%m/%Y").date()
        end = datetime.strptime(request.url.params["dataFinal"], "%d/%m/%Y").date()
        assert request.url.params["formato"] == "json"
        self.requests.append((code, start, end))
        if code == 1 and (end - start).days > 3653:
            return httpx.Response(406, json={"error": "janela de consulta de, no máximo, 10 anos"})
        rows = []
        day = start
        while day <= end:
            keep = day.weekday() < 5 if code == 1 else day.day == 1
            if keep:
                value = self.revised.get(day, "5.1234" if code == 1 else "0.14")
                rows.append({"data": day.strftime("%d/%m/%Y"), "valor": value})
            day += timedelta(days=1)
        return httpx.Response(200, json=rows) if rows else EMPTY_WINDOW


def run(api, tmp_path, policy, *, since, today, series=(DAILY,), **options):
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


def test_daily_windows_never_exceed_ten_years():
    result = sgs.windows(date(2000, 1, 1), date(2026, 11, 26), daily=True)

    assert result == [
        (date(2000, 1, 1), date(2009, 12, 31)),
        (date(2010, 1, 1), date(2019, 12, 31)),
        (date(2020, 1, 1), date(2026, 11, 26)),
    ]


def test_daily_window_starting_on_a_leap_day():
    assert sgs.windows(date(2016, 2, 29), date(2026, 3, 1), daily=True) == [
        (date(2016, 2, 29), date(2026, 2, 27)),
        (date(2026, 2, 28), date(2026, 3, 1)),
    ]


def test_monthly_series_are_requested_in_one_window():
    assert sgs.windows(date(1980, 1, 1), date(2026, 11, 26), daily=False) == [
        (date(1980, 1, 1), date(2026, 11, 26))
    ]


def test_an_inverted_range_needs_no_request():
    assert sgs.windows(date(2026, 2, 1), date(2026, 1, 1), daily=True) == []


def test_first_run_stores_the_response_exactly_as_received(tmp_path, policy):
    api = FakeSgs()

    report, manifest = run(api, tmp_path, policy, since=date(2026, 11, 1), today=date(2026, 11, 26))

    (entry,) = manifest.entries
    assert report.count(Status.DOWNLOADED) == 1
    assert entry.dataset == "sgs_1"
    assert entry.raw_path.startswith("raw/bcb_sgs/sgs_1/start=20261101/end=20261126/sha256=")
    stored = json.loads((tmp_path / entry.raw_path).read_bytes())
    assert stored[0] == {"data": "02/11/2026", "valor": "5.1234"}  # values stay strings
    assert entry.details["first_observation"] == "2026-11-02"
    assert entry.details["last_observation"] == "2026-11-26"
    assert entry.details["observations"] == len(stored) == 19


def test_long_history_is_fetched_in_windows_the_api_accepts(tmp_path, policy):
    api = FakeSgs()

    report, manifest = run(api, tmp_path, policy, since=date(2004, 1, 1), today=date(2026, 11, 26))

    assert not report.failed
    assert len(api.requests) == 3
    assert len(manifest.entries) == 3


def test_next_run_only_asks_for_the_overlap_and_what_is_new(tmp_path, policy):
    api = FakeSgs()
    run(api, tmp_path, policy, since=date(2022, 1, 1), today=date(2026, 11, 20))
    api.requests.clear()

    report, manifest = run(api, tmp_path, policy, since=date(2022, 1, 1), today=date(2026, 11, 27))

    assert api.requests == [(1, date(2026, 10, 21), date(2026, 11, 27))]
    assert report.count(Status.DOWNLOADED) == 1
    assert manifest.entries[-1].details["last_observation"] == "2026-11-27"


def test_a_revised_value_inside_the_overlap_is_captured(tmp_path, policy):
    api = FakeSgs()
    run(api, tmp_path, policy, since=date(2026, 11, 1), today=date(2026, 11, 20))
    api.revised[date(2026, 11, 18)] = "5.9999"

    _, manifest = run(api, tmp_path, policy, since=date(2026, 11, 1), today=date(2026, 11, 27))

    latest = json.loads((tmp_path / manifest.entries[-1].raw_path).read_bytes())
    assert {"data": "18/11/2026", "valor": "5.9999"} in latest


def test_repeating_a_run_on_the_same_day_records_nothing_new(tmp_path, policy):
    api = FakeSgs()
    options = {"since": date(2026, 11, 1), "today": date(2026, 11, 26)}
    run(api, tmp_path, policy, **options)

    report, manifest = run(api, tmp_path, policy, **options)

    assert report.count(Status.UNCHANGED) == 1
    assert len(manifest.entries) == 1


def test_moving_the_start_earlier_backfills_only_the_missing_range(tmp_path, policy):
    api = FakeSgs()
    run(api, tmp_path, policy, since=date(2024, 1, 1), today=date(2026, 11, 26))
    api.requests.clear()

    run(api, tmp_path, policy, since=date(2022, 1, 1), today=date(2026, 11, 26))

    assert api.requests[0] == (1, date(2022, 1, 1), date(2023, 12, 31))


def test_a_window_without_observations_is_not_an_error(tmp_path, policy):
    api = FakeSgs()

    report, manifest = run(
        api, tmp_path, policy, since=date(2026, 11, 21), today=date(2026, 11, 22)
    )

    assert report.count(Status.EMPTY) == 1
    assert not report.failed
    assert manifest.entries == ()


def test_an_unknown_series_is_an_error_even_though_it_is_also_a_404(tmp_path, policy):
    def handler(request):
        return httpx.Response(
            404, text="A serie nao existe!", headers={"Content-Type": "text/plain; charset=utf-8"}
        )

    report, _ = run(handler, tmp_path, policy, since=date(2026, 11, 1), today=date(2026, 11, 26))

    assert report.failed
    assert "404" in report.outcomes[0].detail


def test_monthly_series_use_a_single_request(tmp_path, policy):
    api = FakeSgs()

    _, manifest = run(
        api,
        tmp_path,
        policy,
        since=date(2004, 1, 1),
        today=date(2026, 11, 26),
        series=(MONTHLY,),
    )

    assert api.requests == [(433, date(2004, 1, 1), date(2026, 11, 26))]
    assert manifest.entries[0].details["last_observation"] == "2026-11-01"


def test_dry_run_makes_no_request(tmp_path, policy):
    api = FakeSgs()

    report, manifest = run(
        api, tmp_path, policy, since=date(2026, 11, 1), today=date(2026, 11, 26), dry_run=True
    )

    assert report.count(Status.PLANNED) == 1
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
