"""Pydantic output models: the structured JSON every MCP tool returns.

Architecture summary:
    - Every MCP tool returns one of these shapes (never a raw exception,
      never an unstructured dict) — see `tools/_common.py`'s
      `build_error_response` for the error path, and the caching
      strategy note below for why these models exist at all.
    - `CitationModel` is a JSON-serializable Pydantic mirror of
      `parsing.Citation`. It is intentionally a *separate* model rather
      than making `parsing.Citation` a Pydantic model directly — the
      parsing layer is documented as depending only on `exceptions.py`
      and `logging_config.py`, and adding a Pydantic dependency there
      would break that deliberately narrow scope for no real benefit.
      `CitationModel.from_citation()` is the one place the two meet.
    - Response models fall into two families:
        1. Models for data we fully control and are confident about the
           shape of (`CitationModel`, `RegulationTextResponse`,
           `TitleSummary`, `ErrorResponse`) — these use strict
           `extra="forbid"` validation.
        2. Models wrapping externally-controlled eCFR JSON shapes that
           have **not been live-verified** against the real API in this
           build environment (no network access) — `SearchResultItem`,
           and the `structure`/`versions`/`agencies` fields on their
           respective response models. These use `extra="allow"` or
           plain `dict[str, Any]` passthrough rather than strict
           field-by-field typing, so an eCFR field we didn't anticipate
           doesn't hard-fail validation. This is a deliberate,
           documented trade-off — not an oversight.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cfr_compliance_mcp.parsing import Citation

__all__ = [
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


class _StrictResponse(BaseModel):
    """Base for response models representing data shapes we fully
    control and are confident about."""

    model_config = ConfigDict(extra="forbid")


class _PassthroughResponse(BaseModel):
    """Base for response models wrapping externally-controlled eCFR JSON
    shapes not live-verified in this build environment. `extra="allow"`
    so an unanticipated upstream field passes through instead of
    hard-failing validation."""

    model_config = ConfigDict(extra="allow")


class CitationModel(_StrictResponse):
    """JSON-serializable mirror of `parsing.Citation`. See module
    docstring for why this is a separate model rather than sharing
    `parsing.Citation` directly."""

    title: int
    part: str | None = None
    section: str | None = None
    date: str
    heading: str | None = None
    url: str

    @classmethod
    def from_citation(cls, citation: Citation) -> CitationModel:
        return cls(**citation.as_dict())


class RegulationTextResponse(_StrictResponse):
    """Response shape for `retrieve_section`, `retrieve_part`, and
    `retrieve_title`: clean regulation text plus its citation."""

    text: str
    citation: CitationModel


class TitleSummary(_StrictResponse):
    """One entry from `EcfrClient.get_titles()`. Not currently returned
    directly by any of the 8 tools (none of them expose a raw
    "list all titles" tool), but kept here since `resolve_date` and
    several tools' internal logic consume this shape, and it documents
    the field set precisely — useful if a `list_titles` tool is added later.
    """

    number: int
    name: str
    latest_amended_on: str | None = None
    latest_issue_date: str | None = None
    up_to_date_as_of: str | None = None
    reserved: bool = False


class TitleStructureResponse(_StrictResponse):
    """Response shape for `get_title_structure`.

    eCFR's structure payload is a deeply nested, title-specific
    hierarchy (subtitle -> chapter -> subchapter -> part -> subpart ->
    section). Rather than fully modeling every possible node type — a
    large undertaking with no live API access to verify against this
    session — the hierarchy itself is passed through as `dict[str,
    Any]`. The calling agent consumes this as reference/navigation data
    to plan further tool calls, not as a value we need to validate
    field-by-field.
    """

    title: int
    date: str
    structure: dict[str, Any]


class SearchResultItem(_PassthroughResponse):
    """One match from the eCFR search API. Core fields per the
    documented eCFR search response shape are typed explicitly; any
    additional fields eCFR returns pass through via `extra="allow"`
    rather than being silently dropped or causing validation failure."""

    hierarchy: dict[str, Any] = Field(default_factory=dict)
    hierarchy_headings: dict[str, Any] = Field(default_factory=dict)
    headings: dict[str, Any] = Field(default_factory=dict)
    full_text_excerpt: str | None = None
    score: float | None = None
    starts_on: str | None = None
    ends_on: str | None = None
    type: str | None = None


class SearchResponse(_StrictResponse):
    """Response shape for `search_regulations` and `search_by_keyword`."""

    results: list[SearchResultItem]
    total_count: int
    current_page: int
    total_pages: int


class VersionHistoryResponse(_StrictResponse):
    """Response shape for `get_version_history`.

    `versions` is passed through as raw dicts (not strictly typed) for
    the same reason as `TitleStructureResponse.structure` — the exact
    eCFR versions payload shape has not been live-verified this session.
    """

    title: int
    versions: list[dict[str, Any]]


class AgenciesResponse(_StrictResponse):
    """Response shape for `list_agencies`.

    `agencies` is passed through as raw dicts rather than a strict
    `AgencyItem` model, since eCFR's agency records are hierarchical
    (agencies can have nested child agencies) and that nested shape has
    not been live-verified against the real API in this build
    environment. Strictly modeling it now risked silently dropping or
    rejecting real data once network access is available.
    """

    agencies: list[dict[str, Any]]


class ErrorResponse(_StrictResponse):
    """Structured error payload every tool returns instead of raising —
    see `tools/_common.py`'s `build_error_response`, which is the sole
    place this model is constructed."""

    error: bool = True
    error_type: str
    message: str
    retryable: bool = False
