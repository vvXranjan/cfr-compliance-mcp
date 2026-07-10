"""Async client for the official eCFR REST API.

Official docs: https://www.ecfr.gov/developers/documentation/api/v1
Base URL:      https://www.ecfr.gov
Auth:          none — the eCFR API is fully public.

This module is the *only* place in the codebase that knows eCFR's actual
endpoint paths, query parameters, and behavioral quirks (date lag,
search-history pollution, etc). It is built on top of the API-agnostic
`HttpClient` from `http_client.py` and translates that layer's generic
`Http*Error` exceptions into the eCFR-specific `Ecfr*Error` hierarchy
defined in `exceptions.py`, so every downstream caller (the tool layer)
only ever needs to catch eCFR-flavored exceptions.

This client returns raw eCFR data (JSON dicts for metadata endpoints,
raw XML strings for content endpoints). It does not parse or clean XML
— that is the responsibility of `parsing/xml_parser.py`. Keeping this
client "dumb" (talk to eCFR, translate errors, nothing else) keeps it
easy to test and easy to trust.
"""

from __future__ import annotations

from typing import Any

from cfr_compliance_mcp import constants
from cfr_compliance_mcp.clients.http_client import (
    HttpClient,
    HttpClientError,
    HttpConnectionError,
    HttpNotFoundError,
    HttpRateLimitedError,
    HttpServerError,
    HttpTimeoutError,
)
from cfr_compliance_mcp.config import Settings, get_settings
from cfr_compliance_mcp.exceptions import (
    EcfrApiError,
    EcfrConnectionError,
    EcfrNotFoundError,
    EcfrRateLimitedError,
    EcfrServerError,
    EcfrTimeoutError,
    ValidationError,
)
from cfr_compliance_mcp.logging_config import get_logger

logger = get_logger(__name__)

_USER_AGENT = "cfr-compliance-mcp/0.1 (internal contract-compliance POC)"


