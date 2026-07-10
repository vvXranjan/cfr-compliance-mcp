# Engineering Progress Report
## Contract Compliance POC — eCFR MCP Server

**Prepared by:** Vvr
**Status:** MCP SERVER MILESTONE COMPLETE — all 7 layers built, reviewed, and documented

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

The MCP server is a layered Python application, now fully built:
- **Config & logging layer** *(complete)* — typed, environment-driven settings; structured logging to stderr (required for MCP's stdio transport).
- **Client layer** *(complete)* — a generic, reusable async HTTP client (retries, timeouts, rate limiting) plus an eCFR-specific client built on top of it, handling real eCFR quirks (date lag, search history pollution).
- **Caching layer** *(complete)* — backend-agnostic, string-in/string-out interface; avoids redundant network calls across clauses referencing the same regulation.
- **Parsing layer** *(complete)* — converts raw eCFR XML into clean text with structured citations.
- **Models layer** *(complete)* — Pydantic request/response validation for all 8 tools.
- **Tool layer** *(complete)* — all 8 MCP tools exposed to the agent: `search_regulations`, `search_by_keyword`, `retrieve_section`, `retrieve_part`, `retrieve_title`, `get_title_structure`, `get_version_history`, `list_agencies`.
- **Server entrypoint** *(complete)* — `server.py` wires everything into a runnable FastMCP application.

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
| Input/output validation models (`models/`) | Complete | 8 request models + 9 response models; two real date-validation bugs found and fixed during testing |
| MCP tool implementations (8 tools + shared helpers) | Complete | Factory-function pattern for testability; cache-integration behavior verified against the real cache backend; one real bug found and fixed |
| Server entrypoint (`server.py`) | Complete | FastMCP app wiring; `HttpClient` lifecycle managed via `try/finally`; disclosed risk: `fastmcp` API calls unverified against a live install (no network access) |
| Automated tests | Not started | Verification so far is thorough but ad hoc (inline functional scripts), not committed as `pytest` files |

## Current Progress

**The MCP server is now fully code-complete: all 7 layers are built** (foundation, clients, cache, parsing, models, tools, server entrypoint). Every file has been syntax-verified, and a full post-completion engineering review traced the entire dependency graph (confirmed clean, no circular imports), cross-referenced every tool's calls against actual method and model signatures (all consistent), and swept for code-hygiene issues (none found). Four real bugs were caught and fixed over the course of the build — two date-validation bugs, one XML-whitespace formatting bug, and one cache-key type bug — each caught by direct functional testing or code review before sign-off, not left for later discovery. Three verification gaps remain, all disclosed explicitly rather than hidden: no live network call to the real eCFR API, no live `fastmcp` verification, and no live `pydantic` `BaseModel` verification (all three blocked by this build environment having no network access — confirmed via an actual failed `pip install` attempt, not assumed). These are the right next steps once network access is available, and no known code defects are being carried forward.

## Implementation Decisions

- Standalone `fastmcp` package (v3.x) selected over the MCP SDK's bundled FastMCP class, for more mature production features.
- Strict separation between a reusable, API-agnostic HTTP transport layer and an eCFR-specific client layer, so future integrations (e.g., Federal Register, GovInfo) can reuse the transport layer unchanged.
- All configuration is environment-driven and centrally typed — no scattered environment-variable reads.
- Defense-in-depth validation: input checks exist at the client layer even though a dedicated validation layer is still to be added.
- Cache interface deliberately operates on strings only (not arbitrary Python objects), so a future Redis-backed implementation requires no changes to any calling code.
- Unimplemented cache backends fail loudly rather than silently degrading, to prevent hidden production misconfiguration.
- XML parsing is tag-agnostic (extracts text from every element rather than a hardcoded tag whitelist), making it robust to eCFR schema variation across different CFR titles.
- Tools are built as factory functions (`make_<tool>_tool(ecfr_client, cache) -> Callable`) rather than reading global state, so the shared `EcfrClient`/`CacheBackend` are injected once by `server.py` and every tool stays independently unit-testable.
- Response models use two tiers of strictness: strict validation (`extra="forbid"`) for data shapes we fully control, and lenient passthrough (`extra="allow"` / raw dicts) for externally-controlled eCFR JSON shapes not yet live-verified — avoiding brittle validation against an unconfirmed schema.

## Remaining Work

1. Automated `pytest` test suite (formalizing this session's ad hoc verification into reusable tests).
2. Live integration testing against the real eCFR API.
3. Live verification of `fastmcp` and `pydantic` behavior (both unavailable in the offline build sandbox).
4. README smoke-test once `uv sync` is possible.
5. *(Expanded scope)* Contract Parser, Agno Team/Agent integration, Compliance Engine, and `demo.py` — planned as a separate sibling package, starting next.

## Next Milestone

Contract Parser (PDF/DOCX ingestion + clause extraction) — the first component of the expanded end-to-end pipeline, to be built as a sibling package per `ARCHITECTURE.md`. This involves new architectural decisions (PDF/DOCX parsing library choice, clause-segmentation strategy) that will be presented before implementation begins.

## Timeline

| Phase | Status |
|---|---|
| Research & architecture | Complete |
| Legal MCP ("LCP") evaluation | Complete — decision reaffirmed |
| Foundation + client layer | Complete |
| Caching layer | Complete |
| Parsing layer | Complete |
| Validation models + tool layer | Complete |
| Server entrypoint | Complete |
| **MCP server milestone** | **Complete** |
| Automated tests + live integration validation | Upcoming |
| Contract Parser + Agno integration + Compliance Engine + demo.py | Not started (expanded scope, separate sibling package) |

## Risks

- **No live network testing against the real eCFR API performed yet** — first connected-environment task is a smoke test against it.
- **`fastmcp` and `pydantic` behavior unverified against live installs** — this build sandbox has no network access (confirmed via a failed `pip install` attempt), so both packages' actual runtime behavior could not be exercised. Mitigated by extracting and testing the underlying logic wherever possible, and disclosed explicitly rather than assumed correct.
- **Large CFR titles can time out** on full-title retrieval upstream; mitigated by preferring part/section-level requests, but not yet stress-tested.
- **eCFR is not the CFR's legal edition of record** — may require a disclaimer or secondary verification step for high-stakes compliance determinations.
- **Clause-to-CFR mapping** (deciding which title/part is relevant to an arbitrary contract clause) remains an open design problem for the Agno agent stage, not yet addressed.
- **Citation browse-URL format is unverified** — constructed from eCFR's documented URL pattern but not confirmed against a live request; flagged explicitly in code and docs rather than silently assumed correct.

## Conclusion

**The MCP server milestone is complete.** All 7 layers — foundation, clients, cache, parsing, models, tools, and server entrypoint — are built, internally consistent, and have passed a full post-completion engineering review with no new defects found. Four real bugs were caught and fixed during development, each through direct testing rather than left latent. The three remaining verification gaps (live eCFR, live fastmcp, live pydantic) are clearly scoped, disclosed, and are the correct first steps once network access is available — no known code defects are being carried forward into the next phase. Per instruction, work stops here before Contract Parser begins, pending review of this milestone.
