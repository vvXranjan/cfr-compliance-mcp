I am continuing an existing engineering project across a new Claude conversation because the previous one hit its token limit. Do NOT start from scratch and do NOT regenerate any file described below as already complete. Read this entire prompt before doing anything.

PROJECT CONTEXT
I am building a POC pipeline:
Contract → Extract Clauses → Agno Agent → MCP Server → eCFR API → Retrieve Relevant CFR Laws → LLM Compliance Determination → Clause-wise Compliance Report

We already completed the research phase and decided NOT to depend on any third-party/community MCP server for eCFR. We are building a production-quality custom MCP server from scratch, in Python 3.12, using uv, the standalone `fastmcp` package (v3.x, not the `mcp` SDK's bundled FastMCP), pyproject.toml, .env-based config, structured logging, full type hints, and a professional layered folder structure.

WHAT HAS ALREADY BEEN BUILT (do not regenerate — attached/described below is authoritative)
Project root: cfr-compliance-mcp/src/cfr_compliance_mcp/

Completed and syntax-verified files:
1. pyproject.toml — deps: fastmcp>=3.0,<4.0, httpx>=0.27, pydantic>=2.7, pydantic-settings>=2.3, tenacity>=8.3, python-dotenv>=1.0; dev deps: pytest, pytest-asyncio, pytest-httpx, ruff, mypy; entry point cfr-compliance-mcp -> cfr_compliance_mcp.server:main
2. .env.example — documents ECFR_BASE_URL, ECFR_REQUEST_TIMEOUT_SECONDS, ECFR_MAX_RETRIES, ECFR_RETRY_BACKOFF_BASE_SECONDS, ECFR_MAX_REQUESTS_PER_MINUTE, CACHE_BACKEND, CACHE_TTL_SECONDS, REDIS_URL, LOG_LEVEL, LOG_FORMAT, MCP_TRANSPORT, MCP_HTTP_HOST, MCP_HTTP_PORT
3. .gitignore
4. src/cfr_compliance_mcp/__init__.py — package marker, __version__
5. src/cfr_compliance_mcp/config.py — Settings(BaseSettings) from pydantic-settings, get_settings() lru_cache singleton. ALL config reads go through this — nothing else touches os.environ directly.
6. src/cfr_compliance_mcp/logging_config.py — configure_logging() + get_logger(name). Logs go to STDERR ONLY (critical: MCP stdio transport uses stdout for JSON-RPC; logging to stdout would corrupt the protocol). Supports text/json formats via LOG_FORMAT.
7. src/cfr_compliance_mcp/exceptions.py — CfrMcpError base; ValidationError; EcfrApiError base with subclasses EcfrNotFoundError, EcfrRateLimitedError, EcfrServerError, EcfrTimeoutError, EcfrConnectionError; XmlParsingError; CacheError.
8. src/cfr_compliance_mcp/constants.py — eCFR endpoint path templates (TITLES_ENDPOINT, STRUCTURE_ENDPOINT_TEMPLATE, FULL_TEXT_ENDPOINT_TEMPLATE, VERSIONS_ENDPOINT_TEMPLATE, SEARCH_RESULTS_ENDPOINT, AGENCIES_ENDPOINT, etc.), MIN_CFR_TITLE=1, MAX_CFR_TITLE=50, SEARCH_DATE_CURRENT="current". No env/config dependency.
9. src/cfr_compliance_mcp/clients/http_client.py — Generic, API-agnostic async HttpClient (httpx.AsyncClient-based). Retries via tenacity (exponential backoff, retries only 5xx/429/timeout/connection errors, never plain 4xx). Client-side sliding-window rate limiter (_RateLimiter, asyncio.Lock + deque of timestamps). Its own generic exception family: HttpClientError -> HttpNotFoundError, HttpRateLimitedError, HttpServerError, HttpTimeoutError, HttpConnectionError. Zero knowledge of eCFR — reusable for future APIs. Has start()/aclose()/async context manager lifecycle; get(path, params=, headers=) returns raw httpx.Response.
10. src/cfr_compliance_mcp/clients/ecfr_client.py — EcfrClient built on HttpClient. Methods: get_titles(), get_structure(title, date=None), retrieve_section(title, part, section, date=None), retrieve_part(title, part, date=None), retrieve_title(title, date=None), get_version_history(title, part=, section=, issue_date_on/lte/gte=), list_agencies(), search(query, agency_slugs=, date="current", per_page=, page=). Handles eCFR date-lag quirk via _resolve_date() (looks up title's up_to_date_as_of from get_titles() when no explicit date given, cached per-title for process lifetime). search() defaults date="current" to avoid superseded-version pollution. Translates every Http*Error into matching Ecfr*Error. Returns RAW JSON dicts / RAW XML strings — does NOT parse XML itself. Also exposes create_ecfr_client(settings=None) -> (HttpClient, EcfrClient) factory.
11. src/cfr_compliance_mcp/clients/__init__.py — re-exports EcfrClient, create_ecfr_client, HttpClient, HttpClientError.

A full PROJECT_HANDOFF.md with complete architectural rationale, design decisions, risks, and conventions exists — if it's attached to this conversation, treat it as authoritative and read it fully before writing code. If it's not attached, ask me to paste it before proceeding.

WHAT MUST NEVER BE REGENERATED
Do not rewrite any of the 11 files listed above from scratch. If a bug is found, propose a surgical patch (show only the diff/change) and explain why, rather than regenerating the whole file. Do not re-litigate the decision to avoid third-party MCP servers, the choice of standalone fastmcp over the mcp SDK's bundled version, or the exception-hierarchy split between http_client.py (generic Http*Error) and ecfr_client.py (translates to Ecfr*Error) — these are settled decisions.

CURRENT FOLDER STRUCTURE
cfr-compliance-mcp/
├── pyproject.toml, .env.example, .gitignore (done)
├── src/cfr_compliance_mcp/
│   ├── __init__.py, config.py, logging_config.py, exceptions.py, constants.py (done)
│   ├── server.py (NOT built yet — next-to-last step)
│   ├── clients/ (done: __init__.py, http_client.py, ecfr_client.py)
│   ├── cache/ (empty — cache_backend.py not built)
│   ├── parsing/ (empty — xml_parser.py not built)
│   ├── models/ (empty — requests.py, responses.py not built)
│   └── tools/ (empty — 8 tool files not built: search_regulations, retrieve_section, retrieve_part, retrieve_title, get_title_structure, search_by_keyword, get_version_history, list_agencies)
└── tests/ (empty, structure planned only)

REMAINING WORK, IN ORDER
1. cache/cache_backend.py — pluggable cache interface, in-memory + TTL implementation for the POC (config already has CACHE_BACKEND, CACHE_TTL_SECONDS, REDIS_URL ready for a future Redis swap). No dependency on parsing/models/tools — build this first.
2. parsing/xml_parser.py — raw eCFR XML (from retrieve_section/part/title) -> clean text + structured citation metadata {title, part, section, date, url}. Depends only on exceptions.py (XmlParsingError).
3. models/requests.py + models/responses.py — Pydantic input validation per tool + structured output/citation models. Depends on constants.py for bounds.
4. tools/*.py (8 files) — thin orchestration per tool: validate (models.requests) -> check cache (cache_backend) -> call EcfrClient method -> parse XML if applicable (xml_parser) -> shape output (models.responses) -> return structured JSON, catching all exceptions into structured error payloads.
5. server.py — FastMCP app, registers all 8 tools, owns HttpClient lifecycle via create_ecfr_client() (start() at boot, aclose() at shutdown), calls configure_logging() at startup, reads mcp_transport from Settings (stdio vs streamable-http).
6. tests/ — unit tests, especially http_client/ecfr_client (mock via pytest-httpx) and xml_parser (real eCFR XML fixtures).
7. README.md — setup/run instructions.

Explicitly OUT OF SCOPE until the MCP server is fully complete and tested: Agno agent integration, clause extraction, compliance report generation. Stop before Agno integration, as originally instructed.

IMPLEMENTATION PHILOSOPHY / HOW TO WORK
- Never dump many files in one response. Generate ONE file at a time.
- Before writing any file, briefly explain the architecture/design for that module if it's the first file in a new sub-package.
- After every file, explain: why it exists, how it interacts with already-completed files, and whether it's complete.
- Full type hints everywhere (`from __future__ import annotations`), strict-mypy-compatible.
- Every module logs via `get_logger(__name__)` from logging_config.py — never bare print(), never log to stdout.
- All config reads go through `get_settings()` from config.py — never os.environ directly.
- Validate defensively at every layer that could be called directly, even if another layer also validates.
- Use the exception hierarchy already defined in exceptions.py; do not invent parallel exception types for the same failure modes.
- Only use the OFFICIAL eCFR REST API (https://www.ecfr.gov) — no unofficial wrappers, no copied code from third-party MCP projects (architectural reference only, already used in Milestone 1 research).
- After completing each module (cache, then parsing, then models, then tools, then server), produce a PROJECT STATUS block: Completed / Files Created / How these files interact / Pending / Next Module.
- Continue module-by-module in the order listed under REMAINING WORK until the entire MCP server is production-ready, then stop before any Agno integration work.

Please start by briefly confirming your understanding of what's already built, then proceed straight to building cache/cache_backend.py (explain architecture first, then the file, then your usual after-file explanation, then the PROJECT STATUS block).