class EcfrClient:
    """Async client exposing the official eCFR API as typed Python methods.

    All methods are `async` and expect to be called with an already-
    started `HttpClient` (see `create_ecfr_client()` at the bottom of
    this module for the standard way to construct one).
    """

    def __init__(self, http_client: HttpClient) -> None:
        self._http = http_client
        # Per-title cache of the latest date eCFR has content for.
        # Populated lazily by `_resolve_date`. Process-lifetime cache —
        # cleared only by restarting the server, which is acceptable
        # since eCFR only advances forward in time during a run.
        self._latest_date_cache: dict[int, str] = {}
        self._full_title_xml_cache: dict[tuple[int, str], str] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_title(title: int) -> None:
        if not (constants.MIN_CFR_TITLE <= title <= constants.MAX_CFR_TITLE):
            raise ValidationError(
                f"CFR title must be between {constants.MIN_CFR_TITLE} and "
                f"{constants.MAX_CFR_TITLE}, got {title}."
            )

    async def _request(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """Issue a GET through the underlying HttpClient, translating the
        generic Http*Error hierarchy into the eCFR-specific one.

        Returns the raw `httpx.Response` so callers can choose `.json()`
        or `.text` depending on the endpoint.
        """
        try:
            return await self._http.get(path, params=params, headers={"User-Agent": _USER_AGENT})
        except HttpNotFoundError as exc:
            raise EcfrNotFoundError(str(exc), status_code=exc.status_code, url=exc.url) from exc
        except HttpRateLimitedError as exc:
            raise EcfrRateLimitedError(str(exc), status_code=exc.status_code, url=exc.url) from exc
        except HttpServerError as exc:
            raise EcfrServerError(str(exc), status_code=exc.status_code, url=exc.url) from exc
        except HttpTimeoutError as exc:
            raise EcfrTimeoutError(str(exc), url=exc.url) from exc
        except HttpConnectionError as exc:
            raise EcfrConnectionError(str(exc), url=exc.url) from exc
        except HttpClientError as exc:
            raise EcfrApiError(str(exc), status_code=exc.status_code, url=exc.url) from exc

    async def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        response = await self._request(path, params=params)
        return response.json()

    async def _get_text(self, path: str, *, params: dict[str, Any] | None = None) -> str:
        response = await self._request(path, params=params)
        return response.text

    async def _resolve_date(self, title: int, date: str | None) -> str:
        """Resolve the date to use for versioner endpoints (`/structure/`,
        `/full/`).

        eCFR content lags the Federal Register by 1-2 business days, so
        naively requesting today's date on these endpoints frequently
        404s. If the caller does not supply an explicit `date`, we look
        up that title's `up_to_date_as_of` field from the titles index
        and use it as the latest safe date — mirroring the approach
        documented by existing community eCFR tooling as the standard
        workaround for this quirk.
        """
        if date is not None:
            return date

        if title not in self._latest_date_cache:
            titles_response = await self.get_titles()
            for entry in titles_response.get("titles", []):
                if entry.get("number") == title:
                    self._latest_date_cache[title] = entry["up_to_date_as_of"]
                    break
            else:
                raise EcfrNotFoundError(f"CFR title {title} was not found in the eCFR titles index.")

        return self._latest_date_cache[title]

    async def resolve_date(self, title: int, date: str | None = None) -> str:
        """Public wrapper around `_resolve_date`.

        Added for the tool layer (built after this client): a tool like
        `retrieve_section` needs to know the *concrete* date actually
        used for a retrieval — to attach to the response's citation
        metadata — even when the caller didn't pass one explicitly and
        the date was auto-resolved. Calling the private `_resolve_date`
        from outside this class would violate the module's own
        encapsulation; this method is the sanctioned public entry point
        for that need. Behavior is identical to `_resolve_date` — this
        is purely a visibility change, not new logic.
        """
        return await self._resolve_date(title, date)

    async def _fetch_full_title_xml(self, title: int, date: str | None) -> str:
        resolved_date = await self._resolve_date(title, date)
        cache_key = (title, resolved_date)

        if cache_key in self._full_title_xml_cache:
            return self._full_title_xml_cache[cache_key]

        path = constants.FULL_TEXT_ENDPOINT_TEMPLATE.format(
            date=resolved_date,
            title=title,
        )

        raw_xml = await self._get_text(path)
        self._full_title_xml_cache[cache_key] = raw_xml

        return raw_xml

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_titles(self) -> dict[str, Any]:
        """Return metadata for all 50 CFR titles.

        Each entry includes `number`, `name`, `latest_amended_on`,
        `latest_issue_date`, `up_to_date_as_of`, and `reserved`. Used
        internally by `_resolve_date`, and directly useful to callers
        that want a title index or a freshness check.
        """
        logger.info("Fetching eCFR titles index")
        return await self._get_json(constants.TITLES_ENDPOINT)

    async def get_structure(self, title: int, date: str | None = None) -> dict[str, Any]:
        """Return the full hierarchical structure of a CFR title
        (subtitle -> chapter -> subchapter -> part -> subpart -> section)
        as of the given date, without pulling any regulation text.

        Lets a caller (agent) navigate/plan which part or section to
        pull before spending tokens on full text.
        """
        self._validate_title(title)
        resolved_date = await self._resolve_date(title, date)
        path = constants.STRUCTURE_ENDPOINT_TEMPLATE.format(date=resolved_date, title=title)
        logger.info("Fetching structure: title=%s date=%s", title, resolved_date)
        return await self._get_json(path)

    async def retrieve_section(
        self, title: int, part: str, section: str, date: str | None = None
    ) -> str:
        """Fetch the raw XML text of one specific CFR section.

        This is the most precise retrieval granularity, and the one the
        compliance pipeline will use most often — one contract clause
        typically maps to one section's worth of legal text. Returns
        raw XML; cleaning/citation extraction happens in the parsing
        layer, not here.
        """
        self._validate_title(title)
        resolved_date = await self._resolve_date(title, date)
        path = constants.FULL_TEXT_ENDPOINT_TEMPLATE.format(date=resolved_date, title=title)
        logger.info(
            "Retrieving section: %s CFR %s.%s as of %s", title, part, section, resolved_date
        )
        return await self._fetch_full_title_xml(title, date)

    async def retrieve_part(self, title: int, part: str, date: str | None = None) -> str:
        """Fetch the raw XML text of an entire CFR part (a cluster of
        related sections).

        Use when a clause maps to a broader regulatory topic rather than
        one exact section. Can still return a large payload for big
        parts — the caller should be prepared for that.
        """
        self._validate_title(title)
        resolved_date = await self._resolve_date(title, date)
        path = constants.FULL_TEXT_ENDPOINT_TEMPLATE.format(date=resolved_date, title=title)
        logger.info("Retrieving part: %s CFR %s as of %s", title, part, resolved_date)
        return await self._fetch_full_title_xml(title, date)

    async def retrieve_title(self, title: int, date: str | None = None) -> str:
        """Fetch the raw XML text of an entire CFR title.

        WARNING: some titles (notably Title 40 / EPA) are large enough
        that the upstream eCFR API itself times out (HTTP 504) on a
        full-title request. This method does not implement chunking —
        that decision belongs to the tool layer, which has context
        about what the caller actually needs. Prefer `retrieve_part` or
        `retrieve_section` whenever the caller can scope the request.
        """
        self._validate_title(title)
        resolved_date = await self._resolve_date(title, date)
        path = constants.FULL_TEXT_ENDPOINT_TEMPLATE.format(date=resolved_date, title=title)
        logger.info("Retrieving full title: %s as of %s", title, resolved_date)
        return await self._fetch_full_title_xml(title, date)

    async def get_version_history(
        self,
        title: int,
        *,
        part: str | None = None,
        section: str | None = None,
        issue_date_on: str | None = None,
        issue_date_lte: str | None = None,
        issue_date_gte: str | None = None,
    ) -> dict[str, Any]:
        """Return point-in-time version history for a title, optionally
        scoped to a part/section and/or filtered by issue date.

        This powers "what rule was in effect on date X" questions —
        e.g. determining which version of a regulation applied on the
        date a contract was signed, versus the version in force today.
        """
        self._validate_title(title)
        params: dict[str, Any] = {}
        if part is not None:
            params["part"] = part
        if section is not None:
            params["section"] = section
        if issue_date_on is not None:
            params["issue_date[on]"] = issue_date_on
        if issue_date_lte is not None:
            params["issue_date[lte]"] = issue_date_lte
        if issue_date_gte is not None:
            params["issue_date[gte]"] = issue_date_gte

        path = constants.VERSIONS_ENDPOINT_TEMPLATE.format(title=title)
        logger.info("Fetching version history: title=%s", title, extra={"params": params})
        return await self._get_json(path, params=params or None)

    async def list_agencies(self) -> dict[str, Any]:
        """Return all agencies referenced in the CFR, including their
        title/chapter cross-references.

        Useful for mapping a contract clause's implied regulator (e.g.
        "environmental", "labor", "acquisition") to the correct CFR
        title before running a search.
        """
        logger.info("Fetching agencies list")
        return await self._get_json(constants.AGENCIES_ENDPOINT)

    async def search(
        self,
        query: str,
        *,
        agency_slugs: list[str] | None = None,
        date: str | None = constants.SEARCH_DATE_CURRENT,
        per_page: int = constants.DEFAULT_SEARCH_PER_PAGE,
        page: int = constants.DEFAULT_SEARCH_PAGE,
    ) -> dict[str, Any]:
        """Full-text / keyword search across the CFR.

        IMPORTANT eCFR quirk: without `date="current"`, the search API
        returns matches from *every* historical version of the CFR,
        including superseded text — producing duplicate and stale
        results. This method defaults `date` to `"current"` for exactly
        that reason. Pass `date=None` explicitly only if historical
        (including superseded) results are genuinely wanted.

        This single method backs both of the higher-level MCP tools
        `search_regulations` (free-text query) and `search_by_keyword`
        (keyword-list query) that will be built in the tools layer —
        both are just different ways of building the `query` string
        before calling this same client method.
        """
        if not query or not query.strip():
            raise ValidationError("Search query must not be empty.")
        if not (1 <= per_page <= constants.MAX_SEARCH_PER_PAGE):
            raise ValidationError(
                f"per_page must be between 1 and {constants.MAX_SEARCH_PER_PAGE}, got {per_page}."
            )
        if page < 1:
            raise ValidationError(f"page must be >= 1, got {page}.")

        params: dict[str, Any] = {"query": query, "per_page": per_page, "page": page}
        if date:
            params["date"] = date
        if agency_slugs:
            params["agency_slugs[]"] = agency_slugs

        logger.info(
            "Searching eCFR",
            extra={"query": query, "page": page, "per_page": per_page, "date": date},
        )
        return await self._get_json(constants.SEARCH_RESULTS_ENDPOINT, params=params)


def create_ecfr_client(settings: Settings | None = None) -> tuple[HttpClient, EcfrClient]:
    """Construct a matched `(HttpClient, EcfrClient)` pair from settings.

    Returns the `HttpClient` alongside the `EcfrClient` so the caller
    (`server.py`, in the next milestone) can manage the HTTP client's
    lifecycle explicitly — calling `await http_client.start()` at server
    startup and `await http_client.aclose()` at shutdown — while the
    `EcfrClient` itself stays a thin, stateless-except-for-date-cache
    wrapper around it.
    """
    resolved_settings = settings or get_settings()
    http_client = HttpClient(
        base_url=resolved_settings.ecfr_base_url,
        timeout_seconds=resolved_settings.ecfr_request_timeout_seconds,
        max_retries=resolved_settings.ecfr_max_retries,
        retry_backoff_base_seconds=resolved_settings.ecfr_retry_backoff_base_seconds,
        max_requests_per_minute=resolved_settings.ecfr_max_requests_per_minute,
        default_headers={"User-Agent": _USER_AGENT},
    )
    return http_client, EcfrClient(http_client)