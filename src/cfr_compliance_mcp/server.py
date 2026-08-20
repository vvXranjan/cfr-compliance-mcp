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
    - `HttpClient`'s lifecycle is owned entirely by FastMCP's `lifespan`
      API: `_make_lifespan()` builds an async context manager, bound to
      the single process-lifetime `AppResources` instance, that calls
      `start()` before the server begins serving requests and
      `aclose()` on shutdown (in a `finally` block, so the connection
      pool is always released, including on error or
      `KeyboardInterrupt`). FastMCP invokes this lifespan itself
      whenever it runs the server — via `mcp.run_async()` in `main()`,
      or via any other FastMCP-driven entrypoint such as `fastmcp dev`,
      the FastMCP Inspector, or `create_fastmcp()`. Because of this,
      `_run()` no longer manages `HttpClient` lifecycle itself: doing so
      only worked for the `uv run cfr-compliance-mcp` / `python
      server.py` code path where `_run()` actually executes, and left
      `HttpClient` unstarted (raising "HttpClient used before start()")
      under any launch path that goes through FastMCP directly instead.

KNOWN RISK (disclosed, not hidden): this module's exact calls into the
`fastmcp` package (`FastMCP(...)`, `mcp.tool()`, `mcp.run_async(...)`,
and the `lifespan=` constructor parameter) are written against the
documented FastMCP v3.x API pattern from training knowledge. The
`lifespan` parameter and its `Callable[[FastMCP], AbstractAsyncContextManager[None]]`
signature have been cross-checked against the FastMCP 3.4.4 environment
description supplied for this task, but have **not** been verified
against a live `fastmcp` installation or the current gofastmcp.com docs
in this session — this sandbox has no network access, so `fastmcp`
could not be installed or imported to confirm. This should be the
*first* thing verified once network access is available (`uv sync`,
then `uv run cfr-compliance-mcp --help`, `fastmcp dev server.py`, or
equivalent), before relying on this module in any real environment.
Every other piece of this file (resource wiring, tool registration
order, shutdown handling) is ordinary Python and has been reviewed
accordingly.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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

__all__ = [
    "create_app",
    "create_fastmcp",
    "mcp",
    "main",
]

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
    variables) so `create_app()` has exactly one object to build and
    hand to both the tool factories and the FastMCP `lifespan`, and so
    a caller (e.g. a test) can get the fully-wired app back along with
    its resources without relying on global state. `HttpClient`'s
    `start()`/`aclose()` calls are made against *this* instance's
    `http_client` from within the lifespan built by `_make_lifespan()`
    -- there is exactly one `AppResources` (and therefore exactly one
    `HttpClient`) per process, constructed once in `create_app()`.
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


def _make_lifespan(resources: AppResources):
    """Build the FastMCP `lifespan` async context manager for `resources`.

    This is the single owner of `HttpClient`'s start/stop lifecycle.
    FastMCP calls this context manager itself around serving requests,
    regardless of how the server is launched (`main()`'s
    `mcp.run_async()`, `fastmcp dev`, the FastMCP Inspector,
    `create_fastmcp()`, etc.) -- so binding lifecycle management here,
    rather than in `_run()`, guarantees `resources.http_client.start()`
    always runs before any tool can use it, and `resources.http_client
    .aclose()` always runs on shutdown, no matter which entrypoint
    started the server.

    Takes `resources` as a closure argument (rather than constructing
    its own) so the exact same `AppResources` -- and therefore the exact
    same `HttpClient` -- used to build the registered tools is the one
    whose lifecycle this context manager manages. `create_app()` builds
    `resources` once and passes it both to `_register_tools()` and to
    this factory, so no second `HttpClient` is ever created.
    """

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[None]:
        await resources.http_client.start()
        logger.info("cfr-compliance-mcp starting",
             extra={
                "transport": get_settings().mcp_transport,
                "cache_backend": get_settings().cache_backend,
             },
        )
        try:
            yield
        finally:
            logger.info("cfr-compliance-mcp shutting down")
            await resources.http_client.aclose()

    return lifespan


def create_app() -> tuple[FastMCP, AppResources]:
    """Build the FastMCP application and its backing resources.

    Builds `resources` exactly once and shares that single instance
    between the registered tools and the FastMCP `lifespan` (via
    `_make_lifespan(resources)`), so there is exactly one `HttpClient`
    per app. `HttpClient.start()`/`aclose()` are no longer called here
    or in `main()` -- FastMCP invokes the lifespan itself whenever it
    runs the server, which is what makes this app work correctly under
    `fastmcp dev`, the FastMCP Inspector, and `create_fastmcp()`, not
    just under `main()`'s own `mcp.run_async()` call.
    """
    configure_logging()
    settings = get_settings()
    resources = _build_resources(settings)
    mcp = FastMCP(
        name="cfr-compliance-mcp",
        lifespan=_make_lifespan(resources),
    )
    _register_tools(mcp, resources)
    return mcp, resources


def create_fastmcp() -> FastMCP:
    """Build and return just the FastMCP app, discarding its resources handle.

    Exists for FastMCP-driven entrypoints (`fastmcp dev server.py`, the
    FastMCP Inspector, etc.) that expect a zero-argument factory
    returning a single `FastMCP` instance rather than the
    `(FastMCP, AppResources)` tuple `create_app()` returns. The
    `AppResources` returned by `create_app()` are intentionally
    discarded here (`_`) -- they are still fully wired into the app's
    tools and `lifespan`, so nothing is lost by not keeping a separate
    reference to them at this call site.
    """
    mcp, _ = create_app()
    return mcp


# Module-level FastMCP server for CLI auto-discovery.
mcp = create_fastmcp()


async def _run(mcp: FastMCP, settings: Settings) -> None:
    """Run the server until it stops.

    `HttpClient` lifecycle is no longer managed here -- it is owned
    entirely by the `lifespan` context manager FastMCP was constructed
    with in `create_app()` (see `_make_lifespan()`), which FastMCP
    enters/exits itself around `run_async()`. This function's only job
    now is choosing the transport.
    """
    logger.info(
        "cfr-compliance-mcp configured",
        extra={"transport": settings.mcp_transport, "cache_backend": settings.cache_backend},
    )
    if settings.mcp_transport == "streamable-http":
        await mcp.run_async(
            transport="streamable-http",
            host=settings.mcp_http_host,
            port=settings.mcp_http_port,
        )
    else:
        await mcp.run_async(transport="stdio")


def main() -> None:
    """Console-script entrypoint (`uv run cfr-compliance-mcp`)."""
    settings = get_settings()
    mcp, _resources = create_app()
    try:
        asyncio.run(_run(mcp, settings))
    except KeyboardInterrupt:
        logger.info("cfr-compliance-mcp interrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    main()