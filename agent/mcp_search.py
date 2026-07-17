# """agent/mcp_search.py

# Retrieval layer: for every contract Clause, search the CFR via the
# cfr-compliance-mcp MCP server (`search_regulations`), pick the
# highest-ranked hit, then fetch the actual regulation text
# (`retrieve_section`, falling back to `retrieve_part` when the hit's
# hierarchy has no section), and return a `CfrMatch` pairing the clause
# with its citation and regulation text.

# Retrieval-only, by design: no LLM, no Agno, no compliance reasoning --
# those are later pipeline stages, not this module's job. As of the
# `cfr_query_optimizer` integration, this module *does* do one extra,
# still fully deterministic step ahead of the MCP search call: run the
# clause through `agent.cfr_query_optimizer.optimize_clause()` to build a
# domain-anchored query and a predicted CFR title, and use that title as
# a soft *preference* (never a hard filter) when picking which ranked
# search hit to retrieve text for. eCFR's own search ranking is still the
# thing being trusted for relevance within a title; the optimizer only
# helps pick *which* title's hit to prefer when eCFR's ranking mixes
# titles.

# Run as: `uv run python -m agent.mcp_search` (package imports throughout,
# per that invocation style).
# """

# from __future__ import annotations
# import time
# import asyncio
# import json
# import re
# from dataclasses import dataclass
# from pathlib import Path
# from typing import Any

# from fastmcp import Client
# from fastmcp.client.transports import StdioTransport

# from .cfr_query_optimizer import optimize_clause
# from .contract_parser import ContractParser, split_into_clauses
# from .models import Clause

# # ---------------------------------------------------------------------------
# # Server connection
# # ---------------------------------------------------------------------------
# #
# # The server is a package module, started exactly the way it's run by
# # hand: `uv run python -m cfr_compliance_mcp.server`. It is NOT a bare
# # script file, so FastMCP Client's convenience constructor (which infers
# # `python <path>.py` from a filesystem path) doesn't apply here -- the
# # stdio transport is built explicitly with that exact command instead.
# # `server.py` defaults to the "stdio" transport (see its `_run()`:
# # `mcp.run_async(transport="stdio")` unless `settings.mcp_transport ==
# # "streamable-http"`), which is what a subprocess-spawned Client expects.
# #
# # NOT LIVE-VERIFIED in this environment: no network access, so `fastmcp`
# # could not be imported/run against a live server this session -- same
# # disclosed risk `server.py` itself flags for its FastMCP usage. First
# # thing to confirm once `uv sync` has been run: `uv run python -m
# # agent.mcp_search` actually connects and lists tools.
# MCP_SERVER_COMMAND = "uv"
# MCP_SERVER_ARGS = ["run", "python", "-m", "cfr_compliance_mcp.server"]
# # Maximum concurrent retrieval requests.
# # Prevents flooding the MCP server / eCFR API.
# RETRIEVAL_SEMAPHORE = asyncio.Semaphore(4)


# def _build_transport() -> StdioTransport:
#     return StdioTransport(command=MCP_SERVER_COMMAND, args=MCP_SERVER_ARGS)


# # ---------------------------------------------------------------------------
# # Result shape
# # ---------------------------------------------------------------------------


# @dataclass
# class CfrMatch:
#     """Result of retrieving CFR text for a single contract Clause.

#     Exactly one of (`citation` and `regulation_text`) or `error` is
#     populated -- this module never raises out of `retrieve_for_clause`,
#     it records the failure here instead, so one bad clause can't stop
#     the rest of the document.
#     """

#     clause: Clause
#     citation: str | None = None
#     regulation_text: str | None = None
#     error: str | None = None


# # ---------------------------------------------------------------------------
# # Response-shape helpers
# #
# # These read the *actual* Pydantic models in cfr_compliance_mcp, not
# # assumed field names:
# #   - responses.ErrorResponse: {"error": true, "error_type", "message", "retryable"}
# #     Every tool's outermost try/except (_common.build_error_response)
# #     returns this shape on failure instead of raising -- so success vs.
# #     failure is distinguished by checking payload["error"], never by
# #     catching an exception from call_tool.
# #   - responses.SearchResponse: {"results": [...], "total_count",
# #     "current_page", "total_pages"}; each result is a SearchResultItem
# #     with an untyped `hierarchy: dict[str, Any]` (extra="allow", since
# #     eCFR's hierarchy shape isn't live-verified in that module either)
# #     -- read defensively by key name, never assumed.
# #   - responses.RegulationTextResponse: {"text": ..., "citation": {...}}
# #     from retrieve_section/retrieve_part/retrieve_title, where
# #     `citation` is a CitationModel: {"title", "part", "section", "date",
# #     "heading", "url"}.
# # ---------------------------------------------------------------------------


# def _is_error_response(payload: Any) -> bool:
#     return isinstance(payload, dict) and payload.get("error") is True


