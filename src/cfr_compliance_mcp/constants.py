"""Constants for the eCFR API surface.

This module centralizes every eCFR endpoint path and fixed default used
by the client and tool layers. Nothing here reads environment variables
(that's config.py's job) — these are protocol-level facts about the
official eCFR API itself, not deployment-specific settings.

Official docs: https://www.ecfr.gov/developers/documentation/api/v1
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Versioner API — titles, structure, full text, versions, ancestry
# ---------------------------------------------------------------------------
TITLES_ENDPOINT: Final[str] = "/api/versioner/v1/titles.json"
STRUCTURE_ENDPOINT_TEMPLATE: Final[str] = "/api/versioner/v1/structure/{date}/title-{title}.json"
FULL_TEXT_ENDPOINT_TEMPLATE: Final[str] = "/api/versioner/v1/full/{date}/title-{title}.xml"
VERSIONS_ENDPOINT_TEMPLATE: Final[str] = "/api/versioner/v1/versions/title-{title}.json"
ANCESTRY_ENDPOINT_TEMPLATE: Final[str] = "/api/versioner/v1/ancestry/{date}/title-{title}.json"

# ---------------------------------------------------------------------------
# Search API
# ---------------------------------------------------------------------------
SEARCH_RESULTS_ENDPOINT: Final[str] = "/api/search/v1/results"
SEARCH_COUNT_HIERARCHY_ENDPOINT: Final[str] = "/api/search/v1/count_hierarchy"

# ---------------------------------------------------------------------------
# Admin API — agencies, corrections
# ---------------------------------------------------------------------------
AGENCIES_ENDPOINT: Final[str] = "/api/admin/v1/agencies.json"
CORRECTIONS_ENDPOINT: Final[str] = "/api/admin/v1/corrections.json"

# ---------------------------------------------------------------------------
# CFR structural bounds (used for input validation)
# ---------------------------------------------------------------------------
MIN_CFR_TITLE: Final[int] = 1
MAX_CFR_TITLE: Final[int] = 50

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_SEARCH_PER_PAGE: Final[int] = 20
MAX_SEARCH_PER_PAGE: Final[int] = 100
DEFAULT_SEARCH_PAGE: Final[int] = 1

# Sentinel used to tell the eCFR search API to only return currently
# in-force text, not historical/superseded versions.
SEARCH_DATE_CURRENT: Final[str] = "current"

# Date sentinel meaning "the latest date eCFR has content for" — resolved
# dynamically by the client rather than hardcoded, since eCFR lags the
# Federal Register by 1-2 business days.
LATEST_DATE_SENTINEL: Final[str] = "latest"
