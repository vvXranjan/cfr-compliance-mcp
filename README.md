# CFR Compliance MCP

> An agentic compliance analysis system that evaluates contract clauses against
> authoritative U.S. federal regulations using MCP-based retrieval, deterministic
> rules, LLM reasoning, evidence verification, durable memory, human review
> workflows, and production-ready persistence.

---

## Why This Project

LLMs are powerful, but they should not be allowed to freely invent regulatory
evidence or autonomously execute high-stakes decisions.

This system demonstrates a **controlled AI architecture** where:

- **authoritative regulatory retrieval comes first** — current CFR text is the
  only source of regulatory truth
- **deterministic checks constrain the workflow** — LLM-free rules provide fast,
  auditable verdicts before any model is consulted
- **LLM reasoning is evidence-grounded** — the model must cite only the provided
  regulation, and provenance is attached by the retrieval layer, never the model
- **verification gates uncertain outputs** — conflicts and ungrounded results
  resolve to `NEEDS_REVIEW`, never silent acceptance
- **historical memory is advisory** — prior outcomes are context, not law, and
  can never override the current regulation
- **humans control unresolved decisions** — an explicit review lifecycle
  preserves the original automated result and an immutable audit trail
- **persistence and audit trails make every decision inspectable** — via
  filesystem or optional PostgreSQL, with a review dashboard

## Key Capabilities

- MCP-based retrieval of the official **eCFR** API (8 typed tools)
- Configurable **LLM evaluation** via an OpenAI-compatible endpoint
- Deterministic compliance rules (LLM-free, auditable)
- Evidence grounding + independent **verification**
- **Prompt-injection defenses** and security boundaries
- **Advisory compliance memory** with durable JSONL storage
- Immutable analysis snapshots
- **Optional PostgreSQL** backend with versioned SQL migrations
- Queryable **analysis history**
- **Human-in-the-loop review** with optimistic concurrency
- **Immutable audit events**
- Server-rendered **Jinja2 dashboard**
- **Docker + Docker Compose** deployment
- Liveness and readiness endpoints
- Comprehensive offline + PostgreSQL integration + live LLM test suites

## Architecture

```mermaid
flowchart TD
    C[Contract / Clause] --> S[Security Boundary]
    S --> R[Authoritative eCFR Retrieval]
    R --> M[Memory Lookup<br/>Advisory only]
    M --> D[Deterministic Evaluation]
    D --> L[LLM Evaluation]
    L --> V[Verification]
    V -->|uncertain| NR[NEEDS_REVIEW]
    V -->|verified| OK[VERIFIED]
    NR --> HR[Human Review]
    OK --> P[Persistence]
    HR --> P
    P -->|File| F[FileRepository - JSON reports]
    P -->|Optional PostgreSQL| PG[PostgresRepository - history + review]
```

Compliance Memory is drawn below retrieval on purpose: it is **advisory
context** that can influence an evaluation, but it is never authoritative
regulatory evidence and can never reorder the hierarchy.

## Core Engineering Decisions

### Authoritative Retrieval Before Reasoning

The pipeline runs security → retrieval → deterministic rules → LLM → verification.
Regulatory text is always fetched and made usable before any reasoning begins.
If retrieval fails, the clause resolves to `NEEDS_REVIEW` — the system will not
guess.

### LLMs Do Not Control Evidence Provenance

The model is instructed to reference **only** the provided regulation text, and
evidence provenance (`source`, `retrieved_at`, `retrieval_method`, `version`) is
attached by the retrieval layer. The model never manufactures where evidence
came from; ungrounded output is routed to review.

### Compliance Memory Is Advisory

Verified historical outcomes are stored in an append-only JSONL store and can be
surfaced as labeled `HISTORICAL_CONTEXT` (data, never instructions). Memory:

- never establishes or replaces a CFR requirement
- is injected only into the user prompt, never the system instructions
- cannot trigger a verdict reuse unless current authoritative retrieval is usable
- is **not** auto-indexed when it participated (prevents feedback loops)

### Human Review Does Not Overwrite Automated Results

A reviewer decision transitions an explicit lifecycle and appends an immutable
event; it never rewrites the original `ComplianceResult`, evidence, or audit
trail.