# def _format_error(payload: dict[str, Any]) -> str:
#     error_type = payload.get("error_type", "UnknownError")
#     message = payload.get("message", "no message")
#     return f"{error_type}: {message}"


# async def _call_tool(client: Client, name: str, arguments: dict[str, Any]) -> Any:
#     """Call an MCP tool and return its JSON payload as a plain dict.

#     Every cfr-compliance-mcp tool returns a JSON-serializable dict
#     (`SomeResponse.model_dump()`). Depending on the fastmcp Client
#     version actually installed, that surfaces as structured content on
#     `result.data`, or only as a JSON string in `result.content[0].text`
#     -- handled defensively here since it's not live-verified which one
#     fires (see module docstring). Neither shape crashes the pipeline;
#     if both are absent, a clear RuntimeError is raised so the caller
#     can record it on the relevant `CfrMatch.error` instead of the whole
#     run dying.
#     """
#     result = await client.call_tool(name, arguments)

#     data = getattr(result, "data", None)
#     if isinstance(data, dict):
#         return data

#     for block in getattr(result, "content", None) or []:
#         text = getattr(block, "text", None)
#         if text:
#             try:
#                 return json.loads(text)
#             except json.JSONDecodeError:
#                 continue

#     raise RuntimeError(f"Could not extract a JSON payload from '{name}' tool response")


# def _format_citation(title: Any, part: str | None, section: str | None) -> str:
#     """Build a human-readable CFR citation without duplicating the part.

#     Naively formatting f"{part}.{section}" breaks when the upstream
#     data (either eCFR's search `hierarchy` or a retrieve tool's own
#     `citation.section`) already reports `section` fully qualified with
#     the part prefix -- e.g. part="1910", section="1910.1450" -- which
#     produced invalid citations like "29 CFR 1910.1910.1450" or
#     "41 CFR 60-741.60-741.5". If `section` already starts with `part`
#     as a whole segment (equal to it, or followed by "."), use `section`
#     on its own instead of prepending `part` again:
#         _format_citation(29, "1910", "1910.1450")   -> "29 CFR 1910.1450"
#         _format_citation(41, "60-741", "60-741.5")  -> "41 CFR 60-741.5"
#         _format_citation(48, "52", "212-4")         -> "48 CFR 52.212-4"
#     """
#     if not part and not section:
#         return f"{title} CFR"
#     if not part:
#         return f"{title} CFR {section}"
#     if not section:
#         return f"{title} CFR {part}"
#     if section == part or section.startswith(f"{part}."):
#         return f"{title} CFR {section}"
#     return f"{title} CFR {part}.{section}"


# def _extract_hierarchy_ref(item: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
#     """Pull (title, part, section) out of one SearchResultItem's
#     `hierarchy` dict. Returns None for anything missing rather than
#     raising -- callers decide what's "usable enough" to retrieve with.
#     """
#     hierarchy = item.get("hierarchy")
#     if not isinstance(hierarchy, dict):
#         return None, None, None

#     title = hierarchy.get("title")
#     part = hierarchy.get("part")
#     section = hierarchy.get("section")

#     return (
#         str(title) if title not in (None, "") else None,
#         str(part) if part not in (None, "") else None,
#         str(section) if section not in (None, "") else None,
#     )


# def _is_appendix_only(hierarchy: Any, part: str | None) -> bool:
#     """True when a search hit is an appendix reference with no usable
#     CFR part (`hierarchy.appendix` set, `hierarchy.part` absent).
#     `retrieve_section`/`retrieve_part` have nothing to fetch for that
#     shape, so such hits should be skipped in favor of the next
#     ranked result rather than failing the clause outright."""
#     return isinstance(hierarchy, dict) and hierarchy.get("appendix") not in (None, "") and not part


# def _select_best_candidate(
#     results: list[dict[str, Any]], predicted_title: str | None
# ) -> tuple[str | None, str | None, str | None]:
#     """Walk `results` in rank order exactly as before, collecting every
#     *usable* candidate (has a numeric title + part, not an
#     appendix-only reference) -- then pick the first usable candidate
#     whose title matches `predicted_title`, if one was supplied by
#     `cfr_query_optimizer` and at least one such candidate exists.
#     Otherwise fall back to the first usable candidate overall (eCFR's
#     own rank-1 usable hit), which is this pipeline's original,
#     always-safe behavior.

#     Never hard-fails on a title mismatch: `predicted_title` is a
#     deterministic *hint*, not a filter that can eliminate every
#     candidate. If nothing matches it, the original rank-order selection
#     still applies -- this function always returns the same result the
#     pre-optimizer pipeline would have when `predicted_title` is `None`
#     or matches nothing.

#     Example (matches the optimizer's own docstring example): predicted
#     title "29", search hits ranked [48 CFR appendix-only, 40 CFR
#     (usable, unrelated), 29 CFR 1926 (usable)] -> the appendix-only hit
#     is skipped as before, and "29 CFR 1926" is preferred over the
#     higher-ranked-but-off-domain "40 CFR" hit.
#     """
#     usable: list[tuple[str, str, str | None]] = []

