"""MCP tool: retrieve_section.

Fetches the exact legal text of one CFR section (e.g. 40 CFR 261.10),
parses it into clean text + citation, and returns structured JSON.
Backed by `EcfrClient.retrieve_section()` + `parsing.parse_regulation_xml()`.
This is the most precise retrieval granularity and the one the
compliance pipeline is expected to use most often — one contract clause
typically maps to one section's worth of legal text.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from cfr_compliance_mcp.cache import CacheBackend, build_cache_key
from cfr_compliance_mcp.clients import EcfrClient
from cfr_compliance_mcp.config import get_settings
from cfr_compliance_mcp.logging_config import get_logger
from cfr_compliance_mcp.models.requests import RetrieveSectionRequest
from cfr_compliance_mcp.models.responses import CitationModel, RegulationTextResponse
from cfr_compliance_mcp.parsing import parse_regulation_xml
from cfr_compliance_mcp.tools._common import build_error_response, cached_call

logger = get_logger(__name__)

__all__ = ["make_retrieve_section_tool"]


def make_retrieve_section_tool(
    ecfr_client: EcfrClient, cache: CacheBackend
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Build the `retrieve_section` MCP tool, bound to a specific
    `EcfrClient`/`CacheBackend` pair injected by `server.py` at startup."""

    async def retrieve_section(
        title: int, part: str, section: str, date: str | None = None
    ) -> dict[str, Any]:
        """Retrieve the exact legal text of one CFR section (e.g. 40 CFR 261.10).

        Args:
            title: CFR title number (1-50).
            part: CFR part number, e.g. "261".
            section: CFR section number, e.g. "10".
            date: optional as-of date (YYYY-MM-DD). Defaults to the latest
                available date for this title if omitted.

        Returns:
            On success: {"text": "...", "citation": {"title": ..., "part": ...,
            "section": ..., "date": ..., "heading": ..., "url": ...}}.
            On failure: {"error": true, "error_type": ..., "message": ...,
            "retryable": ...}.
        """
        try:
            req = RetrieveSectionRequest(title=title, part=part, section=section, date=date)
        except PydanticValidationError as exc:
            return build_error_response(exc)

        cache_key = build_cache_key("section", req.title, req.part, req.section, req.date)
        settings = get_settings()

        async def compute() -> dict[str, Any]:
            resolved_date = await ecfr_client.resolve_date(req.title, req.date)
            raw_xml = await ecfr_client.retrieve_section(
                req.title, req.part, req.section, date=req.date
            )
            parsed = parse_regulation_xml(
                raw_xml, title=req.title, date=resolved_date, part=req.part, section=req.section
            )
            response = RegulationTextResponse(
                text=parsed.text, citation=CitationModel.from_citation(parsed.citation)
            )
            return response.model_dump()

        try:
            return await cached_call(cache, cache_key, settings.cache_ttl_seconds, compute)
        except Exception as exc:  # noqa: BLE001 -- tool boundary: never leak raw exceptions to MCP transport
            return build_error_response(exc)

    return retrieve_section
