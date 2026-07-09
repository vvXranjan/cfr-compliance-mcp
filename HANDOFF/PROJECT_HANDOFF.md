# PROJECT HANDOFF DOCUMENT
## Contract Clause Compliance POC — eCFR MCP Server

**Document purpose:** Enable a new Claude conversation (or new engineer) to continue this project with zero loss of context. Read this document fully before writing or modifying any code.

---

## 1. Project Objective

Build a Proof of Concept (POC) that automatically checks contract clauses against U.S. federal regulations (CFR) for compliance. The full target pipeline is:

```
Contract → Extract Clauses → Agno Agent → MCP Server → eCFR API
  → Retrieve Relevant CFR Laws → LLM Compliance Determination
  → Clause-wise Compliance Report
```

This handoff document covers **only the MCP Server** piece of that pipeline — the component that lets an AI agent query official U.S. federal regulations (CFR/eCFR) as structured tools. Clause extraction, the Agno agent(s), and the final report generator are downstream components **not yet started**, but are now formally in scope for the overall project (confirmed by the team lead): the project is complete only when `python demo.py sample_contract.pdf` produces clause extraction, relevant CFR sections, a compliance decision with confidence score and explanation, and both Markdown and JSON reports, with full documentation and tests. This MCP server remains architecturally unaffected by that expanded scope — see Section 3 and `ARCHITECTURE.md` for the planned (not yet built) sibling package that will house those downstream components as an MCP *client* of this server.

## 2. Business Problem

Legal/compliance teams manually cross-reference contract clauses against relevant CFR regulations — slow, error-prone, and not scalable across large contract volumes. The goal is to automate clause-by-clause compliance checking by giving an LLM-based agent structured, reliable, on-demand access to authoritative, current federal regulation text, with proper citations for auditability.

## 3. Final Architecture (target end-state)

```
Contract → Clause Extraction → Agno Agent → Custom MCP Server (this project)
  → Official eCFR REST API → LLM Compliance Reasoning → Clause-wise Compliance Report
```

The MCP server sits between the Agno agent and the eCFR API, exposing 8 typed tools (see Section 15). It never depends on any third-party/community MCP server — it is built from scratch directly against the official eCFR REST API (`https://www.ecfr.gov`), per an explicit decision made in Milestone 1.

## 4. Folder Structure (current, on disk)

