"""FastMCP server entrypoint for cfr-compliance-mcp.

Wires together every layer built so far into a runnable MCP server:
    - `configure_logging()` runs first, before anything else, so every
      subsequent log line (including startup/shutdown) is captured, and
      goes to stderr only (required for the `stdio` transport).
    - `create_ecfr_client()` and `create_cache_backend()` build the
      single, process-lifetime `EcfrClient`/`CacheBackend` instances
      every tool shares — built once here, not per-request.
    - Each of the 8 tool factory functions (`tools/__init__.py`) is
      called once with those shared instances; the resulting callables
      are registered with FastMCP.
    - `HttpClient`'s lifecycle is managed explicitly: `start()` before
      the server begins serving requests, `aclose()` in a `finally`
      block so the connection pool is always released on shutdown,
      including on `KeyboardInterrupt`.

KNOWN RISK (disclosed, not hidden): this module's exact calls into the
`fastmcp` package (`FastMCP(...)`, `mcp.tool()`, `mcp.run_async(...)`)
are written against the documented FastMCP v3.x API pattern from
training knowledge, but have **not** been verified against a live
`fastmcp` installation or the current gofastmcp.com docs in this
session — this sandbox has no network access, so `fastmcp` could not be
installed or imported to confirm. This should be the *first* thing
verified once network access is available (`uv sync`, then
`uv run cfr-compliance-mcp --help` or equivalent), before relying on
this module in any real environment. Every other piece of this file
(resource wiring, tool registration order, shutdown handling) is
ordinary Python and has been reviewed accordingly.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from fastmcp import FastMCP

from cfr_compliance_mcp.cache import CacheBackend, create_cache_backend
from cfr_compliance_mcp.clients import EcfrClient, HttpClient, create_ecfr_client
from cfr_compliance_mcp.config import Settings, get_settings
from cfr_compliance_mcp.logging_config import configure_logging, get_logger
from cfr_compliance_mcp.tools import (
    make_get_title_structure_tool,
    make_get_version_history_tool,
    make_list_agencies_tool,
    make_retrieve_part_tool,
    make_retrieve_section_tool,
    make_retrieve_title_tool,
    make_search_by_keyword_tool,
    make_search_regulations_tool,
)

logger = get_logger(__name__)

__all__ = ["create_app", "main"]

# Every tool factory this server registers. Order matches the
# originally-specified tool list in PROJECT_HANDOFF.md Section 24 --
# kept as an explicit, reviewable list (rather than discovered via
# reflection/introspection) so adding or removing a tool is a one-line,
# obviously-visible change here.
_TOOL_FACTORIES = [
    make_search_regulations_tool,
    make_search_by_keyword_tool,
    make_retrieve_section_tool,
    make_retrieve_part_tool,
    make_retrieve_title_tool,
    make_get_title_structure_tool,
    make_get_version_history_tool,
    make_list_agencies_tool,
]


@dataclass(slots=True)
class AppResources:
    """Process-lifetime resources owned by the server.

    Bundled into one dataclass (rather than passed around as loose
    variables) so `main()` has exactly one thing to start and one thing
    to close, and so `create_app()` can hand both the FastMCP app and
    its resources back to a caller (e.g. a test) without global state.
    """

    http_client: HttpClient
    ecfr_client: EcfrClient
    cache: CacheBackend


def _build_resources(settings: Settings) -> AppResources:
    """Construct the shared EcfrClient/CacheBackend pair from settings.

    Uses the same `create_ecfr_client()`/`create_cache_backend()`
    factories already built and tested in the clients/cache layers --
    this function's only job is bundling their outputs, not
    reimplementing any construction logic.
    """
    http_client, ecfr_client = create_ecfr_client(settings)
    cache = create_cache_backend(settings)
    return AppResources(http_client=http_client, ecfr_client=ecfr_client, cache=cache)


def _register_tools(mcp: FastMCP, resources: AppResources) -> None:
    """Build and register all 8 MCP tools, each bound to the shared
    EcfrClient/CacheBackend via its factory function.

    Each factory in `_TOOL_FACTORIES` returns a plain async callable
    with only MCP-facing parameters in its signature (see each tool
    module's docstring) -- this function's only job is calling each
    factory once and handing the result to FastMCP.
    """
    for factory in _TOOL_FACTORIES:
        tool_fn = factory(resources.ecfr_client, resources.cache)
        mcp.tool()(tool_fn)
        logger.debug("Registered MCP tool", extra={"tool": tool_fn.__name__})
    logger.info("All MCP tools registered", extra={"tool_count": len(_TOOL_FACTORIES)})


def create_app() -> tuple[FastMCP, AppResources]:
    """Build the FastMCP application and its backing resources.

    Deliberately does **not** start the HTTP client or run the server —
    split out from `main()` so a test (or an alternate entrypoint, e.g.
    an ASGI mount) can construct the fully-wired app without triggering
    the blocking server loop or opening a real network connection.
    """
    configure_logging()
    settings = get_settings()
    resources = _build_resources(settings)
    mcp = FastMCP(name="cfr-compliance-mcp")
    _register_tools(mcp, resources)
    return mcp, resources


async def _run(mcp: FastMCP, resources: AppResources, settings: Settings) -> None:
    """Start the HTTP client, run the server until it stops, then always
    close the HTTP client -- the `finally` block guarantees the
    connection pool is released even if the server exits via an
    exception or KeyboardInterrupt."""
    await resources.http_client.start()
    logger.info(
        "cfr-compliance-mcp starting",
        extra={"transport": settings.mcp_transport, "cache_backend": settings.cache_backend},
    )
    try:
        if settings.mcp_transport == "streamable-http":
            await mcp.run_async(
                transport="streamable-http",
                host=settings.mcp_http_host,
                port=settings.mcp_http_port,
            )
        else:
            await mcp.run_async(transport="stdio")
    finally:
        logger.info("cfr-compliance-mcp shutting down")
        await resources.http_client.aclose()


def main() -> None:
    """Console-script entrypoint (`uv run cfr-compliance-mcp`)."""
    settings = get_settings()
    mcp, resources = create_app()
    try:
        asyncio.run(_run(mcp, resources, settings))
    except KeyboardInterrupt:
        logger.info("cfr-compliance-mcp interrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
