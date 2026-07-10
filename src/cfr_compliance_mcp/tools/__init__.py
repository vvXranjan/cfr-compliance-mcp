"""The 8 MCP tools exposed by this server, each as a factory function
(`make_<tool>_tool(ecfr_client, cache) -> Callable`) that `server.py`
calls once at startup, injecting the shared `EcfrClient`/`CacheBackend`
instances, and registers the returned callable with FastMCP."""

from cfr_compliance_mcp.tools.get_title_structure import make_get_title_structure_tool
from cfr_compliance_mcp.tools.get_version_history import make_get_version_history_tool
from cfr_compliance_mcp.tools.list_agencies import make_list_agencies_tool
from cfr_compliance_mcp.tools.retrieve_part import make_retrieve_part_tool
from cfr_compliance_mcp.tools.retrieve_section import make_retrieve_section_tool
from cfr_compliance_mcp.tools.retrieve_title import make_retrieve_title_tool
from cfr_compliance_mcp.tools.search_by_keyword import make_search_by_keyword_tool
from cfr_compliance_mcp.tools.search_regulations import make_search_regulations_tool

__all__ = [
    "make_search_regulations_tool",
    "make_search_by_keyword_tool",
    "make_retrieve_section_tool",
    "make_retrieve_part_tool",
    "make_retrieve_title_tool",
    "make_get_title_structure_tool",
    "make_get_version_history_tool",
    "make_list_agencies_tool",
]
