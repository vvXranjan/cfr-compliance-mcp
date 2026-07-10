# ARCHITECTURE
## Contract Compliance POC — eCFR MCP Server

This document describes the internal architecture of the MCP server itself (not the full contract-compliance pipeline — see `PROJECT_HANDOFF.md` Section 3 for how this server fits into that larger picture).

## Layered Design

```
┌───────────────────────────────────────────────────────────────────┐
│  tools/  ✅ COMPLETE                                               │
│  8 MCP tools — thin orchestration per tool:                       │
│  validate input → check cache → call EcfrClient → parse XML       │
│  if applicable → shape structured JSON output                     │
└───────────────────────────┬─────────────────────────────────────┘
              ┌──────────────┼──────────────┬─────────────────────┐
              ▼              ▼              ▼                     ▼
┌───────────────────┐ ┌─────────────┐ ┌──────────────┐  ┌──────────────────┐
│ models/            │ │ cache/      │ │ parsing/      │  │ clients/          │
│ ✅ COMPLETE         │ │ ✅ COMPLETE │ │ ✅ COMPLETE   │  │ ✅ COMPLETE        │
│ Pydantic request/   │ │ Backend-    │ │ Raw XML →     │  │ ecfr_client.py     │
│ response validation │ │ agnostic    │ │ clean text +  │  │ (eCFR-specific)    │
│                     │ │ cache       │ │ citations     │  │ built on           │
│                     │ │ interface   │ │               │  │ http_client.py     │
│                     │ │             │ │               │  │ (generic, reusable)│
└───────────────────┘ └─────────────┘ └──────────────┘  └──────────────────┘
                                                                    │
                                                                    ▼
                                                      ┌──────────────────────┐
                                                      │ Official eCFR REST API │
                                                      │ https://www.ecfr.gov   │
                                                      └──────────────────────┘

Cross-cutting (used by every layer above):
┌───────────────────────────────────────────────────────────────────┐
│  config.py (Settings, env-driven) │ logging_config.py (stderr-only) │
│  exceptions.py (CfrMcpError hierarchy) │ constants.py (eCFR protocol facts) │
└───────────────────────────────────────────────────────────────────┘

server.py ✅ COMPLETE — sits above tools/, owns the FastMCP app instance,
registers all 8 tools, manages HttpClient lifecycle (start/aclose).
```

## Layer Responsibilities

| Layer | Responsibility | Must NOT do |
|---|---|---|
| `constants.py` | Pure eCFR protocol facts: endpoint templates, CFR structural bounds (title 1–50), search defaults | Read config or environment variables |
| `config.py` | The only place environment variables are read; typed `Settings` singleton | Contain business logic |
| `logging_config.py` | Central, idempotent logging setup; stderr-only output | Ever write to stdout (would corrupt MCP stdio transport) |
| `exceptions.py` | The `CfrMcpError` hierarchy every other layer raises/catches | Contain any HTTP or eCFR-specific behavior |
| `clients/http_client.py` | Generic, API-agnostic async HTTP transport: retries, timeouts, rate limiting | Know anything about eCFR specifically |
| `clients/ecfr_client.py` | eCFR endpoint knowledge, date-lag/search quirk handling, translates `Http*Error` → `Ecfr*Error` | Parse XML or validate business-level input |
| `cache/cache_backend.py` | Backend-agnostic string cache with TTL | Know what's being cached (no eCFR-specific logic) |
| `parsing/xml_parser.py` | Raw eCFR XML → clean text + citation metadata | Make network calls or know about caching |
| `models/*.py` | Pydantic input validation + structured output shaping | Contain retrieval or parsing logic |
| `tools/*.py` | Thin orchestration — wire the above layers together per MCP tool | Contain business logic that belongs in a lower layer |
| `server.py` | FastMCP app instance, tool registration, `HttpClient` lifecycle management | Contain tool-specific logic |

## Data Flow (as implemented)

```
Agno Agent calls MCP tool, e.g. retrieve_section(title=40, part="261", section="10")
   │
   ▼
tools/retrieve_section.py
   │  1. Validate input          → models/requests.py (RetrieveSectionRequest)
   │  2. Build cache key         → cache.build_cache_key("section", 40, "261", "10", date)
   │  3. Check cache             → cache/cache_backend.py CacheBackend.get()
   │        │ hit → return cached structured JSON, done
   │        │ miss ↓
   │  4. Call eCFR client        → clients/ecfr_client.py EcfrClient.retrieve_section()
   │        (raw XML string returned)
   │  5. Parse XML               → parsing/xml_parser.py (clean text + citation metadata)
   │  6. Shape response          → models/responses.py (SectionResponse: text + citation)
   │  7. Write to cache          → cache.set(key, json.dumps(response))
   ▼
Structured JSON returned to the Agno agent, with citation metadata for the
eventual compliance report's audit trail.
```

