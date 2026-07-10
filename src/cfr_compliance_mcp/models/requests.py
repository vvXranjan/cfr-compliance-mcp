"""Pydantic input-validation models for the 8 MCP tools.

Architecture summary:
    - One request model per MCP tool (or shared base classes where tools
      have identical parameter shapes, e.g. all title-scoped retrieval
      tools). These are the tool layer's *first* line of defense: an
      agent calling a tool with a malformed argument (title=99, an
      empty query, a badly-formatted date) fails fast here, before any
      network call, cache lookup, or XML parsing happens.
    - Depends only on `constants.py` (for CFR title bounds and search
      defaults) — no dependency on `clients`, `cache`, or `parsing`,
      matching the dependency graph in `PROJECT_HANDOFF.md`.
    - `extra="forbid"` on every request model: these are *our* tool
      contracts, not externally-controlled schemas, so an agent passing
      an unrecognized/typo'd parameter should fail loudly rather than
      have it silently ignored.
    - Validation failures raise `pydantic.ValidationError`. The tool
      layer (built next) is responsible for catching that and
      translating it into the project's structured `ErrorResponse`
      shape (see `models/responses.py` and `tools/_common.py`) — this
      module does not import or raise `exceptions.ValidationError`
      itself, to avoid conflating "a Pydantic validation failure" with
      "our own hand-written validation failure" (the client layer still
      raises the latter for its own defense-in-depth checks).
"""

from __future__ import annotations

from datetime import date as _date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cfr_compliance_mcp import constants

__all__ = [
    "SearchRegulationsRequest",
    "SearchByKeywordRequest",
    "RetrieveSectionRequest",
    "RetrievePartRequest",
    "RetrieveTitleRequest",
    "GetTitleStructureRequest",
    "GetVersionHistoryRequest",
    "ListAgenciesRequest",
]

_DATE_PATTERN = "YYYY-MM-DD"


def _validate_date(value: str | None) -> str | None:
    """Shared date validator: `None` passes through unchanged (meaning
    "auto-resolve"); a non-`None` value must be a real, valid calendar
    date in exactly `YYYY-MM-DD` format.

    Two checks are combined deliberately:
      1. An exact-shape check (`len == 10` and dashes at positions 4/7)
         — `date.fromisoformat` alone is *not* sufficient here: as of
         Python 3.11+ it also accepts basic ISO format with no
         separators (e.g. "20260101"), which is not the `YYYY-MM-DD`
         shape this API documents or that eCFR expects.
      2. `date.fromisoformat` itself, to catch shape-valid-but-impossible
         dates like "2026-13-40" (month 13) or "2026-02-30" (Feb 30th)
         that a regex-only check would miss.
    Both failure modes were caught by this session's functional testing
    before this fix was made.
    """
    if value is None:
        return value
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise ValueError(
            f"date must be a valid calendar date in {_DATE_PATTERN} format, got {value!r}."
        )
    try:
        _date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"date must be a valid calendar date in {_DATE_PATTERN} format, got {value!r}."
        ) from exc
    return value


class _BaseRequest(BaseModel):
    """Shared config for every request model."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _TitleScopedRequest(_BaseRequest):
    """Shared base for the four tools scoped to a single CFR title:
    retrieve_title, get_title_structure, retrieve_part, retrieve_section.
    """

    title: int = Field(
        ...,
        ge=constants.MIN_CFR_TITLE,
        le=constants.MAX_CFR_TITLE,
        description="CFR title number (1-50).",
    )
    date: str | None = Field(
        default=None,
        description=(
            "As-of date (YYYY-MM-DD). If omitted, resolves automatically to "
            "the latest date eCFR has content for this title."
        ),
    )

    @field_validator("date")
    @classmethod
    def _check_date(cls, v: str | None) -> str | None:
        return _validate_date(v)


class RetrieveTitleRequest(_TitleScopedRequest):
    """Input for `retrieve_title`: fetch an entire CFR title's text.

    No additional fields beyond title/date — but see the tool's own
    docstring for the large-title timeout warning inherited from
    `EcfrClient.retrieve_title`.
    """


class GetTitleStructureRequest(_TitleScopedRequest):
    """Input for `get_title_structure`: fetch a title's hierarchy
    (subtitle -> chapter -> ... -> section) without retrieving any
    regulation text."""


class RetrievePartRequest(_TitleScopedRequest):
    """Input for `retrieve_part`: fetch one CFR part's text."""

    part: str = Field(..., min_length=1, description="CFR part number, e.g. '261'.")


