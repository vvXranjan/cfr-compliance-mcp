# Engineering Progress Report
## Contract Compliance POC — eCFR MCP Server

**Prepared by:** Vvr
**Status:** In Progress — Milestone 4 (Core MCP Server Build: Caching + Parsing Layers Complete)

---

## Project Objective

Build a Proof of Concept that automatically checks contract clauses against U.S. federal regulations (CFR) for compliance, using an AI agent pipeline: contract clauses are extracted, matched against relevant Code of Federal Regulations text retrieved live from the official eCFR API, and evaluated by an LLM to produce a clause-by-clause compliance report with citations.

## Research Completed

A full research and evaluation phase was completed covering:
- GitHub, the official Model Context Protocol registry/servers repository, npm, PyPI, and MCP directories (Glama, PulseMCP, LobeHub), searched for any existing open-source MCP server for the eCFR.
- The official eCFR REST API documentation (endpoints, authentication, rate limits, response formats).

## MCP Research Summary

Three real candidate open-source MCP servers were identified and evaluated in detail (repository activity, feature set, production readiness, licensing):

| Candidate | Verdict |
|---|---|
| `1102tools/federal-contracting-mcps` (ecfr-mcp) | Best of the candidates — purpose-built, MIT-licensed, tested — but a single-maintainer project only weeks old with minimal community adoption. |
| `beshkenadze/us-legal-tools` (ecfr-sdk) | Broader multi-source legal-data coverage, but its eCFR support is a thin wrapper layer with less eCFR-specific hardening. |
| `Travis-Prall/court-listener-mcp` | eCFR access is incidental to a case-law lookup tool and requires an unrelated API key — scope mismatch. |

No enterprise-grade or Anthropic-endorsed eCFR MCP server exists. The entire space is very new (all candidates dated within the last few months).

## Why We Chose a Custom MCP Server

Given the immaturity of every available option, we decided to build our own production-quality MCP server directly against the official eCFR REST API, rather than depend on a third-party project in production. The existing open-source projects were used strictly as architectural reference — no code was copied. This decision gives us full control over reliability (retries, rate limiting, caching), input validation, structured citation output (required for an audit-grade compliance report), and long-term maintainability, without inheriting a small project's bus-factor risk.

## Follow-Up Research: "Legal MCP" / "LCP" Evaluation

At the team lead's request, we paused implementation to evaluate whether an existing "Legal MCP" (referred to as "LCP") could replace this custom build. No single canonical "LCP" project exists in the ecosystem; we evaluated the closest candidates — `open-legal-compliance-mcp`, `court-listener-mcp` (and a hosted Vaquill-AI fork), and the commercial Vaquill AI MCP. None call the official eCFR REST API directly: they route through GovInfo's API, CourtListener's own mirror of eCFR data (subject to a restrictive 125-requests/day free-tier cap as of May 2026), or a proprietary indexed corpus of uncertain freshness. **The decision to build our own MCP server was reaffirmed with no architecture changes.**

## Architecture Overview

```
Contract → Clause Extraction → Agno Agent → Custom MCP Server
  → Official eCFR REST API → LLM Compliance Reasoning → Compliance Report
```

