# Handoff

Operational handoff for the cfr-compliance-mcp project. This document assumes a
clean clone and walks through configuration, running, testing, benchmarking,
Docker, live-test requirements, the HITL boundary, report persistence, and
known limitations.

## 1. Clone and install

```bash
git clone <repo-url>
cd cfr-compliance-mcp
cp .env.example .env    # then fill in credentials
uv sync --extra dev     # installs runtime deps + dev tools from uv.lock
```

Python 3.12+ is required (validated on 3.13). `uv.lock` is committed and is the
source of truth for reproducible installs.

## 2. Configure environment variables

All variables are optional with safe defaults. The full documented surface is
in `.env.example`. The ones you will most likely need:

| Variable | Purpose |
|---|---|
| `ATM_API_KEY` | Preferred LLM credential (ATM-hosted Nemotron endpoint) |
| `OPENAI_API_KEY` | Fallback credential for any OpenAI-compatible endpoint |
| `ECFR_BASE_URL` | eCFR API base URL (default `https://www.ecfr.gov`) |
| `CFR_REPORTS_DIR` | Directory for `/evaluate-bulk` report persistence (opt-in) |
| `CORS_ORIGINS` | Allowed CORS origins for the REST API (default `*`) |
| `JAEGER_AGENT_HOST` / `JAEGER_AGENT_PORT` | Jaeger agent for best-effort tracing (default `localhost:6831`) |
| `LOG_LEVEL` / `LOG_FORMAT` | Logging level and `json`/`text` format |
| `MCP_TRANSPORT` | `stdio` (default) or `streamable-http` for the MCP server |

Never commit a real `.env`; it is git-ignored and excluded from Docker builds.

## 3. Run the project

MCP server (stdio transport):

```bash
uv run cfr-compliance-mcp
```

REST API:

```bash
uv run uvicorn api:app --host 0.0.0.0 --port 8000
```

- `/health` — readiness check (works without credentials)
- `/evaluate-clause` — single clause evaluation
- `/evaluate-bulk` — batch evaluation (max 200 clauses)
- `/api/docs` — interactive OpenAPI docs

## 4. Run tests

```bash
uv run pytest                          # all tests (live tests skip without ATM_API_KEY)
uv run pytest --deselect tests/test_live_llm_integration.py   # offline tests only
uv run pytest tests/test_live_llm_integration.py               # live tests only
```

Current baseline: 139 passed / 0 failed / 0 skipped (130 offline + 9 live).
Live tests require `ATM_API_KEY` and network access to `https://atm.accure.ai`;
they can take several minutes.

Lint:

```bash
uv run ruff check .
```

## 5. Run the benchmark

```bash
uv run python -m benchmark.compliance_benchmark
```

The benchmark is deterministic and offline (stub LLM, network disabled). It
measures sequential vs concurrent evaluation. Its synthetic numbers must not be
confused with the historical live measurement (~12 min → ~4 min 10 s, ~65%),
which was not reproduced in the current environment.

## 6. Run Docker

```bash
docker build -t cfr-compliance-mcp .
docker run --rm -p 8000:8000 -e ATM_API_KEY=... cfr-compliance-mcp
```

- The image serves the FastAPI service on port 8000 as a non-root user.
- It is built reproducibly from `uv.lock`.
- The MCP server is a separate process (`uv run cfr-compliance-mcp`) and is not
  started by the image.
- `.dockerignore` keeps the local `.env` and large unreferenced contract PDFs
  out of the build context.

## 7. Live test requirements

- `tests/test_live_llm_integration.py` requires `ATM_API_KEY` (shell
  environment or `.env`) and network access to the ATM endpoint.
- Without them the tests skip (they are not failures).
- Live eCFR retrieval requires outbound HTTPS access to `https://www.ecfr.gov`.

## 8. The HITL boundary

The system never issues autonomous authorization. Any of the following routes a
clause to **NEEDS_REVIEW** with a `review_audit`:

- LLM evaluation failure or timeout
- Retrieval failure or empty evidence
- Verification conflict (determinism, evidence coverage, injection, version
  consistency, title validity)
- Deterministic rules that are inconclusive
- Missing CFR context (no `cfr_text` supplied to the API)

`NEEDS_REVIEW` responses carry `verification_status`, `review_reason`, and a
full `review_audit` so a human can make the final call.

## 9. Report persistence

- `/evaluate-bulk` persists an auditable JSON report when `CFR_REPORTS_DIR` is
  set (typically the repository `reports/` directory).
- Reports are written atomically (temp file + rename) with sanitized, safe
  analysis IDs and path-traversal defense.
- Reports include statuses, evidence with citations, version metadata, and the
  review audit; they exclude secrets and full contract text.
- Persistence failures are logged and never fail the request.

## 10. Known limitations

- AI-assisted compliance analysis is not autonomous legal authorization.
- NEEDS_REVIEW findings require human judgment.
- Historical regulation text depends on available eCFR/version support.
- Live LLM tests depend on external ATM availability and credentials.
- Synthetic benchmarks do not represent real external network/model latency.
- Report persistence is filesystem-based, not multi-node distributed
  persistence.
- Jaeger trace delivery requires a live Jaeger agent; the app degrades
  gracefully without one.
- The eCFR cache is process-local memory; `CACHE_BACKEND=redis` is declared in
  configuration but intentionally not implemented.

## 11. Out of scope (not implemented)

- Kubernetes, dashboards, message brokers, microservices, auth systems
- Redis (no distributed cache requirement demonstrated)
- PostgreSQL (no multi-instance persistence requirement demonstrated)
- Dense-vector RAG (retrieval is keyword/hierarchy + lexical re-ranking)