#     for candidate in results:
#         if not isinstance(candidate, dict):
#             continue

#         c_title, c_part, c_section = _extract_hierarchy_ref(candidate)
#         hierarchy = candidate.get("hierarchy")

#         print("Search hit:")
#         print(f"  title: {c_title}")
#         print(f"  part: {c_part}")
#         print(f"  section: {c_section}")
#         print(f"  hierarchy: {hierarchy}")

#         if _is_appendix_only(hierarchy, c_part):
#             continue
#         if c_title is None or c_part is None:
#             continue

#         usable.append((c_title, c_part, c_section))

#     if not usable:
#         return None, None, None

#     if predicted_title is not None:
#         for c_title, c_part, c_section in usable:
#             if c_title == predicted_title:
#                 return c_title, c_part, c_section

#     return usable[0]


# # ---------------------------------------------------------------------------
# # Query normalization
# # ---------------------------------------------------------------------------
# #
# # Sending the raw clause paragraph to search_regulations dilutes eCFR's
# # full-text search with contract boilerplate ("Contractor shall...",
# # "...during the course of the Work") and pulls results toward generic
# # procurement matches (48 CFR) instead of the regulation domain the
# # clause is actually about (e.g. 40 CFR hazardous waste, 29 CFR OSHA).
# # `build_search_query` strips that boilerplate deterministically --
# # strip the heading, tokenize, drop stopwords, dedupe, cap length -- no
# # NLP, no embeddings, no synonym expansion. It can only surface words
# # already present in the clause; it won't invent domain terms the
# # clause never used. `cfr_query_optimizer.optimize_clause()` (see that
# # module) runs *after* this function as a second, independent pass, and
# # can override its output with a domain-anchored query when a CFR
# # domain is confidently detected -- but never removes or weakens this
# # function itself; `build_search_query`'s output remains the fallback
# # whenever the optimizer finds no domain match.

# # Matches a leading "SECTION 1." / "Section 2" style heading so it's
# # stripped before tokenizing -- `split_into_clauses` already puts the
# # raw heading into `Clause.title`, and the heading itself
# # ("SECTION 1.") carries no search-relevant meaning.
# _HEADING_PREFIX_PATTERN = re.compile(r"^[ \t]*SECTION\s+\d+\.?\s*", re.IGNORECASE)

# STOP_WORDS: frozenset[str] = frozenset(
#     {
#         # contract/legal boilerplate
#         "contractor", "owner", "agreement", "work", "shall", "comply",
#         "perform", "performed", "performing", "services", "party",
#         "parties", "section", "clause", "contract", "hereby", "herein",
#         "hereof", "hereunder", "thereof", "pursuant", "applicable",
#         "obligation", "obligations", "requirement", "requirements",
#         "provision", "provisions", "term", "terms", "condition",
#         "conditions", "including", "include", "includes", "provide",
#         "provided", "provides", "ensure", "ensures", "course", "date",
#         "time", "days", "written", "notice", "respect", "regard",
#         # generic English stopwords (needed since input is full sentences)
#         "a", "an", "the", "of", "to", "in", "on", "at", "by", "for",
#         "with", "and", "or", "but", "if", "as", "is", "are", "was",
#         "were", "be", "been", "being", "this", "that", "these", "those",
#         "all", "any", "each", "such", "from", "into", "during", "prior",
#         "under", "upon", "not", "no", "so", "than", "then", "which",
#         "who", "whom", "whose", "it", "its", "their", "his", "her",
#         "they", "them", "he", "she", "we", "you", "your", "our", "i",
#         "will", "would", "shall", "may", "must", "can", "could",
#     }
# )

# _MAX_QUERY_CHARS = 250


# def build_search_query(clause: Clause) -> str:
#     """Turn a contract clause into a short, deterministic keyword query.

#     Strips the "SECTION n." heading prefix, tokenizes `title + text`,
#     drops stopwords/boilerplate (`STOP_WORDS`) and short tokens, dedupes
#     while preserving first-seen order, and keeps adding words only
#     while the joined query stays within `_MAX_QUERY_CHARS` (~250 chars)
#     -- favoring the domain-specific nouns ("hazardous", "waste", "OSHA",
#     "disposal") that actually distinguish the clause, over the
#     sentence's boilerplate scaffolding.
#     """
#     title_text = _HEADING_PREFIX_PATTERN.sub("", clause.title)
#     body_text = _HEADING_PREFIX_PATTERN.sub("", clause.text)
#     words = re.findall(r"[A-Za-z][A-Za-z\-]*", f"{title_text} {body_text}")

#     keywords: list[str] = []
#     seen: set[str] = set()
#     length = 0
#     for word in words:
#         lower = word.lower()
#         if lower in STOP_WORDS or len(lower) < 3 or lower in seen:
#             continue
#         added_length = len(lower) + (1 if keywords else 0)  # +1 for the joining space
#         if length + added_length > _MAX_QUERY_CHARS:
#             break
#         seen.add(lower)
#         keywords.append(lower)
#         length += added_length

