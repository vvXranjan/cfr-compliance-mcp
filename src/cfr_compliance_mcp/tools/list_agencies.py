"""MCP tool: list_agencies.

Lists all agencies referenced in the CFR, including their title/chapter
cross-references. Useful for mapping a contract clause's implied
regulator (e.g. "environmental", "labor", "acquisition") to the correct
CFR title before running a search. Backed by `EcfrClient.list_agencies()`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from cfr_compliance_mcp.cache import CacheBackend, build_cache_key
from cfr_compliance_mcp.clients import EcfrClient
from cfr_compliance_mcp.config import get_settings
from cfr_compliance_mcp.logging_config import get_logger
from cfr_compliance_mcp.models.requests import ListAgenciesRequest
from cfr_compliance_mcp.models.responses import AgenciesResponse
from cfr_compliance_mcp.tools._common import build_error_response, cached_call

logger = get_logger(__name__)

__all__ = ["make_list_agencies_tool"]


def make_list_agencies_tool(
    ecfr_client: EcfrClient, cache: CacheBackend
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Build the `list_agencies` MCP tool, bound to a specific
    `EcfrClient`/`CacheBackend` pair injected by `server.py` at startup."""

    async def list_agencies() -> dict[str, Any]:
        """List all agencies referenced in the CFR, with their title/chapter
        cross-references.

        Useful for mapping a contract clause's implied regulator (e.g.
        "environmental", "labor", "acquisition") to the correct CFR title
        before running `search_regulations` or `search_by_keyword`.

        Returns:
            On success: {"agencies": [...as returned by eCFR, including
            nested child agencies...]}.
            On failure: {"error": true, "error_type": ..., "message": ...,
            "retryable": ...}.
        """
        try:
            ListAgenciesRequest()
        except PydanticValidationError as exc:
            return build_error_response(exc)

        cache_key = build_cache_key("agencies")
        settings = get_settings()

        async def compute() -> dict[str, Any]:
            raw = await ecfr_client.list_agencies()
            agencies = raw.get("agencies", []) if isinstance(raw, dict) else (raw or [])
            response = AgenciesResponse(agencies=agencies)
            return response.model_dump()

        try:
            return await cached_call(cache, cache_key, settings.cache_ttl_seconds, compute)
        except Exception as exc:  # noqa: BLE001
            return build_error_response(exc)

    return list_agencies
