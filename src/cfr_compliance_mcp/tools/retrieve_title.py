"""MCP tool: retrieve_title.

Fetches the text of an entire CFR title, parses it into clean text +
citation, and returns structured JSON. Backed by
`EcfrClient.retrieve_title()` + `parsing.parse_regulation_xml()`.

WARNING (surfaced in the tool's own docstring, which becomes the MCP
tool description an agent sees): some titles are large enough that the
upstream eCFR API itself can time out on a full-title request. Prefer
`retrieve_part` or `retrieve_section` wherever possible.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from cfr_compliance_mcp.cache import CacheBackend, build_cache_key
from cfr_compliance_mcp.clients import EcfrClient
from cfr_compliance_mcp.config import get_settings
from cfr_compliance_mcp.logging_config import get_logger
from cfr_compliance_mcp.models.requests import RetrieveTitleRequest
from cfr_compliance_mcp.models.responses import CitationModel, RegulationTextResponse
from cfr_compliance_mcp.parsing import parse_regulation_xml
from cfr_compliance_mcp.tools._common import build_error_response, cached_call

logger = get_logger(__name__)

__all__ = ["make_retrieve_title_tool"]


def make_retrieve_title_tool(
    ecfr_client: EcfrClient, cache: CacheBackend
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Build the `retrieve_title` MCP tool, bound to a specific
    `EcfrClient`/`CacheBackend` pair injected by `server.py` at startup."""

    async def retrieve_title(title: int, date: str | None = None) -> dict[str, Any]:
        """Retrieve the text of an entire CFR title.

        WARNING: some titles (notably Title 40 / EPA) are large enough
        that this can be slow or time out upstream. Strongly prefer
        `retrieve_part` or `retrieve_section` when you know which part
        or section is relevant — use this only for small titles or
        genuinely title-wide questions.

        Args:
            title: CFR title number (1-50).
            date: optional as-of date (YYYY-MM-DD). Defaults to the latest
                available date for this title if omitted.

        Returns:
            On success: {"text": "...", "citation": {...}}.
            On failure: {"error": true, "error_type": ..., "message": ...,
            "retryable": ...}.
        """
        try:
            req = RetrieveTitleRequest(title=title, date=date)
        except PydanticValidationError as exc:
            return build_error_response(exc)

        cache_key = build_cache_key("title", req.title, req.date)
        settings = get_settings()

        async def compute() -> dict[str, Any]:
            resolved_date = await ecfr_client.resolve_date(req.title, req.date)
            raw_xml = await ecfr_client.retrieve_title(req.title, date=req.date)
            parsed = parse_regulation_xml(raw_xml, title=req.title, date=resolved_date)
            response = RegulationTextResponse(
                text=parsed.text, citation=CitationModel.from_citation(parsed.citation)
            )
            return response.model_dump()

        try:
            return await cached_call(cache, cache_key, settings.cache_ttl_seconds, compute)
        except Exception as exc:  # noqa: BLE001
            return build_error_response(exc)

    return retrieve_title
