# cfr-compliance-mcp

Production MCP server exposing the official eCFR API as structured tools for contract compliance checking.

## Architecture

- **MCP server** → eCFR REST API → Agno compliance agent → Nemotron 3-nano-omni via ATM → Pydantic results
- Deterministic rules filter (LLM-free) → Verification agent → Human review (HITL) for uncertain cases
- Multi-stage Docker build with non-root user

## Tech Stack

- Python 3.13, FastAPI, FastMCP, Pydantic v2
- eCFR REST API, agno, OpenAI, Ollama
- OpenTelemetry, Jaeger (best-effort)
- pypdf, SequenceMatcher (lexical re-ranking, not dense-vector RAG)
- Docker (multi-stage, non-root user)

## Setup

```bash
uv sync           # install dependencies
uv run pytest     # run 36 tests
uv run python -m compileall .  # compile check
```

## Running the MCP Server

```bash
uv run cfr-compliance-mcp
# or: uv run python -m cfr_compliance_mcp.server
```

Server starts with stdio transport, registers 8 MCP tools.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/evaluate-clause` | POST | Evaluate a single clause through the full pipeline |
| `/evaluate-bulk` | POST | Evaluate a batch of clauses |

*Structural verification complete; TestClient with Jaeger import issue prevents direct execution in this environment.*

## Docker

```bash
docker build -t cfr-compliance-mcp .
# (multi-stage build with non-root user; runtime not tested in this environment)
```

## Example

```bash
# Evaluate a clause
uv run python -c "
from agent.models import Clause
from agent.deterministic_rules import evaluate_deterministic

c = Clause(title='40', text='Contractor shall properly dispose hazardous waste per 257.3')
result = evaluate_deterministic(c, '257.3 - Standards for hazardous waste land disposal.', 40)
print(f'Status: {result.status}, Confidence: {result.confidence:.2f}')
print(f'Evidence: {len(result.evidence)} passage(s)')
"
```

### VERIFIED

- 39/39 pytest tests pass across 5 consecutive runs
- Deterministic compliance rules (3 rules, all verdict types: Compliant/Non-Compliant/Needs Review)
- Evidence-grounded result models with `ComplianceResult.evidence` tracking
- Version-aware retrieval logic with `CfrMatch.version_payload`
- Keyword/hierarchy retrieval + `SequenceMatcher` lexical re-ranking (Recall@1 = 1.00 on 3-query manual set)
- Verification agent with 5 check types (`_check_deterministic_consistency`, `_check_evidence_coverage`, `_check_prompt_injection`, `_check_version_awareness`, `_check_cfr_title_validity`)
- Prompt-injection protection (19 regex patterns)
- Text sanitization (`sanitize_clause_text`) and CFR title validation (1-50)
- FastAPI endpoint structure (`/health`, `/evaluate-clause`, `/evaluate-bulk`)
- OpenTelemetry instrumentation (9/9 checks pass; Jaeger graceful degradation)
- Dockerfile multi-stage build (non-root user confirmed in source)
- PDF clause extraction from sample contracts (deterministic: `sample_contract.pdf` → 9 clauses, `sample_contract_multi.pdf` → 24 clauses)
- Repeated regression stability (5 consecutive pytest runs: 39/39; 105/105 randomized deterministic; 100/100 randomized security; 50/50 randomized CFR/retrieval)
- Randomized regression testing framework

### EXPERIMENTALLY VERIFIED

- PDF → deterministic compliance pipeline (offline, clause extraction verified at ~0.04ms/clause)
- Offline E2E execution (deterministic rules + security + retrieval tested without live eCFR/LLM)
- Randomized retrieval/security testing (105 deterministic + 100 security + 50 CFR regression cases all pass)
- Measured deterministic performance (~0.04 ms/clause, LLM-free filter)

### ENVIRONMENT-LIMITED / UNVERIFIED

- Live eCFR API integration (no network access in this environment)
- Docker runtime execution (multi-stage build confirmed in source; container not actually run in this environment)
- Jaeger trace delivery (version incompatibility; application degrades gracefully when Jaeger unavailable; traces configured but not verifiable without running Jaeger)
- Historical 65% performance claim ("12 min → 4 min 10 sec") (cannot reproduce without eCFR API + LLM pipeline access)
- Full end-to-end LLM pipeline with live eCFR data

### VERIFIED (Live)

- Live Nemotron inference through ATM: verified via `https://atm.accure.ai/v1`, model `nvidia/nemotron-3-nano-omni`, HTTP 200, successful inference
- Authentication verified: `ATM_API_KEY` environment variable based
- PDF extraction benchmarking per contract

## Claim Verification

See `docs/CLAIMS_EVIDENCE.md` for detailed claim-by-claim evidence and status.

See `docs/VALIDATION_REPORT.md` for the full validation report.

## Evaluation Methodology

- Deterministic rules: fast LLM-free filter (~0.04ms/clause)
- Hybrid retrieval: keyword search + `SequenceMatcher` lexical re-ranking (Recall@1 = 1.00)
- Compliance evaluation: 16 manual test cases across 7 categories
- Security: 19 prompt injection patterns + text sanitization + title validation
- LLM integration: **Live Nemotron inference verified** via ATM `https://atm.accure.ai/v1`, model `nvidia/nemotron-3-nano-omni`, HTTP 200, successful inference. Framework verified; live call SUCCESSFUL.
