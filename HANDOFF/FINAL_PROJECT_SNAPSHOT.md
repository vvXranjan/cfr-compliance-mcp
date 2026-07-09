# PROJECT SNAPSHOT — Read This First

**Project:** Contract Compliance POC — custom MCP server over the official eCFR API (no third-party MCP dependency).

**Pipeline (full, target):** Contract → Extract Clauses → Agno Agent → **MCP Server (this project)** → eCFR API → Retrieve CFR Laws → LLM Compliance Check → Clause-wise Report

**Decision locked in:** Build our own MCP server from scratch against `https://www.ecfr.gov` (no auth needed). Third-party MCP projects researched and rejected as production dependencies — used only as architectural reference.

## Done (✅ syntax-verified, do not regenerate)
`pyproject.toml`, `.env.example`, `.gitignore`, `__init__.py`, `config.py`, `logging_config.py`, `exceptions.py`, `constants.py`, `clients/http_client.py` (generic, reusable HTTP layer: retries/timeouts/rate-limit), `clients/ecfr_client.py` (eCFR-specific, 8 methods, handles date-lag + search-history quirks), `clients/__init__.py`.

## Not done yet (in this order)
1. `cache/cache_backend.py`
2. `parsing/xml_parser.py`
3. `models/requests.py` + `models/responses.py`
4. `tools/*.py` (8 tool files)
5. `server.py` (FastMCP entrypoint)
6. `tests/`
7. `README.md`

## Stack
Python 3.12, `uv`, `fastmcp>=3.0,<4.0` (standalone package, not the `mcp` SDK's bundled version), `httpx`, `pydantic` v2, `pydantic-settings`, `tenacity`.

## Critical things not to forget
- Logs go to **stderr only** (stdio MCP transport uses stdout for JSON-RPC).
- Clients return **raw** JSON/XML — parsing happens in the (not-yet-built) parsing layer.
- Search must default `date="current"` or eCFR returns stale/superseded matches.
- `retrieve_title()` can 504 upstream on large titles (e.g. Title 40) — no chunking built yet.
- Two separate exception families by design: generic `Http*Error` (http_client.py) → translated into `Ecfr*Error` (ecfr_client.py, from exceptions.py). Don't merge them.
- **No live network testing has happened yet** — first task in a connected environment.

## Estimated completion
MCP server: **~30%**. Full project (incl. Agno integration): **~10-12%**.

## To resume
Paste `MASTER_CONTINUATION_PROMPT.md` into a new conversation, attach `PROJECT_HANDOFF.md` for full detail, and say "continue with cache/cache_backend.py."