Project root (this session's sandbox): `/home/claude/cfr-compliance-mcp/`

```
cfr-compliance-mcp/
├── pyproject.toml                          ✅ complete
├── .env.example                            ✅ complete
├── .gitignore                              ✅ complete
├── README.md                               ❌ not created yet
├── src/
│   └── cfr_compliance_mcp/
│       ├── __init__.py                     ✅ complete
│       ├── config.py                       ✅ complete
│       ├── logging_config.py               ✅ complete
│       ├── exceptions.py                   ✅ complete
│       ├── constants.py                    ✅ complete
│       ├── server.py                       ❌ not created yet
│       ├── clients/
│       │   ├── __init__.py                 ✅ complete
│       │   ├── http_client.py               ✅ complete
│       │   └── ecfr_client.py                ✅ complete
│       ├── cache/
│       │   ├── __init__.py                 ✅ complete
│       │   └── cache_backend.py             ✅ complete
│       ├── parsing/
│       │   ├── __init__.py                 ✅ complete
│       │   └── xml_parser.py                ✅ complete
│       ├── models/
│       │   ├── __init__.py                 ❌ not created yet
│       │   ├── requests.py                  ❌ not created yet
│       │   └── responses.py                 ❌ not created yet
│       └── tools/
│           ├── __init__.py                 ❌ not created yet
│           ├── search_regulations.py        ❌ not created yet
│           ├── retrieve_section.py           ❌ not created yet
│           ├── retrieve_part.py              ❌ not created yet
│           ├── retrieve_title.py             ❌ not created yet
│           ├── get_title_structure.py        ❌ not created yet
│           ├── search_by_keyword.py          ❌ not created yet
│           ├── get_version_history.py        ❌ not created yet
│           └── list_agencies.py              ❌ not created yet
└── tests/                                   ❌ not created yet (structure planned, no files)
    ├── __init__.py
    ├── conftest.py
    ├── test_http_client.py
    ├── test_ecfr_client.py
    ├── test_xml_parser.py
    └── test_tools/
```

**IMPORTANT:** Everything marked ✅ already exists, is syntax-verified (`py_compile` passed on every file), and reflects deliberate design decisions explained below. **Do not regenerate these files from scratch** — extend/import from them only. If a bug is found in one, patch it surgically; don't rewrite it.

## 5. Files Already Completed — Purpose of Each

| File | Purpose |
|---|---|
| `pyproject.toml` | Package metadata, dependencies (fastmcp 3.x, httpx, pydantic v2, pydantic-settings, tenacity, python-dotenv), dev tooling config (ruff, mypy, pytest), and the `cfr-compliance-mcp` CLI entry point pointing at `server:main` (not yet written). |
| `.env.example` | Documents every environment variable the server reads: eCFR base URL/timeout, retry/backoff settings, client-side rate limit, cache backend/TTL, log level/format, MCP transport (stdio vs streamable-http). |
| `.gitignore` | Excludes venvs, `.env`, caches, build artifacts. Explicitly keeps `uv.lock` committed (noted as a common mistake to avoid). |
| `src/cfr_compliance_mcp/__init__.py` | Marks the package, exposes `__version__ = "0.1.0"`. |
| `src/cfr_compliance_mcp/config.py` | `Settings` class (pydantic-settings `BaseSettings`) — the **only** place environment variables are read. `get_settings()` is an `lru_cache`-wrapped singleton accessor. Every other module gets config through this, never through `os.environ` directly. |
| `src/cfr_compliance_mcp/logging_config.py` | Central logging setup. `get_logger(__name__)` is the standard way every module obtains a logger. **Critical detail:** logs go to **stderr**, never stdout, because MCP's `stdio` transport uses stdout as the JSON-RPC wire protocol — writing logs there would corrupt it. Supports `text` (dev) and `json` (production) formats. |
| `src/cfr_compliance_mcp/exceptions.py` | The eCFR-specific exception hierarchy: `CfrMcpError` (base) → `ValidationError`, `EcfrApiError` (→ `EcfrNotFoundError`, `EcfrRateLimitedError`, `EcfrServerError`, `EcfrTimeoutError`, `EcfrConnectionError`), `XmlParsingError`, `CacheError`. This is the vocabulary the **tool layer** (not yet built) will catch. |
| `src/cfr_compliance_mcp/constants.py` | All eCFR endpoint path templates (`TITLES_ENDPOINT`, `STRUCTURE_ENDPOINT_TEMPLATE`, `FULL_TEXT_ENDPOINT_TEMPLATE`, `VERSIONS_ENDPOINT_TEMPLATE`, `SEARCH_RESULTS_ENDPOINT`, `AGENCIES_ENDPOINT`, etc.), CFR structural bounds (`MIN_CFR_TITLE=1`, `MAX_CFR_TITLE=50`), and search defaults (`SEARCH_DATE_CURRENT="current"`). No environment/config dependency — pure protocol facts. |
| `src/cfr_compliance_mcp/clients/http_client.py` | **Generic, API-agnostic** async HTTP client (`HttpClient`) built on `httpx.AsyncClient`. Provides retries via `tenacity` (exponential backoff, retries only on 5xx/429/timeout/connection errors — never on 404 or other 4xx), a client-side sliding-window rate limiter (`_RateLimiter`), and its own generic exception family (`HttpClientError` → `HttpNotFoundError`, `HttpRateLimitedError`, `HttpServerError`, `HttpTimeoutError`, `HttpConnectionError`). Deliberately has **zero knowledge of eCFR** so it's reusable for future APIs (Federal Register, GovInfo, etc.). |
| `src/cfr_compliance_mcp/clients/ecfr_client.py` | **eCFR-specific** client (`EcfrClient`) built on top of `HttpClient`. Exposes 8 async methods: `get_titles()`, `get_structure()`, `retrieve_section()`, `retrieve_part()`, `retrieve_title()`, `get_version_history()`, `list_agencies()`, `search()`. Handles eCFR-specific quirks: date-lag resolution (`_resolve_date`, since eCFR trails the Federal Register by 1-2 business days and naive "today" requests 404), and current-only search default (avoids the eCFR quirk where omitting `date=current` returns superseded historical matches). Translates every `Http*Error` into the matching `Ecfr*Error`. Also provides `create_ecfr_client(settings=None)` factory returning a matched `(HttpClient, EcfrClient)` pair. Returns **raw** JSON dicts / raw XML strings — does no parsing itself. |
| `src/cfr_compliance_mcp/clients/__init__.py` | Package marker; re-exports `EcfrClient`, `create_ecfr_client`, `HttpClient`, `HttpClientError`. |
| `src/cfr_compliance_mcp/cache/cache_backend.py` | Backend-agnostic caching layer. `CacheBackend` (ABC) defines a **string-in, string-out** interface (`get`, `set`, `delete`, `clear`, `close`) — deliberately not typed on arbitrary Python objects, since every real cache backend (in-memory, Redis, Memcached) is fundamentally a string/bytes store; callers `json.dumps`/`json.loads` around it. `InMemoryCacheBackend` is the only implementation today: a `dict` of `_CacheEntry(value, expires_at)` records, `time.monotonic()`-based TTL expiry (immune to wall-clock changes), `asyncio.Lock`-guarded for safe concurrent tool calls, lazy eviction on read plus an optional `evict_expired()` for proactive cleanup. `create_cache_backend(settings=None)` factory mirrors `create_ecfr_client()`'s pattern; selecting `CACHE_BACKEND=redis` today raises `CacheError` loudly rather than silently degrading to memory. `build_cache_key(*parts)` builds consistent `"kind:title:part:section:date"`-style keys, rendering `None` as the literal `"none"` so keys never collide ambiguously. Functionally verified at runtime (not just syntax-checked) in this session: set/get, TTL expiry, delete, clear, invalid-TTL rejection, and the redis-not-implemented guard all confirmed working. |
| `src/cfr_compliance_mcp/cache/__init__.py` | Package marker; re-exports `CacheBackend`, `InMemoryCacheBackend`, `create_cache_backend`, `build_cache_key`. |
| `src/cfr_compliance_mcp/parsing/xml_parser.py` | Converts raw eCFR XML (from `EcfrClient.retrieve_section/part/title`) into clean, LLM-readable plain text plus a structured `Citation` (title/part/section/date/heading/url). Tag-agnostic extraction via streaming `xml.etree.ElementTree.iterparse` + `elem.clear()` (bounds memory on large Title-level payloads; extracts direct text of every element in document order rather than hardcoding eCFR's paragraph tag names). Citation title/part/section/date are supplied by the caller (not scraped from the XML — the caller already knows what it requested); the section/part heading is the one thing pulled from the XML itself. Raises `XmlParsingError` on empty input, malformed XML, or well-formed XML with no extractable text. Functionally verified this session against realistic eCFR-shaped XML fixtures, including a real bug caught and fixed in self-review: source-XML line-wrapping was leaking into output paragraphs as stray line breaks, fixed via internal-whitespace collapsing. |
| `src/cfr_compliance_mcp/parsing/__init__.py` | Package marker; re-exports `Citation`, `ParsedRegulation`, `parse_regulation_xml`. |

## 6. Current Implementation Status

**Completed:** Foundation layer (config, logging, exceptions, constants) + full clients layer (generic HTTP transport + eCFR-specific API client) + full cache layer (backend-agnostic interface + in-memory implementation + factory + key builder) + full parsing layer (raw XML → clean text + citation metadata). All files syntax-verified via `python3 -m py_compile`; the cache and parsing layers were additionally functionally verified at runtime this session (dependencies stubbed, since this sandbox has no network access to install them). **No live network testing against the real eCFR API has been done** — this sandbox has no network access, so `uv sync` / actual HTTP calls to `ecfr.gov` have not been executed. This must be the first thing done in a real dev environment.

**Not started:** Pydantic request/response models, all 8 MCP tool files, the FastMCP server entrypoint, any tests, README.

## 7. Pending Modules (in dependency order)

1. ~~`cache/cache_backend.py`~~ — **COMPLETE.**
2. ~~`parsing/xml_parser.py`~~ — **COMPLETE.**
3. **`models/requests.py`** + **`models/responses.py`** — Pydantic input validation models (one per tool) and structured output/citation models. Depends on `constants.py` for bounds. **Next module.**
4. **`tools/*.py`** (8 files) — thin orchestration per tool: validate input (via `models.requests`) → check cache (via `cache_backend`) → call `EcfrClient` method → parse XML if applicable (via `xml_parser`) → shape output (via `models.responses`) → return structured JSON. Depends on all of the above.
5. **`server.py`** — FastMCP app instance, registers all 8 tools, manages `HttpClient` lifecycle (start at boot, close at shutdown) via `create_ecfr_client()`, calls `configure_logging()` at startup, reads `mcp_transport` from `Settings` to choose stdio vs streamable-http. This is the final piece before the server is runnable.
6. **`tests/`** — unit tests per module, especially `http_client`/`ecfr_client` (mocked via `pytest-httpx`) and `xml_parser` (real eCFR XML fixtures — informal versions of these fixtures were already exercised ad hoc this session; formalizing them into `pytest` cases is still pending).
7. **`README.md`** — setup/run instructions (`uv sync`, `uv run cfr-compliance-mcp`).

**Planned, additive, not yet built (does not affect the MCP server's architecture):** per the expanded project brief, a new **sibling package** (outside `cfr_compliance_mcp/`) will eventually house the Contract Parser, Agno Team/Agent integration, Compliance Engine, and `demo.py` — these consume this MCP server as an MCP *client* and do not require any change to what's built so far. See `ARCHITECTURE.md` for the planned high-level shape.

**Explicitly out of scope for this handoff:** Agno agent integration, clause extraction, compliance report generation. Work stops at a fully working, standalone MCP server.

## 8. Dependencies Between Modules

```
constants.py ──┬──▶ config.py (independent, no internal deps)
               │
logging_config.py ──▶ (depends on config.py)
exceptions.py (independent, no internal deps)

clients/http_client.py ──▶ logging_config.py only (API-agnostic, reusable)
clients/ecfr_client.py ──▶ http_client.py, constants.py, config.py, exceptions.py, logging_config.py

cache/cache_backend.py ──▶ config.py, logging_config.py, exceptions.py  (DONE)
parsing/xml_parser.py ──▶ exceptions.py, logging_config.py             (DONE)
models/requests.py, responses.py ──▶ constants.py                      (planned, next)

tools/*.py ──▶ clients/ecfr_client.py, cache/cache_backend.py,
               parsing/xml_parser.py, models/requests.py, models/responses.py,
               exceptions.py, logging_config.py                        (planned)

server.py ──▶ everything above                                         (planned)
```

## 9. Important Architectural Decisions

- **No dependency on any third-party/community MCP server.** Researched candidates (`1102tools/federal-contracting-mcps`, `beshkenadze/us-legal-tools`, `Travis-Prall/court-listener-mcp`) were evaluated and rejected as production dependencies (immature, single-maintainer, low adoption) — used only as architectural reference, never as a runtime dependency or copied code.
- **Two separate exception vocabularies.** `http_client.py` defines generic `Http*Error`s; `ecfr_client.py` translates them into eCFR-specific `Ecfr*Error`s from `exceptions.py`. This is what keeps the HTTP layer genuinely reusable for future non-eCFR APIs.
- **Clients return raw data; they never parse it.** `EcfrClient` returns raw JSON dicts and raw XML strings. Cleaning XML into text + citations is exclusively `parsing/xml_parser.py`'s job (not yet built). This keeps the client testable purely as an HTTP-and-error-translation layer.
- **`fastmcp` (standalone PyPI package, v3.x), not the `mcp` SDK's bundled FastMCP class.** Chosen because the standalone package is the current de facto standard with more production features (per official docs at gofastmcp.com as of this session).
- **Date resolution is automatic, not manual.** Callers of `retrieve_section`/`retrieve_part`/`retrieve_title`/`get_structure` don't need to know eCFR's date-lag quirk — `EcfrClient._resolve_date()` handles it transparently unless an explicit date is passed.
- **Search defaults to `date="current"`.** Prevents the eCFR search API's default behavior of returning every historical/superseded version of a match.
- **Cache interface is string-in, string-out, not typed on arbitrary Python objects.** `CacheBackend.get`/`set` operate on `str` only. Every real cache backend (in-memory, Redis, Memcached) is fundamentally a string/bytes store — standardizing on strings now means `RedisCacheBackend` can be added later with zero interface changes and zero calling-code changes in the tool layer. Callers `json.dumps`/`json.loads` around the interface.
- **Selecting an unimplemented cache backend fails loudly, not silently.** `create_cache_backend()` raises `CacheError` immediately if `CACHE_BACKEND=redis` is configured, rather than silently falling back to in-memory — a real-deployment misconfiguration should never be hidden.

## 10. Design Principles Followed

- Explicit typed configuration (no scattered `os.environ` calls).
- One exception hierarchy per abstraction layer; never leak a lower layer's exception type across an abstraction boundary undostranslated.
- Every file has a single, clearly stated responsibility (constants ≠ config ≠ transport ≠ API knowledge ≠ parsing ≠ validation ≠ orchestration).
- Defense in depth on validation (client-layer validates title bounds/query non-emptiness even though the future Pydantic model layer will also validate).
- Full type hints throughout (`from __future__ import annotations`, `Settings`, `Self`, etc.), targeting strict `mypy`.
- Structured logging everywhere via `extra={...}`, never bare `print()`.
- No secrets in code; everything configurable via `.env`.

## 11. Known Assumptions

- eCFR API remains free, public, and unauthenticated (true as of this session's research).
- Client-side rate limit default (60/min) is a self-imposed conservative estimate, not an eCFR-published limit — may need tuning once real traffic patterns are observed.
- One `HttpClient` instance (one connection pool) is sufficient for the whole server process — no need for per-tool or per-request clients.
- Contract clauses will map to CFR **Part**-level or **Section**-level granularity most of the time; whole-**Title** retrieval is an edge case, not the common path.

## 12. Known Risks

- **No live network testing yet.** All code is syntax-verified only, not integration-tested against the real `ecfr.gov` API. First priority in a networked environment: run `uv sync`, then a smoke test against `get_titles()`.
- **Large titles (e.g., Title 40/EPA) can 504 timeout** on full-title retrieval — `retrieve_title()` explicitly warns about this but doesn't implement chunking; that decision is deferred to the tool layer.
- **eCFR is not the legal edition of record** (that's the annual GPO print edition) — a real product may need a disclaimer or secondary verification for high-stakes determinations.
- **Clause-to-CFR-title/part mapping** (how the Agno agent decides which title/part to search) is the hardest unsolved problem in the whole pipeline and has not been designed at all yet — flagged in the original research report as needing dedicated design time.
- Single-maintainer risk was the reason we rejected third-party MCPs; our own code now carries that same single-maintainer risk until reviewed by others.

## 13. Important Implementation Notes

- MCP `stdio` transport requires **stdout to be pristine JSON-RPC only** — logging must never write to stdout (already handled correctly in `logging_config.py`).
- `EcfrClient` methods are `async` throughout — the whole codebase is async, no sync/async mixing.
- `create_ecfr_client()` returns `(HttpClient, EcfrClient)` as a tuple specifically so `server.py` can own the `HttpClient`'s `start()`/`aclose()` lifecycle explicitly (call `start()` once at process boot, `aclose()` once at shutdown) rather than opening/closing per request.
- Retry logic only retries transient failures (5xx, 429, timeout, connection error) — never a plain 404 or 400, since retrying an identical malformed/missing-resource request cannot succeed.

## 14. APIs Used

- **Official eCFR REST API** — base URL `https://www.ecfr.gov`, no authentication, endpoints under `/api/versioner/v1/`, `/api/search/v1/`, `/api/admin/v1/`. Full documentation: `https://www.ecfr.gov/developers/documentation/api/v1`.
- No other external API is used or planned for the MCP server itself.

## 15. Libraries Used

| Library | Version constraint | Purpose |
|---|---|---|
| `fastmcp` | `>=3.0,<4.0` | MCP server framework (standalone package) |
| `httpx` | `>=0.27` | Async HTTP client |
| `pydantic` | `>=2.7` | Input/output validation models |
| `pydantic-settings` | `>=2.3` | Typed `.env`-driven configuration |
| `tenacity` | `>=8.3` | Retry/backoff logic |
| `python-dotenv` | `>=1.0` | `.env` file loading (used indirectly by pydantic-settings) |
| `pytest`, `pytest-asyncio`, `pytest-httpx` (dev) | — | Testing, including mocked async HTTP |
| `ruff`, `mypy` (dev) | — | Linting, strict type checking |

## 16. Configuration Used (env vars, all in `.env.example`)

`ECFR_BASE_URL`, `ECFR_REQUEST_TIMEOUT_SECONDS`, `ECFR_MAX_RETRIES`, `ECFR_RETRY_BACKOFF_BASE_SECONDS`, `ECFR_MAX_REQUESTS_PER_MINUTE`, `CACHE_BACKEND`, `CACHE_TTL_SECONDS`, `REDIS_URL`, `LOG_LEVEL`, `LOG_FORMAT`, `MCP_TRANSPORT`, `MCP_HTTP_HOST`, `MCP_HTTP_PORT`.

## 17. Coding Conventions

- Python 3.12, `src/` layout, package name `cfr_compliance_mcp`.
- `from __future__ import annotations` at the top of every module.
- Full type hints on every function signature; `strict = true` in `[tool.mypy]`.
- Docstrings on every public class/method explaining *why*, not just *what* (e.g., noting eCFR quirks being worked around).
- One logger per module via `get_logger(__name__)`.
- Ruff rules: `E, F, I, UP, B, ASYNC`.
- 100-character line length.

## 18. Error Handling Strategy

Three-layer exception translation:
1. `httpx` exceptions → generic `Http*Error` (in `http_client.py`).
2. Generic `Http*Error` → eCFR-specific `Ecfr*Error` (in `ecfr_client.py`).
3. (Planned) `Ecfr*Error`/`ValidationError`/`XmlParsingError` → structured JSON error responses returned to the LLM by each tool (in `tools/*.py`), never a raw traceback.

Validation errors are never retried (caller's mistake); transient errors (5xx/429/timeout/connection) are retried with backoff.

## 19. Logging Strategy

Single central configuration (`logging_config.py`), configured once per process (`configure_logging()`, idempotent). Two formats: human-readable text (dev) or single-line JSON (production/aggregation), selected via `LOG_FORMAT`. **All logs go to stderr** — required for MCP stdio transport correctness. Every module logs via `get_logger(__name__)`; structured context passed via `extra={...}` (e.g., `extra={"query": query, "page": page}`).

## 20. Retry Strategy

`tenacity.AsyncRetrying` in `http_client.py`: `stop_after_attempt(max_retries + 1)`, `wait_exponential(multiplier=retry_backoff_base_seconds, min=retry_backoff_base_seconds)`, retrying only on `(HttpServerError, HttpTimeoutError, HttpConnectionError, HttpRateLimitedError)`, with `reraise=True` and a `before_sleep` hook that logs every retry attempt with the triggering exception.

## 21. Rate Limiting Strategy

Client-side only (eCFR publishes no official hard limit). `_RateLimiter` in `http_client.py` implements a sliding 60-second window using a timestamp `deque` guarded by an `asyncio.Lock`; default cap 60 requests/minute (configurable via `ECFR_MAX_REQUESTS_PER_MINUTE`), chosen conservatively based on a third-party connector's observed throttle (100/60s) as a reference point, not an official eCFR limit.

## 22. Caching Strategy (COMPLETE)

`cache/cache_backend.py` defines `CacheBackend` (ABC, string-in/string-out interface: `get`/`set`/`delete`/`clear`/`close`) and `InMemoryCacheBackend` (dict + `time.monotonic()`-based TTL expiry, `asyncio.Lock`-guarded, lazy eviction on read + optional `evict_expired()`). `create_cache_backend(settings=None)` factory reads `CACHE_BACKEND`/`CACHE_TTL_SECONDS` from `Settings` (`CACHE_BACKEND=memory` default, `CACHE_TTL_SECONDS=3600` default); `CACHE_BACKEND=redis` raises `CacheError` today since no Redis implementation exists yet (`REDIS_URL` is already present in config for when it's built — adding `RedisCacheBackend(CacheBackend)` requires no changes to calling code). Cache key design: `build_cache_key(*parts)` builds `"kind:title:part:section:date"`-style keys (e.g. `build_cache_key("section", 40, "261", "10", "2026-01-01")`), since contracts will repeatedly reference the same CFR sections across multiple clauses. Functionally verified at runtime this session (set/get, TTL expiry, delete, clear, invalid-TTL rejection, redis-not-implemented guard).

## 23. XML Parsing Strategy (COMPLETE)

`parsing/xml_parser.py` converts raw eCFR XML into clean, LLM-readable plain text plus a structured `Citation` object (`title`, `part`, `section`, `date`, `heading`, `url`). Parsing is **tag-agnostic**: rather than hardcoding eCFR's paragraph-like tag names (`P`, `FP`, `EXTRACT`, `NOTE`, `CITA`, ...), it extracts the direct text of every XML element in document order via `xml.etree.ElementTree.iterparse` (streaming) + `elem.clear()` per element, bounding memory use even for large Title-level payloads. Citation title/part/section/date are supplied explicitly by the caller (the caller already knows what it requested); the section/part heading is the one value pulled from the XML itself. Internal whitespace from eCFR's line-wrapped source XML is collapsed so it doesn't leak into output paragraphs (a real bug caught and fixed during this session's code review). Raises `XmlParsingError` on empty input, malformed XML, or well-formed-but-empty content. The citation's browse URL (`ecfr.gov/current/title-X/...`) is explicitly documented as best-effort/unverified, since no live network access was available to confirm the exact URL pattern this session.

## 23a. Legal MCP / "LCP" Research Addendum

Per a team-lead request, a separate research pass evaluated whether an existing "Legal MCP" (referred to as "LCP") could replace this custom build. Findings: no single canonical "LCP" project exists in the ecosystem; the closest candidates (`open-legal-compliance-mcp`, `court-listener-mcp` and its Vaquill-AI fork, the commercial Vaquill AI MCP) were evaluated against CFR/eCFR support, official-API integration, tool-surface fit, and production readiness. **None call the official eCFR REST API directly** — they route through GovInfo, CourtListener's own mirror (subject to a restrictive 125-requests/day free-tier cap as of May 2026), or a proprietary indexed corpus of uncertain freshness. **Decision reaffirmed: continue building the custom MCP server (no change).** Full comparison table and reasoning preserved in this session's conversation history; not duplicated here to avoid document bloat, but should be copied into a standalone research addendum file if this decision is ever revisited.

## 24. Expected MCP Tools (8, matching original requirement)

| Tool | Backing `EcfrClient` method(s) |
|---|---|
| `search_regulations(query, ...)` | `search()` |
| `search_by_keyword(keywords[], ...)` | `search()` (multi-term query formatting) |
| `retrieve_section(title, part, section, date?)` | `retrieve_section()` |
| `retrieve_part(title, part, date?)` | `retrieve_part()` |
| `retrieve_title(title, date?)` | `retrieve_title()` |
| `get_title_structure(title, date?)` | `get_structure()` |
| `get_version_history(title, part?, section?, ...)` | `get_version_history()` |
| `list_agencies()` | `list_agencies()` |

Every tool must: validate input (Pydantic), check cache before hitting the network, call the matching `EcfrClient` method, parse XML if the response is XML, return structured JSON with citation metadata, and translate any exception into a structured error payload rather than raising to the MCP transport layer.

## 25. Expected Agno Integration (future, out of scope for this handoff)

The Agno agent will connect to this MCP server as an MCP client, calling the 8 tools above per contract clause to retrieve relevant CFR text, then reasoning over (clause text + retrieved CFR text) to produce a compliance verdict with citation. No Agno-specific code exists yet and none should be written until the MCP server itself is complete and tested end-to-end (per explicit scope boundary: "stop before Agno integration").

---

```
=========================
PROJECT STATUS
=========================

Completed
- Research & architecture phase (Milestone 1)
- Legal MCP / "LCP" research addendum (decision reaffirmed: no change)
- Foundation layer: config.py, logging_config.py, exceptions.py, constants.py
- Clients layer: http_client.py (generic), ecfr_client.py (eCFR-specific), clients/__init__.py
- Cache layer: cache_backend.py (CacheBackend ABC, InMemoryCacheBackend, create_cache_backend,
  build_cache_key), cache/__init__.py
- Parsing layer: xml_parser.py (Citation, ParsedRegulation, parse_regulation_xml),
  parsing/__init__.py
- All files syntax-verified (py_compile); cache and parsing layers additionally functionally
  verified at runtime this session (dependencies stubbed since sandbox has no network access
  to install them); one real bug (source-XML whitespace leakage) caught and fixed in
  self-review before sign-off

Pending
- models/requests.py, models/responses.py
- 8 tool files in tools/
- server.py (FastMCP entrypoint)
- tests/ (no formal pytest coverage yet — ad hoc functional verification only)
- README.md
- Live network testing against the real eCFR API (never yet performed — no network access in
  this sandbox)
- (Later, per expanded scope) Contract Parser, Agno Team/Agent integration, Compliance Engine,
  demo.py — planned as a separate sibling package, not yet started

Next Module
- models/requests.py + models/responses.py (depends on constants.py only — safe to build next)

Estimated % Complete
- MCP Server build: ~50% complete (foundation + clients + cache + parsing done; models, tools,
  server entrypoint, and all formal tests remain)
- Overall project (including Contract Parser, Agno integration, Compliance Engine, demo.py,
  per the expanded final goal): ~18% complete
```
