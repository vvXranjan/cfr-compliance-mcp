"""Custom exception hierarchy for cfr-compliance-mcp.

Every exception raised by our own code (not third-party libraries)
should inherit from `CfrMcpError`. This lets tool-layer code catch
exactly the failure classes it cares about (e.g. "was this a bad
input?" vs "was this an upstream API outage?") and translate them into
structured, LLM-readable error responses instead of leaking raw
tracebacks or ambiguous generic exceptions across the MCP boundary.
"""

from __future__ import annotations


class CfrMcpError(Exception):
    """Base class for all errors raised intentionally by this package."""


class ValidationError(CfrMcpError):
    """Raised when tool input fails validation before any network call is made.

    Distinguishing this from `EcfrApiError` matters because validation
    failures are the caller's (the agent's) mistake and should never be
    retried, whereas API errors may be transient.
    """


class EcfrApiError(CfrMcpError):
    """Base class for all failures originating from the eCFR API itself."""

    def __init__(self, message: str, *, status_code: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class EcfrNotFoundError(EcfrApiError):
    """Raised on HTTP 404 — e.g. requesting a section/date combination that
    doesn't exist (a common eCFR gotcha: requesting "today" on the versioner
    endpoints before eCFR has published that day's snapshot)."""


class EcfrRateLimitedError(EcfrApiError):
    """Raised on HTTP 429 — our client should back off and retry."""


class EcfrServerError(EcfrApiError):
    """Raised on HTTP 5xx — transient upstream failure, safe to retry."""


class EcfrTimeoutError(EcfrApiError):
    """Raised when a request to eCFR exceeds the configured timeout.

    Large titles (e.g. Title 40) are known to time out on full-title
    pulls; callers should prefer part/section-scoped requests.
    """


class EcfrConnectionError(EcfrApiError):
    """Raised on network-level failures (DNS, connection refused, etc.)."""


class XmlParsingError(CfrMcpError):
    """Raised when eCFR XML content cannot be parsed into clean text."""


class CacheError(CfrMcpError):
    """Raised on cache backend failures. Callers should treat cache
    failures as non-fatal where possible (fall through to a live API
    call) rather than surfacing this to the end user."""
