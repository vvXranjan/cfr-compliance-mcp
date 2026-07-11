"""MCP tool: search_regulations.

Free-text/phrase search across the CFR — the primary entry point for an
agent that has a contract clause's language but doesn't yet know which
title/part/section is relevant. Backed by `EcfrClient.search()` via the
shared `tools._common.perform_search` implementation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from cfr_compliance_mcp.cache import CacheBackend, build_cache_key
from cfr_compliance_mcp.clients import EcfrClient
from cfr_compliance_mcp.config import get_settings
from cfr_compliance_mcp.logging_config import get_logger
from cfr_compliance_mcp.models.requests import SearchRegulationsRequest
from cfr_compliance_mcp.tools._common import build_error_response, cached_call, perform_search

logger = get_logger(__name__)

__all__ = ["make_search_regulations_tool"]


def make_search_regulations_tool(
    ecfr_client: EcfrClient, cache: CacheBackend
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Build the `search_regulations` MCP tool, bound to a specific
    `EcfrClient`/`CacheBackend` pair injected by `server.py` at startup."""

    async def search_regulations(
        query: str,
        agency_slugs: list[str] | None = None,
        date: str | None = "current",
        per_page: int = 20,
        page: int = 1,
    ) -> dict[str, Any]:
        """Full-text search across the CFR for a free-text query or phrase.

        Args:
            query: free-text search query, e.g. "hazardous waste characteristics".
            agency_slugs: optional list of agency slugs to restrict the search to.
            date: the sentinel "current" (default) for only in-force text
                (excludes superseded historical matches), an explicit
                YYYY-MM-DD date to scope results to content in force on
                that date, or None for no date restriction at all — matches
                from every historical version of the CFR are included,
                which may return duplicate or superseded text alongside
                current text. Callers that specifically want historical,
                including superseded, matches should pass date=None
                explicitly rather than relying on the default.
            per_page: results per page (1-100, default 20).
            page: page number (>= 1, default 1).

        Returns:
            On success: {"results": [...], "total_count": ..., "current_page": ...,
            "total_pages": ...}.
            On failure: {"error": true, "error_type": ..., "message": ...,
            "retryable": ...}.
        """
        try:
            req = SearchRegulationsRequest(
                query=query, agency_slugs=agency_slugs, date=date, per_page=per_page, page=page
            )
        except PydanticValidationError as exc:
            return build_error_response(exc)

        agency_key = ",".join(sorted(req.agency_slugs)) if req.agency_slugs else None
        cache_key = build_cache_key("search", req.query, agency_key, req.date, req.per_page, req.page)
        settings = get_settings()

        async def compute() -> dict[str, Any]:
            return await perform_search(
                ecfr_client,
                query=req.query,
                agency_slugs=req.agency_slugs,
                date=req.date,
                per_page=req.per_page,
                page=req.page,
            )

        try:
            return await cached_call(cache, cache_key, settings.cache_ttl_seconds, compute)
        except Exception as exc:  # noqa: BLE001 -- tool boundary: never leak raw exceptions to MCP transport
            return build_error_response(exc)

    return search_regulations