#     return " ".join(keywords)


# # ---------------------------------------------------------------------------
# # Retrieval pipeline
# # ---------------------------------------------------------------------------


# async def _search_results(client: Client, query: str) -> list[dict[str, Any]]:
#     """Run search_regulations and return the full ranked results list
#     (possibly empty) -- callers walk it in rank order to find the first
#     *usable* hit, rather than blindly trusting result #1."""
#     payload = await _call_tool(client, "search_regulations", {"query": query})

#     if _is_error_response(payload):
#         raise RuntimeError(_format_error(payload))

#     results = payload.get("results") if isinstance(payload, dict) else None
#     return results or []


# async def _retrieve_text(
#     client: Client, title: int, part: str, section: str | None
# ) -> tuple[str, str]:
#     """Fetch regulation text + a citation string, preferring
#     `retrieve_section` (exact) and falling back to `retrieve_part` when
#     the search hit's hierarchy had no section."""
#     if section:
#         payload = await _call_tool(
#             client, "retrieve_section", {"title": title, "part": part, "section": section}
#         )
#     else:
#         payload = await _call_tool(client, "retrieve_part", {"title": title, "part": part})

#     if _is_error_response(payload):
#         raise RuntimeError(_format_error(payload))

#     text = payload.get("text")
#     citation_obj = payload.get("citation") or {}

#     if not text:
#         raise RuntimeError("Tool response had no 'text' field")

#     cit_title = citation_obj.get("title", title)
#     cit_part = citation_obj.get("part", part)
#     cit_section = citation_obj.get("section", section)
#     citation = _format_citation(cit_title, cit_part, cit_section)

#     return citation, text


# async def retrieve_for_clause(client: Client, clause: Clause) -> CfrMatch:
#    async with RETRIEVAL_SEMAPHORE:
#     try:
#         base_query = build_search_query(clause)
#         optimized = optimize_clause(clause.title, clause.text, base_query)
#         query = optimized["search_query"]
#         predicted_title = optimized["title"]

#         print(f"Base query: {base_query}")
#         print(f"Optimized query: {query}")
#         print(f"Predicted CFR title: {predicted_title}")

#         results = await _search_results(client, query)
#         if not results:
#             return CfrMatch(clause=clause, error="No CFR search results found")

#         # Walk results in rank order and take the first usable one,
#         # preferring a hit whose title matches the optimizer's
#         # prediction (if any) over a higher-ranked but off-domain hit --
#         # see `_select_best_candidate`'s own docstring. Falls back to
#         # the original "first usable hit, in rank order" behavior when
#         # `predicted_title` is None or matches nothing.
#         title_str, part, section = _select_best_candidate(results, predicted_title)

#         if title_str is None or part is None:
#             return CfrMatch(
#                 clause=clause,
#                 error="No search result had a usable CFR title/part (all appendix-only or incomplete)",
#             )

#         try:
#             title = int(title_str)
#         except ValueError:
#             return CfrMatch(clause=clause, error=f"Non-numeric CFR title in hierarchy: {title_str!r}")

#         citation, text = await _retrieve_text(client, title, part, section)
#         return CfrMatch(clause=clause, citation=citation, regulation_text=text)

#     except Exception as exc:  # noqa: BLE001 -- retrieval boundary: one clause must not kill the run
#         return CfrMatch(clause=clause, error=str(exc))


# # async def retrieve_for_clauses(clauses: list[Clause]) -> list[CfrMatch]:
# #     """Run retrieval for every clause against one shared MCP session
# #     (one subprocess/connection for the whole document, not one per
# #     clause)."""
# #     matches: list[CfrMatch] = []

# #     async with Client(_build_transport()) as client:
# #         for clause in clauses:
# #             matches.append(await retrieve_for_clause(client, clause))

# #     return matches
# async def retrieve_for_clauses(clauses: list[Clause]) -> list[CfrMatch]:
#     """Run retrieval for every clause against one shared MCP session."""

#     async with Client(_build_transport()) as client:
#         tasks = [
#             retrieve_for_clause(client, clause)
#             for clause in clauses
#         ]

#         matches = await asyncio.gather(*tasks)

#     return matches


# # ---------------------------------------------------------------------------
# # Test runner
# # ---------------------------------------------------------------------------


# def _print_match(match: CfrMatch) -> None:
#     print("=" * 60)
#     print("Clause:")
#     print(match.clause.title)
#     if match.error:
#         print("Error:")
#         print(match.error)
#         return
#     print("CFR Citation:")
#     print(match.citation)
#     print("Regulation:")
#     text = match.regulation_text or ""
#     print(text[:500] + ("..." if len(text) > 500 else ""))


