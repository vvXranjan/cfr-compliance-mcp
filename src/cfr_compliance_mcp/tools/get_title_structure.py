"""MCP tool: get_title_structure.

Fetches the hierarchical structure (subtitle -> chapter -> subchapter ->
part -> subpart -> section) of a CFR title, without retrieving any
regulation text. Lets an agent navigate/plan which part or section to
retrieve before spending tokens on full text. Backed by
`EcfrClient.get_structure()`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from cfr_compliance_mcp.cache import CacheBackend, build_cache_key
from cfr_compliance_mcp.clients import EcfrClient
from cfr_compliance_mcp.config import get_settings
from cfr_compliance_mcp.logging_config import get_logger
from cfr_compliance_mcp.models.requests import GetTitleStructureRequest
from cfr_compliance_mcp.models.responses import TitleStructureResponse
from cfr_compliance_mcp.tools._common import build_error_response, cached_call

logger = get_logger(__name__)

__all__ = ["make_get_title_structure_tool"]


def make_get_title_structure_tool(
    ecfr_client: EcfrClient, cache: CacheBackend
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Build the `get_title_structure` MCP tool, bound to a specific
    `EcfrClient`/`CacheBackend` pair injected by `server.py` at startup."""

    async def get_title_structure(title: int, date: str | None = None) -> dict[str, Any]:
        """Fetch the hierarchical structure of a CFR title (no regulation
        text) — useful for planning which part/section to retrieve next.

        Args:
            title: CFR title number (1-50).
            date: optional as-of date (YYYY-MM-DD). Defaults to the latest
                available date for this title if omitted.

        Returns:
            On success: {"title": ..., "date": ..., "structure": {...nested
            hierarchy as returned by eCFR...}}.
            On failure: {"error": true, "error_type": ..., "message": ...,
            "retryable": ...}.
        """
        try:
            req = GetTitleStructureRequest(title=title, date=date)
        except PydanticValidationError as exc:
            return build_error_response(exc)

        cache_key = build_cache_key("structure", req.title, req.date)
        settings = get_settings()

        async def compute() -> dict[str, Any]:
            resolved_date = await ecfr_client.resolve_date(req.title, req.date)
            raw_structure = await ecfr_client.get_structure(req.title, date=req.date)
            response = TitleStructureResponse(
                title=req.title, date=resolved_date, structure=raw_structure
            )
            return response.model_dump()

        try:
            return await cached_call(cache, cache_key, settings.cache_ttl_seconds, compute)
        except Exception as exc:  # noqa: BLE001
            return build_error_response(exc)

    return get_title_structure