## Why This Layering

- **Testability.** Each layer can be unit-tested in isolation (e.g., `cache_backend.py` was functionally tested this session with `config`/`exceptions`/`logging_config` stubbed out, requiring zero real dependencies).
- **Reusability.** `http_client.py` and `cache_backend.py` have zero eCFR-specific knowledge — both could back a completely different API/dataset with no changes.
- **Single responsibility per file.** No file mixes concerns (e.g., no file both talks to the network and parses XML).
- **Swap-ability.** The cache backend can move from in-memory to Redis, and no code outside `cache/cache_backend.py`'s factory function needs to change.

## Status of Each Layer (as of this session)

| Layer | Status |
|---|---|
| Foundation (`config`, `logging_config`, `exceptions`, `constants`) | ✅ Complete |
| Clients (`http_client`, `ecfr_client`) | ✅ Complete |
| Cache (`cache_backend`) | ✅ Complete |
| Parsing (`xml_parser`) | ✅ Complete |
| Models (`requests`, `responses`) | ✅ Complete |
| Tools (8 files + `_common.py`) | ✅ Complete |
| Server (`server.py`) | ✅ Complete |
| Tests | ❌ Not started |

**MCP server: 7/7 layers complete.** A full engineering review (dependency-graph tracing, cross-module signature verification, code-hygiene sweep) was performed after `server.py` was finished — no new defects found; all 4 previously-caught bugs (2 date-validator, 1 XML-whitespace, 1 cache-key-tuple) confirmed still fixed. The only remaining gaps are three disclosed, unexercised-due-to-no-network-access verification steps: live eCFR API calls, live `fastmcp` behavior, and live `pydantic` `BaseModel` validation (validator *logic* was tested standalone instead). See `PROJECT_HANDOFF.md` Section 6 for full detail.

This document should be updated whenever a layer's responsibilities, boundaries, or data flow change — not just when files are added. If a future module changes how layers interact (e.g., if the tool layer ends up calling the cache before or after validation differently than described above), update the Data Flow section accordingly.

## Planned: Full Pipeline Architecture (expanded scope)

The project's final goal (confirmed by the team lead) extends beyond this MCP server: `python demo.py sample_contract.pdf` must produce clause extraction, relevant CFR sections, a compliance decision with confidence and explanation, and Markdown + JSON reports. This section documents the **planned, not-yet-built** shape of that larger system, to keep the two halves of the project architecturally consistent as they're built out over time. **Nothing in this section requires changing anything already built in `cfr_compliance_mcp/`.**

```
repo root/
├── cfr_compliance_mcp/          ← THIS package. MCP server. Unaffected by the below.
│   └── (as documented above)
│
└── <planned sibling package, name TBD, e.g. "compliance_agent/">
    ├── contract_parser/          ← PDF/DOCX ingestion, clause extraction (Milestone: "Contract Parser")
    ├── agents/                   ← Agno Team/Agent definitions, MCP client wiring to cfr_compliance_mcp
    │                                (Milestone: "Agno Integration")
    ├── compliance_engine/        ← Orchestrates: clause -> agent -> MCP tool calls -> verdict + citation
    │                                (Milestone: "Compliance Engine")
    ├── reporting/                ← Markdown + JSON compliance report generation
    └── demo.py                   ← CLI entrypoint: python demo.py sample_contract.pdf
```

**Why a sibling package, not a subpackage of `cfr_compliance_mcp`:** the MCP server and the agent/pipeline that *consumes* it are architecturally distinct processes communicating over the MCP protocol (stdio or HTTP) — mixing them into one package would blur that boundary and make the MCP server harder to reason about, test, and potentially deploy independently (e.g., as a hosted MCP endpoint other tools could also connect to). This mirrors how the `court-listener-mcp`/Vaquill-AI research (Milestone 3.5) showed MCP servers being consumed by multiple different client types — our server should stay equally decoupled from any one consumer.

**Build order (per team lead's instructions):** ~~parsing → models → tools → server.py [completes the MCP server]~~ **DONE** → Contract Parser → Agno Integration → Compliance Engine → Testing → Documentation → Demo. Per explicit instruction, work stops here (MCP server milestone) before Contract Parser begins.
