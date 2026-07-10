"""MCP tool: search_by_keyword.

Multi-keyword search across the CFR — for an agent that has extracted a
set of distinct keywords/terms from a contract clause rather than a
single free-text phrase. Keywords are joined into one query string and
handed to the same underlying search as `search_regulations`, via the
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
from cfr_compliance_mcp.models.requests import SearchByKeywordRequest
from cfr_compliance_mcp.tools._common import build_error_response, cached_call, perform_search

logger = get_logger(__name__)

__all__ = ["make_search_by_keyword_tool"]


def make_search_by_keyword_tool(
    ecfr_client: EcfrClient, cache: CacheBackend
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Build the `search_by_keyword` MCP tool, bound to a specific
    `EcfrClient`/`CacheBackend` pair injected by `server.py` at startup."""

    async def search_by_keyword(
        keywords: list[str],
        agency_slugs: list[str] | None = None,
        date: str | None = "current",
        per_page: int = 20,
        page: int = 1,
    ) -> dict[str, Any]:
        """Search the CFR using a list of distinct keywords/terms rather
        than a single free-text phrase.

        Keywords are joined into one query string before searching — for
        precise multi-word phrase search, use `search_regulations` instead.

        Args:
            keywords: list of search terms, e.g. ["hazardous", "ignitability"].
            agency_slugs: optional list of agency slugs to restrict the search to.
            date: "current" (default) for in-force text only, an explicit
                YYYY-MM-DD date, or None for all historical matches.
            per_page: results per page (1-100, default 20).
            page: page number (>= 1, default 1).

        Returns:
            On success: {"results": [...], "total_count": ..., "current_page": ...,
            "total_pages": ...}.
            On failure: {"error": true, "error_type": ..., "message": ...,
            "retryable": ...}.
        """
        try:
            req = SearchByKeywordRequest(
                keywords=keywords, agency_slugs=agency_slugs, date=date, per_page=per_page, page=page
            )
        except PydanticValidationError as exc:
            return build_error_response(exc)

        query = req.build_query()
        agency_key = ",".join(sorted(req.agency_slugs)) if req.agency_slugs else None
        cache_key = build_cache_key("search", query, agency_key, req.date, req.per_page, req.page)
        settings = get_settings()

        async def compute() -> dict[str, Any]:
            return await perform_search(
                ecfr_client,
                query=query,
                agency_slugs=req.agency_slugs,
                date=req.date,
                per_page=req.per_page,
                page=req.page,
            )

        try:
            return await cached_call(cache, cache_key, settings.cache_ttl_seconds, compute)
        except Exception as exc:  # noqa: BLE001
            return build_error_response(exc)

    return search_by_keyword
