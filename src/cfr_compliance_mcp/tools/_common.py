"""Shared internal helpers for the MCP tool layer.

Not one of the 8 public MCP tools itself — an internal utility module
(leading underscore signals this) imported by each tool module, kept
here rather than duplicated 8 times. Two responsibilities:

    1. `build_error_response`: translate any exception raised anywhere
       in a tool's execution into the structured `ErrorResponse` JSON
       shape, per the package's error-handling strategy. No tool should
       ever let a raw exception escape to the MCP transport layer.
    2. `cached_call`: the standard "check cache, compute on miss, write
       back" pattern every retrieval tool uses, with cache failures
       treated as non-fatal (fail soft) per the package's caching
       strategy — the cache is a performance optimization, never a
       correctness dependency.
    3. `perform_search`: the shared search implementation backing both
       `search_regulations` and `search_by_keyword`, since both tools
       ultimately do the same thing (call `EcfrClient.search()` and
       shape a `SearchResponse`) after building their query string
       differently.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from cfr_compliance_mcp.cache import CacheBackend
from cfr_compliance_mcp.clients import EcfrClient
from cfr_compliance_mcp.exceptions import CacheError, CfrMcpError, EcfrApiError, XmlParsingError
from cfr_compliance_mcp.logging_config import get_logger
from cfr_compliance_mcp.models.responses import ErrorResponse, SearchResponse

logger = get_logger(__name__)

__all__ = ["build_error_response", "cached_call", "perform_search"]

# Exception class names from the EcfrApiError hierarchy that represent
# transient upstream failures — surfaced to the caller as `retryable`
# so an agent can decide whether to retry the tool call itself.
_RETRYABLE_ECFR_ERRORS = frozenset(
    {"EcfrServerError", "EcfrTimeoutError", "EcfrConnectionError", "EcfrRateLimitedError"}
)


def build_error_response(exc: Exception) -> dict[str, Any]:
    """Translate any exception into the structured `ErrorResponse` shape.

    Every tool function's outermost try/except calls this — it is the
    single place that decides how each exception type is represented to
    the calling agent, so that decision isn't duplicated (and
    potentially made inconsistently) across 8 tool files.
    """
    if isinstance(exc, PydanticValidationError):
        return ErrorResponse(error_type="ValidationError", message=str(exc), retryable=False).model_dump()  # noqa: E501
    if isinstance(exc, XmlParsingError):
        return ErrorResponse(error_type="XmlParsingError", message=str(exc), retryable=False).model_dump()  # noqa: E501
    if isinstance(exc, CacheError):
        # A CacheError reaching here means it escaped cached_call's own
        # fail-soft handling (e.g. raised from code outside that
        # helper) -- still non-fatal to the caller, but worth its own
        # error_type for debugging.
        return ErrorResponse(error_type="CacheError", message=str(exc), retryable=False).model_dump()  # noqa: E501
    if isinstance(exc, EcfrApiError):
        retryable = type(exc).__name__ in _RETRYABLE_ECFR_ERRORS
        return ErrorResponse(
            error_type=type(exc).__name__, message=str(exc), retryable=retryable
        ).model_dump()
    if isinstance(exc, CfrMcpError):
        # Catches ValidationError (our own, from client-layer defense-in-depth
        # checks) and any future CfrMcpError subclass not special-cased above.
        return ErrorResponse(error_type=type(exc).__name__, message=str(exc), retryable=False).model_dump()  # noqa: E501

    # Truly unexpected, non-project exception: log full detail
    # server-side (stack trace via logger.exception), but return only a
    # generic message to the caller rather than leaking internals.
    logger.exception("Unexpected error in tool layer")
    return ErrorResponse(
        error_type="InternalError",
        message="An unexpected internal error occurred.",
        retryable=False,
    ).model_dump()


async def cached_call(
    cache: CacheBackend,
    key: str,
    ttl_seconds: int | None,
    compute: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Check the cache for `key`; on miss, call `compute()`, cache the
    result, and return it.

    Cache failures on both the read and write side are logged and
    swallowed (fail soft) rather than propagated — a cache outage
    should degrade the system to "always call the live API", never to
    "the tool stops working".
    """
    try:
        cached = await cache.get(key)
        if cached is not None:
            logger.debug("Tool-layer cache hit", extra={"cache_key": key})
            return json.loads(cached)
    except CacheError as exc:
        logger.warning(
            "Cache read failed, falling through to live call",
            extra={"cache_key": key, "error": str(exc)},
        )

    result = await compute()

    try:
        await cache.set(key, json.dumps(result), ttl_seconds=ttl_seconds)
    except CacheError as exc:
        logger.warning("Cache write failed (non-fatal)", extra={"cache_key": key, "error": str(exc)})  # noqa: E501

    return result


async def perform_search(
    ecfr_client: EcfrClient,
    *,
    query: str,
    agency_slugs: list[str] | None,
    date: str | None,
    per_page: int,
    page: int,
) -> dict[str, Any]:
    """Shared search implementation backing both `search_regulations` and
    `search_by_keyword`.

    Both tools validate their own distinct input shapes (a single query
    string vs. a keyword list) via their own request models, then
    converge here once they have a final query string — this is the
    one place `EcfrClient.search()` is called and the one place the
    raw eCFR search JSON is shaped into a `SearchResponse`.
    """
    raw = await ecfr_client.search(
        query, agency_slugs=agency_slugs, date=date, per_page=per_page, page=page
    )

    # eCFR's search response nests pagination metadata under "meta"
    # per the documented API shape (current_page, total_pages,
    # total_count). Accessed defensively since this shape has not been
    # live-verified against the real API in this build environment.
    meta = raw.get("meta", {}) if isinstance(raw, dict) else {}
    results = raw.get("results", []) if isinstance(raw, dict) else []

    response = SearchResponse(
        results=results,
        total_count=meta.get("total_count", len(results)),
        current_page=meta.get("current_page", page),
        total_pages=meta.get("total_pages", 1),
    )
    return response.model_dump()
