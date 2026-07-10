"""MCP tool: get_version_history.

Fetches point-in-time version history for a CFR title, optionally
scoped to a part/section and/or filtered by issue-date range. Powers
"what rule was in effect on date X" compliance questions — e.g.
determining which version of a regulation applied when a contract was
signed, versus the version currently in force. Backed by
`EcfrClient.get_version_history()`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from cfr_compliance_mcp.cache import CacheBackend, build_cache_key
from cfr_compliance_mcp.clients import EcfrClient
from cfr_compliance_mcp.config import get_settings
from cfr_compliance_mcp.logging_config import get_logger
from cfr_compliance_mcp.models.requests import GetVersionHistoryRequest
from cfr_compliance_mcp.models.responses import VersionHistoryResponse
from cfr_compliance_mcp.tools._common import build_error_response, cached_call

logger = get_logger(__name__)

__all__ = ["make_get_version_history_tool"]


def make_get_version_history_tool(
    ecfr_client: EcfrClient, cache: CacheBackend
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Build the `get_version_history` MCP tool, bound to a specific
    `EcfrClient`/`CacheBackend` pair injected by `server.py` at startup."""

    async def get_version_history(
        title: int,
        part: str | None = None,
        section: str | None = None,
        issue_date_on: str | None = None,
        issue_date_lte: str | None = None,
        issue_date_gte: str | None = None,
    ) -> dict[str, Any]:
        """Fetch point-in-time version history for a CFR title, optionally
        scoped to a part/section and/or filtered by an issue-date range.

        Use this to determine which version of a regulation was in effect
        on a specific date (e.g. when a contract was signed), as opposed
        to `retrieve_section`/`retrieve_part`/`retrieve_title`, which
        return the text as of a date but not its full change history.

        Args:
            title: CFR title number (1-50).
            part: optional CFR part number to scope the history to.
            section: optional CFR section number to scope the history to.
            issue_date_on: optional exact issue date filter (YYYY-MM-DD).
            issue_date_lte: optional "on or before" issue date filter (YYYY-MM-DD).
            issue_date_gte: optional "on or after" issue date filter (YYYY-MM-DD).

        Returns:
            On success: {"title": ..., "versions": [...as returned by eCFR...]}.
            On failure: {"error": true, "error_type": ..., "message": ...,
            "retryable": ...}.
        """
        try:
            req = GetVersionHistoryRequest(
                title=title,
                part=part,
                section=section,
                issue_date_on=issue_date_on,
                issue_date_lte=issue_date_lte,
                issue_date_gte=issue_date_gte,
            )
        except PydanticValidationError as exc:
            return build_error_response(exc)

        cache_key = build_cache_key(
            "versions",
            req.title,
            req.part,
            req.section,
            req.issue_date_on,
            req.issue_date_lte,
            req.issue_date_gte,
        )
        settings = get_settings()

        async def compute() -> dict[str, Any]:
            raw = await ecfr_client.get_version_history(
                req.title,
                part=req.part,
                section=req.section,
                issue_date_on=req.issue_date_on,
                issue_date_lte=req.issue_date_lte,
                issue_date_gte=req.issue_date_gte,
            )
            # eCFR's versions payload shape has not been live-verified in
            # this build environment. Defensively unwrap the most likely
            # key ("content_versions", per the documented API), falling
            # back to treating the payload itself as the list, and
            # finally to an empty list rather than raising -- an
            # unexpected shape here shouldn't crash the tool when the
            # underlying data is otherwise valid.
            if isinstance(raw, dict) and isinstance(raw.get("content_versions"), list):
                versions = raw["content_versions"]
            elif isinstance(raw, list):
                versions = raw
            else:
                logger.warning(
                    "Unexpected get_version_history response shape; returning empty versions list",
                    extra={"title": req.title, "raw_type": type(raw).__name__},
                )
                versions = []

            response = VersionHistoryResponse(title=req.title, versions=versions)
            return response.model_dump()

        try:
            return await cached_call(cache, cache_key, settings.cache_ttl_seconds, compute)
        except Exception as exc:  # noqa: BLE001
            return build_error_response(exc)

    return get_version_history