The MCP server is a layered Python application:
- **Config & logging layer** — typed, environment-driven settings; structured logging to stderr (required for MCP's stdio transport).
- **Client layer** — a generic, reusable async HTTP client (retries, timeouts, rate limiting) plus an eCFR-specific client built on top of it, handling real eCFR quirks (date lag, search history pollution).
- **Caching layer** *(complete)* — backend-agnostic, string-in/string-out interface; avoids redundant network calls across clauses referencing the same regulation.
- **Parsing layer** *(complete)* — converts raw eCFR XML into clean text with structured citations.
- **Tool layer** *(in progress)* — 8 MCP tools exposed to the agent: `search_regulations`, `search_by_keyword`, `retrieve_section`, `retrieve_part`, `retrieve_title`, `get_title_structure`, `get_version_history`, `list_agencies`.
- **Server entrypoint** *(pending)* — wires everything into a runnable FastMCP application.

## Modules Completed

| Module | Status | Notes |
|---|---|---|
| Project scaffolding (`pyproject.toml`, `.env.example`, `.gitignore`) | Complete | uv-managed, Python 3.12, fastmcp 3.x |
| Configuration (`config.py`) | Complete | Typed settings via pydantic-settings |
| Logging (`logging_config.py`) | Complete | stderr-only, text/JSON modes |
| Exception hierarchy (`exceptions.py`) | Complete | eCFR-specific error taxonomy |
| Protocol constants (`constants.py`) | Complete | Endpoint templates, CFR bounds |
| Generic HTTP client (`clients/http_client.py`) | Complete | Retries, timeouts, rate limiting; API-agnostic and reusable |
| eCFR API client (`clients/ecfr_client.py`) | Complete | All 8 required data-access methods, quirk-handling built in |
| Caching layer (`cache/cache_backend.py`) | Complete | Backend-agnostic (string-in/string-out) interface; in-memory TTL implementation; functionally tested this session; Redis is a drop-in future upgrade |
| Parsing layer (`parsing/xml_parser.py`) | Complete | Streaming XML parser, tag-agnostic; functionally tested against realistic eCFR XML; one real bug found and fixed in review |
| Input/output validation models | Not started | Next module |
| MCP tool implementations (8 tools) | Not started | |
| Server entrypoint | Not started | |
| Automated tests | Not started | |

## Current Progress

The foundation, client, caching, and parsing layers of the MCP server are now complete and internally consistent — every file has been syntax-verified, and the caching and parsing layers were additionally functionally tested at runtime this session. The parsing layer's code review caught and fixed a real formatting bug (source-XML line-wrapping leaking into output paragraphs) before sign-off — exactly the kind of issue a review process is meant to catch. Combined, these four layers deliver the full "talk to eCFR reliably, don't repeat the same call twice, and hand back clean, citable text" capability. No live network integration testing against the real eCFR API has occurred yet, since this was built in an offline sandbox — that remains the first task once work resumes in a connected environment.

## Implementation Decisions

- Standalone `fastmcp` package (v3.x) selected over the MCP SDK's bundled FastMCP class, for more mature production features.
- Strict separation between a reusable, API-agnostic HTTP transport layer and an eCFR-specific client layer, so future integrations (e.g., Federal Register, GovInfo) can reuse the transport layer unchanged.
- All configuration is environment-driven and centrally typed — no scattered environment-variable reads.
- Defense-in-depth validation: input checks exist at the client layer even though a dedicated validation layer is still to be added.
- Cache interface deliberately operates on strings only (not arbitrary Python objects), so a future Redis-backed implementation requires no changes to any calling code.
- Unimplemented cache backends fail loudly rather than silently degrading, to prevent hidden production misconfiguration.
- XML parsing is tag-agnostic (extracts text from every element rather than a hardcoded tag whitelist), making it robust to eCFR schema variation across different CFR titles.

## Remaining Work

1. Pydantic request/response models for all 8 tools.
2. Implementation of all 8 MCP tools.
3. FastMCP server entrypoint wiring everything together.
4. Automated test suite.
5. Live integration testing against the real eCFR API.
6. README and setup documentation.
7. *(Expanded scope)* Contract Parser, Agno Team/Agent integration, Compliance Engine, and `demo.py` — planned as a separate sibling package once the MCP server itself is complete.

## Next Milestone

Complete the validation models layer, followed by the 8 MCP tool implementations — bringing the server to a fully runnable, testable state, ready for the FastMCP server entrypoint (the final piece of the MCP server itself).

## Timeline

| Phase | Status |
|---|---|
| Research & architecture | Complete |
| Legal MCP ("LCP") evaluation | Complete — decision reaffirmed |
| Foundation + client layer | Complete |
| Caching layer | Complete |
| Parsing layer | Complete |
| Validation models + tool layer | Next |
| Server entrypoint + tests | Upcoming |
| Live integration validation | Upcoming |
| Contract Parser + Agno integration + Compliance Engine + demo.py | Not started (expanded scope, separate sibling package) |

## Risks

- **No live network testing against the real eCFR API performed yet** — first connected-environment task is a smoke test against it. (The caching and parsing layers' own logic have each been functionally verified independently this session.)
- **Large CFR titles can time out** on full-title retrieval upstream; mitigated by preferring part/section-level requests, but not yet stress-tested.
- **eCFR is not the CFR's legal edition of record** — may require a disclaimer or secondary verification step for high-stakes compliance determinations.
- **Clause-to-CFR mapping** (deciding which title/part is relevant to an arbitrary contract clause) remains an open design problem for the Agno agent stage, not yet addressed.
- **Citation browse-URL format is unverified** — constructed from eCFR's documented URL pattern but not confirmed against a live request (no network access in the build environment); flagged explicitly in code and docs rather than silently assumed correct.

## Conclusion

The project is on track. The research phase — including the follow-up "LCP" evaluation — validated that building a custom MCP server was the right call, and the foundation, client, caching, and parsing layers are now complete, well-documented, and verified (syntax and, where applicable, functional testing plus code review that caught and fixed a real bug). The hardest "unknowns" (eCFR's real-world XML quirks and API quirks) have already been identified and handled in code. The remaining work is well-scoped and sequenced: validation models, tool implementation, and the server entrypoint complete the MCP server; the expanded end-to-end scope (Contract Parser, Agno integration, Compliance Engine, demo) follows as a separate, architecturally decoupled milestone. No blockers currently exist.
