import hashlib

import httpx
import pytest

from fuel_price_lakehouse import http


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_get_retries_on_server_error_then_succeeds(policy):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, text="ok")

    response = http.get(client_for(handler), "https://example.test/x", policy=policy)

    assert response.text == "ok"
    assert len(calls) == 3


def test_get_gives_up_after_the_configured_attempts(policy):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(http.RetryableError, match="503"):
        http.get(client_for(handler), "https://example.test/x", policy=policy)
    assert len(calls) == policy.attempts


def test_get_retries_on_connection_errors(policy):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200)

    assert http.get(client_for(handler), "https://example.test/x", policy=policy).status_code == 200


@pytest.mark.parametrize("status", [429, 500, 503, 520])
def test_overload_and_any_server_error_are_retried(policy, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status) if len(calls) == 1 else httpx.Response(200)

    assert http.get(client_for(handler), "https://example.test/x", policy=policy).status_code == 200
    assert len(calls) == 2


def test_get_does_not_retry_client_errors(policy):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404)

    assert http.get(client_for(handler), "https://example.test/x", policy=policy).status_code == 404
    assert len(calls) == 1


def test_remote_size_reads_content_range_from_a_one_byte_request(policy):
    def handler(request):
        assert request.method == "GET"  # the source refuses HEAD
        assert request.headers["Range"] == "bytes=0-0"
        return httpx.Response(206, headers={"Content-Range": "bytes 0-0/8488624"}, content=b"P")

    assert http.remote_size(client_for(handler), "https://example.test/f", policy=policy) == 8488624


def test_remote_size_falls_back_to_content_length_when_range_is_ignored(policy):
    def handler(request):
        return httpx.Response(200, content=b"12345")

    assert http.remote_size(client_for(handler), "https://example.test/f", policy=policy) == 5


def test_remote_size_fails_loudly_on_unexpected_status(policy):
    with pytest.raises(http.FetchError, match="403"):
        http.remote_size(
            client_for(lambda r: httpx.Response(403)), "https://example.test/f", policy=policy
        )


def test_download_writes_the_file_and_reports_size_and_hash(tmp_path, policy):
    body = b"\xef\xbb\xbfRegiao - Sigla;Estado - Sigla\r\nS;RS\r\n"
    client = client_for(
        lambda r: httpx.Response(200, content=body, headers={"Content-Type": "text/csv"})
    )

    result = http.download(
        client, "https://example.test/f.csv", tmp_path / "a" / "f", policy=policy
    )

    assert (tmp_path / "a" / "f").read_bytes() == body
    assert result == http.Download(len(body), hashlib.sha256(body).hexdigest(), "text/csv")


def test_download_retries_a_truncated_transfer(tmp_path, policy):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, headers={"Content-Length": "10"}, stream=_Bytes(b"abc"))
        return httpx.Response(200, content=b"0123456789")

    result = http.download(
        client_for(handler), "https://example.test/f", tmp_path / "f", policy=policy
    )

    assert result.size_bytes == 10
    assert (tmp_path / "f").read_bytes() == b"0123456789"


def test_download_rejects_an_empty_body(tmp_path, policy):
    with pytest.raises(http.RetryableError, match="empty"):
        http.download(
            client_for(lambda r: httpx.Response(200, content=b"")),
            "https://example.test/f",
            tmp_path / "f",
            policy=policy,
        )


def test_download_does_not_retry_a_missing_file(tmp_path, policy):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404)

    with pytest.raises(http.FetchError, match="404"):
        http.download(client_for(handler), "https://example.test/f", tmp_path / "f", policy=policy)
    assert len(calls) == 1


class _Bytes(httpx.SyncByteStream):
    """A body that is shorter than the Content-Length the response announces."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def __iter__(self):
        yield self._data
