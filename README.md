# cfr-compliance-mcp

A production-oriented, evidence-grounded AI compliance engineering system for
evaluating contract clauses against U.S. Code of Federal Regulations (CFR)
regulations retrieved from the official eCFR API.

> **Disclaimer:** This project provides AI-assisted compliance analysis. It is
> not a substitute for legal or regulatory professionals. Every non-trivial
> finding is routed to a human review boundary (NEEDS_REVIEW); nothing in this
> system autonomously issues legal authorization.

## What it does

Given a contract clause, the system:

1. Applies a shared security gate (prompt-injection detection, text
   sanitization, CFR title validation).
2. Runs fast, LLM-free deterministic rules.
3. Retrieves the relevant regulation text from the official eCFR API
   (version-aware where supported).
4. Enriches the result with evidence passages carrying provenance and version
   metadata.
5. Evaluates with a compliance agent (Agno + a configurable LLM).
6. Verifies the result against the evidence.
7. Returns a structured Pydantic result: **VERIFIED** or **NEEDS_REVIEW**
   (with a full review audit trail for human follow-up).

## Architecture

```text
Contract / Clause
        │
        ▼
Security Gate
        │
        ▼
Deterministic Validation
        │
        ▼
CFR / eCFR Retrieval
        │
        ▼
Version-Aware Evidence
        │
        ▼
Compliance Agent
        │
        ▼
Structured Pydantic Result
        │
        ▼
Verification
        │
   ┌────┴─────┐
   ▼          ▼
VERIFIED   NEEDS_REVIEW
               │
               ▼
          Human Review
```

## Components

- **FastMCP** — MCP server exposing the official eCFR API as 8 structured tools
  (`retrieve_section`, `retrieve_part`, `retrieve_title`, `search_regulations`,
  `search_by_keyword`, `get_title_structure`, `get_version_history`,
  `list_agencies`).
- **eCFR integration** — official `https://www.ecfr.gov` REST/XML API with
  client-side rate limiting, retries, and an in-process TTL cache
  (`CACHE_BACKEND=memory`).
- **Agno / LLM** — compliance agent with structured output via Pydantic. LLM
  defaults to Nemotron (`nvidia/nemotron-3-nano-omni`) via ATM, with an
  OpenAI-compatible fallback (`OPENAI_API_KEY`). Ollama is a supported
  compatible backend.
- **Deterministic rules** — LLM-free filter producing
  Compliant / Non-Compliant / Needs Review verdicts with evidence passages.
- **Evidence provenance** — each evidence passage records its source, retrieval
  method, timestamp, citation, text span, and version/effective-version
  metadata; fabrication is guarded by routing ungrounded results to
  NEEDS_REVIEW.
- **Verification** — a verification agent cross-checks determinism, evidence
  coverage, prompt injection, CFR title validity, and version consistency, and
  recommends accept / review / reject.
- **HITL boundary** — any uncertainty, failure, or version inconsistency
  resolves to NEEDS_REVIEW with a `ReviewAudit` (status, reason, timestamps,
  evidence citations) so a human can review.
- **REST API** — FastAPI service (`api.py`) exposing `/health`,
  `/evaluate-clause`, and `/evaluate-bulk`, with structured safe errors.
- **Report persistence** — `/evaluate-bulk` can persist an auditable JSON
  report (opt-in via `CFR_REPORTS_DIR`) with atomic writes and
  path-traversal-safe filenames.
- **Benchmarking** — a deterministic, offline benchmark harness
  (`benchmark/`) comparing sequential vs concurrent evaluation.
- **Observability** — OpenTelemetry HTTP-request tracing with best-effort
  Jaeger export; structured logging honoring `LOG_LEVEL` / `LOG_FORMAT`.
- **Docker** — reproducible, minimal image built from the committed `uv.lock`.

## Setup

```bash
cp .env.example .env       # then fill in credentials
uv sync --extra dev        # install dependencies + dev tools
uv run pytest              # run the test suite
```

Required environment variables (see `.env.example` for the full list):

