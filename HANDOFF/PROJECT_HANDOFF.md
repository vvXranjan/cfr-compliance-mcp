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
│       ├── server.py                       ✅ complete
│       ├── clients/
│       │   ├── __init__.py                 ✅ complete
│       │   ├── http_client.py               ✅ complete
│       │   └── ecfr_client.py                ✅ complete (patched: added public resolve_date())
│       ├── cache/
│       │   ├── __init__.py                 ✅ complete
│       │   └── cache_backend.py             ✅ complete
│       ├── parsing/
│       │   ├── __init__.py                 ✅ complete
│       │   └── xml_parser.py                ✅ complete
│       ├── models/
│       │   ├── __init__.py                 ✅ complete
│       │   ├── requests.py                  ✅ complete
│       │   └── responses.py                 ✅ complete
│       └── tools/
│           ├── __init__.py                 ✅ complete
│           ├── _common.py                   ✅ complete (internal helper, not one of the 8)
│           ├── search_regulations.py        ✅ complete
│           ├── search_by_keyword.py         ✅ complete
│           ├── retrieve_section.py           ✅ complete
│           ├── retrieve_part.py              ✅ complete
│           ├── retrieve_title.py             ✅ complete
│           ├── get_title_structure.py        ✅ complete
│           ├── get_version_history.py        ✅ complete
│           └── list_agencies.py              ✅ complete
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
| `src/cfr_compliance_mcp/models/requests.py` | Pydantic input-validation models, one per tool (with a shared `_TitleScopedRequest` base for the 4 title-scoped tools). `extra="forbid"` — an agent passing a typo'd parameter fails loudly. Two real bugs caught and fixed during functional testing of the shared `_validate_date` logic: (1) regex-only date checking accepted invalid calendar dates like `2026-13-40`; (2) switching to `date.fromisoformat` alone then accepted basic-ISO-format-without-dashes (`20260101`), not the documented `YYYY-MM-DD` shape. Fixed by combining an exact-shape check with `fromisoformat`. |
| `src/cfr_compliance_mcp/models/responses.py` | Pydantic output models. `CitationModel` mirrors `parsing.Citation` (kept separate so the parsing layer stays free of a Pydantic dependency). Two-tier strictness: `_StrictResponse` (`extra="forbid"`) for shapes we fully control (`RegulationTextResponse`, `ErrorResponse`, etc.); `_PassthroughResponse` (`extra="allow"`) plus raw `dict[str, Any]` fields for shapes wrapping externally-controlled, not-live-verified eCFR JSON (`SearchResultItem`, `TitleStructureResponse.structure`, `VersionHistoryResponse.versions`, `AgenciesResponse.agencies`) — a deliberate, documented trade-off given no network access this session. |
| `src/cfr_compliance_mcp/models/__init__.py` | Package marker; re-exports all request and response models. |
| `src/cfr_compliance_mcp/tools/_common.py` | Internal (not one of the 8 public tools) shared helpers: `build_error_response` (translates any exception into the structured `ErrorResponse` shape — the single place this decision is made, not duplicated 8x), `cached_call` (the cache-around-compute pattern, fail-soft on cache errors — integration-tested this session against the real `InMemoryCacheBackend`, confirming `compute()` runs exactly once per unique key and is correctly skipped on a cache hit), and `perform_search` (shared implementation backing both search tools). |
| `src/cfr_compliance_mcp/tools/search_regulations.py`, `search_by_keyword.py`, `retrieve_section.py`, `retrieve_part.py`, `retrieve_title.py`, `get_title_structure.py`, `get_version_history.py`, `list_agencies.py` | The 8 required MCP tools. Each is a **factory function** `make_<tool>_tool(ecfr_client, cache) -> Callable` (not a module-level function reading global state) — `server.py` calls each factory once at startup with the shared `EcfrClient`/`CacheBackend`, and registers the returned callable with FastMCP. Every tool: validates input via its `models.requests` model, builds a deterministic cache key via `cache.build_cache_key`, calls `cached_call`, and on any exception returns `build_error_response(exc)` rather than raising — no raw exception can reach the MCP transport layer. `get_version_history` and `list_agencies` defensively unwrap their eCFR JSON response shape (not live-verified) rather than assuming a fixed structure; this unwrapping logic was extracted and functionally tested against multiple shape variations including hostile/unexpected input. `search_regulations`/`search_by_keyword` intentionally converge on identical cache keys when their final query strings match, so equivalent searches share one cache entry. One real bug caught and fixed: `search_regulations.py`'s original cache-key construction passed a `tuple` into `build_cache_key`, violating its `str \| int \| None` parts contract — fixed by joining sorted agency slugs into a deterministic string. |
| `src/cfr_compliance_mcp/tools/__init__.py` | Package marker; re-exports all 8 `make_*_tool` factory functions. |
| `src/cfr_compliance_mcp/server.py` | FastMCP application entrypoint. `create_app()` builds the fully-wired app (settings → logging → shared `EcfrClient`/`CacheBackend` → all 8 tools registered) without starting the server, for testability. `main()` (the `uv run cfr-compliance-mcp` entry point) starts the `HttpClient`, runs the server via `mcp.run_async(...)` under `asyncio.run`, and guarantees `HttpClient.aclose()` via `try/finally` even on `KeyboardInterrupt`. **Disclosed risk:** the exact `fastmcp` API calls are written from documented v3.x patterns, not verified against a live install — `fastmcp` could not be installed in this offline sandbox. This is the single highest-risk unverified piece in the project; flagged prominently in the module's own docstring as the first thing to check once network access is available. |

