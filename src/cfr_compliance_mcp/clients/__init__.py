"""HTTP client layer: a generic transport client plus the eCFR-specific client built on it."""

from cfr_compliance_mcp.clients.ecfr_client import EcfrClient, create_ecfr_client
from cfr_compliance_mcp.clients.http_client import HttpClient, HttpClientError

__all__ = [
    "EcfrClient",
    "create_ecfr_client",
    "HttpClient",
    "HttpClientError",
]