| Variable | Purpose |
|---|---|
| `ATM_API_KEY` | Preferred LLM credential (ATM-hosted Nemotron endpoint) |
| `OPENAI_API_KEY` | Fallback credential for any OpenAI-compatible endpoint |
| `ECFR_BASE_URL` | eCFR API base URL (default `https://www.ecfr.gov`) |
| `CFR_REPORTS_DIR` | Enable report persistence for `/evaluate-bulk` |
| `CORS_ORIGINS` | Allowed CORS origins for the REST API |
| `JAEGER_AGENT_HOST` / `JAEGER_AGENT_PORT` | Jaeger agent for tracing (best-effort) |

Never commit a real `.env`; it is git-ignored and excluded from Docker builds.

## Running the MCP server

```bash
uv run cfr-compliance-mcp          # stdio transport (default)
uv run cfr-compliance-mcp --help   # transport options
```

For remote/networked deployment, set `MCP_TRANSPORT=streamable-http` (and
`MCP_HTTP_HOST` / `MCP_HTTP_PORT`) in the environment.

## Running the REST API

```bash
uv run uvicorn api:app --host 0.0.0.0 --port 8000
```

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health / readiness check |
| `/evaluate-clause` | POST | Evaluate a single clause through the full pipeline |
| `/evaluate-bulk` | POST | Evaluate a batch of clauses (max 200), optionally persisting a report |

Interactive docs are available at `/api/docs` and `/api/redoc`.

## Running the benchmark

```bash
uv run python -m benchmark.compliance_benchmark
```

The benchmark is deterministic and offline: it uses a stub LLM with simulated
latency and disabled network access. It validates concurrency behavior and
regression characteristics — it does **not** represent real production
eCFR/network/LLM latency.

### Synthetic deterministic benchmark (current, reproducible)

```text
24 clauses
Sequential: ~1.348s
Concurrent: ~0.112s
Improvement: ~91.7%
Concurrency: 12
LLM: deterministic stub
Simulated latency: 50 ms/clause
Network: disabled
```

### Historical live benchmark (NOT reproduced during current validation)

```text
24 clauses
Sequential: ~12 minutes
Optimized: ~4 minutes 10 seconds
Reduction: ~65%
```

The 65% figure is a historical measurement from a previous live run and was
**not** reproduced in the current deterministic validation. The 65% and 91.7%
numbers measure different things and must never be presented as equivalent.

## Docker

```bash
docker build -t cfr-compliance-mcp .
docker run --rm -p 8000:8000 -e ATM_API_KEY=... cfr-compliance-mcp
```

- The image runs the FastAPI service (`api.py`) as a non-root user on port
  8000. The MCP server runs as a separate process (`uv run cfr-compliance-mcp`)
  and is not started by the image.
- The image is built reproducibly from the committed `uv.lock` (via `uv`).
- `.dockerignore` excludes the local `.env`, virtualenvs, caches, tests,
  benchmark, the large unreferenced contract PDFs, and other non-runtime
  files from the build context.

## Testing

- Non-live tests run offline: `uv run pytest --deselect tests/test_live_llm_integration.py`
- Live LLM tests require `ATM_API_KEY` and network access to the ATM endpoint:
  `uv run pytest tests/test_live_llm_integration.py`
- Lint: `uv run ruff check .`

## Evaluation & Validation

See `docs/CLAIMS_EVIDENCE.md` for claim-by-claim evidence and
`docs/VALIDATION_REPORT.md` for the validation report, and
`docs/HANDOFF.md` for operational handoff details.

## Known limitations

- AI-assisted compliance analysis is not autonomous legal authorization.
- NEEDS_REVIEW findings require human judgment.
- Historical regulation text depends on available eCFR version support.
- Live LLM tests depend on external ATM availability and credentials.
- Synthetic benchmarks do not represent real external network/model latency.
- Report persistence is filesystem-based, not multi-node distributed
  persistence.
- The eCFR cache is process-local memory (`CACHE_BACKEND=memory`); Redis is
  declared in configuration but not implemented.