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
5. Evaluates with a compliance agent calling a configurable
   OpenAI-compatible LLM endpoint (defaults to Nemotron via ATM) over a
   direct HTTP request with structured JSON output.
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
CFR / eCFR Retrieval
        │
        ▼
Version-Aware Evidence
        │
        ▼
Deterministic Validation
        │
        ▼
Compliance Agent (LLM)
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

> Compliance Memory is an **advisory, contextual** input to the compliance
> agent step above — it is never authoritative CFR evidence and cannot
> reorder this hierarchy (see below).

## Components

- **FastMCP** — MCP server exposing the official eCFR API as 8 structured tools
  (`retrieve_section`, `retrieve_part`, `retrieve_title`, `search_regulations`,
  `search_by_keyword`, `get_title_structure`, `get_version_history`,
  `list_agencies`).
- **eCFR integration** — official `https://www.ecfr.gov` REST/XML API with
  client-side rate limiting, retries, and an in-process TTL cache
  (`CACHE_BACKEND=memory`).
- **LLM evaluation** — compliance agent that calls a configurable
  OpenAI-compatible endpoint over a direct HTTP request with strict JSON
  output. Defaults to Nemotron (`nvidia/nemotron-3-nano-omni`) via ATM
  (`ATM_API_KEY`, `ATM_BASE_URL`, `ATM_MODEL`), with an OpenAI-compatible
  fallback (`OPENAI_API_KEY`).
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
- **Report persistence** — `/evaluate-bulk` can persist an auditable report
  (opt-in via `CFR_REPORTS_DIR`) with atomic writes and
  path-traversal-safe filenames, written through a `PersistenceRepository`
  boundary. The default `file` backend keeps the existing on-disk JSON
  reports; an optional PostgreSQL backend adds queryable history and the
  human review workflow (see below).
- **Analysis history** — `GET /analyses` and `GET /analyses/{id}` expose
  queryable, paginated analysis history (with status filtering). No full
  contract text, prompts, or secrets are ever exposed or stored.
- **Human review workflow** — `GET/POST /reviews/...` implement an explicit
  reviewer lifecycle (`needs_review → under_review → approved/rejected/
  escalated`) with optimistic concurrency and an immutable audit trail.
  Reviewer decisions NEVER overwrite the original automated result,
  evidence, or `ReviewAudit`. Requires the PostgreSQL backend.
- **Human review dashboard** — a thin, server-rendered Jinja2 UI
  (`/dashboard`) over the same backend: analysis history, analysis/clause
  details with authoritative evidence, the NEEDS_REVIEW queue, review
  decisions, and the audit trail. No React/Vite/Node, no separate frontend
  service, no WebSockets.
- **Compliance Memory** — an opt-in, deterministic, durable, *advisory*
  layer (append-only JSONL store) that persists eligible **VERIFIED**
  outcomes and reuses them only as clearly labeled historical context.
  It is never a source of regulatory truth (see below).
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
| `CFR_PERSISTENCE_BACKEND` | Persistence backend: `file` (default) or `postgres` (queryable history + review workflow). Selection is explicit; no silent fallback |
| `CFR_DATABASE_URL` | PostgreSQL connection string (used when backend is `postgres`) |
| `CFR_MEMORY_ENABLED` | Enable the advisory Compliance Memory layer (default off) |
| `CFR_MEMORY_DIR` | Directory for the append-only memory store (default `<repo>/memory`) |
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
| `/health` | GET | Liveness check |
| `/health/ready` | GET | Readiness check (reflects backend usability) |
| `/evaluate-clause` | POST | Evaluate a single clause through the full pipeline |
| `/evaluate-bulk` | POST | Evaluate a batch of clauses (max 200), optionally persisting a report |

Interactive docs are available at `/api/docs` and `/api/redoc`.

## Compliance Memory (advisory, deterministic)

Compliance Memory is an opt-in layer that gives the pipeline cross-run
knowledge without ever becoming an independent source of regulatory truth.

### Authority hierarchy (never reordered)

1. Current authoritative eCFR / version-aware retrieval
2. Current deterministic validation
3. Current compliance evaluation + verification
4. Historical Compliance Memory as contextual precedent **only**

### Retrieval modes

- **Exact historical match** — may reuse a stored verdict *only* after
  every compatibility gate passes: identical clause fingerprint, an
  eligible/verified/review-free record, compatible citation/scope, and
  compatible effective-version metadata — and only when the current
  authoritative CFR retrieval is present and usable. If any gate fails,
  the record is surfaced as context, never reused.
- **Near-duplicate match** — deterministic token-overlap similarity only.
  Near records are supplied to the LLM as labeled `HISTORICAL_CONTEXT`
  (DATA), never as a verdict reuse. Near matches can never bypass the
  security → retrieval → deterministic → evaluation → verification path.

### Indexing eligibility (Option A — feedback-loop prevention)

Only **CFR-only** evaluations are automatically eligible for indexing: a
result that memory participated in (exact-match reuse or supplied
historical context) is marked `memory_participated` and is **never**
auto-indexed. Auto-indexing additionally requires `verification_status ==
"verified"`, no unresolved review state, no security rejection, and
complete evidence + provenance. NEEDS_REVIEW, security-rejected, failed
and malformed results are never indexed. Deduplication uses a stable
content-addressed `record_id` (`sha256(clause_id|citation|effective_version)`).