## 6. Current Implementation Status

**The MCP server is code-complete.** All 7 layers are built: foundation (config, logging, exceptions, constants), clients (generic HTTP transport + eCFR-specific API client, plus a small backward-compatible patch adding a public `resolve_date()`), cache (backend-agnostic interface + in-memory implementation), parsing (raw XML → clean text + citations), models (Pydantic request/response validation), tools (all 8 MCP tools), and server.py (FastMCP entrypoint). All files syntax-verified via `python3 -m py_compile`. Functional verification was performed wherever possible without network access: cache layer (full CRUD + TTL), parsing layer (realistic eCFR XML fixtures, including a real whitespace bug caught and fixed), models layer (date-validator logic, where two real bugs were caught and fixed), and the tools layer's cache-integration behavior (verified against the real `InMemoryCacheBackend`) and defensive JSON-unwrapping logic (verified against multiple hostile input shapes).

**What has NOT been verified, disclosed explicitly rather than glossed over:**
- No live network call to the real eCFR API has been made (no network access in this sandbox).
- `pydantic` could not be installed, so full `BaseModel` instantiation/validation (as opposed to the extracted validator logic) was not executed.
- `fastmcp` could not be installed, so `server.py`'s actual FastMCP API calls are unverified against a live version.

**Not started:** formal `pytest` test suite (verification so far is ad hoc, not committed as reusable tests), README refinement/smoke-test, and everything in the expanded end-to-end scope (Contract Parser, Agno integration, Compliance Engine, `demo.py`).

## 7. Pending Modules — MCP SERVER IS NOW CODE-COMPLETE

All 7 layers of the MCP server itself are complete: foundation, clients, cache, parsing, models, tools (8 tools), server.py. What remains before this component is "done" in the fullest sense:

1. **`tests/`** — formal `pytest` suite. Verification so far this session has been thorough but ad hoc (inline functional scripts, not committed test files) — see Section 6 and the per-module notes below for exactly what was and wasn't verified.
2. **Live network testing** against the real eCFR API — never yet performed, no network access in this build sandbox. This is the single most important remaining validation step.
3. **Live `fastmcp` verification** — `server.py`'s exact API calls (`FastMCP(...)`, `mcp.tool()`, `mcp.run_async(...)`) are written from documented patterns, not verified against a live install. Flagged as the highest-risk unverified piece in the project (see `server.py`'s module docstring).
4. **`README.md` refinement** — currently accurate but was written before `server.py` existed; running instructions should be smoke-tested once `uv sync` is possible.