### PostgreSQL Is Optional

`file` is the default and fully self-contained. PostgreSQL is an explicit opt-in
that adds queryable history and the review workflow. There is **no silent
fallback** — misconfiguration fails clearly.

### Fail-Open vs Fail-Fast Boundaries

- **Evaluation path**: optional persistence/memory failures are logged and
  never fail a successful compliance analysis (fail-open).
- **Configuration path**: an invalid backend or unreachable configured database
  fails fast rather than silently degrading (fail-fast).

## Human-in-the-Loop Review

```
NEEDS_REVIEW
    ↓
UNDER_REVIEW
    ↓
APPROVED / REJECTED / ESCALATED
```

- **Optimistic concurrency** — a decision carries an `expected_version`; a stale
  write is rejected instead of overwriting another reviewer.
- **Immutable decision events** — every transition is appended to an audit log.
- **Original results preserved** — the automated result, evidence, and audit are
  never modified by review.

## Dashboard

A server-rendered Jinja2 dashboard is served by the API at `/dashboard`:

- **Overview** — aggregate compliance metrics and recent analyses
- **Analysis history** — paginated, filterable list with click-through
- **Analysis detail** — clause results, authoritative CFR evidence, verification,
  audit, and memory-participation indicator
- **Review queue** — actionable `NEEDS_REVIEW` items with state filters
- **Review detail** — automated result, evidence, verification/audit, memory
  context, immutable decision history, and a decision form (valid transitions
  only)

Authoritative CFR evidence and advisory historical memory are visually distinct
— memory is never presented as regulation. No screenshots are bundled; run the
app and visit `/dashboard`.

## Quick Start

Prerequisites: Python 3.12+, `uv`, and a working `eCFR` connection. An LLM key is
only needed for the LLM-fallback evaluation step.

### File persistence (default)

```bash
cp .env.example .env       # fill in credentials if desired
uv sync --extra dev
uv run uvicorn api:app --host 0.0.0.0 --port 8000
open http://localhost:8000/dashboard
```

Enable on-disk report persistence by setting `CFR_REPORTS_DIR=reports` in `.env`.

### PostgreSQL (opt-in)

```bash
# 1. Configure the backend + DSN
CFR_PERSISTENCE_BACKEND=postgres
CFR_DATABASE_URL=postgresql://user:pass@host/db

# 2. Apply the versioned schema (idempotent, safe to re-run)
uv run python scripts/migrate.py --dsn "$CFR_DATABASE_URL"

# 3. Start the API (fails fast at startup if the DB is unreachable)
uv run uvicorn api:app --host 0.0.0.0 --port 8000
```

### Docker Compose (reproducible local stack)

```bash
docker compose up -d --build
open http://localhost:8000/dashboard
```

The Compose stack runs the app + PostgreSQL 16 with a named volume, a one-shot
migration service, and health checks. The app waits for migrations to complete.

## Environment Variables

| Variable | Requirement | Purpose |
|---|---|---|
| `ATM_API_KEY` | Optional (LLM) | Preferred credential for the LLM evaluation step |
| `ATM_BASE_URL` | Optional | LLM endpoint (default `https://atm.accure.ai/v1`) |
| `ATM_MODEL` | Optional | LLM model (default Nemotron) |
| `OPENAI_API_KEY` | Optional | Fallback OpenAI-compatible credential |
| `ECFR_BASE_URL` | Optional | eCFR API base URL |
| `CFR_REPORTS_DIR` | Optional (file backend) | Enable report persistence |
| `CFR_PERSISTENCE_BACKEND` | Backend-specific | `file` (default) or `postgres` |
| `CFR_DATABASE_URL` | Backend-specific | PostgreSQL connection string |
| `CFR_MEMORY_ENABLED` | Optional | Enable advisory Compliance Memory |
| `CFR_MEMORY_DIR` | Optional | Memory store directory |
| `CORS_ORIGINS` | Optional | Allowed CORS origins |
| `JAEGER_AGENT_HOST`/`PORT` | Optional | Best-effort tracing export |