# async def _main_async() -> None:
#     pdf_path = Path("contracts/sample_contract_multi.pdf")
#     parser = ContractParser(pdf_path)
#     text = parser.extract_text()
#     clauses = split_into_clauses(text)

#     print(f"Extracted {len(clauses)} clauses from {pdf_path}")

#     matches = await retrieve_for_clauses(clauses)

#     for match in matches:
#         _print_match(match)

#     failed = sum(1 for m in matches if m.error)
#     print("=" * 60)
#     print(f"Done: {len(matches) - failed}/{len(matches)} clauses retrieved successfully")


# def main() -> None:
#     asyncio.run(_main_async())


# if __name__ == "__main__":
#     main()
"""agent/mcp_search.py

Retrieval layer: for every contract Clause, search the CFR via the
cfr-compliance-mcp MCP server (`search_regulations`), pick the
highest-ranked hit, then fetch the actual regulation text
(`retrieve_section`, falling back to `retrieve_part` when the hit's
hierarchy has no section), and return a `CfrMatch` pairing the clause
with its citation and regulation text.

Retrieval-only, by design: no LLM, no Agno, no compliance reasoning --
those are later pipeline stages, not this module's job. As of the
`cfr_query_optimizer` integration, this module *does* do one extra,
still fully deterministic step ahead of the MCP search call: run the
clause through `agent.cfr_query_optimizer.optimize_clause()` to build a
domain-anchored query and a predicted CFR title, and use that title as
a soft *preference* (never a hard filter) when picking which ranked
search hit to retrieve text for. eCFR's own search ranking is still the
thing being trusted for relevance within a title; the optimizer only
helps pick *which* title's hit to prefer when eCFR's ranking mixes
titles.

Run as: `uv run python -m agent.mcp_search` (package imports throughout,
per that invocation style).
"""

from __future__ import annotations
import time
import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from .cfr_query_optimizer import optimize_clause
from .contract_parser import ContractParser, split_into_clauses
from .models import Clause

# ---------------------------------------------------------------------------
# Server connection
# ---------------------------------------------------------------------------
#
# The server is a package module, started exactly the way it's run by
# hand: `uv run python -m cfr_compliance_mcp.server`. It is NOT a bare
# script file, so FastMCP Client's convenience constructor (which infers
# `python <path>.py` from a filesystem path) doesn't apply here -- the
# stdio transport is built explicitly with that exact command instead.
# `server.py` defaults to the "stdio" transport (see its `_run()`:
# `mcp.run_async(transport="stdio")` unless `settings.mcp_transport ==
# "streamable-http"`), which is what a subprocess-spawned Client expects.
#
# NOT LIVE-VERIFIED in this environment: no network access, so `fastmcp`
# could not be imported/run against a live server this session -- same
# disclosed risk `server.py` itself flags for its FastMCP usage. First
# thing to confirm once `uv sync` has been run: `uv run python -m
# agent.mcp_search` actually connects and lists tools.
MCP_SERVER_COMMAND = "uv"
MCP_SERVER_ARGS = ["run", "python", "-m", "cfr_compliance_mcp.server"]
# Maximum concurrent retrieval requests.
# Prevents flooding the MCP server / eCFR API.
RETRIEVAL_SEMAPHORE = asyncio.Semaphore(4)


def _build_transport() -> StdioTransport:
    return StdioTransport(command=MCP_SERVER_COMMAND, args=MCP_SERVER_ARGS)


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass
class CfrMatch:
    """Result of retrieving CFR text for a single contract Clause.

    Exactly one of (`citation` and `regulation_text`) or `error` is
    populated -- this module never raises out of `retrieve_for_clause`,
    it records the failure here instead, so one bad clause can't stop
    the rest of the document.
    """

    clause: Clause
    citation: str | None = None
    regulation_text: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Response-shape helpers
#
# These read the *actual* Pydantic models in cfr_compliance_mcp, not
# assumed field names:
#   - responses.ErrorResponse: {"error": true, "error_type", "message", "retryable"}
#     Every tool's outermost try/except (_common.build_error_response)
#     returns this shape on failure instead of raising -- so success vs.
#     failure is distinguished by checking payload["error"], never by
#     catching an exception from call_tool.
#   - responses.SearchResponse: {"results": [...], "total_count",
#     "current_page", "total_pages"}; each result is a SearchResultItem
#     with an untyped `hierarchy: dict[str, Any]` (extra="allow", since
#     eCFR's hierarchy shape isn't live-verified in that module either)
#     -- read defensively by key name, never assumed.
#   - responses.RegulationTextResponse: {"text": ..., "citation": {...}}
#     from retrieve_section/retrieve_part/retrieve_title, where
#     `citation` is a CitationModel: {"title", "part", "section", "date",
#     "heading", "url"}.
# ---------------------------------------------------------------------------


