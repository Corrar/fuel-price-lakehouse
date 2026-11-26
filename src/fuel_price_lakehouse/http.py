"""HTTP access shared by every collector: timeouts, retries and streamed downloads."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = logging.getLogger(__name__)

_CONTENT_RANGE_TOTAL = re.compile(r"/(\d+)\s*$")
_CHUNK = 1 << 20


class FetchError(Exception):
    """The source answered, but not with something the pipeline can use."""


class RetryableError(FetchError):
    """A failure worth trying again: overload, gateway error or a cut transfer."""


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 5
    wait_initial: float = 2.0
    wait_max: float = 60.0

    @classmethod
    def no_wait(cls, attempts: int = 3) -> RetryPolicy:
        """Same retry logic without sleeping. For tests."""
        return cls(attempts=attempts, wait_initial=0.0, wait_max=0.0)


@dataclass(frozen=True)
class Download:
    size_bytes: int
    sha256: str
    content_type: str


def build_client() -> httpx.Client:
    """Client with explicit timeouts. Sources are slow, so reads get more room."""
    return httpx.Client(
        timeout=httpx.Timeout(30.0, read=180.0),
        follow_redirects=True,
    )


def _retrying(policy: RetryPolicy) -> Retrying:
    return Retrying(
        stop=stop_after_attempt(policy.attempts),
        wait=wait_exponential_jitter(
            multiplier=policy.wait_initial,
            max=policy.wait_max,
            jitter=min(1.0, policy.wait_max),
        ),
        retry=retry_if_exception_type((httpx.TransportError, RetryableError)),
        before_sleep=_log_retry,
        reraise=True,
    )


def _log_retry(state: RetryCallState) -> None:
    error = state.outcome.exception() if state.outcome else None
    log.warning("attempt %d failed (%s); retrying", state.attempt_number, error)


def _check_retryable(response: httpx.Response) -> None:
    if response.status_code == 429 or response.status_code >= 500:
        raise RetryableError(f"HTTP {response.status_code} from {response.request.url}")


def get(
    client: httpx.Client,
    url: str,
    *,
    policy: RetryPolicy,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    """GET with retries. Non-retryable statuses are returned for the caller to judge."""
    for attempt in _retrying(policy):
        with attempt:
            response = client.get(url, params=params)
            _check_retryable(response)
            return response
    raise AssertionError("unreachable")  # pragma: no cover


def remote_size(client: httpx.Client, url: str, *, policy: RetryPolicy) -> int | None:
    """Size of a remote file without downloading it, or ``None`` if the server won't say.

    Uses a one-byte range request instead of HEAD, because some sources refuse
    HEAD. The body is never read, so a server that ignores the range and starts
    sending the whole file costs nothing.
    """
    for attempt in _retrying(policy):
        with attempt, client.stream("GET", url, headers={"Range": "bytes=0-0"}) as response:
            _check_retryable(response)
            if response.status_code == 206:
                match = _CONTENT_RANGE_TOTAL.search(response.headers.get("Content-Range", ""))
                return int(match.group(1)) if match else None
            if response.status_code == 200:
                length = response.headers.get("Content-Length")
                return int(length) if length and length.isdigit() else None
            raise FetchError(f"HTTP {response.status_code} probing {url}")
    raise AssertionError("unreachable")  # pragma: no cover


def download(client: httpx.Client, url: str, dest: Path, *, policy: RetryPolicy) -> Download:
    """Stream ``url`` into ``dest`` and return its size and SHA-256.

    A transfer shorter than the announced ``Content-Length`` is retried rather
    than accepted, so a cut connection can never produce a truncated raw file.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in _retrying(policy):
        with attempt, client.stream("GET", url) as response:
            _check_retryable(response)
            if response.status_code != 200:
                raise FetchError(f"HTTP {response.status_code} downloading {url}")
            digest = hashlib.sha256()
            size = 0
            with dest.open("wb") as out:
                for chunk in response.iter_bytes(_CHUNK):
                    out.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            announced = response.headers.get("Content-Length")
            encoded = "Content-Encoding" in response.headers
            if not encoded and announced and announced.isdigit() and int(announced) != size:
                raise RetryableError(f"got {size} of {announced} bytes from {url}")
            if size == 0:
                raise RetryableError(f"empty response from {url}")
            return Download(size, digest.hexdigest(), response.headers.get("Content-Type", ""))
    raise AssertionError("unreachable")  # pragma: no cover