**Planned, additive, not yet started (does not affect the MCP server's architecture):** per the expanded project brief, a new **sibling package** will house the Contract Parser, Agno Team/Agent integration, Compliance Engine, and `demo.py`. See `ARCHITECTURE.md`.

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
models/requests.py, responses.py ──▶ constants.py, parsing.Citation    (DONE)

tools/*.py ──▶ clients/ecfr_client.py, cache/cache_backend.py,
               parsing/xml_parser.py, models/requests.py, models/responses.py,
               exceptions.py, logging_config.py                        (DONE)

server.py ──▶ everything above, plus fastmcp                           (DONE)
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

## 24. MCP Tools — IMPLEMENTED (8/8, matching original requirement)

| Tool | Backing `EcfrClient` method(s) | Factory function |
|---|---|---|
| `search_regulations(query, ...)` | `search()` | `make_search_regulations_tool` |
| `search_by_keyword(keywords[], ...)` | `search()` (multi-term query formatting via `perform_search`) | `make_search_by_keyword_tool` |
| `retrieve_section(title, part, section, date?)` | `retrieve_section()` | `make_retrieve_section_tool` |
| `retrieve_part(title, part, date?)` | `retrieve_part()` | `make_retrieve_part_tool` |
| `retrieve_title(title, date?)` | `retrieve_title()` | `make_retrieve_title_tool` |
| `get_title_structure(title, date?)` | `get_structure()` | `make_get_title_structure_tool` |
| `get_version_history(title, part?, section?, ...)` | `get_version_history()` | `make_get_version_history_tool` |
| `list_agencies()` | `list_agencies()` | `make_list_agencies_tool` |

Every tool: validates input via its `models.requests` model, checks cache (`cache.build_cache_key` + `cached_call`) before hitting the network, calls the matching `EcfrClient` method, parses XML via `parsing.parse_regulation_xml` where the response is XML, returns structured JSON (via `models.responses`) with citation metadata, and translates any exception into a structured `ErrorResponse` via `build_error_response` rather than raising to the MCP transport layer. All 8 factory functions are registered in `server.py`'s `_TOOL_FACTORIES` list, verified via AST inspection to contain exactly these 8 with no duplicates.

## 25. Agno Integration (future — MCP server scope boundary unchanged)

The Agno agent(s)/Team will connect to this MCP server as an MCP client, calling the 8 tools above per contract clause to retrieve relevant CFR text, then reasoning over (clause text + retrieved CFR text) to produce a compliance verdict with citation. No Agno-specific code exists yet, and per explicit instruction none should be written until this MCP server is fully reviewed and the milestone is reported — the MCP server is now code-complete, but Agno integration remains a deliberately separate next phase, planned as a sibling package (see `ARCHITECTURE.md`).

---

```
=========================
PROJECT STATUS
=========================

Completed
- Research & architecture phase (Milestone 1)
- Legal MCP / "LCP" research addendum (decision reaffirmed: no change)
- Foundation layer: config.py, logging_config.py, exceptions.py, constants.py
- Clients layer: http_client.py (generic), ecfr_client.py (eCFR-specific, patched with public
  resolve_date()), clients/__init__.py
- Cache layer: cache_backend.py (CacheBackend ABC, InMemoryCacheBackend, create_cache_backend,
  build_cache_key), cache/__init__.py
- Parsing layer: xml_parser.py (Citation, ParsedRegulation, parse_regulation_xml), parsing/__init__.py
- Models layer: requests.py (8 request models), responses.py (9 response models), models/__init__.py
- Tools layer: all 8 MCP tools + _common.py shared helpers, tools/__init__.py
- Server: server.py (FastMCP entrypoint, create_app()/main())
- MCP SERVER IS NOW FULLY CODE-COMPLETE (all 7 layers)
- Full engineering review performed post-completion: dependency graph traced and confirmed
  clean (no cycles), every tool's call sites cross-referenced against actual method/model
  signatures (all consistent), code-hygiene sweep (no bare excepts, no debug prints, no
  TODO/FIXME, consistent logging/typing conventions) — no new issues found, all 4 previously
  fixed bugs confirmed still fixed

Pending
- tests/ (no formal pytest coverage yet — verification so far is thorough but ad hoc: cache
  layer full CRUD+TTL, parsing layer realistic XML fixtures, models date-validator logic,
  tools cache-integration behavior against the real InMemoryCacheBackend, and defensive
  JSON-unwrapping logic all functionally tested inline this session, but not committed as
  reusable pytest files)
- Live network testing against the real eCFR API (never yet performed — no network access in
  this sandbox)
- Live fastmcp verification (server.py's exact API calls are written from documented v3.x
  patterns, not verified against a live install — flagged as the single highest-risk
  unverified piece in the project)
- Live pydantic verification (full BaseModel instantiation/validation not executed; validator
  logic was extracted and tested standalone instead)
- (Next phase, per expanded scope) Contract Parser, Agno Team/Agent integration, Compliance
  Engine, demo.py — planned as a separate sibling package, not yet started per explicit
  instruction to stop after this milestone

Next Module
- Contract Parser (PDF/DOCX) — NOT started yet per explicit instruction to stop here first.

Estimated % Complete
- MCP Server build: 100% code-complete; ~90% production-confidence (the ~10% gap is entirely
  the three disclosed live-verification gaps above — no known code defects)
- Overall project (including Contract Parser, Agno integration, Compliance Engine, demo.py,
  testing, final docs, per the expanded final goal): ~30% complete
```
