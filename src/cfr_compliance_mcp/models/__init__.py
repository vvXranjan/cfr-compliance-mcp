"""Pydantic request/response models for the 8 MCP tools."""

from cfr_compliance_mcp.models.requests import (
    GetTitleStructureRequest,
    GetVersionHistoryRequest,
    ListAgenciesRequest,
    RetrievePartRequest,
    RetrieveSectionRequest,
    RetrieveTitleRequest,
    SearchByKeywordRequest,
    SearchRegulationsRequest,
)
from cfr_compliance_mcp.models.responses import (
    AgenciesResponse,
    CitationModel,
    ErrorResponse,
    RegulationTextResponse,
    SearchResponse,
    SearchResultItem,
    TitleStructureResponse,
    TitleSummary,
    VersionHistoryResponse,
)

__all__ = [
    # requests
    "SearchRegulationsRequest",
    "SearchByKeywordRequest",
    "RetrieveSectionRequest",
    "RetrievePartRequest",
    "RetrieveTitleRequest",
    "GetTitleStructureRequest",
    "GetVersionHistoryRequest",
    "ListAgenciesRequest",
    # responses
    "CitationModel",
    "RegulationTextResponse",
    "TitleSummary",
    "TitleStructureResponse",
    "SearchResultItem",
    "SearchResponse",
    "VersionHistoryResponse",
    "AgenciesResponse",
    "ErrorResponse",
]