def _is_error_response(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("error") is True


def _format_error(payload: dict[str, Any]) -> str:
    error_type = payload.get("error_type", "UnknownError")
    message = payload.get("message", "no message")
    return f"{error_type}: {message}"


async def _call_tool(client: Client, name: str, arguments: dict[str, Any]) -> Any:
    """Call an MCP tool and return its JSON payload as a plain dict.

    Every cfr-compliance-mcp tool returns a JSON-serializable dict
    (`SomeResponse.model_dump()`). Depending on the fastmcp Client
    version actually installed, that surfaces as structured content on
    `result.data`, or only as a JSON string in `result.content[0].text`
    -- handled defensively here since it's not live-verified which one
    fires (see module docstring). Neither shape crashes the pipeline;
    if both are absent, a clear RuntimeError is raised so the caller
    can record it on the relevant `CfrMatch.error` instead of the whole
    run dying.
    """
    result = await client.call_tool(name, arguments)

    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data

    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue

    raise RuntimeError(f"Could not extract a JSON payload from '{name}' tool response")


def _format_citation(title: Any, part: str | None, section: str | None) -> str:
    """Build a human-readable CFR citation without duplicating the part.

    Naively formatting f"{part}.{section}" breaks when the upstream
    data (either eCFR's search `hierarchy` or a retrieve tool's own
    `citation.section`) already reports `section` fully qualified with
    the part prefix -- e.g. part="1910", section="1910.1450" -- which
    produced invalid citations like "29 CFR 1910.1910.1450" or
    "41 CFR 60-741.60-741.5". If `section` already starts with `part`
    as a whole segment (equal to it, or followed by "."), use `section`
    on its own instead of prepending `part` again:
        _format_citation(29, "1910", "1910.1450")   -> "29 CFR 1910.1450"
        _format_citation(41, "60-741", "60-741.5")  -> "41 CFR 60-741.5"
        _format_citation(48, "52", "212-4")         -> "48 CFR 52.212-4"
    """
    if not part and not section:
        return f"{title} CFR"
    if not part:
        return f"{title} CFR {section}"
    if not section:
        return f"{title} CFR {part}"
    if section == part or section.startswith(f"{part}."):
        return f"{title} CFR {section}"
    return f"{title} CFR {part}.{section}"


def _extract_hierarchy_ref(item: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Pull (title, part, section) out of one SearchResultItem's
    `hierarchy` dict. Returns None for anything missing rather than
    raising -- callers decide what's "usable enough" to retrieve with.
    """
    hierarchy = item.get("hierarchy")
    if not isinstance(hierarchy, dict):
        return None, None, None

    title = hierarchy.get("title")
    part = hierarchy.get("part")
    section = hierarchy.get("section")

    return (
        str(title) if title not in (None, "") else None,
        str(part) if part not in (None, "") else None,
        str(section) if section not in (None, "") else None,
    )


def _is_appendix_only(hierarchy: Any, part: str | None) -> bool:
    """True when a search hit is an appendix reference with no usable
    CFR part (`hierarchy.appendix` set, `hierarchy.part` absent).
    `retrieve_section`/`retrieve_part` have nothing to fetch for that
    shape, so such hits should be skipped in favor of the next
    ranked result rather than failing the clause outright."""
    return isinstance(hierarchy, dict) and hierarchy.get("appendix") not in (None, "") and not part


def _select_best_candidate(
    results: list[dict[str, Any]], predicted_title: str | None
) -> tuple[str | None, str | None, str | None]:
    """Walk `results` in rank order exactly as before, collecting every
    *usable* candidate (has a numeric title + part, not an
    appendix-only reference) -- then pick the first usable candidate
    whose title matches `predicted_title`, if one was supplied by
    `cfr_query_optimizer` and at least one such candidate exists.
    Otherwise fall back to the first usable candidate overall (eCFR's
    own rank-1 usable hit), which is this pipeline's original,
    always-safe behavior.

    Never hard-fails on a title mismatch: `predicted_title` is a
    deterministic *hint*, not a filter that can eliminate every
    candidate. If nothing matches it, the original rank-order selection
    still applies -- this function always returns the same result the
    pre-optimizer pipeline would have when `predicted_title` is `None`
    or matches nothing.

    Example (matches the optimizer's own docstring example): predicted
    title "29", search hits ranked [48 CFR appendix-only, 40 CFR
    (usable, unrelated), 29 CFR 1926 (usable)] -> the appendix-only hit
    is skipped as before, and "29 CFR 1926" is preferred over the
    higher-ranked-but-off-domain "40 CFR" hit.
    """
    usable: list[tuple[str, str, str | None]] = []

    for candidate in results:
        if not isinstance(candidate, dict):
            continue

        c_title, c_part, c_section = _extract_hierarchy_ref(candidate)
        hierarchy = candidate.get("hierarchy")

        print("Search hit:")
        print(f"  title: {c_title}")
        print(f"  part: {c_part}")
        print(f"  section: {c_section}")
        print(f"  hierarchy: {hierarchy}")

        if _is_appendix_only(hierarchy, c_part):
            continue
        if c_title is None or c_part is None:
            continue

        usable.append((c_title, c_part, c_section))

    if not usable:
        return None, None, None

    if predicted_title is not None:
        for c_title, c_part, c_section in usable:
            if c_title == predicted_title:
                return c_title, c_part, c_section

    return usable[0]


# ---------------------------------------------------------------------------
# Query normalization
# ---------------------------------------------------------------------------
#
# Sending the raw clause paragraph to search_regulations dilutes eCFR's
# full-text search with contract boilerplate ("Contractor shall...",
# "...during the course of the Work") and pulls results toward generic
# procurement matches (48 CFR) instead of the regulation domain the
# clause is actually about (e.g. 40 CFR hazardous waste, 29 CFR OSHA).
# `build_search_query` strips that boilerplate deterministically --
# strip the heading, tokenize, drop stopwords, dedupe, cap length -- no
# NLP, no embeddings, no synonym expansion. It can only surface words
# already present in the clause; it won't invent domain terms the
# clause never used. `cfr_query_optimizer.optimize_clause()` (see that
# module) runs *after* this function as a second, independent pass, and
# can override its output with a domain-anchored query when a CFR
# domain is confidently detected -- but never removes or weakens this
# function itself; `build_search_query`'s output remains the fallback
# whenever the optimizer finds no domain match.

# Matches a leading "SECTION 1." / "Section 2" style heading so it's
# stripped before tokenizing -- `split_into_clauses` already puts the
# raw heading into `Clause.title`, and the heading itself
# ("SECTION 1.") carries no search-relevant meaning.
_HEADING_PREFIX_PATTERN = re.compile(r"^[ \t]*SECTION\s+\d+\.?\s*", re.IGNORECASE)

STOP_WORDS: frozenset[str] = frozenset(
    {
        # contract/legal boilerplate
        "contractor", "owner", "agreement", "work", "shall", "comply",
        "perform", "performed", "performing", "services", "party",
        "parties", "section", "clause", "contract", "hereby", "herein",
        "hereof", "hereunder", "thereof", "pursuant", "applicable",
        "obligation", "obligations", "requirement", "requirements",
        "provision", "provisions", "term", "terms", "condition",
        "conditions", "including", "include", "includes", "provide",
        "provided", "provides", "ensure", "ensures", "course", "date",
        "time", "days", "written", "notice", "respect", "regard",
        # generic English stopwords (needed since input is full sentences)
        "a", "an", "the", "of", "to", "in", "on", "at", "by", "for",
        "with", "and", "or", "but", "if", "as", "is", "are", "was",
        "were", "be", "been", "being", "this", "that", "these", "those",
        "all", "any", "each", "such", "from", "into", "during", "prior",
        "under", "upon", "not", "no", "so", "than", "then", "which",
        "who", "whom", "whose", "it", "its", "their", "his", "her",
        "they", "them", "he", "she", "we", "you", "your", "our", "i",
        "will", "would", "shall", "may", "must", "can", "could",
    }
)

_MAX_QUERY_CHARS = 250


def build_search_query(clause: Clause) -> str:
    """Turn a contract clause into a short, deterministic keyword query.

    Strips the "SECTION n." heading prefix, tokenizes `title + text`,
    drops stopwords/boilerplate (`STOP_WORDS`) and short tokens, dedupes
    while preserving first-seen order, and keeps adding words only
    while the joined query stays within `_MAX_QUERY_CHARS` (~250 chars)
    -- favoring the domain-specific nouns ("hazardous", "waste", "OSHA",
    "disposal") that actually distinguish the clause, over the
    sentence's boilerplate scaffolding.
    """
    title_text = _HEADING_PREFIX_PATTERN.sub("", clause.title)
    body_text = _HEADING_PREFIX_PATTERN.sub("", clause.text)
    words = re.findall(r"[A-Za-z][A-Za-z\-]*", f"{title_text} {body_text}")

    keywords: list[str] = []
    seen: set[str] = set()
    length = 0
    for word in words:
        lower = word.lower()
        if lower in STOP_WORDS or len(lower) < 3 or lower in seen:
            continue
        added_length = len(lower) + (1 if keywords else 0)  # +1 for the joining space
        if length + added_length > _MAX_QUERY_CHARS:
            break
        seen.add(lower)
        keywords.append(lower)
        length += added_length

    return " ".join(keywords)


# ---------------------------------------------------------------------------
# Retrieval pipeline
# ---------------------------------------------------------------------------


async def _search_results(client: Client, query: str) -> list[dict[str, Any]]:
    """Run search_regulations and return the full ranked results list
    (possibly empty) -- callers walk it in rank order to find the first
    *usable* hit, rather than blindly trusting result #1."""
    payload = await _call_tool(client, "search_regulations", {"query": query})

    if _is_error_response(payload):
        raise RuntimeError(_format_error(payload))

    results = payload.get("results") if isinstance(payload, dict) else None
    return results or []


async def _retrieve_text(
    client: Client, title: int, part: str, section: str | None
) -> tuple[str, str]:
    """Fetch regulation text + a citation string, preferring
    `retrieve_section` (exact) and falling back to `retrieve_part` when
    the search hit's hierarchy had no section."""
    if section:
        payload = await _call_tool(
            client, "retrieve_section", {"title": title, "part": part, "section": section}
        )
    else:
        payload = await _call_tool(client, "retrieve_part", {"title": title, "part": part})

    if _is_error_response(payload):
        raise RuntimeError(_format_error(payload))

    text = payload.get("text")
    citation_obj = payload.get("citation") or {}

    if not text:
        raise RuntimeError("Tool response had no 'text' field")

    cit_title = citation_obj.get("title", title)
    cit_part = citation_obj.get("part", part)
    cit_section = citation_obj.get("section", section)
    citation = _format_citation(cit_title, cit_part, cit_section)

    return citation, text


async def retrieve_for_clause(client: Client, clause: Clause) -> CfrMatch:
    # Limit concurrent retrieval requests to avoid overwhelming the MCP
    # server and the eCFR API -- the entire retrieval body for this
    # clause (search + text fetch) runs inside the semaphore so the
    # concurrency cap applies to the full request, not just part of it.
    start = time.perf_counter()
    async with RETRIEVAL_SEMAPHORE:
        # Performance benchmarking: measure per-clause retrieval time so
        # slow clauses (or slow MCP/eCFR calls) are visible without a
        # profiler. Always printed, even on failure, via `finally`.
        # start = time.perf_counter()
        try:
            base_query = build_search_query(clause)
            optimized = optimize_clause(clause.title, clause.text, base_query)
            query = optimized["search_query"]
            predicted_title = optimized["title"]

            print(f"Base query: {base_query}")
            print(f"Optimized query: {query}")
            print(f"Predicted CFR title: {predicted_title}")

            results = await _search_results(client, query)
            if not results:
                return CfrMatch(clause=clause, error="No CFR search results found")

            # Walk results in rank order and take the first usable one,
            # preferring a hit whose title matches the optimizer's
            # prediction (if any) over a higher-ranked but off-domain hit --
            # see `_select_best_candidate`'s own docstring. Falls back to
            # the original "first usable hit, in rank order" behavior when
            # `predicted_title` is None or matches nothing.
            title_str, part, section = _select_best_candidate(results, predicted_title)

            if title_str is None or part is None:
                return CfrMatch(
                    clause=clause,
                    error="No search result had a usable CFR title/part (all appendix-only or incomplete)",
                )

            try:
                title = int(title_str)
            except ValueError:
                return CfrMatch(clause=clause, error=f"Non-numeric CFR title in hierarchy: {title_str!r}")

            citation, text = await _retrieve_text(client, title, part, section)
            return CfrMatch(clause=clause, citation=citation, regulation_text=text)

        except Exception as exc:  # noqa: BLE001 -- retrieval boundary: one clause must not kill the run
            return CfrMatch(clause=clause, error=str(exc))
        finally:
            elapsed = time.perf_counter() - start
            print(f"[Retrieval] {clause.title}: {elapsed:.2f}s")


async def retrieve_for_clauses(clauses: list[Clause]) -> list[CfrMatch]:
    """Run retrieval for every clause against one shared MCP session."""

    # Performance benchmarking: measure wall-clock time for the whole
    # batch (session setup + all concurrent clause retrievals).
    start = time.perf_counter()

    async with Client(_build_transport()) as client:
        tasks = [
            retrieve_for_clause(client, clause)
            for clause in clauses
        ]

        matches = await asyncio.gather(*tasks)

    elapsed = time.perf_counter() - start
    successful = sum(1 for m in matches if not m.error)
    failed = len(matches) - successful

    print("=" * 60)
    print("Retrieval Summary")
    print(f"Successful : {successful}")
    print(f"Failed     : {failed}")
    print(f"Total Time : {elapsed:.2f}s")
    print("=" * 60)

    return matches


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


def _print_match(match: CfrMatch) -> None:
    print("=" * 60)
    print("Clause:")
    print(match.clause.title)
    if match.error:
        print("Error:")
        print(match.error)
        return
    print("CFR Citation:")
    print(match.citation)
    print("Regulation:")
    text = match.regulation_text or ""
    print(text[:500] + ("..." if len(text) > 500 else ""))


async def _main_async() -> None:
    pdf_path = Path("contracts/sample_contract_multi.pdf")
    parser = ContractParser(pdf_path)
    text = parser.extract_text()
    clauses = split_into_clauses(text)

    print(f"Extracted {len(clauses)} clauses from {pdf_path}")

    matches = await retrieve_for_clauses(clauses)

    for match in matches:
        _print_match(match)

    failed = sum(1 for m in matches if m.error)
    print("=" * 60)
    print(f"Done: {len(matches) - failed}/{len(matches)} clauses retrieved successfully")


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