class RetrieveSectionRequest(_TitleScopedRequest):
    """Input for `retrieve_section`: fetch one CFR section's text — the
    most common retrieval granularity for clause-level compliance checks.
    """

    part: str = Field(..., min_length=1, description="CFR part number, e.g. '261'.")
    section: str = Field(..., min_length=1, description="CFR section number, e.g. '10'.")


class GetVersionHistoryRequest(_BaseRequest):
    """Input for `get_version_history`: point-in-time version history for
    a title, optionally scoped to a part/section and/or an issue-date range.

    Not a `_TitleScopedRequest` subclass because it has no single `date`
    field — it has three independent optional date filters instead.
    """

    title: int = Field(..., ge=constants.MIN_CFR_TITLE, le=constants.MAX_CFR_TITLE)
    part: str | None = Field(default=None, min_length=1)
    section: str | None = Field(default=None, min_length=1)
    issue_date_on: str | None = None
    issue_date_lte: str | None = None
    issue_date_gte: str | None = None

    @field_validator("issue_date_on", "issue_date_lte", "issue_date_gte")
    @classmethod
    def _check_dates(cls, v: str | None) -> str | None:
        return _validate_date(v)


class SearchRegulationsRequest(_BaseRequest):
    """Input for `search_regulations`: free-text/phrase search across the CFR."""

    query: str = Field(..., min_length=1)
    agency_slugs: list[str] | None = Field(default=None)
    date: str | None = Field(
        default=constants.SEARCH_DATE_CURRENT,
        description=(
            'Defaults to "current" to exclude superseded historical matches — '
            "see EcfrClient.search() for why this default matters."
        ),
    )
    per_page: int = Field(
        default=constants.DEFAULT_SEARCH_PER_PAGE, ge=1, le=constants.MAX_SEARCH_PER_PAGE
    )
    page: int = Field(default=constants.DEFAULT_SEARCH_PAGE, ge=1)

    @field_validator("query")
    @classmethod
    def _non_blank_query(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query must not be empty or whitespace-only.")
        return v

    @field_validator("date")
    @classmethod
    def _check_date(cls, v: str | None) -> str | None:
        # "current" is a valid sentinel here, not a YYYY-MM-DD date --
        # only validate the format if it looks like an explicit date.
        if v is None or v == constants.SEARCH_DATE_CURRENT:
            return v
        return _validate_date(v)


class SearchByKeywordRequest(_BaseRequest):
    """Input for `search_by_keyword`: multi-keyword search, joined into a
    single query string before being handed to the same underlying
    `EcfrClient.search()` call `search_regulations` uses."""

    keywords: list[str] = Field(..., min_length=1)
    agency_slugs: list[str] | None = None
    date: str | None = Field(default=constants.SEARCH_DATE_CURRENT)
    per_page: int = Field(
        default=constants.DEFAULT_SEARCH_PER_PAGE, ge=1, le=constants.MAX_SEARCH_PER_PAGE
    )
    page: int = Field(default=constants.DEFAULT_SEARCH_PAGE, ge=1)

    @field_validator("keywords")
    @classmethod
    def _non_empty_keywords(cls, v: list[str]) -> list[str]:
        cleaned = [k.strip() for k in v if k and k.strip()]
        if not cleaned:
            raise ValueError("keywords must contain at least one non-empty term.")
        return cleaned

    @field_validator("date")
    @classmethod
    def _check_date(cls, v: str | None) -> str | None:
        if v is None or v == constants.SEARCH_DATE_CURRENT:
            return v
        return _validate_date(v)

    def build_query(self) -> str:
        """Join validated keywords into a single query string for
        `EcfrClient.search()`. Kept as a method on the request model
        (rather than duplicated logic in the tool function) since it's
        purely a function of this model's own validated data."""
        return " ".join(self.keywords)


class ListAgenciesRequest(_BaseRequest):
    """Input for `list_agencies`. No parameters today, but kept as an
    explicit (empty) model — rather than calling the tool with zero
    arguments and no validation layer — for interface consistency with
    the other 7 tools and to leave room for a future filter (e.g. by
    name) without changing the tool's calling convention."""