### Failure behavior

Memory is fail-open. A disabled, empty, malformed, or failing store
silently degrades to the authoritative-only pipeline. A memory indexing
failure is logged and never invalidates an otherwise valid compliance
result. Memory failures can never produce or change a verdict.

### Observability

Structured events: `memory_index_attempt/success/skipped`,
`memory_retrieval_success/empty/failure`, `memory_exact_match`,
`memory_near_match`, `fallback_to_authoritative_only`. Full contract
text, prompts, embeddings, secrets and API keys are never logged.

## PostgreSQL persistence & human review (optional)

The `file` backend (default) is fully self-contained. When queryable
history and an explicit human review workflow are needed, enable the
PostgreSQL backend:

```bash
# 1. Set the backend + DSN in .env
CFR_PERSISTENCE_BACKEND=postgres
CFR_DATABASE_URL=postgresql://user:pass@host/db

# 2. Apply the versioned schema (idempotent, safe to re-run)
uv run python scripts/migrate.py --dsn "$CFR_DATABASE_URL"

# 3. Start the API; it fails fast at startup if the DB is unreachable
uv run uvicorn api:app --host 0.0.0.0 --port 8000
```

### Reproducible local stack (Docker Compose)

A `compose.yaml` provides the smallest production-style local stack: the
application plus PostgreSQL 16 with a named data volume, health checks,
and a one-shot migration service. No secrets are hard-coded (values come
from the environment with safe local defaults).

```bash
docker compose up -d --build
open http://localhost:8000/dashboard
```

The `migrate` service applies the schema once before the app starts; the
`app` service waits for it to complete successfully. Data persists in the
`pgdata` volume.

Backend selection is explicit — there is never a silent fallback from
`postgres` to `file`. The schema is created by versioned SQL migrations
(`migrations/*.sql`) tracked in a `schema_migrations` table (psycopg 3,
no ORM, no Alembic).

Endpoints added (existing `/health`, `/evaluate-clause`, `/evaluate-bulk`
are unchanged):

- `GET /health` — liveness (process is up)
- `GET /health/ready` — readiness; reflects backend usability (PostgreSQL
  is pinged when configured; never returns DSNs/secrets)
- `GET /analyses` — paginated analysis history, optional `status` filter
- `GET /analyses/{analysis_id}` — full immutable report (clauses, evidence,
  provenance, verification, review audit, memory participation)
- `GET /reviews` — review queue (defaults to actionable `needs_review`)
- `GET /reviews/{analysis_id}/{clause_id}` — full review view
- `POST /reviews/{analysis_id}/{clause_id}/decide` — validated review
  decision with optimistic concurrency

A server-rendered human-review dashboard is served by the same app at
`/dashboard` (Overview, Analyses, Analysis detail, Review Queue, Review
detail with decision form and audit trail). It reads through the same
configured backend; with the `file` backend the history pages work and
review pages show a clear notice that the review workflow requires
PostgreSQL.

Human review is an **additional decision layer**: the reviewer lifecycle
(`needs_review → under_review → approved/rejected/escalated`) is
explicitly validated, uses optimistic concurrency (`expected_version`) so
concurrent reviewers cannot overwrite each other, and appends to an
immutable `review_decision_events` audit log. A reviewer decision never
overwrites the original automated result, evidence, or `ReviewAudit`.
`reviewer_identity` is an **unauthenticated placeholder** — there is no
authentication system in this milestone.

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
- Optional PostgreSQL integration tests (skipped cleanly when no test DB is
  configured): `CFR_TEST_DATABASE_URL=... uv run pytest tests/test_postgres_integration.py`
- Lint: `uv run ruff check .`

## Evaluation & Validation

- 265 offline tests pass (244 prior + overview/aggregation, readiness, and
  compose-config sanity tests using deterministic fakes);
  9 optional PostgreSQL integration tests are exercised against a real DB when
  `CFR_TEST_DATABASE_URL` is set; 9 live LLM tests require `ATM_API_KEY`
- `ruff check .` — clean
- Docker build and runtime verified (`/health`, `/health/ready`, and the
  dashboard at `/dashboard`)
- Note: the offline suite emits a Starlette `StarletteDeprecationWarning`
  about its `TestClient` preferring `httpx2`. This is an intentional
  upstream future-deprecation; the project uses `httpx` (required by its MCP
  and OpenTelemetry dependencies), so no dependency change is warranted.

## Known limitations

- AI-assisted compliance analysis is not autonomous legal authorization.
- NEEDS_REVIEW findings require human judgment.
- Historical regulation text depends on available eCFR version support.
- Live LLM tests depend on external ATM availability and credentials.
- Synthetic benchmarks do not represent real external network/model latency.
- Report persistence is filesystem-based, not multi-node distributed
  persistence.
- Compliance Memory is deterministic and local (append-only JSONL, no
  vector database or embeddings); it is advisory context only and is
  opt-in via `CFR_MEMORY_ENABLED`. Memory-assisted results require
  human-driven re-verification before they can become precedent.
- The eCFR cache is process-local memory (`CACHE_BACKEND=memory`); Redis is
  declared in configuration but not implemented.