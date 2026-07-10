"""MCP tool: retrieve_part.

Fetches the text of an entire CFR part (a cluster of related sections),
parses it into clean text + citation, and returns structured JSON. Use
when a contract clause maps to a broader regulatory topic rather than
one exact section. Backed by `EcfrClient.retrieve_part()` +
`parsing.parse_regulation_xml()`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from cfr_compliance_mcp.cache import CacheBackend, build_cache_key
from cfr_compliance_mcp.clients import EcfrClient
from cfr_compliance_mcp.config import get_settings
from cfr_compliance_mcp.logging_config import get_logger
from cfr_compliance_mcp.models.requests import RetrievePartRequest
from cfr_compliance_mcp.models.responses import CitationModel, RegulationTextResponse
from cfr_compliance_mcp.parsing import parse_regulation_xml
from cfr_compliance_mcp.tools._common import build_error_response, cached_call

logger = get_logger(__name__)

__all__ = ["make_retrieve_part_tool"]


def make_retrieve_part_tool(
    ecfr_client: EcfrClient, cache: CacheBackend
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Build the `retrieve_part` MCP tool, bound to a specific
    `EcfrClient`/`CacheBackend` pair injected by `server.py` at startup."""

    async def retrieve_part(title: int, part: str, date: str | None = None) -> dict[str, Any]:
        """Retrieve the text of an entire CFR part (a cluster of related sections).

        Prefer `retrieve_section` when the clause maps to one exact
        section — a part's response can be large.

        Args:
            title: CFR title number (1-50).
            part: CFR part number, e.g. "261".
            date: optional as-of date (YYYY-MM-DD). Defaults to the latest
                available date for this title if omitted.

        Returns:
            On success: {"text": "...", "citation": {...}}.
            On failure: {"error": true, "error_type": ..., "message": ...,
            "retryable": ...}.
        """
        try:
            req = RetrievePartRequest(title=title, part=part, date=date)
        except PydanticValidationError as exc:
            return build_error_response(exc)

        cache_key = build_cache_key("part", req.title, req.part, req.date)
        settings = get_settings()

        async def compute() -> dict[str, Any]:
            resolved_date = await ecfr_client.resolve_date(req.title, req.date)
            raw_xml = await ecfr_client.retrieve_part(req.title, req.part, date=req.date)
            parsed = parse_regulation_xml(
                raw_xml, title=req.title, date=resolved_date, part=req.part
            )
            response = RegulationTextResponse(
                text=parsed.text, citation=CitationModel.from_citation(parsed.citation)
            )
            return response.model_dump()

        try:
            return await cached_call(cache, cache_key, settings.cache_ttl_seconds, compute)
        except Exception as exc:  # noqa: BLE001
            return build_error_response(exc)

    return retrieve_part
