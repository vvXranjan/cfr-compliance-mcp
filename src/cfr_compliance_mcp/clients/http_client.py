"""Generic async HTTP client: timeouts, retries, and rate limiting.

This module is intentionally API-agnostic. It has zero knowledge of the
eCFR API specifically — it only knows how to talk HTTP reliably. This is
what makes it reusable if we later add clients for the Federal Register
API, GovInfo, or any other REST API: point a new `HttpClient` at a
different `base_url` and get the same retry/timeout/rate-limit behavior
for free.

Design summary:
    - Retries (via tenacity) apply only to transient failures: timeouts,
      connection errors, HTTP 5xx, and HTTP 429. A 4xx client error
      (other than 429) is never retried, since resending an identical
      malformed request will not produce a different result.
    - Rate limiting is a simple client-side sliding-window limiter. It
      protects the upstream API (and us) from bursts, independent of
      whatever the upstream's own limit may or may not be.
    - All exceptions raised here are subclasses of `HttpClientError`,
      which is *not* related to `exceptions.EcfrApiError` — translation
      between the two happens one layer up, in `ecfr_client.py`.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from cfr_compliance_mcp.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Generic HTTP exception hierarchy (API-agnostic)
# ---------------------------------------------------------------------------


class HttpClientError(Exception):
    """Base class for all errors raised by `HttpClient`.

    Deliberately separate from the `EcfrApiError` hierarchy in
    `exceptions.py` — this class knows nothing about eCFR and should
    remain meaningful for any REST API this client is pointed at.
    """

    def __init__(self, message: str, *, status_code: int | None = None, url: str | None = None) -> None:  # noqa: E501
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class HttpNotFoundError(HttpClientError):
    """HTTP 404. Never retried."""


class HttpRateLimitedError(HttpClientError):
    """HTTP 429. Retried with backoff."""


class HttpServerError(HttpClientError):
    """HTTP 5xx. Retried with backoff."""


class HttpTimeoutError(HttpClientError):
    """Request exceeded the configured timeout. Retried with backoff."""


class HttpConnectionError(HttpClientError):
    """Network-level failure (DNS, refused connection, etc). Retried."""


_RETRYABLE_EXCEPTIONS = (
    HttpServerError,
    HttpTimeoutError,
    HttpConnectionError,
    HttpRateLimitedError,
)


# ---------------------------------------------------------------------------
# Client-side rate limiter
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Sliding-window limiter: at most `max_per_minute` calls to `acquire()`
    are allowed to proceed in any trailing 60-second window; excess callers
    `await asyncio.sleep(...)` until a slot frees up.

    This exists to make us a "good API citizen" against a public
    government API that publishes no official hard rate limit, rather
    than to satisfy a limit the server enforces itself.
    """

    def __init__(self, max_per_minute: int) -> None:
        self._max_per_minute = max_per_minute
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            self._evict_expired()
            if len(self._timestamps) >= self._max_per_minute:
                sleep_for = 60.0 - (time.monotonic() - self._timestamps[0])
                if sleep_for > 0:
                    logger.debug(
                        "Client-side rate limit reached; sleeping %.2fs",
                        sleep_for,
                        extra={"sleep_seconds": round(sleep_for, 2)},
                    )
                    await asyncio.sleep(sleep_for)
                self._evict_expired()
            self._timestamps.append(time.monotonic())

    def _evict_expired(self) -> None:
        window_start = time.monotonic() - 60.0
        while self._timestamps and self._timestamps[0] < window_start:
            self._timestamps.popleft()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


