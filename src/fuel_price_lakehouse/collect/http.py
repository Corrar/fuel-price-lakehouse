"""HTTP access shared by the collectors: one request at a time, paced, retried.

Both sources are public services with unknown rate limits, so the collectors
never run requests in parallel and wait between them. Failures that may go away
(network errors, 429, 5xx, a body cut short) are retried with exponential
backoff; anything else is reported immediately.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

T = TypeVar("T")

_CONTENT_RANGE_TOTAL = re.compile(r"bytes\s+\d+-\d+/(\d+)")
_CHUNK = 1024 * 1024


class TransientError(Exception):
    """The request failed in a way that is worth retrying."""


class SourceError(Exception):
    """The source answered in a way retrying will not fix."""


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 5
    initial_wait: float = 2.0
    max_wait: float = 60.0


@dataclass(frozen=True)
class Download:
    size_bytes: int
    sha256: str


def default_client() -> httpx.Client:
    # Connect fast or fail; reading is slow by nature (about 0.75 MB/s from ANP).
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=20.0), follow_redirects=True)


class Fetcher:
    """Sequential, paced, retrying HTTP requests on top of an ``httpx.Client``."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        retry: RetryPolicy | None = None,
        pause: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._retry = retry or RetryPolicy()
        self._pause = pause
        self._sleep = sleep
        self._requests = 0

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        allowed: Collection[int] = (200,),
    ) -> httpx.Response:
        """GET a small resource fully into memory.

        ``allowed`` lists the status codes the caller knows how to interpret.
        """

        def attempt() -> httpx.Response:
            response = self._client.get(url, headers=headers)
            _check_status(response, allowed)
            return response

        return self._run(attempt)

    def remote_size(self, url: str) -> int:
        """Size of a file in bytes, without downloading it.

        ANP refuses ``HEAD``, so this asks for the first byte only and reads the
        total from ``Content-Range``.
        """

        def attempt() -> int:
            with self._client.stream("GET", url, headers={"Range": "bytes=0-0"}) as response:
                _check_status(response, (200, 206))
                match = _CONTENT_RANGE_TOTAL.search(response.headers.get("Content-Range", ""))
                if match:
                    return int(match.group(1))
                # Range ignored: the body is the whole file. Do not read it.
                length = response.headers.get("Content-Length")
                if response.status_code == 200 and length is not None:
                    return int(length)
                raise SourceError(f"{url}: the server did not report the file size")

        return self._run(attempt)

    def download(self, url: str, destination: Path) -> Download:
        """Stream a file to ``destination``, hashing it on the way.

        A body shorter than the announced ``Content-Length`` counts as a
        transient failure and the download starts over.
        """
        partial = destination.with_name(destination.name + ".part")

        def attempt() -> Download:
            digest = hashlib.sha256()
            size = 0
            destination.parent.mkdir(parents=True, exist_ok=True)
            with self._client.stream("GET", url) as response:
                _check_status(response, (200,))
                with partial.open("wb") as file:
                    for chunk in response.iter_bytes(_CHUNK):
                        file.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                announced = response.headers.get("Content-Length")
                encoded = "Content-Encoding" in response.headers
            if announced is not None and not encoded and size != int(announced):
                raise TransientError(f"{url}: got {size} bytes, expected {announced}")
            partial.replace(destination)
            return Download(size_bytes=size, sha256=digest.hexdigest())

        try:
            return self._run(attempt)
        finally:
            partial.unlink(missing_ok=True)

    def _run(self, attempt: Callable[[], T]) -> T:
        def paced() -> T:
            if self._requests:
                self._sleep(self._pause)
            self._requests += 1
            try:
                return attempt()
            except httpx.TransportError as error:
                raise TransientError(f"{type(error).__name__}: {error}") from error

        retrying = Retrying(
            retry=retry_if_exception_type(TransientError),
            stop=stop_after_attempt(self._retry.attempts),
            wait=wait_exponential(multiplier=self._retry.initial_wait, max=self._retry.max_wait),
            sleep=self._sleep,
            reraise=True,
        )
        return retrying(paced)


def _check_status(response: httpx.Response, allowed: Collection[int]) -> None:
    status = response.status_code
    if status in allowed:
        return
    message = f"{response.request.url}: HTTP {status}"
    if status == 429 or status >= 500:
        raise TransientError(message)
    raise SourceError(message)
