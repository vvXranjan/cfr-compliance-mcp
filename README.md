# cfr-compliance-mcp

A production-quality MCP (Model Context Protocol) server exposing the official [eCFR](https://www.ecfr.gov) (Electronic Code of Federal Regulations) API as structured tools, built for an AI-driven contract compliance pipeline.

> **Status: MCP server milestone complete.** All 7 layers — foundation, clients, cache, parsing, models, tools, server entrypoint — are built and have passed a full engineering review. Three verification gaps remain (live eCFR API calls, live `fastmcp` behavior, live `pydantic` validation) — see `HANDOFF/PROJECT_HANDOFF.md` Section 6 for detail. Automated tests and the expanded end-to-end pipeline (Contract Parser, Agno integration, Compliance Engine, demo) are next.

## What this is

This package is the **MCP server** component of a larger pipeline:

```
Contract (PDF/DOCX) → Clause Extraction → Agno Team/Agents → this MCP Server
  → Official eCFR REST API → Relevant CFR Retrieval → Compliance Reasoning
  → Clause-wise Compliance Report
```

It has **no dependency on any third-party MCP server** — it talks directly to the official, public, unauthenticated eCFR REST API (`https://www.ecfr.gov`). See `HANDOFF/PROJECT_HANDOFF.md` Section 9 and the research addendum for why.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/) for dependency management

## Setup

```bash
git clone <this repo>
cd cfr-compliance-mcp
uv sync
cp .env.example .env   # adjust settings if needed; defaults work out of the box
```

## Running

```bash
uv run cfr-compliance-mcp
```

This starts the MCP server over stdio by default (set `MCP_TRANSPORT=streamable-http` in `.env` for HTTP). **Not yet smoke-tested against a live `uv sync` install** — this repo was built in a sandbox with no network access; running it for the first time in a real environment is the recommended next step (see `HANDOFF/PROJECT_HANDOFF.md` Section 6 for the full list of disclosed, not-yet-live-verified areas).

## Project layout

```
src/cfr_compliance_mcp/
├── config.py, logging_config.py, exceptions.py, constants.py   # foundation
├── clients/        # generic HTTP transport + eCFR-specific API client
├── cache/          # backend-agnostic caching (in-memory today, Redis-ready)
├── parsing/        # raw eCFR XML -> clean text + citation metadata
├── models/         # Pydantic request/response validation (8 tools)
├── tools/          # the 8 MCP tools + shared internal helpers
└── server.py        # FastMCP entrypoint
```

Full architectural rationale is in `HANDOFF/ARCHITECTURE.md`. Chronological build history is in `HANDOFF/PROJECT_PROGRESS.md`. A single authoritative current-state reference (what's built, why, what's next) is `HANDOFF/PROJECT_HANDOFF.md` — read that first if you're picking this project up.

## Testing

```bash
uv run pytest
```

(Test suite is not yet built — see `HANDOFF/PROJECT_HANDOFF.md` for status.)

## Development conventions

- Full type hints, `strict` mypy.
- `ruff` for linting (`uv run ruff check .`).
- All configuration via `.env` / `config.Settings` — never `os.environ` directly.
- All logging via `logging_config.get_logger(__name__)` — logs go to **stderr only** (the MCP `stdio` transport uses stdout for the protocol itself; never write there).
- See `HANDOFF/PROJECT_HANDOFF.md` Section 17 for the full conventions list.