def _log_retry_attempt(retry_state: RetryCallState) -> None:
    """tenacity `before_sleep` hook — logs each retry with the triggering error."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "Retrying request (attempt %d) after %s: %s",
        retry_state.attempt_number,
        type(exc).__name__ if exc else "unknown error",
        exc,
        extra={"attempt": retry_state.attempt_number},
    )


class HttpClient:
    """Reusable async HTTP client with retries, timeouts, and rate limiting.

    Usage:
        async with HttpClient(base_url="https://www.ecfr.gov") as client:
            response = await client.get("/api/versioner/v1/titles.json")
            data = response.json()

    This class is API-agnostic by design — it has no eCFR-specific
    behavior. `EcfrClient` (in `ecfr_client.py`) is the layer that adds
    eCFR endpoint knowledge on top of this.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        retry_backoff_base_seconds: float = 1.0,
        max_requests_per_minute: int = 60,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff_base_seconds = retry_backoff_base_seconds
        self._default_headers = dict(default_headers or {})
        self._rate_limiter = _RateLimiter(max_requests_per_minute)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def start(self) -> None:
        """Open the underlying `httpx.AsyncClient`.

        Exposed as an explicit method (not only via `async with`) because
        a long-lived MCP server process will typically open one
        `HttpClient` at startup and keep it alive for the process
        lifetime, rather than using it as a context manager per request.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                headers=self._default_headers,
            )
            logger.debug("HttpClient started", extra={"base_url": self._base_url})

    async def aclose(self) -> None:
        """Close the underlying `httpx.AsyncClient`. Safe to call multiple times."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.debug("HttpClient closed", extra={"base_url": self._base_url})

    def _ensure_started(self) -> httpx.AsyncClient:
        if self._client is None:
            raise HttpClientError(
                "HttpClient used before start(). Call 'await client.start()' or "
                "use 'async with HttpClient(...) as client:'."
            )
        return self._client

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Issue a GET request with rate limiting, retries, and timeout handling.

        Returns the raw `httpx.Response` on success. This client makes no
        assumption about response body format (JSON vs XML) — callers
        decide via `.json()` or `.text`, since the eCFR API mixes both
        across its endpoints.

        Raises:
            HttpNotFoundError: HTTP 404. Not retried.
            HttpRateLimitedError: HTTP 429. Retried with exponential backoff.
            HttpServerError: HTTP 5xx. Retried with exponential backoff.
            HttpTimeoutError: request exceeded `timeout_seconds`. Retried.
            HttpConnectionError: network-level failure. Retried.
            HttpClientError: any other non-2xx status (e.g. 400) — not
                retried, and any other unexpected transport failure.
        """
        client = self._ensure_started()
        full_url = f"{self._base_url}{path}"

        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_retries + 1),
            wait=wait_exponential(
                multiplier=self._retry_backoff_base_seconds,
                min=self._retry_backoff_base_seconds,
            ),
            retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
            before_sleep=_log_retry_attempt,
            reraise=True,
        )

        async for attempt in retrying:
            with attempt:
                await self._rate_limiter.acquire()
                logger.debug(
                    "GET %s",
                    full_url,
                    extra={"params": dict(params or {}), "attempt": attempt.retry_state.attempt_number},  # noqa: E501
                )
                response = await self._send(client, path, params, headers, full_url)
                self._raise_for_status(response, full_url)
                logger.info(
                    "GET %s -> %d",
                    full_url,
                    response.status_code,
                    extra={"status_code": response.status_code},
                )
                return response

        # Unreachable: AsyncRetrying either returns via the loop above or
        # raises (reraise=True). This satisfies the type checker's
        # expectation of a return on every path.
        raise HttpClientError(f"Retry loop exited without a response for {full_url}")

    @staticmethod
    async def _send(
        client: httpx.AsyncClient,
        path: str,
        params: Mapping[str, Any] | None,
        headers: Mapping[str, str] | None,
        full_url: str,
    ) -> httpx.Response:
        """Perform the actual request, translating httpx/network exceptions
        into our generic `HttpClientError` subtypes."""
        try:
            return await client.get(path, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            logger.warning("Request timed out: %s", full_url, extra={"url": full_url})
            raise HttpTimeoutError(f"Request to {full_url} timed out", url=full_url) from exc
        except httpx.ConnectError as exc:
            logger.warning("Connection failed: %s", full_url, extra={"url": full_url})
            raise HttpConnectionError(f"Could not connect to {full_url}: {exc}", url=full_url) from exc  # noqa: E501
        except httpx.HTTPError as exc:
            logger.warning("Transport error: %s", full_url, extra={"url": full_url})
            raise HttpConnectionError(f"Transport error contacting {full_url}: {exc}", url=full_url) from exc  # noqa: E501

    @staticmethod
    def _raise_for_status(response: httpx.Response, url: str) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status == 404:
            raise HttpNotFoundError(f"Resource not found: {url}", status_code=status, url=url)
        if status == 429:
            raise HttpRateLimitedError(f"Rate limited by upstream: {url}", status_code=status, url=url)  # noqa: E501
        if 500 <= status < 600:
            raise HttpServerError(f"Upstream server error ({status}): {url}", status_code=status, url=url)  # noqa: E501
        raise HttpClientError(
            f"Unexpected HTTP status {status} for {url}: {response.text[:300]!r}",
            status_code=status,
            url=url,
        )