Never commit a real `.env`; it is git-ignored and excluded from Docker builds.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Liveness check |
| `/health/ready` | GET | Readiness (reflects backend usability) |
| `/evaluate-clause` | POST | Evaluate a single clause |
| `/evaluate-bulk` | POST | Evaluate a batch (max 200), optionally persist |
| `/analyses` | GET | Paginated analysis history |
| `/analyses/{analysis_id}` | GET | Full immutable report |
| `/reviews` | GET | Review queue (defaults to actionable) |
| `/reviews/{analysis_id}/{clause_id}` | GET | Full review view |
| `/reviews/{analysis_id}/{clause_id}/decide` | POST | Record a review decision |
| `/dashboard` | GET | Server-rendered review dashboard |
| `/api/docs` | GET | Interactive OpenAPI docs |

## Testing

```bash
# Offline suite (no external services required)
uv run pytest --deselect tests/test_live_llm_integration.py -q

# Optional PostgreSQL integration tests (clean-skip without a test DB)
CFR_TEST_DATABASE_URL=postgresql://user:pass@host/db uv run pytest tests/test_postgres_integration.py

# Live LLM tests (require an LLM key + network)
uv run pytest tests/test_live_llm_integration.py

# Lint
uv run ruff check .
```

Current validation: **268 offline tests**, 9 PostgreSQL integration tests
(clean-skip when unavailable), and 9 live LLM tests.

## Project Structure

```text
agent/
  compliance_pipeline.py   # orchestration + authority order
  compliance_agent.py      # LLM evaluation
  security.py              # prompt-injection + input defenses
  deterministic_rules.py   # LLM-free rules
  verification_agent.py    # evidence/consistency verification
  memory.py / memory_store.py  # advisory compliance memory
  reporting.py             # report persistence
  persistence/             # File / Postgres / in-memory repositories + review lifecycle
api.py                     # FastAPI service
dashboard.py               # server-rendered dashboard routes
templates/ static/         # dashboard UI
migrations/ scripts/       # SQL migrations + migration runner
src/cfr_compliance_mcp/    # FastMCP server + eCFR client + tools
tests/                     # offline, integration, live suites
benchmark/                 # deterministic benchmark harness
compose.yaml  Dockerfile  pyproject.toml  uv.lock
```

## Tech Stack

**AI / LLM** — FastMCP, eCFR retrieval, deterministic rules, configurable
OpenAI-compatible LLM, verification, advisory memory

**Backend** — Python 3.12+, FastAPI, Pydantic v2, httpx, structured logging

**Persistence** — filesystem JSON reports (default) + optional PostgreSQL
(`psycopg 3`, versioned SQL migrations)

**Infrastructure** — Docker, Docker Compose, health/readiness endpoints

**Testing** — pytest, offline + optional PostgreSQL integration + live LLM

## What This Project Demonstrates

- **Agentic AI system design** — a full retrieval → reason → verify → decide
  pipeline with explicit boundaries
- **MCP tool integration** — a typed server wrapping a real regulatory API
- **LLM orchestration** — deterministic + probabilistic control flow
- **Retrieval and evidence grounding** — provenance-first design
- **AI security boundaries** — prompt-injection defense and input sanitization
- **Human-in-the-loop workflows** — explicit review lifecycle with audit
- **Transactional persistence** — optimistic concurrency and immutable events
- **API engineering** — validated request/response models, structured errors
- **Dockerized deployment** — reproducible builds and a Compose stack
- **Testing and integration validation** — offline, PostgreSQL, and live suites

## Current Limitations

- `reviewer_identity` is an unauthenticated placeholder (no auth system yet)
- PostgreSQL is single-instance (no distributed orchestration)
- Live LLM tests depend on external provider availability
- Compliance Memory is intentionally local and advisory
- The system does not automatically execute remediation

## Roadmap

- Authenticated reviewer identities and role-based access control
- Production observability and richer audit reporting
- Background job processing for long-running evaluations
- A broader, versioned compliance benchmark suite
- Multi-node deployment support

---

*AI-assisted compliance analysis is not a substitute for legal or regulatory
professionals. Non-trivial findings are routed to human review; nothing in this
system autonomously issues legal authorization.*
